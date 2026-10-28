"""athlete map style (route map background)

Revision ID: x1c6d3e08b52
Revises: w0b5c2d97a41
Create Date: 2026-10-28 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "x1c6d3e08b52"
down_revision: str | Sequence[str] | None = "w0b5c2d97a41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: one display-preference column, "light" for everyone already there.
    with op.batch_alter_table("athlete") as batch:
        batch.add_column(
            sa.Column("map_style", sa.String(), nullable=False, server_default="light")
        )


def downgrade() -> None:
    with op.batch_alter_table("athlete") as batch:
        batch.drop_column("map_style")
