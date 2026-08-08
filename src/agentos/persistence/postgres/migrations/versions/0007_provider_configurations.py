"""durable user-configured provider credentials (frontend Fase D)

Revision ID: 0007_provider_configurations
Revises: 0006_multi_agent_and_tool_events
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_provider_configurations"
down_revision = "0006_multi_agent_and_tool_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("provider_configurations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("provider", sa.String(32), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("model", sa.String(255), nullable=False), sa.Column("api_key", sa.String(4096), nullable=False), sa.Column("secret_ref", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("user_id", "provider", name="uq_provider_configurations_user_provider"))
    op.create_index("ix_provider_configurations_user", "provider_configurations", ["user_id"])


def downgrade() -> None:
    op.drop_table("provider_configurations")
