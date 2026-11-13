"""Unit and migration tests for Phase 2: Migration 0060 and PostgresDiscordStore."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select

from agentos.discord import (
    DiscordBotConfig,
    DiscordInteractionBinding,
    FriendSessionWatch,
    PostgresDiscordStore,
    mask_bot_token_hint,
    normalize_optional_snowflake_id,
    normalize_snowflake_ids,
)
from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import (
    discord_bot_configs,
    discord_interaction_bindings,
    friend_session_watches,
    metadata,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher


def _make_cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher("test-master-secret-key-for-discord-store-0060")


def test_migration_0060_upgrade_downgrade_and_schema_parity(tmp_path: Path) -> None:
    db_path = tmp_path / "discord_0060.sqlite3"
    migrated_engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    created_engine = create_engine("sqlite:///:memory:")

    try:
        upgrade(migrated_engine, "head")
        metadata.create_all(created_engine)

        mig_inspector = inspect(migrated_engine)
        create_inspector = inspect(created_engine)

        expected_new_tables = {
            "discord_bot_configs",
            "friend_session_watches",
            "discord_interaction_bindings",
        }
        assert expected_new_tables <= set(mig_inspector.get_table_names())

        for table_name in sorted(expected_new_tables):
            mig_cols = {col["name"]: col for col in mig_inspector.get_columns(table_name)}
            create_cols = {col["name"]: col for col in create_inspector.get_columns(table_name)}
            assert set(mig_cols.keys()) == set(create_cols.keys()), f"Column mismatch on {table_name}"
            for col_name in mig_cols:
                assert mig_cols[col_name]["nullable"] == create_cols[col_name]["nullable"], (
                    f"Nullable mismatch on {table_name}.{col_name}"
                )

            mig_indexes = {idx["name"] for idx in mig_inspector.get_indexes(table_name)}
            create_indexes = {idx["name"] for idx in create_inspector.get_indexes(table_name)}
            assert mig_indexes == create_indexes, f"Index mismatch on {table_name}"

            mig_uqs = {uq["name"] for uq in mig_inspector.get_unique_constraints(table_name)}
            create_uqs = {uq["name"] for uq in create_inspector.get_unique_constraints(table_name)}
            assert mig_uqs == create_uqs, f"UniqueConstraint mismatch on {table_name}"

        # Downgrade -1 (back to 0059) removes the 3 tables cleanly
        downgrade(migrated_engine, "0059_memory_fact_4000_and_episodic")
        tables_after_downgrade = set(inspect(migrated_engine).get_table_names())
        assert expected_new_tables.isdisjoint(tables_after_downgrade)

        # Re-upgrade to head restores all 3 tables and indexes
        upgrade(migrated_engine, "head")
        tables_after_reupgrade = set(inspect(migrated_engine).get_table_names())
        assert expected_new_tables <= tables_after_reupgrade
    finally:
        migrated_engine.dispose()
        created_engine.dispose()


def test_snowflake_validation_accepts_valid_and_rejects_invalid() -> None:
    valid = normalize_snowflake_ids([
        "12345678901234567",
        "12345678901234567890",
        "  987654321098765432  ",
        "12345678901234567",  # duplicate
    ])
    assert valid == (
        "12345678901234567",
        "12345678901234567890",
        "987654321098765432",
    )

    from_csv = normalize_snowflake_ids("123456789012345678, 987654321098765432")
    assert from_csv == ("123456789012345678", "987654321098765432")
    assert normalize_snowflake_ids(None) == ()
    assert normalize_snowflake_ids("   ") == ()
    assert normalize_optional_snowflake_id(None) is None
    assert normalize_optional_snowflake_id("  ") is None
    assert normalize_optional_snowflake_id("123456789012345678") == "123456789012345678"

    invalid_inputs = [
        ["1234567890123456"],  # 16 digits (too short)
        ["123456789012345678901"],  # 21 digits (too long)
        ["12345678901234567a"],  # non-numeric
        ["-12345678901234567"],  # negative
        ["00000000000000000"],  # zero
        ["１２３４５６７８９０１２３４５６７８"],  # full-width unicode digits
        [True],
        [None],
        [""],
    ]
    for bad in invalid_inputs:
        with pytest.raises(ValueError, match="Snowflake"):
            normalize_snowflake_ids(bad)


def test_bot_config_encryption_at_rest_and_fail_closed_validation() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    cipher = _make_cipher()
    store = PostgresDiscordStore(engine, cipher)

    raw_token = "fake-discord-bot-token-unit-test-only-a9Xz"
    user_snowflake = "123456789012345678"
    chan_snowflake = "987654321098765432"

    # Fail-closed: cannot enable without bot_token
    with pytest.raises(ValueError, match="bot_token"):
        store.save_config(
            "user-a",
            enabled=True,
            bot_token=None,
            allowed_discord_user_ids=[user_snowflake],
        )

    # Fail-closed: cannot enable without allowed_discord_user_ids
    with pytest.raises(ValueError, match="allowed_discord_user_ids"):
        store.save_config(
            "user-a",
            enabled=True,
            bot_token=raw_token,
            allowed_discord_user_ids=[],
        )

    # Save valid enabled config
    cfg = store.save_config(
        "user-a",
        enabled=True,
        bot_token=raw_token,
        allowed_discord_user_ids=[user_snowflake],
        allowed_channel_ids=[chan_snowflake],
    )
    assert isinstance(cfg, DiscordBotConfig)
    assert cfg.user_id == "user-a"
    assert cfg.enabled is True
    assert cfg.has_bot_token is True
    assert cfg.masked_token_hint == "...a9Xz"
    assert raw_token not in str(cfg)
    assert raw_token not in str(cfg.to_public_dict())
    assert cfg.allowed_discord_user_ids == (user_snowflake,)
    assert cfg.allowed_channel_ids == (chan_snowflake,)

    # Inspect raw DB row to verify encryption at rest (enc:v1:...)
    with engine.connect() as conn:
        db_row = (
            conn.execute(select(discord_bot_configs).where(discord_bot_configs.c.user_id == "user-a"))
            .mappings()
            .one()
        )
    assert db_row["bot_token_ciphertext"].startswith("enc:v1:")
    assert raw_token not in db_row["bot_token_ciphertext"]
    assert db_row["bot_token_hint"] == "...a9Xz"

    # Decrypt via dedicated method
    assert store.get_decrypted_bot_token("user-a") == raw_token
    assert store.get_decrypted_bot_token("non-existent") is None

    # Update allowlist with bot_token=None preserves existing encrypted token
    second_user_snowflake = "234567890123456789"
    updated_cfg = store.save_config(
        "user-a",
        enabled=True,
        bot_token=None,
        allowed_discord_user_ids=[user_snowflake, second_user_snowflake],
    )
    assert updated_cfg.has_bot_token is True
    assert updated_cfg.masked_token_hint == "...a9Xz"
    assert updated_cfg.allowed_discord_user_ids == (user_snowflake, second_user_snowflake)
    assert store.get_decrypted_bot_token("user-a") == raw_token

    # Update bot identity and default notify channel
    with_identity = store.update_bot_identity(
        "user-a",
        application_id="345678901234567890",
        bot_user_id="456789012345678901",
        bot_username="OrinFriendBot",
    )
    assert with_identity is not None
    assert with_identity.bot_application_id == "345678901234567890"
    assert with_identity.bot_user_id == "456789012345678901"
    assert with_identity.bot_username == "OrinFriendBot"

    with_notify = store.update_default_notify_channel("user-a", "567890123456789012")
    assert with_notify is not None
    assert with_notify.default_notify_channel_id == "567890123456789012"

    enabled_list = store.list_enabled_configs()
    assert len(enabled_list) == 1
    assert enabled_list[0].user_id == "user-a"

    assert store.delete_config("user-a") is True
    assert store.get_config("user-a") is None
    assert store.delete_config("user-a") is False


def test_friend_session_watches_idempotent_upsert_and_lifecycle() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    store = PostgresDiscordStore(engine, _make_cipher())

    w1 = store.upsert_watch(
        "user-1",
        "conv-code-1",
        "turn-1",
        friend_conversation_id="conv-friend-1",
        origin="friend",
        discord_channel_id="123456789012345678",
    )
    assert isinstance(w1, FriendSessionWatch)
    assert w1.status == "watching"
    assert w1.last_activity_seq == 0
    assert w1.notified_terminal is False
    assert w1.friend_conversation_id == "conv-friend-1"
    assert w1.discord_channel_id == "123456789012345678"

    # Idempotent upsert on (user_id, target_conversation_id, target_turn_id)
    w1_again = store.upsert_watch(
        "user-1",
        "conv-code-1",
        "turn-1",
        origin="friend",
    )
    assert w1_again.watch_id == w1.watch_id
    assert w1_again.discord_channel_id == "123456789012345678"

    # Update watch progress & waiting_user prompt fingerprint
    patched_time = datetime.now(UTC)
    w_waiting = store.update_watch(
        w1.watch_id,
        status="waiting_user",
        discord_progress_message_id="223456789012345678",
        discord_prompt_message_id="323456789012345678",
        last_activity_seq=14,
        last_digest_hash="abc123digest",
        last_prompt_fingerprint="fp_ask_user_1",
        last_patched_at=patched_time,
    )
    assert w_waiting is not None
    assert w_waiting.status == "waiting_user"
    assert w_waiting.discord_progress_message_id == "223456789012345678"
    assert w_waiting.discord_prompt_message_id == "323456789012345678"
    assert w_waiting.last_activity_seq == 14
    assert w_waiting.last_digest_hash == "abc123digest"
    assert w_waiting.last_prompt_fingerprint == "fp_ask_user_1"
    assert w_waiting.last_patched_at == patched_time

    # Open watches includes waiting_user and terminal watches with notified_terminal=False
    open_watches = store.list_open_watches(user_id="user-1")
    assert [w.watch_id for w in open_watches] == [w1.watch_id]

    # Mark completed but notified_terminal=False -> still returned by list_open_watches
    store.update_watch(w1.watch_id, status="completed", notified_terminal=False)
    assert len(store.list_open_watches(user_id="user-1")) == 1

    # Once notified_terminal=True and status="completed" -> no longer in open watches
    store.update_watch(w1.watch_id, status="completed", notified_terminal=True)
    assert store.list_open_watches(user_id="user-1") == []

    # Invalid watch status raises ValueError
    with pytest.raises(ValueError, match="Invalid watch status"):
        store.update_watch(w1.watch_id, status="invalid_state")


def test_interaction_bindings_atomic_consume_and_expiry() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    store = PostgresDiscordStore(engine, _make_cipher())

    binding = store.create_binding(
        "user-1",
        target_conversation_id="conv-code-1",
        target_turn_id="turn-1",
        action_kind="tool_permission",
        payload={"request_id": "req-99", "decision": "allow_once"},
        ttl_seconds=3600,
    )
    assert isinstance(binding, DiscordInteractionBinding)
    assert binding.binding_id.startswith("dbnd_")
    assert len(binding.binding_id) <= 32
    assert binding.is_consumed is False
    assert binding.payload["request_id"] == "req-99"

    attached = store.attach_binding_message(
        [binding.binding_id],
        discord_channel_id="123456789012345678",
        discord_message_id="223456789012345678",
    )
    assert attached == 1
    fetched = store.get_binding(binding.binding_id)
    assert fetched is not None
    assert fetched.discord_channel_id == "123456789012345678"
    assert fetched.discord_message_id == "223456789012345678"

    # Wrong user cannot consume the binding
    assert store.consume_binding(binding.binding_id, user_id="other-user") is None

    # First valid consume succeeds
    consumed = store.consume_binding(binding.binding_id, user_id="user-1")
    assert consumed is not None
    assert consumed.is_consumed is True
    assert consumed.consumed_at is not None

    # Second consume (double click / race condition) returns None
    second_attempt = store.consume_binding(binding.binding_id, user_id="user-1")
    assert second_attempt is None

    # Expired binding cannot be consumed and is purged by purge_expired_bindings
    expiring_binding = store.create_binding(
        "user-1",
        target_conversation_id="conv-code-1",
        target_turn_id="turn-1",
        action_kind="stop_session",
        payload={"action": "stop"},
        ttl_seconds=10,
    )
    future_now = datetime.now(UTC) + timedelta(seconds=30)
    assert store.consume_binding(expiring_binding.binding_id, user_id="user-1", now=future_now) is None

    purged_count = store.purge_expired_bindings(now=future_now)
    assert purged_count == 1
    assert store.get_binding(expiring_binding.binding_id) is None
    assert store.get_binding(binding.binding_id) is not None


def test_get_config_without_secret_does_not_decrypt_or_require_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)

    # Verify index ix_discord_interaction_bindings_expires_at exists
    idx_names = {idx["name"] for idx in inspect(engine).get_indexes("discord_interaction_bindings")}
    assert "ix_discord_interaction_bindings_expires_at" in idx_names

    # Process A saves config with its own cipher key
    writer_store = PostgresDiscordStore(engine, ProviderSecretCipher("writer-process-secret-key-001"))
    writer_store.save_config(
        "user-worker-test",
        enabled=True,
        bot_token="MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.token_xyz9",
        allowed_discord_user_ids=["123456789012345678"],
        default_notify_channel_id="222333444555666777",
    )

    # Process B (e.g. worker without AGENTOS_PROVIDER_ENCRYPTION_KEY or with a different key)
    monkeypatch.delenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", raising=False)
    reader_store_no_key = PostgresDiscordStore(engine)
    cfg = reader_store_no_key.get_config("user-worker-test", include_secret=False)
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.has_bot_token is True
    assert cfg.default_notify_channel_id == "222333444555666777"
    assert cfg.allowed_discord_user_ids == ("123456789012345678",)

    # Reader with a different cipher key also succeeds on get_config(include_secret=False)
    reader_store_diff_key = PostgresDiscordStore(engine, ProviderSecretCipher("different-ephemeral-key-002"))
    cfg2 = reader_store_diff_key.get_config("user-worker-test", include_secret=False)
    assert cfg2 is not None
    assert cfg2.has_bot_token is True
    assert cfg2.default_notify_channel_id == "222333444555666777"

