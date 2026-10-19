"""record the local date each email report last went out

Revision ID: v9a4b1c86f30
Revises: u8f3a0b75e29
Create Date: 2026-10-19 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v9a4b1c86f30"
down_revision: str | Sequence[str] | None = "u8f3a0b75e29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: two nullable columns.
    with op.batch_alter_table("athlete_email_report_config") as batch_op:
        batch_op.add_column(sa.Column("weekly_last_sent_on", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("monthly_last_sent_on", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("athlete_email_report_config") as batch_op:
        batch_op.drop_column("monthly_last_sent_on")
        batch_op.drop_column("weekly_last_sent_on")
