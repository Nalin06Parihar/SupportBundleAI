"""Workflow, approval, and released-bundle API routes."""

import logging
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import Field

from app.core.config import Settings, get_settings
from app.core.exceptions import AIProviderError, PolicyViolationError
from app.models._common import StrictModel, validate_relative_path
from app.models.analysis import (
    AnalysisResult,
    ConfidenceLevel,
    EvidenceSufficiency,
)
from app.models.approval import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
    BundleReleaseDecision,
    EvidenceItemDecisionType,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)
from app.models.bundle import BundleVerificationStatus
from app.models.evidence import (
    MissingEvidence,
    ResidualRiskLevel,
)
from app.models.issue import IssueRequest
from app.models.plan import EvidencePlan, PolicyDecisionStatus
from app.models.workflow import WorkflowState, WorkflowStatus
from app.repositories.approvals import ApprovalRepository
from app.repositories.bundles import BundleRepository
from app.repositories.workflows import WorkflowRepository
from app.scenarios.loader import (
    ScenarioError,
    ScenarioLoader,
    ScenarioNotFoundError,
)
from app.services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)
router = APIRouter()


class ScenarioView(StrictModel):
    scenario_id: str
    title: str
    description: str
    issue_category: str


class WorkflowStarted(StrictModel):
    workflow_id: UUID
    status: WorkflowStatus
    current_stage: str
    pending_approval: ApprovalRequest | None


class IssueSummary(StrictModel):
    title: str
    description: str
    application_name: str
    symptoms: list[str]
    scenario_id: str | None


class PlanStepView(StrictModel):
    step_id: UUID
    collector: str
    reason: str
    parameters: dict[str, object]
    required: bool
    status: str
    policy_status: str | None
    policy_reason: str | None


class PlanView(StrictModel):
    plan_id: UUID
    issue_summary: str
    suspected_category: str
    status: str
    policy_status: str | None
    steps: list[PlanStepView]
    warnings: list[str]


class CollectionProgress(StrictModel):
    step_id: UUID
    collector: str
    status: str
    warnings: list[str]
    error_code: str | None
    error_message: str | None


class SanitizationSummary(StrictModel):
    status: str
    files_processed: int
    total_redactions: int
    approved_artifact_count: int
    blocked_artifact_count: int
    residual_risk_count: int


class EvidenceReferenceView(StrictModel):
    artifact_id: UUID
    excerpt_id: UUID | None


class FindingView(StrictModel):
    finding_id: UUID
    title: str
    description: str
    category: str
    confidence: ConfidenceLevel
    evidence_references: list[EvidenceReferenceView]
    unverified_assumptions: list[str]
    suggested_next_step: str | None


class AnalysisView(StrictModel):
    analysis_id: UUID
    issue_summary: str
    findings: list[FindingView]
    missing_evidence: list[MissingEvidence]
    evidence_sufficiency: EvidenceSufficiency
    supplemental_plan_required: bool
    warnings: list[str]


class EvidenceReviewExcerpt(StrictModel):
    excerpt_id: UUID
    artifact_id: UUID
    content: str = Field(max_length=10_000)
    redaction_count: int
    residual_risk_level: ResidualRiskLevel


class BundleReviewView(StrictModel):
    workflow_id: UUID
    bundle_id: UUID
    zip_filename: str
    included_relative_filenames: list[str]
    manifest_verification: str
    artifact_count: int
    sanitized_only: bool
    missing_evidence: list[dict[str, str]]
    warnings: list[str]


class WorkflowView(StrictModel):
    workflow_id: UUID
    status: WorkflowStatus
    current_stage: str
    issue: IssueSummary
    proposed_plan: PlanView | None
    collection_progress: list[CollectionProgress]
    sanitization_summary: SanitizationSummary | None
    analysis_result: AnalysisView | None
    missing_evidence: list[MissingEvidence]
    pending_approval: ApprovalRequest | None
    evidence_review_excerpts: list[EvidenceReviewExcerpt]
    bundle_review: BundleReviewView | None
    bundle_available: bool
    bundle_filename: str | None
    warnings: list[str]
    error_code: str | None
    error_message: str | None


def get_workflow_service(request: Request) -> WorkflowService:
    """Return a process-local service instance bound to the active app database."""
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "service_unavailable", "message": "Workflow service is unavailable."},
        )
    service = getattr(request.app.state, "workflow_service", None)
    if (
        service is None
        or getattr(request.app.state, "workflow_service_factory", None)
        is not session_factory
    ):
        service = WorkflowService(session_factory, settings=get_settings())
        request.app.state.workflow_service = service
        request.app.state.workflow_service_factory = session_factory
    return service


