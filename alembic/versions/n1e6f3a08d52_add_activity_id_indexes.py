"""index lap/split/activity_workout_step by activity_id, planned_workout_step by workout

These tables are looked up by activity_id alone (an activity's detail page, the insight
loader's per-activity subqueries), but their unique keys lead with athlete_id, so every such
lookup scanned the table. Measured on the real database: the insight loader's split subqueries
went from 660 ms to 12 ms per 1,907-activity pass.

Revision ID: n1e6f3a08d52
Revises: m0d5e2f97c41
Create Date: 2026-10-06 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n1e6f3a08d52"
down_revision: str | Sequence[str] | None = "m0d5e2f97c41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("ix_lap_activity", "lap", "activity_id"),
    ("ix_split_activity", "split", "activity_id"),
    ("ix_activity_workout_step_activity", "activity_workout_step", "activity_id"),
    ("ix_planned_workout_step_workout", "planned_workout_step", "planned_workout_id"),
)


def upgrade() -> None:
    for name, table, column in _INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _column in _INDEXES:
        op.drop_index(name, table_name=table)
