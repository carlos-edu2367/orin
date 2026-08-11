"""decouple provider credentials from legacy model column

Revision ID: 0008_provider_config
Revises: 0007_provider_configurations
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_provider_config"
down_revision = "0007_provider_configurations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_configurations") as batch:
        batch.alter_column("model", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("provider_configurations") as batch:
        batch.alter_column("model", existing_type=sa.String(255), nullable=False)
