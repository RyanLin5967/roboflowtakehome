"""Portable machine-verified VQA rows derived from accepted evidence, not requested actions."""

import json

from .evidence_revisions import HUMAN_BASIS, validate_adjudication
from .splits import split_for_session


def examples(manifest: dict) -> list[dict]:
    human = manifest.get("schema_version") == "counterpart.pair.v2"
    if human:
        validate_adjudication(manifest, replay_decisions=False)
    recipe = manifest["recipe"]
    a, b = manifest["objects"]["a"], manifest["objects"]["b"]
    rows = []
    for member in ("baseline", "counterpart"):
        capture = manifest[member]
        result = capture["result"]
        if not human and (capture["status"] != "accepted" or result["accepted"] is not True):
            raise ValueError("Only accepted observations can produce dataset examples.")
        evidence = result["evidence"]
        if recipe == "spatial":
            question = f"Is the {a} to the left or right of the {b} in this image?"
            answer = (
                manifest["evidence_adjudication"]["labels"][member] if human else evidence.get("relation")
            )
            if answer not in {"left", "right"}:
                raise ValueError("The evidence does not establish the spatial answer.")
        elif recipe == "temporal":
            question = (
                f"Which object enters the right-hand target band first: the {a} or the {b}? "
                "The band starts at 62% of the image width."
            )
            order = (
                [
                    manifest["evidence_adjudication"]["labels"][member],
                    b if manifest["evidence_adjudication"]["labels"][member] == a else a,
                ]
                if human
                else evidence.get("arrival_order")
            )
            if not isinstance(order, list) or len(order) != 2 or set(order) != {a, b}:
                raise ValueError("The evidence does not establish the arrival order.")
            answer = order[0]
        else:
            raise ValueError("Unsupported dataset recipe.")
        rows.append(
            {
                "schema_version": "counterpart.vqa.v1",
                "id": capture["id"],
                "pair_id": manifest["pair_id"],
                "member": member,
                "split_group": manifest["split_group"],
                "split": manifest.get("split", split_for_session(manifest["split_group"])),
                "question": question,
                "answer": answer,
                "media": [
                    {
                        "path": f"assets/{frame['asset_id']}",
                        "sha256": frame["asset_id"],
                        "timestamp_ms": frame["t_ms"],
                        "width": frame["width"],
                        "height": frame["height"],
                    }
                    for frame in capture["frames"]
                ],
                "evidence_source": manifest["provenance"]["evidence_kind"],
                "human_reviewed": human,
                "verifier_version": result["verifier_version"],
                "label_basis": HUMAN_BASIS
                if human
                else "Machine verified from detections and recipe checks; not human ground truth.",
                **(
                    {"adjudication": manifest["evidence_adjudication"], "label_origin": "human_adjudication"}
                    if human
                    else {}
                ),
            }
        )
    if rows[0]["answer"] == rows[1]["answer"]:
        raise ValueError("A controlled pair must have distinct established answers.")
    return rows


def jsonl(manifest: dict) -> str:
    return "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in examples(manifest))
