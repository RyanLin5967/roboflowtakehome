"""Frozen model contracts: real PostgreSQL and fake HTTP, never model compute."""

from __future__ import annotations

import base64
import hashlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import psycopg
import pytest
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from counterpart.config import Settings
from counterpart.execution_contract import (
    ExecutionContractError,
    assert_execution_matches,
    build_execution_contract,
    canonical,
    normalize_legacy_configuration,
    validate_execution_contract,
)
from counterpart.inference_runtime import InferenceRuntime
from counterpart.providers import RoboflowDetector
from counterpart.recipes import build_recipe_snapshot
from counterpart.repository import Conflict, Repository
from counterpart.storage import MediaStore
from counterpart.worker import Worker


@pytest.fixture
def environment(tmp_path):
    dsn = os.environ.get("COUNTERPART_TEST_DSN")
    if not dsn:
        pytest.skip("COUNTERPART_TEST_DSN required for isolated PostgreSQL evidence")
    schema = "cp_contract_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repo = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    repo.initialize()
    media = MediaStore(tmp_path / "media")
    config = Settings(database_url=repo.dsn, media_dir=media.root, live_enabled=True, inference_limit=10)
    try:
        yield repo, media, config
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def frame(media, color="navy"):
    out = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(out, "PNG")
    return {**media.put(out.getvalue()), "t_ms": 0}


def create(repo, config, *, legacy=False):
    # Frozen v1 intentionally isolates model pin semantics from v2 visual checks.
    with repo._connection() as connection:
        if legacy:
            identifier = str(uuid4())
            connection.execute(
                "INSERT INTO counterpart_sessions(id,capability_hash,recipe,mode,object_a,object_b,"
                "recipe_snapshot) VALUES (%s,'test','spatial','live','bottle','cup',%s)",
                (identifier, Jsonb(build_recipe_snapshot("spatial"))),
            )
            return repo._snapshot(connection, identifier)
        return repo._create_session(
            connection,
            "spatial",
            "live",
            "bottle",
            "cup",
            recipe_snapshot=build_recipe_snapshot("spatial"),
            execution_contract=build_execution_contract(config),
        )["session"]


def submit(repo, session, source, slot="baseline"):
    return repo.submit(
        session["id"],
        {
            "slot": slot,
            "idempotency_key": uuid4().hex,
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "client_seq": 0,
        },
        [source],
        uuid4().hex,
    )


def provider(calls, *, model="coco/39"):
    def respond(request):
        calls.append(base64.b64decode(request.content))
        return httpx.Response(
            200,
            json={
                "image": {"width": 800, "height": 600},
                "model_id": model,
                "predictions": [
                    {"class": "bottle", "confidence": 0.95, "x": 160, "y": 300, "width": 72, "height": 108},
                    {"class": "cup", "confidence": 0.94, "x": 400, "y": 300, "width": 72, "height": 108},
                ],
            },
        )

    return RoboflowDetector(
        "test-no-provider-key", model, client=httpx.Client(transport=httpx.MockTransport(respond))
    )


def spend(repo):
    return sum(row["reserved_units"] for row in repo.metrics()["budget_reservations"])


def test_canonical_detached_and_no_secrets():
    config = Settings(roboflow_model="custom-model/7")
    contract = build_execution_contract(config)
    assert contract["configuration"]["model_id"] == "custom-model/7"
    assert contract["configuration"]["endpoint"] == "https://serverless.roboflow.com"
    assert validate_execution_contract(contract) == contract
    changed = {**contract, "configuration": {**contract["configuration"], "api_key": "excluded"}}
    with pytest.raises(ExecutionContractError):
        validate_execution_contract(changed)
    changed = {**contract, "digest": "0" * 64}
    with pytest.raises(ExecutionContractError):
        validate_execution_contract(changed)


