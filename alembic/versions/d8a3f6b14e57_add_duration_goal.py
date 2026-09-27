"""add duration goal table

Revision ID: d8a3f6b14e57
Revises: c5e1b7a92d34
Create Date: 2026-09-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8a3f6b14e57"
down_revision: str | Sequence[str] | None = "c5e1b7a92d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "duration_goal",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("period_type", sa.String(), nullable=False),
        sa.Column("period_start", sa.String(), nullable=False),
        sa.Column("sport", sa.String(), nullable=True),
        sa.Column("target_duration_s", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_duration_goal_athlete_period",
        "duration_goal",
        ["athlete_id", "period_type", "period_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_duration_goal_athlete_period", table_name="duration_goal")
    op.drop_table("duration_goal")
