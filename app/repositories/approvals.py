"""Transactional repository for approval requests and finalized decisions."""

from uuid import UUID

from sqlalchemy import or_, select, update

from app.database.models import ApprovalRecord, ArtifactRecord, BundleRecord
from app.models.approval import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
    BundleReleaseDecision,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)
from app.models._common import utc_now, validate_secret_free_text
from app.models.evidence import SensitivityStatus
from app.repositories._base import BaseRepository

ApprovalDecision = (
    PlanApprovalDecision | EvidenceReleaseDecision | BundleReleaseDecision
)


class ApprovalRepository(BaseRepository):
    """Create approval requests and finalize each request exactly once."""

    def create(
        self,
        request: ApprovalRequest,
        *,
        bundle_id: UUID | None = None,
    ) -> ApprovalRecord:
        if request.approval_type == ApprovalType.BUNDLE_RELEASE and bundle_id is None:
            raise ValueError("Bundle-release approvals require a bundle.")
        if request.approval_type != ApprovalType.BUNDLE_RELEASE and bundle_id is not None:
            raise ValueError("Only bundle-release approvals may reference a bundle.")
        if request.status != ApprovalStatus.PENDING:
            raise ValueError("Approval requests must start pending.")
        validate_secret_free_text(request.title)
        validate_secret_free_text(request.explanation)

        now = utc_now()
        record = ApprovalRecord(
            id=str(request.approval_id),
            workflow_id=str(request.workflow_id),
            bundle_id=str(bundle_id) if bundle_id else None,
            approval_type=request.approval_type.value,
            status=request.status.value,
            title=request.title,
            explanation=request.explanation,
            decision=None,
            requested_at=request.requested_at,
            expires_at=request.expires_at,
            decided_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._session() as session:
            session.add(record)
            session.flush()
            session.expunge(record)
            return record

    def get_pending(self, workflow_id: UUID) -> list[ApprovalRecord]:
        now = utc_now()
        with self._session() as session:
            records = list(
                session.scalars(
                    select(ApprovalRecord)
                    .where(
                        ApprovalRecord.workflow_id == str(workflow_id),
                        ApprovalRecord.status == ApprovalStatus.PENDING.value,
                        or_(
                            ApprovalRecord.expires_at.is_(None),
                            ApprovalRecord.expires_at > now,
                        ),
                    )
                    .order_by(ApprovalRecord.requested_at)
                )
            )
            for record in records:
                session.expunge(record)
            return records

    def finalize(self, decision: ApprovalDecision) -> ApprovalRecord:
        if decision.status == ApprovalStatus.PENDING:
            raise ValueError("A finalized decision cannot remain pending.")
        if isinstance(decision, PlanApprovalDecision):
            if decision.comment:
                validate_secret_free_text(decision.comment)
        elif isinstance(decision, BundleReleaseDecision):
            if decision.comment:
                validate_secret_free_text(decision.comment)
        else:
            for item in decision.item_decisions:
                if item.reviewer_comment:
                    validate_secret_free_text(item.reviewer_comment)
        now = utc_now()
        approval_id = str(decision.approval_id)
        with self._session() as session:
            statement = select(ApprovalRecord).where(
                ApprovalRecord.id == approval_id
            )
            record = session.scalar(statement)
            if record is None:
                raise LookupError("Approval request was not found.")
            if record.workflow_id != str(decision.workflow_id):
                raise ValueError("Approval decision workflow does not match.")
            if record.status != ApprovalStatus.PENDING.value:
                raise ValueError("Approval request has already been finalized.")

            self._validate_decision_type(record, decision)
            self._validate_release_evidence(session, decision)
            self._validate_bundle_release(session, record, decision)

            result = session.execute(
                update(ApprovalRecord)
                .where(
                    ApprovalRecord.id == approval_id,
                    ApprovalRecord.status == ApprovalStatus.PENDING.value,
                )
                .values(
                    status=decision.status.value,
                    decision=decision.model_dump(mode="json"),
                    decided_at=decision.decided_at,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise ValueError("Approval request has already been finalized.")

            record = session.get(ApprovalRecord, approval_id)
            assert record is not None
            if isinstance(decision, BundleReleaseDecision):
                bundle = session.get(BundleRecord, str(decision.bundle_id))
                assert bundle is not None
                bundle.release_status = decision.status.value
                bundle.release_decision = decision.model_dump(mode="json")
                bundle.released_at = (
                    decision.decided_at
                    if decision.status == ApprovalStatus.APPROVED
                    else None
                )
            session.flush()
            session.expunge(record)
            return record

    @staticmethod
    def _validate_decision_type(
        record: ApprovalRecord,
        decision: ApprovalDecision,
    ) -> None:
        expected = {
            ApprovalType.COLLECTION_PLAN.value: PlanApprovalDecision,
            ApprovalType.SUPPLEMENTAL_COLLECTION.value: PlanApprovalDecision,
            ApprovalType.EVIDENCE_RELEASE.value: EvidenceReleaseDecision,
            ApprovalType.BUNDLE_RELEASE.value: BundleReleaseDecision,
        }[record.approval_type]
        if not isinstance(decision, expected):
            raise ValueError("Approval decision type does not match its request.")
        if isinstance(decision, BundleReleaseDecision):
            if record.bundle_id != str(decision.bundle_id):
                raise ValueError("Bundle approval references a different bundle.")

    @staticmethod
    def _validate_release_evidence(session, decision: ApprovalDecision) -> None:
        if not isinstance(decision, EvidenceReleaseDecision):
            return
        if not decision.allow_external_ai_analysis:
            return
        for item in decision.item_decisions:
            if not item.approved_excerpt_ids:
                continue
            artifact = session.get(ArtifactRecord, str(item.artifact_id))
            if artifact is None:
                raise ValueError("Evidence release references an unknown artifact.")
            if (
                not artifact.sanitized
                or artifact.sensitivity_status == SensitivityStatus.BLOCKED.value
            ):
                raise ValueError("Blocked or unsanitized evidence cannot be released.")

    @staticmethod
    def _validate_bundle_release(
        session,
        record: ApprovalRecord,
        decision: ApprovalDecision,
    ) -> None:
        if not isinstance(decision, BundleReleaseDecision):
            return
        bundle = session.get(BundleRecord, str(decision.bundle_id))
        if bundle is None or record.bundle_id != bundle.id:
            raise ValueError("Bundle release references an unknown bundle.")
        if decision.status == ApprovalStatus.APPROVED and (
            bundle.verification_status != "verified" or not bundle.sanitized_only
        ):
            raise ValueError("Only verified sanitized bundles may be approved.")
