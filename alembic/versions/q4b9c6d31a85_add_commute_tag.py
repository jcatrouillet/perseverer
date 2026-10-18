"""add activity.is_commute, its durable override and athlete.commute_filter

Revision ID: q4b9c6d31a85
Revises: p3a8b5c20f74
Create Date: 2026-10-18 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q4b9c6d31a85"
down_revision: str | Sequence[str] | None = "p3a8b5c20f74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: every existing activity is "not a commute", every athlete has the filter off.
    with op.batch_alter_table("activity") as batch_op:
        batch_op.add_column(
            sa.Column("is_commute", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
    with op.batch_alter_table("activity_sport_override") as batch_op:
        batch_op.add_column(sa.Column("is_commute", sa.Boolean(), nullable=True))
    with op.batch_alter_table("athlete") as batch_op:
        batch_op.add_column(
            sa.Column("commute_filter", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("athlete") as batch_op:
        batch_op.drop_column("commute_filter")
    with op.batch_alter_table("activity_sport_override") as batch_op:
        batch_op.drop_column("is_commute")
    with op.batch_alter_table("activity") as batch_op:
        batch_op.drop_column("is_commute")
