"""Discord bridge configuration, Orin Friend session watches, and interaction bindings

Revision ID: 0060_discord_bridge_and_session_watches
Revises: 0059_memory_fact_4000_and_episodic
"""
from alembic import op
import sqlalchemy as sa


revision = "0060_discord_bridge_and_session_watches"
down_revision = "0059_memory_fact_4000_and_episodic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "discord_bot_configs" not in existing_tables:
        op.create_table(
            "discord_bot_configs",
            sa.Column("user_id", sa.String(length=255), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("bot_token_ciphertext", sa.Text(), nullable=True),
            sa.Column("bot_token_hint", sa.String(length=32), nullable=True),
            sa.Column("allowed_discord_user_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("allowed_channel_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("default_notify_channel_id", sa.String(length=64), nullable=True),
            sa.Column("bot_application_id", sa.String(length=64), nullable=True),
            sa.Column("bot_user_id", sa.String(length=64), nullable=True),
            sa.Column("bot_username", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "friend_session_watches" not in existing_tables:
        op.create_table(
            "friend_session_watches",
            sa.Column("watch_id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("friend_conversation_id", sa.String(length=255), nullable=True),
            sa.Column("target_conversation_id", sa.String(length=255), nullable=False),
            sa.Column("target_turn_id", sa.String(length=255), nullable=False),
            sa.Column("origin", sa.String(length=32), nullable=False, server_default="friend"),
            sa.Column("discord_channel_id", sa.String(length=64), nullable=True),
            sa.Column("discord_progress_message_id", sa.String(length=64), nullable=True),
            sa.Column("discord_prompt_message_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="watching"),
            sa.Column("last_activity_seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_digest_hash", sa.String(length=64), nullable=True),
            sa.Column("last_prompt_fingerprint", sa.String(length=64), nullable=True),
            sa.Column("last_patched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notified_terminal", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "target_conversation_id",
                "target_turn_id",
                name="uq_friend_session_watches_turn",
            ),
            sa.CheckConstraint(
                "status IN ('watching', 'waiting_user', 'completed', 'failed', 'cancelled')",
                name="ck_friend_session_watches_status",
            ),
        )
    watch_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("friend_session_watches")}
    if "ix_friend_session_watches_user_status" not in watch_indexes:
        op.create_index(
            "ix_friend_session_watches_user_status",
            "friend_session_watches",
            ["user_id", "status"],
        )
    if "ix_friend_session_watches_target_conv" not in watch_indexes:
        op.create_index(
            "ix_friend_session_watches_target_conv",
            "friend_session_watches",
            ["target_conversation_id"],
        )

    if "discord_interaction_bindings" not in existing_tables:
        op.create_table(
            "discord_interaction_bindings",
            sa.Column("binding_id", sa.String(length=32), primary_key=True),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("discord_channel_id", sa.String(length=64), nullable=True),
            sa.Column("discord_message_id", sa.String(length=64), nullable=True),
            sa.Column("target_conversation_id", sa.String(length=255), nullable=False),
            sa.Column("target_turn_id", sa.String(length=255), nullable=False),
            sa.Column("action_kind", sa.String(length=32), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "action_kind IN ('ask_user', 'plan_decision', 'tool_permission', 'stop_session')",
                name="ck_discord_interaction_bindings_action_kind",
            ),
        )
    binding_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("discord_interaction_bindings")}
    if "ix_discord_interaction_bindings_user_expires" not in binding_indexes:
        op.create_index(
            "ix_discord_interaction_bindings_user_expires",
            "discord_interaction_bindings",
            ["user_id", "expires_at"],
        )
    if "ix_discord_interaction_bindings_target_turn" not in binding_indexes:
        op.create_index(
            "ix_discord_interaction_bindings_target_turn",
            "discord_interaction_bindings",
            ["target_conversation_id", "target_turn_id"],
        )
    if "ix_discord_interaction_bindings_expires_at" not in binding_indexes:
        op.create_index(
            "ix_discord_interaction_bindings_expires_at",
            "discord_interaction_bindings",
            ["expires_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "discord_interaction_bindings" in existing_tables:
        binding_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("discord_interaction_bindings")}
        if "ix_discord_interaction_bindings_expires_at" in binding_indexes:
            op.drop_index("ix_discord_interaction_bindings_expires_at", table_name="discord_interaction_bindings")
        if "ix_discord_interaction_bindings_target_turn" in binding_indexes:
            op.drop_index("ix_discord_interaction_bindings_target_turn", table_name="discord_interaction_bindings")
        if "ix_discord_interaction_bindings_user_expires" in binding_indexes:
            op.drop_index("ix_discord_interaction_bindings_user_expires", table_name="discord_interaction_bindings")
        op.drop_table("discord_interaction_bindings")

    if "friend_session_watches" in existing_tables:
        watch_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("friend_session_watches")}
        if "ix_friend_session_watches_target_conv" in watch_indexes:
            op.drop_index("ix_friend_session_watches_target_conv", table_name="friend_session_watches")
        if "ix_friend_session_watches_user_status" in watch_indexes:
            op.drop_index("ix_friend_session_watches_user_status", table_name="friend_session_watches")
        op.drop_table("friend_session_watches")

    if "discord_bot_configs" in existing_tables:
        op.drop_table("discord_bot_configs")
