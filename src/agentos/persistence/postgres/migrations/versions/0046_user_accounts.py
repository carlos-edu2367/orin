"""User accounts, first-admin setup, sign-in attempts and session expiry

Revision ID: 0046_user_accounts
Revises: 0045_mcp_oauth
"""
from alembic import op
import sqlalchemy as sa


revision = "0046_user_accounts"
down_revision = "0045_mcp_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_users_role"),
    )
    op.create_table(
        "instance_setup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(320), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_login_attempts_key_time", "auth_login_attempts", ["key", "occurred_at"])
    with op.batch_alter_table("security_sessions") as batch:
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("security_sessions") as batch:
        batch.drop_column("last_seen_at")
        batch.drop_column("expires_at")
    op.drop_index("ix_auth_login_attempts_key_time", table_name="auth_login_attempts")
    op.drop_table("auth_login_attempts")
    op.drop_table("instance_setup")
    op.drop_table("users")
