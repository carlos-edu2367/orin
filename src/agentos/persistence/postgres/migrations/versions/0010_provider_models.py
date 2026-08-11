"""persist sanitized provider model catalogs and favorites

Revision ID: 0010_provider_models
Revises: 0009_catalog_state
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_provider_models"
down_revision = "0009_catalog_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_model_catalog",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False), sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=False), sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False), sa.Column("input_modalities", sa.JSON(), nullable=False),
        sa.Column("output_modalities", sa.JSON(), nullable=False), sa.Column("input_per_million", sa.String(64), nullable=True),
        sa.Column("output_per_million", sa.String(64), nullable=True), sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", "model_id", name="uq_provider_model_catalog_scope"),
    )
    op.create_index("ix_provider_model_catalog_scope", "provider_model_catalog", ["user_id", "provider"])
    op.create_table(
        "provider_model_favorites",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False), sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", "model_id", name="uq_provider_model_favorites_scope"),
    )
    op.create_index("ix_provider_model_favorites_scope", "provider_model_favorites", ["user_id", "provider"])


def downgrade() -> None:
    op.drop_table("provider_model_favorites")
    op.drop_table("provider_model_catalog")
