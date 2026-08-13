"""Persist message attachments and the visual-reading model selection."""
from alembic import op
import sqlalchemy as sa

revision = "0032_message_attachments"
down_revision = "0031_catalog_source_url"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "conversation_message_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attachment_id", sa.String(255), nullable=False, unique=True),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_message_attachments_message", "conversation_message_attachments", ["message_id"])
    op.create_table(
        "vision_model_selections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False, unique=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("vision_model_selections")
    op.drop_index("ix_conversation_message_attachments_message", table_name="conversation_message_attachments")
    op.drop_table("conversation_message_attachments")
