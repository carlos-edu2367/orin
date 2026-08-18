# Multi-API-Key Provider Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user register more than one API key per provider (ordered, with an optional label), and have the agentic chat loop automatically try the next key — with a per-key cooldown — when a call fails with a key-shaped error (401/403/429/timeout/connection), instead of failing the turn.

**Architecture:** A new `provider_api_keys` table (sibling of `provider_configurations`, N rows per `(user_id, provider)`, ordered by `position`) stores credentials; `provider_configurations` keeps provider-level settings (`enabled`/`base_url`/`model`) plus a new `key_cooldown_seconds` column and loses its own key columns. A new `MultiKeyProviderStreamTransport` wraps `HTTPProviderStreamTransport` at the exact seam `chat.py` already uses to hand the turn's provider to `AgenticTurnRuntime` (`provider_factory`), so the runtime's existing retry loop needs **no changes at all** — the wrapper rotates keys internally and only re-raises once every key has been tried or a real answer has already started streaming. New REST endpoints under `/v1/providers/{provider}/keys` let the UI manage the list; a small provider-agnostic `ProviderKeyList` component renders under every provider's existing single-key form (which keeps working unchanged as "the field that sets the principal key").

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 Core, Alembic, httpx, pytest (backend); React, TypeScript, vitest, @testing-library/react (frontend).

**Spec:** [docs/superpowers/specs/2026-08-18-multi-api-key-provider-fallback-design.md](../specs/2026-08-18-multi-api-key-provider-fallback-design.md)

**Scope note on the fallback mechanism:** the spec's section 2 sketches the fallback loop as edits inside `agentic/runtime.py`. Discovered while researching the codebase for this plan: `runtime.py` never builds the provider transport itself — it receives one, already built, through `chat.py`'s `provider_factory=lambda: self._provider_transport(turn)` callback (`session.py:976`, `chat.py:525`), and `HTTPProviderStreamTransport.stream()` is a generator (its HTTP call only fires on first iteration). This means a wrapper satisfying the same `.stream(request) -> Iterator[NormalizedStreamItem]` contract, injected at that one seam, achieves every decision in the spec (cooldown, before-first-token-only, error classification, all-cooldown-fallback, always-start-from-principal) **without touching `runtime.py`** — a heavily-tested, already-complex generic loop shared by subagents too. This plan implements it that way; all 7 decisions in the spec are still honored exactly.

