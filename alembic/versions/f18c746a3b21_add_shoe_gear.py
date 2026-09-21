"""add shoe gear and default-per-sport assignment

Revision ID: f18c746a3b21
Revises: 9861c92c896b
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f18c746a3b21"
down_revision: str | Sequence[str] | None = "9861c92c896b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shoe",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("brand", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("size", sa.String(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("initial_distance_m", sa.Float(), nullable=False),
        sa.Column("max_distance_m", sa.Float(), nullable=True),
        sa.Column("alert_emailed_at", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shoe_athlete", "shoe", ["athlete_id"])
    op.create_table(
        "athlete_default_shoe",
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("sport", sa.String(), nullable=False),
        sa.Column("shoe_id", sa.String(length=26), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.ForeignKeyConstraint(["shoe_id"], ["shoe.id"]),
        sa.PrimaryKeyConstraint("athlete_id", "sport"),
    )
    op.create_index(
        "ix_default_shoe_athlete_shoe", "athlete_default_shoe", ["athlete_id", "shoe_id"]
    )
    op.add_column("activity", sa.Column("shoe_id", sa.String(length=26), nullable=True))
    op.create_index("ix_activity_shoe", "activity", ["shoe_id"])


def downgrade() -> None:
    op.drop_index("ix_activity_shoe", table_name="activity")
    op.drop_column("activity", "shoe_id")
    op.drop_index("ix_default_shoe_athlete_shoe", table_name="athlete_default_shoe")
    op.drop_table("athlete_default_shoe")
    op.drop_index("ix_shoe_athlete", table_name="shoe")
    op.drop_table("shoe")
