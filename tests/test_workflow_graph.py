"""Focused tests for LangGraph approval, resume, and collection boundaries."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.database.models import ArtifactRecord, CollectorRunRecord, WorkflowEventRecord
from app.models.approval import ApprovalStatus, PlanApprovalDecision
from app.models.approval import ApprovalType
from app.models.issue import IssueRequest
from app.models.evidence import SensitivityStatus
from app.models.plan import CollectorName, EvidencePlan, EvidenceStep
from app.models.workflow import WorkflowStatus
from app.repositories import ApprovalRepository, WorkflowRepository
from app.scenarios.loader import ScenarioLoader
from app.services.workflow_service import WorkflowService


class FakePlanner:
    def __init__(self, plan: EvidencePlan) -> None:
        self.plan = plan

    async def create_plan(self, issue, scenario) -> EvidencePlan:
        return self.plan


class ReplanningFakePlanner:
    def __init__(self) -> None:
        self.count = 0

    async def create_plan(self, issue, scenario) -> EvidencePlan:
        self.count += 1
        return plan(CollectorName.PROCESS_LIST).model_copy(
            update={"plan_id": UUID(int=self.count)}
        )


@pytest.fixture
def workflow_environment(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from pathlib import Path

    controlled = Path(__file__).resolve().parents[1] / "controlled_environment"
    monkeypatch.setenv("CONTROLLED_ENVIRONMENT_DIRECTORY", str(controlled))
    monkeypatch.setenv("OUTPUT_DIRECTORY", str(tmp_path / "output"))
    monkeypatch.setenv("MAX_TOOL_CALLS", "6")
    monkeypatch.setenv("MAX_LOG_FILE_SIZE_MB", "5")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def issue(scenario_id: str = "high-cpu-usage") -> IssueRequest:
    return IssueRequest(
        title="High CPU in MapleDesk",
        description="The application has sustained high CPU utilization.",
        application_name="MapleDesk",
        symptoms=["Slow response"],
        scenario_id=scenario_id,
    )


def plan(*collectors: CollectorName) -> EvidencePlan:
    reasons = {
        CollectorName.PROCESS_LIST: "Identify processes associated with high CPU usage.",
        CollectorName.RESOURCE_USAGE: "Confirm resource usage during the reported issue.",
        CollectorName.NETWORK_INFO: "Inspect scenario network state relevant to the issue.",
        CollectorName.APPLICATION_LOGS: "Inspect scenario logs for the reported failure.",
    }
    return EvidencePlan(
        issue_summary="Investigate sustained synthetic CPU usage.",
        suspected_category="resource_usage",
        steps=[
            EvidenceStep(collector=name, reason=reasons[name])
            for name in collectors
        ],
    )


def make_service(
    session_factory,
    base_path,
    proposal: EvidencePlan,
) -> WorkflowService:
    loader = ScenarioLoader()
    return WorkflowService(
        session_factory,
        planner=FakePlanner(proposal),
        scenario_loader=loader,
        checkpoint_path=base_path / "workflow-checkpoints.sqlite3",
    )


def start(service: WorkflowService):
    return asyncio.run(service.start_workflow(issue()))


def approve(state, status=ApprovalStatus.APPROVED, *, approved=None, rejected=None):
    steps = [UUID(step["step_id"]) for step in state.evidence_plan.model_dump(mode="json")["steps"]]
    return PlanApprovalDecision(
        workflow_id=state.workflow_id,
        approval_id=state.active_approval_request.approval_id,
        status=status,
        approved_step_ids=approved if approved is not None else steps,
        rejected_step_ids=rejected or [],
    )


def test_valid_plan_interrupts_for_human_approval(
    session_factory,
    workflow_environment,
) -> None:
    service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.PROCESS_LIST),
    )

    state = start(service)

    assert state.status == WorkflowStatus.AWAITING_PLAN_APPROVAL
    assert state.active_approval_request is not None
    assert state.evidence_plan is not None
    assert ApprovalRepository(session_factory).get_pending(state.workflow_id)


def test_full_approval_resumes_and_executes_approved_collector(
    session_factory,
    workflow_environment,
) -> None:
    service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.PROCESS_LIST),
    )
    pending = start(service)

    state = asyncio.run(service.resume_workflow(pending.workflow_id, approve(pending)))

    assert state.status == WorkflowStatus.AWAITING_EVIDENCE_APPROVAL
    assert len(state.collector_results) == 1
    assert state.collector_results[0].data == {}
    assert state.collector_results[0].status.value == "success"


def test_collected_artifact_is_persisted_as_unsanitized(
    session_factory,
    workflow_environment,
) -> None:
    service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.APPLICATION_LOGS),
    )
    pending = asyncio.run(
        service.start_workflow(issue("network-dependency-timeout"))
    )

    state = asyncio.run(
        service.resume_workflow(pending.workflow_id, approve(pending))
    )

    assert state.status == WorkflowStatus.AWAITING_EVIDENCE_APPROVAL
    assert state.collector_results[0].data == {}
    assert state.sanitization_report is not None
    assert state.active_approval_request.approval_type == ApprovalType.EVIDENCE_RELEASE
    assert state.sanitized_excerpts
    assert all(len(excerpt.content) <= 8_000 for excerpt in state.sanitized_excerpts)
    with session_factory() as session:
        artifacts = list(session.scalars(select(ArtifactRecord)))
    assert any(not artifact.sanitized for artifact in artifacts)
    assert any(artifact.sanitized for artifact in artifacts)
    assert all(
        artifact.sensitivity_status != SensitivityStatus.APPROVED_FOR_EXTERNAL_ANALYSIS.value
        for artifact in artifacts
    )


def test_partial_approval_executes_only_approved_step(
    session_factory,
    workflow_environment,
) -> None:
    service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.PROCESS_LIST, CollectorName.RESOURCE_USAGE),
    )
    pending = start(service)
    approved_id, rejected_id = [step.step_id for step in pending.evidence_plan.steps]
    decision = approve(
        pending,
        ApprovalStatus.PARTIALLY_APPROVED,
        approved=[approved_id],
        rejected=[rejected_id],
    )

    state = asyncio.run(service.resume_workflow(pending.workflow_id, decision))

    assert state.status == WorkflowStatus.AWAITING_EVIDENCE_APPROVAL
    assert [result.status.value for result in state.collector_results] == [
        "success",
        "skipped",
    ]


def test_rejection_stops_without_running_collectors(
    session_factory,
    workflow_environment,
) -> None:
    service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.PROCESS_LIST),
    )
    pending = start(service)
    decision = PlanApprovalDecision(
        workflow_id=pending.workflow_id,
        approval_id=pending.active_approval_request.approval_id,
        status=ApprovalStatus.REJECTED,
    )

    state = asyncio.run(service.resume_workflow(pending.workflow_id, decision))

    assert state.status == WorkflowStatus.REJECTED
    assert state.collector_results == []
    assert _run_count(session_factory) == 0


def test_policy_block_never_creates_an_approval(
    session_factory,
    workflow_environment,
) -> None:
    service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.NETWORK_INFO),
    )

    state = start(service)

    assert state.status == WorkflowStatus.REJECTED
    assert state.active_approval_request is None
    assert ApprovalRepository(session_factory).get_pending(state.workflow_id) == []
    assert _run_count(session_factory) == 0


def test_reusing_finalized_approval_does_not_duplicate_collection_or_events(
    session_factory,
    workflow_environment,
) -> None:
    service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.PROCESS_LIST),
    )
    pending = start(service)
    decision = approve(pending)

    first = asyncio.run(service.resume_workflow(pending.workflow_id, decision))
    first_event_count = _event_count(session_factory, pending.workflow_id)
    second = asyncio.run(service.resume_workflow(pending.workflow_id, decision))

    assert first.status == second.status == WorkflowStatus.AWAITING_EVIDENCE_APPROVAL
    assert _run_count(session_factory) == 1
    assert _event_count(session_factory, pending.workflow_id) == first_event_count


def test_sqlite_checkpoint_resumes_by_workflow_thread_id(
    session_factory,
    workflow_environment,
) -> None:
    first_service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.PROCESS_LIST),
    )
    pending = start(first_service)
    resumed_service = make_service(
        session_factory,
        workflow_environment,
        plan(CollectorName.PROCESS_LIST),
    )

    checkpointed = asyncio.run(
        resumed_service.get_current_state(pending.workflow_id)
    )
    assert checkpointed is not None
    assert checkpointed.status == WorkflowStatus.AWAITING_PLAN_APPROVAL

    completed = asyncio.run(
        resumed_service.resume_workflow(pending.workflow_id, approve(checkpointed))
    )
    assert completed.status == WorkflowStatus.AWAITING_EVIDENCE_APPROVAL
    assert asyncio.run(
        resumed_service.get_current_state(pending.workflow_id)
    ).workflow_id == pending.workflow_id
    assert asyncio.run(
        resumed_service.get_current_state(UUID(int=0))
    ) is None


def test_revision_returns_to_planning_once_then_stops_at_limit(
    session_factory,
    workflow_environment,
) -> None:
    loader = ScenarioLoader()
    service = WorkflowService(
        session_factory,
        planner=ReplanningFakePlanner(),
        scenario_loader=loader,
        checkpoint_path=workflow_environment / "revision-checkpoints.sqlite3",
    )
    pending = start(service)
    first_revision = PlanApprovalDecision(
        workflow_id=pending.workflow_id,
        approval_id=pending.active_approval_request.approval_id,
        status=ApprovalStatus.REVISION_REQUESTED,
        comment="Please revise the proposed collection.",
    )

    revised = asyncio.run(
        service.resume_workflow(pending.workflow_id, first_revision)
    )
    assert revised.status == WorkflowStatus.AWAITING_PLAN_APPROVAL
    assert revised.active_approval_request.approval_id != first_revision.approval_id

    second_revision = PlanApprovalDecision(
        workflow_id=revised.workflow_id,
        approval_id=revised.active_approval_request.approval_id,
        status=ApprovalStatus.REVISION_REQUESTED,
        comment="One more revision requested.",
    )
    stopped = asyncio.run(
        service.resume_workflow(revised.workflow_id, second_revision)
    )

    assert stopped.status == WorkflowStatus.REJECTED, (
        f"{stopped.error_code}: {stopped.error_message}"
    )
    assert "revision limit" in stopped.warnings[-1]


def _run_count(session_factory) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(CollectorRunRecord))


def _event_count(session_factory, workflow_id: UUID) -> int:
    with session_factory() as session:
        return session.scalar(
            select(func.count())
            .select_from(WorkflowEventRecord)
            .where(WorkflowEventRecord.workflow_id == str(workflow_id))
        )
