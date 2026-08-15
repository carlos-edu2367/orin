"""persist MCP server configurations and their discovered tool cache

Revision ID: 0034_mcp_servers
Revises: 0033_scheduled_chats
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_mcp_servers"
down_revision = "0033_scheduled_chats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("server_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("catalog_id", sa.String(64), nullable=True),
        sa.Column("transport", sa.String(16), nullable=False),
        sa.Column("command", sa.String(512), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("secret_names", sa.JSON(), nullable=False),
        sa.Column("secrets_ciphertext", sa.Text(), nullable=True),
        sa.Column("tool_allowlist", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("state_reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("protocol_version", sa.String(32), nullable=False, server_default=""),
        sa.Column("tools_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "slug", name="uq_mcp_servers_slug"),
    )
    op.create_index("ix_mcp_servers_user", "mcp_servers", ["user_id", "state"])
    op.create_table(
        "mcp_server_tools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("server_id", "name", name="uq_mcp_server_tools_ref"),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.server_id"], name="fk_mcp_tools_server", ondelete="CASCADE"),
    )
    op.create_index("ix_mcp_server_tools_server", "mcp_server_tools", ["server_id", "enabled"])


def downgrade() -> None:
    op.drop_index("ix_mcp_server_tools_server", table_name="mcp_server_tools")
    op.drop_table("mcp_server_tools")
    op.drop_index("ix_mcp_servers_user", table_name="mcp_servers")
    op.drop_table("mcp_servers")
