"""Stable server-assigned scene grouping for local and remote dataset splits.

Names are display metadata. Only an opaque group ID chooses a partition, so two
operators using the same human-readable name do not silently merge scenes.
Historical pairs used their session ID, which remains a supported policy.
"""

import hashlib
from uuid import UUID


def split_for_group(group_id: str) -> str:
    bucket = int(hashlib.sha256(group_id.encode()).hexdigest(), 16) % 100
    return "train" if bucket < 80 else "valid" if bucket < 90 else "test"


def split_for_session(session_id: str) -> str:
    """Compatibility alias retaining the original session-only partition hash."""
    return split_for_group(session_id)


def session_split_group(session_id: str) -> dict:
    return validate_split_group(
        {
            "schema_version": "counterpart.split-group.v1",
            "id": session_id,
            "kind": "session",
            "name": None,
        },
        session_id,
    )


def manifest_split(session: dict) -> dict:
    """Project a DB session's frozen grouping into an immutable pair manifest."""
    session_id = str(session["id"])
    snapshot = session.get("split_group_snapshot")
    if snapshot is None:
        return {"split_group": session_id, "split": split_for_group(session_id)}
    frozen = validate_split_group(snapshot, session_id)
    return {"split_group": frozen["id"], "split": split_for_group(frozen["id"]), "split_provenance": frozen}


def validate_split_group(snapshot: dict, session_id: str) -> dict:
    """Validate frozen provenance without consulting mutable operator metadata."""
    if not isinstance(snapshot, dict) or set(snapshot) != {"schema_version", "id", "kind", "name"}:
        raise ValueError("Split group provenance has an unsupported shape")
    try:
        identifier = str(UUID(snapshot["id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Split group must have a canonical opaque UUID") from error
    if identifier != snapshot["id"] or snapshot["schema_version"] != "counterpart.split-group.v1":
        raise ValueError("Split group identity or version is unsupported")
    if snapshot["kind"] == "session":
        if identifier != session_id or snapshot["name"] is not None:
            raise ValueError("Independent split group must equal its session ID")
    elif snapshot["kind"] == "operator_group":
        name = snapshot["name"]
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 120:
            raise ValueError("Operator split group requires its frozen display name")
    else:
        raise ValueError("Split group kind is unsupported")
    return dict(snapshot)
