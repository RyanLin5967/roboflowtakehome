"""Transport contract tests only: fixtures below are not real model predictions."""

import base64
import hashlib
import json
import logging
from copy import deepcopy
from xml.etree.ElementTree import fromstring

import httpx
import pytest

from counterpart.providers import ProviderError, RoboflowDetector, RoboflowExporter, voc_prediction

KEY = "provider-test-secret-not-a-real-key"
IMAGE = b"mock-image-bytes-image-validation-belongs-to-storage"
WIDTH, HEIGHT = 640, 480
RESPONSE = {
    "image": {"width": WIDTH, "height": HEIGHT},
    "predictions": [{"class": "cup", "confidence": 0.85, "x": 160, "y": 120, "width": 80, "height": 60}],
}


def detector(handler, **kwargs):
    return RoboflowDetector(KEY, client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def exporter(handler, **kwargs):
    return RoboflowExporter(
        KEY, "workspace", "project", client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs
    )


def test_detector_exact_header_only_protocol_and_geometry():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "serverless.roboflow.com"
        assert request.url.path == "/coco/39"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert request.headers["Content-Type"] == "application/json"
        assert KEY not in str(request.url)
        assert request.content == base64.b64encode(IMAGE)
        assert request.url.params["confidence"] == "0.25"
        assert request.url.params["disable_active_learning"] == "true"
        assert "api_key" not in request.url.params
        return httpx.Response(200, json=RESPONSE)

    with detector(handler) as adapter:
        result = adapter.detect(IMAGE, WIDTH, HEIGHT)
    assert len(requests) == 1
    assert result["detections"] == [
        {"label": "cup", "confidence": 0.85, "box": [0.1875, 0.1875, 0.3125, 0.3125]}
    ]
    assert result["model"]["model_id"] == "coco/39"
    assert result["model"]["provider"] == "roboflow"
    assert result["latency_ms"] >= 0


def test_empty_detections_are_valid_observation_not_absence_claim():
    with detector(lambda _: httpx.Response(200, json={**RESPONSE, "predictions": []})) as adapter:
        assert adapter.detect(IMAGE, WIDTH, HEIGHT)["detections"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", float("nan")),
        ("confidence", -1),
        ("confidence", 1.01),
        ("confidence", True),
        ("confidence", "0.8"),
        ("width", -1),
        ("width", 0),
        ("width", 900),
        ("x", -1),
        ("x", float("inf")),
        ("height", None),
        ("class", ""),
        ("class", "a\nb"),
        ("class", 5),
    ],
)
def test_detector_rejects_invalid_values_instead_of_silent_filtering(field, value):
    data = deepcopy(RESPONSE)
    data["predictions"][0][field] = value
    # json.dumps intentionally permits NaN/Infinity to exercise malicious responses.
    with detector(lambda _: httpx.Response(200, content=json.dumps(data))) as adapter:
        with pytest.raises(ProviderError, match="invalid_provider_response"):
            adapter.detect(IMAGE, WIDTH, HEIGHT)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {**RESPONSE, "image": None},
        {**RESPONSE, "predictions": None},
        {**RESPONSE, "predictions": [None]},
        {**RESPONSE, "predictions": [RESPONSE["predictions"][0]] * 1001},
        {**RESPONSE, "image": {"width": "640", "height": 480}},
    ],
)
def test_detector_rejects_malformed_responses(data):
    with detector(lambda _: httpx.Response(200, json=data)) as adapter:
        with pytest.raises(ProviderError):
            adapter.detect(IMAGE, WIDTH, HEIGHT)


def test_provider_cannot_change_image_dimensions():
    data = {**RESPONSE, "image": {"width": 480, "height": 640}}
    with detector(lambda _: httpx.Response(200, json=data)) as adapter:
        with pytest.raises(ProviderError, match="provider_dimension_mismatch"):
            adapter.detect(IMAGE, WIDTH, HEIGHT)


