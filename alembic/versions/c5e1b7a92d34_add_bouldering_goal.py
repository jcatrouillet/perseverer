"""add bouldering goal table

Revision ID: c5e1b7a92d34
Revises: af2a72d8334e
Create Date: 2026-09-27 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e1b7a92d34"
down_revision: str | Sequence[str] | None = "af2a72d8334e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bouldering_goal",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("period_type", sa.String(), nullable=False),
        sa.Column("period_start", sa.String(), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=True),
        sa.Column("and_harder", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bouldering_goal_athlete_period",
        "bouldering_goal",
        ["athlete_id", "period_type", "period_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_bouldering_goal_athlete_period", table_name="bouldering_goal")
    op.drop_table("bouldering_goal")
