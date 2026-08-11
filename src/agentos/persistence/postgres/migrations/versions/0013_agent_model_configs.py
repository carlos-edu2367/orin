"""persist versioned agent provider model configuration

Revision ID: 0013_agent_model_configs
Revises: 0012_selection_snapshots
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_agent_model_configs"
down_revision = "0012_selection_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_model_configurations",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(255), nullable=False), sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False), sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("model_profile_ref", sa.String(1024), nullable=False), sa.Column("catalog_refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "agent_id", "config_version", name="uq_agent_model_configuration_revision"),
    )


def downgrade() -> None:
    op.drop_table("agent_model_configurations")
