"""record the URL that produced each provider catalog

Revision ID: 0031_catalog_source_url
Revises: 0030_workspace_roots
"""

from alembic import op
import sqlalchemy as sa


revision = "0031_catalog_source_url"
down_revision = "0030_workspace_roots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_model_catalog", sa.Column("catalog_base_url", sa.String(2048), nullable=True))


def downgrade() -> None:
    op.drop_column("provider_model_catalog", "catalog_base_url")
