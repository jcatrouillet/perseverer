"""add kaya attempt table

Revision ID: f3c8d5e20b74
Revises: e2b7c4d19a63
Create Date: 2026-09-30 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3c8d5e20b74"
down_revision: str | Sequence[str] | None = "e2b7c4d19a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kaya_attempt",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("kaya_id", sa.String(), nullable=False),
        sa.Column("session_kaya_id", sa.String(), nullable=False),
        sa.Column("climb_kaya_id", sa.String(), nullable=True),
        sa.Column("climb_name", sa.String(), nullable=True),
        sa.Column("grade_name", sa.String(), nullable=True),
        sa.Column("climb_type", sa.String(), nullable=True),
        sa.Column("is_lead", sa.Boolean(), nullable=True),
        sa.Column("raw_object_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.ForeignKeyConstraint(["raw_object_id"], ["raw_object.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("athlete_id", "kaya_id", name="uq_kaya_attempt_identity"),
    )
    op.create_index("ix_kaya_attempt_session", "kaya_attempt", ["athlete_id", "session_kaya_id"])


def downgrade() -> None:
    op.drop_index("ix_kaya_attempt_session", table_name="kaya_attempt")
    op.drop_table("kaya_attempt")
