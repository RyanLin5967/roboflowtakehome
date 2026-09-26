"""Untrusted HTTP input. Never accept detections or model decisions from a client."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSession(StrictModel):
    recipe: Literal["spatial", "temporal"] = "spatial"
    mode: Literal["live", "replay"] = "replay"
    object_a: Literal["bottle"] = "bottle"
    object_b: Literal["cup"] = "cup"


class Resume(StrictModel):
    expected_epoch: int = Field(ge=0)


class Publish(StrictModel):
    expected_revision: int = Field(ge=0)


class CaptureMetadata(StrictModel):
    slot: Literal["baseline", "counterpart", "recovery", "preview"]
    expected_revision: int = Field(ge=0)
    epoch: int = Field(ge=0)
    client_seq: int = Field(default=0, ge=0, le=2**53 - 1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    timestamps_ms: list[float] = Field(min_length=1, max_length=48)
    interrupted: bool = False

    @model_validator(mode="after")
    def time_bounds(self):
        ts = self.timestamps_ms
        if any(not math.isfinite(t) or t < 0 or t > 12000 for t in ts):
            raise ValueError("Frame times must be finite and within a 12-second recording.")
        if any(b <= a for a, b in zip(ts, ts[1:], strict=False)):
            raise ValueError("Frame times must be strictly increasing.")
        return self


class Replay(StrictModel):
    scenario: Literal["baseline", "counterpart", "wrong_relation", "camera_moved", "ambiguous", "recovery"]
    expected_revision: int = Field(ge=0)
    epoch: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
