"""persist resolved provider model selections

Revision ID: 0011_model_selections
Revises: 0010_provider_models
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_model_selections"
down_revision = "0010_provider_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_model_selections",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("selection_ref", sa.String(512), nullable=False, unique=True),
        sa.Column("user_id", sa.String(255), nullable=False), sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False), sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(128), nullable=False), sa.Column("model_ref", sa.String(1024), nullable=False),
        sa.Column("model_revision", sa.String(512), nullable=False), sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_provider_model_selections_scope", "provider_model_selections", ["user_id", "agent_id"])


def downgrade() -> None:
    op.drop_table("provider_model_selections")
