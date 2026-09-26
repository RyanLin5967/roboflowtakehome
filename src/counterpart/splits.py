"""Stable session grouping, shared by local manifests and remote dataset exports."""

import hashlib


def split_for_session(session_id: str) -> str:
    bucket = int(hashlib.sha256(session_id.encode()).hexdigest(), 16) % 100
    return "train" if bucket < 80 else "valid" if bucket < 90 else "test"
