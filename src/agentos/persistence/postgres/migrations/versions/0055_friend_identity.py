"""Orin Friend identity: a name of the person's choosing, how it addresses them, and its look

Revision ID: 0055_friend_identity
Revises: 0054_orin_friend_and_live_mode
"""
from alembic import op
import sqlalchemy as sa


revision = "0055_friend_identity"
down_revision = "0054_orin_friend_and_live_mode"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "user_friend_personas" not in set(sa.inspect(bind).get_table_names()):
        return
    existing = _columns(bind, "user_friend_personas")
    if "display_name" not in existing:
        op.add_column("user_friend_personas", sa.Column("display_name", sa.String(length=40), nullable=False, server_default="Orin"))
    if "user_nickname" not in existing:
        op.add_column("user_friend_personas", sa.Column("user_nickname", sa.String(length=40), nullable=False, server_default=""))
    if "orb_theme" not in existing:
        op.add_column("user_friend_personas", sa.Column("orb_theme", sa.String(length=24), nullable=False, server_default="aurora"))


def downgrade() -> None:
    bind = op.get_bind()
    if "user_friend_personas" not in set(sa.inspect(bind).get_table_names()):
        return
    existing = _columns(bind, "user_friend_personas")
    with op.batch_alter_table("user_friend_personas") as batch:
        for column in ("orb_theme", "user_nickname", "display_name"):
            if column in existing:
                batch.drop_column(column)
