"""Create explicit fixture-only records for the real product browser rehearsal.

This is a trusted test-data writer, not a public prediction-input endpoint. It
never starts a detector. The running worker processes visibly labeled generated
media with a deliberately uncertain fixture observation. Session capabilities
and provider credentials are neither printed nor written to the output.
"""

import json
import time
from copy import deepcopy
from uuid import uuid4

from counterpart.collections import CollectionRepository
from counterpart.config import settings
from counterpart.fixtures import generate
from counterpart.lifecycle import media_reference_guard
from counterpart.object_storage import create_media_store
from counterpart.repository import Repository


def run() -> dict:
    config = settings()
    repo = Repository(config.database_url)
    media = create_media_store(config)
    collections = CollectionRepository(repo)
    label = f"Gap rehearsal {uuid4().hex[:8]}"
    group = collections.create_split_group(label + " scene", "Generated fixture only; no physical trial")
    campaign = collections.create_campaign(label, "spatial", mode="replay", target_pairs=3)
    invite = collections.create_invite(campaign["id"], max_sessions=3, split_group_id=group["id"])

    def capture(session, slot, scenario, *, uncertain=False):
        key = uuid4().hex
        with media_reference_guard(config.database_url):
            frames = []
            for frame in generate("spatial", scenario):
                detections = deepcopy(frame["detections"])
                if uncertain:
                    # The original image still depicts the valid controlled
                    # change. Only its explicitly synthetic observation is low
                    # confidence, so a scripted human-origin decision is honest
                    # as a workflow fixture, never real quality evidence.
                    detections[0]["confidence"] = 0.50
                frames.append(
                    {**media.put(frame["bytes"]), "t_ms": frame["t_ms"], "fixture_detections": detections}
                )
            submitted = repo.submit(
                session["id"],
                {
                    "slot": slot,
                    "expected_revision": session["revision"],
                    "epoch": session["epoch"],
                    "client_seq": 0,
                    "idempotency_key": key,
                },
                frames,
                key,
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = repo.snapshot(session["id"])
            latest = current["latest_capture"]
            if latest["id"] == submitted["capture"]["id"] and latest["status"] in {
                "accepted",
                "rejected",
                "failed",
            }:
                return current
            time.sleep(0.1)
        raise RuntimeError("Fixture verification timed out; start the current worker.")

    records = []
    try:
        for kind in ("uncertain", "accepted", "known_failure"):
            saved = collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]
            assert saved["mode"] == "replay"
            saved = capture(saved, "baseline", "baseline")
            assert saved["baseline"] and saved["latest_capture"]["status"] == "accepted"
            saved = capture(
                saved,
                "counterpart",
                "wrong_relation" if kind == "known_failure" else "counterpart",
                uncertain=kind == "uncertain",
            )
            expected = {
                "uncertain": "low_confidence",
                "accepted": "accepted",
                "known_failure": "wrong_relation",
            }
            assert saved["latest_capture"]["result"]["code"] == expected[kind]
            pair_id = None
            if kind == "accepted":
                pair_id = repo.publish(saved["id"], saved["revision"])["id"]
            records.append(
                {
                    "kind": kind,
                    "session_id": saved["id"],
                    "baseline_id": saved["baseline"]["id"],
                    "counterpart_id": saved["latest_capture"]["id"],
                    "pair_id": pair_id,
                }
            )
        return {
            "campaign_id": campaign["id"],
            "name": label,
            "group_id": group["id"],
            "sessions": records,
            "evidence_kind": "fixture_replay",
            "model_calls": 0,
        }
    finally:
        if hasattr(media, "close"):
            media.close()


if __name__ == "__main__":
    print(json.dumps(run()))
