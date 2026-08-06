# Event System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar o domínio completo da RFC 103 com envelope canônico, publicação pós-commit, bus em memória at-least-once, subscriptions autorizadas, deduplicação, ordenação, quarentena, archive, query e replay, preservando os contratos atuais de Execution.

**Architecture:** `agentos.events` será o pacote canônico. `models.py` concentra value objects e estados; `ports.py` define Protocols; `security.py` centraliza validação bounded/sanitização; `in_memory.py` implementa bus, publisher e archive determinísticos; `compat.py` adapta os tipos legados de Execution sem fazer Runtime depender do bus. Todas as operações são síncronas, imutáveis e substituíveis por adapters futuros.

**Tech Stack:** Python 3.13+, dataclasses congeladas/slotted, `typing.Protocol`, `enum.StrEnum`, pytest, sem dependências de produção.

## Global Constraints

- Publicar somente entradas de outbox confirmadas por `TransactionalPersistence`; `UNKNOWN` exige `inspect_commit` autorizado.
- Não prometer exactly-once; retries mantêm `event_id` e geram novo `delivery_id`.
- Não incluir segredo, token, credencial, cookie, header, prompt, resposta completa, argumento privado ou exceção tecnológica em envelope, payload, repr ou erro público.
- Ownership e classificação são filtros obrigatórios antes do consumer; conhecer IDs não autoriza acesso.
- `sequence` é positiva e existe exatamente quando `execution_id` existe; lacunas são observadas, nunca inventadas.
- Replay preserva envelope e identidade histórica; somente metadado operacional externo identifica replay.
- Não adicionar broker, PostgreSQL, Redis, SQLAlchemy, FastAPI, HTTP, SDK de Provider, filesystem, storage ou worker.
- Runtime não importa bus/archive/publisher concretos e a suíte existente deve permanecer verde.
- Cada comportamento novo segue RED (teste falha observado), GREEN (mínimo), REFACTOR, com verificação fresca.

---

### Task 1: Value objects, envelope canônico e sanitização

**Files:**
- Create: `src/agentos/events/models.py`
- Create: `src/agentos/events/security.py`
- Create: `src/agentos/events/__init__.py`
- Create: `tests/unit/events/conftest.py`
- Create: `tests/unit/events/test_event_contracts.py`

**Interfaces:**
- Produces immutable `EventEnvelope`, `EventId`, `EventType`, `EventVersion`, `EventSequence`, `EventOwnership`, `EventContext`, `DataClassification`, `PayloadReference`, `BoundedPayload`, `ReplayPolicy`, and public validation errors.
- Consumes only standard library types and legacy-independent string references.

- [ ] **Step 1: Write failing contract tests**

```python
def test_execution_event_requires_sequence_and_workspace_ownership(event_factory):
    with pytest.raises(ValueError):
        event_factory(execution_id="execution-1", sequence=None)


def test_event_without_execution_has_no_sequence(event_factory):
    event = event_factory(execution_id=None, sequence=None)
    assert event.execution_id is None
    assert event.sequence is None


def test_payload_rejects_secret_keys_and_exposes_only_bounded_data(event_factory):
    with pytest.raises(ValueError):
        event_factory(payload={"api_key": "private"})
    event = event_factory(payload={"result_ref": "artifact:1"})
    assert "private" not in repr(event)


def test_event_requires_offset_aware_time_and_positive_version(event_factory, naive_datetime):
    with pytest.raises(ValueError):
        event_factory(occurred_at=naive_datetime)
    with pytest.raises(ValueError):
        event_factory(event_version=0)
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest tests/unit/events/test_event_contracts.py -q`

Expected: FAIL because `agentos.events` does not exist.

- [ ] **Step 3: Implement minimal immutable contracts**

