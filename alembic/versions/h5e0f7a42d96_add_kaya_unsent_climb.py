"""add kaya unsent climb attempts table

Revision ID: h5e0f7a42d96
Revises: g4d9e6f31c85
Create Date: 2026-09-30 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h5e0f7a42d96"
down_revision: str | Sequence[str] | None = "g4d9e6f31c85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kaya_unsent_climb",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("climb_kaya_id", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("raw_object_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.ForeignKeyConstraint(["raw_object_id"], ["raw_object.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("athlete_id", "climb_kaya_id", name="uq_kaya_unsent_climb_identity"),
    )


def downgrade() -> None:
    op.drop_table("kaya_unsent_climb")
