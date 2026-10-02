"""add split.garmin_grade / garmin_result

Revision ID: j7a2b9c64f18
Revises: i6f1a8b53e07
Create Date: 2026-10-01 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j7a2b9c64f18"
down_revision: str | Sequence[str] | None = "i6f1a8b53e07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("split", sa.Column("garmin_grade", sa.Integer(), nullable=True))
    op.add_column("split", sa.Column("garmin_result", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("split") as batch:
        batch.drop_column("garmin_result")
        batch.drop_column("garmin_grade")