Create frozen/slotted dataclasses and string-backed enums. Validate non-blank IDs,
offset-aware datetimes, positive versions/sequences, ownership consistency and
bounded recursive payloads. Reject sensitive key names and secret-like values;
allow only scalar values, opaque `PayloadReference`s and bounded tuples/maps.
Implement a sanitized `__repr__`/error path that reports type and references,
not payload contents. Keep `DataClassification` values compatible with
`agentos.execution.events.DataClassification` by using the same string values.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python -m pytest tests/unit/events/test_event_contracts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/agentos/events tests/unit/events
git commit -m "feat: add canonical event contracts"
```

### Task 2: Public ports, publication state and delivery contracts

**Files:**
- Create: `src/agentos/events/ports.py`
- Modify: `src/agentos/events/models.py`
- Modify: `src/agentos/events/__init__.py`
- Create: `tests/unit/events/test_event_ports.py`

**Interfaces:**
- Produces `OutboxPublisher`, `EventBus`, `EventConsumer`, `EventArchive` Protocols and immutable request/result types.
- Consumes Task 1 envelope, ownership and bounded values.

- [ ] **Step 1: Write failing port tests**

```python
def test_delivery_disposition_is_explicit_and_delivery_ids_are_distinct(event_factory):
    delivery_a = EventDelivery(event=event_factory(), delivery_id="delivery:1", attempt=1)
    delivery_b = EventDelivery(event=event_factory(), delivery_id="delivery:2", attempt=2)
    assert delivery_a.delivery_id != delivery_b.delivery_id
    assert DeliveryDisposition.ACKNOWLEDGED.value == "ACKNOWLEDGED"


def test_publish_request_requires_bounded_limit_and_lease():
    with pytest.raises(ValueError):
        PublishOutboxBatch("publisher:1", "partition:1", None, 0, PublicationLease("lease:1", "owner:1", 1))


def test_subscription_declares_types_versions_clearance_and_replay_policy():
    spec = SubscriptionSpec(
        consumer_name="audit",
        accepted_event_types=("Execution*",),
        accepted_versions=(VersionRange(1, 2),),
        ownership_scope=OwnershipScope("user-1", "workspace-1"),
        ordering_requirement=OrderingRequirement.PER_EXECUTION,
        data_clearance=DataClassification.INTERNAL,
        replay_policy=ReplayPolicy.ALLOWED,
    )
    assert spec.accepted_versions[0].maximum == 2
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest tests/unit/events/test_event_ports.py -q`

Expected: FAIL because the port and operational contracts do not exist.

- [ ] **Step 3: Implement public Protocols and operational values**

Define `PublishOutboxBatch`, `PublicationLease`, `OutboxPosition`,
`OutboxPublishResult`, `PublishReceipt`, `SubscriptionSpec`, `SubscriptionRef`,
`EventDelivery`, `DeliveryDisposition`, `OrderingRequirement`, `OwnershipScope`,
`VersionRange`, `EventPage`, `AuthorizedEventQuery`, `ReplayRequest`,
`ReplayJobRef`, `ReplayStatus`, `QuarantineRecord`, cancellation requests/results
and bounded failure/audit types. Ensure all numeric limits are positive/bounded,
all contexts are explicit and every Protocol has the signatures from RFC 103.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python -m pytest tests/unit/events/test_event_ports.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/agentos/events tests/unit/events
git commit -m "feat: define event system ports"
```

### Task 3: In-memory EventBus delivery semantics

**Files:**
- Create: `src/agentos/events/in_memory.py`
- Create: `tests/unit/events/test_event_bus.py`

**Interfaces:**
- Consumes Tasks 1–2 contracts.
- Produces `InMemoryEventBus.publish`, `.subscribe`, `.deliver_pending`, `.retry`, `.cancel_subscription`, `.quarantine` and inspection views.

- [ ] **Step 1: Write failing delivery tests**

