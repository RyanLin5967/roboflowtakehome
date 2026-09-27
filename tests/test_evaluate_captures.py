"""Evaluator arithmetic/provenance tests; these fixtures are NOT physical validation."""

import hashlib
import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate_captures.py"
spec = importlib.util.spec_from_file_location("evaluate_captures", SCRIPT)
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


def capture(name, status="accepted", slot="baseline", recipe="spatial"):
    digest = hashlib.sha256(name.encode()).hexdigest()
    evidence = {"relation": "left" if slot == "baseline" else "right"}
    if recipe == "temporal":
        evidence = {"arrival_order": ["bottle", "cup"] if slot == "baseline" else ["cup", "bottle"]}
    result = {
        "accepted": status == "accepted",
        "code": "accepted" if status == "accepted" else "missing_object",
        "verifier_version": "test-only/1",
        "evidence": evidence,
    }
    return {
        "id": name,
        "slot": slot,
        "status": status,
        "result": result,
        "frames": [{"asset_id": digest, "t_ms": 0, "model": {"provider": "roboflow", "model_id": "test"}}],
    }


def document(session="session1", recipe="spatial", statuses=("accepted", "rejected", "accepted")):
    captures = [
        capture(f"{session}-{i}", status, "baseline" if i == 0 else "counterpart", recipe)
        for i, status in enumerate(statuses)
    ]
    for attempt in captures[1:]:
        attempt["reference_capture_id"] = captures[0]["id"]
    return {
        "schema_version": "counterpart.evaluation.session.v1",
        "session_id": session,
        "mode": "live",
        "provenance": {"evidence_kind": "roboflow_capture"},
        "recipe": recipe,
        "captures_complete": True,
        "captures": captures,
        "pairs": [],
    }


def load(tmp_path, *documents):
    paths = []
    for i, doc in enumerate(documents):
        path = tmp_path / f"manifest-{i}.json"
        path.write_text(json.dumps(doc))
        paths.append(path)
    return evaluator.load_documents(paths)


def labels(rows, decisions=None):
    result = evaluator.label_template(rows)
    for i, row in enumerate(result):
        row.update(
            {
                "scene_id": "scene-" + row["session_id"],
                "reviewer_id": "independent-reviewer",
                "reviewed_without_model_output": True,
                "decision": decisions[i] if decisions else "valid",
                "observed_answer": rows[i]["answer"],
                "condition": "nominal",
            }
        )
    return result


def test_exact_denominators_and_false_accept_not_hidden(tmp_path):
    rows, pairs, complete, _ = load(tmp_path, document())
    report = evaluator.evaluate(rows, pairs, labels(rows, ["valid", "valid", "invalid"]), complete=complete)
    summary = report["summary"]
    assert summary["accepted_precision_among_decisively_reviewed"] == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert summary["valid_capture_recall"]["value"] == 0.5
    assert summary["acceptance_rate"]["denominator"] == 3
    assert summary["verifier_uncertainty_refusal"]["numerator"] == 1
    assert report["strata"]["session_id"]["session1"] == summary
    assert not report["confidence_intervals"]["available"]


def test_human_uncertainty_is_separate_not_counted_as_correct(tmp_path):
    rows, pairs, complete, _ = load(tmp_path, document())
    report = evaluator.evaluate(
        rows, pairs, labels(rows, ["uncertain", "invalid", "valid"]), complete=complete
    )
    assert report["summary"]["accepted_precision_among_decisively_reviewed"]["denominator"] == 1
    assert report["summary"]["accepted_human_uncertainty"]["value"] == 0.5


def test_wrong_answer_counts_as_false_accept_even_if_recipe_judged_valid(tmp_path):
    rows, pairs, complete, _ = load(tmp_path, document(statuses=("accepted",)))
    truth = labels(rows)
    truth[0]["observed_answer"] = "right"
    result = evaluator.evaluate(rows, pairs, truth, complete=complete)
    assert result["summary"]["accepted_precision_among_decisively_reviewed"]["value"] == 0


def test_published_only_cannot_claim_coverage_or_recall(tmp_path):
    doc = document(statuses=("accepted", "accepted"))
    pair = {
        **{key: doc[key] for key in ("session_id", "mode", "provenance", "recipe")},
        "schema_version": "counterpart.pair.v1",
        "pair_id": "pair1",
        "baseline": doc["captures"][0],
        "counterpart": doc["captures"][1],
    }
    rows, pairs, complete, _ = load(tmp_path, pair)
    report = evaluator.evaluate(rows, pairs, labels(rows), complete=complete)
    assert report["sampling_frame"] == "published_pairs_only"
    assert report["summary"]["acceptance_rate"] is None
    assert report["summary"]["valid_capture_recall"] is None
    assert report["published_pair_precision_among_decisively_reviewed"]["denominator"] == 1


