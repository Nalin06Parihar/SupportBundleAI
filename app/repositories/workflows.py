"""Persistence operations for workflows, plans, collector runs, and events."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from app.database.models import (
    CollectorRunRecord,
    EvidenceStepRecord,
    WorkflowEventRecord,
    WorkflowRecord,
)
from app.models._common import (
    utc_now,
    validate_persistable_metadata,
    validate_secret_free_text,
    validate_warnings,
)
from app.models.evidence import CollectorResult
from app.models.issue import IssueRequest
from app.models.plan import EvidencePlan
from app.models.workflow import WorkflowEvent, WorkflowEventType, WorkflowStatus
from app.repositories._base import BaseRepository


class WorkflowRepository(BaseRepository):
    """Create, query, and update workflow metadata in separate transactions."""

    def create(
        self,
        issue: IssueRequest,
        *,
        workflow_id: UUID | None = None,
        issue_id: UUID | None = None,
        evidence_plan: EvidencePlan | None = None,
    ) -> WorkflowRecord:
        now = utc_now()
        issue_metadata = issue.model_dump(mode="json")
        for value in issue_metadata.values():
            if isinstance(value, str):
                validate_secret_free_text(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        validate_secret_free_text(item)
        if evidence_plan is not None:
            self._validate_plan_metadata(evidence_plan)
        workflow = WorkflowRecord(
            id=str(workflow_id or uuid4()),
            issue_id=str(issue_id or uuid4()),
            issue_metadata=issue_metadata,
            plan_id=None,
            plan_status=None,
            plan_summary=None,
            suspected_category=None,
            plan_warnings=[],
            status=WorkflowStatus.RECEIVED.value,
            current_stage=WorkflowStatus.RECEIVED.value,
            warning_count=0,
            created_at=now,
            updated_at=now,
        )
        with self._session() as session:
            session.add(workflow)
            if evidence_plan is not None:
                self._apply_plan(session, workflow, evidence_plan, now)
            session.add(
                WorkflowEventRecord(
                    id=str(uuid4()),
                    workflow_id=workflow.id,
                    event_type=WorkflowEventType.WORKFLOW_CREATED.value,
                    message="Workflow created.",
                    created_at=now,
                )
            )
            session.flush()
            session.expunge(workflow)
            return workflow

    def get(self, workflow_id: UUID) -> WorkflowRecord | None:
        with self._session() as session:
            workflow = session.get(WorkflowRecord, str(workflow_id))
            if workflow is not None:
                session.expunge(workflow)
            return workflow

    def list(
        self,
        *,
        status: WorkflowStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkflowRecord]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Workflow pagination is outside the allowed range.")
        statement = select(WorkflowRecord).order_by(WorkflowRecord.created_at.desc())
        if status is not None:
            statement = statement.where(WorkflowRecord.status == status.value)
        statement = statement.limit(limit).offset(offset)
        with self._session() as session:
            records = list(session.scalars(statement))
            for record in records:
                session.expunge(record)
            return records

    def update_status(
        self,
        workflow_id: UUID,
        status: WorkflowStatus,
        *,
        current_stage: str | None = None,
    ) -> WorkflowRecord:
        with self._session() as session:
            workflow = self._require_workflow(session, workflow_id)
            previous_status = workflow.status
            workflow.status = status.value
            workflow.current_stage = current_stage or status.value
            workflow.updated_at = utc_now()
            if status != WorkflowStatus.COMPLETED:
                workflow.completed_at = None
            if previous_status != status.value:
                session.add(
                    WorkflowEventRecord(
                        id=str(uuid4()),
                        workflow_id=workflow.id,
                        event_type=WorkflowEventType.STATUS_CHANGED.value,
                        message=f"Workflow status changed to {status.value}.",
                        created_at=workflow.updated_at,
                    )
                )
            session.flush()
            session.expunge(workflow)
            return workflow

    def complete(self, workflow_id: UUID) -> WorkflowRecord:
        now = utc_now()
        with self._session() as session:
            workflow = self._require_workflow(session, workflow_id)
            workflow.status = WorkflowStatus.COMPLETED.value
            workflow.current_stage = WorkflowStatus.COMPLETED.value
            workflow.error_code = None
            workflow.error_message = None
            workflow.completed_at = now
            workflow.updated_at = now
            session.add(
                WorkflowEventRecord(
                    id=str(uuid4()),
                    workflow_id=workflow.id,
                    event_type=WorkflowEventType.WORKFLOW_COMPLETED.value,
                    message="Workflow completed.",
                    created_at=now,
                )
            )
            session.flush()
            session.expunge(workflow)
            return workflow

    def fail(
        self,
        workflow_id: UUID,
        *,
        error_code: str,
        safe_message: str,
    ) -> WorkflowRecord:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,99}", error_code) is None:
            raise ValueError("Failure code must be a safe identifier.")
        if (
            not safe_message.strip()
            or len(safe_message) > 500
            or "\n" in safe_message
            or "\r" in safe_message
            or re.search(
                r"(?i)(traceback|stack trace|bearer\s+\S+|"
                r"[A-Z]:\\|\\\\[^\\]+\\|/[^ ]+/)",
                safe_message,
            )
        ):
            raise ValueError("Failure message must be safe for metadata storage.")
        validate_secret_free_text(safe_message)
        now = utc_now()
        with self._session() as session:
            workflow = self._require_workflow(session, workflow_id)
            workflow.status = WorkflowStatus.FAILED.value
            workflow.current_stage = WorkflowStatus.FAILED.value
            workflow.error_code = error_code
            workflow.error_message = safe_message.strip()
            workflow.completed_at = None
            workflow.updated_at = now
            session.add(
                WorkflowEventRecord(
                    id=str(uuid4()),
                    workflow_id=workflow.id,
                    event_type=WorkflowEventType.WORKFLOW_FAILED.value,
                    message="Workflow failed.",
                    created_at=now,
                )
            )
            session.flush()
            session.expunge(workflow)
            return workflow

    def save_plan(self, workflow_id: UUID, plan: EvidencePlan) -> WorkflowRecord:
        self._validate_plan_metadata(plan)
        now = utc_now()
        with self._session() as session:
            workflow = self._require_workflow(session, workflow_id)
            self._apply_plan(session, workflow, plan, now)
            workflow.updated_at = now
            session.add(
                WorkflowEventRecord(
                    id=str(uuid4()),
                    workflow_id=workflow.id,
                    event_type=WorkflowEventType.PLAN_GENERATED.value,
                    message="Evidence plan metadata saved.",
                    created_at=now,
                )
            )
            session.flush()
            session.expunge(workflow)
            return workflow

    def add_event(self, workflow_id: UUID, event: WorkflowEvent) -> WorkflowEventRecord:
        validate_secret_free_text(event.message)
        with self._session() as session:
            self._require_workflow(session, workflow_id)
            record = WorkflowEventRecord(
                id=str(event.event_id),
                workflow_id=str(workflow_id),
                event_type=event.event_type.value,
                message=event.message,
                created_at=event.created_at,
            )
            session.add(record)
            session.flush()
            session.expunge(record)
            return record

    def list_events(self, workflow_id: UUID) -> list[WorkflowEventRecord]:
        with self._session() as session:
            self._require_workflow(session, workflow_id)
            records = list(
                session.scalars(
                    select(WorkflowEventRecord)
                    .where(WorkflowEventRecord.workflow_id == str(workflow_id))
                    .order_by(WorkflowEventRecord.created_at, WorkflowEventRecord.id)
                )
            )
            for record in records:
                session.expunge(record)
            return records

    def list_steps(self, workflow_id: UUID) -> list[EvidenceStepRecord]:
        with self._session() as session:
            self._require_workflow(session, workflow_id)
            records = list(
                session.scalars(
                    select(EvidenceStepRecord)
                    .where(EvidenceStepRecord.workflow_id == str(workflow_id))
                    .order_by(EvidenceStepRecord.position)
                )
            )
            for record in records:
                session.expunge(record)
            return records

    def record_collector_result(self, result: CollectorResult) -> CollectorRunRecord:
        now = utc_now()
        validate_warnings(result.warnings)
        for warning in result.warnings:
            validate_secret_free_text(warning)
        if result.error is not None:
            validate_secret_free_text(result.error.message)
        with self._session() as session:
            self._require_workflow(session, result.workflow_id)
            step = session.get(EvidenceStepRecord, str(result.step_id))
            if step is None or step.workflow_id != str(result.workflow_id):
                raise ValueError("Collector result references an unknown workflow step.")
            if step.collector != result.collector.value:
                raise ValueError("Collector result does not match its approved step.")
            record = CollectorRunRecord(
                id=str(result.result_id),
                workflow_id=str(result.workflow_id),
                step_id=str(result.step_id),
                collector=result.collector.value,
                status=result.status.value,
                collected_at=result.collected_at,
                warnings=list(result.warnings),
                error=result.error.model_dump(mode="json") if result.error else None,
                duration_ms=result.duration_ms,
            )
            session.add(record)
            session.add(
                WorkflowEventRecord(
                    id=str(uuid4()),
                    workflow_id=str(result.workflow_id),
                    event_type=WorkflowEventType.COLLECTOR_COMPLETED.value,
                    message=f"Collector run finished with status {result.status.value}.",
                    created_at=now,
                )
            )
            session.flush()
            session.expunge(record)
            return record

    @staticmethod
    def _require_workflow(session, workflow_id: UUID) -> WorkflowRecord:
        workflow = session.get(WorkflowRecord, str(workflow_id))
        if workflow is None:
            raise LookupError("Workflow was not found.")
        return workflow

    @staticmethod
    def _validate_plan_metadata(plan: EvidencePlan) -> None:
        for value in (plan.issue_summary, plan.suspected_category, *plan.warnings):
            validate_secret_free_text(value)
        for step in plan.steps:
            validate_secret_free_text(step.reason)
            validate_persistable_metadata(step.parameters)

    @staticmethod
    def _apply_plan(
        session,
        workflow: WorkflowRecord,
        plan: EvidencePlan,
        now: datetime,
    ) -> None:
        statement = select(EvidenceStepRecord).where(
            EvidenceStepRecord.workflow_id == workflow.id
        )
        existing_steps = {
            record.id: record for record in session.scalars(statement)
        }
        workflow.plan_id = str(plan.plan_id)
        workflow.plan_status = plan.status.value
        workflow.plan_summary = plan.issue_summary
        workflow.suspected_category = plan.suspected_category
        workflow.plan_warnings = list(plan.warnings)
        position = max(
            (record.position for record in existing_steps.values()),
            default=-1,
        ) + 1
        for step in plan.steps:
            record = existing_steps.get(str(step.step_id))
            if record is None:
                record = EvidenceStepRecord(
                    id=str(step.step_id),
                    workflow_id=workflow.id,
                    position=position,
                    created_at=now,
                )
                position += 1
                session.add(record)
            record.collector = step.collector.value
            record.reason = step.reason
            record.parameters = step.parameters
            record.required = step.required
            record.status = step.status.value
            record.updated_at = now
