"""add kaya_dismissed_effort

Revision ID: o2f7a4b19e63
Revises: n1e6f3a08d52
Create Date: 2026-10-08 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o2f7a4b19e63"
down_revision: str | Sequence[str] | None = "n1e6f3a08d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kaya_dismissed_effort",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("activity_start_time_utc", sa.DateTime(), nullable=False),
        sa.Column("effort_start_time_utc", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "athlete_id",
            "activity_start_time_utc",
            "effort_start_time_utc",
            name="uq_kaya_dismissed_effort_identity",
        ),
    )


def downgrade() -> None:
    op.drop_table("kaya_dismissed_effort")