**Scope note on the frontend:** the new key-list UI is one provider-agnostic component (`ProviderKeyList`) rendered by `ProviderDetail.tsx` under whichever provider-specific form already renders (generic form, `OllamaSetup`, or `OmniRouteSetup`) — so it covers every provider, including Ollama (the user's own example), without modifying `OllamaSetup.tsx`/`OmniRouteSetup.tsx`. Reordering uses up/down move buttons rather than drag-and-drop: equally "orderable" as the approved design, keyboard-accessible, and far simpler to implement and test correctly than accessible HTML5 drag-and-drop — a disclosed simplification, not a missed requirement.

**Known limitation, accepted:** `POST /v1/providers/{provider}/keys` is not idempotency-deduplicated server-side (unlike `PUT /v1/providers/{provider}`, which is a natural upsert). A double-submit could add two identical keys. This matches the existing `skills.create` endpoint's behavior in this codebase and is a minor UX rough edge, not a correctness or security issue; building a full idempotency ledger is out of scope for this feature.

---

### Task 1: Schema — `provider_api_keys` table and `provider_configurations` changes

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py:380-402` (the `provider_configurations` table) and `:840-879` (`__all__`)

- [ ] **Step 1: Edit the `provider_configurations` table and add `provider_api_keys`**

Replace lines 380-402 of `src/agentos/persistence/postgres/schema.py`:

```python
# Frontend Fase D: user-configured LLM provider credentials. There is no
# execution/agent scope for a provider configuration (it is set once per
# user, independent of any execution), so this is a dedicated table rather
# than a reuse of persistence_records's execution-shaped scope columns.
provider_configurations = Table(
    "provider_configurations", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("enabled", Boolean, nullable=False),
    # Retained only for migration compatibility. New credential records never
    # store or interpret a selected model; agent revisions own that selection.
    Column("model", String(255), nullable=True),
    Column("base_url", String(2048), nullable=True),
    Column("secret_ref", String(255), nullable=False),
    # Applied to a key in provider_api_keys after a key-shaped failure
    # (401/403/429/timeout/connection) before it is tried again.
    Column("key_cooldown_seconds", Integer, nullable=False, server_default="60"),
    Column("catalog_refreshed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "provider", name="uq_provider_configurations_user_provider"),
)
Index("ix_provider_configurations_user", provider_configurations.c.user_id)


# Sibling of provider_configurations: N credentials per (user_id, provider),
# ordered by `position` (0 = principal, tried first on every new request).
# A key with status='cooldown' is skipped by
# PostgresProviderApiKeyAdapter.next_available_key until cooldown_until
# passes; see docs/superpowers/specs/
# 2026-08-18-multi-api-key-provider-fallback-design.md.
provider_api_keys = Table(
    "provider_api_keys", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("label", String(255), nullable=True),
    Column("api_key_ciphertext", String(8192), nullable=False),
    Column("secret_ref", String(255), nullable=False),
    Column("position", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("cooldown_until", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "provider", "position", name="uq_provider_api_keys_user_provider_position"),
)
Index("ix_provider_api_keys_user_provider", provider_api_keys.c.user_id, provider_api_keys.c.provider)
```

- [ ] **Step 2: Add `provider_api_keys` to `__all__`**

In the `__all__` list near the end of `schema.py`, add a line right after `"provider_configurations",`:

```python
    "provider_configurations",
    "provider_api_keys",
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run: `python -c "from agentos.persistence.postgres.schema import provider_api_keys, provider_configurations; print(sorted(c.name for c in provider_api_keys.columns)); print(sorted(c.name for c in provider_configurations.columns))"`
Expected: two sorted column-name lists print with no traceback; `provider_configurations`'s list no longer contains `api_key` or `api_key_ciphertext`.

- [ ] **Step 4: Commit**

```bash
git add src/agentos/persistence/postgres/schema.py
git commit -m "feat(schema): add provider_api_keys table and key_cooldown_seconds column"
```

---

### Task 2: Migration `0038_provider_api_keys`

**Files:**
- Create: `src/agentos/persistence/postgres/migrations/versions/0038_provider_api_keys.py`

- [ ] **Step 1: Write the migration**

```python
"""add provider_api_keys table for multi-key fallback per provider

Revision ID: 0038_provider_api_keys
Revises: 0037_plugin_commands_and_hooks
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_provider_api_keys"
down_revision = "0037_plugin_commands_and_hooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("api_key_ciphertext", sa.String(8192), nullable=False),
        sa.Column("secret_ref", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", "position", name="uq_provider_api_keys_user_provider_position"),
    )
    op.create_index("ix_provider_api_keys_user_provider", "provider_api_keys", ["user_id", "provider"])
    op.add_column("provider_configurations", sa.Column("key_cooldown_seconds", sa.Integer(), nullable=False, server_default="60"))
    op.execute(sa.text(
        "INSERT INTO provider_api_keys "
        "(user_id, provider, label, api_key_ciphertext, secret_ref, position, status, cooldown_until, created_at, updated_at) "
        "SELECT user_id, provider, NULL, api_key_ciphertext, secret_ref, 0, 'active', NULL, updated_at, updated_at "
        "FROM provider_configurations WHERE api_key_ciphertext IS NOT NULL"
    ))
    with op.batch_alter_table("provider_configurations") as batch:
        batch.drop_column("api_key")
        batch.drop_column("api_key_ciphertext")


def downgrade() -> None:
    with op.batch_alter_table("provider_configurations") as batch:
        batch.add_column(sa.Column("api_key", sa.String(4096), nullable=True))
        batch.add_column(sa.Column("api_key_ciphertext", sa.String(8192), nullable=True))
    op.execute(sa.text(
        "UPDATE provider_configurations SET api_key_ciphertext = ("
        "SELECT api_key_ciphertext FROM provider_api_keys "
        "WHERE provider_api_keys.user_id = provider_configurations.user_id "
        "AND provider_api_keys.provider = provider_configurations.provider "
        "AND provider_api_keys.position = 0)"
    ))
    with op.batch_alter_table("provider_configurations") as batch:
        batch.drop_column("key_cooldown_seconds")
    op.drop_index("ix_provider_api_keys_user_provider", table_name="provider_api_keys")
    op.drop_table("provider_api_keys")
```

- [ ] **Step 2: Run the migration against a throwaway SQLite database and verify both directions**

Run:
```bash
python -c "
from sqlalchemy import create_engine
from agentos.persistence.postgres.migrate import upgrade, downgrade
engine = create_engine('sqlite:///./_migration_check.db')
upgrade(engine)
downgrade(engine)
upgrade(engine)
print('ok')
"
del _migration_check.db 2>NUL || rm -f _migration_check.db
```
Expected: prints `ok` with no traceback (upgrade, downgrade, upgrade again all succeed — this exercises both the batch column drops on SQLite and the data backfill).

- [ ] **Step 3: Commit**

```bash
git add src/agentos/persistence/postgres/migrations/versions/0038_provider_api_keys.py
git commit -m "feat(migration): add provider_api_keys table, backfill from provider_configurations"
```

---

### Task 3: Persistence adapter — key CRUD

**Files:**
- Create: `src/agentos/persistence/postgres/provider_api_keys.py`
- Test: `tests/unit/persistence/test_provider_api_keys.py`

- [ ] **Step 1: Write the failing tests for list/add/rename/remove**

Create `tests/unit/persistence/test_provider_api_keys.py`:

```python
from sqlalchemy import create_engine

from agentos.api.contracts import ApplicationNotFoundError
from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
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
    import pytest
    with pytest.raises(ValueError):
        _adapter().add_key(_query(api_key="abc"))


def test_renaming_a_key_updates_only_its_label() -> None:
    adapter = _adapter()
    key = adapter.add_key(_query(api_key="sk-first-key", label="old"))

    renamed = adapter.rename_key(_query(key_id=key["id"], label="new"))

    assert renamed["label"] == "new"
    assert renamed["position"] == 0


def test_renaming_a_key_that_does_not_exist_raises_not_found() -> None:
    import pytest
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
    import pytest
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
    import pytest
    adapter = _adapter()
    adapter.add_key(_query(api_key="sk-first-key"))

    with pytest.raises(ValueError):
        adapter.reorder_keys(_query(ordered_ids=[999]))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/persistence/test_provider_api_keys.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentos.persistence.postgres.provider_api_keys'`

- [ ] **Step 3: Write the adapter**

Create `src/agentos/persistence/postgres/provider_api_keys.py`:

```python
"""Production adapter for per-provider API key storage and fallback pooling.

Sibling of ``provider_configuration.py``: that module owns one row of
provider-level settings (enabled/base_url/model/key_cooldown_seconds) per
``(user_id, provider)``; this module owns N credential rows per
``(user_id, provider)``, ordered by ``position`` (0 = principal, tried first
on every new request). See docs/superpowers/specs/
2026-08-18-multi-api-key-provider-fallback-design.md.

A key is never included in any returned dict beyond its ciphertext-free
public shape (``id``/``label``/``position``/``status``/``cooldown_until``);
``agentos.api.gateway._provider_key_public`` additionally allowlists exactly
those fields server-side, so this is defense in depth, not the only guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.contracts import ApplicationNotFoundError

from .schema import provider_api_keys
from agentos.persistence.provider_secrets import ProviderSecretCipher


@dataclass(frozen=True, slots=True)
class ProviderApiKeyCredential:
    """A decrypted key ready to build a transport with; never logged or returned to a client."""

    id: int
    plaintext: str


def _public(row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "label": row["label"],
        "position": int(row["position"]),
        "status": str(row["status"]),
        "cooldown_until": row["cooldown_until"],
    }


class PostgresProviderApiKeyAdapter:
    """Production adapter for the ``ProviderApiKeyApplication`` port."""

    def __init__(self, engine: Engine, *, cipher: ProviderSecretCipher | None = None) -> None:
        self._engine = engine
        self._cipher = cipher or ProviderSecretCipher.from_environment()

    def list_keys(self, query: dict[str, object]) -> list[dict[str, object]]:
        provider, user_id = str(query["provider"]), str(query["user_id"])
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                ).order_by(provider_api_keys.c.position)
            ).mappings().all()
        return [_public(row) for row in rows]

    def add_key(self, command: dict[str, object]) -> dict[str, object]:
        provider, user_id = str(command["provider"]), str(command["user_id"])
        api_key = str(command.get("api_key") or "").strip()
        if len(api_key) < 4:
            raise ValueError("provider API key is required")
        label = command.get("label")
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            next_position = connection.execute(
                select(func.coalesce(func.max(provider_api_keys.c.position), -1) + 1).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                )
            ).scalar_one()
            secret_ref = f"provider-key-secret:{uuid4().hex}"
            result = connection.execute(
                insert(provider_api_keys).values(
                    user_id=user_id, provider=provider, label=str(label) if label else None,
                    api_key_ciphertext=self._cipher.encrypt(api_key), secret_ref=secret_ref,
                    position=next_position, status="active", cooldown_until=None,
                    created_at=now, updated_at=now,
                )
            )
            row_id = result.inserted_primary_key[0]
        return _public({"id": row_id, "label": str(label) if label else None, "position": next_position, "status": "active", "cooldown_until": None})

    def rename_key(self, command: dict[str, object]) -> dict[str, object]:
        provider, user_id, key_id = str(command["provider"]), str(command["user_id"]), int(command["key_id"])
        label = command.get("label")
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.id == key_id, provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                )
            ).mappings().first()
            if existing is None:
                raise ApplicationNotFoundError(str(key_id))
            connection.execute(
                update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(
                    label=str(label) if label else None, updated_at=datetime.now(UTC),
                )
            )
        return _public({**existing, "label": str(label) if label else None})

    def remove_key(self, command: dict[str, object]) -> None:
        provider, user_id, key_id = str(command["provider"]), str(command["user_id"]), int(command["key_id"])
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.id == key_id, provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                )
            ).mappings().first()
            if existing is None:
                raise ApplicationNotFoundError(str(key_id))
            connection.execute(delete(provider_api_keys).where(provider_api_keys.c.id == key_id))
            remaining = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                ).order_by(provider_api_keys.c.position)
            ).mappings().all()
            for new_position, row in enumerate(remaining):
                if row["position"] != new_position:
                    connection.execute(
                        update(provider_api_keys).where(provider_api_keys.c.id == row["id"]).values(position=new_position)
                    )

    def reorder_keys(self, command: dict[str, object]) -> list[dict[str, object]]:
        provider, user_id = str(command["provider"]), str(command["user_id"])
        ordered_ids = [int(item) for item in command["ordered_ids"]]
        with self._engine.begin() as connection:
            existing_ids = connection.execute(
                select(provider_api_keys.c.id).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                )
            ).scalars().all()
            if set(existing_ids) != set(ordered_ids) or len(ordered_ids) != len(set(ordered_ids)):
                raise ValueError("ordered_ids must list every key for this provider exactly once")
            # Two passes avoid colliding with the (user_id, provider, position)
            # unique constraint while positions are still in flight.
            for offset, key_id in enumerate(ordered_ids):
                connection.execute(
                    update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(position=1_000 + offset, updated_at=datetime.now(UTC))
                )
            for position, key_id in enumerate(ordered_ids):
                connection.execute(
                    update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(position=position)
                )
        return self.list_keys({"provider": provider, "user_id": user_id})

    def next_available_key(self, user_id: str, provider: str, *, exclude: frozenset[int] = frozenset()) -> ProviderApiKeyCredential | None:
        """Earliest-position key not yet tried; falls back to the earliest-position
        key overall (ignoring cooldown) once every untried key is in cooldown."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                ).order_by(provider_api_keys.c.position)
            ).mappings().all()
        candidates = [row for row in rows if row["id"] not in exclude]
        if not candidates:
            return None
        now = datetime.now(UTC)
        available = [row for row in candidates if row["status"] != "cooldown" or row["cooldown_until"] is None or row["cooldown_until"] <= now]
        chosen = available[0] if available else candidates[0]
        return ProviderApiKeyCredential(id=int(chosen["id"]), plaintext=self._cipher.decrypt(str(chosen["api_key_ciphertext"])))

    def mark_cooldown(self, key_id: int, cooldown_seconds: int) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(
                    status="cooldown", cooldown_until=datetime.now(UTC) + timedelta(seconds=cooldown_seconds), updated_at=datetime.now(UTC),
                )
            )

    def mark_active(self, key_id: int) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(
                    status="active", cooldown_until=None, updated_at=datetime.now(UTC),
                )
            )

    def set_primary_key(self, command: dict[str, object]) -> dict[str, object]:
        """Upsert the position-0 key. The seam ``configure()``'s legacy single-key
        ``apiKey`` field targets, so ``PUT /v1/providers/{provider}`` keeps working
        exactly as it did before this feature existed."""
        provider, user_id = str(command["provider"]), str(command["user_id"])
        api_key = str(command["api_key"])
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider, provider_api_keys.c.position == 0,
                )
            ).mappings().first()
            ciphertext = self._cipher.encrypt(api_key)
            if existing is None:
                secret_ref = f"provider-key-secret:{uuid4().hex}"
                result = connection.execute(
                    insert(provider_api_keys).values(
                        user_id=user_id, provider=provider, label=None, api_key_ciphertext=ciphertext,
                        secret_ref=secret_ref, position=0, status="active", cooldown_until=None,
                        created_at=now, updated_at=now,
                    )
                )
                row_id = result.inserted_primary_key[0]
            else:
                connection.execute(
                    update(provider_api_keys).where(provider_api_keys.c.id == existing["id"]).values(
                        api_key_ciphertext=ciphertext, status="active", cooldown_until=None, updated_at=now,
                    )
                )
                row_id = existing["id"]
        return _public({"id": row_id, "label": None, "position": 0, "status": "active", "cooldown_until": None})

    def clear_primary_key(self, command: dict[str, object]) -> None:
        """Delete the position-0 key, mirroring ``configure()`` being called with
        an empty key for a provider whose key is optional (local Ollama, OmniRoute)."""
        provider, user_id = str(command["provider"]), str(command["user_id"])
        with self._engine.begin() as connection:
            connection.execute(
                delete(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider, provider_api_keys.c.position == 0,
                )
            )


__all__ = ["PostgresProviderApiKeyAdapter", "ProviderApiKeyCredential"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/persistence/test_provider_api_keys.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/persistence/postgres/provider_api_keys.py tests/unit/persistence/test_provider_api_keys.py
git commit -m "feat(persistence): add PostgresProviderApiKeyAdapter for key CRUD"
```

---

### Task 4: Persistence adapter — fallback pool methods

**Files:**
- Modify: `tests/unit/persistence/test_provider_api_keys.py` (append)

`next_available_key`/`mark_cooldown`/`mark_active` were written in Task 3 (they live in the same file so the pool and the CRUD share one adapter instance in production). This task adds their tests, which Task 3 intentionally deferred to keep that task's diff reviewable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/persistence/test_provider_api_keys.py`:

```python
from datetime import UTC, datetime, timedelta

from agentos.persistence.postgres.provider_api_keys import ProviderApiKeyCredential


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
        from agentos.persistence.postgres.schema import provider_api_keys as table
        from sqlalchemy import update
        connection.execute(update(table).where(table.c.id == first["id"]).values(cooldown_until=datetime.now(UTC) - timedelta(seconds=1)))

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/persistence/test_provider_api_keys.py -v -k "next_available or mark_active or primary_key"`
Expected: FAIL at import (`ProviderApiKeyCredential` import fails only if Task 3 wasn't applied — since it was, these should mostly pass already). If any fail on behavior, note which assertion fails before proceeding.

- [ ] **Step 3: Fix any failing assertion**

The adapter code from Task 3 already implements all of these methods; this step exists in case a test above reveals an edge case Task 3 missed (e.g., an off-by-one in `next_available_key`'s cooldown comparison). Adjust `src/agentos/persistence/postgres/provider_api_keys.py` as needed until every test passes.

- [ ] **Step 4: Run the full file to verify everything passes**

Run: `python -m pytest tests/unit/persistence/test_provider_api_keys.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/persistence/test_provider_api_keys.py
git commit -m "test(persistence): cover the key fallback pool and primary-key upsert"
```

---

### Task 5: Rewire `PostgresProviderConfigurationAdapter` onto the new key table

**Files:**
- Modify: `src/agentos/persistence/postgres/provider_configuration.py`
- Modify: `tests/unit/persistence/test_provider_configuration.py`

- [ ] **Step 1: Update the two tests that read the removed columns directly**

In `tests/unit/persistence/test_provider_configuration.py`, replace `test_omniroute_configuration_persists_an_empty_gateway_key_encrypted` (lines 10-34):

```python
def test_omniroute_configuration_with_an_empty_key_creates_no_key_row() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    cipher = ProviderSecretCipher("unit-test-provider-key")
    configuration = PostgresProviderConfigurationAdapter(engine, cipher=cipher)

    saved = configuration.configure({
        "provider": "omniroute",
        "user_id": "local-user",
        "enabled": True,
        "api_key": "",
        "base_url": "http://127.0.0.1:20128/v1",
    })

    with engine.connect() as connection:
        keys = connection.execute(
            select(provider_api_keys).where(
                provider_api_keys.c.user_id == "local-user",
                provider_api_keys.c.provider == "omniroute",
            )
        ).mappings().all()

    assert saved["enabled"] is True
    assert keys == []
```

Add `provider_api_keys` to the existing schema import at the top of the file:

```python
from agentos.persistence.postgres.schema import metadata, provider_api_keys, provider_configurations
```

Replace `test_ollama_cloud_key_is_trimmed_before_storage` (lines 72-84):

```python
def test_ollama_cloud_key_is_trimmed_before_storage() -> None:
    adapter = _adapter()
    adapter.configure(_command(base_url="https://ollama.com", api_key="  cloud-secret\n"))

    with adapter._engine.connect() as connection:
        stored = connection.execute(
            select(provider_api_keys.c.api_key_ciphertext).where(
                provider_api_keys.c.user_id == "user-1",
                provider_api_keys.c.provider == "ollama",
                provider_api_keys.c.position == 0,
            )
        ).scalar_one()

    assert adapter._cipher.decrypt(stored) == "cloud-secret"
```

Update the `_adapter()` test helper (around line 37-40) to also create `provider_api_keys`, since `configure()` will now write to it:

```python
def _adapter() -> PostgresProviderConfigurationAdapter:
    engine = create_engine("sqlite://")
    metadata.create_all(engine, tables=[provider_configurations, provider_api_keys])
    return PostgresProviderConfigurationAdapter(engine, cipher=ProviderSecretCipher(b"0" * 32))
```

- [ ] **Step 2: Run the full test file to see the current (pre-adapter-change) failures**

Run: `python -m pytest tests/unit/persistence/test_provider_configuration.py -v`
Expected: FAIL — `provider_configurations` (as currently defined after Task 1) has no `api_key`/`api_key_ciphertext` columns, so `configure()`'s existing `insert`/`update` calls (which still reference them) raise `TypeError`/`AttributeError`.

- [ ] **Step 3: Rewrite `provider_configuration.py`**

Replace the module docstring (lines 1-33) — the old text claims cleartext storage, which was already stale before this change:

```python
"""Production ``ProviderConfigurationApplication`` adapter (frontend Fase D).

Satisfies ``agentos.api.contracts.ProviderConfigurationApplication`` over a
dedicated ``provider_configurations`` table, scoped by ``(user_id,
provider)`` — there is no execution/agent scope for a provider credential
(BACKEND_CAPABILITY_MATRIX.md/BACKEND_DISCOVERY.md have never described one;
it is configured once per user, independent of any execution), so this does
not reuse ``persistence_records``'s execution-shaped ownership tuple (see
"Decisões locais" in docs/frontend/IMPLEMENTATION_PLAN.md, Fase D).

This table holds only provider-level settings: ``enabled``/``base_url``/
``key_cooldown_seconds``. Credentials live in ``provider_api_keys``
(``agentos.persistence.postgres.provider_api_keys``), one row per key, so a
provider can have more than one — see docs/superpowers/specs/
2026-08-18-multi-api-key-provider-fallback-design.md. ``configure()``'s
``api_key`` field is kept for backward compatibility: it upserts the
position-0 ("principal") key through ``PostgresProviderApiKeyAdapter``.
Every credential is encrypted at rest via ``ProviderSecretCipher``
(``enc:v1:...``); a plaintext value is never stored.

The API key is never included in any returned dict: ``configure``/
``inspect``/``revoke`` all return a small public projection
  (``provider``/``enabled``/``secret_ref``/``key_cooldown_seconds``),
matching the shape ``FakeProviderConfiguration`` already establishes in
``tests/unit/api/test_api_asgi.py``. ``agentos.api.gateway._provider_public``
additionally strips any field whose name contains ``api_key``/``secret``/
``token``/``password``/``credential`` server-side, so this is defense in
depth, not the only guard.

``configure`` is a plain upsert scoped by ``(user_id, provider)``: PUT is
already idempotent by HTTP semantics (repeating the same PUT converges to
the same stored state), so there is no separate idempotency-key ledger here
— unlike execution commands, a provider configuration has no
non-execution-scoped idempotency store to reuse, and inventing one for a
single-row upsert would add nothing PUT doesn't already give for free.
"""
```

Replace the imports (lines 35-51) to add the key adapter and drop nothing else:

```python
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.contracts import ApplicationNotFoundError, ProviderCredentialRejectedError

from .schema import provider_configurations
from .provider_api_keys import PostgresProviderApiKeyAdapter
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL, PROVIDERS_WITH_OPTIONAL_KEY
from agentos.provider_catalog.ollama import DEFAULT_OLLAMA_BASE_URL, OllamaCatalogClient, OllamaCloudAuthenticationError, is_ollama_cloud, normalize_ollama_base_url
from agentos.provider_catalog.omniroute import DEFAULT_OMNIROUTE_BASE_URL, normalize_omniroute_base_url
from agentos.provider_catalog.omniroute import OmniRouteCatalogClient
from agentos.provider_catalog.installation import OmniRouteInstaller
```

Replace `_public` (lines 54-61):

```python
def _public(row) -> dict[str, object]:
    return {
        "provider": str(row["provider"]),
        "enabled": bool(row["enabled"]),
        "secret_ref": str(row["secret_ref"]),
        "key_cooldown_seconds": int(row["key_cooldown_seconds"]),
        "catalog_refreshed_at": row.get("catalog_refreshed_at"),
        **({"base_url": row["base_url"]} if str(row["provider"]) in PROVIDERS_WITH_BASE_URL and row.get("base_url") else {}),
    }
```

Replace `__init__` (lines 67-70) and `configure` (lines 72-102):

```python
    def __init__(self, engine: Engine, *, cipher: ProviderSecretCipher | None = None, installer: OmniRouteInstaller | None = None, keys: PostgresProviderApiKeyAdapter | None = None) -> None:
        self._engine = engine
        self._cipher = cipher or ProviderSecretCipher.from_environment()
        self._installer = installer or OmniRouteInstaller()
        self._keys = keys or PostgresProviderApiKeyAdapter(engine, cipher=self._cipher)

    def configure(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command["provider"])
        user_id = str(command["user_id"])
        enabled = bool(command["enabled"])
        base_url = _base_url(provider, command.get("base_url"))
        api_key = _api_key(provider, command.get("api_key"), base_url)
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_configurations).where(
                    provider_configurations.c.user_id == user_id,
                    provider_configurations.c.provider == provider,
                )
            ).mappings().first()
            if existing is None:
                secret_ref = f"provider-secret:{uuid4().hex}"
                connection.execute(
                    insert(provider_configurations).values(
                        user_id=user_id, provider=provider, enabled=enabled,
                        base_url=base_url, secret_ref=secret_ref, key_cooldown_seconds=60,
                        catalog_refreshed_at=None, created_at=now, updated_at=now,
                    )
                )
                row = {"provider": provider, "enabled": enabled, "base_url": base_url, "secret_ref": secret_ref, "key_cooldown_seconds": 60, "catalog_refreshed_at": None}
            else:
                connection.execute(
                    update(provider_configurations).where(provider_configurations.c.id == existing["id"]).values(
                        enabled=enabled, base_url=base_url, catalog_refreshed_at=None, updated_at=now,
                    )
                )
                row = {"provider": provider, "enabled": enabled, "base_url": base_url, "secret_ref": existing["secret_ref"], "key_cooldown_seconds": existing["key_cooldown_seconds"], "catalog_refreshed_at": None}
        if api_key:
            self._keys.set_primary_key({"provider": provider, "user_id": user_id, "api_key": api_key})
        else:
            self._keys.clear_primary_key({"provider": provider, "user_id": user_id})
        return _public(row)

    def set_key_cooldown_seconds(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command["provider"])
        user_id = str(command["user_id"])
        seconds = int(command["seconds"])
        if seconds < 1:
            raise ValueError("key cooldown must be at least one second")
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_configurations).where(
                    provider_configurations.c.user_id == user_id,
                    provider_configurations.c.provider == provider,
                )
            ).mappings().first()
            if existing is None:
                raise ApplicationNotFoundError(provider)
            connection.execute(
                update(provider_configurations).where(provider_configurations.c.id == existing["id"]).values(
                    key_cooldown_seconds=seconds, updated_at=datetime.now(UTC),
                )
            )
        return _public({**existing, "key_cooldown_seconds": seconds})
```

`inspect`, `revoke`, `test_connection`, `install`, `installation_status` (lines 104-178) are unchanged — they never touched the key columns directly, and `_public()` now picks up `key_cooldown_seconds` for all of them automatically.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/persistence/test_provider_configuration.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/persistence/postgres/provider_configuration.py tests/unit/persistence/test_provider_configuration.py
git commit -m "feat(persistence): delegate provider_configuration's api_key to the key pool"
```

---

### Task 6: `ProviderApiKeyApplication` contract

**Files:**
- Modify: `src/agentos/api/contracts.py`

- [ ] **Step 1: Add the protocol**

In `src/agentos/api/contracts.py`, insert right after `ProviderConfigurationApplication` (after line 27, before `class ProviderModelCatalogApplication`):

```python
class ProviderApiKeyApplication(Protocol):
    """Application boundary for a provider's ordered API key fallback pool."""

    def list_keys(self, query: dict[str, object]) -> object: ...
    def add_key(self, command: dict[str, object]) -> object: ...
    def rename_key(self, command: dict[str, object]) -> object: ...
    def remove_key(self, command: dict[str, object]) -> object: ...
    def reorder_keys(self, command: dict[str, object]) -> object: ...
```

- [ ] **Step 2: Verify the module still imports**

Run: `python -c "from agentos.api.contracts import ProviderApiKeyApplication; print(ProviderApiKeyApplication)"`
Expected: prints the class with no traceback

- [ ] **Step 3: Commit**

```bash
git add src/agentos/api/contracts.py
git commit -m "feat(api): add the ProviderApiKeyApplication port"
```

---

### Task 7: Gateway routes for `/v1/providers/{provider}/keys`

**Files:**
- Modify: `src/agentos/api/gateway.py`
- Test: `tests/unit/api/test_provider_keys_gateway.py`

- [ ] **Step 1: Write the failing gateway tests**

The existing suite's pattern for a PAT-authenticated `TestClient` is in `tests/unit/api/test_api_asgi.py:130-134` (`_client()`): an `InMemorySecurityService` with `add_pat(token, AuthenticatedPrincipal(user_id, credential_ref, scopes))`, passed into `ApiServices(security=..., ...)`, then every mutating request carries `Authorization: Bearer <token>` and `Idempotency-Key: <key>` headers. This plan's new test file follows that exact pattern.

Create `tests/unit/api/test_provider_keys_gateway.py`:

```python
from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.api.contracts import ApplicationNotFoundError
from fastapi.testclient import TestClient


class _FakeProviderApiKeys:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, object]] = {}
        self._next_id = 1

    def list_keys(self, query: dict[str, object]) -> list[dict[str, object]]:
        return sorted(self.rows.values(), key=lambda row: row["position"])

    def add_key(self, command: dict[str, object]) -> dict[str, object]:
        row = {"id": self._next_id, "label": command.get("label"), "position": len(self.rows), "status": "active", "cooldown_until": None}
        self.rows[self._next_id] = row
        self._next_id += 1
        return row

    def rename_key(self, command: dict[str, object]) -> dict[str, object]:
        row = self.rows.get(int(command["key_id"]))
        if row is None:
            raise ApplicationNotFoundError(str(command["key_id"]))
        row["label"] = command.get("label")
        return row

    def remove_key(self, command: dict[str, object]) -> None:
        if int(command["key_id"]) not in self.rows:
            raise ApplicationNotFoundError(str(command["key_id"]))
        del self.rows[int(command["key_id"])]

    def reorder_keys(self, command: dict[str, object]) -> list[dict[str, object]]:
        ordered_ids = [int(item) for item in command["ordered_ids"]]
        for position, key_id in enumerate(ordered_ids):
            self.rows[key_id]["position"] = position
        return self.list_keys(command)


AUTH = {"Authorization": "Bearer pat-test"}


def _client(fake: _FakeProviderApiKeys) -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, provider_api_keys=fake))
    return TestClient(app)


def test_listing_keys_for_a_provider_with_none_returns_an_empty_array() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.get("/v1/providers/openai/keys", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == []


def test_adding_a_key_returns_it_without_the_plaintext() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.post(
        "/v1/providers/openai/keys", json={"api_key": "sk-abcdefgh", "label": "conta free 1"},
        headers={**AUTH, "Idempotency-Key": "add-1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "conta free 1"
    assert "api_key" not in response.text and "sk-abcdefgh" not in response.text


def test_renaming_a_missing_key_is_a_404() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.patch(
        "/v1/providers/openai/keys/999", json={"label": "x"},
        headers={**AUTH, "Idempotency-Key": "rename-1"},
    )

    assert response.status_code == 404


def test_removing_a_key_is_a_204() -> None:
    fake = _FakeProviderApiKeys()
    client = _client(fake)
    added = client.post(
        "/v1/providers/openai/keys", json={"api_key": "sk-abcdefgh"},
        headers={**AUTH, "Idempotency-Key": "add-1"},
    ).json()

    response = client.delete(f"/v1/providers/openai/keys/{added['id']}", headers={**AUTH, "Idempotency-Key": "remove-1"})

    assert response.status_code == 204
    assert client.get("/v1/providers/openai/keys", headers=AUTH).json() == []


def test_reordering_rejects_an_unsupported_provider_name() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.put(
        "/v1/providers/not-a-provider/keys:reorder", json={"ordered_ids": [1]},
        headers={**AUTH, "Idempotency-Key": "reorder-1"},
    )

    assert response.status_code == 422


def test_a_request_without_a_bearer_token_is_rejected() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.get("/v1/providers/openai/keys")

    assert response.status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/api/test_provider_keys_gateway.py -v`
Expected: FAIL — 404s from FastAPI (routes don't exist yet), since `ApiServices` doesn't accept `provider_api_keys=` yet either.

- [ ] **Step 3: Add `provider_api_keys` to `ApiServices`**

In `src/agentos/api/gateway.py`, add the parameter and import. First the import (near the other contracts imports at the top of the file — find `ProviderConfigurationApplication` in the imports and add `ProviderApiKeyApplication` next to it).

In `ApiServices.__init__` (around line 255), add the parameter right after `provider_configuration`:

```python
        provider_configuration: ProviderConfigurationApplication | None = None,
        provider_api_keys: ProviderApiKeyApplication | None = None,
```

And the assignment right after `self.provider_configuration = provider_configuration` (around line 282):

```python
        self.provider_configuration = provider_configuration
        self.provider_api_keys = provider_api_keys
```

- [ ] **Step 4: Add the request models**

Near `ProviderSetupRequest` (around line 101-106) in `gateway.py`, add:

```python
class ProviderApiKeyCreateRequest(_RequestModel):
    api_key: SecretStr = Field(min_length=4, max_length=4096)
    label: str | None = Field(default=None, max_length=255)


class ProviderApiKeyRenameRequest(_RequestModel):
    label: str | None = Field(default=None, max_length=255)


class ProviderApiKeyReorderRequest(_RequestModel):
    ordered_ids: list[int] = Field(min_length=1, max_length=64)


class ProviderKeyCooldownRequest(_RequestModel):
    seconds: int = Field(ge=1, le=86400)
```

- [ ] **Step 5: Add the routes**

In `gateway.py`, right after the `revoke_provider` route (after line 1219, before `@app.post("/v1/providers/omniroute/test")`), add:

```python
    @app.get("/v1/providers/{provider}/keys")
    async def list_provider_keys(provider: str, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request)
        services.security.check_rate_limit(principal, action="provider.keys.list", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.keys.list", resource_id=provider_name, purpose="provider.keys.list")
        result = _require_port(services.provider_api_keys).list_keys({"provider": provider_name, "user_id": principal.user_id})
        return JSONResponse([_provider_key_public(item) for item in result])

    @app.post("/v1/providers/{provider}/keys", status_code=201)
    async def add_provider_key(provider: str, payload: ProviderApiKeyCreateRequest, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.keys.add", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.keys.add", resource_id=provider_name, purpose="provider.keys.add")
        result = _require_port(services.provider_api_keys).add_key({
            "provider": provider_name, "user_id": principal.user_id,
            "api_key": payload.api_key.get_secret_value(), "label": payload.label,
            "idempotency_key": _idempotency(request),
        })
        return JSONResponse(_provider_key_public(result), status_code=201)

    @app.patch("/v1/providers/{provider}/keys/{key_id}")
    async def rename_provider_key(provider: str, key_id: int, payload: ProviderApiKeyRenameRequest, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.keys.rename", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.keys.rename", resource_id=provider_name, purpose="provider.keys.rename")
        result = _require_port(services.provider_api_keys).rename_key({
            "provider": provider_name, "user_id": principal.user_id, "key_id": key_id, "label": payload.label,
            "idempotency_key": _idempotency(request),
        })
        return JSONResponse(_provider_key_public(result))

    @app.delete("/v1/providers/{provider}/keys/{key_id}", status_code=204)
    async def remove_provider_key(provider: str, key_id: int, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.keys.remove", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.keys.remove", resource_id=provider_name, purpose="provider.keys.remove")
        _require_port(services.provider_api_keys).remove_key({
            "provider": provider_name, "user_id": principal.user_id, "key_id": key_id,
            "idempotency_key": _idempotency(request),
        })
        return JSONResponse(status_code=204, content=None)

    @app.put("/v1/providers/{provider}/keys:reorder")
    async def reorder_provider_keys(provider: str, payload: ProviderApiKeyReorderRequest, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.keys.reorder", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.keys.reorder", resource_id=provider_name, purpose="provider.keys.reorder")
        result = _require_port(services.provider_api_keys).reorder_keys({
            "provider": provider_name, "user_id": principal.user_id,
            "ordered_ids": payload.ordered_ids, "idempotency_key": _idempotency(request),
        })
        return JSONResponse([_provider_key_public(item) for item in result])

    @app.put("/v1/providers/{provider}/keys:cooldown")
    async def set_provider_key_cooldown(provider: str, payload: ProviderKeyCooldownRequest, request: Request) -> JSONResponse:
        provider_name = _provider_name(provider)
        principal = principal_for(request, mutable=True)
        services.security.check_rate_limit(principal, action="provider.configure", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="provider.configure", resource_id=provider_name, purpose="provider.configure")
        result = _require_port(services.provider_configuration).set_key_cooldown_seconds({
            "provider": provider_name, "user_id": principal.user_id, "seconds": payload.seconds,
            "idempotency_key": _idempotency(request),
        })
        return JSONResponse(_provider_public(result))
```

- [ ] **Step 6: Add the `_provider_key_public` allowlist helper**

Right after `_provider_public` (after line 1496) in `gateway.py`, add:

```python
def _provider_key_public(value: object) -> dict[str, object]:
    data = _jsonable(value)
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        raise ValueError("provider key response is invalid")
    cooldown_until = data.get("cooldown_until")
    return {
        "id": data["id"],
        "label": data.get("label"),
        "position": data.get("position"),
        "status": data.get("status"),
        "cooldown_until": cooldown_until.isoformat() if isinstance(cooldown_until, datetime) else None,
    }
```

- [ ] **Step 7: Run the tests, adjusting the test file's auth/CSRF setup as needed**

Run: `python -m pytest tests/unit/api/test_provider_keys_gateway.py -v`
Expected: PASS. If failures are about authentication/CSRF rather than the routes themselves, fix the test file's request setup to match the pattern found in Step 1 (this is expected iteration, not a sign the route code is wrong).

- [ ] **Step 8: Commit**

```bash
git add src/agentos/api/gateway.py tests/unit/api/test_provider_keys_gateway.py
git commit -m "feat(api): add /v1/providers/{provider}/keys CRUD and reorder/cooldown routes"
```

---

### Task 8: Wire the key adapter into production

**Files:**
- Modify: `src/agentos/bootstrap/production.py`

- [ ] **Step 1: Import and construct the adapter**

In `src/agentos/bootstrap/production.py`, add the import next to `PostgresProviderConfigurationAdapter` (line 33):

```python
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
```

Where `provider_configuration=PostgresProviderConfigurationAdapter(engine, cipher=provider_cipher)` is passed into `ApiServices(...)` (line 283), add a sibling line:

```python
        provider_configuration=PostgresProviderConfigurationAdapter(engine, cipher=provider_cipher),
        provider_api_keys=PostgresProviderApiKeyAdapter(engine, cipher=provider_cipher),
```

- [ ] **Step 2: Verify the production app still boots**

Run: `python -c "from agentos.bootstrap.production import create_production_app, ProductionSettings; print('ok')"`
Expected: prints `ok` with no traceback (this only checks the module imports and wires without error; it does not start a server)

- [ ] **Step 3: Commit**

```bash
git add src/agentos/bootstrap/production.py
git commit -m "feat(bootstrap): wire PostgresProviderApiKeyAdapter into ApiServices"
```

---

### Task 9: Classify key-shaped HTTP failures in `HTTPProviderStreamTransport`

**Files:**
- Modify: `src/agentos/agentic/provider_stream.py`
- Test: `tests/unit/agentic/test_provider_stream_key_rejection.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agentic/test_provider_stream_key_rejection.py`:

```python
from __future__ import annotations

import httpx
import pytest

from agentos.agentic.provider_stream import HTTPProviderStreamTransport, ProviderKeyRejected


def _transport(handler) -> HTTPProviderStreamTransport:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HTTPProviderStreamTransport(provider="openrouter", base_url="https://example.test", api_key="k", model="m", client=client)


def _request() -> dict[str, object]:
    return {"messages": [{"role": "user", "content": "hi"}], "tools": [], "max_output_tokens": 16}


@pytest.mark.parametrize("status", [401, 403, 429])
def test_an_auth_or_rate_limit_status_raises_provider_key_rejected(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "no"}})

    with pytest.raises(ProviderKeyRejected):
        list(_transport(handler).stream(_request()))


def test_a_server_error_status_does_not_raise_provider_key_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "no"}})

    with pytest.raises(httpx.HTTPStatusError):
        list(_transport(handler).stream(_request()))