@pytest.mark.parametrize(
    "key,value",
    [
        ("model_id", "coco/40"),
        ("endpoint", "https://another.example"),
        ("adapter", "another.Adapter"),
        ("adapter_version", "serverless-v0-v2"),
        ("normalizer_version", "rf-xyxy-v2"),
        ("confidence_threshold", 0.4),
        ("disable_active_learning", False),
    ],
)
def test_any_semantic_change_refused(key, value):
    contract = build_execution_contract()
    with pytest.raises(ExecutionContractError, match="differs"):
        assert_execution_matches(contract, {**contract["configuration"], key: value})


@pytest.mark.postgres
def test_model_a_baseline_then_worker_b_refused_before_calls_or_reservations(environment):
    repo, media, config = environment
    session = create(repo, config)
    assert session["execution_contract"] == build_execution_contract(config)
    calls_a, calls_b = [], []
    with provider(calls_a) as detector_a, provider(calls_b, model="coco/40") as detector_b:
        submit(repo, session, frame(media))
        assert Worker(repo, media, config, detector_a).once()
        first = repo.snapshot(session["id"])
        assert first["baseline"]["status"] == "accepted"
        assert len(calls_a) == spend(repo) == 1
        submit(repo, first, frame(media, "gold"), slot="counterpart")
        assert Worker(repo, media, config, detector_b).once()
        final = repo.snapshot(session["id"])
        assert final["latest_capture"]["error"] == "execution_contract_mismatch"
        assert final["baseline"] == first["baseline"]
        assert final["execution_contract"] == first["execution_contract"]
        assert calls_b == [] and spend(repo) == 1


@pytest.mark.postgres
def test_runtime_mismatch_refuses_before_ddl_media_budget(environment):
    repo, media, config = environment
    source = frame(media)
    calls = []
    with provider(calls) as detector:
        runtime = InferenceRuntime(repo, media, config, detector)
        wrong = build_execution_contract(Settings(roboflow_model="coco/40"))
        with pytest.raises(ExecutionContractError):
            runtime.observe_frames(
                [source], session_id="not-even-a-session", job_key="none", execution_contract=wrong
            )
        assert not runtime._initialized and calls == [] and spend(repo) == 0
        with repo._connection() as connection:
            assert (
                connection.execute("SELECT to_regclass('counterpart_inference_cache')").fetchone()[
                    "to_regclass"
                ]
                is None
            )


@pytest.mark.postgres
def test_legacy_no_contract_refuses_without_reinterpreting(environment):
    repo, media, config = environment
    old = create(repo, config, legacy=True)
    submit(repo, old, frame(media))
    calls = []
    with provider(calls) as detector:
        assert Worker(repo, media, config, detector).once()
    saved = repo.snapshot(old["id"])
    assert saved["execution_contract"] is None
    assert saved["latest_capture"]["error"] == "execution_contract_migration_required"
    assert calls == [] and spend(repo) == 0
    repo.initialize()
    assert repo.snapshot(old["id"])["execution_contract"] is None


@pytest.mark.postgres
def test_legacy_adoption_is_explicit_audited_and_concurrently_immutable(environment):
    repo, _, config = environment
    old = create(repo, config, legacy=True)
    first, second = (
        build_execution_contract(config),
        build_execution_contract(Settings(roboflow_model="coco/40")),
    )

    def adopt(contract):
        try:
            return repo.migrate_execution_contract(
                old["id"],
                expected_revision=0,
                reviewer="operator",
                reason="Pin untouched legacy session",
                execution_contract=contract,
            )["execution_contract"]
        except Conflict as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(adopt, [first, second]))
    assert outcomes.count("execution_contract_frozen") == 1
    saved = repo.snapshot(old["id"])
    assert saved["execution_contract"] in (first, second)
    assert sum(event["kind"] == "execution_contract_migrated" for event in saved["events"]) == 1
    with repo._connection() as connection, pytest.raises(psycopg.errors.RaiseException):
        connection.execute(
            "UPDATE counterpart_sessions SET execution_contract=NULL WHERE id=%s", (old["id"],)
        )


