"""bind immutable agent model configuration to resolver selection

Revision ID: 0015_agent_config_selection
Revises: 0014_conversation_prompts
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_agent_config_selection"
down_revision = "0014_conversation_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_model_configurations", sa.Column("selection_ref", sa.String(length=512), nullable=True))
    op.add_column("agent_model_configurations", sa.Column("model_revision", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_model_configurations", "model_revision")
    op.drop_column("agent_model_configurations", "selection_ref")
