"""Cloud probe orchestration regressions with fake CUDA/model; no local ML."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "counterpart_modal_probe_test", ROOT / "scripts/modal_perception_probe.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_late_visual_and_timing_failures_preserve_all_observed_photos(tmp_path, monkeypatch):
    plan = copy.deepcopy(json.loads((ROOT / "research/modal_perception_probe.plan.json").read_text()))
    plan["source_hashes"] = {}
    plan["source"]["prior_six_hashes"] = {}
    plan["_frozen_plan_sha256"] = "frozen-plan-test"
    plan["_runner_sha256"] = "runner-test"
    original_plan = copy.deepcopy(plan)
    raw = io.BytesIO()
    Image.new("RGB", (256, 256), "gray").save(raw, format="JPEG")
    payload = raw.getvalue()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as handle:
        for row in plan["selected"]:
            info = tarfile.TarInfo(row["source_member"])
            info.size = len(payload)
            handle.addfile(info, io.BytesIO(payload))
    archive_bytes = archive.getvalue()
    weights = tmp_path / "fake-weights.pth"
    weights.write_bytes(b"fake checkpoint metadata only; no ML")

    class FakeModel:
        def __init__(self, **kwargs):
            self.model_config = SimpleNamespace(pretrain_weights=weights)
            self.calls = 0

        def predict(self, image, **kwargs):
            self.calls += 1
            if self.calls > 58:
                raise RuntimeError("Injected timing failure after all original photos")
            return SimpleNamespace(
                xyxy=[[32, 32, 96, 96], [128, 32, 192, 96]],
                confidence=[0.9, 0.9],
                data={"class_name": ["book", "cup"]},
            )

    fake_torch = ModuleType("torch")
    fake_torch.cuda = SimpleNamespace(
        is_available=lambda: True, get_device_name=lambda: "FAKE GPU", synchronize=lambda: None
    )
    fake_torch.set_num_threads = lambda value: None
    fake_torch.manual_seed = lambda value: None
    fake_rfdetr = ModuleType("rfdetr")
    fake_rfdetr.RFDETRSmall = FakeModel
    fake_recipes = ModuleType("counterpart.recipes")
    fake_recipes.build_recipe_snapshot = lambda *args, **kwargs: {}
    fake_verification = ModuleType("counterpart.verification")
    fake_verification.verify = lambda *args, **kwargs: {"evidence": {"relation": "left"}}
    fake_visual = ModuleType("counterpart.visual_checks")

    def fail_visual(*args, **kwargs):
        raise ValueError("Injected corrupt visual evidence")

    fake_visual.evaluate_visual_controls = fail_visual
    for name, value in {
        "torch": fake_torch,
        "rfdetr": fake_rfdetr,
        "counterpart.recipes": fake_recipes,
        "counterpart.verification": fake_verification,
        "counterpart.visual_checks": fake_visual,
    }.items():
        monkeypatch.setitem(sys.modules, name, value)
    with (
        patch("urllib.request.urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(archive_bytes)),
        patch("importlib.metadata.version", return_value="fake-test-package"),
    ):
        result = probe.cloud_probe(plan)
    assert result["status"] == "complete_with_failures"
    assert result["counts"]["observed"] == 58
    assert result["counts"]["total_model_calls"] == 184
    assert len(result["pairs"]) == 29
    assert all(pair["diagnostic_failure"] == "ValueError" for pair in result["pairs"])
    assert len(result["failures"]) == 29 + 6 + 120
    assert result["warm_timing"]["count"] == 0
    assert result["warm_timing"]["p95_ms"] is None
    assert result["plan"] == {key: value for key, value in original_plan.items() if not key.startswith("_")}
    assert result["frozen_plan_sha256"] == "frozen-plan-test"
    assert result["runner_sha256"] == "runner-test"
