"""allow several distance goals in one period (one per sport)

Revision ID: s6d1e8f53c07
Revises: r5c0d7e42b96
Create Date: 2026-10-19 08:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "s6d1e8f53c07"
down_revision: str | Sequence[str] | None = "r5c0d7e42b96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Relaxes a constraint, deletes nothing: the API now enforces one goal per period AND sport
    # (a NULL sport can't carry a SQL unique constraint), the way duration goals already do.
    with op.batch_alter_table("goal") as batch_op:
        batch_op.drop_constraint("uq_goal_identity", type_="unique")
    op.create_index(
        "ix_goal_athlete_period", "goal", ["athlete_id", "period_type", "period_start"]
    )


def downgrade() -> None:
    op.drop_index("ix_goal_athlete_period", table_name="goal")
    # Fails if a period now holds several goals: remove the extras first.
    with op.batch_alter_table("goal") as batch_op:
        batch_op.create_unique_constraint(
            "uq_goal_identity", ["athlete_id", "period_type", "period_start"]
        )
