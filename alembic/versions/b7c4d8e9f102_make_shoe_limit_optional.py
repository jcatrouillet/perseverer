"""make shoe limit optional and support retirement

Revision ID: b7c4d8e9f102
Revises: f18c746a3b21
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c4d8e9f102"
down_revision: str | Sequence[str] | None = "f18c746a3b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns("shoe")}
    if "retired_at" not in columns or not columns["max_distance_m"]["nullable"]:
        with op.batch_alter_table("shoe") as batch:
            if "retired_at" not in columns:
                batch.add_column(sa.Column("retired_at", sa.DateTime(), nullable=True))
            if not columns["max_distance_m"]["nullable"]:
                batch.alter_column("max_distance_m", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    # Retiring a pair is persistent user history, so downgrade deliberately leaves its data intact.
    pass
