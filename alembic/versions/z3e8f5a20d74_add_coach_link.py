"""add coach_link (an athlete's coach)

Revision ID: z3e8f5a20d74
Revises: y2d7e4f19c63
Create Date: 2026-11-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "z3e8f5a20d74"
down_revision: str | Sequence[str] | None = "y2d7e4f19c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: one new table; no row means the athlete has no coach.
    op.create_table(
        "coach_link",
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("coach_athlete_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.ForeignKeyConstraint(["coach_athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("athlete_id"),
    )
    op.create_index("ix_coach_link_coach", "coach_link", ["coach_athlete_id"])


def downgrade() -> None:
    op.drop_index("ix_coach_link_coach", table_name="coach_link")
    op.drop_table("coach_link")
