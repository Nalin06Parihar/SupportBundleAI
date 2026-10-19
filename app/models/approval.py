"""Human approval request and decision contracts."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.models._common import StrictModel, UTCDateTime, utc_now


class ApprovalType(StrEnum):
    COLLECTION_PLAN = "collection_plan"
    EVIDENCE_RELEASE = "evidence_release"
    SUPPLEMENTAL_COLLECTION = "supplemental_collection"
    BUNDLE_RELEASE = "bundle_release"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    PARTIALLY_APPROVED = "partially_approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"
    CANCELLED = "cancelled"


class ApprovalRequest(StrictModel):
    """Safe metadata asking a human to review a workflow action."""

    approval_id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID
    approval_type: ApprovalType
    status: ApprovalStatus = ApprovalStatus.PENDING
    title: str = Field(min_length=1, max_length=150)
    explanation: str = Field(min_length=1, max_length=1000)
    requested_at: UTCDateTime = Field(default_factory=utc_now)
    expires_at: UTCDateTime | None = None


class PlanApprovalDecision(StrictModel):
    """Human decision over individual evidence-plan steps."""

    approval_id: UUID
    workflow_id: UUID
    status: ApprovalStatus
    approved_step_ids: list[UUID] = Field(default_factory=list, max_length=10)
    rejected_step_ids: list[UUID] = Field(default_factory=list, max_length=10)
    comment: str | None = Field(default=None, max_length=1000)
    decided_at: UTCDateTime = Field(default_factory=utc_now)

    @field_validator("approved_step_ids", "rejected_step_ids")
    @classmethod
    def deduplicate_step_ids(cls, step_ids: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(step_ids))

    @model_validator(mode="after")
    def validate_decision(self) -> "PlanApprovalDecision":
        valid_statuses = {
            ApprovalStatus.APPROVED,
            ApprovalStatus.PARTIALLY_APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.REVISION_REQUESTED,
            ApprovalStatus.CANCELLED,
        }
        if self.status not in valid_statuses:
            raise ValueError("Plan decision status is not valid.")
        approved = set(self.approved_step_ids)
        rejected = set(self.rejected_step_ids)
        if approved & rejected:
            raise ValueError("A step cannot be both approved and rejected.")
        if self.status == ApprovalStatus.APPROVED and not approved:
            raise ValueError("An approved decision requires an approved step.")
        if self.status == ApprovalStatus.PARTIALLY_APPROVED and (
            not approved or not rejected
        ):
            raise ValueError(
                "Partial approval requires approved and rejected steps."
            )
        if self.status == ApprovalStatus.REJECTED and approved:
            raise ValueError("Rejected decisions cannot include approved steps.")
        if self.status == ApprovalStatus.REVISION_REQUESTED and not self.comment:
            raise ValueError("Revision requests require a non-empty comment.")
        return self


class EvidenceItemDecisionType(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    REVISE = "revise"


class EvidenceItemDecision(StrictModel):
    """Human review decision for one sanitized artifact."""

    artifact_id: UUID
    decision: EvidenceItemDecisionType
    approved_excerpt_ids: list[UUID] = Field(default_factory=list, max_length=100)
    excluded_excerpt_ids: list[UUID] = Field(default_factory=list, max_length=100)
    reviewer_comment: str | None = Field(default=None, max_length=1000)

    @field_validator("approved_excerpt_ids", "excluded_excerpt_ids")
    @classmethod
    def deduplicate_excerpt_ids(cls, excerpt_ids: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(excerpt_ids))

    @model_validator(mode="after")
    def validate_excerpt_decisions(self) -> "EvidenceItemDecision":
        if set(self.approved_excerpt_ids) & set(self.excluded_excerpt_ids):
            raise ValueError("An excerpt cannot be both approved and excluded.")
        if (
            self.decision == EvidenceItemDecisionType.EXCLUDE
            and self.approved_excerpt_ids
        ):
            raise ValueError("Excluded evidence cannot contain approved excerpts.")
        if self.decision == EvidenceItemDecisionType.INCLUDE and not (
            self.approved_excerpt_ids
        ):
            raise ValueError("Included evidence must identify approved excerpts.")
        return self


class EvidenceReleaseDecision(StrictModel):
    """Explicit human approval for external analysis of selected evidence."""

    approval_id: UUID
    workflow_id: UUID
    status: ApprovalStatus
    item_decisions: list[EvidenceItemDecision] = Field(max_length=100)
    allow_external_ai_analysis: bool
    reviewer_acknowledgement: bool
    decided_at: UTCDateTime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_external_release(self) -> "EvidenceReleaseDecision":
        artifact_ids = [item.artifact_id for item in self.item_decisions]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("Evidence items must be unique.")
        approved_excerpt_ids = {
            excerpt_id
            for item in self.item_decisions
            for excerpt_id in item.approved_excerpt_ids
        }
        excluded_excerpt_ids = {
            excerpt_id
            for item in self.item_decisions
            for excerpt_id in item.excluded_excerpt_ids
        }
        if approved_excerpt_ids & excluded_excerpt_ids:
            raise ValueError("An excerpt cannot be included and excluded in one decision.")

        if self.status in {ApprovalStatus.REJECTED, ApprovalStatus.CANCELLED}:
            if self.allow_external_ai_analysis:
                raise ValueError(
                    "Rejected or cancelled evidence cannot be released externally."
                )
        if self.allow_external_ai_analysis:
            if self.status not in {
                ApprovalStatus.APPROVED,
                ApprovalStatus.PARTIALLY_APPROVED,
            }:
                raise ValueError("Evidence release requires an approved decision.")
            if not self.reviewer_acknowledgement:
                raise ValueError("External analysis requires reviewer acknowledgement.")
            if not any(
                item.decision == EvidenceItemDecisionType.INCLUDE
                and item.approved_excerpt_ids
                for item in self.item_decisions
            ):
                raise ValueError("External analysis requires included evidence.")
        return self


class BundleReleaseDecision(StrictModel):
    """Human decision authorizing release of a candidate diagnostic bundle."""

    approval_id: UUID
    workflow_id: UUID
    bundle_id: UUID
    status: ApprovalStatus
    comment: str | None = Field(default=None, max_length=1000)
    reviewer_acknowledgement: bool
    decided_at: UTCDateTime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_bundle_acknowledgement(self) -> "BundleReleaseDecision":
        if (
            self.status == ApprovalStatus.APPROVED
            and not self.reviewer_acknowledgement
        ):
            raise ValueError("Bundle approval requires reviewer acknowledgement.")
        return self