def _session_factory(request: Request):
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        _raise_api_error(500, "service_unavailable", "Workflow service is unavailable.")
    return session_factory


def _raise_api_error(code_status: int, code: str, message: str) -> None:
    raise HTTPException(
        status_code=code_status,
        detail={"code": code, "message": message},
    )


def _raise_service_error(error: Exception) -> None:
    if isinstance(error, AIProviderError):
        _raise_api_error(502, "ai_provider_error", "The analysis provider could not complete the request.")
    if isinstance(error, PolicyViolationError):
        _raise_api_error(403, "policy_violation", "The requested operation is not permitted.")
    if isinstance(error, (ValueError,)):
        _raise_api_error(409, "invalid_workflow_state", "The workflow is not waiting for this decision.")
    if isinstance(error, LookupError):
        _raise_api_error(404, "not_found", "The requested workflow resource was not found.")
    if isinstance(error, RuntimeError):
        _raise_api_error(500, "workflow_failure", "The workflow could not be completed safely.")
    logger.error("Workflow API operation failed (%s)", type(error).__name__)
    _raise_api_error(500, "workflow_failure", "The workflow could not be completed safely.")


def _load_scenario_loader() -> ScenarioLoader:
    settings = get_settings()
    try:
        return ScenarioLoader(settings.controlled_environment_directory)
    except ScenarioError:
        _raise_api_error(500, "scenario_unavailable", "Controlled scenarios are unavailable.")


async def _current_state(
    service: WorkflowService,
    session_factory: Any,
    workflow_id: UUID,
) -> WorkflowState:
    try:
        state = await service.get_current_state(workflow_id)
    except RuntimeError:
        _raise_api_error(500, "workflow_state_unavailable", "Workflow state is unavailable.")
    if state is not None:
        return state
    if WorkflowRepository(session_factory).get(workflow_id) is None:
        _raise_api_error(404, "workflow_not_found", "Workflow was not found.")
    _raise_api_error(500, "workflow_state_unavailable", "Workflow state is unavailable.")


def _required_state(
    state: WorkflowState,
    statuses: set[WorkflowStatus],
    approval_type: ApprovalType | set[ApprovalType],
    decision_workflow_id: UUID,
    approval_id: UUID,
) -> ApprovalRequest:
    active = state.active_approval_request
    if (
        state.status not in statuses
        or active is None
        or active.workflow_id != state.workflow_id
        or active.approval_id != approval_id
        or active.approval_type
        not in (approval_type if isinstance(approval_type, set) else {approval_type})
        or active.status != ApprovalStatus.PENDING
        or decision_workflow_id != state.workflow_id
    ):
        _raise_api_error(409, "approval_not_pending", "The matching approval is not pending.")
    return active


def _approval_record(
    session_factory: Any,
    active: ApprovalRequest,
    *,
    bundle_id: UUID | None = None,
) -> None:
    record = ApprovalRepository(session_factory).get(active.approval_id)
    if (
        record is None
        or record.workflow_id != str(active.workflow_id)
        or record.approval_type != active.approval_type.value
        or (
            bundle_id is not None
            and record.bundle_id != str(bundle_id)
        )
    ):
        _raise_api_error(409, "approval_mismatch", "The approval does not match this workflow.")
    if record.status != ApprovalStatus.PENDING.value:
        _raise_api_error(409, "approval_not_pending", "The approval has already been finalized.")


def _raise_for_failed_state(state: WorkflowState) -> None:
    if state.status != WorkflowStatus.FAILED:
        return
    if state.error_code == "ai_provider_error":
        _raise_api_error(502, "ai_provider_error", "The analysis provider could not complete the request.")
    _raise_api_error(500, "workflow_failure", "The workflow could not be completed safely.")


def _plan_view(state: WorkflowState) -> PlanView | None:
    plan = state.evidence_plan
    if plan is None:
        return None
    decisions = {
        decision.step_id: decision
        for decision in state.policy_result.step_decisions
    } if state.policy_result is not None else {}
    return PlanView(
        plan_id=plan.plan_id,
        issue_summary=plan.issue_summary,
        suspected_category=plan.suspected_category,
        status=plan.status.value,
        policy_status=state.policy_result.status.value if state.policy_result else None,
        steps=[
            PlanStepView(
                step_id=step.step_id,
                collector=step.collector.value,
                reason=step.reason,
                parameters=step.parameters,
                required=step.required,
                status=step.status.value,
                policy_status=(
                    decisions[step.step_id].status.value
                    if step.step_id in decisions else None
                ),
                policy_reason=(
                    decisions[step.step_id].safe_reason
                    if step.step_id in decisions else None
                ),
            )
            for step in plan.steps
        ],
        warnings=plan.warnings,
    )