def test_provider_cannot_change_recorded_model():
    with detector(lambda _: httpx.Response(200, json={**RESPONSE, "model_id": "coco/38"})) as adapter:
        with pytest.raises(ProviderError, match="provider_model_mismatch"):
            adapter.detect(IMAGE, WIDTH, HEIGHT)


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "provider_auth_failed", False),
        (403, "provider_auth_failed", False),
        (402, "provider_budget_exhausted", False),
        (404, "provider_request_failed", False),
        (429, "provider_rate_limited", True),
        (500, "provider_request_failed", True),
        (302, "provider_redirect_rejected", False),
    ],
)
def test_http_failure_sanitized_and_never_retried(status, code, retryable):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"error": f"private ?api_key={KEY}"},
            headers={"Location": "https://untrusted.example"},
        )

    with detector(handler) as adapter:
        with pytest.raises(ProviderError) as caught:
            adapter.detect(IMAGE, WIDTH, HEIGHT)
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert KEY not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_transport_error_no_hidden_retry_or_secret_in_exception(error):
    requests = []

    def handler(request):
        requests.append(request)
        raise error(f"a URL with secret {KEY}", request=request)

    with detector(handler) as adapter:
        with pytest.raises(ProviderError) as caught:
            adapter.detect(IMAGE, WIDTH, HEIGHT)
    assert len(requests) == 1
    assert caught.value.retryable is True
    assert caught.value.ambiguous is True
    assert KEY not in str(caught.value)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("raw", [b"not json", b"null", b"[]", b"x" * (2 * 1024 * 1024 + 1)])
def test_invalid_json_is_sanitized(raw):
    with detector(lambda _: httpx.Response(200, content=raw)) as adapter:
        with pytest.raises(ProviderError, match="invalid_provider_response"):
            adapter.detect(IMAGE, WIDTH, HEIGHT)


@pytest.mark.parametrize(
    "model_id",
    [
        "https://evil.test/model/1",
        "coco/39?api_key=bad",
        "../39",
        "coco/39/2",
        "coco\\39",
        "coco/39\n",
        "rfdetr-small",
    ],
)
def test_only_canonical_identifiers_no_arbitrary_urls(model_id):
    with pytest.raises(ProviderError, match="invalid_model_identifier"):
        detector(lambda _: pytest.fail("No request permitted"), model_id=model_id)


@pytest.mark.parametrize(
    "image,width,height",
    [(b"", 640, 480), ("url", 640, 480), (IMAGE, True, 480), (IMAGE, 640, 0), (IMAGE, 9000, 9000)],
)
def test_input_bounds_before_dispatch(image, width, height):
    with detector(lambda _: pytest.fail("No request permitted")) as adapter:
        with pytest.raises(ProviderError):
            adapter.detect(image, width, height)


def test_export_documented_multipart_receipt_and_redacted_library_logs(caplog):
    requests = []
    caplog.set_level(logging.INFO, logger="httpx")

    def handler(request):
        requests.append(request)
        assert request.url.path == "/dataset/project/upload"
        assert request.url.params["api_key"] == KEY
        assert request.url.params["tag"] == "cp-asset-asset1"
        assert b'"counterpart_pair_id":"pair1"' in request.content
        assert b'"counterpart_session_id":"session1"' in request.content
        assert hashlib.sha256(IMAGE).hexdigest().encode() in request.content
        assert "multipart/form-data" in request.headers["Content-Type"]
        return httpx.Response(200, json={"success": True, "id": "rf-image-1"})

    with exporter(handler) as adapter:
        receipt = adapter.upload(
            IMAGE, asset_id="asset1", pair_id="pair1", session_id="session1", split="train"
        )
    assert len(requests) == 1
    assert receipt["image_id"] == "rf-image-1"
    assert receipt["status"] == "confirmed"
    assert receipt["metadata_confirmed"] is True
    assert KEY not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_duplicate_receipt_does_not_claim_new_metadata_was_applied():
    with exporter(lambda _: httpx.Response(200, json={"duplicate": True, "id": "existing"})) as adapter:
        receipt = adapter.upload(
            IMAGE, asset_id="asset1", pair_id="pair1", session_id="session1", split="test"
        )
    assert receipt["duplicate"] is True
    assert receipt["metadata_confirmed"] is False


