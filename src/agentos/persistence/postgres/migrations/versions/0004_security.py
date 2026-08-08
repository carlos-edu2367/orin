"""durable security credential, session and rate-limit state

Revision ID: 0004_security
Revises: 0003_workers_scheduler
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_security"
down_revision = "0003_workers_scheduler"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("security_pats", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("credential_ref", sa.String(255), nullable=False), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("token_digest", sa.String(64), nullable=False), sa.Column("scopes", sa.JSON(), nullable=False), sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("credential_ref", name="uq_security_pats_credential_ref"), sa.UniqueConstraint("token_digest", name="uq_security_pats_token_digest"))
    op.create_index("ix_security_pats_user", "security_pats", ["user_id"])
    op.create_table("security_sessions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("session_id", sa.String(255), nullable=False), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("credential_ref", sa.String(255), nullable=False), sa.Column("csrf_digest", sa.String(64), nullable=False), sa.Column("scopes", sa.JSON(), nullable=False), sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("session_id", name="uq_security_sessions_session_id"))
    op.create_index("ix_security_sessions_credential_ref", "security_sessions", ["credential_ref"])
    op.create_table("security_revocations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("credential_ref", sa.String(255), nullable=False), sa.Column("epoch", sa.Integer(), nullable=False), sa.UniqueConstraint("credential_ref", name="uq_security_revocations_credential_ref"), sa.CheckConstraint("epoch >= 0", name="ck_security_revocations_epoch_nonnegative"))
    op.create_table("security_rate_limit_hits", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("credential_ref", sa.String(255), nullable=False), sa.Column("action", sa.String(128), nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_security_rate_limit_window", "security_rate_limit_hits", ["credential_ref", "action", "occurred_at"])


def downgrade() -> None:
    op.drop_table("security_rate_limit_hits")
    op.drop_table("security_revocations")
    op.drop_table("security_sessions")
    op.drop_table("security_pats")
