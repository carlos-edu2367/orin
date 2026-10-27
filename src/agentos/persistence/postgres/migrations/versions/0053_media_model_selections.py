"""Per-modality default provider and model selections for media generation

Revision ID: 0053_media_model_selections
Revises: 0052_w6_cli_turn_options
"""
from alembic import op
import sqlalchemy as sa


revision = "0053_media_model_selections"
down_revision = "0052_w6_cli_turn_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "media_model_selections" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "media_model_selections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("modality", sa.String(length=32), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("model_id", sa.String(length=512), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "modality", name="uq_media_model_selections_user_modality"),
            sa.CheckConstraint("modality IN ('image', 'audio', 'video')", name="ck_media_model_selections_modality"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "media_model_selections" in set(sa.inspect(bind).get_table_names()):
        op.drop_table("media_model_selections")
