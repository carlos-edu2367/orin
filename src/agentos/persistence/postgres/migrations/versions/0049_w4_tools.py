"""W4: folders a person added to a conversation with /add-dir

Revision ID: 0049_w4_tools
Revises: 0048_w3_loop_and_context
"""
from alembic import op
import sqlalchemy as sa


revision = "0049_w4_tools"
down_revision = "0048_w3_loop_and_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ConversationDirectoryStore creates the table itself on a database that skipped migrations: create it
    # only when it is missing, so 0049 succeeds whichever of the two runs first.
    bind = op.get_bind()
    if "conversation_directories" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "conversation_directories",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("directory_id", sa.String(255), nullable=False, unique=True),
            sa.Column("conversation_id", sa.String(255), nullable=False),
            sa.Column("user_id", sa.String(255), nullable=False),
            sa.Column("path", sa.String(1024), nullable=False),
            sa.Column("path_key", sa.String(1024), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("announced_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "ux_conversation_directories_path" not in {index["name"] for index in sa.inspect(bind).get_indexes("conversation_directories")}:
        op.create_index("ux_conversation_directories_path", "conversation_directories", ["conversation_id", "path_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_conversation_directories_path", table_name="conversation_directories")
    op.drop_table("conversation_directories")
