#!/usr/bin/env python3
"""Inspect a portable release without extracting files or loading any model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from counterpart.consumer import ArchiveValidationError, PortableRelease


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--allow-replay", action="store_true", help="Explicitly accept illustrative fixture data"
    )
    parser.add_argument("--show", type=int, default=2, help="Print up to this many example questions (0–20)")
    args = parser.parse_args()
    if not 0 <= args.show <= 20:
        parser.error("--show must be between 0 and 20")
    try:
        with PortableRelease(args.archive, allow_replay=args.allow_replay) as release:
            print(json.dumps(release.summary, indent=2))
            for index, row in enumerate(release.iter_examples()):
                if index >= args.show:
                    break
                print(f"\n{row['member']} · {row['split']} · {len(row['media'])} frame(s)")
                print(f"Q: {row['question']}\nA: {row['answer']}")
            print("\nIntegrity validated. This is not a signature or proof that observations are correct.")
            if release.summary["human_reviewed"]:
                print(
                    "Human review accepted the recorded machine evidence; it is not independent ground truth."
                )
            if release.evidence_kind == "fixture_replay":
                print("ILLUSTRATIVE REPLAY: generated test data, not actual model predictions.")
        return 0
    except ArchiveValidationError as error:
        # Validator messages are fixed strings, never untrusted archive text.
        print(f"Archive rejected: {error} No files extracted.", file=sys.stderr)
        return 2
    except OSError:
        print("Archive could not be read. No files extracted.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