```python
def test_bus_retries_with_same_event_id_and_new_delivery_id(bus_fixture):
    consumer = bus_fixture.retry_once_consumer()
    bus_fixture.bus.subscribe(bus_fixture.subscription, consumer)
    receipt = bus_fixture.bus.publish((bus_fixture.event,))
    first = bus_fixture.bus.deliver_pending()
    second = bus_fixture.bus.deliver_pending()
    assert first.attempts[0].event_id == second.attempts[0].event_id
    assert first.attempts[0].delivery_id != second.attempts[0].delivery_id


def test_ack_deduplicates_by_consumer_and_failure_isolated(bus_fixture):
    bus_fixture.bus.subscribe(bus_fixture.subscription, bus_fixture.ack_consumer)
    bus_fixture.bus.subscribe(bus_fixture.other_subscription, bus_fixture.permanent_failure_consumer)
    bus_fixture.bus.publish((bus_fixture.event,))
    result = bus_fixture.bus.deliver_pending()
    assert result.acknowledged == ("subscription:1",)
    assert len(bus_fixture.ack_consumer.deliveries) == 1
    assert result.quarantined


def test_per_execution_ordering_retains_gap_and_ignores_late_event(bus_fixture):
    bus_fixture.bus.subscribe(bus_fixture.ordered_subscription, bus_fixture.ack_consumer)
    bus_fixture.bus.publish((bus_fixture.event_sequence_2, bus_fixture.event_sequence_1))
    first = bus_fixture.bus.deliver_pending()
    assert first.reconciliation[0].missing_sequence == 1
    bus_fixture.bus.publish((bus_fixture.event_sequence_1,))
    second = bus_fixture.bus.deliver_pending()
    assert [d.event.sequence for d in bus_fixture.ack_consumer.deliveries] == [1, 2]
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest tests/unit/events/test_event_bus.py -q`

Expected: FAIL because `InMemoryEventBus` does not exist.

- [ ] **Step 3: Implement minimal bus state machine**

Store immutable envelopes by `event_id`, subscriptions by opaque ref, pending
deliveries by `(subscription_ref, event_id)`, ACK set, per-execution cursors,
attempt counters and quarantine records. Filter ownership/type/version/
clearance before consumer invocation. Generate `delivery:{counter}` and preserve
the original envelope on retries. Apply ACK only after consumer returns
`ACKNOWLEDGED`; retain retryable failures; quarantine permanent failures.
Implement ordering with a per-subscription/per-execution next-sequence cursor:
duplicate or late events are ignored, a higher sequence with a gap is retained
and exposed as reconciliation, and no synthetic event is created. Delivery of
one subscription must not prevent progress of another.

- [ ] **Step 4: Run focused and regression tests**

Run: `python -m pytest tests/unit/events/test_event_bus.py tests/unit/execution tests/unit/runtime -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/agentos/events tests/unit/events
git commit -m "feat: add at-least-once in-memory event bus"
```

### Task 4: Post-commit OutboxPublisher

**Files:**
- Modify: `src/agentos/execution/ports.py`
- Modify: `src/agentos/execution/in_memory.py`
- Modify: `src/agentos/events/in_memory.py`
- Create: `tests/unit/events/test_outbox_publisher.py`

**Interfaces:**
- Consumes `TransactionalPersistence`, legacy `OutboxEntry` and canonical bus.
- Produces `InMemoryOutboxPublisher` and a narrow confirmed-outbox inspection adapter without changing transaction semantics.

- [ ] **Step 1: Write failing outbox tests**

```python
def test_publisher_only_publishes_committed_entries(outbox_fixture):
    outbox_fixture.persistence.seed_committed(outbox_fixture.entry)
    result = outbox_fixture.publisher.publish_pending(outbox_fixture.request)
    assert result.published_event_ids == (outbox_fixture.entry.event.event_id,)


def test_unknown_commit_requires_authorized_inspection(outbox_fixture):
    outbox_fixture.persistence.seed_indeterminate(outbox_fixture.entry)
    result = outbox_fixture.publisher.publish_pending(outbox_fixture.request)
    assert result.pending_count == 1
    assert outbox_fixture.bus.events == ()


def test_retry_reuses_event_id_and_does_not_confirm_domain(outbox_fixture):
    outbox_fixture.persistence.seed_committed(outbox_fixture.entry)
    first = outbox_fixture.publisher.publish_pending(outbox_fixture.request)
    second = outbox_fixture.publisher.publish_pending(outbox_fixture.request)
    assert first.published_event_ids == second.published_event_ids
    assert outbox_fixture.persistence.domain_confirmation_count == 1
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest tests/unit/events/test_outbox_publisher.py -q`