@pytest.mark.postgres
def test_legacy_adoption_requires_known_evidence_and_preserves_original_bytes(environment):
    repo, media, config = environment
    old = create(repo, config, legacy=True)
    source = frame(media)
    submit(repo, old, source)
    calls = []
    with provider(calls) as detector:
        runtime = InferenceRuntime(repo, media, config, detector)
        observation = runtime.observe_frames([source], session_id=old["id"], job_key="legacy")[0]
    job = repo.claim("legacy-test")
    result = {"accepted": True, "checks": [], "evidence": {"fixture": True}}
    assert repo.finish(job["id"], job["lease_token"], result, [observation])
    before = repo.evaluation_snapshot(old["id"])
    pinned = repo.migrate_execution_contract(
        old["id"], expected_revision=1, reviewer="operator", reason="Derive pin from recorded cache evidence"
    )
    after = repo.evaluation_snapshot(old["id"])
    assert pinned["execution_contract"] == build_execution_contract(config)
    assert before["captures"] == after["captures"]
    assert before["pairs"] == after["pairs"]
    assert spend(repo) == 1
    assert repo.assert_execution_contract(old["id"], runtime.model_config)


@pytest.mark.postgres
def test_unknown_legacy_model_is_not_filled_from_current_worker(environment):
    repo, media, config = environment
    old = create(repo, config, legacy=True)
    submit(repo, old, frame(media))
    job = repo.claim("legacy-test")
    unknown = {**job["capture"]["frames"][0], "model": {"provider": "roboflow", "model_id": "coco/39"}}
    repo.finish(job["id"], job["lease_token"], {"accepted": True, "checks": [], "evidence": {}}, [unknown])
    with pytest.raises(Conflict) as caught:
        repo.migrate_execution_contract(
            old["id"],
            expected_revision=1,
            reviewer="operator",
            reason="Cannot guess missing provenance",
            execution_contract=build_execution_contract(config),
        )
    assert caught.value.code == "legacy_model_unknown"
    assert repo.snapshot(old["id"])["execution_contract"] is None


@pytest.mark.postgres
def test_exact_legacy_endpoint_mapping_preserves_old_observation_digest(environment):
    repo, media, config = environment
    old = create(repo, config, legacy=True)
    old_configuration = {
        key: value
        for key, value in build_execution_contract(config)["configuration"].items()
        if key != "endpoint"
    }
    source = frame(media)
    submit(repo, old, source)
    calls = []
    with provider(calls) as detector:
        runtime = InferenceRuntime(repo, media, config, detector, detector_configuration=old_configuration)
        observation = runtime.observe_frames([source], session_id=old["id"], job_key="old-runtime")[0]
    # Initial runtime did not include the complete config on the observation.
    observation["inference"].pop("configuration")
    old_digest = hashlib.sha256(canonical(old_configuration).encode()).hexdigest()
    assert observation["inference"]["configuration_digest"] == old_digest
    job = repo.claim("legacy")
    repo.finish(
        job["id"], job["lease_token"], {"accepted": True, "checks": [], "evidence": {}}, [observation]
    )
    before = repo.snapshot(old["id"])["baseline"]
    migrated = repo.migrate_execution_contract(
        old["id"], expected_revision=1, reviewer="operator", reason="Explicit known adapter endpoint mapping"
    )
    assert migrated["baseline"] == before
    assert migrated["execution_contract"] == build_execution_contract(config)
    assert migrated["events"][-1]["data"]["legacy_endpoint_mapping"] is True
    repo.assert_execution_contract(old["id"], build_execution_contract(config)["configuration"])
    wrong = {**old_configuration, "adapter_version": "unknown-v9"}
    assert normalize_legacy_configuration(wrong) == wrong


@pytest.mark.postgres
def test_partial_crash_observations_prevent_conflicting_migration(environment):
    repo, media, config = environment
    old = create(repo, config, legacy=True)
    source = frame(media)
    calls = []
    with provider(calls) as detector:
        runtime = InferenceRuntime(repo, media, config, detector)
        runtime.observe_frames([source], session_id=old["id"], job_key="crashed-before-finish")
    with pytest.raises(Conflict) as caught:
        repo.migrate_execution_contract(
            old["id"],
            expected_revision=0,
            reviewer="operator",
            reason="Wrong model must fail",
            execution_contract=build_execution_contract(Settings(roboflow_model="coco/40")),
        )
    assert caught.value.code == "legacy_model_conflict"
    assert repo.snapshot(old["id"])["execution_contract"] is None
    assert spend(repo) == 1


