"""Bounded, synchronous Roboflow adapters; the worker owns durable reservations.

Each method makes exactly one HTTP request. No adapter retries, remote URL inputs,
project creation, local models, or implicit annotation promotion are permitted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import re
import time
from typing import Any
from urllib.parse import quote, quote_plus
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx

_INFERENCE_URL = "https://serverless.roboflow.com"
_DATASET_URL = "https://api.roboflow.com"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}/[0-9]{1,8}$")
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderError(RuntimeError):
    """Safe to expose in logs; never retains upstream response or request URLs."""

    def __init__(
        self,
        code: str,
        retryable: bool = False,
        *,
        ambiguous: bool = False,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.status_code = status_code
        super().__init__(code)


class _CredentialFilter(logging.Filter):
    """Dataset API still requires query auth; sanitize HTTP library log records."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._variants = {secret, quote(secret, safe=""), quote_plus(secret)}

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for value in self._variants:
            message = message.replace(value, "[REDACTED]")
        message = re.sub(r"(?i)(api_key=)[^&\s\"'<>]+", r"\1[REDACTED]", message)
        record.msg, record.args = message, ()
        return True


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ProviderError("invalid_provider_identifier")
    return value


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProviderError("invalid_provider_response")
    return float(value)


def _dimensions(width: int, height: int) -> None:
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or not 1 <= width <= 16_400
        or not 1 <= height <= 16_400
        or width * height > 80_000_000
    ):
        raise ProviderError("invalid_image_dimensions")


def _image_input(image_bytes: bytes) -> None:
    if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= _MAX_IMAGE_BYTES:
        raise ProviderError("invalid_image_bytes")


def _normalized_detection(detection: Any) -> dict:
    if not isinstance(detection, dict):
        raise ProviderError("invalid_provider_response")
    label = detection.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 100 or any(ord(c) < 32 for c in label):
        raise ProviderError("invalid_provider_response")
    confidence = _finite(detection.get("confidence"))
    box = detection.get("box")
    if not isinstance(box, (tuple, list)) or len(box) != 4:
        raise ProviderError("invalid_provider_response")
    x1, y1, x2, y2 = [_finite(x) for x in box]
    if not (0 <= confidence <= 1 and 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ProviderError("invalid_provider_response")
    return {"label": label.strip(), "confidence": confidence, "box": [x1, y1, x2, y2]}


class _HTTPAdapter:
    def __init__(self, api_key: str, timeout: float, client: httpx.Client | None) -> None:
        if not isinstance(api_key, str) or not api_key or "\n" in api_key or "\r" in api_key:
            raise ProviderError("invalid_provider_configuration")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ProviderError("invalid_provider_configuration")
        self._api_key = api_key
        self._timeout = timeout
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.Client(trust_env=False, headers={"Accept-Encoding": "identity"})
        )
        self._log_filter = _CredentialFilter(api_key)
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).addFilter(self._log_filter)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).removeFilter(self._log_filter)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _request(self, url: str, *, mutating: bool = False, http_method: str = "POST", **kwargs) -> dict:
        # No redirects, eager unbounded response buffer, or automatic retries.
        try:
            with self._client.stream(
                http_method, url, timeout=self._timeout, follow_redirects=False, **kwargs
            ) as response:
                status = response.status_code
                if status != 200:
                    ambiguous = mutating and (200 <= status < 300 or status >= 500 or status in (408, 409))
                    if ambiguous:
                        code = "export_outcome_unknown"
                    elif status in (401, 403):
                        code = "provider_auth_failed"
                    elif status == 402:
                        code = "provider_budget_exhausted"
                    elif status == 429:
                        code = "provider_rate_limited"
                    elif 300 <= status < 400:
                        code = "provider_redirect_rejected"
                    else:
                        code = "provider_request_failed"
                    raise ProviderError(
                        code,
                        retryable=not mutating and (status in (408, 429) or status >= 500),
                        ambiguous=ambiguous,
                        status_code=status,
                    )
                body = bytearray()
                # Force an uncompressed response in our clients; still bound decoded
                # byte count for an upstream that ignores Accept-Encoding: identity.
                for chunk in response.iter_bytes(chunk_size=16_384):
                    if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ProviderError("invalid_provider_response", ambiguous=mutating)
                    body.extend(chunk)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            raise ProviderError(
                "export_outcome_unknown" if mutating else "provider_unavailable",
                retryable=not mutating,
                ambiguous=True,
            ) from None
        except httpx.HTTPError:
            raise ProviderError("provider_transport_error", ambiguous=mutating) from None
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ProviderError("invalid_provider_response", ambiguous=mutating) from None
        if not isinstance(payload, dict):
            raise ProviderError("invalid_provider_response", ambiguous=mutating)
        return payload


