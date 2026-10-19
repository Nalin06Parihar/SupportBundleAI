"""Transactional repository for candidate bundle metadata and release."""

from uuid import UUID

from sqlalchemy import select

from app.database.models import ApprovalRecord, BundleRecord, WorkflowRecord
from app.models._common import utc_now, validate_relative_path
from app.models.approval import (
    ApprovalStatus,
    ApprovalType,
    BundleReleaseDecision,
)
from app.models._common import validate_secret_free_text
from app.models.bundle import BundleMetadata, BundleVerificationStatus
from app.repositories._base import BaseRepository


class BundleRepository(BaseRepository):
    """Register bundles, record verification, and gate release metadata."""

    def register(self, bundle: BundleMetadata) -> BundleRecord:
        record = BundleRecord(
            id=str(bundle.bundle_id),
            workflow_id=str(bundle.workflow_id),
            filename=bundle.filename,
            relative_path=validate_relative_path(bundle.relative_path),
            manifest_relative_path=(
                validate_relative_path(bundle.manifest_relative_path)
                if bundle.manifest_relative_path
                else None
            ),
            verification_status=bundle.verification_status.value,
            sanitized_only=bundle.sanitized_only,
            release_status=None,
            release_decision=None,
            created_at=bundle.created_at,
            verified_at=(
                bundle.created_at
                if bundle.verification_status == BundleVerificationStatus.VERIFIED
                else None
            ),
            released_at=None,
        )
        with self._session() as session:
            if session.get(WorkflowRecord, str(bundle.workflow_id)) is None:
                raise LookupError("Workflow was not found.")
            session.add(record)
            session.flush()
            session.expunge(record)
            return record

    def get(self, bundle_id: UUID) -> BundleRecord | None:
        with self._session() as session:
            record = session.get(BundleRecord, str(bundle_id))
            if record is not None:
                session.expunge(record)
            return record

    def list_for_workflow(self, workflow_id: UUID) -> list[BundleRecord]:
        with self._session() as session:
            records = list(
                session.scalars(
                    select(BundleRecord)
                    .where(BundleRecord.workflow_id == str(workflow_id))
                    .order_by(BundleRecord.created_at)
                )
            )
            for record in records:
                session.expunge(record)
            return records

    def update_verification(
        self,
        bundle_id: UUID,
        status: BundleVerificationStatus,
    ) -> BundleRecord:
        now = utc_now()
        with self._session() as session:
            record = session.get(BundleRecord, str(bundle_id))
            if record is None:
                raise LookupError("Bundle was not found.")
            if status == BundleVerificationStatus.VERIFIED and not record.sanitized_only:
                raise ValueError("Only sanitized-only bundles may be verified.")
            if record.release_status == ApprovalStatus.APPROVED.value and (
                status != BundleVerificationStatus.VERIFIED
            ):
                raise ValueError("A released bundle cannot lose verification.")
            record.verification_status = status.value
            record.verified_at = (
                now if status == BundleVerificationStatus.VERIFIED else None
            )
            session.flush()
            session.expunge(record)
            return record

    def record_release(self, decision: BundleReleaseDecision) -> BundleRecord:
        if decision.status == ApprovalStatus.PENDING:
            raise ValueError("A bundle release decision cannot remain pending.")
        if decision.comment:
            validate_secret_free_text(decision.comment)
        now = utc_now()
        with self._session() as session:
            bundle = session.get(BundleRecord, str(decision.bundle_id))
            if bundle is None or bundle.workflow_id != str(decision.workflow_id):
                raise LookupError("Bundle was not found for this workflow.")
            if bundle.release_status is not None:
                raise ValueError("Bundle release has already been recorded.")
            if decision.status == ApprovalStatus.APPROVED and (
                bundle.verification_status != BundleVerificationStatus.VERIFIED.value
                or not bundle.sanitized_only
            ):
                raise ValueError("Only verified sanitized bundles may be released.")
            approval = session.get(ApprovalRecord, str(decision.approval_id))
            if (
                approval is None
                or approval.workflow_id != bundle.workflow_id
                or approval.bundle_id != bundle.id
                or approval.approval_type != ApprovalType.BUNDLE_RELEASE.value
            ):
                raise LookupError("Pending bundle-release approval was not found.")
            if approval.status != ApprovalStatus.PENDING.value:
                raise ValueError("Bundle-release approval has already been finalized.")

            approval.status = decision.status.value
            approval.decision = decision.model_dump(mode="json")
            approval.decided_at = decision.decided_at
            approval.updated_at = now
            bundle.release_status = decision.status.value
            bundle.release_decision = decision.model_dump(mode="json")
            bundle.released_at = (
                decision.decided_at
                if decision.status == ApprovalStatus.APPROVED
                else None
            )
            session.flush()
            session.expunge(bundle)
            return bundle
