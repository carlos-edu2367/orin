"""OAuth sign-in for remote MCP servers

Revision ID: 0045_mcp_oauth
Revises: 0044_memory_learning
"""
from alembic import op
import sqlalchemy as sa


revision = "0045_mcp_oauth"
down_revision = "0044_memory_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("auth_kind", sa.String(16), nullable=False, server_default="none"))
    # A server approved with a pasted credential keeps working exactly as before.
    # CAST keeps this valid on Postgres, whose json type has no equality operator.
    op.execute("UPDATE mcp_servers SET auth_kind = 'static' WHERE CAST(secret_names AS TEXT) NOT IN ('[]', 'null')")
    op.add_column("oauth_tokens", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("oauth_tokens", sa.Column("refresh_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("server_id", sa.String(255), primary_key=True),
        sa.Column("issuer", sa.String(2048), nullable=False),
        sa.Column("authorization_endpoint", sa.String(2048), nullable=False),
        sa.Column("token_endpoint", sa.String(2048), nullable=False),
        sa.Column("revocation_endpoint", sa.String(2048)),
        sa.Column("resource", sa.String(2048), nullable=False),
        sa.Column("scope", sa.String(1024)),
        sa.Column("client_id", sa.String(512), nullable=False),
        sa.Column("client_secret_ciphertext", sa.Text()),
        sa.Column("token_endpoint_auth_method", sa.String(32), nullable=False),
        sa.Column("redirect_uri", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.server_id"], name="fk_mcp_oauth_clients_server", ondelete="CASCADE"),
    )
    op.create_table(
        "oauth_pending_authorizations",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("server_id", sa.String(255), nullable=False),
        sa.Column("code_verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.server_id"], name="fk_oauth_pending_server", ondelete="CASCADE"),
    )
    op.create_index("ix_oauth_pending_server", "oauth_pending_authorizations", ["server_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_pending_server", table_name="oauth_pending_authorizations")
    op.drop_table("oauth_pending_authorizations")
    op.drop_table("mcp_oauth_clients")
    op.drop_column("oauth_tokens", "refresh_lease_until")
    op.drop_column("oauth_tokens", "version")
    op.drop_column("mcp_servers", "auth_kind")
