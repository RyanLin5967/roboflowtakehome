"""Frozen, credential-free perception semantics for a collection.

This records the versioned provider identifier and our wire/normalization
contract. It is not a claim that a remote vendor cryptographically pins weights.
Scheduling limits, credentials and timeouts do not change model semantics and
are intentionally excluded.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

SCHEMA_VERSION = "counterpart.execution.v1"
_MODEL_KEYS = {
    "provider",
    "model_id",
    "adapter",
    "adapter_version",
    "normalizer_version",
    "confidence_threshold",
    "disable_active_learning",
    "endpoint",
}


class ExecutionContractError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def detector_configuration(config=None, detector=None) -> dict:
    return {
        "provider": "roboflow",
        "endpoint": "https://serverless.roboflow.com",
        "model_id": getattr(detector, "model_id", getattr(config, "roboflow_model", "coco/39")),
        "adapter": (
            f"{type(detector).__module__}.{type(detector).__qualname__}"
            if detector is not None
            else "counterpart.providers.RoboflowDetector"
        ),
        "adapter_version": "serverless-v0-v1",
        "normalizer_version": "rf-xyxy-v1",
        "confidence_threshold": 0.25,
        "disable_active_learning": True,
    }


def _configuration(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != _MODEL_KEYS:
        raise ExecutionContractError("invalid_execution_contract", "Unexpected model configuration fields")
    for key in _MODEL_KEYS - {"confidence_threshold", "disable_active_learning"}:
        item = value[key]
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.:/<>-]{1,200}", item):
            raise ExecutionContractError(
                "invalid_execution_contract", "Invalid model configuration identifier"
            )
    threshold = value["confidence_threshold"]
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 1
        or value["disable_active_learning"] is not True
    ):
        raise ExecutionContractError("invalid_execution_contract", "Invalid model inference policy")
    return json.loads(canonical(value))


def build_execution_contract(
    config=None, *, mode: str = "live", detector=None, detector_configuration: dict | None = None
) -> dict:
    if mode not in {"live", "replay"}:
        raise ExecutionContractError("invalid_execution_contract", "Unsupported collection mode")
    configuration = detector_configuration
    if configuration is None:
        if mode == "replay":
            configuration = {
                "provider": "fixture",
                "model_id": "illustrative-replay-v1",
                "endpoint": "fixture://local",
                "adapter": "counterpart.worker.fixture",
                "adapter_version": "1",
                "normalizer_version": "fixture-normalized-v1",
                "confidence_threshold": 0.0,
                "disable_active_learning": True,
            }
        else:
            configuration = globals()["detector_configuration"](config, detector)
    configuration = _configuration(configuration)
    if (mode == "replay") != (configuration["provider"] == "fixture"):
        raise ExecutionContractError(
            "invalid_execution_contract", "Model provider does not match collection mode"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "configuration": configuration,
        "digest": hashlib.sha256(canonical(configuration).encode()).hexdigest(),
    }


def validate_execution_contract(value: dict, *, mode: str | None = None) -> dict:
    if mode is not None and mode not in {"live", "replay"}:
        raise ExecutionContractError("invalid_execution_contract", "Unsupported collection mode")
    if not isinstance(value, dict) or set(value) != {"schema_version", "configuration", "digest"}:
        raise ExecutionContractError(
            "execution_contract_missing", "The collection has no valid model contract"
        )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ExecutionContractError("invalid_execution_contract", "Unsupported model contract version")
    configuration = _configuration(value["configuration"])
    digest = hashlib.sha256(canonical(configuration).encode()).hexdigest()
    if value["digest"] != digest:
        raise ExecutionContractError("invalid_execution_contract", "Model contract digest does not match")
    if mode is not None and (mode == "replay") != (configuration["provider"] == "fixture"):
        raise ExecutionContractError(
            "invalid_execution_contract", "Model provider does not match collection mode"
        )
    return json.loads(canonical(value))


def assert_execution_matches(contract: dict, configuration: dict) -> dict:
    saved = validate_execution_contract(contract)
    if canonical(saved["configuration"]) != canonical(configuration):
        raise ExecutionContractError(
            "execution_contract_mismatch", "This worker's detector differs from the frozen collection model"
        )
    return saved


def normalize_legacy_configuration(value: dict) -> dict:
    """One explicitly versioned mapping for our pre-pin serverless adapter.

    That adapter's source fixed the endpoint, although old cache identities did
    not record it. Only the exact known adapter/version/normalizer and complete
    old key set qualifies. No general defaults are filled into unknown evidence.
    """
    if (
        isinstance(value, dict)
        and set(value) == _MODEL_KEYS - {"endpoint"}
        and value.get("provider") == "roboflow"
        and value.get("adapter") == "counterpart.providers.RoboflowDetector"
        and value.get("adapter_version") == "serverless-v0-v1"
        and value.get("normalizer_version") == "rf-xyxy-v1"
        and value.get("confidence_threshold") == 0.25
        and value.get("disable_active_learning") is True
    ):
        return {**value, "endpoint": "https://serverless.roboflow.com"}
    return value


def accepted_observation_digests(contract: dict) -> set[str]:
    configuration = contract["configuration"]
    digests = {contract["digest"]}
    old = {key: value for key, value in configuration.items() if key != "endpoint"}
    if normalize_legacy_configuration(old) == configuration:
        digests.add(hashlib.sha256(canonical(old).encode()).hexdigest())
    return digests


def assert_result_matches(result: dict, configuration: dict) -> None:
    """Check a detector result on both fresh dispatch and saved-cache paths."""
    model = result.get("model", {}) if isinstance(result, dict) else {}
    if not isinstance(model, dict) or any(
        model.get(key) != configuration[key] for key in ("provider", "model_id")
    ):
        raise ExecutionContractError("provider_model_mismatch", "Provider result names a different model")
    for key in ("normalizer_version", "confidence_threshold"):
        if key in model and model[key] != configuration[key]:
            raise ExecutionContractError("provider_model_mismatch", "Provider result semantics changed")


def assert_observation_matches(frame: dict, contract: dict, mode: str) -> None:
    """Shared worker/archive invariant; historical observations stay unchanged."""
    configuration = contract["configuration"]
    try:
        assert_result_matches(frame, configuration)
    except ExecutionContractError as error:
        raise ExecutionContractError(
            "execution_evidence_mismatch", "Saved observation model differs from the frozen contract"
        ) from error
    if mode == "live":
        provenance = frame.get("inference", {})
        if not isinstance(provenance, dict):
            raise ExecutionContractError(
                "execution_evidence_mismatch", "Saved observation has no valid inference provenance"
            )
        if provenance.get("configuration_digest") not in accepted_observation_digests(contract):
            raise ExecutionContractError(
                "execution_evidence_mismatch", "Saved observation has another or unknown model configuration"
            )
        if "configuration" in provenance and canonical(
            normalize_legacy_configuration(provenance["configuration"])
        ) != canonical(configuration):
            raise ExecutionContractError(
                "execution_evidence_mismatch", "Saved observation configuration does not match its digest"
            )
