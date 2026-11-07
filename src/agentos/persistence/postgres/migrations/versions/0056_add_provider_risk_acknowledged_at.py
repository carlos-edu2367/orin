"""Add risk_acknowledged_at to provider_configurations

Revision ID: 0056_add_provider_risk_acknowledged_at
Revises: 0055_friend_identity
"""
from alembic import op
import sqlalchemy as sa


revision = "0056_add_provider_risk_acknowledged_at"
down_revision = "0055_friend_identity"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "provider_configurations" not in set(sa.inspect(bind).get_table_names()):
        return
    existing = _columns(bind, "provider_configurations")
    if "risk_acknowledged_at" not in existing:
        op.add_column(
            "provider_configurations",
            sa.Column("risk_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "provider_configurations" not in set(sa.inspect(bind).get_table_names()):
        return
    existing = _columns(bind, "provider_configurations")
    if "risk_acknowledged_at" in existing:
        with op.batch_alter_table("provider_configurations") as batch:
            batch.drop_column("risk_acknowledged_at")
