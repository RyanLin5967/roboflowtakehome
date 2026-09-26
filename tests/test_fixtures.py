from io import BytesIO

import pytest
from PIL import Image

from counterpart.fixtures import FIXTURE_VERSION, generate
from counterpart.verification import verify


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
@pytest.mark.parametrize(
    "scenario", ["baseline", "counterpart", "wrong_relation", "camera_moved", "ambiguous", "recovery"]
)
def test_every_fixture_is_decodable_and_visibly_provenanced(recipe, scenario):
    observations = generate(recipe, scenario)
    assert observations
    for frame in observations:
        with Image.open(BytesIO(frame["bytes"])) as image:
            assert image.format == "JPEG"
            assert image.size == (frame["width"], frame["height"])
            image.verify()
        assert frame["provenance"]["kind"] == "illustrative_fixture"
        assert frame["model"] == {"provider": "fixture", "model_id": FIXTURE_VERSION}
        assert "not model predictions" in frame["provenance"]["notice"]


def test_generated_fixtures_are_deterministic_and_independent():
    first, second = generate("spatial", "baseline"), generate("spatial", "baseline")
    assert first == second
    first[0]["detections"][0]["confidence"] = 0
    assert second[0]["detections"][0]["confidence"] == 0.97


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
def test_fixtures_exercise_both_pair_members_and_recovery(recipe):
    first = generate(recipe, "baseline")
    result = verify(recipe, "baseline", first)
    assert result["accepted"], result
    reference = {**result, "frames": first}
    counterpart = verify(recipe, "counterpart", generate(recipe, "counterpart"), reference)
    assert counterpart["accepted"], counterpart
    assert verify(recipe, "recovery", generate(recipe, "recovery"), reference)["accepted"]
    for scenario in ("wrong_relation", "camera_moved", "ambiguous"):
        rejected = verify(recipe, "counterpart", generate(recipe, scenario), reference)
        assert not rejected["accepted"], (scenario, rejected)


@pytest.mark.parametrize("recipe,scenario", [("unknown", "baseline"), ("spatial", "unrecognized")])
def test_fixture_request_validation(recipe, scenario):
    with pytest.raises(ValueError, match="Unknown"):
        generate(recipe, scenario)
