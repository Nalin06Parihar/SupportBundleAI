"""Persistence behavior tests using isolated temporary SQLite databases."""

from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    ApprovalRecord,
    ArtifactRecord,
    BundleRecord,
    CollectorRunRecord,
    EvidenceStepRecord,
    WorkflowEventRecord,
    WorkflowRecord,
)
from app.database.engine import create_database_engine, initialize_database_engine
from app.database.session import session_scope
from app.models._common import utc_now
from app.models.approval import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
    BundleReleaseDecision,
    EvidenceItemDecision,
    EvidenceItemDecisionType,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)
from app.models.bundle import BundleMetadata, BundleVerificationStatus
from app.models.evidence import (
    ArtifactMetadata,
    ArtifactType,
    CollectionStatus,
    CollectorResult,
    SensitivityStatus,
)
from app.models.issue import IssueRequest
from app.models.plan import CollectorName, EvidencePlan, EvidenceStep
from app.models.workflow import WorkflowEvent, WorkflowEventType, WorkflowStatus
from app.repositories import (
    ApprovalRepository,
    ArtifactRepository,
    BundleRepository,
    WorkflowRepository,
)


def make_issue(**overrides: object) -> IssueRequest:
    values: dict[str, object] = {
        "title": "Application startup",
        "description": "The application fails during startup.",
        "application_name": "Example App",
        "symptoms": ["startup error"],
    }
    values.update(overrides)
    return IssueRequest(**values)


def make_plan(**overrides: object) -> EvidencePlan:
    values: dict[str, object] = {
        "issue_summary": "Application startup fails",
        "suspected_category": "application",
        "steps": [
            EvidenceStep(
                collector=CollectorName.SYSTEM_INFO,
                reason="Collect basic system metadata",
                parameters={"include": ["platform"]},
            )
        ],
    }
    values.update(overrides)
    return EvidencePlan(**values)


def create_workflow(session_factory, *, with_plan: bool = False) -> WorkflowRecord:
    return WorkflowRepository(session_factory).create(
        make_issue(),
        evidence_plan=make_plan() if with_plan else None,
    )


def make_artifact(workflow_id, **overrides: object) -> ArtifactMetadata:
    values: dict[str, object] = {
        "workflow_id": workflow_id,
        "name": "system.json",
        "artifact_type": ArtifactType.SYSTEM_INFORMATION,
        "relative_path": "evidence/system.json",
        "media_type": "application/json",
        "size_bytes": 20,
        "sanitized": True,
        "sensitivity_status": SensitivityStatus.AUTOMATICALLY_SANITIZED,
    }
    values.update(overrides)
    return ArtifactMetadata(**values)


def test_engine_and_session_initialization(database_engine, session_factory) -> None:
    assert database_engine.dialect.name == "sqlite"
    with session_factory() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_engine_initialization_does_not_create_schema(tmp_path) -> None:
    database_path = tmp_path / "unmigrated.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database_engine(engine)
        with engine.connect() as connection:
            tables = connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ).all()
        assert tables == []
    finally:
        engine.dispose()


def test_session_scope_commits_and_rolls_back(session_factory) -> None:
    workflow = create_workflow(session_factory)
    with pytest.raises(RuntimeError):
        with session_scope(session_factory) as session:
            record = session.get(WorkflowRecord, workflow.id)
            assert record is not None
            record.status = WorkflowStatus.FAILED.value
            raise RuntimeError("rollback test")

    repository = WorkflowRepository(session_factory)
    persisted = repository.get(UUID(workflow.id))
    assert persisted is not None
    assert persisted.status == WorkflowStatus.RECEIVED.value


def test_session_scope_commits_changes(session_factory) -> None:
    workflow = create_workflow(session_factory)
    repository = WorkflowRepository(session_factory)
    updated = repository.update_status(
        UUID(workflow.id),
        WorkflowStatus.PLANNING,
    )
    assert updated.status == WorkflowStatus.PLANNING.value


