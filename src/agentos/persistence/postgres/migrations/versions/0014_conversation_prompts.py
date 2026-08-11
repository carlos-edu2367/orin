"""persist opaque conversation prompt references

Revision ID: 0014_conversation_prompts
Revises: 0013_agent_model_configs
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_conversation_prompts"
down_revision = "0013_agent_model_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_prompts",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("prompt_ref", sa.String(255), nullable=False, unique=True),
        sa.Column("user_id", sa.String(255), nullable=False), sa.Column("message", sa.String(16000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_prompts_user", "conversation_prompts", ["user_id"])


def downgrade() -> None:
    op.drop_table("conversation_prompts")
