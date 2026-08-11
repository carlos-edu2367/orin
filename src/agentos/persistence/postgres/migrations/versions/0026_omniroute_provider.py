"""persist OmniRoute's public endpoint and route kind.

Revision ID: 0026_omniroute
Revises: 0025_agent_usage
"""
from alembic import op
import sqlalchemy as sa


revision = "0026_omniroute"
down_revision = "0025_agent_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_configurations", sa.Column("base_url", sa.String(2048), nullable=True))
    op.add_column("provider_model_catalog", sa.Column("route_kind", sa.String(32), nullable=False, server_default="model"))


def downgrade() -> None:
    op.drop_column("provider_model_catalog", "route_kind")
    op.drop_column("provider_configurations", "base_url")
