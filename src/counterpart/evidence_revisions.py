"""Saved-evidence decisions with explicit human origin and immutable machine history.

A new verifier revision never changes the capture, its model observations, an
existing review, or a released dataset. Human adjudication has an intentionally
narrow authority: resolve perceptual uncertainty after reviewing every named
control. Known-false controls and corrupt/incomplete evidence cannot be waived.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .collections import _key, _text, _uuid
from .execution_contract import assert_observation_matches, validate_execution_contract
from .recipes import RecipeContractError, build_recipe_snapshot, validate_recipe_snapshot
from .repository import Conflict, NotFound, _json
from .splits import manifest_split
from .storage import inspect_image
from .verification import UNCERTAIN_CODES, adjudication_geometry_checks, verify

# These are perceptual absences, not observations of violated scene controls.
REVIEWABLE_CODES = frozenset(UNCERTAIN_CODES) - {
    "ambiguous_order",
    "ambiguous_lanes",
    "visual_evidence_unavailable",
}

CRITERIA = {
    "original_media_reviewed": "I inspected all original images or sampled sequence frames.",
    "objects_identified": "Both named objects are visible and their identities are unambiguous to me.",
    "relation_confirmed": "I independently established both answers from the recorded evidence.",
    "controls_confirmed": "I inspected every named recipe control across both captures.",
}
BASELINE_CRITERIA = {
    "original_media_reviewed": "I inspected every original baseline frame.",
    "objects_identified": "The named objects and protected objects are unambiguous.",
    "initial_relation_confirmed": "The frames establish the required initial relation or arrival order.",
    "uncertain_visual_controls_reviewed": "I inspected visual uncertainty; no known failure is waived.",
}
HUMAN_BASIS = (
    "Human adjudication of recorded perceptual uncertainty; original machine refusals are retained. "
    "This is a human-derived label, not machine acceptance or independent ground truth."
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(_json(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def capture_digest(capture: dict, snapshot: dict) -> str:
    return digest({"capture": capture, "recipe_snapshot": snapshot})


def pair_digest(session: dict, baseline: dict, counterpart: dict) -> str:
    return digest(
        {
            "session_id": str(session["id"]),
            "recipe_snapshot": session["recipe_snapshot"],
            "baseline": baseline,
            "counterpart": counterpart,
            "execution_contract": session.get("execution_contract"),
        }
    )


def _error(code: str, message: str):
    raise Conflict(code, message)


def validate_structure(capture: dict, snapshot: dict, *, media_reader=None) -> None:
    """Checks that cannot be excused by an early perceptual refusal."""
    if (
        capture.get("slot") not in {"baseline", "counterpart"}
        or capture.get("status") not in {"accepted", "rejected", "superseded"}
        or capture.get("interrupted") is not False
        or capture.get("error") is not None
    ):
        _error(
            "evidence_not_reviewable", "Only complete, uninterrupted committed observations can be reviewed."
        )
    frames = capture.get("frames")
    if not isinstance(frames, list) or not frames or len(frames) > 48:
        _error("invalid_evidence", "A complete bounded sequence of saved frames is required.")
    times = []
    for frame in frames:
        if not isinstance(frame, dict):
            _error("invalid_evidence", "A saved frame is malformed.")
        t = frame.get("t_ms")
        if type(t) not in {int, float} or not 0 <= t <= 12000 or not math.isfinite(t):
            _error("invalid_evidence", "Saved frame timestamps are invalid.")
        times.append(t)
        for dimension in ("width", "height"):
            if type(frame.get(dimension)) is not int or not 80 <= frame[dimension] <= 2560:
                _error("invalid_evidence", "Saved dimensions are invalid.")
        if frame["width"] * frame["height"] > 4_000_000:
            _error("invalid_evidence", "Saved image exceeds the pixel limit.")
        model = frame.get("model")
        if (
            not isinstance(model, dict)
            or model.get("provider") not in {"roboflow", "fixture"}
            or not model.get("model_id")
        ):
            _error("invalid_evidence", "Original observation provenance is required.")
        if not isinstance(frame.get("detections"), list):
            _error("invalid_evidence", "Original detector observations are missing.")
        for detection in frame["detections"]:
            if not isinstance(detection, dict) or not isinstance(detection.get("label"), str):
                _error("invalid_evidence", "A detection is malformed.")
            confidence, box = detection.get("confidence"), detection.get("box")
            if (
                type(confidence) not in {int, float}
                or not 0 <= confidence <= 1
                or not math.isfinite(confidence)
            ):
                _error("invalid_evidence", "Detector confidence is invalid.")
            if (
                not isinstance(box, (list, tuple))
                or len(box) != 4
                or any(type(v) not in {int, float} or not 0 <= v <= 1 or not math.isfinite(v) for v in box)
                or box[0] >= box[2]
                or box[1] >= box[3]
            ):
                _error("invalid_evidence", "Detection geometry is invalid.")
        if media_reader is not None:
            actual = inspect_image(media_reader(frame["asset_id"]))
            if any(actual[k] != frame.get(k) for k in actual):
                _error("invalid_evidence", "Original media differs from its recorded identity.")
    if any(b <= a for a, b in zip(times, times[1:], strict=False)):
        _error("invalid_evidence", "Frame timestamps are not strictly increasing.")
    thresholds = snapshot["verifier"]["thresholds"]
    if snapshot["recipe"] == "spatial" and len(frames) != 1:
        _error("invalid_evidence", "A spatial member must contain exactly one original image.")
    if snapshot["recipe"] == "temporal":
        if not thresholds["temporal_min_frames"] <= len(frames) <= thresholds["temporal_max_frames"]:
            _error("invalid_evidence", "A sequence is missing required sampled frames.")
        if times[-1] - times[0] > thresholds["temporal_max_duration_ms"] or any(
            b - a > thresholds["temporal_max_gap_ms"] for a, b in zip(times, times[1:], strict=False)
        ):
            _error("invalid_evidence", "Sequence timing or sampling gaps cannot be overridden.")


def eligibility(capture: dict, snapshot: dict) -> dict:
    try:
        validate_structure(capture, snapshot)
        result = capture.get("result")
        if not isinstance(result, dict) or type(result.get("accepted")) is not bool:
            _error("evidence_not_reviewable", "A complete original machine decision is required.")
        if (capture["status"] == "accepted" and result["accepted"] is not True) or (
            capture["status"] == "rejected" and result["accepted"] is not False
        ):
            _error("invalid_evidence", "Capture status contradicts its original machine decision.")
        if result.get("hard_error") is True:
            _error("evidence_not_reviewable", "Hard evidence errors cannot be adjudicated.")
        if not result["accepted"] and result.get("code") not in REVIEWABLE_CODES:
            _error("known_control_failure", "A known failed control cannot be overridden by review.")
        # Multi-clause verifiers can surface an uncertainty and a known failure together.
        for check in result.get("checks", []):
            if check.get("outcome") == "fail" or (
                check.get("passed") is False
                and check.get("outcome") != "unknown"
                and check.get("name") not in REVIEWABLE_CODES
            ):
                _error("known_control_failure", "A known failed control cannot be overridden by review.")
        return {
            "allowed": True,
            "code": "reviewable",
            "message": "Review every named control from the original media.",
        }
    except Conflict as error:
        return {"allowed": False, "code": error.code, "message": error.message}


def evaluate(capture: dict, snapshot: dict, reference: dict | None, *, media_reader=None) -> dict:
    """Execute an exact registered version, never the mutable default version."""
    snapshot = validate_recipe_snapshot(snapshot)
    kwargs = {
        "object_a": snapshot["object_a"],
        "object_b": snapshot["object_b"],
        "interrupted": capture["interrupted"],
    }
    kwargs.update(
        recipe_snapshot=snapshot,
        media_reader=media_reader,
        baseline_authorization=capture.get("baseline_authorization"),
    )
    return verify(snapshot["recipe"], capture["slot"], capture["frames"], reference, **kwargs)


def comparison_status(capture: dict, result: dict, reference_result: dict | None) -> dict:
    """Explain a strict verifier dependency without changing its decision.

    A reference freshly rechecked under v2 may be uncertain even though its
    historical v1 decision passed. The strict candidate verifier correctly
    refuses that unaccepted reference. Describe this as a blocked comparison,
    retaining both exact decisions and never inventing candidate CV checks.
    """
    outcome = result.get("outcome") or (
        "pass" if result["accepted"] else "unknown" if result.get("code") in UNCERTAIN_CODES else "fail"
    )
    summary = {
        "schema_version": "counterpart.comparison-status.v1",
        "status": "evaluated",
        "outcome": outcome,
        "candidate_checks": "complete",
        "reference_capture_id": capture.get("reference_capture_id"),
        "explanation": "Saved observations were compared. Original decisions and reviews are unchanged.",
    }
    # Do not hide a separate authorization/data error behind reference uncertainty.
    dependency_only = result.get("code") == "baseline_required" and all(
        check.get("passed") is True or check.get("code", check.get("name")) == "baseline_required"
        for check in result.get("checks", [])
    )
    if reference_result is not None and reference_result.get("accepted") is False and dependency_only:
        reference_outcome = reference_result.get("outcome") or (
            "unknown" if reference_result.get("code") in UNCERTAIN_CODES else "fail"
        )
        unknown = reference_outcome == "unknown" and reference_result.get("hard_error") is not True
        summary.update(
            status="blocked_reference_unknown" if unknown else "blocked_reference_failure",
            outcome="unknown" if unknown else "fail",
            candidate_checks="dependency_gate_only",
            explanation=(
                "The saved baseline is uncertain under this verifier. Its checks ran, but counterpart "
                "checks stopped at the reference requirement. No new capture was accepted."
                if unknown
                else "The saved baseline failed this verifier. Counterpart checks stopped at the reference "
                "requirement. No new capture was accepted."
            ),
        )
    return summary


def validate_adjudication(pair: dict, *, media_reader=None, replay_decisions=True) -> None:
    """Portable validation shared with the independent archive consumer."""
    adjudication = pair.get("evidence_adjudication")
    if (
        not isinstance(adjudication, dict)
        or adjudication.get("schema_version") != "counterpart.adjudication.v1"
    ):
        _error("invalid_adjudication", "Explicit human adjudication provenance is required.")
    required_fields = {
        "schema_version",
        "id",
        "version",
        "assigned_roles",
        "evidence_sha256",
        "labels",
        "criteria",
        "reviewed_controls",
        "reason",
        "reviewer",
        "created_at",
        "record_sha256",
    }
    if set(adjudication) != required_fields:
        _error("invalid_adjudication", "The human decision record has unsupported fields.")
    try:
        if (
            str(UUID(adjudication["id"])) != adjudication["id"]
            or datetime.fromisoformat(adjudication["created_at"]).tzinfo is None
        ):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        _error("invalid_adjudication", "The human decision identity or time is invalid.")
    snapshot = validate_recipe_snapshot(pair["recipe_snapshot"])
    execution = pair.get("execution_contract")
    if execution is not None:
        execution = validate_execution_contract(execution, mode=pair["mode"])
    if (
        pair["baseline"]["id"] == pair["counterpart"]["id"]
        or pair["baseline"]["revision"] >= pair["counterpart"]["revision"]
    ):
        _error("invalid_adjudication", "Adjudication requires two distinct, ordered original observations.")
    authorization = pair.get("baseline_authorization")
    if authorization is not None:
        validate_baseline_authorization(authorization, pair["baseline"], snapshot, execution)
        if authorization["session_id"] != pair["session_id"]:
            _error("invalid_adjudication", "The baseline authorization belongs to another session.")
        if (
            pair["counterpart"]["epoch"] != pair["baseline"]["epoch"]
            and pair["counterpart"].get("recovery_unresolved") is not True
        ):
            _error(
                "invalid_adjudication",
                "Cross-epoch authorized evidence must retain unresolved scene recovery.",
            )
        if pair["counterpart"].get("baseline_authorization") != authorization:
            _error("invalid_adjudication", "Counterpart uses a different baseline authorization.")
    elif pair["counterpart"].get("baseline_authorization") is not None:
        _error("invalid_adjudication", "The pair omits its human baseline authorization.")
    original_baseline = None
    for slot in ("baseline", "counterpart"):
        capture = pair[slot]
        if (
            capture.get("slot") == "counterpart"
            and capture.get("reference_capture_id") != pair["baseline"]["id"]
        ):
            _error("invalid_adjudication", "The original counterpart belongs to a different reference.")
        if slot == "baseline" and capture.get("slot") != "baseline":
            _error("invalid_adjudication", "The first member must be an original baseline observation.")
        # A source baseline may be assigned the human counterpart role, but its
        # original machine decision is still replayed as a baseline decision.
        actual = (
            evaluate(
                capture,
                snapshot,
                original_baseline if capture["slot"] == "counterpart" else None,
                media_reader=media_reader,
            )
            if replay_decisions
            else capture.get("result")
        )
        if digest(actual) != digest(capture.get("result")):
            _error(
                "invalid_adjudication", "The original machine decision differs from its saved observations."
            )
        checks = adjudication_geometry_checks(
            pair["recipe"],
            slot,
            capture["frames"],
            original_baseline,
            object_a=snapshot["object_a"],
            object_b=snapshot["object_b"],
        )
        if any(check.get("outcome") == "fail" for check in checks):
            _error(
                "known_control_failure",
                "An independently observable control failed despite perceptual uncertainty.",
            )
        if slot == "baseline":
            original_baseline = {**actual, "frames": capture["frames"]}
        for frame in capture["frames"]:
            if frame.get("model", {}).get("provider") != (
                "fixture" if pair["mode"] == "replay" else "roboflow"
            ):
                _error(
                    "invalid_adjudication", "The original model provider differs from the declared source."
                )
            if execution is not None:
                assert_observation_matches(frame, execution, pair["mode"])
        allowed = eligibility(capture, snapshot)
        if not allowed["allowed"]:
            _error(allowed["code"], allowed["message"])
    payload = {key: value for key, value in adjudication.items() if key != "record_sha256"}
    if digest(payload) != adjudication.get("record_sha256"):
        _error("invalid_adjudication", "Human decision digest does not match its record.")
    source = {
        "id": pair["session_id"],
        "recipe_snapshot": snapshot,
        "execution_contract": pair.get("execution_contract"),
    }
    if adjudication.get("evidence_sha256") != pair_digest(source, pair["baseline"], pair["counterpart"]):
        _error("invalid_adjudication", "Human decision belongs to different original evidence.")
    if adjudication.get("assigned_roles") != {
        "baseline": pair["baseline"]["id"],
        "counterpart": pair["counterpart"]["id"],
    }:
        _error("invalid_adjudication", "Human decision roles differ from their original captures.")
    if adjudication.get("criteria") != dict.fromkeys(CRITERIA, True) or any(
        value is not True for value in adjudication.get("criteria", {}).values()
    ):
        _error("invalid_adjudication", "All independent review criteria are required.")
    if adjudication.get("reviewed_controls") != snapshot["controls"]:
        _error("invalid_adjudication", "The reviewer did not bind every frozen recipe control.")
    labels = adjudication.get("labels")
    expected = snapshot["expected_answers"]
    if labels != expected:
        _error("invalid_adjudication", "Human answers must independently establish this recipe's two roles.")
    if (
        not isinstance(adjudication.get("reason"), str)
        or not 20 <= len(adjudication["reason"].strip()) <= 2000
    ):
        _error("invalid_adjudication", "A specific adjudication rationale is required.")
    if (
        not isinstance(adjudication.get("reviewer"), str)
        or not adjudication["reviewer"].strip()
        or type(adjudication.get("version")) is not int
        or adjudication["version"] != 1
    ):
        _error("invalid_adjudication", "A versioned human identity is required.")
    if all(pair[slot]["result"]["accepted"] for slot in ("baseline", "counterpart")):
        _error("invalid_adjudication", "Accepted pairs use the ordinary publication and review workflow.")


def baseline_eligibility(capture: dict, snapshot: dict) -> dict:
    try:
        from .verification import baseline_authorization_binding

        validate_structure(capture, snapshot)
        if capture["slot"] != "baseline":
            _error("baseline_not_authorizable", "Only a saved initial observation can be authorized.")
        baseline_authorization_binding(snapshot, {**capture["result"], "frames": capture["frames"]})
        return {
            "allowed": True,
            "code": "reviewable_visual_uncertainty",
            "message": "Geometry passed. Review the remaining visual uncertainty to collect a counterpart.",
        }
    except (Conflict, ValueError, KeyError, TypeError) as error:
        return {
            "allowed": False,
            "code": getattr(error, "code", "baseline_not_authorizable"),
            "message": "Initial object geometry must already be complete and valid; otherwise recapture it.",
        }


def validate_baseline_authorization(
    record: dict, capture: dict, snapshot: dict, execution: dict | None
) -> None:
    from .verification import baseline_authorization_binding

    fields = {
        "schema_version",
        "decision",
        "id",
        "version",
        "session_id",
        "capture_id",
        "evidence_sha256",
        "recipe_snapshot_sha256",
        "baseline_decision_sha256",
        "baseline_frames_sha256",
        "execution_contract_digest",
        "criteria",
        "answer",
        "reason",
        "reviewer",
        "created_at",
        "record_sha256",
    }
    if not isinstance(record, dict) or set(record) != fields:
        _error("invalid_baseline_authorization", "The baseline authorization has unsupported fields.")
    if (
        record["schema_version"] != "counterpart.baseline-authorization/1"
        or record["decision"] != "allow_counterpart"
    ):
        _error("invalid_baseline_authorization", "An explicit counterpart authorization is required.")
    if digest({k: v for k, v in record.items() if k != "record_sha256"}) != record["record_sha256"]:
        _error("invalid_baseline_authorization", "The baseline authorization digest is invalid.")
    if record["capture_id"] != capture["id"] or record["evidence_sha256"] != capture_digest(
        capture, snapshot
    ):
        _error("invalid_baseline_authorization", "The authorization belongs to different original evidence.")
    bindings = baseline_authorization_binding(snapshot, {**capture["result"], "frames": capture["frames"]})
    if any(record.get(key) != value for key, value in bindings.items()):
        _error("invalid_baseline_authorization", "The original baseline or its recipe changed.")
    if record["execution_contract_digest"] != (execution["digest"] if execution else None):
        _error("invalid_baseline_authorization", "The baseline model configuration changed.")
    if record["criteria"] != dict.fromkeys(BASELINE_CRITERIA, True) or any(
        v is not True for v in record["criteria"].values()
    ):
        _error("invalid_baseline_authorization", "Every baseline review criterion must be confirmed.")
    if record["answer"] != snapshot["expected_answers"]["baseline"]:
        _error("invalid_baseline_authorization", "The reviewed initial answer does not match this recipe.")
    if (
        type(record["version"]) is not int
        or record["version"] != 1
        or not isinstance(record["reviewer"], str)
        or not record["reviewer"].strip()
        or not isinstance(record["reason"], str)
        or not 20 <= len(record["reason"].strip()) <= 2000
    ):
        _error("invalid_baseline_authorization", "A versioned reviewer and specific rationale are required.")
    try:
        for identifier in ("id", "session_id", "capture_id"):
            if str(UUID(record[identifier])) != record[identifier]:
                raise ValueError
        if datetime.fromisoformat(record["created_at"]).tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        _error("invalid_baseline_authorization", "Authorization identity or creation time is invalid.")


class EvidenceRepository:
    def __init__(self, repository, media):
        self.repository, self.media = repository, media

    def _load(self, connection, session_id: str, capture_id: str):
        row = connection.execute(
            "SELECT session_id FROM counterpart_captures WHERE id=%s", (_uuid(capture_id, "Capture"),)
        ).fetchone()
        if row is None or str(row["session_id"]) != session_id:
            raise NotFound("Capture not found in this session")
        return self.repository._capture(connection, capture_id, internal=True)

    def detail(self, session_id: str, baseline_id=None, counterpart_id=None):
        with self.repository._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            session = _json(self.repository._session(connection, session_id))
            session_id = session["id"]
            snapshot = validate_recipe_snapshot(session["recipe_snapshot"])
            rows = connection.execute(
                "SELECT id FROM counterpart_captures WHERE session_id=%s "
                "AND slot IN ('baseline','counterpart') ORDER BY created_at,id",
                (session_id,),
            ).fetchall()
            captures = []
            for row in rows:
                capture = self.repository._capture(connection, row["id"], internal=True)
                revisions = [
                    r["record"]
                    for r in connection.execute(
                        "SELECT record FROM counterpart_evidence_revisions WHERE capture_id=%s ORDER BY "
                        "version",
                        (row["id"],),
                    ).fetchall()
                ]
                reviews = [
                    r["record"]
                    for r in connection.execute(
                        "SELECT record FROM counterpart_attempt_reviews WHERE capture_id=%s ORDER BY version",
                        (row["id"],),
                    ).fetchall()
                ]
                captures.append(
                    {
                        "capture": capture,
                        "evidence_sha256": capture_digest(capture, snapshot),
                        "revision_version": len(revisions),
                        "revisions": revisions,
                        "review_version": len(reviews),
                        "reviews": reviews,
                        "review_eligibility": eligibility(capture, snapshot),
                        "baseline_authorization_eligibility": baseline_eligibility(capture, snapshot),
                    }
                )
            baseline_id = baseline_id or session.get("baseline_id")
            counterpart_id = counterpart_id or next(
                (c["capture"]["id"] for c in reversed(captures) if c["capture"]["slot"] == "counterpart"),
                None,
            )
            selected = None
            if baseline_id and counterpart_id:
                baseline = self._load(connection, session_id, baseline_id)
                counterpart = self._load(connection, session_id, counterpart_id)
                selected = {
                    "baseline_id": baseline["id"],
                    "counterpart_id": counterpart["id"],
                    "evidence_sha256": pair_digest(session, baseline, counterpart),
                }
            adjudications = [
                r["record"]
                for r in connection.execute(
                    "SELECT record FROM counterpart_evidence_adjudications WHERE session_id=%s ORDER BY "
                    "created_at",
                    (session_id,),
                ).fetchall()
            ]
            from .recipes import recipe_catalogue

            versions = sorted(
                {r["recipe_version"] for r in recipe_catalogue() if r["recipe"] == session["recipe"]}
            )
            return {
                "session_id": session_id,
                "session_revision": session["revision"],
                "recipe_snapshot": snapshot,
                "captures": captures,
                "selected_pair": selected,
                "adjudications": adjudications,
                "adjudication_criteria": [{"id": k, "label": v} for k, v in CRITERIA.items()],
                "recipe_versions": versions,
                "baseline_authorization": session.get("baseline_authorization"),
                "baseline_authorization_criteria": [
                    {"id": k, "label": v} for k, v in BASELINE_CRITERIA.items()
                ],
            }

    def reverify(
        self,
        session_id,
        capture_id,
        *,
        expected_version,
        expected_evidence_sha256,
        recipe_version,
        idempotency_key,
        reviewer="workspace-operator",
    ):
        key = _key(idempotency_key)
        request = {
            "expected_version": expected_version,
            "expected_evidence_sha256": expected_evidence_sha256,
            "recipe_version": recipe_version,
            "reviewer": reviewer,
        }
        request_hash = digest(request)
        with self.repository._connection() as connection:
            session = _json(self.repository._session(connection, session_id))
            capture = self._load(connection, session_id, capture_id)
            existing = connection.execute(
                "SELECT request_fingerprint,record FROM counterpart_evidence_revisions WHERE "
                "capture_id=%s AND request_key=%s",
                (capture_id, key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != request_hash:
                    _error("idempotency_conflict", "This request key belongs to another re-verification.")
                return existing["record"]
            reference = (
                self._load(connection, session_id, capture["reference_capture_id"])
                if capture["reference_capture_id"]
                else None
            )
        original_hash = capture_digest(capture, session["recipe_snapshot"])
        if original_hash != expected_evidence_sha256:
            _error("stale_evidence", "Reload the original evidence before re-verification.")
        validate_structure(capture, session["recipe_snapshot"], media_reader=self.media.read)
        parameters = {
            "recipe_version": recipe_version,
            "protected_objects": session["recipe_snapshot"].get("protected_objects", []),
        }
        try:
            snapshot = build_recipe_snapshot(
                session["recipe"], session["object_a"], session["object_b"], **parameters
            )
        except RecipeContractError as error:
            _error("unsupported_verifier", str(error))
        reference_result = None
        if reference:
            validate_structure(reference, session["recipe_snapshot"], media_reader=self.media.read)
            reference_result = {
                **evaluate(reference, snapshot, None, media_reader=self.media.read),
                "frames": reference["frames"],
            }
        comparison_capture = deepcopy(capture)
        source_authorization = capture.get("baseline_authorization")
        authorization_applied = source_authorization is not None
        if snapshot["snapshot_sha256"] != session["recipe_snapshot"]["snapshot_sha256"]:
            # An old human decision authorizes only its exact contract. A
            # comparison under another contract may not silently reuse it.
            comparison_capture.pop("baseline_authorization", None)
            authorization_applied = False
        result = evaluate(comparison_capture, snapshot, reference_result, media_reader=self.media.read)
        record = {
            "schema_version": "counterpart.evidence-revision.v1",
            "id": str(uuid4()),
            "session_id": session_id,
            "capture_id": capture_id,
            "version": expected_version + 1,
            "evidence_sha256": original_hash,
            "original_recipe_snapshot": session["recipe_snapshot"],
            "execution_contract": session.get("execution_contract"),
            "recipe_snapshot": snapshot,
            "reference_capture_id": capture["reference_capture_id"],
            "reference_evidence_sha256": capture_digest(reference, session["recipe_snapshot"])
            if reference
            else None,
            "reference_result": reference_result,
            "result": result,
            "comparison": comparison_status(capture, result, reference_result),
            "origin": "saved_observations",
            "model_rerun": False,
            "source_baseline_authorization_id": source_authorization["id"] if source_authorization else None,
            "baseline_authorization_applied": authorization_applied,
            "cloud_calls": 0,
            "reviewer": reviewer,
            "created_at": datetime.now(UTC).isoformat(),
        }
        record["record_sha256"] = digest(record)
        with self.repository._connection() as connection:
            self.repository._session(connection, session_id, lock=True)
            existing = connection.execute(
                "SELECT request_fingerprint,record FROM counterpart_evidence_revisions WHERE "
                "capture_id=%s AND request_key=%s",
                (capture_id, key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != request_hash:
                    _error("idempotency_conflict", "This request key belongs to another re-verification.")
                return existing["record"]
            latest = connection.execute(
                "SELECT coalesce(max(version),0) AS version FROM counterpart_evidence_revisions WHERE "
                "capture_id=%s",
                (capture_id,),
            ).fetchone()["version"]
            if latest != expected_version:
                _error(
                    "stale_decision_version", "Another verifier revision was recorded. Reload its history."
                )
            if (
                capture_digest(self._load(connection, session_id, capture_id), session["recipe_snapshot"])
                != original_hash
            ):
                _error("stale_evidence", "Original evidence changed while verification was running.")
            connection.execute(
                "INSERT INTO counterpart_evidence_revisions "
                "(id,session_id,capture_id,version,request_key,request_fingerprint,evidence_sha256,record) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    record["id"],
                    session_id,
                    capture_id,
                    record["version"],
                    key,
                    request_hash,
                    original_hash,
                    Jsonb(record),
                ),
            )
            self.repository._event(
                connection, session_id, "evidence_reverified", capture_id=capture_id, decision_id=record["id"]
            )
        return record

    def review_attempt(
        self,
        session_id,
        capture_id,
        *,
        decision,
        expected_version,
        expected_evidence_sha256,
        reason,
        idempotency_key,
        reviewer="workspace-operator",
    ):
        if decision not in {"confirmed_uncertain", "rejected", "needs_recapture"}:
            _error("invalid_review", "Choose a supported attempt review decision.")
        reason, reviewer = _text(reason, "Reason", 2000), _text(reviewer, "Reviewer", 200)
        key = _key(idempotency_key)
        request_hash = digest(
            {
                "decision": decision,
                "expected_version": expected_version,
                "evidence_sha256": expected_evidence_sha256,
                "reason": reason,
                "reviewer": reviewer,
            }
        )
        with self.repository._connection() as connection:
            session = _json(self.repository._session(connection, session_id, lock=True))
            capture = self._load(connection, session_id, capture_id)
            existing = connection.execute(
                "SELECT request_fingerprint,record FROM counterpart_attempt_reviews WHERE capture_id=%s "
                "AND request_key=%s",
                (capture_id, key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != request_hash:
                    _error("idempotency_conflict", "This key belongs to another attempt review.")
                return existing["record"]
            if capture_digest(capture, session["recipe_snapshot"]) != expected_evidence_sha256:
                _error("stale_evidence", "Reload this original attempt before reviewing it.")
            latest = connection.execute(
                "SELECT coalesce(max(version),0) AS version FROM counterpart_attempt_reviews WHERE "
                "capture_id=%s",
                (capture_id,),
            ).fetchone()["version"]
            if latest != expected_version:
                _error("stale_review_version", "Another operator reviewed this attempt. Reload it.")
            if capture["status"] in {"queued", "running"}:
                _error("unfinished_evidence", "Wait for the original machine observation to finish.")
            record = {
                "schema_version": "counterpart.attempt-review.v1",
                "id": str(uuid4()),
                "capture_id": capture_id,
                "version": latest + 1,
                "decision": decision,
                "evidence_sha256": expected_evidence_sha256,
                "reason": reason,
                "reviewer": reviewer,
                "created_at": datetime.now(UTC).isoformat(),
            }
            record["record_sha256"] = digest(record)
            connection.execute(
                "INSERT INTO counterpart_attempt_reviews "
                "(id,session_id,capture_id,version,request_key,request_fingerprint,record) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s)",
                (record["id"], session_id, capture_id, record["version"], key, request_hash, Jsonb(record)),
            )
            return record

    def authorize_baseline(
        self,
        session_id,
        capture_id,
        *,
        expected_session_revision,
        expected_evidence_sha256,
        answer,
        criteria,
        reason,
        idempotency_key,
        reviewer="workspace-operator",
    ):
        from .verification import baseline_authorization_binding

        key = _key(idempotency_key)
        reason, reviewer = _text(reason, "Reason", 2000), _text(reviewer, "Reviewer", 200)
        request_hash = digest(
            {
                "capture_id": capture_id,
                "expected_session_revision": expected_session_revision,
                "expected_evidence_sha256": expected_evidence_sha256,
                "answer": answer,
                "criteria": criteria,
                "reason": reason,
                "reviewer": reviewer,
            }
        )
        with self.repository._connection() as connection:
            session = _json(self.repository._session(connection, session_id))
            existing = connection.execute(
                "SELECT request_fingerprint,record FROM counterpart_baseline_authorizations "
                "WHERE session_id=%s AND request_key=%s",
                (session_id, key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != request_hash:
                    _error("idempotency_conflict", "This key belongs to another baseline authorization.")
                return existing["record"]
            capture = self._load(connection, session_id, capture_id)
        snapshot = validate_recipe_snapshot(session["recipe_snapshot"])
        if capture_digest(capture, snapshot) != expected_evidence_sha256:
            _error("stale_evidence", "Reload the original baseline before authorizing it.")
        validate_structure(capture, snapshot, media_reader=self.media.read)
        allowed = baseline_eligibility(capture, snapshot)
        if not allowed["allowed"]:
            _error(allowed["code"], allowed["message"])
        if digest(evaluate(capture, snapshot, None, media_reader=self.media.read)) != digest(
            capture["result"]
        ):
            _error("invalid_evidence", "The original baseline decision differs from its saved observations.")
        bindings = baseline_authorization_binding(
            snapshot, {**capture["result"], "frames": capture["frames"]}
        )
        execution = session.get("execution_contract")
        if execution:
            validate_execution_contract(execution, mode=session["mode"])
            for frame in capture["frames"]:
                assert_observation_matches(frame, execution, session["mode"])
        record = {
            "schema_version": "counterpart.baseline-authorization/1",
            "decision": "allow_counterpart",
            "id": str(uuid4()),
            "version": 1,
            "session_id": session_id,
            "capture_id": capture_id,
            "evidence_sha256": expected_evidence_sha256,
            **bindings,
            "execution_contract_digest": execution["digest"] if execution else None,
            "criteria": deepcopy(criteria),
            "answer": answer,
            "reason": reason,
            "reviewer": reviewer,
            "created_at": datetime.now(UTC).isoformat(),
        }
        record["record_sha256"] = digest(record)
        validate_baseline_authorization(record, capture, snapshot, execution)
        with self.repository._connection() as connection:
            current = _json(self.repository._session(connection, session_id, lock=True))
            existing = connection.execute(
                "SELECT request_fingerprint,record FROM counterpart_baseline_authorizations "
                "WHERE session_id=%s AND request_key=%s",
                (session_id, key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != request_hash:
                    _error("idempotency_conflict", "This key belongs to another baseline authorization.")
                return existing["record"]
            if current["revision"] != expected_session_revision:
                _error("stale_revision", "A newer capture changed this session. Reload it.")
            if current["state"] == "published" or current.get("baseline_authorization") is not None:
                _error(
                    "baseline_already_authorized",
                    "This session already has a publication or baseline authorization.",
                )
            if str(current["latest_capture_id"]) != capture_id:
                _error("stale_evidence", "Only the current initial observation can be authorized.")
            if (
                capture_digest(self._load(connection, session_id, capture_id), snapshot)
                != expected_evidence_sha256
            ):
                _error("stale_evidence", "The original baseline changed while it was reviewed.")
            if connection.execute(
                "SELECT 1 FROM counterpart_jobs j JOIN counterpart_captures c ON c.id=j.capture_id "
                "WHERE j.session_id=%s AND c.slot IN ('baseline','counterpart') "
                "AND j.status IN ('pending','running','retry_wait')",
                (session_id,),
            ).fetchone():
                _error("unfinished_evidence", "Wait for committed observations to finish.")
            connection.execute(
                "INSERT INTO counterpart_baseline_authorizations "
                "(id,session_id,capture_id,request_key,request_fingerprint,record) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (record["id"], session_id, capture_id, key, request_hash, Jsonb(record)),
            )
            connection.execute(
                "UPDATE counterpart_sessions SET baseline_id=%s,counterpart_id=NULL,state='baseline_ready',"
                "baseline_authorization=%s,revision=revision+1,"
                "recovery_required=(recovery_required OR %s) WHERE id=%s",
                (capture_id, Jsonb(record), capture["epoch"] != current["epoch"], session_id),
            )
            self.repository._supersede(connection, session_id, ("preview", "recovery"))
            self.repository._event(
                connection,
                session_id,
                "baseline_human_authorized",
                capture_id=capture_id,
                authorization_id=record["id"],
            )
            return record

    def adjudicate(
        self,
        session_id,
        *,
        baseline_id,
        counterpart_id,
        expected_session_revision,
        expected_evidence_sha256,
        labels,
        criteria,
        reason,
        idempotency_key,
        reviewer="workspace-operator",
    ):
        key = _key(idempotency_key)
        reason, reviewer = _text(reason, "Reason", 2000), _text(reviewer, "Reviewer", 200)
        request = {
            "baseline_id": baseline_id,
            "counterpart_id": counterpart_id,
            "expected_session_revision": expected_session_revision,
            "evidence_sha256": expected_evidence_sha256,
            "labels": labels,
            "criteria": criteria,
            "reason": reason,
            "reviewer": reviewer,
        }
        request_hash = digest(request)
        with self.repository._connection() as connection:
            existing = connection.execute(
                "SELECT request_fingerprint,pair_id FROM counterpart_evidence_adjudications WHERE "
                "session_id=%s AND request_key=%s",
                (_uuid(session_id, "Session"), key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != request_hash:
                    _error("idempotency_conflict", "This key belongs to another adjudication.")
                return _json(
                    connection.execute(
                        "SELECT * FROM counterpart_pairs WHERE id=%s", (existing["pair_id"],)
                    ).fetchone()
                )
            session = _json(self.repository._session(connection, session_id))
            baseline, counterpart = (
                self._load(connection, session_id, baseline_id),
                self._load(connection, session_id, counterpart_id),
            )
        if baseline_id == counterpart_id or baseline["revision"] >= counterpart["revision"]:
            _error("invalid_evidence_pair", "Choose two distinct ordered original capture attempts.")
        if pair_digest(session, baseline, counterpart) != expected_evidence_sha256:
            _error("stale_evidence", "Reload both original observations before adjudicating them.")
        for capture in (baseline, counterpart):
            validate_structure(capture, session["recipe_snapshot"], media_reader=self.media.read)
        pair_id = str(uuid4())
        record = {
            "schema_version": "counterpart.adjudication.v1",
            "id": str(uuid4()),
            "version": 1,
            "assigned_roles": {"baseline": baseline_id, "counterpart": counterpart_id},
            "evidence_sha256": expected_evidence_sha256,
            "labels": deepcopy(labels),
            "criteria": deepcopy(criteria),
            "reviewed_controls": session["recipe_snapshot"]["controls"],
            "reason": reason,
            "reviewer": reviewer,
            "created_at": datetime.now(UTC).isoformat(),
        }
        record["record_sha256"] = digest(record)
        manifest = {
            "schema_version": "counterpart.pair.v2",
            "pair_id": pair_id,
            "session_id": session_id,
            "recipe": session["recipe"],
            "mode": session["mode"],
            "revision": expected_session_revision,
            "objects": {"a": session["object_a"], "b": session["object_b"]},
            "recipe_snapshot": session["recipe_snapshot"],
            "campaign_id": session.get("campaign_id"),
            **manifest_split(session),
            "baseline": baseline,
            "counterpart": counterpart,
            "evidence_adjudication": record,
            "provenance": {
                "evidence_kind": "fixture_replay" if session["mode"] == "replay" else "roboflow_capture",
                "human_reviewed": True,
                "legacy_recipe_snapshot_missing": False,
                "label_origin": "human_adjudication",
            },
        }
        for optional in ("execution_contract", "split_provenance"):
            if session.get(optional) is not None:
                manifest[optional] = session[optional]
        if counterpart.get("baseline_authorization") is not None:
            manifest["baseline_authorization"] = counterpart["baseline_authorization"]
        validate_adjudication(manifest, media_reader=self.media.read)
        with self.repository._connection() as connection:
            current = _json(self.repository._session(connection, session_id, lock=True))
            existing = connection.execute(
                "SELECT request_fingerprint,pair_id FROM counterpart_evidence_adjudications WHERE "
                "session_id=%s AND request_key=%s",
                (session_id, key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != request_hash:
                    _error("idempotency_conflict", "This key belongs to another adjudication.")
                return _json(
                    connection.execute(
                        "SELECT * FROM counterpart_pairs WHERE id=%s", (existing["pair_id"],)
                    ).fetchone()
                )
            if current["revision"] != expected_session_revision:
                _error("stale_revision", "Another capture was saved. Reload the session before adjudicating.")
            if current["state"] == "published":
                _error("already_published", "This session already has an immutable pair.")
            if connection.execute(
                "SELECT 1 FROM counterpart_jobs j JOIN counterpart_captures c ON c.id=j.capture_id WHERE "
                "j.session_id=%s AND c.slot IN ('baseline','counterpart') AND j.status IN "
                "('pending','running','retry_wait')",
                (session_id,),
            ).fetchone():
                _error("unfinished_evidence", "Wait for pending committed observations to finish.")
            if (
                pair_digest(
                    current,
                    self._load(connection, session_id, baseline_id),
                    self._load(connection, session_id, counterpart_id),
                )
                != expected_evidence_sha256
            ):
                _error("stale_evidence", "Evidence changed while it was being reviewed.")
            status = "disabled_replay" if session["mode"] == "replay" else "local_only"
            connection.execute(
                "INSERT INTO counterpart_pairs (id,session_id,manifest,export_status) VALUES (%s,%s,%s,%s)",
                (pair_id, session_id, Jsonb(manifest), status),
            )
            connection.execute(
                "INSERT INTO counterpart_export_intents (id,pair_id,status) VALUES (%s,%s,'disabled')",
                (str(uuid4()), pair_id),
            )
            connection.execute(
                "INSERT INTO counterpart_evidence_adjudications "
                "(id,session_id,pair_id,request_key,request_fingerprint,record) VALUES (%s,%s,%s,%s,%s,%s)",
                (record["id"], session_id, pair_id, key, request_hash, Jsonb(record)),
            )
            connection.execute("UPDATE counterpart_sessions SET state='published' WHERE id=%s", (session_id,))
            self.repository._supersede(connection, session_id, ("preview", "recovery"))
            self.repository._event(
                connection,
                session_id,
                "pair_human_adjudicated",
                pair_id=pair_id,
                adjudication_id=record["id"],
            )
            return _json(
                connection.execute("SELECT * FROM counterpart_pairs WHERE id=%s", (pair_id,)).fetchone()
            )
