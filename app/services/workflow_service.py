"""Application service for starting and resuming collection workflows."""

import logging
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.collectors.registry import CollectorRegistry, create_default_registry
from app.core.config import Settings, get_settings
from app.models.approval import (
    ApprovalStatus,
    BundleReleaseDecision,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)
from app.models.bundle import BundleMetadata, BundleVerificationStatus
from app.models._common import validate_secret_free_text
from app.models.issue import IssueRequest
from app.models.workflow import WorkflowEvent, WorkflowState, WorkflowStatus
from app.repositories.approvals import ApprovalRepository
from app.repositories.artifacts import ArtifactRepository
from app.repositories.analyses import AnalysisRepository
from app.repositories.bundles import BundleRepository
from app.repositories.workflows import WorkflowRepository
from app.scenarios.loader import ScenarioLoader
from app.security.policy import PlanPolicyValidator
from app.security.sanitizer import EvidenceSanitizationService
from app.services.planning_service import EvidencePlanningService
from app.services.analysis_service import EvidenceAnalysisService
from app.workflows.graph import WorkflowGraphState, WorkflowNodes

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {
    WorkflowStatus.COLLECTION_COMPLETED,
    WorkflowStatus.PARTIAL,
    WorkflowStatus.REJECTED,
    WorkflowStatus.CANCELLED,
    WorkflowStatus.FAILED,
    WorkflowStatus.COMPLETED,
}
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9:/])(?:[A-Za-z]:\\[^\r\n\"<>|?*]+|"
    r"\\\\[^\\/\r\n]+\\[^\\\r\n]+|"
    r"/(?:[^\s/\"'<>]+/)*[^\s/\"'<>]+)"
)


class WorkflowService:
    """Hide graph compilation, checkpoint configuration, and business repositories."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        planner: EvidencePlanningService | None = None,
        analyzer: EvidenceAnalysisService | None = None,
        scenario_loader: ScenarioLoader | None = None,
        registry: CollectorRegistry | None = None,
        policy_validator: PlanPolicyValidator | None = None,
        checkpoint_path: Path | str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._workflows = WorkflowRepository(session_factory)
        self._approvals = ApprovalRepository(session_factory)
        self._artifacts = ArtifactRepository(session_factory)
        self._analyses = AnalysisRepository(session_factory)
        self._bundles = BundleRepository(session_factory)
        self._scenario_loader = scenario_loader or ScenarioLoader()
        self._registry = registry or create_default_registry(self._scenario_loader)
        self._policy_validator = policy_validator or PlanPolicyValidator(
            registry=self._registry,
            scenario_loader=self._scenario_loader,
        )
        self._planner = planner or EvidencePlanningService(
            registry=self._registry,
            settings=self._settings,
        )
        self._analyzer = analyzer or EvidenceAnalysisService(settings=self._settings)
        self._sanitizer = EvidenceSanitizationService(
            self._artifacts,
            scenario_loader=self._scenario_loader,
            settings=self._settings,
        )
        self._checkpoint_path = (
            Path(checkpoint_path)
            if checkpoint_path is not None
            else _checkpoint_path_from_database_url(self._settings)
        )

    async def start_workflow(self, issue: IssueRequest) -> WorkflowState:
        """Create a workflow and run to approval, rejection, or safe failure."""
        validated_issue = IssueRequest.model_validate(issue.model_dump(mode="json"))
        try:
            for value in (
                validated_issue.title,
                validated_issue.description,
                validated_issue.application_name,
                *validated_issue.symptoms,
            ):
                validate_secret_free_text(value)
                if _ABSOLUTE_PATH_PATTERN.search(value):
                    raise ValueError("Issue contains an absolute path.")
        except ValueError:
            raise ValueError("Issue contains information that cannot be checkpointed.") from None
        workflow_id = uuid4()
        now = _now()
        initial: WorkflowGraphState = {
            "workflow_id": str(workflow_id),
            "issue_id": str(uuid4()),
            "issue": validated_issue.model_dump(mode="json"),
            "scenario_id": validated_issue.scenario_id,
            "status": WorkflowStatus.RECEIVED.value,
            "current_stage": "received",
            "evidence_plan": None,
            "policy_result": None,
            "active_approval_request": None,
            "approval_payload": None,
            "evidence_approval_payload": None,
            "plan_approval": None,
            "collector_results": [],
            "sanitization_report": None,
            "sanitized_excerpts": [],
            "evidence_release_decision": None,
            "analysis_result": None,
            "missing_evidence": [],
            "supplemental_plan": None,
            "supplemental_cycle_count": 0,
            "bundle_metadata": None,
            "bundle_release_decision": None,
            "bundle_review_payload": None,
            "bundle_manifest": [],
            "bundle_artifact_paths": [],
            "bundle_file_types": {},
            "bundle_revision_count": 0,
            "supplemental_pending": False,
            "evidence_review_revision": 0,
            "external_analysis_payload": None,
            "warnings": [],
            "error_code": None,
            "error_message": None,
            "revision_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        config = self._config(workflow_id)
        try:
            self._prepare_checkpoint_directory()
            async with AsyncSqliteSaver.from_conn_string(
                str(self._checkpoint_path)
            ) as saver:
                await saver.setup()
                graph = self._build_graph(saver)
                await graph.ainvoke(initial, config=config)
                snapshot = await graph.aget_state(config)
                if not snapshot.values:
                    raise RuntimeError("Workflow checkpoint has no state.")
                return self._to_contract(snapshot.values)
        except Exception:
            logger.error("Workflow start failed safely")
            self._mark_failed(workflow_id, "workflow_start_failed", "Workflow could not be started.")
            return self._fallback_state(
                initial,
                WorkflowStatus.FAILED,
                "workflow_start_failed",
                "Workflow could not be started.",
            )

    async def resume_workflow(
        self,
        workflow_id: UUID,
        decision: PlanApprovalDecision,
    ) -> WorkflowState:
        """Resume the matching pending approval and continue through collection."""
        validated = PlanApprovalDecision.model_validate(decision.model_dump(mode="json"))
        if validated.workflow_id != workflow_id:
            raise ValueError("Approval decision does not belong to this workflow.")
        try:
            try:
                checkpointed = await self.get_current_state(workflow_id)
            except RuntimeError:
                raise RuntimeError("Evidence approval state is unavailable.") from None
        except RuntimeError:
            self._mark_failed(
                workflow_id,
                "checkpoint_unavailable",
                "Workflow checkpoint storage is unavailable.",
            )
            return self._persisted_fallback(
                workflow_id,
                "checkpoint_unavailable",
                "Workflow checkpoint storage is unavailable.",
            )
        if checkpointed is None:
            raise ValueError("Workflow state is unavailable.")
        state = checkpointed.model_dump(mode="json")
        if state.get("plan_approval") == validated.model_dump(mode="json"):
            return checkpointed
        if checkpointed.status in _TERMINAL_STATUSES:
            raise ValueError("Workflow is no longer awaiting plan approval.")
        active = state.get("active_approval_request")
        if (
            checkpointed.status not in {
                WorkflowStatus.AWAITING_PLAN_APPROVAL,
                WorkflowStatus.AWAITING_SUPPLEMENTAL_APPROVAL,
            }
            or not isinstance(active, dict)
            or active.get("approval_id") != str(validated.approval_id)
            or active.get("workflow_id") != str(workflow_id)
        ):
            raise ValueError("Approval decision does not match the active request.")
        try:
            record = self._approvals.get(validated.approval_id)
        except Exception:
            self._mark_failed(
                workflow_id,
                "approval_lookup_failed",
                "The active approval could not be verified.",
            )
            return self._fallback_state(
                state,
                WorkflowStatus.FAILED,
                "approval_lookup_failed",
                "The active approval could not be verified.",
            )
        if record is None or record.workflow_id != str(workflow_id):
            raise ValueError("Active approval request is unavailable.")
        if record.status != ApprovalStatus.PENDING.value and (
            record.decision != validated.model_dump(mode="json")
        ):
            raise ValueError("Approval request has already been finalized.")

        try:
            self._prepare_checkpoint_directory()
            async with AsyncSqliteSaver.from_conn_string(
                str(self._checkpoint_path)
            ) as saver:
                await saver.setup()
                graph = self._build_graph(saver)
                await graph.ainvoke(
                    Command(resume=validated.model_dump(mode="json")),
                    config=self._config(workflow_id),
                )
                resumed = await graph.aget_state(self._config(workflow_id))
                if not resumed.values:
                    raise RuntimeError("Workflow checkpoint has no state.")
                return self._to_contract(resumed.values)
        except Exception:
            logger.error("Workflow resume failed safely")
            self._mark_failed(
                workflow_id,
                "workflow_resume_failed",
                "Workflow could not be resumed.",
            )
            return self._fallback_state(
                state,
                WorkflowStatus.FAILED,
                "workflow_resume_failed",
                "Workflow could not be resumed.",
            )

    async def resume_evidence_release(
        self,
        workflow_id: UUID,
        decision: EvidenceReleaseDecision,
    ) -> WorkflowState:
        """Resume only the matching evidence-release interrupt."""
        validated = EvidenceReleaseDecision.model_validate(
            decision.model_dump(mode="json")
        )
        if validated.workflow_id != workflow_id:
            raise ValueError("Evidence-release decision does not belong to this workflow.")
        try:
            checkpointed = await self.get_current_state(workflow_id)
        except RuntimeError:
            raise RuntimeError("Evidence approval state is unavailable.") from None
        if checkpointed is None:
            raise ValueError("Workflow state is unavailable.")
        state = checkpointed.model_dump(mode="json")
        decision_json = validated.model_dump(mode="json")
        expected_analysis_id = uuid5(
            workflow_id,
            "analysis:"
            f"{checkpointed.supplemental_cycle_count}:"
            f"{validated.approval_id}",
        )
        if (
            state.get("evidence_release_decision") == decision_json
            and state.get("analysis_result") is not None
            and state["analysis_result"].get("analysis_id")
            == str(expected_analysis_id)
        ):
            return checkpointed
        for item in validated.item_decisions:
            if item.reviewer_comment:
                validate_secret_free_text(item.reviewer_comment)
                if _ABSOLUTE_PATH_PATTERN.search(item.reviewer_comment):
                    raise ValueError("Evidence-review comment contains an absolute path.")
        active = state.get("active_approval_request")
        if (
            checkpointed.status != WorkflowStatus.AWAITING_EVIDENCE_APPROVAL
            or not isinstance(active, dict)
            or active.get("approval_id") != str(validated.approval_id)
            or active.get("workflow_id") != str(workflow_id)
            or active.get("approval_type") != "evidence_release"
        ):
            raise ValueError("Evidence-release decision does not match the active request.")
        try:
            record = self._approvals.get(validated.approval_id)
        except Exception:
            logger.error("Evidence approval lookup failed safely")
            raise RuntimeError("Evidence approval could not be verified.") from None
        if record is None or record.workflow_id != str(workflow_id):
            raise ValueError("Active evidence approval is unavailable.")
        if record.status != ApprovalStatus.PENDING.value and record.decision != decision_json:
            raise ValueError("Evidence approval has already been finalized differently.")
        return await self._resume_interrupt(
            workflow_id,
            state,
            decision_json,
        )

    async def resume_bundle_release(
        self,
        workflow_id: UUID,
        decision: BundleReleaseDecision,
    ) -> WorkflowState:
        """Resume only the matching pending final bundle-release approval."""
        validated = BundleReleaseDecision.model_validate(
            decision.model_dump(mode="json")
        )
        if validated.workflow_id != workflow_id:
            raise ValueError("Bundle-release decision does not belong to this workflow.")
        checkpointed = await self.get_current_state(workflow_id)
        if checkpointed is None:
            raise ValueError("Workflow state is unavailable.")
        state = checkpointed.model_dump(mode="json")
        decision_json = validated.model_dump(mode="json")
        if state.get("bundle_release_decision") == decision_json:
            return checkpointed
        active = state.get("active_approval_request")
        bundle_value = state.get("bundle_metadata")
        if (
            checkpointed.status != WorkflowStatus.AWAITING_RELEASE_APPROVAL
            or not isinstance(active, dict)
            or not isinstance(bundle_value, dict)
            or active.get("approval_id") != str(validated.approval_id)
            or active.get("workflow_id") != str(workflow_id)
            or active.get("approval_type") != "bundle_release"
        ):
            raise ValueError("Bundle-release decision does not match the active request.")
        bundle = BundleMetadata.model_validate(bundle_value)
        if (
            validated.bundle_id != bundle.bundle_id
            or bundle.workflow_id != workflow_id
            or bundle.verification_status != BundleVerificationStatus.VERIFIED
            or not bundle.sanitized_only
        ):
            raise ValueError("Bundle is not eligible for release.")
        record = self._approvals.get(validated.approval_id)
        if (
            record is None
            or record.workflow_id != str(workflow_id)
            or record.bundle_id != str(bundle.bundle_id)
        ):
            raise ValueError("Active bundle-release approval is unavailable.")
        if record.status != ApprovalStatus.PENDING.value and (
            record.decision != decision_json
        ):
            raise ValueError("Bundle-release approval was already finalized differently.")
        return await self._resume_interrupt(
            workflow_id,
            state,
            decision_json,
        )

    async def _resume_interrupt(
        self,
        workflow_id: UUID,
        state: dict[str, Any],
        resume_value: dict[str, Any],
    ) -> WorkflowState:
        try:
            self._prepare_checkpoint_directory()
            async with AsyncSqliteSaver.from_conn_string(
                str(self._checkpoint_path)
            ) as saver:
                await saver.setup()
                graph = self._build_graph(saver)
                await graph.ainvoke(
                    Command(resume=resume_value),
                    config=self._config(workflow_id),
                )
                resumed = await graph.aget_state(self._config(workflow_id))
                if not resumed.values:
                    raise RuntimeError("Workflow checkpoint has no state.")
                return self._to_contract(resumed.values)
        except Exception:
            logger.error("Evidence-release resume failed safely")
            self._mark_failed(
                workflow_id,
                "evidence_release_resume_failed",
                "Evidence approval could not be resumed.",
            )
            return self._fallback_state(
                state,
                WorkflowStatus.FAILED,
                "evidence_release_resume_failed",
                "Evidence approval could not be resumed.",
            )

    async def get_current_state(self, workflow_id: UUID) -> WorkflowState | None:
        """Load the current JSON checkpoint using the workflow UUID thread ID."""
        try:
            self._prepare_checkpoint_directory()
            config = self._config(workflow_id)
            async with AsyncSqliteSaver.from_conn_string(
                str(self._checkpoint_path)
            ) as saver:
                await saver.setup()
                snapshot = await self._build_graph(saver).aget_state(config)
                return self._to_contract(snapshot.values) if snapshot.values else None
        except Exception:
            logger.error("Workflow checkpoint lookup failed safely")
            raise RuntimeError("Workflow state could not be retrieved.") from None

    def _build_graph(self, checkpointer):
        nodes = WorkflowNodes(
            workflows=self._workflows,
            approvals=self._approvals,
            artifacts=self._artifacts,
            analyses=self._analyses,
            bundles=self._bundles,
            scenario_loader=self._scenario_loader,
            registry=self._registry,
            policy_validator=self._policy_validator,
            planner=self._planner,
            sanitizer=self._sanitizer,
            analyzer=self._analyzer,
            settings=self._settings,
        )
        return nodes.build(checkpointer)

    @staticmethod
    def _config(workflow_id: UUID) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": str(workflow_id)}}

    def _prepare_checkpoint_directory(self) -> None:
        try:
            self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise RuntimeError("Workflow checkpoint storage is unavailable.") from None

    def _mark_failed(self, workflow_id: UUID, code: str, message: str) -> None:
        try:
            if self._workflows.get(workflow_id) is not None:
                self._workflows.fail(workflow_id, error_code=code, safe_message=message)
        except Exception:
            logger.error("Workflow failure status could not be persisted")

    def _to_contract(self, state: dict[str, Any]) -> WorkflowState:
        workflow_id = UUID(state["workflow_id"])
        events: list[WorkflowEvent] = []
        try:
            events = [
                WorkflowEvent(
                    event_id=UUID(record.id),
                    event_type=record.event_type,
                    message=record.message,
                    created_at=record.created_at,
                )
                for record in self._workflows.list_events(workflow_id)
            ]
        except Exception:
            logger.error("Workflow event lookup failed safely")
        values = {
            key: state[key]
            for key in (
                "workflow_id",
                "issue",
                "status",
                "current_stage",
                "evidence_plan",
                "policy_result",
                "active_approval_request",
                "plan_approval",
                "collector_results",
                "sanitized_excerpts",
                "sanitization_report",
                "evidence_release_decision",
                "analysis_result",
                "supplemental_plan",
                "supplemental_cycle_count",
                "bundle_metadata",
                "bundle_release_decision",
                "bundle_review_payload",
                "bundle_revision_count",
                "missing_evidence",
                "warnings",
                "error_code",
                "error_message",
                "created_at",
                "updated_at",
            )
            if key in state
        }
        values["events"] = [event.model_dump(mode="json") for event in events]
        return WorkflowState.model_validate(values)

    def _fallback_state(
        self,
        state: dict[str, Any],
        status: WorkflowStatus,
        error_code: str,
        error_message: str,
    ) -> WorkflowState:
        values = {
            key: state[key]
            for key in (
                "workflow_id",
                "issue",
                "evidence_plan",
                "policy_result",
                "active_approval_request",
                "plan_approval",
                "collector_results",
                "sanitized_excerpts",
                "sanitization_report",
                "evidence_release_decision",
                "analysis_result",
                "supplemental_plan",
                "supplemental_cycle_count",
                "bundle_metadata",
                "bundle_release_decision",
                "bundle_review_payload",
                "bundle_revision_count",
                "missing_evidence",
                "warnings",
                "created_at",
            )
            if key in state
        }
        values.update({
            "status": status.value,
            "current_stage": status.value,
            "error_code": error_code,
            "error_message": error_message,
            "updated_at": _now(),
            "events": [],
        })
        return WorkflowState.model_validate(values)

    def _persisted_fallback(
        self,
        workflow_id: UUID,
        error_code: str,
        error_message: str,
    ) -> WorkflowState:
        record = self._workflows.get(workflow_id)
        if record is None:
            raise RuntimeError("Workflow state is unavailable.")
        return WorkflowState(
            workflow_id=workflow_id,
            issue=IssueRequest.model_validate(record.issue_metadata),
            status=WorkflowStatus.FAILED,
            current_stage=WorkflowStatus.FAILED.value,
            error_code=error_code,
            error_message=error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


def _checkpoint_path_from_database_url(settings: Settings) -> Path:
    try:
        url = make_url(settings.database_url)
    except (TypeError, ValueError):
        raise ValueError("Workflow checkpoint configuration is invalid.") from None
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("A file-backed SQLite database is required for workflow checkpoints.")
    database_path = Path(url.database)
    return database_path.with_name(f"{database_path.stem}.checkpoints.sqlite3")


def _now() -> str:
    from app.models._common import utc_now

    return utc_now().isoformat()
