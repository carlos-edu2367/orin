"""persist versioned procedural Agent Skills.

Revision ID: 0027_agent_skills
Revises: 0026_omniroute
"""
from alembic import op
import sqlalchemy as sa


revision = "0027_agent_skills"
down_revision = "0026_omniroute"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("skills", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("skill_id", sa.String(255), nullable=False), sa.Column("user_id", sa.String(255)), sa.Column("workspace_id", sa.String(255)), sa.Column("scope", sa.String(32), nullable=False), sa.Column("source", sa.String(32), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_skills_scope", "skills", ["scope", "user_id", "workspace_id"])
    op.create_index("ix_skills_identity", "skills", ["skill_id", "scope", "user_id", "workspace_id"])
    op.create_table("skill_versions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("skill_record_id", sa.Integer(), nullable=False), sa.Column("version", sa.String(64), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("metadata", sa.JSON(), nullable=False), sa.Column("instructions", sa.Text(), nullable=False), sa.Column("content_digest", sa.String(64), nullable=False), sa.Column("published_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("skill_record_id", "version", name="uq_skill_versions_ref"))
    op.create_index("ix_skill_versions_skill", "skill_versions", ["skill_record_id", "published_at"])
    op.create_table("agent_skills", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("agent_id", sa.String(255), nullable=False), sa.Column("skill_version_id", sa.Integer(), nullable=False), sa.Column("mode", sa.String(32), nullable=False, server_default="pinned"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("user_id", "agent_id", "skill_version_id", name="uq_agent_skills_ref"))
    op.create_index("ix_agent_skills_agent", "agent_skills", ["user_id", "agent_id"])
    op.create_table("execution_skills", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.String(255), nullable=False), sa.Column("execution_id", sa.String(255), nullable=False), sa.Column("agent_id", sa.String(255), nullable=False), sa.Column("skill_id", sa.String(255), nullable=False), sa.Column("version", sa.String(64), nullable=False), sa.Column("content_digest", sa.String(64), nullable=False), sa.Column("content_snapshot", sa.Text(), nullable=False), sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False), sa.Column("load_count", sa.Integer(), nullable=False, server_default="1"), sa.UniqueConstraint("execution_id", "skill_id", "version", name="uq_execution_skills_ref"))
    op.create_index("ix_execution_skills_execution", "execution_skills", ["user_id", "execution_id"])


def downgrade() -> None:
    for table, indexes in (
        ("execution_skills", ("ix_execution_skills_execution",)),
        ("agent_skills", ("ix_agent_skills_agent",)),
        ("skill_versions", ("ix_skill_versions_skill",)),
        ("skills", ("ix_skills_identity", "ix_skills_scope")),
    ):
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
