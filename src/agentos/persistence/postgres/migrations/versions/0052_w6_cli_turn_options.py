"""W6: the options `orin code` attaches to a turn

Revision ID: 0052_w6_cli_turn_options
Revises: 0051_w5_plugin_settings
"""
from alembic import op
import sqlalchemy as sa


revision = "0052_w6_cli_turn_options"
down_revision = "0051_w5_plugin_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The chat store adds the column itself on a database that skipped migrations: add it only when it is missing.
    bind = op.get_bind()
    if "cli_options" not in {column["name"] for column in sa.inspect(bind).get_columns("conversation_turns")}:
        with op.batch_alter_table("conversation_turns") as batch:
            batch.add_column(sa.Column("cli_options", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch:
        batch.drop_column("cli_options")
