"""SQLAlchemy records for workflow and diagnostic metadata only."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, UTCDateTime


class WorkflowRecord(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    issue_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(36))
    plan_status: Mapped[str | None] = mapped_column(String(40))
    plan_summary: Mapped[str | None] = mapped_column(String(1000))
    suspected_category: Mapped[str | None] = mapped_column(String(100))
    plan_warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    current_stage: Mapped[str] = mapped_column(String(100), nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    evidence_steps: Mapped[list["EvidenceStepRecord"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EvidenceStepRecord.position",
    )
    approvals: Mapped[list["ApprovalRecord"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    collector_runs: Mapped[list["CollectorRunRecord"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    artifacts: Mapped[list["ArtifactRecord"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    events: Mapped[list["WorkflowEventRecord"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WorkflowEventRecord.created_at",
    )
    bundles: Mapped[list["BundleRecord"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EvidenceStepRecord(Base):
    __tablename__ = "evidence_steps"
    __table_args__ = (
        UniqueConstraint("workflow_id", "position", name="uq_step_workflow_position"),
        Index("ix_evidence_steps_workflow_status", "workflow_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    collector: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    workflow: Mapped[WorkflowRecord] = relationship(back_populates="evidence_steps")
    collector_runs: Mapped[list["CollectorRunRecord"]] = relationship(
        back_populates="evidence_step",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_workflow_status", "workflow_id", "status"),
        Index("ix_approvals_type_status", "approval_type", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bundle_id: Mapped[str | None] = mapped_column(
        ForeignKey("bundles.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    approval_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    explanation: Mapped[str] = mapped_column(String(1000), nullable=False)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    workflow: Mapped[WorkflowRecord] = relationship(back_populates="approvals")
    bundle: Mapped["BundleRecord | None"] = relationship(back_populates="approvals")


class CollectorRunRecord(Base):
    __tablename__ = "collector_runs"
    __table_args__ = (
        Index("ix_collector_runs_workflow_collected", "workflow_id", "collected_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_steps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    collector: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    error: Mapped[dict[str, str] | None] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    workflow: Mapped[WorkflowRecord] = relationship(back_populates="collector_runs")
    evidence_step: Mapped[EvidenceStepRecord] = relationship(
        back_populates="collector_runs"
    )
    artifacts: Mapped[list["ArtifactRecord"]] = relationship(
        back_populates="collector_run",
        passive_deletes=True,
    )


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("workflow_id", "relative_path", name="uq_artifact_workflow_path"),
        Index("ix_artifacts_workflow_sensitivity", "workflow_id", "sensitivity_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    collector_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("collector_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sanitized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sensitivity_status: Mapped[str] = mapped_column(String(50), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    workflow: Mapped[WorkflowRecord] = relationship(back_populates="artifacts")
    collector_run: Mapped[CollectorRunRecord | None] = relationship(
        back_populates="artifacts"
    )


class WorkflowEventRecord(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        Index("ix_workflow_events_workflow_created", "workflow_id", "created_at"),
        Index("ix_workflow_events_event_type", "event_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    workflow: Mapped[WorkflowRecord] = relationship(back_populates="events")


class BundleRecord(Base):
    __tablename__ = "bundles"
    __table_args__ = (
        UniqueConstraint("workflow_id", "relative_path", name="uq_bundle_workflow_path"),
        Index("ix_bundles_workflow_verification", "workflow_id", "verification_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    manifest_relative_path: Mapped[str | None] = mapped_column(String(500))
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False)
    sanitized_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    release_status: Mapped[str | None] = mapped_column(String(40))
    release_decision: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    workflow: Mapped[WorkflowRecord] = relationship(back_populates="bundles")
    approvals: Mapped[list[ApprovalRecord]] = relationship(back_populates="bundle")