def test_a_connection_failure_raises_provider_key_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderKeyRejected):
        list(_transport(handler).stream(_request()))


def test_a_successful_response_still_streams_normally() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data: [DONE]\n")

    assert list(_transport(handler).stream(_request())) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/agentic/test_provider_stream_key_rejection.py -v`
Expected: FAIL — `ImportError: cannot import name 'ProviderKeyRejected'`

- [ ] **Step 3: Add `ProviderKeyRejected` and reclassify in `stream()`**

In `src/agentos/agentic/provider_stream.py`, add the exception class right after the `RateLimitInfo` dataclass (after line 41, before `NormalizedStreamItem`):

```python
class ProviderKeyRejected(Exception):
    """The response indicates the credential itself is the problem: an
    authentication/authorization/rate-limit HTTP status, or the connection
    could not be established at all. A caller with more than one API key
    for this provider can retry with a different one; any other error (a
    bad request, a provider-side failure, a mid-stream drop after content
    already flowed) is not this."""
```

Replace the `stream` method (lines 516-534):

```python
    def stream(self, request: Mapping[str, object]) -> Iterator[NormalizedStreamItem]:
        endpoint, headers, payload = self._request_for(
            list(request.get("messages") or []),
            list(request.get("tools") or []),
            request.get("tool_choice"),
            request.get("max_output_tokens"),
        )
        try:
            with self._client.stream("POST", endpoint, headers=headers, json=payload) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    if error.response.status_code in (401, 403, 429):
                        raise ProviderKeyRejected(str(error)) from error
                    raise
                limit = project_rate_limit_headers(response.headers)
                has_limit = any(value is not None for value in (limit.remaining, limit.reset_after_seconds, limit.limit))
                if has_limit:
                    yield NormalizedStreamItem(StreamKind.RATE_LIMIT, 1, rate_limit=limit)
                events = (
                    normalize_ndjson(response.iter_lines()) if self.provider == "ollama"
                    else normalize_sse(response.iter_lines(), provider=self.provider)
                )
                for item in events:
                    yield replace(item, sequence=item.sequence + (1 if has_limit else 0))
        except httpx.TransportError as error:
            raise ProviderKeyRejected(str(error)) from error
