"""add poster (year posters rendered in the background)

Revision ID: w0b5c2d97a41
Revises: v9a4b1c86f30
Create Date: 2026-10-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w0b5c2d97a41"
down_revision: str | Sequence[str] | None = "v9a4b1c86f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: one new table.
    op.create_table(
        "poster",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("activity_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_poster_athlete_year", "poster", ["athlete_id", "year"])


def downgrade() -> None:
    op.drop_index("ix_poster_athlete_year", table_name="poster")
    op.drop_table("poster")
