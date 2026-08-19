"""allow users to register provider models absent from upstream catalogs

Revision ID: 0039_custom_provider_models
Revises: 0038_provider_api_keys
"""
from alembic import op
import sqlalchemy as sa


revision = "0039_custom_provider_models"
down_revision = "0038_provider_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_model_catalog", sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    with op.batch_alter_table("provider_model_catalog") as batch:
        batch.drop_column("is_custom")