def _analysis_view(analysis: AnalysisResult | None) -> AnalysisView | None:
    if analysis is None:
        return None
    return AnalysisView(
        analysis_id=analysis.analysis_id,
        issue_summary=analysis.issue_summary,
        findings=[
            FindingView(
                finding_id=finding.finding_id,
                title=finding.title,
                description=finding.description,
                category=finding.category,
                confidence=finding.confidence,
                evidence_references=[
                    EvidenceReferenceView(
                        artifact_id=reference.artifact_id,
                        excerpt_id=reference.excerpt_id,
                    )
                    for reference in finding.evidence_references
                ],
                unverified_assumptions=finding.unverified_assumptions,
                suggested_next_step=finding.suggested_next_step,
            )
            for finding in analysis.findings
        ],
        missing_evidence=analysis.missing_evidence,
        evidence_sufficiency=analysis.evidence_sufficiency,
        supplemental_plan_required=analysis.supplemental_plan_required,
        warnings=analysis.warnings,
    )


def _workflow_view(state: WorkflowState) -> WorkflowView:
    report = state.sanitization_report
    analysis = state.analysis_result
    missing = analysis.missing_evidence if analysis is not None else state.missing_evidence
    warnings = list(
        dict.fromkeys(
            state.warnings
            + (report.warnings if report is not None else [])
            + (analysis.warnings if analysis is not None else [])
        )
    )
    bundle = state.bundle_metadata
    released = (
        state.status == WorkflowStatus.COMPLETED
        and state.bundle_release_decision is not None
        and state.bundle_release_decision.status == ApprovalStatus.APPROVED
        and bundle is not None
        and bundle.verification_status == BundleVerificationStatus.VERIFIED
        and bundle.sanitized_only
    )
    excerpts: list[EvidenceReviewExcerpt] = []
    if state.status == WorkflowStatus.AWAITING_EVIDENCE_APPROVAL and report is not None:
        approved_artifacts = set(report.approved_artifact_ids)
        blocked_artifacts = set(report.blocked_artifact_ids)
        excerpts = [
            EvidenceReviewExcerpt(
                excerpt_id=excerpt.excerpt_id,
                artifact_id=excerpt.artifact_id,
                content=excerpt.content,
                redaction_count=excerpt.redaction_count,
                residual_risk_level=excerpt.residual_risk_level,
            )
            for excerpt in state.sanitized_excerpts
            if excerpt.artifact_id in approved_artifacts
            and excerpt.artifact_id not in blocked_artifacts
            and excerpt.residual_risk_level != ResidualRiskLevel.BLOCKED
        ]
    return WorkflowView(
        workflow_id=state.workflow_id,
        status=state.status,
        current_stage=state.current_stage,
        issue=IssueSummary(
            title=state.issue.title,
            description=state.issue.description,
            application_name=state.issue.application_name,
            symptoms=state.issue.symptoms,
            scenario_id=state.issue.scenario_id,
        ),
        proposed_plan=_plan_view(state),
        collection_progress=[
            CollectionProgress(
                step_id=result.step_id,
                collector=result.collector.value,
                status=result.status.value,
                warnings=result.warnings,
                error_code=result.error.code if result.error else None,
                error_message=result.error.message if result.error else None,
            )
            for result in state.collector_results
        ],
        sanitization_summary=(
            SanitizationSummary(
                status=report.status.value,
                files_processed=report.files_processed,
                total_redactions=report.total_redactions,
                approved_artifact_count=len(report.approved_artifact_ids),
                blocked_artifact_count=len(report.blocked_artifact_ids),
                residual_risk_count=len(report.residual_risk_flags),
            )
            if report is not None
            else None
        ),
        analysis_result=_analysis_view(analysis),
        missing_evidence=missing,
        pending_approval=(
            state.active_approval_request
            if state.status in {
                WorkflowStatus.AWAITING_PLAN_APPROVAL,
                WorkflowStatus.AWAITING_SUPPLEMENTAL_APPROVAL,
                WorkflowStatus.AWAITING_EVIDENCE_APPROVAL,
                WorkflowStatus.AWAITING_RELEASE_APPROVAL,
            }
            and state.active_approval_request is not None
            and state.active_approval_request.status == ApprovalStatus.PENDING
            else None
        ),
        evidence_review_excerpts=excerpts,
        bundle_review=(
            BundleReviewView.model_validate(
                {
                    **state.bundle_review_payload,
                    "sanitized_only": bundle.sanitized_only if bundle else False,
                }
            )
            if state.bundle_review_payload is not None
            else None
        ),
        bundle_available=released,
        bundle_filename=bundle.filename if released and bundle is not None else None,
        warnings=warnings,
        error_code=state.error_code,
        error_message=state.error_message,
    )