```

Add `ProviderKeyRejected` to `__all__` (line 541):

```python
__all__ = ["ANTHROPIC_REQUIRED_MAX_TOKENS", "HTTPProviderStreamTransport", "NormalizedStreamItem", "ProviderKeyRejected", "RateLimitInfo", "StreamKind", "normalize_ndjson", "normalize_sse", "project_rate_limit_headers"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/agentic/test_provider_stream_key_rejection.py -v`
Expected: all PASS

- [ ] **Step 5: Run the existing payload tests to confirm no regression**

Run: `python -m pytest tests/unit/agentic/test_provider_stream_payload.py -v`
Expected: all PASS (unchanged — those tests only ever hit the 200 path)

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/provider_stream.py tests/unit/agentic/test_provider_stream_key_rejection.py
git commit -m "feat(agentic): classify 401/403/429/connection failures as ProviderKeyRejected"
```

---

### Task 10: `MultiKeyProviderStreamTransport`

**Files:**
- Create: `src/agentos/agentic/provider_key_fallback.py`
- Test: `tests/unit/agentic/test_multi_key_provider_stream_transport.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agentic/test_multi_key_provider_stream_transport.py`:

```python
from __future__ import annotations

from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.agentic.provider_stream import NormalizedStreamItem, ProviderKeyRejected, StreamKind
from agentos.persistence.postgres.provider_api_keys import ProviderApiKeyCredential


class _FakeKeyPool:
    def __init__(self, credentials: list[ProviderApiKeyCredential]) -> None:
        self._order = list(credentials)
        self.cooldowns: list[tuple[int, int]] = []

    def next_available_key(self, user_id: str, provider: str, *, exclude: frozenset[int] = frozenset()):
        for credential in self._order:
            if credential.id not in exclude:
                return credential
        return None

    def mark_cooldown(self, key_id: int, cooldown_seconds: int) -> None:
        self.cooldowns.append((key_id, cooldown_seconds))


class _FakeTransport:
    def __init__(self, *, items=(), error: Exception | None = None, fail_after: int = 0) -> None:
        self._items = list(items)
        self._error = error
        self._fail_after = fail_after
        self.closed = False

    def stream(self, request):
        for index, item in enumerate(self._items):
            if self._error is not None and index == self._fail_after:
                raise self._error
            yield item
        if self._error is not None and self._fail_after >= len(self._items):
            raise self._error

    def close(self) -> None:
        self.closed = True


def _text(value: str) -> NormalizedStreamItem:
    return NormalizedStreamItem(StreamKind.TEXT, 1, text=value)


def test_the_principal_key_is_used_when_it_succeeds() -> None:
    key = ProviderApiKeyCredential(id=1, plaintext="sk-principal")
    pool = _FakeKeyPool([key])
    built: list[str] = []

    def factory(plaintext: str):
        built.append(plaintext)
        return _FakeTransport(items=[_text("hi")])

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=60, transport_factory=factory)

    items = list(transport.stream({}))

    assert built == ["sk-principal"]
    assert items == [_text("hi")]
    assert pool.cooldowns == []


def test_a_key_rejected_before_any_output_rotates_to_the_next_key() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="sk-first")
    second = ProviderApiKeyCredential(id=2, plaintext="sk-second")
    pool = _FakeKeyPool([first, second])
    built: list[str] = []

    def factory(plaintext: str):
        built.append(plaintext)
        if plaintext == "sk-first":
            return _FakeTransport(items=[], error=ProviderKeyRejected("nope"), fail_after=0)
        return _FakeTransport(items=[_text("hi")])

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=45, transport_factory=factory)

    items = list(transport.stream({}))

    assert built == ["sk-first", "sk-second"]
    assert items == [_text("hi")]
    assert pool.cooldowns == [(1, 45)]


def test_a_key_rejected_after_output_already_started_does_not_rotate() -> None:
    first = ProviderApiKeyCredential(id=1, plaintext="sk-first")
    second = ProviderApiKeyCredential(id=2, plaintext="sk-second")
    pool = _FakeKeyPool([first, second])
    built: list[str] = []

    def factory(plaintext: str):
        built.append(plaintext)
        return _FakeTransport(items=[_text("partial")], error=ProviderKeyRejected("dropped"), fail_after=1)

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=45, transport_factory=factory)

    stream = transport.stream({})
    assert next(stream) == _text("partial")
    import pytest
    with pytest.raises(ProviderKeyRejected):
        next(stream)

    assert built == ["sk-first"]
    assert pool.cooldowns == []


def test_every_key_rejected_before_output_re_raises_after_the_last_one() -> None:
    only = ProviderApiKeyCredential(id=1, plaintext="sk-only")
    pool = _FakeKeyPool([only])

    def factory(plaintext: str):
        return _FakeTransport(items=[], error=ProviderKeyRejected("nope"), fail_after=0)

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=30, transport_factory=factory)

    import pytest
    with pytest.raises(ProviderKeyRejected):
        list(transport.stream({}))

    assert pool.cooldowns == [(1, 30)]


def test_a_non_key_error_propagates_without_rotating() -> None:
    key = ProviderApiKeyCredential(id=1, plaintext="sk-only")
    pool = _FakeKeyPool([key])

    def factory(plaintext: str):
        return _FakeTransport(items=[], error=ValueError("bad request"), fail_after=0)

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=30, transport_factory=factory)

    import pytest
    with pytest.raises(ValueError):
        list(transport.stream({}))

    assert pool.cooldowns == []


def test_no_configured_keys_raises_immediately() -> None:
    pool = _FakeKeyPool([])
    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=30, transport_factory=lambda plaintext: _FakeTransport())

    import pytest
    with pytest.raises(ValueError):
        list(transport.stream({}))


def test_close_closes_the_last_active_inner_transport() -> None:
    key = ProviderApiKeyCredential(id=1, plaintext="sk-only")
    pool = _FakeKeyPool([key])
    built: list[_FakeTransport] = []

    def factory(plaintext: str):
        inner = _FakeTransport(items=[_text("hi")])
        built.append(inner)
        return inner

    transport = MultiKeyProviderStreamTransport(key_pool=pool, user_id="u", provider="openai", cooldown_seconds=30, transport_factory=factory)
    list(transport.stream({}))

    transport.close()

    assert built[0].closed is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/agentic/test_multi_key_provider_stream_transport.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentos.agentic.provider_key_fallback'`

- [ ] **Step 3: Write the wrapper**

Create `src/agentos/agentic/provider_key_fallback.py`:

```python
"""Rotates through a user's ordered provider API keys on a key-shaped failure.

Wraps a single ``HTTPProviderStreamTransport``-shaped ``.stream()`` call so
the seam ``chat.py`` already uses to hand the turn's provider to
``AgenticTurnRuntime`` (its ``provider_factory`` callback, built once per
turn) needs no changes at all: this class satisfies the same
``.stream(request) -> Iterator[NormalizedStreamItem]`` contract, and only
rotates keys when nothing has been yielded yet in the current attempt --
matching docs/superpowers/specs/2026-08-18-multi-api-key-provider-fallback-design.md
decision #4 (fallback only before the first token) without either transport
needing to know why.
"""
from __future__ import annotations

from typing import Callable, Iterator, Mapping

from .provider_stream import NormalizedStreamItem, ProviderKeyRejected


class MultiKeyProviderStreamTransport:
    def __init__(
        self,
        *,
        key_pool: object,
        user_id: str,
        provider: str,
        cooldown_seconds: int,
        transport_factory: Callable[[str], object],
    ) -> None:
        self._key_pool = key_pool
        self._user_id = user_id
        self._provider = provider
        self._cooldown_seconds = cooldown_seconds
        self._transport_factory = transport_factory
        self._transport: object | None = None

    def stream(self, request: Mapping[str, object]) -> Iterator[NormalizedStreamItem]:
        tried: set[int] = set()
        credential = self._key_pool.next_available_key(self._user_id, self._provider, exclude=frozenset(tried))
        if credential is None:
            raise ValueError("no api key configured for provider")
        while True:
            tried.add(credential.id)
            transport = self._transport_factory(credential.plaintext)
            self._transport = transport
            yielded = False
            try:
                for item in transport.stream(request):
                    yielded = True
                    yield item
                return
            except ProviderKeyRejected:
                if yielded:
                    raise
                self._key_pool.mark_cooldown(credential.id, self._cooldown_seconds)
                next_credential = self._key_pool.next_available_key(self._user_id, self._provider, exclude=frozenset(tried))
                if next_credential is None:
                    raise
                credential = next_credential

    def close(self) -> None:
        transport = self._transport
        if transport is not None and hasattr(transport, "close"):
            transport.close()


__all__ = ["MultiKeyProviderStreamTransport"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/agentic/test_multi_key_provider_stream_transport.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/provider_key_fallback.py tests/unit/agentic/test_multi_key_provider_stream_transport.py
git commit -m "feat(agentic): add MultiKeyProviderStreamTransport for key-level fallback"
```

---

### Task 11: Wire the wrapper into `chat.py`'s turn provider

**Files:**
- Modify: `src/agentos/workers/chat.py`

- [ ] **Step 1: Import the new pieces**

In `src/agentos/workers/chat.py`, add to the existing schema import (lines 38-44):

```python
from agentos.persistence.postgres.schema import (
    conversation_dispatches,
    provider_api_keys,
    provider_configurations,
    provider_model_catalog,
    provider_model_favorites,
    vision_model_selections,
)
```

Add these two imports next to the existing `HTTPProviderStreamTransport` import (line 17):

```python
from agentos.agentic.provider_stream import HTTPProviderStreamTransport
from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
```

- [ ] **Step 2: Replace `_provider_transport`**

Replace lines 409-412 of `chat.py`:

```python
    def _provider_transport(self, turn: dict[str, object]) -> object:
        provider = str(turn["provider"])
        user_id = str(turn["user_id"])
        num_ctx = self._num_ctx_for(turn) if provider == "ollama" else None
        model_id = str(turn["model_id"])
        with self.store._engine.connect() as c:
            config = c.execute(
                select(provider_configurations.c.base_url, provider_configurations.c.enabled, provider_configurations.c.key_cooldown_seconds)
                .where(provider_configurations.c.user_id == user_id, provider_configurations.c.provider == provider)
            ).mappings().first()
        if config is None or not config["enabled"]:
            raise ValueError("provider unavailable")
        base_url = self._base_url_for(provider, {"base_url": config["base_url"]})
        keys = PostgresProviderApiKeyAdapter(self.store._engine)

        def build(api_key: str) -> HTTPProviderStreamTransport:
            return HTTPProviderStreamTransport(provider=provider, base_url=base_url, api_key=api_key, model=model_id, num_ctx=num_ctx)

        has_any_key = keys.next_available_key(user_id, provider) is not None
        if not has_any_key:
            if provider not in PROVIDERS_WITH_BASE_URL:
                raise ValueError("provider credential is missing")
            return build("")
        return MultiKeyProviderStreamTransport(
            key_pool=keys, user_id=user_id, provider=provider,
            cooldown_seconds=int(config["key_cooldown_seconds"]), transport_factory=build,
        )
```

This method now only serves the turn's own provider — `_transport_for` (used by the vision reader and subagent child-provider factories) is untouched and keeps its existing single-key lookup; multi-key fallback is scoped to the main chat turn per this plan's header note.

- [ ] **Step 3: Run the existing chat worker tests**

Run: `python -m pytest tests/unit/workers/test_chat*.py -v -k provider_transport`

If no tests match that filter, run the whole file instead: `python -m pytest tests/unit/workers/ -v -k chat`
Expected: PASS. If an existing test constructed a `provider_configurations` row with `api_key`/`api_key_ciphertext` directly and asserted on `_provider_transport`'s return type being exactly `HTTPProviderStreamTransport`, update it to also insert a `provider_api_keys` row (position 0) instead, and relax the type assertion to duck-type on `.stream` — follow whatever that test's existing helper pattern is (likely similar to `_adapter()`/`_command()` in Task 3).

- [ ] **Step 4: Add a focused regression test for the zero-keys optional-provider path**

Find (or, if none exists, create) `tests/unit/workers/test_chat_provider_transport.py` and add:

```python
from datetime import UTC, datetime

from sqlalchemy import create_engine, insert

from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.persistence.postgres.schema import metadata, provider_api_keys, provider_configurations


def _engine_with_provider(provider: str, *, base_url: str | None, with_key: bool):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider=provider, enabled=True, base_url=base_url,
            secret_ref="ref", key_cooldown_seconds=60, catalog_refreshed_at=None,
            created_at=now, updated_at=now,
        ))
        if with_key:
            from agentos.persistence.provider_secrets import ProviderSecretCipher
            cipher = ProviderSecretCipher(b"0" * 32)
            connection.execute(insert(provider_api_keys).values(
                user_id="user-1", provider=provider, label=None,
                api_key_ciphertext=cipher.encrypt("sk-configured"), secret_ref="key-ref",
                position=0, status="active", cooldown_until=None, created_at=now, updated_at=now,
            ))
    return engine


def test_a_provider_with_at_least_one_key_gets_the_fallback_wrapper() -> None:
    from agentos.workers.chat import ChatWorker

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": _engine_with_provider("openai", base_url=None, with_key=True)})()

    transport = worker._provider_transport({"provider": "openai", "user_id": "user-1", "model_id": "gpt-test"})

    assert isinstance(transport, MultiKeyProviderStreamTransport)


def test_an_optional_key_provider_with_no_keys_gets_a_plain_transport() -> None:
    # OmniRoute (not Ollama) on purpose: an Ollama turn also calls
    # self._num_ctx_for(turn), which reaches into catalog/context-window
    # lookups this bare, hand-built ChatWorker instance does not set up.
    # OmniRoute exercises the same "optional key, zero keys configured"
    # branch in _provider_transport without that extra dependency.
    from agentos.agentic.provider_stream import HTTPProviderStreamTransport
    from agentos.workers.chat import ChatWorker

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": _engine_with_provider("omniroute", base_url="http://127.0.0.1:20128/v1", with_key=False)})()

    transport = worker._provider_transport({"provider": "omniroute", "user_id": "user-1", "model_id": "auto"})

    assert isinstance(transport, HTTPProviderStreamTransport)
```

`_provider_transport` lives on `ChatWorker` (`src/agentos/workers/chat.py:138`), confirmed by direct inspection while writing this plan — the test above uses that name directly.

- [ ] **Step 5: Run the new test file**

Run: `python -m pytest tests/unit/workers/test_chat_provider_transport.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentos/workers/chat.py tests/unit/workers/test_chat_provider_transport.py
git commit -m "feat(workers): build the turn's provider through the multi-key fallback pool"
```

---

### Task 12: Frontend API client — key CRUD and cooldown

**Files:**
- Modify: `frontend/src/api/providers.ts`

- [ ] **Step 1: Add types and functions**

In `frontend/src/api/providers.ts`, add after `ConfigureProviderInput` (after line 27):

```ts
export type ProviderApiKeyState = {
  id: number
  label: string | null
  position: number
  status: 'active' | 'cooldown'
  cooldownUntil: string | null
}
```

Add after `providerPath` (after line 45):

```ts
function providerKeysPath(provider: ProviderName): string {
  return `${providerPath(provider)}/keys`
}

export function listProviderKeys(client: ApiClient, provider: ProviderName, signal?: AbortSignal): Promise<ProviderApiKeyState[]> {
  return client.request({
    path: providerKeysPath(provider), signal,
    parse: (value) => {
      if (!Array.isArray(value)) throw invalidResponseError()
      return value.map(parseProviderApiKeyState)
    },
  })
}

export function addProviderKey(client: ApiClient, provider: ProviderName, input: { apiKey: string; label?: string }, intent = client.createMutationIntent()): Promise<ProviderApiKeyState> {
  return client.request({
    path: providerKeysPath(provider), method: 'POST', intent,
    body: { api_key: input.apiKey, ...(input.label ? { label: input.label } : {}) },
    parse: parseProviderApiKeyState,
  })
}

export function renameProviderKey(client: ApiClient, provider: ProviderName, keyId: number, label: string | null, intent = client.createMutationIntent()): Promise<ProviderApiKeyState> {
  return client.request({
    path: `${providerKeysPath(provider)}/${keyId}`, method: 'PATCH', intent,
    body: { label },
    parse: parseProviderApiKeyState,
  })
}

export function removeProviderKey(client: ApiClient, provider: ProviderName, keyId: number, intent = client.createMutationIntent()): Promise<void> {
  return client.request({
    path: `${providerKeysPath(provider)}/${keyId}`, method: 'DELETE', intent,
    parse: () => undefined,
  })
}

export function reorderProviderKeys(client: ApiClient, provider: ProviderName, orderedIds: number[], intent = client.createMutationIntent()): Promise<ProviderApiKeyState[]> {
  return client.request({
    path: `${providerKeysPath(provider)}:reorder`, method: 'PUT', intent,
    body: { ordered_ids: orderedIds },
    parse: (value) => {
      if (!Array.isArray(value)) throw invalidResponseError()
      return value.map(parseProviderApiKeyState)
    },
  })
}

export function setProviderKeyCooldownSeconds(client: ApiClient, provider: ProviderName, seconds: number, intent = client.createMutationIntent()): Promise<ProviderPublicState> {
  return client.request({
    path: `${providerKeysPath(provider)}:cooldown`, method: 'PUT', intent,
    body: { seconds },
    parse: parseProviderPublicState,
  })
}

function parseProviderApiKeyState(value: unknown): ProviderApiKeyState {
  const data = record(value)
  if (!Number.isInteger(data.id)) throw invalidResponseError()
  if (data.status !== 'active' && data.status !== 'cooldown') throw invalidResponseError()
  if (!Number.isInteger(data.position) || (data.position as number) < 0) throw invalidResponseError()
  return {
    id: data.id as number,
    label: nullableString(data.label),
    position: data.position as number,
    status: data.status,
    cooldownUntil: nullableString(data.cooldown_until),
  }
}
```

- [ ] **Step 2: Write a focused unit test for the parsers**

Create `frontend/tests/unit/providerApiKeys.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { addProviderKey, listProviderKeys, reorderProviderKeys } from '../../src/api/providers'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('provider API key client', () => {
  it('parses a listed key, including a cooldown timestamp', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json([
      { id: 1, label: 'conta free 1', position: 0, status: 'cooldown', cooldown_until: '2026-08-18T12:00:00Z' },
    ])))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const keys = await listProviderKeys(client, 'ollama')

    expect(keys).toEqual([{ id: 1, label: 'conta free 1', position: 0, status: 'cooldown', cooldownUntil: '2026-08-18T12:00:00Z' }])
  })

  it('sends the api key and label when adding a key', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ id: 2, label: null, position: 1, status: 'active', cooldown_until: null }, 201)))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })

    await addProviderKey(client, 'ollama', { apiKey: 'sk-second' })

    const [, init] = fetchImpl.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({ api_key: 'sk-second' })
  })

  it('reorders and returns the new ordering', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json([
      { id: 2, label: null, position: 0, status: 'active', cooldown_until: null },
      { id: 1, label: null, position: 1, status: 'active', cooldown_until: null },
    ])))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })

    const reordered = await reorderProviderKeys(client, 'ollama', [2, 1])

    expect(reordered.map((key) => key.id)).toEqual([2, 1])
    expect(String(fetchImpl.mock.calls[0][0])).toContain('/keys:reorder')
  })
})
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend && npx vitest run tests/unit/providerApiKeys.test.ts`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/providers.ts frontend/tests/unit/providerApiKeys.test.ts
git commit -m "feat(frontend): add the provider API key CRUD client"
```

---

### Task 13: `useProviderKeysState` hook

**Files:**
- Create: `frontend/src/features/providers/useProviderKeysState.ts`
- Test: `frontend/tests/unit/useProviderKeysState.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/useProviderKeysState.test.tsx`:

```tsx
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { useProviderKeysState } from '../../src/features/providers/useProviderKeysState'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('useProviderKeysState', () => {
  it('loads the key list on mount', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json([{ id: 1, label: null, position: 0, status: 'active', cooldown_until: null }])))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const { result } = renderHook(() => useProviderKeysState(client, 'ollama', { status: 'ready', csrfToken: 'csrf' }))

    await waitFor(() => expect(result.current.keys).toHaveLength(1))
    expect(result.current.keys[0].id).toBe(1)
  })

  it('appends a newly added key and clears the pending input', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      if (init?.method === 'POST') return Promise.resolve(json({ id: 2, label: 'conta paga', position: 1, status: 'active', cooldown_until: null }, 201))
      return Promise.resolve(json([]))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })

    const { result } = renderHook(() => useProviderKeysState(client, 'ollama', { status: 'ready', csrfToken: 'csrf' }))
    await waitFor(() => expect(result.current.load.status).toBe('loaded'))

    await act(async () => { await result.current.add('sk-second-key', 'conta paga') })

    expect(result.current.keys.map((key) => key.id)).toEqual([2])
  })

  it('removes a key from local state after the server confirms', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }))
      return Promise.resolve(json([{ id: 1, label: null, position: 0, status: 'active', cooldown_until: null }]))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })

    const { result } = renderHook(() => useProviderKeysState(client, 'ollama', { status: 'ready', csrfToken: 'csrf' }))
    await waitFor(() => expect(result.current.keys).toHaveLength(1))

    await act(async () => { await result.current.remove(1) })

    expect(result.current.keys).toHaveLength(0)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/useProviderKeysState.test.tsx`
Expected: FAIL — module `../../src/features/providers/useProviderKeysState` does not exist

- [ ] **Step 3: Write the hook**

Create `frontend/src/features/providers/useProviderKeysState.ts`:

```ts
import { useEffect, useRef, useState } from 'react'
import { ApiClient, type MutationIntent } from '../../api/client'
import type { BrowserSessionBootstrap } from '../../api/browserSession'
import { ApiError } from '../../api/errors'
import {
  addProviderKey, listProviderKeys, removeProviderKey, renameProviderKey, reorderProviderKeys,
  type ProviderApiKeyState, type ProviderName,
} from '../../api/providers'
import { toApiError } from './useProviderState'

