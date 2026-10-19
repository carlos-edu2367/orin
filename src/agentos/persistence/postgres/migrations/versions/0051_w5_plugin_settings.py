"""W5: a plugin's options (userConfig), encrypted per person

Revision ID: 0051_w5_plugin_settings
Revises: 0050_w5_extensibility
"""
from alembic import op
import sqlalchemy as sa


revision = "0051_w5_plugin_settings"
down_revision = "0050_w5_extensibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PluginConfigStore creates the table itself on a database that skipped migrations: create it only when missing.
    bind = op.get_bind()
    if "plugin_settings" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "plugin_settings",
            sa.Column("user_id", sa.String(255), primary_key=True),
            sa.Column("plugin_id", sa.String(64), primary_key=True),
            sa.Column("values_ciphertext", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("plugin_settings")
