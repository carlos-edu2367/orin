"""Orin Friend affective dual-layer memory, persona table, and STT media modality

Revision ID: 0054_orin_friend_and_live_mode
Revises: 0053_media_model_selections
"""
from alembic import op
import sqlalchemy as sa


revision = "0054_orin_friend_and_live_mode"
down_revision = "0053_media_model_selections"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "agent_memories" in tables:
        existing_cols = _columns(bind, "agent_memories")
        if "emotional_valence" not in existing_cols:
            op.add_column(
                "agent_memories",
                sa.Column("emotional_valence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
            )
        if "salience" not in existing_cols:
            op.add_column(
                "agent_memories",
                sa.Column("salience", sa.Float(), nullable=False, server_default=sa.text("0.5")),
            )

    if "user_friend_personas" not in tables:
        op.create_table(
            "user_friend_personas",
            sa.Column("user_id", sa.String(length=255), primary_key=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("bond_stage", sa.String(length=32), nullable=False, server_default="acquaintance"),
            sa.Column("affinity_score", sa.Float(), nullable=False, server_default=sa.text("10.0")),
            sa.Column("current_mood", sa.String(length=64), nullable=False, server_default="sereno"),
            sa.Column("traits_json", sa.JSON(), nullable=False),
            sa.Column("style_notes_json", sa.JSON(), nullable=False),
            sa.Column("compiled_rubric", sa.Text(), nullable=False, server_default=""),
            sa.Column("interaction_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("soul_sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("last_reflected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "media_model_selections" in tables:
        checks = {c.get("name") for c in sa.inspect(bind).get_check_constraints("media_model_selections")}
        with op.batch_alter_table("media_model_selections", recreate="always") as batch:
            if "ck_media_model_selections_modality" in checks:
                batch.drop_constraint("ck_media_model_selections_modality", type_="check")
            batch.create_check_constraint(
                "ck_media_model_selections_modality",
                "modality IN ('image', 'audio', 'video', 'stt')",
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "media_model_selections" in tables:
        checks = {c.get("name") for c in sa.inspect(bind).get_check_constraints("media_model_selections")}
        with op.batch_alter_table("media_model_selections", recreate="always") as batch:
            if "ck_media_model_selections_modality" in checks:
                batch.drop_constraint("ck_media_model_selections_modality", type_="check")
            batch.create_check_constraint(
                "ck_media_model_selections_modality",
                "modality IN ('image', 'audio', 'video')",
            )

    if "user_friend_personas" in tables:
        op.drop_table("user_friend_personas")

    if "agent_memories" in tables:
        existing_cols = _columns(bind, "agent_memories")
        with op.batch_alter_table("agent_memories") as batch:
            if "salience" in existing_cols:
                batch.drop_column("salience")
            if "emotional_valence" in existing_cols:
                batch.drop_column("emotional_valence")