def test_sqlite_enforces_foreign_keys(database_engine, session_factory) -> None:
    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            session.add(
                EvidenceStepRecord(
                    id=str(uuid4()),
                    workflow_id=str(uuid4()),
                    position=0,
                    collector=CollectorName.SYSTEM_INFO.value,
                    reason="Collect basic system information",
                    parameters={},
                    required=True,
                    status="proposed",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )


def test_workflow_creation_retrieval_listing_status_completion_failure(session_factory) -> None:
    repository = WorkflowRepository(session_factory)
    created = repository.create(make_issue())
    workflow_id = UUID(created.id)

    assert repository.get(workflow_id).issue_metadata["title"] == "Application startup"
    assert repository.list(status=WorkflowStatus.RECEIVED)[0].id == created.id
    assert repository.update_status(workflow_id, WorkflowStatus.COLLECTING).status == "collecting"
    assert repository.complete(workflow_id).completed_at is not None

    failed = repository.fail(
        workflow_id,
        error_code="collection_failed",
        safe_message="Approved collection could not be completed.",
    )
    assert failed.status == WorkflowStatus.FAILED.value
    assert failed.error_code == "collection_failed"
    with pytest.raises(ValueError):
        repository.fail(
            workflow_id,
            error_code="unsafe",
            safe_message="Traceback: C:\\private\\secret",
        )


def test_workflow_plan_steps_and_events_round_trip(session_factory) -> None:
    repository = WorkflowRepository(session_factory)
    plan = make_plan()
    workflow = repository.create(make_issue(), evidence_plan=plan)
    workflow_id = UUID(workflow.id)

    steps = repository.list_steps(workflow_id)
    assert len(steps) == 1
    assert steps[0].parameters == {"include": ["platform"]}
    assert repository.get(workflow_id).plan_id == str(plan.plan_id)

    event = WorkflowEvent(
        event_type=WorkflowEventType.APPROVAL_REQUESTED,
        message="Collection plan review is required.",
    )
    repository.add_event(workflow_id, event)
    assert any(
        item.id == str(event.event_id) for item in repository.list_events(workflow_id)
    )
    assert repository.list_events(workflow_id)[0].created_at.tzinfo is not None


def test_collector_run_metadata_round_trip(session_factory) -> None:
    repository = WorkflowRepository(session_factory)
    plan = make_plan()
    workflow = repository.create(make_issue(), evidence_plan=plan)
    step = plan.steps[0]
    result = CollectorResult(
        workflow_id=workflow.id,
        step_id=step.step_id,
        collector=CollectorName.SYSTEM_INFO,
        status=CollectionStatus.SUCCESS,
        data={"platform": "synthetic"},
        warnings=["Synthetic collector completed."],
        duration_ms=1,
    )
    stored = repository.record_collector_result(result)
    assert stored.status == "success"
    assert stored.warnings == ["Synthetic collector completed."]
    assert not hasattr(stored, "data")


def test_collector_run_does_not_persist_evidence_data(session_factory) -> None:
    repository = WorkflowRepository(session_factory)
    plan = make_plan()
    workflow = repository.create(make_issue(), evidence_plan=plan)
    result = CollectorResult(
        workflow_id=workflow.id,
        step_id=plan.steps[0].step_id,
        collector=CollectorName.SYSTEM_INFO,
        status=CollectionStatus.SUCCESS,
        data={"platform": "synthetic"},
        duration_ms=1,
    )
    result.data = {"raw_evidence": "synthetic raw sample"}  # type: ignore[assignment]
    stored = repository.record_collector_result(result)
    assert stored.status == CollectionStatus.SUCCESS.value
    assert not hasattr(stored, "data")


def test_approval_create_lookup_and_one_time_finalization(session_factory) -> None:
    workflow = create_workflow(session_factory)
    workflow_id = UUID(workflow.id)
    request = ApprovalRequest(
        workflow_id=workflow_id,
        approval_type=ApprovalType.COLLECTION_PLAN,
        title="Review collection plan",
        explanation="Approve or reject the requested local collectors.",
    )
    repository = ApprovalRepository(session_factory)
    approval = repository.create(request)
    assert repository.get_pending(workflow_id)[0].id == str(request.approval_id)

    decision = PlanApprovalDecision(
        approval_id=request.approval_id,
        workflow_id=workflow_id,
        status=ApprovalStatus.APPROVED,
        approved_step_ids=[uuid4()],
    )
    finalized = repository.finalize(decision)
    assert finalized.status == ApprovalStatus.APPROVED.value
    with pytest.raises(ValueError):
        repository.finalize(decision)
    assert repository.get_pending(workflow_id) == []


def test_approval_rejects_external_release_of_blocked_artifact(session_factory) -> None:
    workflow = create_workflow(session_factory)
    workflow_id = UUID(workflow.id)
    artifact = make_artifact(
        workflow_id,
        sensitivity_status=SensitivityStatus.BLOCKED,
    )
    ArtifactRepository(session_factory).register(artifact)
    approval = ApprovalRequest(
        workflow_id=workflow_id,
        approval_type=ApprovalType.EVIDENCE_RELEASE,
        title="Review sanitized evidence",
        explanation="Select evidence excerpts for approved analysis.",
    )
    approvals = ApprovalRepository(session_factory)
    approvals.create(approval)
    decision = EvidenceReleaseDecision(
        approval_id=approval.approval_id,
        workflow_id=workflow_id,
        status=ApprovalStatus.APPROVED,
        item_decisions=[
            EvidenceItemDecision(
                artifact_id=artifact.artifact_id,
                decision=EvidenceItemDecisionType.INCLUDE,
                approved_excerpt_ids=[uuid4()],
            )
        ],
        allow_external_ai_analysis=True,
        reviewer_acknowledgement=True,
    )
    with pytest.raises(ValueError):
        approvals.finalize(decision)


def test_artifact_sensitivity_and_hash_updates(session_factory) -> None:
    workflow = create_workflow(session_factory)
    repository = ArtifactRepository(session_factory)
    artifact = make_artifact(workflow.id)
    registered = repository.register(artifact)

    assert repository.get(artifact.artifact_id).relative_path == "evidence/system.json"
    approved = repository.update_sensitivity(
        artifact.artifact_id,
        SensitivityStatus.APPROVED_FOR_EXTERNAL_ANALYSIS,
    )
    assert approved.sensitivity_status == SensitivityStatus.APPROVED_FOR_EXTERNAL_ANALYSIS.value
    hashed = repository.update_hash(artifact.artifact_id, "a" * 64)
    assert hashed.sha256 == "a" * 64
    assert registered.id == str(artifact.artifact_id)


def test_blocked_artifact_cannot_be_reclassified(session_factory) -> None:
    workflow = create_workflow(session_factory)
    artifact = make_artifact(
        workflow.id,
        sensitivity_status=SensitivityStatus.BLOCKED,
    )
    repository = ArtifactRepository(session_factory)
    repository.register(artifact)
    with pytest.raises(ValueError):
        repository.update_sensitivity(
            artifact.artifact_id,
            SensitivityStatus.APPROVED_FOR_EXTERNAL_ANALYSIS,
        )


def test_bundle_verification_and_release_rules(session_factory) -> None:
    workflow = create_workflow(session_factory)
    workflow_id = UUID(workflow.id)
    bundle = BundleMetadata(
        workflow_id=workflow_id,
        filename="support-bundle.zip",
        relative_path="bundles/support-bundle.zip",
        sanitized_only=True,
    )
    repository = BundleRepository(session_factory)
    repository.register(bundle)
    approval = ApprovalRequest(
        workflow_id=workflow_id,
        approval_type=ApprovalType.BUNDLE_RELEASE,
        title="Review bundle release",
        explanation="Approve release of the verified sanitized bundle.",
    )
    ApprovalRepository(session_factory).create(
        approval,
        bundle_id=bundle.bundle_id,
    )
    with pytest.raises(ValueError):
        repository.record_release(
            BundleReleaseDecision(
                approval_id=approval.approval_id,
                workflow_id=workflow_id,
                bundle_id=bundle.bundle_id,
                status=ApprovalStatus.APPROVED,
                reviewer_acknowledgement=True,
            )
        )

    verified = repository.update_verification(
        bundle.bundle_id,
        BundleVerificationStatus.VERIFIED,
    )
    assert verified.verified_at is not None
    decision = BundleReleaseDecision(
        approval_id=approval.approval_id,
        workflow_id=workflow_id,
        bundle_id=bundle.bundle_id,
        status=ApprovalStatus.APPROVED,
        reviewer_acknowledgement=True,
    )
    released = repository.record_release(decision)
    assert released.release_status == ApprovalStatus.APPROVED.value
    assert repository.get(bundle.bundle_id).released_at is not None
    assert repository.record_release(decision).release_status == (
        ApprovalStatus.APPROVED.value
    )


def test_relationships_and_metadata_cascade_do_not_delete_files(
    session_factory,
    tmp_path,
) -> None:
    workflow = create_workflow(session_factory, with_plan=True)
    workflow_id = UUID(workflow.id)
    artifact = make_artifact(workflow_id)
    ArtifactRepository(session_factory).register(artifact)
    bundle = BundleMetadata(
        workflow_id=workflow_id,
        filename="sample.zip",
        relative_path="bundles/sample.zip",
        sanitized_only=True,
    )
    BundleRepository(session_factory).register(bundle)

    diagnostic_file = tmp_path / "system.json"
    diagnostic_file.write_text("synthetic", encoding="utf-8")
    with session_scope(session_factory) as session:
        record = session.get(WorkflowRecord, workflow.id)
        assert record is not None
        assert len(record.evidence_steps) == 1
        assert len(record.artifacts) == 1
        assert len(record.bundles) == 1
        session.delete(record)

    with session_scope(session_factory) as session:
        for model in (
            EvidenceStepRecord,
            ApprovalRecord,
            CollectorRunRecord,
            ArtifactRecord,
            WorkflowEventRecord,
            BundleRecord,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert diagnostic_file.exists()


def test_orm_records_are_separate_from_pydantic_contracts() -> None:
    assert not issubclass(WorkflowRecord, BaseModel)
    assert not issubclass(ArtifactRecord, BaseModel)
    assert issubclass(IssueRequest, BaseModel)


def test_schema_has_no_sensitive_or_binary_columns(database_engine) -> None:
    inspector = inspect(database_engine)
    for table_name in inspector.get_table_names():
        column_names = {column["name"].lower() for column in inspector.get_columns(table_name)}
        assert "data" not in column_names
        assert not any(
            forbidden in column_name
            for forbidden in (
                "api_key",
                "raw_evidence",
                "binary",
                "stack_trace",
                "file_contents",
                "prompt",
                "command",
            )
            for column_name in column_names
        )