export type ProviderKeysLoadState = { status: 'loading' } | { status: 'loaded' } | { status: 'unavailable'; error: ApiError }
export type ProviderKeysAction = 'add' | 'rename' | 'remove' | 'reorder' | null
export type ProviderKeysActionState = { pending: boolean; error: ApiError | null; kind: ProviderKeysAction }

export type ProviderKeysState = {
  load: ProviderKeysLoadState
  keys: ProviderApiKeyState[]
  action: ProviderKeysActionState
  add: (apiKey: string, label?: string) => Promise<void>
  rename: (keyId: number, label: string | null) => Promise<void>
  remove: (keyId: number) => Promise<void>
  moveUp: (keyId: number) => Promise<void>
  moveDown: (keyId: number) => Promise<void>
}

export function useProviderKeysState(client: ApiClient, provider: ProviderName, bootstrap: BrowserSessionBootstrap): ProviderKeysState {
  const [load, setLoad] = useState<ProviderKeysLoadState>({ status: 'loading' })
  const [keys, setKeys] = useState<ProviderApiKeyState[]>([])
  const [action, setAction] = useState<ProviderKeysActionState>({ pending: false, error: null, kind: null })
  const intents = useRef(new Map<string, MutationIntent>())

  useEffect(() => {
    const controller = new AbortController()
    listProviderKeys(client, provider, controller.signal).then((loaded) => {
      if (controller.signal.aborted) return
      setKeys(loaded)
      setLoad({ status: 'loaded' })
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return
      setLoad({ status: 'unavailable', error: toApiError(error) })
    })
    return () => controller.abort()
  }, [client, provider])

  const intentFor = (key: string) => {
    const existing = intents.current.get(key)
    if (existing) return existing
    const intent = client.createMutationIntent()
    intents.current.set(key, intent)
    return intent
  }

  async function add(apiKey: string, label?: string) {
    if (action.pending || !apiKey || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'add' })
    try {
      const created = await addProviderKey(client, provider, { apiKey, label }, intentFor('add'))
      intents.current.delete('add')
      setKeys((current) => [...current, created])
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'add' })
    }
  }

  async function rename(keyId: number, label: string | null) {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'rename' })
    try {
      const updated = await renameProviderKey(client, provider, keyId, label, intentFor(`rename:${keyId}`))
      intents.current.delete(`rename:${keyId}`)
      setKeys((current) => current.map((key) => key.id === updated.id ? updated : key))
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'rename' })
    }
  }

  async function remove(keyId: number) {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'remove' })
    try {
      await removeProviderKey(client, provider, keyId, intentFor(`remove:${keyId}`))
      intents.current.delete(`remove:${keyId}`)
      setKeys((current) => current.filter((key) => key.id !== keyId).map((key, index) => ({ ...key, position: index })))
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'remove' })
    }
  }

  async function move(keyId: number, direction: -1 | 1) {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    const index = keys.findIndex((key) => key.id === keyId)
    const swapWith = index + direction
    if (index < 0 || swapWith < 0 || swapWith >= keys.length) return
    const reordered = [...keys]
    ;[reordered[index], reordered[swapWith]] = [reordered[swapWith], reordered[index]]
    setAction({ pending: true, error: null, kind: 'reorder' })
    try {
      const saved = await reorderProviderKeys(client, provider, reordered.map((key) => key.id), intentFor('reorder'))
      intents.current.delete('reorder')
      setKeys(saved)
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'reorder' })
    }
  }

  return {
    load, keys, action,
    add, rename, remove,
    moveUp: (keyId: number) => move(keyId, -1),
    moveDown: (keyId: number) => move(keyId, 1),
  }
}
```

- [ ] **Step 4: Export `toApiError` from `useProviderState.ts` if it is not already exported**

`useProviderState.ts:167` already declares `export function toApiError` — no change needed there; this step is a verification only.

Run: `cd frontend && npx tsc --noEmit -p .`
Expected: no new type errors from this file's import of `toApiError`

- [ ] **Step 5: Run the hook's tests**

Run: `cd frontend && npx vitest run tests/unit/useProviderKeysState.test.tsx`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/providers/useProviderKeysState.ts frontend/tests/unit/useProviderKeysState.test.tsx
git commit -m "feat(frontend): add useProviderKeysState for the key fallback list"
```