def test_duplicate_document_does_not_inflate_sample_size(tmp_path):
    doc = document()
    rows, _, _, _ = load(tmp_path, doc, doc)
    assert len(rows) == 3


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda d: d.update(mode="replay"), "Only live"),
        (lambda d: d.update(captures_complete=False), "all attempts"),
        (lambda d: d["captures"][0]["frames"][0].update(fixture_detections=[]), "Fixture"),
        (lambda d: d["captures"][0]["frames"][0].update(model={}), "provider provenance"),
        (lambda d: d["captures"][0]["result"].update(accepted=False), "Decision/status"),
        (lambda d: d["captures"][0]["frames"][0].update(t_ms=float("nan")), "timestamp"),
    ],
)
def test_invalid_sources_refused(tmp_path, mutation, match):
    doc = document()
    mutation(doc)
    with pytest.raises(ValueError, match=match):
        load(tmp_path, doc)


def test_conflicting_duplicate_is_not_silently_replaced(tmp_path):
    doc = document()
    other = deepcopy(doc)
    other["captures"][0]["frames"][0]["t_ms"] = 1
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        load(tmp_path, doc, other)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("reviewed_without_model_output", False, "independent"),
        ("evidence_digest", "wrong", "evidence mismatch"),
        ("decision", None, "human decision"),
        ("scene_id", None, "scene/condition"),
        ("observed_answer", None, "observed answer"),
    ],
)
def test_incomplete_or_nonindependent_labels_refused(tmp_path, field, value, match):
    rows, _, _, _ = load(tmp_path, document())
    truth = labels(rows)
    truth[0][field] = value
    with pytest.raises(ValueError, match=match):
        evaluator.bind_labels(rows, truth)


def test_scene_and_asset_leakage_are_rejected(tmp_path):
    rows, _, _, _ = load(tmp_path, document(), document("session2"))
    truth = labels(rows)
    truth[0]["partition"] = "calibration"
    with pytest.raises(ValueError, match="Scene leaked"):
        evaluator.bind_labels(rows, truth)
    truth = labels(rows)
    for row in truth[:3]:
        row["partition"] = "calibration"
    rows[3]["assets"] = rows[0]["assets"]
    with pytest.raises(ValueError, match="Identical media leaked"):
        evaluator.bind_labels(rows, truth)


def test_calibration_excluded_and_unreviewed_attempts_reported(tmp_path):
    rows, pairs, complete, _ = load(tmp_path, document(), document("s2"))
    truth = labels(rows)
    for row in truth[:3]:
        row["partition"] = "calibration"
    report = evaluator.evaluate(rows, pairs, truth[:-1], complete=complete)
    assert report["calibration_labels_excluded"] == 3
    assert report["evaluation_labels"] == 2
    assert report["missing_human_labels"] == [rows[-1]["capture_id"]]
    assert report["capture_population"]["accepted_over_all_finished_submissions"]["denominator"] == 6


def test_scene_bootstrap_reproducible_and_clustered(tmp_path):
    docs = [document(f"s{i}", statuses=("accepted",)) for i in range(10)]
    rows, pairs, complete, _ = load(tmp_path, *docs)
    truth = labels(rows, ["invalid"] * 3 + ["valid"] * 7)
    result = evaluator.evaluate(rows, pairs, truth, complete=complete, repeats=100)
    ci = result["confidence_intervals"]
    assert ci["available"] and ci["independent_scenes"] == 10
    assert (
        ci == evaluator.evaluate(rows, pairs, truth, complete=complete, repeats=100)["confidence_intervals"]
    )
    assert ci["intervals"]["accepted_precision_among_decisively_reviewed"]["lower"] < 0.7
    assert not ci["intervals"]["acceptance_rate"]["available"]


def test_temporal_custom_objects_are_bound_to_recipe_snapshot(tmp_path):
    doc = document(recipe="temporal", statuses=("accepted",))
    doc["recipe_snapshot"] = {"object_a": "book", "object_b": "mouse"}
    doc["captures"][0]["result"]["evidence"]["arrival_order"] = ["book", "mouse"]
    rows, pairs, complete, _ = load(tmp_path, doc)
    report = evaluator.evaluate(rows, pairs, labels(rows), complete=complete)
    assert report["summary"]["accepted_precision_among_decisively_reviewed"]["value"] == 1


