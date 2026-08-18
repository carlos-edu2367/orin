"""add provider_api_keys table for multi-key fallback per provider

Revision ID: 0038_provider_api_keys
Revises: 0037_plugin_commands_and_hooks
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_provider_api_keys"
down_revision = "0037_plugin_commands_and_hooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("api_key_ciphertext", sa.String(8192), nullable=False),
        sa.Column("secret_ref", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", "position", name="uq_provider_api_keys_user_provider_position"),
    )
    op.create_index("ix_provider_api_keys_user_provider", "provider_api_keys", ["user_id", "provider"])
    op.add_column("provider_configurations", sa.Column("key_cooldown_seconds", sa.Integer(), nullable=False, server_default="60"))
    op.execute(sa.text(
        "INSERT INTO provider_api_keys "
        "(user_id, provider, label, api_key_ciphertext, secret_ref, position, status, cooldown_until, created_at, updated_at) "
        "SELECT user_id, provider, NULL, api_key_ciphertext, secret_ref, 0, 'active', NULL, updated_at, updated_at "
        "FROM provider_configurations WHERE api_key_ciphertext IS NOT NULL"
    ))
    with op.batch_alter_table("provider_configurations") as batch:
        batch.drop_column("api_key")
        batch.drop_column("api_key_ciphertext")


def downgrade() -> None:
    with op.batch_alter_table("provider_configurations") as batch:
        batch.add_column(sa.Column("api_key", sa.String(4096), nullable=True))
        batch.add_column(sa.Column("api_key_ciphertext", sa.String(8192), nullable=True))
    op.execute(sa.text(
        "UPDATE provider_configurations SET api_key_ciphertext = ("
        "SELECT api_key_ciphertext FROM provider_api_keys "
        "WHERE provider_api_keys.user_id = provider_configurations.user_id "
        "AND provider_api_keys.provider = provider_configurations.provider "
        "AND provider_api_keys.position = 0)"
    ))
    with op.batch_alter_table("provider_configurations") as batch:
        batch.drop_column("key_cooldown_seconds")
    op.drop_index("ix_provider_api_keys_user_provider", table_name="provider_api_keys")
    op.drop_table("provider_api_keys")
