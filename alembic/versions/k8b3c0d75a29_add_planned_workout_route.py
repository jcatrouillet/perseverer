"""add planned_workout route (GPX) columns

Revision ID: k8b3c0d75a29
Revises: j7a2b9c64f18
Create Date: 2026-10-02 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k8b3c0d75a29"
down_revision: str | Sequence[str] | None = "j7a2b9c64f18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("planned_workout") as batch:
        batch.add_column(sa.Column("route_raw_object_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("route_name", sa.String(), nullable=True))
        batch.add_column(sa.Column("route_distance_m", sa.Float(), nullable=True))
        batch.add_column(sa.Column("route_elevation_gain_m", sa.Float(), nullable=True))
        batch.add_column(sa.Column("route_polyline", sa.Text(), nullable=True))
        batch.add_column(sa.Column("route_uploaded_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_planned_workout_route_raw_object", "raw_object", ["route_raw_object_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("planned_workout") as batch:
        batch.drop_constraint("fk_planned_workout_route_raw_object", type_="foreignkey")
        batch.drop_column("route_uploaded_at")
        batch.drop_column("route_polyline")
        batch.drop_column("route_elevation_gain_m")
        batch.drop_column("route_distance_m")
        batch.drop_column("route_name")
        batch.drop_column("route_raw_object_id")
