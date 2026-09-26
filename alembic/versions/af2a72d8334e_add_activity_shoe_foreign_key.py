"""add activity shoe foreign key

Revision ID: af2a72d8334e
Revises: a3d5e7f91b24
Create Date: 2026-09-21 22:18:41.340523

"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "af2a72d8334e"
down_revision: str | Sequence[str] | None = "a3d5e7f91b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite needs batch mode to add the foreign key to its existing activity table. The shoe
    # column and lookup index already exist from f18c746a3b21; preserve that index while the
    # table is rebuilt.
    with op.batch_alter_table("activity") as batch:
        batch.create_foreign_key("fk_activity_shoe_id", "shoe", ["shoe_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("activity") as batch:
        batch.drop_constraint("fk_activity_shoe_id", type_="foreignkey")
