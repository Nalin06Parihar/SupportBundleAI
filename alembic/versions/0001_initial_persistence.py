"""Create initial workflow persistence tables."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("issue_id", sa.String(length=36), nullable=False),
        sa.Column("issue_metadata", sa.JSON(), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=True),
        sa.Column("plan_status", sa.String(length=40), nullable=True),
        sa.Column("plan_summary", sa.String(length=1000), nullable=True),
        sa.Column("suspected_category", sa.String(length=100), nullable=True),
        sa.Column("plan_warnings", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("current_stage", sa.String(length=100), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issue_id"),
    )
    op.create_index("ix_workflows_status", "workflows", ["status"])

    op.create_table(
        "evidence_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("collector", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "position", name="uq_step_workflow_position"),
    )
    op.create_index("ix_evidence_steps_workflow_id", "evidence_steps", ["workflow_id"])
    op.create_index(
        "ix_evidence_steps_workflow_status",
        "evidence_steps",
        ["workflow_id", "status"],
    )

    op.create_table(
        "collector_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("step_id", sa.String(length=36), nullable=False),
        sa.Column("collector", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("collected_at", sa.String(length=40), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["step_id"], ["evidence_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_collector_runs_step_id", "collector_runs", ["step_id"])
    op.create_index("ix_collector_runs_status", "collector_runs", ["status"])
    op.create_index("ix_collector_runs_workflow_id", "collector_runs", ["workflow_id"])
    op.create_index(
        "ix_collector_runs_workflow_collected",
        "collector_runs",
        ["workflow_id", "collected_at"],
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("collector_run_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("artifact_type", sa.String(length=50), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sanitized", sa.Boolean(), nullable=False),
        sa.Column("sensitivity_status", sa.String(length=50), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["collector_run_id"], ["collector_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "relative_path", name="uq_artifact_workflow_path"),
    )
    op.create_index("ix_artifacts_collector_run_id", "artifacts", ["collector_run_id"])
    op.create_index("ix_artifacts_workflow_id", "artifacts", ["workflow_id"])
    op.create_index(
        "ix_artifacts_workflow_sensitivity",
        "artifacts",
        ["workflow_id", "sensitivity_status"],
    )

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_events_event_type", "workflow_events", ["event_type"])
    op.create_index("ix_workflow_events_workflow_id", "workflow_events", ["workflow_id"])
    op.create_index(
        "ix_workflow_events_workflow_created",
        "workflow_events",
        ["workflow_id", "created_at"],
    )

    op.create_table(
        "bundles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("manifest_relative_path", sa.String(length=500), nullable=True),
        sa.Column("verification_status", sa.String(length=30), nullable=False),
        sa.Column("sanitized_only", sa.Boolean(), nullable=False),
        sa.Column("release_status", sa.String(length=40), nullable=True),
        sa.Column("release_decision", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("verified_at", sa.String(length=40), nullable=True),
        sa.Column("released_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "relative_path", name="uq_bundle_workflow_path"),
    )
    op.create_index("ix_bundles_workflow_id", "bundles", ["workflow_id"])
    op.create_index(
        "ix_bundles_workflow_verification",
        "bundles",
        ["workflow_id", "verification_status"],
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("bundle_id", sa.String(length=36), nullable=True),
        sa.Column("approval_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("explanation", sa.String(length=1000), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=True),
        sa.Column("requested_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=True),
        sa.Column("decided_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["bundle_id"], ["bundles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_bundle_id", "approvals", ["bundle_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_workflow_id", "approvals", ["workflow_id"])
    op.create_index(
        "ix_approvals_workflow_status",
        "approvals",
        ["workflow_id", "status"],
    )
    op.create_index(
        "ix_approvals_type_status",
        "approvals",
        ["approval_type", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_approvals_type_status", table_name="approvals")
    op.drop_index("ix_approvals_workflow_status", table_name="approvals")
    op.drop_index("ix_approvals_workflow_id", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_bundle_id", table_name="approvals")
    op.drop_table("approvals")

    op.drop_index("ix_bundles_workflow_verification", table_name="bundles")
    op.drop_index("ix_bundles_workflow_id", table_name="bundles")
    op.drop_table("bundles")

    op.drop_index("ix_workflow_events_workflow_created", table_name="workflow_events")
    op.drop_index("ix_workflow_events_workflow_id", table_name="workflow_events")
    op.drop_index("ix_workflow_events_event_type", table_name="workflow_events")
    op.drop_table("workflow_events")

    op.drop_index("ix_artifacts_workflow_sensitivity", table_name="artifacts")
    op.drop_index("ix_artifacts_workflow_id", table_name="artifacts")
    op.drop_index("ix_artifacts_collector_run_id", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_collector_runs_workflow_collected", table_name="collector_runs")
    op.drop_index("ix_collector_runs_workflow_id", table_name="collector_runs")
    op.drop_index("ix_collector_runs_status", table_name="collector_runs")
    op.drop_index("ix_collector_runs_step_id", table_name="collector_runs")
    op.drop_table("collector_runs")

    op.drop_index("ix_evidence_steps_workflow_status", table_name="evidence_steps")
    op.drop_index("ix_evidence_steps_workflow_id", table_name="evidence_steps")
    op.drop_table("evidence_steps")

    op.drop_index("ix_workflows_status", table_name="workflows")
    op.drop_table("workflows")
