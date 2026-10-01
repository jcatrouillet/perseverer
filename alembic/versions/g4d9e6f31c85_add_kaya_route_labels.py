"""add kaya route colour/wall and split.climb_name

Revision ID: g4d9e6f31c85
Revises: f3c8d5e20b74
Create Date: 2026-09-30 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g4d9e6f31c85"
down_revision: str | Sequence[str] | None = "f3c8d5e20b74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("kaya_ascent", "kaya_attempt"):
        op.add_column(table, sa.Column("climb_color", sa.String(), nullable=True))
        op.add_column(table, sa.Column("climb_wall", sa.String(), nullable=True))
    op.add_column("split", sa.Column("climb_name", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("split") as batch:
        batch.drop_column("climb_name")
    for table in ("kaya_attempt", "kaya_ascent"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("climb_wall")
            batch.drop_column("climb_color")
