"""add athlete.insights_sport

Revision ID: p3a8b5c20f74
Revises: o2f7a4b19e63
Create Date: 2026-10-18 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p3a8b5c20f74"
down_revision: str | Sequence[str] | None = "o2f7a4b19e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: every existing athlete opens the Insights page on running, as before.
    with op.batch_alter_table("athlete") as batch_op:
        batch_op.add_column(
            sa.Column("insights_sport", sa.String(), nullable=False, server_default="running")
        )


def downgrade() -> None:
    with op.batch_alter_table("athlete") as batch_op:
        batch_op.drop_column("insights_sport")
