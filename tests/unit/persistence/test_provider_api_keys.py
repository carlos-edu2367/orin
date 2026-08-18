from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, update

from agentos.api.contracts import ApplicationNotFoundError
from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter, ProviderApiKeyCredential
from agentos.persistence.postgres.schema import metadata, provider_api_keys
from agentos.persistence.provider_secrets import ProviderSecretCipher


def _adapter() -> PostgresProviderApiKeyAdapter:
    engine = create_engine("sqlite://")
    metadata.create_all(engine, tables=[provider_api_keys])
    return PostgresProviderApiKeyAdapter(engine, cipher=ProviderSecretCipher(b"0" * 32))


def _query(**overrides: object) -> dict[str, object]:
    return {"provider": "openai", "user_id": "user-1", **overrides}


def test_a_provider_with_no_keys_lists_empty() -> None:
    assert _adapter().list_keys(_query()) == []


def test_adding_a_key_appends_it_as_the_first_position() -> None:
    adapter = _adapter()

    saved = adapter.add_key(_query(api_key="sk-first-key", label="conta free 1"))

    assert saved["label"] == "conta free 1"
    assert saved["position"] == 0
    assert saved["status"] == "active"
    assert saved["cooldown_until"] is None
    assert "api_key" not in saved and "api_key_ciphertext" not in saved


def test_a_second_key_is_appended_after_the_first() -> None:
    adapter = _adapter()
    adapter.add_key(_query(api_key="sk-first-key"))

    second = adapter.add_key(_query(api_key="sk-second-key", label="conta paga"))

    assert second["position"] == 1


def test_a_short_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        _adapter().add_key(_query(api_key="abc"))


def test_renaming_a_key_updates_only_its_label() -> None:
    adapter = _adapter()
    key = adapter.add_key(_query(api_key="sk-first-key", label="old"))

    renamed = adapter.rename_key(_query(key_id=key["id"], label="new"))

    assert renamed["label"] == "new"
    assert renamed["position"] == 0


def test_renaming_a_key_that_does_not_exist_raises_not_found() -> None:
    with pytest.raises(ApplicationNotFoundError):
        _adapter().rename_key(_query(key_id=999, label="x"))


def test_removing_a_key_compacts_the_positions_of_the_rest() -> None:
    adapter = _adapter()
    first = adapter.add_key(_query(api_key="sk-first-key"))
    adapter.add_key(_query(api_key="sk-second-key"))
    third = adapter.add_key(_query(api_key="sk-third-key"))

    adapter.remove_key(_query(key_id=first["id"]))

    remaining = adapter.list_keys(_query())
    assert [row["position"] for row in remaining] == [0, 1]
    assert remaining[-1]["id"] == third["id"]


def test_removing_a_key_that_does_not_exist_raises_not_found() -> None:
    with pytest.raises(ApplicationNotFoundError):
        _adapter().remove_key(_query(key_id=999))


def test_reordering_moves_a_key_to_the_front() -> None:
    adapter = _adapter()
    first = adapter.add_key(_query(api_key="sk-first-key"))
    second = adapter.add_key(_query(api_key="sk-second-key"))

    reordered = adapter.reorder_keys(_query(ordered_ids=[second["id"], first["id"]]))

    assert [row["id"] for row in reordered] == [second["id"], first["id"]]
    assert [row["position"] for row in reordered] == [0, 1]


def test_reordering_with_a_mismatched_id_set_is_rejected() -> None:
    adapter = _adapter()
    adapter.add_key(_query(api_key="sk-first-key"))

    with pytest.raises(ValueError):
        adapter.reorder_keys(_query(ordered_ids=[999]))


def test_next_available_key_prefers_the_earliest_position() -> None:
    adapter = _adapter()
    adapter.add_key(_query(api_key="sk-first-key"))
    adapter.add_key(_query(api_key="sk-second-key"))

    credential = adapter.next_available_key("user-1", "openai")

    assert credential == ProviderApiKeyCredential(id=1, plaintext="sk-first-key")