@pytest.mark.postgres
def test_mutated_adapter_identity_is_refused_before_dispatch(environment):
    from counterpart.providers import ProviderError

    repo, media, config = environment
    source = frame(media)
    calls = []
    with provider(calls) as detector:
        runtime = InferenceRuntime(repo, media, config, detector)
        detector.model_id = "coco/40"
        with pytest.raises(ProviderError) as caught:
            runtime.observe_frames(
                [source],
                session_id="no-dispatch",
                job_key="none",
                execution_contract=build_execution_contract(config),
            )
        assert caught.value.code == "detector_configuration_changed"
        assert not runtime._initialized and calls == [] and spend(repo) == 0


@pytest.mark.postgres
def test_returned_observation_cannot_mutate_runtime_configuration(environment):
    repo, media, config = environment
    source = frame(media)
    calls = []
    with provider(calls) as detector:
        runtime = InferenceRuntime(repo, media, config, detector)
        contract = build_execution_contract(config)
        result = runtime.observe_frames(
            [source],
            session_id="first",
            job_key="one",
            execution_contract=contract,
        )[0]
        result["inference"]["configuration"]["model_id"] = "coco/40"
        public_copy = runtime.model_config
        public_copy["confidence_threshold"] = 0.99
        assert runtime.model_config == contract["configuration"]
        second = runtime.observe_frames(
            [source],
            session_id="second",
            job_key="two",
            execution_contract=contract,
        )[0]
        assert second["inference"]["source"] == "cache"
        assert second["inference"]["configuration"] == contract["configuration"]
        assert len(calls) == spend(repo) == 1


@pytest.mark.postgres
def test_baseline_with_wrong_recorded_model_is_refused_even_with_right_digest(environment):
    from counterpart.execution_contract import assert_observation_matches

    _, _, config = environment
    contract = build_execution_contract(config)
    wrong = {
        "model": {"provider": "roboflow", "model_id": "coco/40"},
        "inference": {"configuration_digest": contract["digest"]},
    }
    with pytest.raises(ExecutionContractError) as caught:
        assert_observation_matches(wrong, contract, "live")
    assert caught.value.code == "execution_evidence_mismatch"


def test_contract_parameters_match_actual_builtin_wire_request():
    contract = build_execution_contract()["configuration"]
    seen = []

    def respond(request):
        seen.append(request)
        assert str(request.url).split("?")[0] == f"{contract['endpoint']}/{contract['model_id']}"
        assert request.url.params["confidence"] == str(contract["confidence_threshold"])
        assert request.url.params["disable_active_learning"] == "true"
        return httpx.Response(
            200,
            json={
                "image": {"width": 80, "height": 80},
                "predictions": [],
                "model_id": contract["model_id"],
            },
        )

    image = io.BytesIO()
    Image.new("RGB", (80, 80), "navy").save(image, "PNG")
    with RoboflowDetector(
        "test-no-provider-key", client=httpx.Client(transport=httpx.MockTransport(respond))
    ) as detector:
        result = detector.detect(image.getvalue(), 80, 80)
        for key in ("provider", "model_id", "normalizer_version", "confidence_threshold"):
            assert result["model"][key] == contract[key]
    assert len(seen) == 1


@pytest.mark.parametrize("malformed", [None, [], 1, "not-a-record"])
def test_malformed_observation_provenance_fails_closed(malformed):
    from counterpart.execution_contract import assert_observation_matches

    contract = build_execution_contract()
    frame = {"model": {"provider": "roboflow", "model_id": "coco/39"}, "inference": malformed}
    with pytest.raises(ExecutionContractError):
        assert_observation_matches(frame, contract, "live")