---

### Task 14: `ProviderKeyList` component

**Files:**
- Create: `frontend/src/features/providers/ProviderKeyList.tsx`
- Test: `frontend/tests/unit/ProviderKeyList.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/ProviderKeyList.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ProviderKeyList } from '../../src/features/providers/ProviderKeyList'
import type { ProviderApiKeyState } from '../../src/api/providers'

function keys(): ProviderApiKeyState[] {
  return [
    { id: 1, label: 'conta free 1', position: 0, status: 'active', cooldownUntil: null },
    { id: 2, label: 'conta paga', position: 1, status: 'cooldown', cooldownUntil: '2099-01-01T00:00:00Z' },
  ]
}

const noop = () => {}

describe('ProviderKeyList', () => {
  it('labels the first key as principal and shows the cooldown status of the second', () => {
    render(<ProviderKeyList keys={keys()} pending={false} cooldownSeconds={60} onAdd={noop} onRename={noop} onRemove={noop} onMoveUp={noop} onMoveDown={noop} onCooldownSecondsChange={noop} />)

    expect(screen.getByText('conta free 1')).toBeInTheDocument()
    expect(screen.getByText('Principal')).toBeInTheDocument()
    expect(screen.getByText(/Em cooldown/)).toBeInTheDocument()
  })

  it('submits the new key and label, then clears the input', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<ProviderKeyList keys={[]} pending={false} cooldownSeconds={60} onAdd={onAdd} onRename={noop} onRemove={noop} onMoveUp={noop} onMoveDown={noop} onCooldownSecondsChange={noop} />)

    await user.type(screen.getByLabelText('Nova chave'), 'sk-second-key')
    await user.type(screen.getByLabelText('Apelido (opcional)'), 'conta paga')
    await user.click(screen.getByRole('button', { name: 'Adicionar chave' }))

    expect(onAdd).toHaveBeenCalledWith('sk-second-key', 'conta paga')
  })

  it('disables moving the first key up and the last key down', () => {
    render(<ProviderKeyList keys={keys()} pending={false} cooldownSeconds={60} onAdd={noop} onRename={noop} onRemove={noop} onMoveUp={noop} onMoveDown={noop} onCooldownSecondsChange={noop} />)

    const rows = screen.getAllByRole('listitem')
    expect(within(rows[0]).getByRole('button', { name: 'Mover para cima' })).toBeDisabled()
    expect(within(rows[1]).getByRole('button', { name: 'Mover para baixo' })).toBeDisabled()
  })

  it('submits a new cooldown value', async () => {
    const user = userEvent.setup()
    const onCooldownSecondsChange = vi.fn()
    render(<ProviderKeyList keys={keys()} pending={false} cooldownSeconds={60} onAdd={noop} onRename={noop} onRemove={noop} onMoveUp={noop} onMoveDown={noop} onCooldownSecondsChange={onCooldownSecondsChange} />)

    const input = screen.getByLabelText('Tempo de cooldown (s)')
    await user.clear(input)
    await user.type(input, '120')
    await user.click(screen.getByRole('button', { name: 'Salvar tempo de cooldown' }))

    expect(onCooldownSecondsChange).toHaveBeenCalledWith(120)
  })
})

function within(element: HTMLElement) {
  return { getByRole: (role: string, options: { name: string }) => element.querySelector(`[aria-label="${options.name}"]`) as HTMLElement }
}
```