@pytest.mark.parametrize(
    "reply",
    [
        {"success": True},
        {"duplicate": True},
        {"id": "image1"},
        {"success": "true", "id": "image1"},
        {"success": True, "id": "../evil"},
    ],
)
def test_missing_receipt_is_unknown_not_automatic_reupload(reply):
    with exporter(lambda _: httpx.Response(200, json=reply)) as adapter:
        with pytest.raises(ProviderError) as caught:
            adapter.upload(IMAGE, asset_id="asset1", pair_id="pair1", session_id="session1", split="train")
    assert caught.value.ambiguous is True
    assert caught.value.retryable is False


def test_timeout_during_upload_marks_uncertain_external_effect():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout(f"{KEY}", request=request)

    with exporter(handler) as adapter:
        with pytest.raises(ProviderError) as caught:
            adapter.upload(IMAGE, asset_id="asset1", pair_id="pair1", session_id="session1", split="train")
    assert len(calls) == 1
    assert caught.value.code == "export_outcome_unknown"
    assert caught.value.ambiguous is True
    assert caught.value.retryable is False
    assert KEY not in str(caught.value)


def test_reconcile_requires_one_exact_asset_hash():
    digest = hashlib.sha256(IMAGE).hexdigest()

    def handler(request):
        assert request.url.path == "/workspace/project/search"
        data = json.loads(request.content)
        assert data["tag"] == "cp-asset-asset1"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "rf-image-1",
                        "user_metadata": {
                            "counterpart_asset_id": "asset1",
                            "counterpart_media_sha256": digest,
                        },
                    }
                ]
            },
        )

    with exporter(handler) as adapter:
        receipt = adapter.reconcile(asset_id="asset1", media_sha256=digest)
    assert receipt["image_id"] == "rf-image-1"
    assert receipt["reconciled"] is True


@pytest.mark.parametrize("results", [[{}, {}], [{}], [{"id": "abc", "user_metadata": {}}], "bad", [{}] * 100])
def test_reconcile_fails_closed_on_ambiguous_matches(results):
    with exporter(lambda _: httpx.Response(200, json={"results": results})) as adapter:
        with pytest.raises(ProviderError, match="export_reconciliation_ambiguous"):
            adapter.reconcile(asset_id="asset1", media_sha256=hashlib.sha256(IMAGE).hexdigest())


def test_reconcile_zero_is_only_no_observed_match():
    with exporter(lambda _: httpx.Response(200, json={"results": []})) as adapter:
        assert adapter.reconcile(asset_id="asset1", media_sha256=hashlib.sha256(IMAGE).hexdigest()) is None


def test_voc_predictions_preserve_extent_and_xml_escape_labels():
    xml = voc_prediction(
        [{"label": "cup<&", "confidence": 0.8, "box": [0, 0.25, 0.5, 0.75]}], 640, 480, filename="test.jpg"
    )
    root = fromstring(xml)
    assert root.findtext("object/name") == "cup<&"
    assert root.findtext("object/bndbox/xmin") == "1"
    assert root.findtext("object/bndbox/xmax") == "321"
    assert root.findtext("object/bndbox/ymin") == "121"
    assert root.findtext("object/bndbox/ymax") == "361"


