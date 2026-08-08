"""durable client event stream bindings

Revision ID: 0005_event_stream_bindings
Revises: 0004_security
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_event_stream_bindings"
down_revision = "0004_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("event_stream_bindings", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("stream_id", sa.String(255), nullable=False), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("credential_ref", sa.String(255), nullable=False), sa.Column("execution_ids", sa.JSON(), nullable=False), sa.Column("digest", sa.String(64), nullable=False), sa.Column("epoch", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("stream_id", name="uq_event_stream_bindings_stream_id"))
    op.create_index("ix_event_stream_bindings_user", "event_stream_bindings", ["user_id"])


def downgrade() -> None:
    op.drop_table("event_stream_bindings")
