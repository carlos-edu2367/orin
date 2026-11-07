"""A person's own subagents (specialised agents) made in the interface

Revision ID: 0057_user_agents
Revises: 0056_add_provider_risk_acknowledged_at
"""
from alembic import op
import sqlalchemy as sa


revision = "0057_user_agents"
down_revision = "0056_add_provider_risk_acknowledged_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "user_agents" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "user_agents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=True),
            sa.Column("models_json", sa.JSON(), nullable=False),
            sa.Column("tools_json", sa.JSON(), nullable=True),
            sa.Column("disallowed_tools_json", sa.JSON(), nullable=False),
            sa.Column("effort", sa.String(length=16), nullable=True),
            sa.Column("permission_mode", sa.String(length=32), nullable=True),
            sa.Column("max_turns", sa.Integer(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "name", name="uq_user_agents_user_name"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "user_agents" in set(sa.inspect(bind).get_table_names()):
        op.drop_table("user_agents")
