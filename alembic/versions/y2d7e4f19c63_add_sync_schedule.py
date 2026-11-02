"""add sync_schedule (per-source automatic sync times)

Revision ID: y2d7e4f19c63
Revises: x1c6d3e08b52
Create Date: 2026-10-30 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "y2d7e4f19c63"
down_revision: str | Sequence[str] | None = "x1c6d3e08b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: one new table; no row means the deployment's default time.
    op.create_table(
        "sync_schedule",
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("local_time", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_run_on", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("athlete_id", "source"),
    )


def downgrade() -> None:
    op.drop_table("sync_schedule")
