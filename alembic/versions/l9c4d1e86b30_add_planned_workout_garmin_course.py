"""add planned_workout garmin course columns

Revision ID: l9c4d1e86b30
Revises: k8b3c0d75a29
Create Date: 2026-10-03 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l9c4d1e86b30"
down_revision: str | Sequence[str] | None = "k8b3c0d75a29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("planned_workout", sa.Column("garmin_course_id", sa.Integer(), nullable=True))
    op.add_column(
        "planned_workout", sa.Column("garmin_course_pushed_at", sa.DateTime(), nullable=True)
    )
    op.add_column("planned_workout", sa.Column("garmin_course_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("planned_workout") as batch:
        batch.drop_column("garmin_course_error")
        batch.drop_column("garmin_course_pushed_at")
        batch.drop_column("garmin_course_id")
