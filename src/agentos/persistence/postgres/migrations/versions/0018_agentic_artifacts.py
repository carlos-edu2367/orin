"""Persist bounded artifact metadata for agentic actions."""
from alembic import op
import sqlalchemy as sa

revision = "0018_agentic_artifacts"
down_revision = "0017_agentic_activity"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "agentic_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artifact_id", sa.String(255), nullable=False, unique=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255)),
        sa.Column("conversation_id", sa.String(255)),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("media_type", sa.String(128)),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agentic_artifacts_owner_created", "agentic_artifacts", ["user_id", "created_at"])

def downgrade() -> None:
    op.drop_index("ix_agentic_artifacts_owner_created", table_name="agentic_artifacts")
    op.drop_table("agentic_artifacts")
