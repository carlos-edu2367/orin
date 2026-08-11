"""add provider catalog refresh timestamp

Revision ID: 0009_catalog_state
Revises: 0008_provider_config
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_catalog_state"
down_revision = "0008_provider_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_configurations", sa.Column("catalog_refreshed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("provider_configurations", "catalog_refreshed_at")
