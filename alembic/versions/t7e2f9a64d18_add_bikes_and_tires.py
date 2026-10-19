"""add bikes, default bikes, tires, tire installations and the per-ride bike

Revision ID: t7e2f9a64d18
Revises: s6d1e8f53c07
Create Date: 2026-10-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "t7e2f9a64d18"
down_revision: str | Sequence[str] | None = "s6d1e8f53c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive: new tables and two nullable columns.
    op.create_table(
        "bike",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("brand", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("size", sa.String(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("initial_distance_m", sa.Float(), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bike_athlete", "bike", ["athlete_id"])
    op.create_table(
        "athlete_default_bike",
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("bike_id", sa.String(length=26), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.ForeignKeyConstraint(["bike_id"], ["bike.id"]),
        sa.PrimaryKeyConstraint("athlete_id", "category"),
    )
    op.create_index(
        "ix_default_bike_athlete_bike", "athlete_default_bike", ["athlete_id", "bike_id"]
    )
    op.create_table(
        "bike_tire",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("brand", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("size", sa.String(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("initial_distance_m", sa.Float(), nullable=False),
        sa.Column("max_distance_m", sa.Float(), nullable=True),
        sa.Column("alert_emailed_at", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bike_tire_athlete", "bike_tire", ["athlete_id"])
    op.create_table(
        "bike_tire_install",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("athlete_id", sa.String(), nullable=False),
        sa.Column("tire_id", sa.String(length=26), nullable=False),
        sa.Column("bike_id", sa.String(length=26), nullable=False),
        sa.Column("position", sa.String(), nullable=False),
        sa.Column("installed_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["athlete_id"], ["athlete.id"]),
        sa.ForeignKeyConstraint(["tire_id"], ["bike_tire.id"]),
        sa.ForeignKeyConstraint(["bike_id"], ["bike.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bike_tire_install_tire", "bike_tire_install", ["tire_id"])
    op.create_index("ix_bike_tire_install_bike", "bike_tire_install", ["bike_id"])
    with op.batch_alter_table("activity") as batch_op:
        batch_op.add_column(sa.Column("bike_id", sa.String(length=26), nullable=True))
        batch_op.create_foreign_key("fk_activity_bike", "bike", ["bike_id"], ["id"])
        batch_op.create_index("ix_activity_bike", ["bike_id"])
    with op.batch_alter_table("activity_sport_override") as batch_op:
        batch_op.add_column(sa.Column("bike_id", sa.String(length=26), nullable=True))
        batch_op.create_foreign_key("fk_activity_sport_override_bike", "bike", ["bike_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("activity_sport_override") as batch_op:
        batch_op.drop_constraint("fk_activity_sport_override_bike", type_="foreignkey")
        batch_op.drop_column("bike_id")
    with op.batch_alter_table("activity") as batch_op:
        batch_op.drop_index("ix_activity_bike")
        batch_op.drop_constraint("fk_activity_bike", type_="foreignkey")
        batch_op.drop_column("bike_id")
    op.drop_table("bike_tire_install")
    op.drop_table("bike_tire")
    op.drop_table("athlete_default_bike")
    op.drop_table("bike")
