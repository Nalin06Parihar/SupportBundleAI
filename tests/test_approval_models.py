"""Validation tests for explicit human approval contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.approval import (
    ApprovalStatus,
    BundleReleaseDecision,
    EvidenceItemDecision,
    EvidenceItemDecisionType,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)


def test_valid_plan_approval_is_accepted() -> None:
    decision = PlanApprovalDecision(
        approval_id=uuid4(),
        workflow_id=uuid4(),
        status=ApprovalStatus.APPROVED,
        approved_step_ids=[uuid4()],
    )
    assert decision.status == ApprovalStatus.APPROVED


def test_step_cannot_be_both_approved_and_rejected() -> None:
    step_id = uuid4()
    with pytest.raises(ValidationError):
        PlanApprovalDecision(
            approval_id=uuid4(),
            workflow_id=uuid4(),
            status=ApprovalStatus.PARTIALLY_APPROVED,
            approved_step_ids=[step_id],
            rejected_step_ids=[step_id],
        )


def test_approved_decision_requires_approved_steps() -> None:
    with pytest.raises(ValidationError):
        PlanApprovalDecision(
            approval_id=uuid4(),
            workflow_id=uuid4(),
            status=ApprovalStatus.APPROVED,
        )


def test_partial_decision_requires_approved_and_rejected_steps() -> None:
    with pytest.raises(ValidationError):
        PlanApprovalDecision(
            approval_id=uuid4(),
            workflow_id=uuid4(),
            status=ApprovalStatus.PARTIALLY_APPROVED,
            approved_step_ids=[uuid4()],
        )


def test_revision_request_requires_comment() -> None:
    with pytest.raises(ValidationError):
        PlanApprovalDecision(
            approval_id=uuid4(),
            workflow_id=uuid4(),
            status=ApprovalStatus.REVISION_REQUESTED,
        )


def make_release_decision(**overrides: object) -> EvidenceReleaseDecision:
    values: dict[str, object] = {
        "approval_id": uuid4(),
        "workflow_id": uuid4(),
        "status": ApprovalStatus.APPROVED,
        "item_decisions": [
            EvidenceItemDecision(
                artifact_id=uuid4(),
                decision=EvidenceItemDecisionType.INCLUDE,
                approved_excerpt_ids=[uuid4()],
            )
        ],
        "allow_external_ai_analysis": True,
        "reviewer_acknowledgement": True,
    }
    values.update(overrides)
    return EvidenceReleaseDecision(**values)


def test_rejected_release_cannot_enable_external_analysis() -> None:
    with pytest.raises(ValidationError):
        make_release_decision(
            status=ApprovalStatus.REJECTED,
            allow_external_ai_analysis=True,
        )


def test_external_analysis_requires_reviewer_acknowledgement() -> None:
    with pytest.raises(ValidationError):
        make_release_decision(reviewer_acknowledgement=False)


def test_external_analysis_requires_included_evidence() -> None:
    with pytest.raises(ValidationError):
        make_release_decision(item_decisions=[])


def test_explicitly_approved_evidence_release_is_accepted() -> None:
    decision = make_release_decision()
    assert decision.allow_external_ai_analysis
    assert decision.reviewer_acknowledgement


def test_approved_excerpt_cannot_also_be_excluded() -> None:
    excerpt_id = uuid4()
    with pytest.raises(ValidationError):
        EvidenceItemDecision(
            artifact_id=uuid4(),
            decision=EvidenceItemDecisionType.INCLUDE,
            approved_excerpt_ids=[excerpt_id],
            excluded_excerpt_ids=[excerpt_id],
        )


def test_excluded_artifact_cannot_include_approved_excerpts() -> None:
    with pytest.raises(ValidationError):
        EvidenceItemDecision(
            artifact_id=uuid4(),
            decision=EvidenceItemDecisionType.EXCLUDE,
            approved_excerpt_ids=[uuid4()],
        )


def test_approved_bundle_release_requires_acknowledgement() -> None:
    with pytest.raises(ValidationError):
        BundleReleaseDecision(
            approval_id=uuid4(),
            workflow_id=uuid4(),
            bundle_id=uuid4(),
            status=ApprovalStatus.APPROVED,
            reviewer_acknowledgement=False,
        )


def test_approved_bundle_release_with_acknowledgement_is_accepted() -> None:
    decision = BundleReleaseDecision(
        approval_id=uuid4(),
        workflow_id=uuid4(),
        bundle_id=uuid4(),
        status=ApprovalStatus.APPROVED,
        reviewer_acknowledgement=True,
    )
    assert decision.status == ApprovalStatus.APPROVED