Expected: FAIL because the confirmed-outbox adapter/publisher does not exist.

- [ ] **Step 3: Implement confirmed outbox view and publisher**

Add a read-only protocol for bounded confirmed outbox batches, implemented by
`InMemoryTransactionalPersistence` from its existing committed records. Expose
only `OutboxEntry`, commit state, transaction/idempotency references and opaque
position metadata. `InMemoryOutboxPublisher` must call `inspect_commit` for
indeterminate requests only when the request supplies the matching authorized
context; it must never publish `NOT_COMMITTED`. Forward canonical-converted
events to `EventBus.publish`, maintain cursor/lease state, report duplicates and
pending entries, and never mutate Execution/audit/outbox commit state.

- [ ] **Step 4: Run focused and full existing tests**

Run: `python -m pytest tests/unit/events/test_outbox_publisher.py tests/unit/execution -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/agentos/events src/agentos/execution tests/unit/events
git commit -m "feat: publish confirmed execution outbox entries"
```

### Task 5: Archive, authorized query and replay

**Files:**
- Modify: `src/agentos/events/in_memory.py`
- Create: `tests/unit/events/test_event_archive.py`

**Interfaces:**
- Consumes canonical envelopes, subscriptions and bus from Tasks 1–3.
- Produces `InMemoryEventArchive.query`, `.replay`, `.cancel_replay`, retention-expiration results and bounded audit inspection.

- [ ] **Step 1: Write failing archive/replay tests**

```python
def test_query_is_paginated_and_does_not_cross_ownership(archive_fixture):
    first = archive_fixture.archive.query(archive_fixture.user_query(limit=1))
    assert len(first.events) == 1
    assert first.next_cursor is not None
    other = archive_fixture.archive.query(archive_fixture.other_user_query())
    assert other.events == ()


def test_replay_preserves_identity_and_marks_operation_outside_envelope(archive_fixture):
    job = archive_fixture.archive.replay(archive_fixture.replay_request)
    archive_fixture.archive.run_replay(job)
    delivery = archive_fixture.consumer.deliveries[-1]
    assert delivery.event == archive_fixture.event
    assert delivery.event.event_id == archive_fixture.event.event_id
    assert delivery.replay is True


def test_cancelled_replay_stops_new_deliveries_without_deleting_history(archive_fixture):
    job = archive_fixture.archive.replay(archive_fixture.replay_request)
    archive_fixture.archive.cancel_replay(job)
    result = archive_fixture.archive.run_replay(job)
    assert result.status is ReplayStatus.CANCELLED
    assert archive_fixture.archive.query(archive_fixture.user_query()).events
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest tests/unit/events/test_event_archive.py -q`

Expected: FAIL because archive/query/replay are not implemented.

- [ ] **Step 3: Implement archive and replay state**

Archive envelopes by immutable `event_id`, reject conflicting envelopes for the
same identity, apply type/version/ownership/classification/time filters and
opaque bounded cursors. Distinguish expired references from empty results with
an explicit retention status. Require authorized scope, purpose, actor and
target subscription for replay. Resolve event IDs/cursors only inside that
scope, preserve the exact envelope and submit deliveries through the bus with
`replay=True` operational metadata. Store audit/quarantine references without
payloads. Cancellation blocks future replay submissions but leaves history and
already ACKed effects intact.

- [ ] **Step 4: Run focused and regression tests**

