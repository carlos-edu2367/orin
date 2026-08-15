"""persist installed plugins and their contributions

Revision ID: 0035_plugins
Revises: 0034_mcp_servers
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_plugins"
down_revision = "0034_mcp_servers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("plugins",
        sa.Column("plugin_id", sa.String(64), primary_key=True), sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False), sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""), sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column("homepage", sa.String(512)), sa.Column("source_reference", sa.String(1024), nullable=False), sa.Column("install_path", sa.String(1024), nullable=False),
        sa.Column("package_digest", sa.String(64), nullable=False), sa.Column("state", sa.String(32), nullable=False), sa.Column("state_reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("warnings", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plugin_id", "user_id", name="uq_plugins_identity"))
    op.create_index("ix_plugins_user", "plugins", ["user_id", "state"])
    op.create_table("plugin_contributions",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("plugin_id", sa.String(64), nullable=False), sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("reference", sa.String(255), nullable=False), sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plugin_id", "user_id", "kind", "reference", name="uq_plugin_contributions_ref"))
    op.create_index("ix_plugin_contributions_plugin", "plugin_contributions", ["user_id", "plugin_id"])
    op.create_table("plugin_marketplaces",
        sa.Column("marketplace_id", sa.String(64), primary_key=True), sa.Column("user_id", sa.String(255), primary_key=True), sa.Column("name", sa.String(255), nullable=False),
        sa.Column("reference", sa.String(1024), nullable=False), sa.Column("clone_path", sa.String(1024), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.add_column("skills", sa.Column("plugin_id", sa.String(64), nullable=True))
    op.create_index("ix_skills_plugin", "skills", ["plugin_id"])


def downgrade() -> None:
    op.drop_index("ix_skills_plugin", table_name="skills")
    op.drop_column("skills", "plugin_id")
    op.drop_table("plugin_marketplaces")
    op.drop_index("ix_plugin_contributions_plugin", table_name="plugin_contributions")
    op.drop_table("plugin_contributions")
    op.drop_index("ix_plugins_user", table_name="plugins")
    op.drop_table("plugins")
