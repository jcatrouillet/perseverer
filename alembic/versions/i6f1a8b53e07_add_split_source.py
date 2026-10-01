"""add split.source

Revision ID: i6f1a8b53e07
Revises: h5e0f7a42d96
Create Date: 2026-10-01 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i6f1a8b53e07"
down_revision: str | Sequence[str] | None = "h5e0f7a42d96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("split", sa.Column("source", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("split") as batch:
        batch.drop_column("source")