Run: `python -m pytest tests/unit/events/test_event_archive.py tests/unit/events -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/agentos/events tests/unit/events
git commit -m "feat: add authorized event archive and replay"
```

### Task 6: Execution compatibility and public exports

**Files:**
- Create: `src/agentos/events/compat.py`
- Modify: `src/agentos/events/__init__.py`
- Modify: `src/agentos/execution/events.py`
- Modify: `src/agentos/execution/in_memory.py`
- Create: `tests/unit/events/test_execution_compat.py`
- Modify: `tests/unit/execution/test_in_memory_persistence.py`

**Interfaces:**
- Consumes legacy `ExecutionEventType`, `EventEnvelope`, `OutboxEntry` and canonical event contracts.
- Produces `to_canonical_event`, `from_execution_event`, confirmed-outbox inspection and stable package exports.

- [ ] **Step 1: Write failing compatibility tests**

```python
def test_legacy_execution_envelope_round_trips_without_losing_identity(execution_event_fixture):
    canonical = to_canonical_event(execution_event_fixture.legacy)
    restored = from_execution_event(canonical)
    assert restored.event_id == execution_event_fixture.legacy.event_id
    assert restored.sequence == execution_event_fixture.legacy.sequence
    assert restored.payload == execution_event_fixture.legacy.payload


def test_execution_control_commit_exposes_confirmed_outbox_only(execution_event_fixture):
    receipt = execution_event_fixture.persistence.confirmed_outbox()
    assert receipt[0].event.event_id == execution_event_fixture.legacy.event_id
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest tests/unit/events/test_execution_compat.py -q`

Expected: FAIL because the adapter and confirmed-outbox view do not exist.

- [ ] **Step 3: Implement compatibility without changing Runtime ownership**

Convert the legacy `ownership` object to canonical `user_id`/`workspace_id` and
back, map `ExecutionEventType` values to string event types, preserve sequence,
causation, classification and payload, and reject an inconsistent conversion.
Add a read-only confirmed-outbox method/Protocol surface to the in-memory
adapter without exposing mutable internals. Export canonical contracts from
`agentos.events`; do not make Runtime import concrete event implementations.

- [ ] **Step 4: Run all current and event tests**

Run: `python -m pytest tests/unit/events tests/unit/execution tests/unit/runtime tests/unit/context tests/unit/providers -q`

Expected: PASS with no regressions.

- [ ] **Step 5: Commit**

```text
git add src/agentos/events src/agentos/execution tests/unit/events tests/unit/execution
git commit -m "feat: preserve execution event compatibility"
```

### Task 7: Requirement audit, full verification and final integration commit

**Files:**
- Modify only files identified by failing verification or audit checks from Tasks 1–6.

- [ ] **Step 1: Run the complete suite**

Run: `python -m pytest -q`

Expected: exit code 0 and all tests passing.

- [ ] **Step 2: Compile all source and tests**

Run: `python -m compileall -q src tests`

Expected: exit code 0.

- [ ] **Step 3: Scan the Event System boundary**

Run: `rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Redis|redis|filesystem|ArtifactStorage|requests|httpx|kafka|rabbit" src/agentos/events`

Expected: no output and exit code 1 (no matches).

- [ ] **Step 4: Audit every required invariant**

Verify source/tests for: complete envelope; sanitized bounded payload; Execution
compatibility; confirmed-only publishing; inspect of UNKNOWN; cursor/lease;
same event ID on retry; per-consumer dedup; distinct delivery IDs; ACK after
consumer effect; consumer isolation; retry/quarantine/cancel; per-Execution
ordering, late event and explicit gap; ownership/classification/version
filtering; archive pagination and retention status; authorized replay preserving
identity/sequence/causality; Runtime independence; and absence of concrete
infrastructure or proprietary payloads.

- [ ] **Step 5: Commit the verified implementation**

```text
git add src tests docs/superpowers/plans/2026-08-06-event-system.md
git commit -m "feat: complete RFC 103 event system"
```

Expected: `git status --short` is empty after the commit.