def test_posthoc_baseline_requires_identical_bound_attempts(tmp_path):
    rows, pairs, complete, _ = load(tmp_path, document())
    decisions = [
        {
            "schema_version": "counterpart.posthoc-decision.v1",
            "capture_id": row["capture_id"],
            "evidence_digest": row["evidence_digest"],
            "recipe_digest": row["recipe_digest"],
            "reference_capture_id": row["reference_capture_id"],
            "reference_evidence_digest": row["reference_evidence_digest"],
            "reviewed_without_model_output": True,
            "reviewer_id": "different-reviewer",
            "decision": "accepted",
            "answer": row["answer"],
        }
        for row in rows
    ]
    truth = labels(rows, ["valid", "invalid", "valid"])
    report = evaluator.evaluate(rows, pairs, truth, complete=complete, baseline=decisions)
    assert (
        report["posthoc_baseline"]["summary"]["accepted_precision_among_decisively_reviewed"]["value"]
        == 2 / 3
    )
    with pytest.raises(ValueError, match="exactly the same"):
        evaluator.evaluate(rows, pairs, truth, complete=complete, baseline=decisions[:-1])


def test_cli_template_does_not_invent_judgments_or_overwrite(tmp_path):
    doc = document()
    source = tmp_path / "source.json"
    source.write_text(json.dumps(doc))
    template = tmp_path / "labels.jsonl"
    completed = subprocess.run(
        ["python3", str(SCRIPT), str(source), "--write-label-template", str(template)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    records = evaluator.load_jsonl(template)
    assert all(row["decision"] is None and row["observed_answer"] is None for row in records)
    assert "accepted" not in records[0] and "answer" not in records[0]
    again = subprocess.run(
        ["python3", str(SCRIPT), str(source), "--write-label-template", str(template)],
        capture_output=True,
        text=True,
    )
    assert again.returncode != 0


def test_cli_report_preserves_input_hashes(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(document()))
    rows, _, _, _ = evaluator.load_documents([source])
    truth = tmp_path / "human.jsonl"
    truth.write_text("\n".join(json.dumps(row) for row in labels(rows)))
    output = tmp_path / "report.json"
    completed = subprocess.run(
        ["python3", str(SCRIPT), str(source), "--labels", str(truth), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text())
    assert report["human_labels_sha256"] == hashlib.sha256(truth.read_bytes()).hexdigest()
    assert report["input_files"][0]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_many_sessions_in_one_scene_do_not_create_fake_independent_samples(tmp_path):
    rows, pairs, complete, _ = load(tmp_path, *[document(f"s{i}") for i in range(12)])
    truth = labels(rows)
    for judgment in truth:
        judgment["scene_id"] = "same-desk-and-props"
    report = evaluator.evaluate(rows, pairs, truth, complete=complete)
    assert report["summary"]["sessions"] == 12
    assert report["summary"]["scenes"] == 1
    assert not report["confidence_intervals"]["available"]


def test_changed_recipe_cannot_reuse_judgments_of_same_pixels(tmp_path):
    original = document()
    rows, _, _, _ = load(tmp_path, original)
    truth = labels(rows)
    altered = deepcopy(original)
    altered["recipe_snapshot"] = {"object_a": "book", "object_b": "cup", "revision": 2}
    new_rows, _, _, _ = load(tmp_path, altered)
    with pytest.raises(ValueError, match="evidence mismatch"):
        evaluator.bind_labels(new_rows, truth)


def test_empty_acceptance_denominator_is_unknown_not_perfect(tmp_path):
    rows, pairs, complete, _ = load(tmp_path, document(statuses=("rejected",)))
    report = evaluator.evaluate(rows, pairs, labels(rows, ["invalid"]), complete=complete)
    assert report["summary"]["accepted_precision_among_decisively_reviewed"] == {
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }
    assert report["summary"]["valid_capture_recall"]["value"] is None


def test_counterpart_review_requires_its_original_reference_not_latest_pointer(tmp_path):
    doc = document()
    del doc["captures"][2]["reference_capture_id"]
    rows, pairs, complete, _ = load(tmp_path, doc)
    truth = labels(rows)
    with pytest.raises(ValueError, match="historical reference"):
        evaluator.evaluate(rows, pairs, truth, complete=complete)
    truth[2]["decision"] = "uncertain"
    result = evaluator.evaluate(rows, pairs, truth, complete=complete)
    assert result["counterparts_without_historical_reference"] == [rows[2]["capture_id"]]
    assert result["summary"]["accepted_human_uncertainty"]["numerator"] == 1
