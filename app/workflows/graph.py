"""JSON-only LangGraph orchestration through sanitized evidence review."""

import logging
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TypedDict
from uuid import UUID, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from app.collectors.registry import CollectorRegistry
from app.models.approval import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
    BundleReleaseDecision,
    EvidenceItemDecisionType,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)
from app.models.analysis import AnalysisResult, EvidenceSufficiency
from app.models.bundle import (
    BundleMetadata,
    BundleVerificationStatus,
    ManifestEntry,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import AIProviderError
from app.models._common import validate_secret_free_text
from app.models.evidence import (
    ArtifactMetadata,
    ArtifactType,
    CollectionStatus,
    CollectorResult,
    MissingEvidence,
    RedactionCount,
    SafeCollectorError,
    SanitizationReport,
    SanitizationStatus,
    SanitizedEvidenceExcerpt,
    SensitivityStatus,
)
from app.models.issue import IssueRequest
from app.models.plan import (
    EvidencePlan,
    EvidenceStep,
    EvidenceStepStatus,
    PlanPolicyResult,
    PlanStatus,
    PolicyDecisionStatus,
)
from app.models.scenario import ScenarioDefinition
from app.models.workflow import (
    WorkflowEvent,
    WorkflowEventType,
    WorkflowStatus,
)
from app.repositories.approvals import ApprovalRepository
from app.repositories.artifacts import ArtifactRepository
from app.repositories.analyses import AnalysisRepository
from app.repositories.bundles import BundleRepository
from app.repositories.workflows import WorkflowRepository
from app.scenarios.loader import ScenarioLoader
from app.security.policy import PlanPolicyValidator
from app.security.sanitizer import EvidenceSanitizationService
from app.services.planning_service import EvidencePlanningService
from app.services.analysis_service import (
    EvidenceAnalysisService,
    build_external_analysis_payload,
    validate_analysis_result,
)
from app.reporting.summary_renderer import render_escalation_summary
from app.bundling.manifest import MANIFEST_FILENAME, generate_manifest
from app.bundling.exporter import create_candidate_zip, verify_candidate_zip

logger = logging.getLogger(__name__)
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9:/])(?:[A-Za-z]:\\[^\r\n\"<>|?*]+|"
    r"\\\\[^\\/\r\n]+\\[^\\\r\n]+|"
    r"/(?:[^\s/\"'<>]+/)*[^\s/\"'<>]+)"
)


class WorkflowGraphState(TypedDict, total=False):
    """Checkpoint-safe graph data; every field is JSON serializable."""

    workflow_id: str
    issue_id: str
    issue: dict[str, object]
    scenario_id: str | None
    status: str
    current_stage: str
    evidence_plan: dict[str, object] | None
    policy_result: dict[str, object] | None
    active_approval_request: dict[str, object] | None
    approval_payload: dict[str, object] | None
    evidence_approval_payload: dict[str, object] | None
    external_analysis_payload: dict[str, object] | None
    plan_approval: dict[str, object] | None
    evidence_release_decision: dict[str, object] | None
    collector_results: list[dict[str, object]]
    sanitization_report: dict[str, object] | None
    sanitized_excerpts: list[dict[str, object]]
    analysis_result: dict[str, object] | None
    missing_evidence: list[dict[str, object]]
    supplemental_plan: dict[str, object] | None
    supplemental_cycle_count: int
    bundle_metadata: dict[str, object] | None
    bundle_release_decision: dict[str, object] | None
    bundle_review_payload: dict[str, object] | None
    bundle_manifest: list[dict[str, object]]
    bundle_artifact_paths: list[str]
    bundle_file_types: dict[str, str]
    bundle_revision_count: int
    supplemental_pending: bool
    evidence_review_revision: int
    warnings: list[str]
    error_code: str | None
    error_message: str | None
    revision_count: int
    _collection_partial: bool
    created_at: str
    updated_at: str


class WorkflowNodes:
    """Small graph-node adapter around existing domain services and repositories."""

    def __init__(
        self,
        *,
        workflows: WorkflowRepository,
        approvals: ApprovalRepository,
        artifacts: ArtifactRepository,
        analyses: AnalysisRepository,
        bundles: BundleRepository,
        scenario_loader: ScenarioLoader,
        registry: CollectorRegistry,
        policy_validator: PlanPolicyValidator,
        planner: EvidencePlanningService,
        sanitizer: EvidenceSanitizationService,
        analyzer: EvidenceAnalysisService,
        settings: Settings | None = None,
    ) -> None:
        self.workflows = workflows
        self.approvals = approvals
        self.artifacts = artifacts
        self.analyses = analyses
        self.scenario_loader = scenario_loader
        self.registry = registry
        self.policy_validator = policy_validator
        self.planner = planner
        self.sanitizer = sanitizer
        self.analyzer = analyzer
        self.settings = settings or get_settings()
        self.bundles = bundles

    def build(self, checkpointer):
        graph = StateGraph(WorkflowGraphState)
        graph.add_node("initialize_workflow", self._guard("initialize_workflow", self.initialize_workflow))
        graph.add_node("validate_context", self._guard("validate_context", self.validate_context))
        graph.add_node("generate_evidence_plan", self._guard("generate_evidence_plan", self.generate_evidence_plan))
        graph.add_node("validate_evidence_plan", self._guard("validate_evidence_plan", self.validate_evidence_plan))
        graph.add_node("request_plan_approval", self._guard("request_plan_approval", self.request_plan_approval))
        graph.add_node("await_plan_approval", self._guard("await_plan_approval", self.await_plan_approval))
        graph.add_node("process_plan_decision", self._guard("process_plan_decision", self.process_plan_decision))
        graph.add_node("execute_approved_collectors", self._guard("execute_approved_collectors", self.execute_approved_collectors))
        graph.add_node("complete_collection_stage", self._guard("complete_collection_stage", self.complete_collection_stage))
        graph.add_node("sanitize_evidence", self._guard("sanitize_evidence", self.sanitize_evidence))
        graph.add_node("verify_sanitized_evidence", self._guard("verify_sanitized_evidence", self.verify_sanitized_evidence))
        graph.add_node("classify_residual_risk", self._guard("classify_residual_risk", self.classify_residual_risk))
        graph.add_node("prepare_evidence_review", self._guard("prepare_evidence_review", self.prepare_evidence_review))
        graph.add_node("await_evidence_approval", self._guard("await_evidence_approval", self.await_evidence_approval))
        graph.add_node("process_evidence_release_decision", self._guard("process_evidence_release_decision", self.process_evidence_release_decision))
        graph.add_node("build_external_analysis_payload", self._guard("build_external_analysis_payload", self.build_external_analysis_payload_node))
        graph.add_node("analyze_approved_evidence", self._guard("analyze_approved_evidence", self.analyze_approved_evidence))
        graph.add_node("validate_analysis_result", self._guard("validate_analysis_result", self.validate_analysis_result_node))
        graph.add_node("evaluate_evidence_sufficiency", self._guard("evaluate_evidence_sufficiency", self.evaluate_evidence_sufficiency))
        graph.add_node("prepare_supplemental_plan", self._guard("prepare_supplemental_plan", self.prepare_supplemental_plan))
        graph.add_node("complete_analysis_stage", self._guard("complete_analysis_stage", self.complete_analysis_stage))
        graph.add_node("render_escalation_report", self._guard("render_escalation_report", self.render_escalation_report))
        graph.add_node("assemble_candidate_bundle", self._guard("assemble_candidate_bundle", self.assemble_candidate_bundle))
        graph.add_node("generate_manifest", self._guard("generate_manifest", self.generate_manifest_node))
        graph.add_node("create_candidate_zip", self._guard("create_candidate_zip", self.create_candidate_zip_node))
        graph.add_node("verify_candidate_bundle", self._guard("verify_candidate_bundle", self.verify_candidate_bundle))
        graph.add_node("request_bundle_release", self._guard("request_bundle_release", self.request_bundle_release))
        graph.add_node("await_bundle_release", self._guard("await_bundle_release", self.await_bundle_release))
        graph.add_node("process_bundle_release_decision", self._guard("process_bundle_release_decision", self.process_bundle_release_decision))
        graph.add_node("complete_workflow", self._guard("complete_workflow", self.complete_workflow))
        graph.add_node(
            "reject_policy_plan",
            self._guard("reject_policy_plan", self.reject_policy_plan),
        )
        graph.add_node(
            "finish_plan_decision",
            self._guard("finish_plan_decision", self.finish_plan_decision),
        )
        graph.add_node(
            "handle_workflow_failure",
            self._guard("handle_workflow_failure", self.handle_workflow_failure),
        )

        graph.add_edge(START, "initialize_workflow")
        graph.add_conditional_edges(
            "initialize_workflow",
            self._failure_route("validate_context"),
            {"validate_context": "validate_context", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "validate_context",
            self._failure_route("generate_evidence_plan"),
            {"generate_evidence_plan": "generate_evidence_plan", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "generate_evidence_plan",
            self._failure_route("validate_evidence_plan"),
            {"validate_evidence_plan": "validate_evidence_plan", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "validate_evidence_plan",
            self.route_after_policy,
            {
                "request_plan_approval": "request_plan_approval",
                "request_supplemental_approval": "request_plan_approval",
                "reject_policy_plan": "reject_policy_plan",
                "failure": "handle_workflow_failure",
            },
        )
        graph.add_edge("request_plan_approval", "await_plan_approval")
        graph.add_conditional_edges(
            "await_plan_approval",
            self._failure_route("process_plan_decision"),
            {"process_plan_decision": "process_plan_decision", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "process_plan_decision",
            self.route_after_decision,
            {
                "execute_approved_collectors": "execute_approved_collectors",
                "generate_evidence_plan": "generate_evidence_plan",
                "finish_plan_decision": "finish_plan_decision",
                "failure": "handle_workflow_failure",
            },
        )
        graph.add_conditional_edges(
            "execute_approved_collectors",
            self._failure_route("complete_collection_stage"),
            {"complete_collection_stage": "complete_collection_stage", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "complete_collection_stage",
            self._failure_route("sanitize_evidence"),
            {"sanitize_evidence": "sanitize_evidence", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "sanitize_evidence",
            self._failure_route("verify_sanitized_evidence"),
            {"verify_sanitized_evidence": "verify_sanitized_evidence", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "verify_sanitized_evidence",
            self._failure_route("classify_residual_risk"),
            {"classify_residual_risk": "classify_residual_risk", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "classify_residual_risk",
            self._failure_route("prepare_evidence_review"),
            {"prepare_evidence_review": "prepare_evidence_review", "failure": "handle_workflow_failure"},
        )
        graph.add_edge("prepare_evidence_review", "await_evidence_approval")
        graph.add_conditional_edges(
            "await_evidence_approval",
            self._failure_route("process_evidence_release_decision"),
            {"process_evidence_release_decision": "process_evidence_release_decision", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "process_evidence_release_decision",
            self.route_after_evidence_release,
            {
                "build_external_analysis_payload": "build_external_analysis_payload",
                "prepare_evidence_review": "prepare_evidence_review",
                "complete_analysis_stage": "complete_analysis_stage",
                "end": END,
                "failure": "handle_workflow_failure",
            },
        )
        graph.add_conditional_edges(
            "build_external_analysis_payload",
            self._failure_route("analyze_approved_evidence"),
            {"analyze_approved_evidence": "analyze_approved_evidence", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "analyze_approved_evidence",
            self._failure_route("validate_analysis_result"),
            {"validate_analysis_result": "validate_analysis_result", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "validate_analysis_result",
            self._failure_route("evaluate_evidence_sufficiency"),
            {"evaluate_evidence_sufficiency": "evaluate_evidence_sufficiency", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "evaluate_evidence_sufficiency",
            self.route_after_sufficiency,
            {
                "prepare_supplemental_plan": "prepare_supplemental_plan",
                "complete_analysis_stage": "complete_analysis_stage",
                "failure": "handle_workflow_failure",
            },
        )
        graph.add_conditional_edges(
            "prepare_supplemental_plan",
            self._failure_route("validate_evidence_plan"),
            {"validate_evidence_plan": "validate_evidence_plan", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "complete_analysis_stage",
            self._failure_route("render_escalation_report"),
            {"render_escalation_report": "render_escalation_report", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "render_escalation_report",
            self._failure_route("assemble_candidate_bundle"),
            {"assemble_candidate_bundle": "assemble_candidate_bundle", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "assemble_candidate_bundle",
            self._failure_route("generate_manifest"),
            {"generate_manifest": "generate_manifest", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "generate_manifest",
            self._failure_route("create_candidate_zip"),
            {"create_candidate_zip": "create_candidate_zip", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "create_candidate_zip",
            self._failure_route("verify_candidate_bundle"),
            {"verify_candidate_bundle": "verify_candidate_bundle", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "verify_candidate_bundle",
            self._failure_route("request_bundle_release"),
            {"request_bundle_release": "request_bundle_release", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "request_bundle_release",
            self._failure_route("await_bundle_release"),
            {"await_bundle_release": "await_bundle_release", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "await_bundle_release",
            self._failure_route("process_bundle_release_decision"),
            {"process_bundle_release_decision": "process_bundle_release_decision", "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "process_bundle_release_decision",
            self.route_after_bundle_release,
            {
                "complete_workflow": "complete_workflow",
                "render_escalation_report": "render_escalation_report",
                "end": END,
                "failure": "handle_workflow_failure",
            },
        )
        graph.add_conditional_edges(
            "complete_workflow",
            self._failure_route("end"),
            {"end": END, "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "reject_policy_plan",
            self._failure_route("end"),
            {"end": END, "failure": "handle_workflow_failure"},
        )
        graph.add_conditional_edges(
            "finish_plan_decision",
            self._failure_route("end"),
            {"end": END, "failure": "handle_workflow_failure"},
        )
        graph.add_edge("handle_workflow_failure", END)
        return graph.compile(checkpointer=checkpointer)

    def _guard(self, node_name: str, function: Callable) -> Callable:
        async def guarded(state: WorkflowGraphState) -> dict[str, object]:
            try:
                return await function(state)
            except GraphInterrupt:
                raise
            except Exception as error:
                logger.error(
                    "Workflow node failed: %s for workflow %s (%s)",
                    node_name,
                    state.get("workflow_id", "unknown"),
                    type(error).__name__,
                )
                if isinstance(error, AIProviderError):
                    return {
                        "status": WorkflowStatus.FAILED.value,
                        "current_stage": WorkflowStatus.FAILED.value,
                        "error_code": "ai_provider_error",
                        "error_message": "The analysis provider could not complete the request.",
                        "updated_at": _now(),
                    }
                return self._failure_update(node_name)

        return guarded

    def _failure_update(self, node_name: str) -> dict[str, object]:
        messages = {
            "initialize_workflow": "Workflow could not be initialized.",
            "validate_context": "Issue or scenario validation failed.",
            "generate_evidence_plan": "Evidence plan could not be generated.",
            "validate_evidence_plan": "Evidence plan could not be validated.",
            "request_plan_approval": "Plan approval request could not be created.",
            "await_plan_approval": "Plan approval decision is invalid.",
            "process_plan_decision": "Plan approval decision could not be processed.",
            "execute_approved_collectors": "Approved collection could not be completed.",
            "complete_collection_stage": "Collection stage could not be completed.",
            "sanitize_evidence": "Collected evidence could not be sanitized safely.",
            "verify_sanitized_evidence": "Sanitized evidence failed local verification.",
            "classify_residual_risk": "Residual risk could not be classified safely.",
            "prepare_evidence_review": "Evidence review could not be prepared.",
            "await_evidence_approval": "Evidence review could not be paused safely.",
            "process_evidence_release_decision": "Evidence-release decision could not be validated.",
            "build_external_analysis_payload": "Approved evidence could not be prepared safely.",
            "analyze_approved_evidence": "Approved evidence could not be analyzed.",
            "validate_analysis_result": "Analysis output failed local validation.",
            "evaluate_evidence_sufficiency": "Evidence sufficiency could not be evaluated.",
            "prepare_supplemental_plan": "Supplemental collection could not be prepared.",
            "complete_analysis_stage": "Analysis stage could not be completed.",
            "render_escalation_report": "Escalation report could not be rendered safely.",
            "assemble_candidate_bundle": "Candidate bundle contents could not be assembled safely.",
            "generate_manifest": "Candidate bundle manifest could not be generated.",
            "create_candidate_zip": "Candidate ZIP could not be created.",
            "verify_candidate_bundle": "Candidate ZIP verification failed.",
            "request_bundle_release": "Bundle-release approval could not be requested.",
            "await_bundle_release": "Bundle-release approval could not be paused safely.",
            "process_bundle_release_decision": "Bundle-release decision could not be processed.",
            "complete_workflow": "Workflow could not be completed.",
            "reject_policy_plan": "Local policy could not finalize the proposed plan.",
            "finish_plan_decision": "The plan decision could not be finalized.",
            "handle_workflow_failure": "The workflow could not be completed.",
        }
        return {
            "status": WorkflowStatus.FAILED.value,
            "current_stage": WorkflowStatus.FAILED.value,
            "error_code": f"{node_name}_failed",
            "error_message": messages[node_name],
            "updated_at": _now(),
        }

    @staticmethod
    def _failure_route(next_node: str) -> Callable[[WorkflowGraphState], str]:
        def route(state: WorkflowGraphState) -> str:
            return "failure" if state.get("error_code") else next_node

        return route

    async def initialize_workflow(self, state: WorkflowGraphState) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        issue = IssueRequest.model_validate(state["issue"])
        existing = self.workflows.get(workflow_id)
        if existing is None:
            self.workflows.create(
                issue,
                workflow_id=workflow_id,
                issue_id=UUID(state["issue_id"]),
            )
        elif existing.issue_id != state["issue_id"]:
            raise ValueError("Workflow identity does not match persisted metadata.")
        return {"status": WorkflowStatus.RECEIVED.value, "current_stage": "initialize_workflow", "updated_at": _now()}

    async def validate_context(self, state: WorkflowGraphState) -> dict[str, object]:
        issue = IssueRequest.model_validate(state["issue"])
        if not issue.scenario_id:
            raise ValueError("Issue must identify a controlled scenario.")
        scenario = self.scenario_loader.load_scenario(issue.scenario_id)
        if scenario.scenario_id != state.get("scenario_id"):
            raise ValueError("Scenario does not match the submitted request.")
        workflow_id = UUID(state["workflow_id"])
        self.workflows.update_status(workflow_id, WorkflowStatus.PLANNING)
        return {
            "status": WorkflowStatus.PLANNING.value,
            "current_stage": "validate_context",
            "updated_at": _now(),
        }

    async def generate_evidence_plan(self, state: WorkflowGraphState) -> dict[str, object]:
        issue = IssueRequest.model_validate(state["issue"])
        scenario = self.scenario_loader.load_scenario(issue.scenario_id or "")
        plan = await self.planner.create_plan(issue, scenario)
        if plan.status != PlanStatus.PROPOSED or any(
            step.status != EvidenceStepStatus.PROPOSED for step in plan.steps
        ):
            raise ValueError("Planning service returned a non-proposed plan.")
        self.workflows.save_plan(UUID(state["workflow_id"]), plan)
        return {
            "evidence_plan": plan.model_dump(mode="json"),
            "status": WorkflowStatus.PLAN_VALIDATION.value,
            "current_stage": "generate_evidence_plan",
            "updated_at": _now(),
        }

    async def validate_evidence_plan(self, state: WorkflowGraphState) -> dict[str, object]:
        issue = IssueRequest.model_validate(state["issue"])
        plan = EvidencePlan.model_validate(state["evidence_plan"])
        scenario = self.scenario_loader.load_scenario(issue.scenario_id or "")
        result = self.policy_validator.validate(plan, scenario)
        workflow_id = UUID(state["workflow_id"])
        self.workflows.update_status(workflow_id, WorkflowStatus.PLAN_VALIDATION)
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"plan-validated:{plan.plan_id}",
                WorkflowEventType.PLAN_VALIDATED,
                "Evidence plan was checked by local policy.",
            ),
        )
        return {
            "policy_result": result.model_dump(mode="json"),
            "warnings": _merge_warnings(state.get("warnings", []), result.warnings),
            "status": WorkflowStatus.PLAN_VALIDATION.value,
            "current_stage": "validate_evidence_plan",
            "updated_at": _now(),
        }

    @staticmethod
    def route_after_policy(state: WorkflowGraphState) -> str:
        if state.get("error_code"):
            return "failure"
        result = PlanPolicyResult.model_validate(state["policy_result"])
        if result.status == PolicyDecisionStatus.BLOCKED:
            return "reject_policy_plan"
        return (
            "request_supplemental_approval"
            if state.get("supplemental_pending")
            else "request_plan_approval"
        )

    async def request_plan_approval(self, state: WorkflowGraphState) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        plan = EvidencePlan.model_validate(state["evidence_plan"])
        policy = PlanPolicyResult.model_validate(state["policy_result"])
        supplemental = bool(state.get("supplemental_pending"))
        request = ApprovalRequest(
            approval_id=uuid5(
                workflow_id,
                (
                    f"supplemental-plan-approval:{plan.plan_id}"
                    if supplemental
                    else f"plan-approval:{plan.plan_id}"
                ),
            ),
            workflow_id=workflow_id,
            approval_type=(
                ApprovalType.SUPPLEMENTAL_COLLECTION
                if supplemental
                else ApprovalType.COLLECTION_PLAN
            ),
            title=(
                "Review supplemental evidence collection"
                if supplemental
                else "Review proposed evidence collection"
            ),
            explanation="Review the locally validated collection steps and approve or reject them.",
        )
        record = self.approvals.get_or_create_pending(request)
        if record.id != str(request.approval_id):
            raise ValueError("A different plan already has a pending approval.")
        active_request = ApprovalRequest(
            approval_id=UUID(record.id),
            workflow_id=workflow_id,
            approval_type=ApprovalType(record.approval_type),
            status=ApprovalStatus(record.status),
            title=record.title,
            explanation=record.explanation,
            requested_at=record.requested_at,
            expires_at=record.expires_at,
        )
        policy_by_step = {item.step_id: item for item in policy.step_decisions}
        review_steps = []
        for step in plan.steps:
            decision = policy_by_step.get(step.step_id)
            if decision is None:
                raise ValueError("Policy result does not cover every plan step.")
            parameters = (
                decision.modified_parameters
                if decision.modified_parameters is not None
                else step.parameters
            )
            review_steps.append(
                {
                    "step_id": str(step.step_id),
                    "collector": step.collector.value,
                    "reason": step.reason,
                    "parameters": parameters,
                    "required": step.required,
                    "policy_status": decision.status.value,
                    "policy_reason": decision.safe_reason,
                }
            )
        payload = {
            "workflow_id": str(workflow_id),
            "approval_id": str(active_request.approval_id),
            "plan_id": str(plan.plan_id),
            "steps": review_steps,
            "policy_decisions": [
                item.model_dump(mode="json") for item in policy.step_decisions
            ],
            "warnings": _merge_warnings(
                plan.warnings,
                policy.warnings,
                state.get("warnings", []),
            ),
        }
        self.workflows.update_status(
            workflow_id,
            (
                WorkflowStatus.AWAITING_SUPPLEMENTAL_APPROVAL
                if supplemental
                else WorkflowStatus.AWAITING_PLAN_APPROVAL
            ),
            current_stage=(
                "request_supplemental_approval"
                if supplemental
                else "request_plan_approval"
            ),
        )
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"approval-requested:{active_request.approval_id}",
                WorkflowEventType.APPROVAL_REQUESTED,
                (
                    "A human supplemental-collection decision is required."
                    if supplemental
                    else "A human plan-approval decision is required."
                ),
            ),
        )
        return {
            "active_approval_request": active_request.model_dump(mode="json"),
            "approval_payload": payload,
            "status": (
                WorkflowStatus.AWAITING_SUPPLEMENTAL_APPROVAL.value
                if supplemental
                else WorkflowStatus.AWAITING_PLAN_APPROVAL.value
            ),
            "current_stage": (
                "request_supplemental_approval"
                if supplemental
                else "request_plan_approval"
            ),
            "updated_at": _now(),
        }

    async def await_plan_approval(self, state: WorkflowGraphState) -> dict[str, object]:
        received = interrupt(state["approval_payload"])
        decision = PlanApprovalDecision.model_validate(received)
        if decision.comment:
            validate_secret_free_text(decision.comment)
            if _ABSOLUTE_PATH_PATTERN.search(decision.comment):
                raise ValueError("Approval comment contains an absolute path.")
        return {
            "plan_approval": decision.model_dump(mode="json"),
            "current_stage": "process_plan_decision",
            "updated_at": _now(),
        }

    async def process_plan_decision(self, state: WorkflowGraphState) -> dict[str, object]:
        decision = PlanApprovalDecision.model_validate(state["plan_approval"])
        workflow_id = UUID(state["workflow_id"])
        active = ApprovalRequest.model_validate(state["active_approval_request"])
        plan = EvidencePlan.model_validate(state["evidence_plan"])
        policy = PlanPolicyResult.model_validate(state["policy_result"])
        if (
            decision.workflow_id != workflow_id
            or decision.approval_id != active.approval_id
            or active.workflow_id != workflow_id
            or active.status != ApprovalStatus.PENDING
            or policy.plan_id != plan.plan_id
        ):
            raise ValueError("Approval decision does not match the active request.")

        policy_by_step = {item.step_id: item for item in policy.step_decisions}
        eligible = {
            step.step_id
            for step in plan.steps
            if policy_by_step.get(step.step_id)
            and policy_by_step[step.step_id].status != PolicyDecisionStatus.BLOCKED
        }
        approved = set(decision.approved_step_ids)
        rejected = set(decision.rejected_step_ids)
        known = {step.step_id for step in plan.steps}
        if not approved.issubset(eligible) or not rejected.issubset(known):
            raise ValueError("Approval decision contains an ineligible step.")
        if decision.status == ApprovalStatus.APPROVED and (
            approved != eligible or rejected
        ):
            raise ValueError("Full approval must explicitly approve every eligible step.")
        if decision.status == ApprovalStatus.PARTIALLY_APPROVED and (
            approved | rejected != eligible
        ):
            raise ValueError("Partial approval must decide every eligible step.")
        if decision.status in {
            ApprovalStatus.REJECTED,
            ApprovalStatus.CANCELLED,
            ApprovalStatus.REVISION_REQUESTED,
        } and approved:
            raise ValueError("This decision cannot approve collection steps.")
        if decision.status == ApprovalStatus.REVISION_REQUESTED and (
            decision.rejected_step_ids
        ):
            raise ValueError("Revision requests cannot decide individual steps.")

        stored = self.approvals.get(decision.approval_id)
        if stored is None:
            raise LookupError("Approval request was not found.")
        decision_json = decision.model_dump(mode="json")
        if stored.status == ApprovalStatus.PENDING.value:
            self.approvals.finalize(decision)
        elif stored.decision != decision_json:
            raise ValueError("Approval request was already finalized differently.")

        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"approval-received:{decision.approval_id}",
                WorkflowEventType.APPROVAL_RECEIVED,
                f"Plan approval was finalized as {decision.status.value}.",
            ),
        )
        finalized_request = active.model_copy(update={"status": decision.status})
        if decision.status == ApprovalStatus.REVISION_REQUESTED:
            if state.get("revision_count", 0) >= 1:
                return {
                    "status": WorkflowStatus.REJECTED.value,
                    "current_stage": "revision_limit_reached",
                    "warnings": _merge_warnings(
                        state.get("warnings", []),
                        ["The plan revision limit has been reached."],
                    ),
                    "updated_at": _now(),
                }
            self.workflows.update_status(workflow_id, WorkflowStatus.PLANNING)
            return {
                "status": WorkflowStatus.PLANNING.value,
                "current_stage": "revision_requested",
                "revision_count": state.get("revision_count", 0) + 1,
                "evidence_plan": None,
                "policy_result": None,
                "active_approval_request": None,
                "approval_payload": None,
                "updated_at": _now(),
            }
        return {
            "active_approval_request": finalized_request.model_dump(mode="json"),
            "plan_approval": decision_json,
            "updated_at": _now(),
        }

    @staticmethod
    def route_after_decision(state: WorkflowGraphState) -> str:
        if state.get("error_code"):
            return "finish_plan_decision"
        decision = PlanApprovalDecision.model_validate(state["plan_approval"])
        if decision.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.PARTIALLY_APPROVED,
        }:
            return "execute_approved_collectors"
        if decision.status == ApprovalStatus.REVISION_REQUESTED and state.get("evidence_plan") is None:
            return "generate_evidence_plan"
        return "finish_plan_decision"

    async def finish_plan_decision(self, state: WorkflowGraphState) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        decision = PlanApprovalDecision.model_validate(state["plan_approval"])
        if state.get("error_code"):
            self.workflows.update_status(
                workflow_id,
                WorkflowStatus.REJECTED,
                current_stage=str(state.get("current_stage") or "revision_limit_reached"),
            )
            return {"updated_at": _now()}
        status = (
            WorkflowStatus.CANCELLED
            if decision.status == ApprovalStatus.CANCELLED
            else WorkflowStatus.REJECTED
        )
        self.workflows.update_status(
            workflow_id,
            status,
            current_stage="plan_approval_finished",
        )
        return {
            "status": status.value,
            "current_stage": "plan_approval_finished",
            "updated_at": _now(),
        }

    async def reject_policy_plan(self, state: WorkflowGraphState) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        self.workflows.update_status(
            workflow_id,
            WorkflowStatus.REJECTED,
            current_stage="policy_rejected",
        )
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"policy-blocked:{state['workflow_id']}",
                WorkflowEventType.STATUS_CHANGED,
                "Local policy blocked the proposed collection plan.",
            ),
        )
        policy = PlanPolicyResult.model_validate(state["policy_result"])
        return {
            "status": WorkflowStatus.REJECTED.value,
            "current_stage": "policy_rejected",
            "warnings": _merge_warnings(state.get("warnings", []), policy.warnings),
            "updated_at": _now(),
        }

    async def execute_approved_collectors(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        issue = IssueRequest.model_validate(state["issue"])
        scenario = self.scenario_loader.load_scenario(issue.scenario_id or "")
        plan = EvidencePlan.model_validate(state["evidence_plan"])
        policy = PlanPolicyResult.model_validate(state["policy_result"])
        decision = PlanApprovalDecision.model_validate(state["plan_approval"])
        allowed_step_ids = set(decision.approved_step_ids)
        policy_by_step = {item.step_id: item for item in policy.step_decisions}
        self.workflows.update_status(workflow_id, WorkflowStatus.COLLECTING)

        graph_results: list[dict[str, object]] = list(
            state.get("collector_results", [])
        )
        partial = False
        for step in plan.steps:
            policy_decision = policy_by_step.get(step.step_id)
            permitted = (
                step.step_id in allowed_step_ids
                and policy_decision is not None
                and policy_decision.status != PolicyDecisionStatus.BLOCKED
            )
            parameters = (
                policy_decision.modified_parameters
                if policy_decision is not None
                and policy_decision.modified_parameters is not None
                else step.parameters
            )
            execution_step = step.model_copy(
                update={
                    "parameters": parameters,
                    "status": (
                        EvidenceStepStatus.HUMAN_APPROVED
                        if permitted
                        else EvidenceStepStatus.REJECTED
                    ),
                }
            )
            result = await self._collect_one(
                workflow_id,
                execution_step,
                scenario,
                permitted=permitted,
            )
            result_json = result.model_copy(update={"data": {}}).model_dump(mode="json")
            graph_results = [
                item
                for item in graph_results
                if item.get("step_id") != str(result.step_id)
            ]
            graph_results.append(result_json)
            if result.status != CollectionStatus.SUCCESS:
                partial = True
        return {
            "collector_results": graph_results,
            "status": WorkflowStatus.COLLECTING.value,
            "current_stage": "execute_approved_collectors",
            "updated_at": _now(),
            "warnings": list(state.get("warnings", [])),
            "_collection_partial": partial,
            "supplemental_pending": False,
        }

    async def _collect_one(
        self,
        workflow_id: UUID,
        step: EvidenceStep,
        scenario: ScenarioDefinition,
        *,
        permitted: bool,
    ) -> CollectorResult:
        run, reserved = self.workflows.reserve_collector_run(
            workflow_id,
            step.step_id,
            step.collector.value,
        )
        run_id = UUID(run.id)
        if not reserved:
            if run.status == "running":
                result = self._result(
                    workflow_id,
                    step,
                    run_id,
                    CollectionStatus.FAILED,
                    error=SafeCollectorError(
                        code="collector_interrupted",
                        message="The collector did not finish before workflow recovery.",
                    ),
                )
                self.workflows.complete_collector_run(result)
                return result
            return CollectorResult(
                result_id=run_id,
                workflow_id=workflow_id,
                step_id=step.step_id,
                collector=step.collector,
                status=CollectionStatus(run.status),
                collected_at=run.collected_at,
                data={},
                warnings=run.warnings,
                error=run.error,
                duration_ms=run.duration_ms,
            )

        if not permitted:
            result = self._result(
                workflow_id,
                step,
                run_id,
                CollectionStatus.SKIPPED,
                warnings=["The step was not approved by the reviewer."],
            )
        else:
            try:
                collected = self.registry.execute(
                    step,
                    scenario,
                    workflow_id=workflow_id,
                )
                staged = (
                    self.sanitizer.stage_collector_data(
                        workflow_id=workflow_id,
                        step_id=step.step_id,
                        data=collected.data,
                    )
                    if not collected.artifacts
                    else None
                )
                if staged is not None:
                    collected = collected.model_copy(
                        update={"artifacts": [*collected.artifacts, staged]}
                    )
                result = collected.model_copy(update={"result_id": run_id})
            except Exception:
                logger.error("Approved collector failed: %s", step.collector.value)
                result = self._result(
                    workflow_id,
                    step,
                    run_id,
                    CollectionStatus.FAILED,
                    error=SafeCollectorError(
                        code="collector_failed",
                        message="The approved collector could not complete.",
                    ),
                )
        self.workflows.complete_collector_run(result)
        return result

    @staticmethod
    def _result(
        workflow_id: UUID,
        step: EvidenceStep,
        result_id: UUID,
        status: CollectionStatus,
        *,
        warnings: list[str] | None = None,
        error: SafeCollectorError | None = None,
    ) -> CollectorResult:
        return CollectorResult(
            result_id=result_id,
            workflow_id=workflow_id,
            step_id=step.step_id,
            collector=step.collector,
            status=status,
            data={},
            warnings=warnings or [],
            error=error,
            duration_ms=0,
        )

    async def complete_collection_stage(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        results = state.get("collector_results", [])
        partial = bool(state.get("_collection_partial")) or any(
            item.get("status") != CollectionStatus.SUCCESS.value for item in results
        )
        workflow = self.workflows.finish_collection(
            UUID(state["workflow_id"]),
            partial=partial,
            cycle=int(state.get("supplemental_cycle_count", 0)),
        )
        return {
            "status": workflow.status,
            "current_stage": "collection_complete",
            "updated_at": _now(),
        }

    async def sanitize_evidence(self, state: WorkflowGraphState) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        self.workflows.update_status(
            workflow_id,
            WorkflowStatus.SANITIZING,
            current_stage="sanitize_evidence",
        )
        report, excerpts, artifacts = self.sanitizer.sanitize_workflow(workflow_id)
        for artifact in artifacts:
            self.artifacts.register_if_missing(artifact)
        previous_report = (
            SanitizationReport.model_validate(state["sanitization_report"])
            if state.get("sanitization_report")
            else None
        )
        if previous_report is not None:
            report = _merge_sanitization_reports(
                previous_report,
                report,
                UUID(state["workflow_id"]),
                int(state.get("supplemental_cycle_count", 0)),
            )
        existing_excerpts = [
            SanitizedEvidenceExcerpt.model_validate(item)
            for item in state.get("sanitized_excerpts", [])
        ]
        excerpts = list(
            {
                excerpt.excerpt_id: excerpt
                for excerpt in [*existing_excerpts, *excerpts]
            }.values()
        )
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"sanitization-completed:{report.report_id}",
                WorkflowEventType.SANITIZATION_COMPLETED,
                "Local evidence sanitization completed.",
            ),
        )
        return {
            "sanitization_report": report.model_dump(mode="json"),
            "sanitized_excerpts": [
                excerpt.model_dump(mode="json") for excerpt in excerpts
            ],
            "status": WorkflowStatus.SANITIZING.value,
            "current_stage": "sanitize_evidence",
            "updated_at": _now(),
        }

    async def verify_sanitized_evidence(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        report = SanitizationReport.model_validate(state["sanitization_report"])
        approved = list(report.approved_artifact_ids)
        blocked = list(report.blocked_artifact_ids)
        valid_ids: set[UUID] = set()
        for artifact_id in approved:
            record = self.artifacts.get(artifact_id)
            if record is None:
                raise LookupError("Sanitized artifact metadata was not persisted.")
            metadata = _artifact_metadata(record)
            if self.sanitizer.verify_artifact(metadata):
                valid_ids.add(artifact_id)
            else:
                self.artifacts.update_sensitivity(
                    artifact_id,
                    SensitivityStatus.BLOCKED,
                    sanitized=False,
                )
                blocked.append(artifact_id)

        excerpts = [
            SanitizedEvidenceExcerpt.model_validate(item)
            for item in state.get("sanitized_excerpts", [])
        ]
        excerpts = [
            excerpt for excerpt in excerpts if excerpt.artifact_id in valid_ids
        ]
        approved = [artifact_id for artifact_id in approved if artifact_id in valid_ids]
        status = report.status
        warnings = list(report.warnings)
        if blocked and approved:
            status = SanitizationStatus.PARTIAL
        elif blocked:
            status = SanitizationStatus.BLOCKED
        if len(blocked) > len(report.blocked_artifact_ids):
            warnings.append("An artifact failed independent verification and was blocked.")
        updated = report.model_copy(
            update={
                "status": status,
                "approved_artifact_ids": approved,
                "blocked_artifact_ids": list(dict.fromkeys(blocked)),
                "warnings": list(dict.fromkeys(warnings))[:20],
            }
        )
        return {
            "sanitization_report": updated.model_dump(mode="json"),
            "sanitized_excerpts": [
                excerpt.model_dump(mode="json") for excerpt in excerpts
            ],
            "current_stage": "verify_sanitized_evidence",
            "updated_at": _now(),
        }

    async def classify_residual_risk(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        report = SanitizationReport.model_validate(state["sanitization_report"])
        self.workflows.update_status(
            workflow_id,
            WorkflowStatus.RESIDUAL_RISK_REVIEW,
            current_stage="classify_residual_risk",
        )
        if report.residual_risk_flags:
            self.workflows.add_event(
                workflow_id,
                _event(
                    workflow_id,
                    f"residual-risk:{report.report_id}",
                    WorkflowEventType.RESIDUAL_RISK_DETECTED,
                    "Residual contextual risk was identified for human review.",
                ),
            )
        return {
            "current_stage": "classify_residual_risk",
            "updated_at": _now(),
        }

    async def prepare_evidence_review(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        report = SanitizationReport.model_validate(state["sanitization_report"])
        request = ApprovalRequest(
            approval_id=uuid5(
                workflow_id,
                (
                    f"evidence-approval:{report.report_id}:"
                    f"cycle:{state.get('supplemental_cycle_count', 0)}:"
                    f"revision:{state.get('evidence_review_revision', 0)}"
                ),
            ),
            workflow_id=workflow_id,
            approval_type=ApprovalType.EVIDENCE_RELEASE,
            title="Review sanitized evidence",
            explanation="Review sanitized artifacts and residual-risk findings before any external analysis.",
        )
        record = self.approvals.get_or_create_pending(request)
        if record.id != str(request.approval_id):
            raise ValueError("A different evidence approval is already pending.")
        active = ApprovalRequest(
            approval_id=UUID(record.id),
            workflow_id=workflow_id,
            approval_type=ApprovalType(record.approval_type),
            status=ApprovalStatus(record.status),
            title=record.title,
            explanation=record.explanation,
            requested_at=record.requested_at,
            expires_at=record.expires_at,
        )
        metadata = []
        for artifact_id in report.approved_artifact_ids + report.blocked_artifact_ids:
            artifact = self.artifacts.get(artifact_id)
            if artifact is None:
                raise LookupError("Evidence artifact metadata is unavailable.")
            metadata.append(
                {
                    "artifact_id": artifact.id,
                    "name": artifact.name,
                    "relative_path": artifact.relative_path,
                    "sanitized": artifact.sanitized,
                    "sensitivity_status": artifact.sensitivity_status,
                }
            )
        payload = {
            "workflow_id": str(workflow_id),
            "approval_id": str(active.approval_id),
            "report_id": str(report.report_id),
            "artifacts": metadata,
            "excerpts": state.get("sanitized_excerpts", []),
            "residual_risk_flags": [
                flag.model_dump(mode="json")
                for flag in report.residual_risk_flags
            ],
            "redactions_by_category": [
                item.model_dump(mode="json")
                for item in report.redactions_by_category
            ],
            "warnings": _merge_warnings(
                state.get("warnings", []),
                report.warnings,
            ),
        }
        self.workflows.update_status(
            workflow_id,
            WorkflowStatus.AWAITING_EVIDENCE_APPROVAL,
            current_stage="prepare_evidence_review",
        )
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"evidence-approval-requested:{active.approval_id}",
                WorkflowEventType.APPROVAL_REQUESTED,
                "A human evidence-review decision is required.",
            ),
        )
        return {
            "active_approval_request": active.model_dump(mode="json"),
            "evidence_approval_payload": payload,
            "status": WorkflowStatus.AWAITING_EVIDENCE_APPROVAL.value,
            "current_stage": "prepare_evidence_review",
            "updated_at": _now(),
        }

    async def await_evidence_approval(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        received = interrupt(state["evidence_approval_payload"])
        decision = EvidenceReleaseDecision.model_validate(received)
        for item in decision.item_decisions:
            if item.reviewer_comment:
                validate_secret_free_text(item.reviewer_comment)
                if _ABSOLUTE_PATH_PATTERN.search(item.reviewer_comment):
                    raise ValueError("Evidence-review comment contains an absolute path.")
        return {
            "evidence_release_decision": decision.model_dump(mode="json"),
            "current_stage": "await_evidence_approval",
            "updated_at": _now(),
        }

    async def process_evidence_release_decision(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        decision = EvidenceReleaseDecision.model_validate(
            state["evidence_release_decision"]
        )
        active = ApprovalRequest.model_validate(state["active_approval_request"])
        requires_evidence_release = decision.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.PARTIALLY_APPROVED,
        }
        if (
            decision.workflow_id != workflow_id
            or decision.approval_id != active.approval_id
            or active.workflow_id != workflow_id
            or active.approval_type != ApprovalType.EVIDENCE_RELEASE
            or active.status != ApprovalStatus.PENDING
            or (requires_evidence_release and not decision.reviewer_acknowledgement)
        ):
            raise ValueError("Evidence-release decision does not match the active request.")
        if requires_evidence_release:
            self._validate_release_selection(state, decision)
        stored = self.approvals.get(decision.approval_id)
        if stored is None or stored.workflow_id != str(workflow_id):
            raise LookupError("Evidence-release approval was not found.")
        decision_json = decision.model_dump(mode="json")
        if stored.status == ApprovalStatus.PENDING.value:
            self.approvals.finalize(decision)
        elif stored.decision != decision_json:
            raise ValueError("Evidence-release approval was finalized differently.")
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"evidence-approval-received:{decision.approval_id}",
                WorkflowEventType.APPROVAL_RECEIVED,
                f"Evidence review was finalized as {decision.status.value}.",
            ),
        )
        if decision.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.PARTIALLY_APPROVED,
        }:
            if not decision.allow_external_ai_analysis:
                raise ValueError("External analysis was not explicitly authorized.")
            return {
                "active_approval_request": active.model_copy(
                    update={"status": decision.status}
                ).model_dump(mode="json"),
                "current_stage": "build_external_analysis_payload",
                "updated_at": _now(),
            }
        if decision.status == ApprovalStatus.REVISION_REQUESTED:
            revision = int(state.get("evidence_review_revision", 0))
            if revision >= 1:
                self.workflows.update_status(
                    workflow_id,
                    WorkflowStatus.REJECTED,
                    current_stage="evidence_review_revision_limit",
                )
                return {
                    "active_approval_request": None,
                    "status": WorkflowStatus.REJECTED.value,
                    "current_stage": "evidence_review_revision_limit",
                    "warnings": _merge_warnings(
                        state.get("warnings", []),
                        ["Evidence-review revision limit was reached."],
                    ),
                    "updated_at": _now(),
                }
            return {
                "active_approval_request": None,
                "evidence_release_decision": None,
                "evidence_review_revision": revision + 1,
                "status": WorkflowStatus.RESIDUAL_RISK_REVIEW.value,
                "current_stage": "evidence_review_revision_requested",
                "updated_at": _now(),
            }
        status = (
            WorkflowStatus.CANCELLED
            if decision.status == ApprovalStatus.CANCELLED
            else WorkflowStatus.REJECTED
        )
        self.workflows.update_status(
            workflow_id,
            status,
            current_stage="evidence_release_finished",
        )
        return {
            "active_approval_request": None,
            "status": status.value,
            "current_stage": "evidence_release_finished",
            "updated_at": _now(),
        }

    def _validate_release_selection(
        self,
        state: WorkflowGraphState,
        decision: EvidenceReleaseDecision,
    ) -> None:
        workflow_id = UUID(state["workflow_id"])
        report = SanitizationReport.model_validate(state["sanitization_report"])
        excerpts = {
            item.excerpt_id: item
            for item in (
                SanitizedEvidenceExcerpt.model_validate(value)
                for value in state.get("sanitized_excerpts", [])
            )
        }
        approved_artifacts = set(report.approved_artifact_ids)
        included: set[UUID] = set()
        excluded: set[UUID] = set()
        for item in decision.item_decisions:
            artifact = self.artifacts.get(item.artifact_id)
            if artifact is None or artifact.workflow_id != str(workflow_id):
                raise ValueError("Evidence decision references an unknown artifact.")
            ids = set(item.approved_excerpt_ids) | set(item.excluded_excerpt_ids)
            if any(
                excerpt_id not in excerpts
                or excerpts[excerpt_id].artifact_id != item.artifact_id
                for excerpt_id in ids
            ):
                raise ValueError("Evidence decision references an unknown excerpt.")
            if item.decision == EvidenceItemDecisionType.INCLUDE:
                if item.artifact_id not in approved_artifacts or (
                    not artifact.sanitized
                    or artifact.sensitivity_status == SensitivityStatus.BLOCKED.value
                ):
                    raise ValueError("Evidence decision includes blocked evidence.")
                included.update(item.approved_excerpt_ids)
                excluded.update(item.excluded_excerpt_ids)
            else:
                if item.approved_excerpt_ids:
                    raise ValueError("Excluded or revised excerpts cannot be included.")
                excluded.update(item.excluded_excerpt_ids)
        if included & excluded:
            raise ValueError("An excluded excerpt cannot be included.")
        if decision.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.PARTIALLY_APPROVED,
        } and (
            not decision.allow_external_ai_analysis or not included
        ):
            raise ValueError("At least one safe approved excerpt is required.")
        if decision.status == ApprovalStatus.REVISION_REQUESTED and included:
            raise ValueError("Revision requests cannot release evidence.")

    @staticmethod
    def route_after_evidence_release(state: WorkflowGraphState) -> str:
        if state.get("error_code"):
            return "failure"
        decision = state.get("evidence_release_decision")
        if not isinstance(decision, dict):
            return "failure"
        status = decision.get("status")
        if status in {
            ApprovalStatus.APPROVED.value,
            ApprovalStatus.PARTIALLY_APPROVED.value,
        }:
            return "build_external_analysis_payload"
        if status == ApprovalStatus.REVISION_REQUESTED.value and state.get("status") != WorkflowStatus.REJECTED.value:
            return "prepare_evidence_review"
        if state.get("status") in {
            WorkflowStatus.REJECTED.value,
            WorkflowStatus.CANCELLED.value,
        }:
            return "end"
        return "complete_analysis_stage"

    async def build_external_analysis_payload_node(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        decision = EvidenceReleaseDecision.model_validate(
            state["evidence_release_decision"]
        )
        report = SanitizationReport.model_validate(state["sanitization_report"])
        approved_ids = set(report.approved_artifact_ids)
        excerpt_values = [
            SanitizedEvidenceExcerpt.model_validate(item)
            for item in state.get("sanitized_excerpts", [])
        ]
        excerpt_map = {item.excerpt_id: item for item in excerpt_values}
        artifact_ids = {
            item.artifact_id
            for item in decision.item_decisions
        }
        artifact_records = []
        source_collectors: dict[str, str | None] = {}
        for artifact_id in artifact_ids:
            record = self.artifacts.get(artifact_id)
            if record is None or record.workflow_id != str(workflow_id):
                raise ValueError("Evidence decision references an unknown artifact.")
            if any(
                item.decision == EvidenceItemDecisionType.INCLUDE
                and item.artifact_id == artifact_id
                for item in decision.item_decisions
            ) and (
                artifact_id not in approved_ids
                or not record.sanitized
                or record.sensitivity_status == SensitivityStatus.BLOCKED.value
            ):
                raise ValueError("Evidence decision includes blocked evidence.")
            artifact_records.append(record)
            source_collectors[str(artifact_id)] = self.artifacts.source_collector_name(record)

        issue = IssueRequest.model_validate(state["issue"])
        summary = f"{issue.title}. {issue.description}"
        payload, warnings = build_external_analysis_payload(
            workflow_id=workflow_id,
            issue_summary=summary,
            decision=decision,
            excerpts=list(excerpt_map.values()),
            artifacts=artifact_records,
            source_collectors=source_collectors,
            missing_evidence=[
                MissingEvidence.model_validate(item)
                for item in state.get("missing_evidence", [])
            ],
            warnings=_merge_warnings(
                state.get("warnings", []),
                report.warnings,
            ),
            maximum_bytes=self.analyzer.maximum_payload_bytes,
        )
        return {
            "external_analysis_payload": payload,
            "warnings": _merge_warnings(state.get("warnings", []), warnings),
            "status": WorkflowStatus.ANALYZING.value,
            "current_stage": "build_external_analysis_payload",
            "updated_at": _now(),
        }

    async def analyze_approved_evidence(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        approval = ApprovalRequest.model_validate(state["active_approval_request"])
        cycle = int(state.get("supplemental_cycle_count", 0))
        analysis_id = uuid5(
            workflow_id,
            f"analysis:{cycle}:{approval.approval_id}",
        )
        record, reserved = self.analyses.reserve(analysis_id, workflow_id)
        if record.status == "completed" and record.result is not None:
            result = AnalysisResult.model_validate(record.result)
            self._validate_analysis_for_payload(
                result,
                workflow_id,
                state["external_analysis_payload"],
            )
            return {
                "analysis_result": result.model_dump(mode="json"),
                "current_stage": "validate_analysis_result",
                "updated_at": _now(),
            }
        if not reserved:
            raise ValueError("An analysis attempt was already reserved.")
        result = await self.analyzer.analyze(
            workflow_id,
            state["external_analysis_payload"],
        )
        result = result.model_copy(update={"analysis_id": analysis_id})
        self._validate_analysis_for_payload(
            result,
            workflow_id,
            state["external_analysis_payload"],
        )
        self.analyses.complete(result)
        return {
            "analysis_result": result.model_dump(mode="json"),
            "current_stage": "validate_analysis_result",
            "updated_at": _now(),
        }

    @staticmethod
    def _validate_analysis_for_payload(
        result: AnalysisResult,
        workflow_id: UUID,
        payload: dict[str, object],
    ) -> None:
        validate_analysis_result(
            result,
            workflow_id=workflow_id,
            approved_artifacts={
                UUID(item["artifact_id"])
                for item in payload["approved_excerpts"]
            },
            approved_excerpts={
                UUID(item["excerpt_id"]): UUID(item["artifact_id"])
                for item in payload["approved_excerpts"]
            },
        )

    async def validate_analysis_result_node(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        result = AnalysisResult.model_validate(state["analysis_result"])
        payload = state["external_analysis_payload"]
        validate_analysis_result(
            result,
            workflow_id=UUID(state["workflow_id"]),
            approved_artifacts={
                UUID(item["artifact_id"]) for item in payload["approved_excerpts"]
            },
            approved_excerpts={
                UUID(item["excerpt_id"]): UUID(item["artifact_id"])
                for item in payload["approved_excerpts"]
            },
        )
        return {
            "current_stage": "evaluate_evidence_sufficiency",
            "updated_at": _now(),
        }

    async def evaluate_evidence_sufficiency(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        result = AnalysisResult.model_validate(state["analysis_result"])
        missing = [item.model_dump(mode="json") for item in result.missing_evidence]
        warnings = _merge_warnings(state.get("warnings", []), result.warnings)
        if (
            result.evidence_sufficiency == EvidenceSufficiency.INSUFFICIENT
            and result.supplemental_plan_required
            and int(state.get("supplemental_cycle_count", 0)) >= 1
        ):
            warnings = _merge_warnings(
                warnings,
                ["Supplemental collection limit reached; findings retain visible limitations."],
            )
        return {
            "missing_evidence": missing,
            "warnings": warnings,
            "status": WorkflowStatus.EVALUATING_EVIDENCE.value,
            "current_stage": "evaluate_evidence_sufficiency",
            "updated_at": _now(),
        }

    @staticmethod
    def route_after_sufficiency(state: WorkflowGraphState) -> str:
        if state.get("error_code"):
            return "failure"
        result = AnalysisResult.model_validate(state["analysis_result"])
        if (
            result.evidence_sufficiency == EvidenceSufficiency.INSUFFICIENT
            and result.supplemental_plan_required
            and int(state.get("supplemental_cycle_count", 0)) < 1
        ):
            return "prepare_supplemental_plan"
        return "complete_analysis_stage"

    async def prepare_supplemental_plan(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        cycle = self.analyses.reserve_supplemental_cycle(workflow_id)
        if cycle != 1:
            raise ValueError("Supplemental collection limit reached.")
        missing = [
            MissingEvidence.model_validate(item)
            for item in state.get("missing_evidence", [])
        ]
        if not missing:
            raise ValueError("Supplemental planning requires missing-evidence metadata.")
        issue = IssueRequest.model_validate(state["issue"])
        gap_text = "; ".join(
            f"{item.collector.value}: {item.reason} (impact: {item.impact})"
            for item in missing
        )
        revised_issue = issue.model_copy(
            update={
                "description": (
                    issue.description[: max(0, 4800 - len(gap_text))]
                    + "\nEvidence gaps for one bounded supplemental plan: "
                    + gap_text
                )[:5000]
            }
        )
        scenario = self.scenario_loader.load_scenario(issue.scenario_id or "")
        proposal = await self.planner.create_plan(revised_issue, scenario)
        stable_steps = [
            step.model_copy(
                update={
                    "step_id": uuid5(
                        workflow_id,
                        "supplemental-step:"
                        + step.collector.value
                        + ":"
                        + json.dumps(step.parameters, sort_keys=True),
                    )
                }
            )
            for step in proposal.steps
        ]
        proposal = proposal.model_copy(
            update={
                "plan_id": uuid5(workflow_id, "supplemental-plan:1"),
                "steps": stable_steps,
                "issue_summary": "One supplemental plan for identified evidence gaps.",
            }
        )
        self.workflows.save_plan(
            workflow_id,
            proposal,
            preserve_existing_steps=True,
        )
        self.workflows.update_status(
            workflow_id,
            WorkflowStatus.PLANNING,
            current_stage="prepare_supplemental_plan",
        )
        return {
            "evidence_plan": proposal.model_dump(mode="json"),
            "supplemental_plan": proposal.model_dump(mode="json"),
            "supplemental_cycle_count": cycle,
            "supplemental_pending": True,
            "status": WorkflowStatus.PLANNING.value,
            "current_stage": "prepare_supplemental_plan",
            "updated_at": _now(),
        }

    async def complete_analysis_stage(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        status = WorkflowStatus(state.get("status", WorkflowStatus.ANALYZING.value))
        if status not in {WorkflowStatus.REJECTED, WorkflowStatus.CANCELLED}:
            status = WorkflowStatus.ANALYZING
            result = state.get("analysis_result")
            if result:
                analysis = AnalysisResult.model_validate(result)
                self.workflows.add_event(
                    workflow_id,
                    _event(
                        workflow_id,
                        f"analysis-completed:{analysis.analysis_id}",
                        WorkflowEventType.ANALYSIS_COMPLETED,
                        "Approved sanitized evidence analysis completed.",
                    ),
                )
            self.workflows.update_status(
                workflow_id,
                status,
                current_stage="analysis_complete",
            )
        return {
            "status": status.value,
            "current_stage": "analysis_complete",
            "updated_at": _now(),
        }

    def _candidate_root(self, workflow_id: UUID, revision: int) -> Path:
        relative = Path("workflows") / str(workflow_id) / f"candidate-{revision}"
        root = Path(self.settings.output_directory).resolve()
        unresolved = root / relative
        if any(
            path.is_symlink()
            for path in (
                root / "workflows",
                root / "workflows" / str(workflow_id),
                unresolved,
            )
        ):
            raise ValueError("Candidate bundle path cannot use symbolic links.")
        candidate = unresolved.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("Candidate bundle path is outside the output directory.")
        return candidate

    def _bundle_file_map(
        self,
        state: WorkflowGraphState,
    ) -> tuple[dict[str, ArtifactType], list[dict[str, object]]]:
        workflow_id = UUID(state["workflow_id"])
        analysis = AnalysisResult.model_validate(state["analysis_result"])
        sanitization = SanitizationReport.model_validate(state["sanitization_report"])
        release = EvidenceReleaseDecision.model_validate(
            state["evidence_release_decision"]
        )
        if analysis.workflow_id != workflow_id or sanitization.workflow_id != workflow_id:
            raise ValueError("Workflow report data does not match its owner.")
        if not release.allow_external_ai_analysis:
            raise ValueError("Validated analysis and approved sanitized evidence are required.")

        selected_excerpt_ids = {
            excerpt_id
            for item in release.item_decisions
            if item.decision == EvidenceItemDecisionType.INCLUDE
            for excerpt_id in item.approved_excerpt_ids
        }
        analysis_excerpt_ids = {
            reference.excerpt_id
            for finding in analysis.findings
            for reference in finding.evidence_references
            if reference.excerpt_id is not None
        }
        if not selected_excerpt_ids or not analysis_excerpt_ids.issubset(selected_excerpt_ids):
            raise ValueError("Analysis references evidence outside the release approval.")
        approved_artifact_ids = {
            item.artifact_id
            for item in release.item_decisions
            if item.decision == EvidenceItemDecisionType.INCLUDE
            and item.approved_excerpt_ids
        }
        report_approved = set(sanitization.approved_artifact_ids)
        blocked = set(sanitization.blocked_artifact_ids)
        if not approved_artifact_ids or approved_artifact_ids & blocked:
            raise ValueError("Blocked or unapproved artifacts cannot be packaged.")

        revision = int(state.get("bundle_revision_count", 0))
        root = self._candidate_root(workflow_id, revision)
        root.mkdir(parents=True, exist_ok=True)
        output_root = Path(self.settings.output_directory).resolve()
        file_types: dict[str, ArtifactType] = {
            "escalation_summary.html": ArtifactType.OTHER,
            "issue.json": ArtifactType.OTHER,
            "evidence_plan.json": ArtifactType.OTHER,
            "collection_results.json": ArtifactType.OTHER,
            "sanitization_report.json": ArtifactType.SANITIZATION_REPORT,
            "analysis_result.json": ArtifactType.ANALYSIS,
            "missing_evidence.json": ArtifactType.OTHER,
        }
        included_artifacts: list[dict[str, object]] = []
        for artifact_id in sorted(approved_artifact_ids, key=str):
            if artifact_id not in report_approved:
                raise ValueError("Approved artifact is absent from sanitization metadata.")
            record = self.artifacts.get(artifact_id)
            if (
                record is None
                or record.workflow_id != str(workflow_id)
                or not record.sanitized
                or record.sensitivity_status == SensitivityStatus.BLOCKED.value
            ):
                raise ValueError("Only approved sanitized artifacts can be packaged.")
            metadata = _artifact_metadata(record)
            if not self.sanitizer.verify_artifact(metadata):
                raise ValueError("A sanitized artifact failed local verification.")
            source = (output_root / metadata.relative_path).resolve(strict=True)
            if not source.is_relative_to(output_root) or not source.is_file():
                raise ValueError("Sanitized artifact path is unsafe.")
            suffix = PurePosixPath(metadata.relative_path).suffix.lower()
            destination = f"artifacts/{artifact_id}{suffix}"
            if destination in file_types:
                raise ValueError("Candidate bundle contains a duplicate artifact path.")
            target = root / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as input_stream, target.open("wb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            file_types[destination] = ArtifactType(record.artifact_type)
            included_artifacts.append(
                {
                    "relative_path": destination,
                    "artifact_type": record.artifact_type,
                    "artifact_id": str(artifact_id),
                }
            )
        return file_types, included_artifacts

    async def render_escalation_report(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        if not state.get("analysis_result"):
            raise ValueError("Analysis result is required before report rendering.")
        issue = IssueRequest.model_validate(state["issue"])
        plan = EvidencePlan.model_validate(state["evidence_plan"])
        results = [CollectorResult.model_validate(item) for item in state.get("collector_results", [])]
        sanitization = SanitizationReport.model_validate(state["sanitization_report"])
        analysis = AnalysisResult.model_validate(state["analysis_result"])
        release = EvidenceReleaseDecision.model_validate(state["evidence_release_decision"])
        excluded_ids = {
            excerpt_id
            for item in release.item_decisions
            for excerpt_id in item.excluded_excerpt_ids
        }
        included_ids = {
            excerpt_id
            for item in release.item_decisions
            if item.decision == EvidenceItemDecisionType.INCLUDE
            for excerpt_id in item.approved_excerpt_ids
        }
        included_artifact_ids = {
            item.artifact_id
            for item in release.item_decisions
            if item.decision == EvidenceItemDecisionType.INCLUDE
            and item.approved_excerpt_ids
        }
        excerpts = {
            item.excerpt_id: item
            for item in (
                SanitizedEvidenceExcerpt.model_validate(value)
                for value in state.get("sanitized_excerpts", [])
            )
        }
        excerpt_map = {
            excerpt_id: excerpts[excerpt_id]
            for excerpt_id in included_ids
            if excerpt_id not in excluded_ids and excerpt_id in excerpts
        }
        if set(excerpt_map) != included_ids:
            raise ValueError("Approved report excerpts are not available.")
        if any(item.artifact_id not in included_artifact_ids for item in excerpt_map.values()):
            raise ValueError("Report contains an unapproved artifact.")
        file_types, artifacts = self._bundle_file_map(state)
        warnings = _merge_warnings(
            state.get("warnings", []),
            sanitization.warnings,
            analysis.warnings,
        )
        content = render_escalation_summary(
            {
                "issue": issue.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "collection_results": [
                    {
                        "collector": item.collector.value,
                        "status": item.status.value,
                        "error": item.error.model_dump(mode="json") if item.error else None,
                        "warnings": item.warnings,
                    }
                    for item in results
                ],
                "sanitization": sanitization.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json"),
                "missing_evidence": [
                    item.model_dump(mode="json")
                    for item in analysis.missing_evidence
                ],
                "warnings": warnings,
                "artifacts": artifacts,
            }
        )
        root = self._candidate_root(
            workflow_id,
            int(state.get("bundle_revision_count", 0)),
        )
        root.mkdir(parents=True, exist_ok=True)
        (root / "escalation_summary.html").write_text(content, encoding="utf-8")
        return {
            "bundle_file_types": {
                key: value.value for key, value in file_types.items()
            },
            "bundle_artifact_paths": [
                str(item["relative_path"])
                for item in artifacts
            ],
            "current_stage": "render_escalation_report",
            "status": WorkflowStatus.GENERATING_REPORT.value,
            "updated_at": _now(),
        }

    async def assemble_candidate_bundle(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        root = self._candidate_root(
            workflow_id,
            int(state.get("bundle_revision_count", 0)),
        )
        issue = IssueRequest.model_validate(state["issue"])
        plan = EvidencePlan.model_validate(state["evidence_plan"])
        results = [CollectorResult.model_validate(item) for item in state.get("collector_results", [])]
        sanitization = SanitizationReport.model_validate(state["sanitization_report"])
        analysis = AnalysisResult.model_validate(state["analysis_result"])
        json_files = {
            "issue.json": issue.model_dump(mode="json"),
            "evidence_plan.json": plan.model_dump(mode="json"),
            "collection_results.json": [
                {
                    "result_id": str(item.result_id),
                    "step_id": str(item.step_id),
                    "collector": item.collector.value,
                    "status": item.status.value,
                    "warnings": item.warnings,
                    "error": item.error.model_dump(mode="json") if item.error else None,
                    "duration_ms": item.duration_ms,
                }
                for item in results
            ],
            "sanitization_report.json": sanitization.model_dump(mode="json"),
            "analysis_result.json": analysis.model_dump(mode="json"),
            "missing_evidence.json": [
                item.model_dump(mode="json")
                for item in analysis.missing_evidence
            ],
        }
        for filename, value in json_files.items():
            (root / filename).write_text(
                json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        file_types = {
            key: ArtifactType(value)
            for key, value in state["bundle_file_types"].items()
        }
        if set(state["bundle_artifact_paths"]) != {
            item for item in file_types if item.startswith("artifacts/")
        }:
            raise ValueError("Candidate artifact list does not match approved files.")
        expected_json = set(json_files) | {"escalation_summary.html"}
        if not expected_json.issubset(file_types):
            raise ValueError("Candidate report files are incomplete.")
        return {
            "bundle_file_types": {key: value.value for key, value in file_types.items()},
            "status": WorkflowStatus.PACKAGING.value,
            "current_stage": "assemble_candidate_bundle",
            "updated_at": _now(),
        }

    async def generate_manifest_node(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        root = self._candidate_root(
            workflow_id,
            int(state.get("bundle_revision_count", 0)),
        )
        file_types = {
            key: ArtifactType(value)
            for key, value in state["bundle_file_types"].items()
        }
        entries = generate_manifest(root, file_types)
        return {
            "bundle_manifest": [item.model_dump(mode="json") for item in entries],
            "current_stage": "generate_manifest",
            "updated_at": _now(),
        }

    async def create_candidate_zip_node(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        revision = int(state.get("bundle_revision_count", 0))
        relative_archive = f"workflows/{workflow_id}/diagnostic-bundle-r{revision}.zip"
        output_root = Path(self.settings.output_directory).resolve()
        unresolved_archive = output_root / relative_archive
        if (
            (output_root / "workflows").is_symlink()
            or (output_root / "workflows" / str(workflow_id)).is_symlink()
            or unresolved_archive.is_symlink()
        ):
            raise ValueError("Candidate ZIP path cannot use symbolic links.")
        archive = unresolved_archive.resolve()
        if not archive.is_relative_to(output_root):
            raise ValueError("Candidate ZIP path is unsafe.")
        archive.parent.mkdir(parents=True, exist_ok=True)
        create_candidate_zip(
            self._candidate_root(workflow_id, revision),
            archive,
        )
        bundle = BundleMetadata(
            bundle_id=uuid5(workflow_id, f"bundle:{revision}"),
            workflow_id=workflow_id,
            filename=f"diagnostic-bundle-r{revision}.zip",
            relative_path=relative_archive,
            manifest_relative_path=(
                f"workflows/{workflow_id}/candidate-{revision}/{MANIFEST_FILENAME}"
            ),
            verification_status=BundleVerificationStatus.PENDING,
            sanitized_only=False,
        )
        return {
            "bundle_metadata": bundle.model_dump(mode="json"),
            "current_stage": "create_candidate_zip",
            "updated_at": _now(),
        }

    async def verify_candidate_bundle(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        bundle = BundleMetadata.model_validate(state["bundle_metadata"])
        record = self.bundles.register_if_missing(bundle)
        try:
            revision = int(state.get("bundle_revision_count", 0))
            candidate = self._candidate_root(workflow_id, revision)
            output_root = Path(self.settings.output_directory).resolve()
            unresolved_archive = output_root / bundle.relative_path
            if unresolved_archive.is_symlink():
                raise ValueError("Candidate ZIP path cannot use symbolic links.")
            archive = unresolved_archive.resolve(strict=True)
            if not archive.is_relative_to(output_root) or not archive.is_file():
                raise ValueError("Candidate ZIP path is unsafe.")
            manifest = [
                ManifestEntry.model_validate(item)
                for item in state.get("bundle_manifest", [])
            ]
            manifest_by_path = {item.relative_path: item for item in manifest}
            release = EvidenceReleaseDecision.model_validate(
                state["evidence_release_decision"]
            )
            approved_artifact_ids = {
                item.artifact_id
                for item in release.item_decisions
                if item.decision == EvidenceItemDecisionType.INCLUDE
                and item.approved_excerpt_ids
            }
            if len(approved_artifact_ids) != len(state.get("bundle_artifact_paths", [])):
                raise ValueError("Bundle artifacts do not match the evidence-release decision.")
            output_root = Path(self.settings.output_directory).resolve()
            for relative in state.get("bundle_artifact_paths", []):
                safe_relative = Path(relative)
                artifact_id = UUID(safe_relative.stem)
                if artifact_id not in approved_artifact_ids:
                    raise ValueError("Bundle contains an artifact outside the approval.")
                artifact_record = self.artifacts.get(artifact_id)
                if (
                    artifact_record is None
                    or artifact_record.workflow_id != str(workflow_id)
                    or not artifact_record.sanitized
                    or artifact_record.sensitivity_status == SensitivityStatus.BLOCKED.value
                ):
                    raise ValueError("Bundle contains an ineligible artifact.")
                artifact_metadata = _artifact_metadata(artifact_record)
                if not self.sanitizer.verify_artifact(artifact_metadata):
                    raise ValueError("A packaged sanitized artifact failed verification.")
                entry = manifest_by_path.get(relative)
                if (
                    entry is None
                    or not artifact_record.sha256
                    or entry.sha256 != artifact_record.sha256
                    or entry.size_bytes != artifact_record.size_bytes
                ):
                    raise ValueError("Packaged artifact does not match its local metadata.")
            verify_candidate_zip(
                candidate,
                archive,
                manifest,
                required_files={
                    "escalation_summary.html",
                    "issue.json",
                    "evidence_plan.json",
                    "collection_results.json",
                    "sanitization_report.json",
                    "analysis_result.json",
                    "missing_evidence.json",
                },
                approved_artifacts=set(state.get("bundle_artifact_paths", [])),
            )
        except Exception:
            self.bundles.update_verification(
                bundle.bundle_id,
                BundleVerificationStatus.FAILED,
            )
            raise
        verified = bundle.model_copy(
            update={
                "verification_status": BundleVerificationStatus.VERIFIED,
                "sanitized_only": True,
            }
        )
        self.bundles.update_verification(
            bundle.bundle_id,
            BundleVerificationStatus.VERIFIED,
            sanitized_only=True,
        )
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"bundle-verified:{bundle.bundle_id}",
                WorkflowEventType.BUNDLE_VERIFIED,
                "Candidate diagnostic bundle passed local verification.",
            ),
        )
        return {
            "bundle_metadata": verified.model_dump(mode="json"),
            "status": WorkflowStatus.VERIFYING.value,
            "current_stage": "verify_candidate_bundle",
            "updated_at": _now(),
        }

    async def request_bundle_release(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        bundle = BundleMetadata.model_validate(state["bundle_metadata"])
        record = self.bundles.get(bundle.bundle_id)
        if (
            record is None
            or record.workflow_id != str(workflow_id)
            or record.verification_status != BundleVerificationStatus.VERIFIED.value
            or not record.sanitized_only
        ):
            raise ValueError("Only a verified sanitized bundle may be submitted for approval.")
        approval = ApprovalRequest(
            approval_id=uuid5(workflow_id, f"bundle-release:{bundle.bundle_id}"),
            workflow_id=workflow_id,
            approval_type=ApprovalType.BUNDLE_RELEASE,
            title="Review diagnostic bundle release",
            explanation="Review the verified diagnostic bundle before release.",
        )
        stored = self.approvals.get_or_create_pending(
            approval,
            bundle_id=bundle.bundle_id,
        )
        active = ApprovalRequest(
            approval_id=UUID(stored.id),
            workflow_id=workflow_id,
            approval_type=ApprovalType(stored.approval_type),
            status=ApprovalStatus(stored.status),
            title=stored.title,
            explanation=stored.explanation,
            requested_at=stored.requested_at,
            expires_at=stored.expires_at,
        )
        analysis = AnalysisResult.model_validate(state["analysis_result"])
        review_payload = {
            "workflow_id": str(workflow_id),
            "bundle_id": str(bundle.bundle_id),
            "zip_filename": bundle.filename,
            "included_relative_filenames": sorted(
                [
                    ManifestEntry.model_validate(item).relative_path
                    for item in state.get("bundle_manifest", [])
                ]
                + [MANIFEST_FILENAME]
            ),
            "manifest_verification": record.verification_status,
            "artifact_count": len(state.get("bundle_artifact_paths", [])),
            "missing_evidence": [
                {
                    "collector": item.collector.value,
                    "reason": item.reason,
                    "impact": item.impact,
                }
                for item in analysis.missing_evidence
            ],
            "warnings": _merge_warnings(
                state.get("warnings", []),
                analysis.warnings,
            ),
        }
        self.workflows.update_status(
            workflow_id,
            WorkflowStatus.AWAITING_RELEASE_APPROVAL,
            current_stage="request_bundle_release",
        )
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"bundle-approval-requested:{active.approval_id}",
                WorkflowEventType.APPROVAL_REQUESTED,
                "A human bundle-release decision is required.",
            ),
        )
        return {
            "active_approval_request": active.model_dump(mode="json"),
            "bundle_review_payload": review_payload,
            "status": WorkflowStatus.AWAITING_RELEASE_APPROVAL.value,
            "current_stage": "request_bundle_release",
            "updated_at": _now(),
        }

    async def await_bundle_release(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        received = interrupt(state["bundle_review_payload"])
        decision = BundleReleaseDecision.model_validate(received)
        if decision.comment:
            validate_secret_free_text(decision.comment)
            if _ABSOLUTE_PATH_PATTERN.search(decision.comment):
                raise ValueError("Bundle-release comment contains an absolute path.")
        return {
            "bundle_release_decision": decision.model_dump(mode="json"),
            "current_stage": "process_bundle_release_decision",
            "updated_at": _now(),
        }

    async def process_bundle_release_decision(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        decision = BundleReleaseDecision.model_validate(
            state["bundle_release_decision"]
        )
        bundle = BundleMetadata.model_validate(state["bundle_metadata"])
        active = ApprovalRequest.model_validate(state["active_approval_request"])
        if (
            decision.workflow_id != workflow_id
            or decision.bundle_id != bundle.bundle_id
            or decision.approval_id != active.approval_id
            or active.workflow_id != workflow_id
            or active.approval_type != ApprovalType.BUNDLE_RELEASE
            or active.status != ApprovalStatus.PENDING
        ):
            raise ValueError("Bundle-release decision does not match the active approval.")
        record = self.bundles.get(bundle.bundle_id)
        if (
            record is None
            or record.workflow_id != str(workflow_id)
            or record.verification_status != BundleVerificationStatus.VERIFIED.value
            or not record.sanitized_only
        ):
            raise ValueError("Bundle is not eligible for release.")
        stored_approval = self.approvals.get(decision.approval_id)
        if (
            stored_approval is None
            or stored_approval.status != ApprovalStatus.PENDING.value
            and stored_approval.decision != decision.model_dump(mode="json")
        ):
            raise ValueError("Bundle-release approval is not pending.")
        await self.verify_candidate_bundle(state)
        self.bundles.record_release(decision)
        self.workflows.add_event(
            workflow_id,
            _event(
                workflow_id,
                f"bundle-release-decision:{decision.approval_id}",
                WorkflowEventType.APPROVAL_RECEIVED,
                f"Bundle release was finalized as {decision.status.value}.",
            ),
        )
        if decision.status == ApprovalStatus.APPROVED:
            if not decision.reviewer_acknowledgement:
                raise ValueError("Bundle release requires reviewer acknowledgement.")
            self.workflows.add_event(
                workflow_id,
                _event(
                    workflow_id,
                    f"bundle-released:{bundle.bundle_id}",
                    WorkflowEventType.BUNDLE_RELEASED,
                    "The verified diagnostic bundle was released.",
                ),
            )
            return {
                "bundle_release_decision": decision.model_dump(mode="json"),
                "status": WorkflowStatus.VERIFYING.value,
                "current_stage": "complete_workflow",
                "updated_at": _now(),
            }
        if decision.status == ApprovalStatus.REVISION_REQUESTED:
            revision = int(state.get("bundle_revision_count", 0))
            if revision < 1:
                self.workflows.update_status(
                    workflow_id,
                    WorkflowStatus.PACKAGING,
                    current_stage="bundle_revision_requested",
                )
                return {
                    "bundle_release_decision": decision.model_dump(mode="json"),
                    "bundle_revision_count": revision + 1,
                    "active_approval_request": None,
                    "status": WorkflowStatus.PACKAGING.value,
                    "current_stage": "bundle_revision_requested",
                    "updated_at": _now(),
                }
            self.workflows.update_status(
                workflow_id,
                WorkflowStatus.REJECTED,
                current_stage="bundle_revision_limit",
            )
            return {
                "bundle_release_decision": decision.model_dump(mode="json"),
                "status": WorkflowStatus.REJECTED.value,
                "current_stage": "bundle_revision_limit",
                "updated_at": _now(),
            }
        if decision.status not in {ApprovalStatus.REJECTED, ApprovalStatus.CANCELLED}:
            raise ValueError("Bundle-release decision status is invalid.")
        status = (
            WorkflowStatus.CANCELLED
            if decision.status == ApprovalStatus.CANCELLED
            else WorkflowStatus.REJECTED
        )
        self.workflows.update_status(
            workflow_id,
            status,
            current_stage="bundle_release_finished",
        )
        return {
            "bundle_release_decision": decision.model_dump(mode="json"),
            "status": status.value,
            "current_stage": "bundle_release_finished",
            "updated_at": _now(),
        }

    @staticmethod
    def route_after_bundle_release(state: WorkflowGraphState) -> str:
        if state.get("error_code"):
            return "failure"
        status = state.get("status")
        if state.get("current_stage") == "complete_workflow":
            return "complete_workflow"
        if state.get("current_stage") == "bundle_revision_requested":
            return "render_escalation_report"
        if status in {WorkflowStatus.REJECTED.value, WorkflowStatus.CANCELLED.value}:
            return "end"
        return "failure"

    async def complete_workflow(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        decision = BundleReleaseDecision.model_validate(state["bundle_release_decision"])
        bundle = self.bundles.get(decision.bundle_id)
        if (
            decision.status != ApprovalStatus.APPROVED
            or not decision.reviewer_acknowledgement
            or bundle is None
            or bundle.workflow_id != str(workflow_id)
            or bundle.release_status != ApprovalStatus.APPROVED.value
            or bundle.verification_status != BundleVerificationStatus.VERIFIED.value
            or not bundle.sanitized_only
        ):
            raise ValueError("Workflow completion requires an approved verified bundle.")
        workflow = self.workflows.complete(workflow_id)
        return {
            "status": workflow.status,
            "current_stage": workflow.current_stage,
            "updated_at": _now(),
        }

    async def handle_workflow_failure(
        self,
        state: WorkflowGraphState,
    ) -> dict[str, object]:
        workflow_id = UUID(state["workflow_id"])
        if self.workflows.get(workflow_id) is not None:
            self.workflows.fail(
                workflow_id,
                error_code=state.get("error_code") or "workflow_failed",
                safe_message=state.get("error_message") or "The workflow could not be completed.",
            )
        return {
            "status": WorkflowStatus.FAILED.value,
            "current_stage": WorkflowStatus.FAILED.value,
            "updated_at": _now(),
        }


def _event(
    workflow_id: UUID,
    key: str,
    event_type: WorkflowEventType,
    message: str,
) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=uuid5(workflow_id, key),
        event_type=event_type,
        message=message,
    )


def _artifact_metadata(record) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=UUID(record.id),
        workflow_id=UUID(record.workflow_id),
        name=record.name,
        artifact_type=ArtifactType(record.artifact_type),
        relative_path=record.relative_path,
        media_type=record.media_type,
        size_bytes=record.size_bytes,
        sanitized=record.sanitized,
        sensitivity_status=SensitivityStatus(record.sensitivity_status),
        sha256=record.sha256,
        created_at=record.created_at,
    )


def _merge_sanitization_reports(
    previous: SanitizationReport,
    current: SanitizationReport,
    workflow_id: UUID,
    cycle: int,
) -> SanitizationReport:
    counts: dict[str, int] = {}
    categories = {}
    for item in [
        *previous.redactions_by_category,
        *current.redactions_by_category,
    ]:
        key = item.category.value
        categories[key] = item.category
        counts[key] = counts.get(key, 0) + item.count
    approved_ids = list(
        dict.fromkeys(
            [*previous.approved_artifact_ids, *current.approved_artifact_ids]
        )
    )
    blocked_ids = list(
        dict.fromkeys(
            [*previous.blocked_artifact_ids, *current.blocked_artifact_ids]
        )
    )
    approved_ids = [item for item in approved_ids if item not in blocked_ids]
    status = (
        SanitizationStatus.PARTIAL
        if approved_ids and blocked_ids
        else SanitizationStatus.BLOCKED
        if blocked_ids
        else SanitizationStatus.COMPLETED
    )
    flags = {
        item.flag_id: item
        for item in [
            *previous.residual_risk_flags,
            *current.residual_risk_flags,
        ]
    }
    return SanitizationReport(
        report_id=uuid5(workflow_id, f"sanitization-report:{cycle}"),
        workflow_id=workflow_id,
        status=status,
        files_processed=len(set(approved_ids) | set(blocked_ids)),
        total_redactions=sum(counts.values()),
        redactions_by_category=[
            RedactionCount(category=categories[key], count=value)
            for key, value in sorted(counts.items())
        ],
        residual_risk_flags=list(flags.values())[:500],
        approved_artifact_ids=approved_ids,
        blocked_artifact_ids=blocked_ids,
        warnings=_merge_warnings(previous.warnings, current.warnings),
    )


def _merge_warnings(*groups: list[str]) -> list[str]:
    return list(dict.fromkeys(warning for group in groups for warning in group))[:100]


def _now() -> str:
    from app.models._common import utc_now

    return utc_now().isoformat()