def test_next_available_key_skips_a_key_in_unexpired_cooldown() -> None:
    adapter = _adapter()
    first = adapter.add_key(_query(api_key="sk-first-key"))
    adapter.add_key(_query(api_key="sk-second-key"))
    adapter.mark_cooldown(first["id"], 3600)

    credential = adapter.next_available_key("user-1", "openai")

    assert credential.plaintext == "sk-second-key"


def test_next_available_key_honors_an_expired_cooldown() -> None:
    adapter = _adapter()
    first = adapter.add_key(_query(api_key="sk-first-key"))
    adapter.mark_cooldown(first["id"], 3600)
    with adapter._engine.begin() as connection:
        connection.execute(update(provider_api_keys).where(provider_api_keys.c.id == first["id"]).values(cooldown_until=datetime.now(UTC) - timedelta(seconds=1)))

    credential = adapter.next_available_key("user-1", "openai")

    assert credential.plaintext == "sk-first-key"


def test_next_available_key_falls_back_to_the_principal_when_everything_is_in_cooldown() -> None:
    adapter = _adapter()
    first = adapter.add_key(_query(api_key="sk-first-key"))
    second = adapter.add_key(_query(api_key="sk-second-key"))
    adapter.mark_cooldown(first["id"], 3600)
    adapter.mark_cooldown(second["id"], 3600)

    credential = adapter.next_available_key("user-1", "openai")

    assert credential.plaintext == "sk-first-key"


def test_next_available_key_excludes_already_tried_ids() -> None:
    adapter = _adapter()
    first = adapter.add_key(_query(api_key="sk-first-key"))
    adapter.add_key(_query(api_key="sk-second-key"))

    credential = adapter.next_available_key("user-1", "openai", exclude=frozenset({first["id"]}))

    assert credential.plaintext == "sk-second-key"


def test_next_available_key_returns_none_when_every_key_is_excluded() -> None:
    adapter = _adapter()
    first = adapter.add_key(_query(api_key="sk-first-key"))

    assert adapter.next_available_key("user-1", "openai", exclude=frozenset({first["id"]})) is None


def test_next_available_key_returns_none_for_a_provider_with_no_keys() -> None:
    assert _adapter().next_available_key("user-1", "openai") is None


def test_mark_active_clears_a_cooldown() -> None:
    adapter = _adapter()
    key = adapter.add_key(_query(api_key="sk-first-key"))
    adapter.mark_cooldown(key["id"], 3600)

    adapter.mark_active(key["id"])

    assert adapter.list_keys(_query())[0]["status"] == "active"
    assert adapter.list_keys(_query())[0]["cooldown_until"] is None


def test_set_primary_key_creates_position_zero_when_none_exists() -> None:
    adapter = _adapter()

    saved = adapter.set_primary_key(_query(api_key="sk-primary"))

    assert saved["position"] == 0
    assert adapter.next_available_key("user-1", "openai").plaintext == "sk-primary"


def test_set_primary_key_overwrites_the_existing_position_zero_key() -> None:
    adapter = _adapter()
    adapter.set_primary_key(_query(api_key="sk-old"))

    adapter.set_primary_key(_query(api_key="sk-new"))

    keys = adapter.list_keys(_query())
    assert len(keys) == 1
    assert adapter.next_available_key("user-1", "openai").plaintext == "sk-new"


def test_set_primary_key_clears_an_existing_cooldown() -> None:
    adapter = _adapter()
    key = adapter.set_primary_key(_query(api_key="sk-old"))
    adapter.mark_cooldown(key["id"], 3600)

    adapter.set_primary_key(_query(api_key="sk-new"))

    assert adapter.list_keys(_query())[0]["status"] == "active"


def test_clear_primary_key_removes_only_position_zero() -> None:
    adapter = _adapter()
    adapter.set_primary_key(_query(api_key="sk-primary"))
    second = adapter.add_key(_query(api_key="sk-second"))

    adapter.clear_primary_key(_query())

    remaining = adapter.list_keys(_query())
    assert [row["id"] for row in remaining] == [second["id"]]


def test_clear_primary_key_on_a_provider_with_no_keys_is_a_no_op() -> None:
    _adapter().clear_primary_key(_query())
