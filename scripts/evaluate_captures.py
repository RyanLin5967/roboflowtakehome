#!/usr/bin/env python3
"""Offline audit of saved live captures against independently recorded human judgments.

No model inference, threshold tuning, generated labels, or provider requests occur.
See docs/plans/PHYSICAL_VALIDATION.md for the frozen protocol and input contracts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

VERSION = "counterpart.evaluation.v1"
UNCERTAIN_CODES = frozenset(
    {
        "missing_object",
        "ambiguous_object",
        "low_confidence",
        "unclear_relation",
        "ambiguous_order",
        "ambiguous_lanes",
        "observation_gap",
        "capture_interrupted",
    }
)
TERMINAL = frozenset({"accepted", "rejected", "failed", "superseded"})


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def evidence_digest(session_id: str, capture: dict) -> str:
    """Bind reviewer judgments to immutable source media and sequence order, not predictions."""
    frames = capture.get("frames")
    require(isinstance(frames, list) and bool(frames), "Capture has no original frames")
    evidence = []
    for frame in frames:
        asset_id = frame.get("asset_id", "")
        require(
            isinstance(asset_id, str)
            and len(asset_id) == 64
            and all(c in "0123456789abcdef" for c in asset_id),
            "Invalid frame SHA256",
        )
        require(number(frame.get("t_ms")) and frame["t_ms"] >= 0, "Invalid frame timestamp")
        require("fixture_detections" not in frame, "Fixture evidence cannot enter physical evaluation")
        evidence.append({"asset_id": asset_id, "t_ms": float(frame["t_ms"])})
    return hashlib.sha256(
        canonical(
            {
                "session_id": session_id,
                "capture_id": capture["id"],
                "slot": capture["slot"],
                "frames": evidence,
            }
        )
    ).hexdigest()


def load_documents(paths: list[Path]) -> tuple[list[dict], list[dict], bool, list[dict]]:
    """Load saved pair manifests or complete session-audit manifests; never infer missing attempts."""
    rows: dict[str, dict] = {}
    pairs: dict[str, dict] = {}
    complete_sessions = set()
    sessions = set()
    inputs = []
    for path in paths:
        raw = path.read_bytes()
        document = json.loads(raw)
        inputs.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        schema = document.get("schema_version")
        require(
            schema in {"counterpart.pair.v1", "counterpart.evaluation.session.v1"},
            "Unsupported manifest schema",
        )
        require(document.get("mode") == "live", "Only live evidence can enter physical evaluation")
        require(
            document.get("provenance", {}).get("evidence_kind") == "roboflow_capture",
            "Manifest does not identify live Roboflow captures",
        )
        session_id, recipe = document.get("session_id"), document.get("recipe")
        require(text(session_id) and recipe in {"spatial", "temporal"}, "Invalid session identity")
        sessions.add(session_id)
        snapshot = document.get("recipe_snapshot") or {}
        objects = document.get("objects") or {}
        object_a = snapshot.get("object_a") or objects.get("a") or document.get("object_a", "bottle")
        object_b = snapshot.get("object_b") or objects.get("b") or document.get("object_b", "cup")
        require(text(object_a) and text(object_b) and object_a != object_b, "Invalid recipe objects")
        allowed_answers = ["left", "right"] if recipe == "spatial" else [object_a, object_b]
        recipe_digest = hashlib.sha256(
            canonical(
                {
                    "recipe": recipe,
                    "object_a": object_a,
                    "object_b": object_b,
                    "snapshot": snapshot,
                }
            )
        ).hexdigest()
        if schema == "counterpart.pair.v1":
            captures = [document["baseline"], document["counterpart"]]
            publications = [document]
        else:
            require(document.get("captures_complete") is True, "Session audit must include all attempts")
            captures = document.get("captures")
            require(isinstance(captures, list), "Session audit captures must be a list")
            complete_sessions.add(session_id)
            publications = document.get("pairs", [])
            require(isinstance(publications, list), "Session audit pairs must be a list")
        for pair in publications:
            require(
                pair.get("schema_version") == "counterpart.pair.v1"
                and pair.get("session_id") == session_id
                and pair.get("mode") == "live"
                and pair.get("recipe") == recipe,
                "Invalid nested publication",
            )
            pair_id = pair.get("pair_id")
            require(text(pair_id), "Publication has no ID")
            entry = {
                "id": pair_id,
                "session_id": session_id,
                "recipe": recipe,
                "members": [pair[slot]["id"] for slot in ("baseline", "counterpart")],
            }
            require(len(set(entry["members"])) == 2, "Publication repeats a capture")
            require(pair_id not in pairs or pairs[pair_id] == entry, "Conflicting publication IDs")
            pairs[pair_id] = entry
        for capture in captures:
            if capture.get("slot") in {"preview", "recovery"}:
                continue
            require(capture.get("slot") in {"baseline", "counterpart"}, "Invalid capture slot")
            require(text(capture.get("id")), "Capture has no ID")
            require(capture.get("session_id", session_id) == session_id, "Capture/session mismatch")
            status = capture.get("status")
            require(status in TERMINAL | {"queued", "running"}, "Invalid capture status")
            result = capture.get("result") or {}
            require(isinstance(result, dict), "Invalid capture result")
            if status in {"accepted", "rejected"}:
                require(result.get("accepted") is (status == "accepted"), "Decision/status mismatch")
                require(text(result.get("verifier_version")), "Completed decision lacks verifier version")
                for frame in capture["frames"]:
                    require(
                        frame.get("model", {}).get("provider") == "roboflow"
                        and text(frame.get("model", {}).get("model_id")),
                        "Completed capture lacks real provider provenance",
                    )
            answer = result.get("evidence", {}).get("relation") if recipe == "spatial" else None
            if recipe == "temporal":
                order = result.get("evidence", {}).get("arrival_order")
                answer = order[0] if isinstance(order, list) and len(order) == 2 else None
            if status == "accepted":
                require(answer in allowed_answers, "Accepted capture lacks a supported observed answer")
            row = {
                "capture_id": capture["id"],
                "session_id": session_id,
                "recipe": recipe,
                "slot": capture["slot"],
                "status": status,
                "allowed_answers": allowed_answers,
                "accepted": status == "accepted",
                "answer": answer,
                "code": result.get("code") or capture.get("error") or status,
                "verifier_version": result.get("verifier_version"),
                "reference_capture_id": capture.get("reference_capture_id"),
                "evidence_digest": evidence_digest(session_id, capture),
                "recipe_digest": recipe_digest,
                "assets": [{"asset_id": f["asset_id"], "t_ms": f["t_ms"]} for f in capture["frames"]],
            }
            require(
                row["capture_id"] not in rows or rows[row["capture_id"]] == row,
                "Conflicting duplicate capture; use one frozen snapshot",
            )
            rows[row["capture_id"]] = row
    require(bool(rows), "No committed captures found")
    for pair in pairs.values():
        require(
            all(member in rows and rows[member]["accepted"] for member in pair["members"]),
            "Publication references unavailable or unaccepted captures",
        )
    for pair in pairs.values():
        baseline_id, counterpart_id = pair["members"]
        saved_reference = rows[counterpart_id]["reference_capture_id"]
        require(saved_reference in (None, baseline_id), "Publication/reference mismatch")
        rows[counterpart_id]["reference_capture_id"] = baseline_id
    for row in rows.values():
        reference_id = row["reference_capture_id"]
        reference = rows.get(reference_id)
        if reference_id is not None:
            require(
                reference is not None
                and reference["session_id"] == row["session_id"]
                and reference["slot"] == "baseline"
                and row["slot"] != "baseline",
                "Historical reference is missing or belongs to another session",
            )
        row["reference_evidence_digest"] = reference["evidence_digest"] if reference else None
        row["reference_assets"] = reference["assets"] if reference else []
    return list(rows.values()), list(pairs.values()), sessions <= complete_sessions, inputs


def label_template(rows: list[dict]) -> list[dict]:
    return [
        {
            "schema_version": "counterpart.human-label.v1",
            "capture_id": row["capture_id"],
            "session_id": row["session_id"],
            "evidence_digest": row["evidence_digest"],
            "recipe_digest": row["recipe_digest"],
            "reference_capture_id": row["reference_capture_id"],
            "reference_evidence_digest": row["reference_evidence_digest"],
            "reference_assets": row["reference_assets"],
            "scene_id": None,
            "partition": "evaluation",
            "reviewer_id": None,
            "reviewed_without_model_output": False,
            "decision": None,
            "observed_answer": None,
            "condition": None,
            "assets": row["assets"],
        }
        for row in rows
        if row["status"] in TERMINAL
    ]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def bind_labels(rows: list[dict], labels: list[dict]) -> list[dict]:
    indexed = {row["capture_id"]: row for row in rows}
    seen = set()
    scene_by_session: dict[str, str] = {}
    partition_by_scene: dict[str, str] = {}
    assets_by_partition: dict[str, str] = {}
    result = []
    for label in labels:
        capture_id = label.get("capture_id")
        require(capture_id in indexed and capture_id not in seen, "Unknown or duplicate human label")
        seen.add(capture_id)
        row = indexed[capture_id]
        require(label.get("schema_version") == "counterpart.human-label.v1", "Invalid human label schema")
        require(
            label.get("session_id") == row["session_id"]
            and label.get("evidence_digest") == row["evidence_digest"]
            and label.get("recipe_digest") == row["recipe_digest"]
            and label.get("reference_capture_id") == row["reference_capture_id"]
            and label.get("reference_evidence_digest") == row["reference_evidence_digest"],
            "Human label evidence mismatch",
        )
        require(
            label.get("reviewed_without_model_output") is True and text(label.get("reviewer_id")),
            "Human review must be independent of model output",
        )
        require(label.get("decision") in {"valid", "invalid", "uncertain"}, "Missing human decision")
        require(label.get("partition") in {"calibration", "evaluation"}, "Missing study partition")
        require(text(label.get("scene_id")) and text(label.get("condition")), "Missing scene/condition")
        require(row["status"] in TERMINAL, "Cannot review unfinished capture")
        scene = label["scene_id"]
        require(
            scene_by_session.setdefault(row["session_id"], scene) == scene,
            "One session cannot belong to multiple scenes",
        )
        require(
            partition_by_scene.setdefault(scene, label["partition"]) == label["partition"],
            "Scene leaked between calibration and evaluation",
        )
        for asset in row["assets"]:
            require(
                assets_by_partition.setdefault(asset["asset_id"], label["partition"]) == label["partition"],
                "Identical media leaked between calibration and evaluation",
            )
        allowed_answers = set(row["allowed_answers"])
        require(label.get("observed_answer") in allowed_answers | {None}, "Invalid independently seen answer")
        if label["decision"] == "valid":
            require(
                row["slot"] == "baseline" or row["reference_capture_id"] is not None,
                "Valid counterpart review needs its historical reference",
            )
            require(
                label.get("observed_answer") in allowed_answers, "A valid judgment needs an observed answer"
            )
        result.append(
            {
                **row,
                "human": label["decision"],
                "observed_answer": label.get("observed_answer"),
                "scene_id": scene,
                "condition": label["condition"],
                "partition": label["partition"],
                "reviewer_id": label["reviewer_id"],
            }
        )
    return result


def ratio(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def metrics(rows: list[dict], *, complete: bool) -> dict:
    known = [row for row in rows if row["human"] != "uncertain"]
    accepted = [row for row in known if row["accepted"]]
    correct = sum(row["human"] == "valid" and row["answer"] == row["observed_answer"] for row in accepted)
    human_valid = sum(row["human"] == "valid" for row in known)
    return {
        "captures": len(rows),
        "sessions": len({row["session_id"] for row in rows}),
        "scenes": len({row["scene_id"] for row in rows}),
        "accepted_precision_among_decisively_reviewed": ratio(correct, len(accepted)),
        "human_uncertainty": ratio(sum(row["human"] == "uncertain" for row in rows), len(rows)),
        "accepted_human_uncertainty": ratio(
            sum(row["accepted"] and row["human"] == "uncertain" for row in rows),
            sum(row["accepted"] for row in rows),
        ),
        "acceptance_rate": ratio(sum(row["accepted"] for row in rows), len(rows)) if complete else None,
        "valid_capture_recall": ratio(correct, human_valid) if complete else None,
        "verifier_uncertainty_refusal": ratio(sum(row["code"] in UNCERTAIN_CODES for row in rows), len(rows))
        if complete
        else None,
        "decision_codes": dict(sorted(Counter(row["code"] for row in rows).items())),
    }


def bootstrap(rows: list[dict], *, repeats: int, seed: int, complete: bool) -> dict:
    """Resample whole scenes, so retries/pairs/session restarts are never independent samples."""
    groups = defaultdict(list)
    for row in rows:
        groups[row["scene_id"]].append(row)
    if len(groups) < 8:
        return {"available": False, "reason": "Fewer than 8 independent scenes; report counts only."}
    keys = sorted(groups)
    rng = random.Random(seed)
    estimates = defaultdict(list)
    targets = ["accepted_precision_among_decisively_reviewed"]
    if complete:
        targets += ["acceptance_rate", "valid_capture_recall"]
    for _ in range(repeats):
        sample = [row for _ in keys for row in groups[rng.choice(keys)]]
        sample_metrics = metrics(sample, complete=complete)
        for name in targets:
            value = sample_metrics[name]["value"]
            if value is not None:
                estimates[name].append(value)
    intervals = {}
    for name in targets:
        values = sorted(estimates[name])
        if len(values) < 0.95 * repeats or not values or values[0] == values[-1]:
            intervals[name] = {
                "available": False,
                "reason": "Undefined or degenerate resamples; no precision claim.",
            }
        else:
            intervals[name] = {
                "available": True,
                "lower": values[int(0.025 * (len(values) - 1))],
                "upper": values[int(0.975 * (len(values) - 1))],
                "defined_resamples": len(values),
            }
    return {
        "available": True,
        "method": "scene-cluster percentile bootstrap",
        "level": 0.95,
        "repeats": repeats,
        "seed": seed,
        "independent_scenes": len(keys),
        "intervals": intervals,
    }


def evaluate(
    rows: list[dict],
    pairs: list[dict],
    labels: list[dict],
    *,
    complete: bool,
    repeats: int = 1000,
    seed: int = 20260926,
    baseline: list[dict] | None = None,
) -> dict:
    require(type(repeats) is int and 100 <= repeats <= 10000, "Bootstrap repeats must be 100–10000")
    bound = bind_labels(rows, labels)
    evaluated = [row for row in bound if row["partition"] == "evaluation"]
    require(bool(evaluated), "No independent evaluation labels supplied")
    indexed = {row["capture_id"]: row for row in evaluated}
    summary = metrics(evaluated, complete=complete)
    strata = {}
    for field in ("recipe", "scene_id", "session_id", "condition"):
        grouped = defaultdict(list)
        for row in evaluated:
            grouped[row[field]].append(row)
        strata[field] = {key: metrics(values, complete=complete) for key, values in sorted(grouped.items())}
    reviewed_pairs, valid_pairs, uncertain_pairs = 0, 0, 0
    for pair in pairs:
        if all(member in indexed for member in pair["members"]):
            members = [indexed[member] for member in pair["members"]]
            if any(member["human"] == "uncertain" for member in members):
                uncertain_pairs += 1
                continue
            reviewed_pairs += 1
            valid_pairs += all(
                member["human"] == "valid" and member["answer"] == member["observed_answer"]
                for member in members
            )
    report = {
        "schema_version": VERSION,
        "evidence_kind": "independent_human_review_of_saved_live_captures",
        "sampling_frame": "all_committed_attempts" if complete else "published_pairs_only",
        "counterparts_without_historical_reference": [
            row["capture_id"]
            for row in rows
            if row["slot"] == "counterpart" and row["reference_capture_id"] is None
        ],
        "input_captures": len(rows),
        "evaluation_labels": len(evaluated),
        "capture_population": {
            "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
            "accepted_over_all_finished_submissions": ratio(
                sum(row["accepted"] for row in rows),
                sum(row["status"] in TERMINAL for row in rows),
            )
            if complete
            else None,
            "scope": "All supplied sessions, including any calibration captures; not holdout performance.",
        },
        "calibration_labels_excluded": len(bound) - len(evaluated),
        "missing_human_labels": sorted(
            set(row["capture_id"] for row in rows) - {row["capture_id"] for row in bound}
        ),
        "review_coverage": ratio(len(bound), len(rows)),
        "summary": summary,
        "strata": strata,
        "published_pair_precision_among_decisively_reviewed": ratio(valid_pairs, reviewed_pairs),
        "published_pairs": len(pairs),
        "published_pairs_with_uncertain_human_review": uncertain_pairs,
        "confidence_intervals": bootstrap(evaluated, repeats=repeats, seed=seed, complete=complete),
        "verifier_versions": sorted(
            {row["verifier_version"] for row in evaluated if row["verifier_version"]}
        ),
        "uncertainty_code_policy": sorted(UNCERTAIN_CODES),
        "limits": [
            "Human validity includes requested relation/order and named controls, not every physical detail.",
            "Metrics use reviewed evaluation captures; inspect missing labels before generalizing.",
            "Live manifest provenance is not cryptographic attestation or an independent scene observation.",
            "No collection-speed gain or generalization to unseen objects is established by this report.",
            "Published-pair-only input cannot estimate acceptance coverage or valid-capture recall.",
        ],
    }
    if baseline is not None:
        decisions = {}
        for record in baseline:
            capture_id = record.get("capture_id")
            require(
                record.get("schema_version") == "counterpart.posthoc-decision.v1"
                and capture_id in indexed
                and capture_id not in decisions,
                "Invalid post-hoc baseline row",
            )
            row = indexed[capture_id]
            require(
                record.get("evidence_digest") == row["evidence_digest"]
                and record.get("recipe_digest") == row["recipe_digest"]
                and record.get("reference_capture_id") == row["reference_capture_id"]
                and record.get("reference_evidence_digest") == row["reference_evidence_digest"]
                and record.get("reviewed_without_model_output") is True
                and text(record.get("reviewer_id"))
                and record["reviewer_id"] != row["reviewer_id"],
                "Post-hoc baseline is not independently bound",
            )
            require(
                record.get("decision") in {"accepted", "rejected", "uncertain"},
                "Invalid post-hoc baseline decision",
            )
            answer = record.get("answer")
            if record["decision"] == "accepted":
                require(answer in row["allowed_answers"], "Post-hoc acceptance needs its own answer")
            decisions[capture_id] = {
                **row,
                "accepted": record["decision"] == "accepted",
                "answer": answer,
                "code": "low_confidence"
                if record["decision"] == "uncertain"
                else "posthoc_" + record["decision"],
            }
        require(
            set(decisions) == set(indexed),
            "Post-hoc baseline must judge exactly the same evaluation captures",
        )
        report["posthoc_baseline"] = {
            "method": "written shotlist plus independent post-hoc review",
            "summary": metrics(list(decisions.values()), complete=complete),
            "paired_capture_ids": sorted(decisions),
            "limits": "Same saved captures tests filtering quality only, not the benefit of live guidance.",
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--baseline-decisions", type=Path)
    parser.add_argument("--write-label-template", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bootstrap-repeats", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260926)
    args = parser.parse_args()
    try:
        rows, pairs, complete, inputs = load_documents(args.manifests)
        if args.write_label_template:
            require(
                not args.labels and not args.baseline_decisions and not args.output,
                "Template mode cannot also evaluate",
            )
            with args.write_label_template.open("x") as handle:
                for label in label_template(rows):
                    handle.write(json.dumps(label, sort_keys=True, allow_nan=False) + "\n")
            print(f"Wrote {len(label_template(rows))} blank judgments; no human decisions were invented.")
            return
        require(args.labels is not None and args.output is not None, "Supply --labels and --output")
        report = evaluate(
            rows,
            pairs,
            load_jsonl(args.labels),
            complete=complete,
            repeats=args.bootstrap_repeats,
            seed=args.seed,
            baseline=load_jsonl(args.baseline_decisions) if args.baseline_decisions else None,
        )
        report["input_files"] = inputs
        report["human_labels_sha256"] = hashlib.sha256(args.labels.read_bytes()).hexdigest()
        if args.baseline_decisions:
            report["baseline_decisions_sha256"] = hashlib.sha256(
                args.baseline_decisions.read_bytes()
            ).hexdigest()
        with args.output.open("x") as handle:
            json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        print(
            f"Reviewed {report['evaluation_labels']} evaluation captures from "
            f"{report['summary']['sessions']} sessions. Report: {args.output}"
        )
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
