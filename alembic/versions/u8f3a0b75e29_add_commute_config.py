"""add athlete_commute_config (home and work addresses for commute detection)

Revision ID: u8f3a0b75e29
Revises: t7e2f9a64d18
Create Date: 2026-10-19 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "u8f3a0b75e29"
down_revision: str | Sequence[str] | None = "t7e2f9a64d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: one new table, nothing else touched.
    op.create_table(
        "athlete_commute_config",
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("home_address", sa.String(), nullable=True),
        sa.Column("home_label", sa.String(), nullable=True),
        sa.Column("home_lat", sa.Float(), nullable=True),
        sa.Column("home_lon", sa.Float(), nullable=True),
        sa.Column("work_address", sa.String(), nullable=True),
        sa.Column("work_label", sa.String(), nullable=True),
        sa.Column("work_lat", sa.Float(), nullable=True),
        sa.Column("work_lon", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("athlete_id"),
    )


def downgrade() -> None:
    op.drop_table("athlete_commute_config")
