"""add activity.is_virtual, and its durable override

Revision ID: r5c0d7e42b96
Revises: q4b9c6d31a85
Create Date: 2026-10-18 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r5c0d7e42b96"
down_revision: str | Sequence[str] | None = "q4b9c6d31a85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: every existing activity is "not virtual".
    with op.batch_alter_table("activity") as batch_op:
        batch_op.add_column(
            sa.Column("is_virtual", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
    with op.batch_alter_table("activity_sport_override") as batch_op:
        batch_op.add_column(sa.Column("is_virtual", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("activity_sport_override") as batch_op:
        batch_op.drop_column("is_virtual")
    with op.batch_alter_table("activity") as batch_op:
        batch_op.drop_column("is_virtual")
