"""Add immutable mode and permission_mode to conversations

Revision ID: 0047_conversation_mode_and_permission
Revises: 0046_user_accounts
"""
from alembic import op
import sqlalchemy as sa


revision = "0047_conversation_mode_and_permission"
down_revision = "0046_user_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("mode", sa.String(16), nullable=False, server_default="chat"))
        batch.add_column(sa.Column("permission_mode", sa.String(32), nullable=False, server_default="ask_permissions"))

    # Backfill existing conversations that had any code_mode='code' turn
    op.execute(
        """
        UPDATE conversations
        SET mode = 'code', permission_mode = 'accept_edits'
        WHERE conversation_id IN (
            SELECT DISTINCT conversation_id
            FROM conversation_turns
            WHERE code_mode = 'code'
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("permission_mode")
        batch.drop_column("mode")
