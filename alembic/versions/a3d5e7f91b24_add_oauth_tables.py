"""add OAuth authorization-server tables for the MCP endpoint

Revision ID: a3d5e7f91b24
Revises: b7c4d8e9f102
Create Date: 2026-09-25

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3d5e7f91b24"
down_revision: str | Sequence[str] | None = "b7c4d8e9f102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_client",
        sa.Column("client_id", sa.String(), primary_key=True),
        sa.Column("client_info_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "oauth_authorization_code",
        sa.Column("code_hash", sa.String(), primary_key=True),
        sa.Column("athlete_id", sa.String(), sa.ForeignKey("athlete.id"), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "oauth_token",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("athlete_id", sa.String(), sa.ForeignKey("athlete.id"), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("grant_id", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_oauth_token_grant", "oauth_token", ["grant_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_token_grant", table_name="oauth_token")
    op.drop_table("oauth_token")
    op.drop_table("oauth_authorization_code")
    op.drop_table("oauth_client")
