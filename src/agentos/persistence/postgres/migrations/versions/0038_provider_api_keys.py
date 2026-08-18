"""add provider_api_keys table for multi-key fallback per provider

Revision ID: 0038_provider_api_keys
Revises: 0037_plugin_commands_and_hooks
"""
from datetime import UTC, datetime
from uuid import uuid4

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
    # A row saved before field-level encryption existed (or that predates
    # migration 0022 and was never read through a chat turn since, which used
    # to lazily encrypt it on read via workers/chat.py's now-removed
    # _credential_value) still holds its key in the legacy plaintext `api_key`
    # column with `api_key_ciphertext` NULL. The INSERT above skips it; without
    # this pass, the columns get dropped a few lines down and the credential is
    # gone for good. Encryption is a Python-level operation (Fernet, via
    # ProviderSecretCipher), so this half of the backfill runs row-by-row here
    # instead of as a single INSERT...SELECT.
    from agentos.persistence.provider_secrets import ProviderSecretCipher

    connection = op.get_bind()
    legacy_rows = connection.execute(sa.text(
        "SELECT user_id, provider, secret_ref, api_key FROM provider_configurations "
        "WHERE api_key_ciphertext IS NULL AND api_key IS NOT NULL AND api_key != ''"
    )).fetchall()
    if legacy_rows:
        cipher = ProviderSecretCipher.from_environment(required=True)
        now = datetime.now(UTC)
        for row in legacy_rows:
            connection.execute(
                sa.text(
                    "INSERT INTO provider_api_keys "
                    "(user_id, provider, label, api_key_ciphertext, secret_ref, position, status, cooldown_until, created_at, updated_at) "
                    "VALUES (:user_id, :provider, NULL, :ciphertext, :secret_ref, 0, 'active', NULL, :now, :now)"
                ),
                {
                    "user_id": row.user_id, "provider": row.provider, "secret_ref": row.secret_ref,
                    "ciphertext": cipher.encrypt(str(row.api_key)), "now": now,
                },
            )
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
