"""Persist safe analysis metadata and supplemental-cycle state."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_analysis"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "supplemental_cycle_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analyses_workflow_id", "analyses", ["workflow_id"])


def downgrade() -> None:
    op.drop_index("ix_analyses_workflow_id", table_name="analyses")
    op.drop_table("analyses")
    op.drop_column("workflows", "supplemental_cycle_count")
