"""add kaya session and ascent tables

Revision ID: e2b7c4d19a63
Revises: d8a3f6b14e57
Create Date: 2026-09-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2b7c4d19a63"
down_revision: str | Sequence[str] | None = "d8a3f6b14e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kaya_session",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("kaya_id", sa.String(), nullable=False),
        sa.Column("start_time_utc", sa.DateTime(), nullable=False),
        sa.Column("end_time_utc", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("gym_name", sa.String(), nullable=True),
        sa.Column("gym_city", sa.String(), nullable=True),
        sa.Column("raw_object_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.ForeignKeyConstraint(["raw_object_id"], ["raw_object.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("athlete_id", "kaya_id", name="uq_kaya_session_identity"),
    )
    op.create_table(
        "kaya_ascent",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("kaya_id", sa.String(), nullable=False),
        sa.Column("session_kaya_id", sa.String(), nullable=False),
        sa.Column("date_utc", sa.DateTime(), nullable=False),
        sa.Column("ascent_type", sa.String(), nullable=True),
        sa.Column("grade_name", sa.String(), nullable=True),
        sa.Column("climb_kaya_id", sa.String(), nullable=True),
        sa.Column("climb_name", sa.String(), nullable=True),
        sa.Column("climb_type", sa.String(), nullable=True),
        sa.Column("is_lead", sa.Boolean(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("raw_object_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.ForeignKeyConstraint(["raw_object_id"], ["raw_object.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("athlete_id", "kaya_id", name="uq_kaya_ascent_identity"),
    )
    op.create_index("ix_kaya_ascent_session", "kaya_ascent", ["athlete_id", "session_kaya_id"])


def downgrade() -> None:
    op.drop_index("ix_kaya_ascent_session", table_name="kaya_ascent")
    op.drop_table("kaya_ascent")
    op.drop_table("kaya_session")
