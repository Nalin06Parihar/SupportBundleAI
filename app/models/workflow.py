"""Workflow identity, status, audit-event, and LangGraph-state contracts."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from app.models._common import StrictModel, UTCDateTime, utc_now
from app.models.analysis import AnalysisResult
from app.models.approval import (
    ApprovalRequest,
    BundleReleaseDecision,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)
from app.models.bundle import BundleMetadata
from app.models.evidence import (
    CollectorResult,
    MissingEvidence,
    SanitizationReport,
    SanitizedEvidenceExcerpt,
)
from app.models.issue import IssueRequest
from app.models.plan import EvidencePlan, PlanPolicyResult


class WorkflowStatus(StrEnum):
    RECEIVED = "received"
    PLANNING = "planning"
    PLAN_VALIDATION = "plan_validation"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    COLLECTING = "collecting"
    SANITIZING = "sanitizing"
    RESIDUAL_RISK_REVIEW = "residual_risk_review"
    AWAITING_EVIDENCE_APPROVAL = "awaiting_evidence_approval"
    ANALYZING = "analyzing"
    EVALUATING_EVIDENCE = "evaluating_evidence"
    AWAITING_SUPPLEMENTAL_APPROVAL = "awaiting_supplemental_approval"
    GENERATING_REPORT = "generating_report"
    PACKAGING = "packaging"
    AWAITING_RELEASE_APPROVAL = "awaiting_release_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    PARTIAL = "partial"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class WorkflowEventType(StrEnum):
    WORKFLOW_CREATED = "workflow_created"
    STATUS_CHANGED = "status_changed"
    PLAN_GENERATED = "plan_generated"
    PLAN_VALIDATED = "plan_validated"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RECEIVED = "approval_received"
    COLLECTOR_STARTED = "collector_started"
    COLLECTOR_COMPLETED = "collector_completed"
    SANITIZATION_COMPLETED = "sanitization_completed"
    RESIDUAL_RISK_DETECTED = "residual_risk_detected"
    EVIDENCE_RELEASED = "evidence_released"
    ANALYSIS_COMPLETED = "analysis_completed"
    SUPPLEMENTAL_PLAN_REQUESTED = "supplemental_plan_requested"
    REPORT_GENERATED = "report_generated"
    BUNDLE_CREATED = "bundle_created"
    BUNDLE_VERIFIED = "bundle_verified"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"


class WorkflowEvent(StrictModel):
    """Safe, displayable workflow audit event."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: WorkflowEventType
    message: str = Field(
        min_length=1,
        max_length=500,
        description="Safe display summary only; never raw logs, secrets, prompts, or evidence.",
    )
    created_at: UTCDateTime = Field(default_factory=utc_now)


class WorkflowSummary(StrictModel):
    """Public summary of workflow identity and current status."""

    workflow_id: UUID
    issue_id: UUID
    status: WorkflowStatus
    current_stage: str = Field(min_length=1, max_length=100)
    created_at: UTCDateTime
    updated_at: UTCDateTime
    warning_count: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=100)


class WorkflowState(StrictModel):
    """Serializable business state intended to move between future graph nodes."""

    workflow_id: UUID = Field(default_factory=uuid4)
    issue: IssueRequest
    status: WorkflowStatus = WorkflowStatus.RECEIVED
    current_stage: str = Field(default="received", min_length=1, max_length=100)
    evidence_plan: EvidencePlan | None = None
    policy_result: PlanPolicyResult | None = None
    active_approval_request: ApprovalRequest | None = None
    plan_approval: PlanApprovalDecision | None = None
    collector_results: list[CollectorResult] = Field(default_factory=list, max_length=100)
    sanitized_excerpts: list[SanitizedEvidenceExcerpt] = Field(
        default_factory=list,
        max_length=1000,
    )
    sanitization_report: SanitizationReport | None = None
    evidence_release_decision: EvidenceReleaseDecision | None = None
    analysis_result: AnalysisResult | None = None
    supplemental_plan: EvidencePlan | None = None
    bundle_metadata: BundleMetadata | None = None
    bundle_release_decision: BundleReleaseDecision | None = None
    missing_evidence: list[MissingEvidence] = Field(default_factory=list, max_length=100)
    events: list[WorkflowEvent] = Field(default_factory=list, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=500)
    created_at: UTCDateTime = Field(default_factory=utc_now)
    updated_at: UTCDateTime = Field(default_factory=utc_now)
