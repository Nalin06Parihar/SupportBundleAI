"""Transactional repository for artifact metadata."""

from uuid import UUID

from sqlalchemy import select

from app.database.models import (
    ArtifactRecord,
    CollectorRunRecord,
    WorkflowRecord,
)
from app.models._common import (
    utc_now,
    validate_secret_free_text,
    validate_sha256,
    validate_relative_path,
)
from app.models.evidence import ArtifactMetadata, SensitivityStatus
from app.repositories._base import BaseRepository


class ArtifactRepository(BaseRepository):
    """Register and update artifact metadata without touching filesystem files."""

    def register(
        self,
        artifact: ArtifactMetadata,
        *,
        collector_run_id: UUID | None = None,
    ) -> ArtifactRecord:
        path = validate_relative_path(artifact.relative_path)
        validate_secret_free_text(artifact.name)
        validate_secret_free_text(artifact.media_type)
        now = utc_now()
        record = ArtifactRecord(
            id=str(artifact.artifact_id),
            workflow_id=str(artifact.workflow_id),
            collector_run_id=str(collector_run_id) if collector_run_id else None,
            name=artifact.name,
            artifact_type=artifact.artifact_type.value,
            relative_path=path,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
            sanitized=artifact.sanitized,
            sensitivity_status=artifact.sensitivity_status.value,
            sha256=validate_sha256(artifact.sha256),
            created_at=artifact.created_at,
            updated_at=now,
        )
        with self._session() as session:
            if session.get(WorkflowRecord, str(artifact.workflow_id)) is None:
                raise LookupError("Workflow was not found.")
            if collector_run_id is not None:
                run = session.get(CollectorRunRecord, str(collector_run_id))
                if run is None or run.workflow_id != str(artifact.workflow_id):
                    raise ValueError("Collector run does not belong to the workflow.")
            session.add(record)
            session.flush()
            session.expunge(record)
            return record

    def register_if_missing(
        self,
        artifact: ArtifactMetadata,
        *,
        collector_run_id: UUID | None = None,
    ) -> ArtifactRecord:
        """Register deterministic artifact metadata once across node retries."""
        existing = self.get(artifact.artifact_id)
        if existing is not None:
            if (
                existing.workflow_id != str(artifact.workflow_id)
                or existing.relative_path != artifact.relative_path
            ):
                raise ValueError("Artifact identifier is already associated with different metadata.")
            return existing
        return self.register(artifact, collector_run_id=collector_run_id)

    def get(self, artifact_id: UUID) -> ArtifactRecord | None:
        with self._session() as session:
            record = session.get(ArtifactRecord, str(artifact_id))
            if record is not None:
                session.expunge(record)
            return record

    def list_for_workflow(
        self,
        workflow_id: UUID,
        *,
        sensitivity_status: SensitivityStatus | None = None,
    ) -> list[ArtifactRecord]:
        statement = select(ArtifactRecord).where(
            ArtifactRecord.workflow_id == str(workflow_id)
        )
        if sensitivity_status is not None:
            statement = statement.where(
                ArtifactRecord.sensitivity_status == sensitivity_status.value
            )
        statement = statement.order_by(ArtifactRecord.created_at)
        with self._session() as session:
            records = list(session.scalars(statement))
            for record in records:
                session.expunge(record)
            return records

    def update_sensitivity(
        self,
        artifact_id: UUID,
        sensitivity_status: SensitivityStatus,
        *,
        sanitized: bool | None = None,
    ) -> ArtifactRecord:
        with self._session() as session:
            record = session.get(ArtifactRecord, str(artifact_id))
            if record is None:
                raise LookupError("Artifact was not found.")
            if record.sensitivity_status == SensitivityStatus.BLOCKED.value and (
                sensitivity_status != SensitivityStatus.BLOCKED
            ):
                raise ValueError("A blocked artifact cannot be reclassified.")
            if sanitized is not None:
                record.sanitized = sanitized
            if sensitivity_status in {
                SensitivityStatus.APPROVED_FOR_EXTERNAL_ANALYSIS,
                SensitivityStatus.APPROVED_FOR_BUNDLE,
            } and not record.sanitized:
                raise ValueError("Only sanitized artifacts may be approved.")
            record.sensitivity_status = sensitivity_status.value
            record.updated_at = utc_now()
            session.flush()
            session.expunge(record)
            return record

    def update_hash(self, artifact_id: UUID, sha256: str | None) -> ArtifactRecord:
        digest = validate_sha256(sha256)
        with self._session() as session:
            record = session.get(ArtifactRecord, str(artifact_id))
            if record is None:
                raise LookupError("Artifact was not found.")
            record.sha256 = digest
            record.updated_at = utc_now()
            session.flush()
            session.expunge(record)
            return record

    def source_collector_name(self, artifact: ArtifactRecord) -> str | None:
        """Resolve a sanitized artifact's source collector without exposing paths."""
        from uuid import uuid5

        from app.database.models import CollectorRunRecord

        workflow_artifacts = self.list_for_workflow(UUID(artifact.workflow_id))
        source = next(
            (
                item
                for item in workflow_artifacts
                if str(uuid5(UUID(item.id), "sanitized-v1")) == artifact.id
            ),
            artifact,
        )
        if source.collector_run_id is None:
            return None
        with self._session() as session:
            run = session.get(CollectorRunRecord, source.collector_run_id)
            if run is None or run.workflow_id != artifact.workflow_id:
                return None
            return run.collector