@router.get(
    "/scenarios",
    response_model=list[ScenarioView],
    summary="List diagnostic scenarios",
)
def list_scenarios() -> list[ScenarioView]:
    try:
        scenarios = _load_scenario_loader().list_scenarios()
    except ScenarioNotFoundError:
        _raise_api_error(404, "scenario_not_found", "A controlled scenario is unavailable.")
    except ScenarioError:
        _raise_api_error(500, "scenario_unavailable", "Controlled scenarios are unavailable.")
    return [
        ScenarioView(
            scenario_id=item.scenario_id,
            title=item.title,
            description=item.description,
            issue_category=item.issue_category,
        )
        for item in scenarios
    ]


@router.post(
    "/workflows",
    response_model=WorkflowStarted,
    status_code=status.HTTP_201_CREATED,
    summary="Start a diagnostic workflow",
)
async def start_workflow(
    issue: IssueRequest,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowStarted:
    if not issue.scenario_id:
        _raise_api_error(422, "scenario_required", "A supported scenario must be selected.")
    try:
        _load_scenario_loader().load_scenario(issue.scenario_id)
        state = await service.start_workflow(issue)
    except ScenarioNotFoundError:
        _raise_api_error(404, "scenario_not_found", "The requested scenario was not found.")
    except ScenarioError:
        _raise_api_error(500, "scenario_unavailable", "Controlled scenarios are unavailable.")
    except HTTPException:
        raise
    except Exception as error:
        _raise_service_error(error)
    _raise_for_failed_state(state)
    return WorkflowStarted(
        workflow_id=state.workflow_id,
        status=state.status,
        current_stage=state.current_stage,
        pending_approval=state.active_approval_request,
    )


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowView,
    summary="Get safe workflow status",
)
async def get_workflow(
    workflow_id: UUID,
    request: Request,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowView:
    state = await _current_state(service, _session_factory(request), workflow_id)
    return _workflow_view(state)


@router.post(
    "/workflows/{workflow_id}/approvals/plan",
    response_model=WorkflowView,
    summary="Submit plan approval",
)
async def approve_plan(
    workflow_id: UUID,
    decision: PlanApprovalDecision,
    request: Request,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowView:
    factory = _session_factory(request)
    state = await _current_state(service, factory, workflow_id)
    active = _required_state(
        state,
        {WorkflowStatus.AWAITING_PLAN_APPROVAL, WorkflowStatus.AWAITING_SUPPLEMENTAL_APPROVAL},
        {ApprovalType.COLLECTION_PLAN, ApprovalType.SUPPLEMENTAL_COLLECTION},
        decision.workflow_id,
        decision.approval_id,
    )
    _approval_record(factory, active)
    plan = state.evidence_plan
    policy = state.policy_result
    if plan is None or policy is None:
        _raise_api_error(409, "plan_unavailable", "The approved plan is unavailable.")
    plan_step_ids = {step.step_id for step in plan.steps}
    proposed_step_ids = set(decision.approved_step_ids) | set(
        decision.rejected_step_ids
    )
    if not proposed_step_ids.issubset(plan_step_ids):
        _raise_api_error(409, "invalid_plan_step", "The decision references an unknown plan step.")
    policy_approved_ids = {
        item.step_id
        for item in policy.step_decisions
        if item.status
        in {
            PolicyDecisionStatus.APPROVED,
            PolicyDecisionStatus.APPROVED_WITH_CHANGES,
        }
    }
    if not set(decision.approved_step_ids).issubset(policy_approved_ids):
        _raise_api_error(403, "policy_violation", "The selected plan step is not locally approved.")
    try:
        updated = await service.resume_workflow(workflow_id, decision)
    except Exception as error:
        _raise_service_error(error)
    _raise_for_failed_state(updated)
    return _workflow_view(updated)


@router.post(
    "/workflows/{workflow_id}/approvals/evidence",
    response_model=WorkflowView,
    summary="Submit evidence-release approval",
)
async def approve_evidence(
    workflow_id: UUID,
    decision: EvidenceReleaseDecision,
    request: Request,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowView:
    factory = _session_factory(request)
    state = await _current_state(service, factory, workflow_id)
    active = _required_state(
        state,
        {WorkflowStatus.AWAITING_EVIDENCE_APPROVAL},
        ApprovalType.EVIDENCE_RELEASE,
        decision.workflow_id,
        decision.approval_id,
    )
    _approval_record(factory, active)
    report = state.sanitization_report
    excerpt_by_id = {item.excerpt_id: item for item in state.sanitized_excerpts}
    if decision.status in {
        ApprovalStatus.APPROVED,
        ApprovalStatus.PARTIALLY_APPROVED,
    }:
        if report is None:
            _raise_api_error(409, "evidence_unavailable", "Sanitized evidence is unavailable.")
        approved_artifacts = set(report.approved_artifact_ids)
        blocked_artifacts = set(report.blocked_artifact_ids)
        for item in decision.item_decisions:
            if item.decision == EvidenceItemDecisionType.INCLUDE:
                if item.artifact_id not in approved_artifacts or item.artifact_id in blocked_artifacts:
                    _raise_api_error(403, "evidence_blocked", "The selected artifact is not eligible for release.")
            if any(
                excerpt_id not in excerpt_by_id
                or excerpt_by_id[excerpt_id].artifact_id != item.artifact_id
                or excerpt_by_id[excerpt_id].residual_risk_level == ResidualRiskLevel.BLOCKED
                for excerpt_id in item.approved_excerpt_ids
            ):
                _raise_api_error(403, "evidence_blocked", "The selected excerpt is not eligible for release.")
    try:
        updated = await service.resume_evidence_release(workflow_id, decision)
    except Exception as error:
        _raise_service_error(error)
    _raise_for_failed_state(updated)
    return _workflow_view(updated)


@router.post(
    "/workflows/{workflow_id}/approvals/bundle",
    response_model=WorkflowView,
    summary="Submit bundle-release approval",
)
async def approve_bundle(
    workflow_id: UUID,
    decision: BundleReleaseDecision,
    request: Request,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowView:
    factory = _session_factory(request)
    state = await _current_state(service, factory, workflow_id)
    active = _required_state(
        state,
        {WorkflowStatus.AWAITING_RELEASE_APPROVAL},
        ApprovalType.BUNDLE_RELEASE,
        decision.workflow_id,
        decision.approval_id,
    )
    _approval_record(factory, active, bundle_id=decision.bundle_id)
    bundle = state.bundle_metadata
    if (
        bundle is None
        or bundle.bundle_id != decision.bundle_id
        or bundle.verification_status != BundleVerificationStatus.VERIFIED
        or not bundle.sanitized_only
    ):
        _raise_api_error(403, "bundle_not_verified", "Only verified sanitized bundles may be released.")
    try:
        updated = await service.resume_bundle_release(workflow_id, decision)
    except Exception as error:
        _raise_service_error(error)
    _raise_for_failed_state(updated)
    return _workflow_view(updated)


@router.get(
    "/workflows/{workflow_id}/bundle",
    summary="Download a released diagnostic bundle",
    response_class=FileResponse,
)
async def download_bundle(
    workflow_id: UUID,
    request: Request,
    service: WorkflowService = Depends(get_workflow_service),
) -> FileResponse:
    factory = _session_factory(request)
    state = await _current_state(service, factory, workflow_id)
    bundle = state.bundle_metadata
    if bundle is None:
        _raise_api_error(404, "bundle_not_found", "A released bundle was not found.")
    record = BundleRepository(factory).get(bundle.bundle_id)
    if (
        record is None
        or record.workflow_id != str(workflow_id)
        or record.verification_status != BundleVerificationStatus.VERIFIED.value
        or not record.sanitized_only
        or record.release_status != ApprovalStatus.APPROVED.value
        or state.status != WorkflowStatus.COMPLETED
        or state.bundle_release_decision is None
        or state.bundle_release_decision.status != ApprovalStatus.APPROVED
    ):
        _raise_api_error(403, "bundle_not_released", "The bundle is not approved for release.")

    settings: Settings = get_settings()
    try:
        relative_path = validate_relative_path(record.relative_path)
        root = Path(settings.output_directory).resolve(strict=True)
        unresolved = root.joinpath(*PurePosixPath(relative_path).parts)
        current = root
        for part in PurePosixPath(relative_path).parts:
            current = current / part
            if current.is_symlink():
                _raise_api_error(403, "bundle_path_invalid", "The released bundle is unavailable.")
        archive = unresolved.resolve(strict=True)
        if not archive.is_relative_to(root) or not archive.is_file():
            _raise_api_error(404, "bundle_not_found", "The released bundle is unavailable.")
    except HTTPException:
        raise
    except (OSError, ValueError):
        _raise_api_error(404, "bundle_not_found", "The released bundle is unavailable.")

    return FileResponse(
        path=archive,
        media_type="application/zip",
        filename=record.filename,
    )