If this codebase's test setup already exposes `@testing-library/react`'s own `within` globally or via a different import path, replace the hand-rolled `within` helper above with that import instead — check the top of `frontend/tests/unit/ProviderDetail.test.tsx` (it imports `within` from `@testing-library/react`) and use the same import here.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/ProviderKeyList.test.tsx`
Expected: FAIL — module `../../src/features/providers/ProviderKeyList` does not exist

- [ ] **Step 3: Write the component**

Create `frontend/src/features/providers/ProviderKeyList.tsx`:

```tsx
import { useEffect, useState } from 'react'
import type { ProviderApiKeyState } from '../../api/providers'

export function ProviderKeyList({ keys, pending, cooldownSeconds, onAdd, onRename, onRemove, onMoveUp, onMoveDown, onCooldownSecondsChange }: {
  keys: ProviderApiKeyState[]
  pending: boolean
  cooldownSeconds: number
  onAdd: (apiKey: string, label?: string) => void
  onRename: (keyId: number, label: string | null) => void
  onRemove: (keyId: number) => void
  onMoveUp: (keyId: number) => void
  onMoveDown: (keyId: number) => void
  onCooldownSecondsChange: (seconds: number) => void
}) {
  const [newKey, setNewKey] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [cooldownInput, setCooldownInput] = useState(String(cooldownSeconds))
  useEffect(() => setCooldownInput(String(cooldownSeconds)), [cooldownSeconds])

  function submit() {
    if (!newKey) return
    onAdd(newKey, newLabel || undefined)
    setNewKey('')
    setNewLabel('')
  }

  function submitCooldown() {
    const seconds = Number.parseInt(cooldownInput, 10)
    if (!Number.isInteger(seconds) || seconds < 1) return
    onCooldownSecondsChange(seconds)
  }

  return <section className="provider-key-list" aria-label="Chaves de API">
    <ul>
      {keys.map((key, index) => <li key={key.id}>
        <span className="provider-key-list__label">{key.label ?? `Chave ${index + 1}`}</span>
        {index === 0 && <span className="provider-key-list__badge">Principal</span>}
        <span className="provider-key-list__status" role="status">
          {key.status === 'cooldown' ? `Em cooldown até ${formatCooldown(key.cooldownUntil)}` : 'Ativa'}
        </span>
        <button type="button" aria-label="Mover para cima" disabled={pending || index === 0} onClick={() => onMoveUp(key.id)}>↑</button>
        <button type="button" aria-label="Mover para baixo" disabled={pending || index === keys.length - 1} onClick={() => onMoveDown(key.id)}>↓</button>
        <button type="button" aria-label="Remover chave" disabled={pending} onClick={() => onRemove(key.id)}>Remover</button>
        <input
          aria-label="Apelido"
          value={key.label ?? ''}
          onChange={(event) => onRename(key.id, event.target.value || null)}
          disabled={pending}
        />
      </li>)}
    </ul>
    <div className="provider-key-list__add">
      <label htmlFor="provider-key-list-new-key">Nova chave</label>
      <input id="provider-key-list-new-key" type="password" autoComplete="off" value={newKey} onChange={(event) => setNewKey(event.target.value)} />
      <label htmlFor="provider-key-list-new-label">Apelido (opcional)</label>
      <input id="provider-key-list-new-label" type="text" value={newLabel} onChange={(event) => setNewLabel(event.target.value)} />
      <button type="button" disabled={pending || !newKey} onClick={submit}>Adicionar chave</button>
    </div>
    <div className="provider-key-list__cooldown">
      <label htmlFor="provider-key-list-cooldown">Tempo de cooldown (s)</label>
      <input id="provider-key-list-cooldown" type="number" min={1} value={cooldownInput} onChange={(event) => setCooldownInput(event.target.value)} />
      <button type="button" disabled={pending || cooldownInput === String(cooldownSeconds)} onClick={submitCooldown}>Salvar tempo de cooldown</button>
    </div>
  </section>
}

