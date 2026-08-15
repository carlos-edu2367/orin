"""persist encrypted OAuth tokens for the generic OAuth broker

Revision ID: 0036_oauth_tokens
Revises: 0035_plugins
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_oauth_tokens"
down_revision = "0035_plugins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("oauth_tokens",
        sa.Column("user_id", sa.String(255), primary_key=True), sa.Column("provider_id", sa.String(64), primary_key=True),
        sa.Column("access_token_ciphertext", sa.Text(), nullable=False), sa.Column("refresh_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(1024), nullable=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_table("oauth_tokens")
