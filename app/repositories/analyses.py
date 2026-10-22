"""Idempotent persistence for safe structured analysis results."""

from uuid import UUID

from sqlalchemy import select, update

from app.database.models import AnalysisRecord, WorkflowRecord
from app.models.analysis import AnalysisResult
from app.models._common import utc_now
from app.repositories._base import BaseRepository


class AnalysisRepository(BaseRepository):
    """Reserve an analysis before provider work and persist its validated result."""

    def get(self, analysis_id: UUID) -> AnalysisRecord | None:
        with self._session() as session:
            record = session.get(AnalysisRecord, str(analysis_id))
            if record is not None:
                session.expunge(record)
            return record

    def reserve(self, analysis_id: UUID, workflow_id: UUID) -> tuple[AnalysisRecord, bool]:
        with self._session() as session:
            workflow = session.get(WorkflowRecord, str(workflow_id))
            if workflow is None:
                raise LookupError("Workflow was not found.")
            record = session.get(AnalysisRecord, str(analysis_id))
            if record is not None:
                if record.workflow_id != str(workflow_id):
                    raise ValueError("Analysis identifier belongs to another workflow.")
                session.expunge(record)
                return record, False
            record = AnalysisRecord(
                id=str(analysis_id),
                workflow_id=str(workflow_id),
                status="running",
                result=None,
                created_at=utc_now(),
                completed_at=None,
            )
            session.add(record)
            session.flush()
            session.expunge(record)
            return record, True

    def complete(self, analysis: AnalysisResult) -> AnalysisRecord:
        result = analysis.model_dump(mode="json")
        if any(
            reference.safe_excerpt is not None
            for finding in analysis.findings
            for reference in finding.evidence_references
        ):
            raise ValueError("Analysis persistence cannot contain excerpt contents.")
        with self._session() as session:
            record = session.get(AnalysisRecord, str(analysis.analysis_id))
            if record is None or record.workflow_id != str(analysis.workflow_id):
                raise LookupError("Analysis reservation was not found.")
            if record.status == "completed":
                if record.result != result:
                    raise ValueError("Analysis result was already persisted differently.")
                session.expunge(record)
                return record
            changed = session.execute(
                update(AnalysisRecord)
                .where(
                    AnalysisRecord.id == str(analysis.analysis_id),
                    AnalysisRecord.status == "running",
                )
                .values(
                    status="completed",
                    result=result,
                    completed_at=analysis.analyzed_at,
                )
            )
            if changed.rowcount != 1:
                raise ValueError("Analysis reservation has already been finalized.")
            session.flush()
            record = session.get(AnalysisRecord, str(analysis.analysis_id))
            assert record is not None
            session.expunge(record)
            return record

    def reserve_supplemental_cycle(self, workflow_id: UUID) -> int:
        """Atomically claim the only permitted supplemental cycle."""
        with self._session() as session:
            workflow = session.get(WorkflowRecord, str(workflow_id))
            if workflow is None:
                raise LookupError("Workflow was not found.")
            if workflow.supplemental_cycle_count == 1:
                return 1
            changed = session.execute(
                update(WorkflowRecord)
                .where(
                    WorkflowRecord.id == str(workflow_id),
                    WorkflowRecord.supplemental_cycle_count == 0,
                )
                .values(supplemental_cycle_count=1, updated_at=utc_now())
            )
            if changed.rowcount != 1:
                workflow = session.get(WorkflowRecord, str(workflow_id))
                assert workflow is not None
                if workflow.supplemental_cycle_count != 1:
                    raise ValueError("Supplemental cycle could not be reserved.")
            return 1