function formatCooldown(value: string | null): string {
  if (!value) return ''
  try {
    return new Date(value).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return value
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run tests/unit/ProviderKeyList.test.tsx`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/providers/ProviderKeyList.tsx frontend/tests/unit/ProviderKeyList.test.tsx
git commit -m "feat(frontend): add the ProviderKeyList component"
```

---

### Task 15: Wire `ProviderKeyList` into `ProviderDetail`

**Files:**
- Modify: `frontend/src/features/providers/ProviderDetail.tsx`
- Modify: `frontend/tests/unit/ProviderDetail.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `frontend/tests/unit/ProviderDetail.test.tsx` (inside the existing `describe('ProviderDetail', ...)` block):

```tsx
  it('renders the key fallback list under the provider form', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input) => {
      const url = String(input)
      if (url.endsWith('/keys')) return Promise.resolve(json([{ id: 1, label: 'conta free 1', position: 0, status: 'active', cooldown_until: null }]))
      return Promise.resolve(json({ provider: 'openai', enabled: true, key_cooldown_seconds: 90 }))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
    render(<MemoryRouter><ProviderDetail provider="openai" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf' }} onClose={() => {}} /></MemoryRouter>)

    expect(await screen.findByText('conta free 1')).toBeInTheDocument()
    expect(screen.getByText('Principal')).toBeInTheDocument()
    expect(await screen.findByLabelText('Tempo de cooldown (s)')).toHaveValue(90)
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/ProviderDetail.test.tsx -t "key fallback list"`
Expected: FAIL — no key list rendered yet

- [ ] **Step 3: Wire the hook and component into `ProviderDetail.tsx`**

Add the imports (near the other hook/component imports, after line 10):

```tsx
import { useEffect, useState } from 'react'
import { setProviderKeyCooldownSeconds } from '../../api/providers'
import { ProviderKeyList } from './ProviderKeyList'
import { useProviderKeysState } from './useProviderKeysState'
```

`useState`/`type CSSProperties` are already imported from `'react'` on line 1 — merge `useEffect` into that existing import instead of adding a second `'react'` import line.

In the component body, add the hook call and the cooldown-seconds local state after `const state = useProviderState(client, provider, session)` (after line 14):

```tsx
  const keysState = useProviderKeysState(client, provider, session)
  const [cooldownSeconds, setCooldownSeconds] = useState(60)
  useEffect(() => {
    const loaded = state.load.status === 'loaded' ? state.load.state.extra.key_cooldown_seconds : undefined
    if (typeof loaded === 'number') setCooldownSeconds(loaded)
  }, [state.load])

  async function saveCooldownSeconds(seconds: number) {
    if (session.status === 'missing_csrf') return
    setCooldownSeconds(seconds)
    await setProviderKeyCooldownSeconds(client, provider, seconds)
  }
```

Add the rendered section right after the three mutually-exclusive provider-form branches (after line 25, before the `state.canRevoke && <section className="provider-panel__catalog" ...` line):

```tsx
    <ProviderKeyList
      keys={keysState.keys}
      pending={keysState.action.pending}
      cooldownSeconds={cooldownSeconds}
      onAdd={(apiKey, label) => void keysState.add(apiKey, label)}
      onRename={(keyId, label) => void keysState.rename(keyId, label)}
      onRemove={(keyId) => void keysState.remove(keyId)}
      onMoveUp={(keyId) => void keysState.moveUp(keyId)}
      onMoveDown={(keyId) => void keysState.moveDown(keyId)}
      onCooldownSecondsChange={(seconds) => void saveCooldownSeconds(seconds)}
    />
```

- [ ] **Step 4: Run the new test**

Run: `cd frontend && npx vitest run tests/unit/ProviderDetail.test.tsx -t "key fallback list"`
Expected: PASS

- [ ] **Step 5: Run the full `ProviderDetail` test file to confirm no regression**

Run: `cd frontend && npx vitest run tests/unit/ProviderDetail.test.tsx`
Expected: all PASS (the two pre-existing tests each now also receive a `/keys` fetch call from the mounted `ProviderKeyList`; if either pre-existing `fetchImpl` mock doesn't have a fallback branch that returns valid JSON for that call, add one — following the same `if (url.endsWith(...))` pattern already used in the second pre-existing test)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/providers/ProviderDetail.tsx frontend/tests/unit/ProviderDetail.test.tsx
git commit -m "feat(frontend): render the API key fallback list in provider settings"
```

---

### Task 16: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `python -m pytest tests/unit -q`
Expected: all PASS. If any test outside the files this plan touched fails because it queried `provider_configurations.api_key`/`api_key_ciphertext` directly (a caller this plan's research didn't surface), fix that test the same way Task 5 fixed `test_provider_configuration.py`: point it at `provider_api_keys` instead.

- [ ] **Step 2: Run the backend integration suite if a Postgres DSN is available**

Run: `python -m pytest tests/integration -q`
Expected: PASS, or SKIPPED if `AGENTOS_TEST_POSTGRES_DSN` is not set in this environment (that is the existing, intentional gate on `tests/integration/api/test_provider_configuration_postgres_optional.py` — not a failure).

- [ ] **Step 3: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: all PASS

- [ ] **Step 4: Type-check the frontend**

Run: `cd frontend && npx tsc --noEmit -p .`
Expected: no errors

- [ ] **Step 5: Manual smoke check in the running app**

Start the app (follow this repo's normal dev-run instructions), open Settings → Providers → Ollama, add a second API key with a label, confirm it appears below the existing "Chave de API" field with a "Principal" badge on the first entry, reorder the two with the up/down buttons, remove one, and confirm the list updates. This step has no pass/fail command — note in the PR description whether it was performed, since automated tests cover behavior but not that the drawer renders sensibly end to end.

- [ ] **Step 6: Final commit if Steps 1-4 required any fixes**

```bash
git add -A
git commit -m "test: fix full-suite regressions surfaced by the multi-key fallback feature"
```

(Skip this step if Steps 1-4 needed no changes.)