class RoboflowDetector(_HTTPAdapter):
    def __init__(
        self,
        api_key: str,
        model_id: str = "coco/39",
        timeout: float = 20,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
            raise ProviderError("invalid_model_identifier")
        super().__init__(api_key, timeout, client)
        self.model_id = model_id

    def detect(self, image_bytes: bytes, width: int, height: int) -> dict:
        _image_input(image_bytes)
        _dimensions(width, height)
        started = time.perf_counter()
        payload = self._request(
            f"{_INFERENCE_URL}/{self.model_id}",
            # Exact serverless V0 wire shape from Inference SDK 1.7.2.
            # The self-hosted /infer/object_detection route returned hosted 404.
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            params={"confidence": 0.25, "disable_active_learning": "true"},
            content=base64.b64encode(image_bytes),
        )
        image = payload.get("image")
        if (
            not isinstance(image, dict)
            or _finite(image.get("width")) != width
            or _finite(image.get("height")) != height
        ):
            raise ProviderError("provider_dimension_mismatch")
        if payload.get("model_id", self.model_id) != self.model_id:
            raise ProviderError("provider_model_mismatch")
        predictions = payload.get("predictions")
        if not isinstance(predictions, list) or len(predictions) > 1000:
            raise ProviderError("invalid_provider_response")
        normalized = []
        for item in predictions:
            if not isinstance(item, dict):
                raise ProviderError("invalid_provider_response")
            x, y, w, h = [_finite(item.get(k)) for k in ("x", "y", "width", "height")]
            normalized.append(
                _normalized_detection(
                    {
                        "label": item.get("class"),
                        "confidence": item.get("confidence"),
                        "box": [
                            (x - w / 2) / width,
                            (y - h / 2) / height,
                            (x + w / 2) / width,
                            (y + h / 2) / height,
                        ],
                    }
                )
            )
        return {
            "detections": normalized,
            "model": {
                "provider": "roboflow",
                "model_id": self.model_id,
                "normalizer_version": "rf-xyxy-v1",
                "confidence_threshold": 0.25,
            },
            "latency_ms": (time.perf_counter() - started) * 1000,
        }


class RoboflowExporter(_HTTPAdapter):
    """One external operation per call; persist receipts in the caller's outbox.

    Dataset endpoints have documented query-key authentication, unlike inference.
    HTTPX request logs are redacted and exceptions never contain URLs or response bodies.
    This adapter never creates a project or claims atomic/exactly-once publication.
    """

    def __init__(
        self,
        api_key: str,
        workspace: str,
        project: str,
        timeout: float = 30,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.workspace, self.project = _safe_id(workspace), _safe_id(project)
        super().__init__(api_key, timeout, client)

    @staticmethod
    def asset_identity(image_bytes: bytes) -> str:
        _image_input(image_bytes)
        return hashlib.sha256(image_bytes).hexdigest()

    def upload(
        self,
        image_bytes: bytes,
        *,
        asset_id: str,
        pair_id: str,
        session_id: str,
        split: str,
        media_type: str = "image/jpeg",
    ) -> dict:
        asset_id, pair_id, session_id = map(_safe_id, (asset_id, pair_id, session_id))
        digest = self.asset_identity(image_bytes)
        if split not in ("train", "valid", "test") or media_type not in ("image/jpeg", "image/png"):
            raise ProviderError("invalid_export_parameters")
        suffix = "jpg" if media_type == "image/jpeg" else "png"
        name = f"counterpart-{digest}.{suffix}"
        payload = self._request(
            f"{_DATASET_URL}/dataset/{self.project}/upload",
            mutating=True,
            params={"api_key": self._api_key, "batch": "counterpart-v1", "tag": f"cp-asset-{asset_id}"},
            data={
                "name": name,
                "split": split,
                "metadata": json.dumps(
                    {
                        "counterpart_asset_id": asset_id,
                        "counterpart_pair_id": pair_id,
                        "counterpart_session_id": session_id,
                        "counterpart_media_sha256": digest,
                    },
                    separators=(",", ":"),
                ),
            },
            files={"file": (name, image_bytes, media_type)},
        )
        image_id = payload.get("id")
        if (
            not (payload.get("success") is True or payload.get("duplicate") is True)
            or not isinstance(image_id, str)
            or not _SAFE_ID.fullmatch(image_id)
        ):
            raise ProviderError("export_outcome_unknown", ambiguous=True)
        duplicate = payload.get("duplicate") is True
        return {
            "provider": "roboflow",
            "image_id": image_id,
            "asset_id": asset_id,
            "media_sha256": digest,
            "status": "confirmed",
            "duplicate": duplicate,
            "metadata_confirmed": not duplicate,
            "pair_id": pair_id,
            "session_id": session_id,
            "split": split,
        }

    def reconcile(
        self,
        *,
        asset_id: str,
        media_sha256: str,
        expected_pair_id: str | None = None,
        expected_session_id: str | None = None,
        expected_split: str | None = None,
    ) -> dict | None:
        """None is no observed match, NOT permission to repeat an uncertain write."""
        asset_id = _safe_id(asset_id)
        if expected_pair_id is not None:
            _safe_id(expected_pair_id)
        if expected_session_id is not None:
            _safe_id(expected_session_id)
        if expected_split is not None and expected_split not in ("train", "valid", "test"):
            raise ProviderError("invalid_export_parameters")
        if not isinstance(media_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", media_sha256):
            raise ProviderError("invalid_export_parameters")
        payload = self._request(
            f"{_DATASET_URL}/{self.workspace}/{self.project}/search",
            params={"api_key": self._api_key},
            json={
                "tag": f"cp-asset-{asset_id}",
                "offset": 0,
                "limit": 100,
                "fields": ["id", "name", "tags", "user_metadata", "split"],
            },
        )
        results = payload.get("results")
        if not isinstance(results, list) or len(results) >= 100:
            raise ProviderError("export_reconciliation_ambiguous", ambiguous=True)
        if not results:
            return None
        if len(results) != 1 or not isinstance(results[0], dict):
            raise ProviderError("export_reconciliation_ambiguous", ambiguous=True)
        item = results[0]
        metadata = item.get("user_metadata")
        if (
            not isinstance(metadata, dict)
            or metadata.get("counterpart_asset_id") != asset_id
            or metadata.get("counterpart_media_sha256") != media_sha256
            or (expected_pair_id is not None and metadata.get("counterpart_pair_id") != expected_pair_id)
            or (
                expected_session_id is not None
                and metadata.get("counterpart_session_id") != expected_session_id
            )
            or (expected_split is not None and item.get("split") != expected_split)
            or not isinstance(item.get("id"), str)
            or not _SAFE_ID.fullmatch(item["id"])
        ):
            raise ProviderError("export_reconciliation_ambiguous", ambiguous=True)
        return {
            "provider": "roboflow",
            "image_id": item["id"],
            "asset_id": asset_id,
            "media_sha256": media_sha256,
            "status": "confirmed",
            "reconciled": True,
            "metadata_confirmed": True,
            "pair_id": metadata.get("counterpart_pair_id"),
            "session_id": metadata.get("counterpart_session_id"),
            "split": item.get("split"),
        }

    def annotate(self, image_id: str, *, detections: list[dict], width: int, height: int) -> dict:
        """Store machine boxes as VOC predictions, without adding them as ground truth."""
        image_id = _safe_id(image_id)
        annotation = voc_prediction(detections, width, height, filename=f"{image_id}.jpg")
        payload = self._request(
            f"{_DATASET_URL}/dataset/{self.project}/annotate/{image_id}",
            mutating=True,
            params={
                "api_key": self._api_key,
                "name": "annotation.xml",
                "jobName": "counterpart-v1",
                "prediction": "true",
                "overwrite": "false",
                "addToDataset": "false",
            },
            json={"annotationFile": annotation, "labelmap": None},
        )
        if payload.get("success") is not True:
            raise ProviderError("export_outcome_unknown", ambiguous=True)
        return {
            "provider": "roboflow",
            "image_id": image_id,
            "annotation_status": "confirmed",
            "annotation_format": "voc-one-based-v1",
            "is_prediction": True,
            "annotation_sha256": hashlib.sha256(annotation.encode()).hexdigest(),
        }


def voc_prediction(detections: list[dict], width: int, height: int, *, filename: str) -> str:
    """Roboflow VOC parser expects one-based origins; preserve continuous box extent."""
    _dimensions(width, height)
    if not isinstance(detections, list) or len(detections) > 1000:
        raise ProviderError("invalid_provider_response")
    root = Element("annotation")
    SubElement(root, "filename").text = filename
    size = SubElement(root, "size")
    for key, value in (("width", width), ("height", height), ("depth", 3)):
        SubElement(size, key).text = str(value)
    for detection in detections:
        d = _normalized_detection(detection)
        obj = SubElement(root, "object")
        SubElement(obj, "name").text = d["label"]
        bounds = SubElement(obj, "bndbox")
        x1, y1, x2, y2 = d["box"]
        for key, value in (
            ("xmin", x1 * width + 1),
            ("ymin", y1 * height + 1),
            ("xmax", x2 * width + 1),
            ("ymax", y2 * height + 1),
        ):
            SubElement(bounds, key).text = format(value, ".10g")
    return tostring(root, encoding="unicode")
