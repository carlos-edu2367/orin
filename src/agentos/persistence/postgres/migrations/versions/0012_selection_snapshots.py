"""persist model selection and requirements snapshots

Revision ID: 0012_selection_snapshots
Revises: 0011_model_selections
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_selection_snapshots"
down_revision = "0011_model_selections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_model_selections", sa.Column("selection", sa.JSON(), nullable=True))
    op.add_column("provider_model_selections", sa.Column("requirements", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("provider_model_selections", "requirements")
    op.drop_column("provider_model_selections", "selection")