def test_annotation_is_prediction_and_never_promotes_ground_truth():
    def handler(request):
        assert request.url.path == "/dataset/project/annotate/rf-image-1"
        assert request.url.params["prediction"] == "true"
        assert request.url.params["overwrite"] == "false"
        assert request.url.params["addToDataset"] == "false"
        assert fromstring(json.loads(request.content)["annotationFile"]).findtext("object/name") == "cup"
        return httpx.Response(200, json={"success": True})

    with exporter(handler) as adapter:
        result = adapter.annotate(
            "rf-image-1",
            detections=[{"label": "cup", "confidence": 0.8, "box": [0, 0.25, 0.5, 0.75]}],
            width=640,
            height=480,
        )
    assert result["is_prediction"] is True
    assert result["annotation_format"] == "voc-one-based-v1"


def test_existing_annotation_conflict_not_claimed_as_confirmed():
    with exporter(lambda _: httpx.Response(409, json={"error": "already annotated"})) as adapter:
        with pytest.raises(ProviderError) as caught:
            adapter.annotate("rf-image-1", detections=[], width=640, height=480)
    assert caught.value.ambiguous is True
    assert caught.value.retryable is False


@pytest.mark.parametrize("status", [201, 202, 204])
def test_unexpected_success_status_during_upload_is_ambiguous(status):
    with exporter(lambda _: httpx.Response(status)) as adapter:
        with pytest.raises(ProviderError) as caught:
            adapter.upload(IMAGE, asset_id="asset1", pair_id="pair1", session_id="session1", split="train")
    assert caught.value.code == "export_outcome_unknown"
    assert caught.value.ambiguous is True
    assert caught.value.retryable is False


def test_oversized_response_stops_reading_and_closes_stream():
    class UnboundedStream(httpx.SyncByteStream):
        chunks = 0
        closed = False

        def __iter__(self):
            while self.chunks < 10000:
                self.chunks += 1
                yield b"x" * 16384

        def close(self):
            self.closed = True

    stream = UnboundedStream()
    with detector(lambda _: httpx.Response(200, stream=stream)) as adapter:
        with pytest.raises(ProviderError, match="invalid_provider_response"):
            adapter.detect(IMAGE, WIDTH, HEIGHT)
    assert stream.chunks <= 129
    assert stream.closed is True


@pytest.mark.parametrize("changed_field", ["pair", "session", "split"])
def test_reconciliation_rejects_duplicate_from_other_pair_session_or_split(changed_field):
    digest = hashlib.sha256(IMAGE).hexdigest()
    item = {
        "id": "rf-image-1",
        "split": "train",
        "user_metadata": {
            "counterpart_asset_id": "asset1",
            "counterpart_media_sha256": digest,
            "counterpart_pair_id": "pair1",
            "counterpart_session_id": "session1",
        },
    }
    if changed_field == "split":
        item["split"] = "test"
    else:
        item["user_metadata"][f"counterpart_{changed_field}_id"] = "other"
    with exporter(lambda _: httpx.Response(200, json={"results": [item]})) as adapter:
        with pytest.raises(ProviderError, match="export_reconciliation_ambiguous"):
            adapter.reconcile(
                asset_id="asset1",
                media_sha256=digest,
                expected_pair_id="pair1",
                expected_session_id="session1",
                expected_split="train",
            )


def test_reconciliation_returns_verified_context():
    digest = hashlib.sha256(IMAGE).hexdigest()
    item = {
        "id": "rf-image-1",
        "split": "train",
        "user_metadata": {
            "counterpart_asset_id": "asset1",
            "counterpart_media_sha256": digest,
            "counterpart_pair_id": "pair1",
            "counterpart_session_id": "session1",
        },
    }
    with exporter(lambda _: httpx.Response(200, json={"results": [item]})) as adapter:
        receipt = adapter.reconcile(
            asset_id="asset1",
            media_sha256=digest,
            expected_pair_id="pair1",
            expected_session_id="session1",
            expected_split="train",
        )
    assert receipt["pair_id"] == "pair1"
    assert receipt["session_id"] == "session1"
    assert receipt["split"] == "train"
