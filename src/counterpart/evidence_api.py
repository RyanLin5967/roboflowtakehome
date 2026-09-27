"""Operator-only saved-observation comparison and explicit uncertainty review."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from .evidence_revisions import EvidenceRepository
from .models import StrictModel


class Reverify(StrictModel):
    expected_version: int = Field(ge=0)
    expected_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipe_version: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")


class AttemptReview(StrictModel):
    decision: Literal["confirmed_uncertain", "rejected", "needs_recapture"]
    expected_version: int = Field(ge=0)
    expected_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")


class AdjudicationLabels(StrictModel):
    baseline: str = Field(min_length=1, max_length=120)
    counterpart: str = Field(min_length=1, max_length=120)


class AdjudicationCriteria(StrictModel):
    original_media_reviewed: Literal[True]
    objects_identified: Literal[True]
    relation_confirmed: Literal[True]
    controls_confirmed: Literal[True]


class BaselineCriteria(StrictModel):
    original_media_reviewed: Literal[True]
    objects_identified: Literal[True]
    initial_relation_confirmed: Literal[True]
    uncertain_visual_controls_reviewed: Literal[True]


class AuthorizeBaseline(StrictModel):
    expected_session_revision: int = Field(ge=0)
    expected_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer: str = Field(min_length=1, max_length=120)
    criteria: BaselineCriteria
    reason: str = Field(min_length=20, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")


class Adjudicate(StrictModel):
    baseline_id: str = Field(min_length=36, max_length=36)
    counterpart_id: str = Field(min_length=36, max_length=36)
    expected_session_revision: int = Field(ge=0)
    expected_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    labels: AdjudicationLabels
    criteria: AdjudicationCriteria
    reason: str = Field(min_length=20, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")


def mount_evidence(app, repo, media, auth):
    evidence = EvidenceRepository(repo, media)
    app.state.evidence = evidence
    router = APIRouter(prefix="/api/operator", dependencies=[Depends(auth.require)])

    @router.get("/sessions/{session_id}/evidence")
    def detail(session_id: str, baseline_id: str | None = None, counterpart_id: str | None = None):
        return evidence.detail(session_id, baseline_id, counterpart_id)

    @router.post("/sessions/{session_id}/captures/{capture_id}/reverify", status_code=201)
    def reverify(session_id: str, capture_id: str, body: Reverify):
        return evidence.reverify(session_id, capture_id, **body.model_dump())

    @router.post("/sessions/{session_id}/captures/{capture_id}/review", status_code=201)
    def review_attempt(session_id: str, capture_id: str, body: AttemptReview):
        return evidence.review_attempt(session_id, capture_id, **body.model_dump())

    @router.post("/sessions/{session_id}/captures/{capture_id}/authorize-baseline", status_code=201)
    def authorize_baseline(session_id: str, capture_id: str, body: AuthorizeBaseline):
        return evidence.authorize_baseline(session_id, capture_id, **body.model_dump())

    @router.post("/sessions/{session_id}/adjudications", status_code=201)
    def adjudicate(session_id: str, body: Adjudicate):
        return evidence.adjudicate(session_id, **body.model_dump())

    app.include_router(router)
