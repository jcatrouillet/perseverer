"""add kaya_climb_note and split.climb_kaya_id

Revision ID: m0d5e2f97c41
Revises: l9c4d1e86b30
Create Date: 2026-10-04 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m0d5e2f97c41"
down_revision: str | Sequence[str] | None = "l9c4d1e86b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("split", sa.Column("climb_kaya_id", sa.String(), nullable=True))
    op.create_table(
        "kaya_climb_note",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("climb_kaya_id", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("athlete_id", "climb_kaya_id", name="uq_kaya_climb_note_identity"),
    )


def downgrade() -> None:
    op.drop_table("kaya_climb_note")
    with op.batch_alter_table("split") as batch:
        batch.drop_column("climb_kaya_id")
