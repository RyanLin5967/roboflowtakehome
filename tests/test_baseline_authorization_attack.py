"""Authority-copy database fences, separately from visual binding checks.

These deliberately seed trusted authorization rows to isolate the database
boundary. They do not substitute for the authenticated operator workflow tests.
Two sessions use identical baseline bytes and detector observations: matching
content must not make one session's authorization transferable to another.
"""

from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_repository import repository as repository
from test_visual_attack import authorized_flat_pair

pytestmark = pytest.mark.postgres


@pytest.fixture
def authority_records(repository):
    _, _, baseline, binding, _ = authorized_flat_pair()
    sessions, captures, records = [], [], []
    for index in range(2):
        session = repository.create_session("spatial", "replay", "bottle", "cup")["session"]
        key = uuid4().hex
        capture = repository.submit(
            session["id"],
            {
                "slot": "baseline",
                "idempotency_key": key,
                "expected_revision": session["revision"],
                "epoch": session["epoch"],
                "client_seq": 0,
            },
            baseline["frames"],
            key,
        )["capture"]
        record = {
            **binding,
            "id": str(uuid4()),
            "session_id": session["id"],
            "capture_id": capture["id"],
            "reason": f"Trusted seed for database authority boundary {index}",
        }
        with repository._connection() as connection:
            connection.execute(
                "INSERT INTO counterpart_baseline_authorizations "
                "(id,session_id,capture_id,request_key,request_fingerprint,record) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (record["id"], session["id"], capture["id"], key, "f" * 64, Jsonb(record)),
            )
            connection.execute(
                "UPDATE counterpart_sessions SET baseline_id=%s,baseline_authorization=%s WHERE id=%s",
                (capture["id"], Jsonb(record), session["id"]),
            )
        sessions.append(session)
        captures.append(capture)
        records.append(record)
    return repository, sessions, captures, records


def test_attack_same_content_foreign_session_authorization_copy_is_rejected(authority_records):
    repo, sessions, captures, records = authority_records
    assert captures[0]["frames"] == captures[1]["frames"]
    assert records[0]["baseline_frames_sha256"] == records[1]["baseline_frames_sha256"]
    with pytest.raises(psycopg.errors.CheckViolation):
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_sessions SET baseline_authorization=%s WHERE id=%s",
                (Jsonb(records[0]), sessions[1]["id"]),
            )
    assert repo.snapshot(sessions[1]["id"])["baseline_authorization"] == records[1]


def test_attack_capture_cannot_import_a_foreign_authorization(authority_records):
    repo, _, captures, records = authority_records
    with pytest.raises(psycopg.errors.CheckViolation):
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_captures SET reference_capture_id=%s,baseline_authorization=%s "
                "WHERE id=%s",
                (captures[1]["id"], Jsonb(records[0]), captures[1]["id"]),
            )


def test_attack_authorization_json_cannot_drift_from_immutable_record(authority_records):
    repo, sessions, _, records = authority_records
    changed = {**records[0], "reason": "Silently changed authorization"}
    with pytest.raises(psycopg.errors.CheckViolation):
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_sessions SET baseline_authorization=%s WHERE id=%s",
                (Jsonb(changed), sessions[0]["id"]),
            )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_baseline_authorizations SET record=%s WHERE id=%s",
                (Jsonb(changed), records[0]["id"]),
            )
    assert repo.snapshot(sessions[0]["id"])["baseline_authorization"] == records[0]
