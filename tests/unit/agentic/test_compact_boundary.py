from __future__ import annotations

from sqlalchemy import create_engine

from agentos.agentic.compaction import COMPACTION_HEADER, COMPACTION_HEADING, COMPACTION_INSTRUCTION, COMPACTION_SECTIONS
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata

NINE = "\n".join(f"### {section}\n- detail" for section in COMPACTION_SECTIONS)


class Summarizer:
    def __init__(self, text: str = NINE) -> None:
        self.text = text
        self.requests: list[dict] = []

    def stream(self, request):
        self.requests.append(request)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=self.text)])


class Store:
    def __init__(self) -> None:
        self.boundaries: list[dict] = []

    def record_compact_boundary(self, turn, **values):
        self.boundaries.append(dict(values))
        return values


def _bulky() -> list[dict[str, object]]:
    # Each message carries the stored sequence the runtime reads when it persists the boundary.
    messages: list[dict[str, object]] = [
        {"role": "user", "content": f"step {index} " + "x" * 600, "_sequence": index + 1} for index in range(10)
    ]
    messages.append({"role": "user", "content": "current request", "_sequence": 11})
    return messages


def test_the_sections_are_standard_and_the_header_never_invites_rework() -> None:
    assert len(COMPACTION_SECTIONS) == 9
    for section in COMPACTION_SECTIONS:
        assert section in COMPACTION_INSTRUCTION
    assert "language" in COMPACTION_INSTRUCTION.lower()
    for phrase in ("re-read", "re-run", "confirm details"):
        assert phrase not in COMPACTION_HEADER.lower()


def test_chat_and_code_share_the_sections_and_the_boundary_is_persisted() -> None:
    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=Summarizer(), limits=AgenticLimits(max_context_tokens=1_000))
    messages = _bulky()
    runtime._pinned_index = len(messages) - 1
    assert runtime._run_compaction(messages, {"turn_id": "t"}) is True
    replacement = next(item for item in messages if str(item.get("content", "")).startswith(COMPACTION_HEADING))
    for section in COMPACTION_SECTIONS:
        assert f"### {section}" in str(replacement["content"])
    boundary = store.boundaries[-1]
    assert boundary["kind"] == "compact" and boundary["trigger"] == "auto"
    assert boundary["pre_tokens"] > boundary["post_tokens"] > 0
    assert COMPACTION_HEADING not in boundary["summary"] and "### Primary Request and Intent" in boundary["summary"]
    assert runtime._last_compaction == {"pre_tokens": boundary["pre_tokens"], "post_tokens": boundary["post_tokens"]}


def test_a_prose_summary_falls_back_to_the_nine_sections() -> None:
    runtime = AgenticTurnRuntime(store=Store(), provider=Summarizer("just prose"), limits=AgenticLimits(max_context_tokens=1_000))
    messages = _bulky()
    runtime._pinned_index = len(messages) - 1
    runtime._run_compaction(messages, {"turn_id": "t"})
    replacement = next(item for item in messages if str(item.get("content", "")).startswith(COMPACTION_HEADING))
    for section in COMPACTION_SECTIONS:
        assert f"### {section}" in str(replacement["content"])


def _db_store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _turn(store: PostgresChatStore, message: str, key: str, conversation_id: str | None = None) -> dict:
    receipt = store.create(user_id="u", message=message, provider="openrouter", model_id="m", idempotency_key=key, conversation_id=conversation_id)
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    return turn


def test_history_after_a_boundary_starts_from_its_summary() -> None:
    store = _db_store()
    first = _turn(store, "one", "k1")
    store.delta(first, "answer one")
    store.finish(first)
    conversation_id = str(first["conversation_id"])
    second = _turn(store, "two", "k2", conversation_id)
    recorded = store.record_compact_boundary(second, kind="compact", trigger="auto", pre_tokens=1000, post_tokens=200, summary="SUMMARY")
    assert recorded is not None and recorded["cumulative_dropped_tokens"] == 800
    history = store.history_for_turn(second)
    assert history[0]["role"] == "system" and history[0]["content"] == f"{COMPACTION_HEADER}\nSUMMARY"
    assert [item["content"] for item in history[1:]] == ["two"]
    store.finish(second)
    third = _turn(store, "three", "k3", conversation_id)
    assert [item["content"] for item in store.history_for_turn(third)[1:]] == ["two", "", "three"]
    again = store.record_compact_boundary(third, kind="compact", trigger="manual", pre_tokens=500, post_tokens=100, summary="S2")
    assert again is not None and again["cumulative_dropped_tokens"] == 1200


def test_a_microcompact_boundary_does_not_replace_history() -> None:
    store = _db_store()
    first = _turn(store, "one", "k1")
    store.record_compact_boundary(first, kind="microcompact", trigger="auto", pre_tokens=900, post_tokens=400)
    assert [item["content"] for item in store.history_for_turn(first)] == ["one"]


def _fact_numbers(messages: list[dict[str, object]]) -> set[int]:
    import re

    text = "\n".join(str(item.get("content", "")) for item in messages)
    return {int(match) for match in re.findall(r"FACT-(\d+)", text)}


class FactSummarizer:
    """Lists the FACT markers it was given, so the test can tell what the summary covers."""

    def stream(self, request):
        import re

        source = "\n".join(str(m.get("content")) for m in request["messages"])
        facts = ", ".join(sorted({f"FACT-{n}" for n in re.findall(r"FACT-(\d+)", source)}, key=lambda f: int(f.split("-")[1])))
        body = "\n".join(f"### {section}\n- " + (facts if section == "All user messages" else "none") for section in COMPACTION_SECTIONS)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=body)])


def _nine_turns_then_compact(*, force: bool) -> PostgresChatStore:
    """Eight finished turns carrying FACT-1..8, then a ninth turn whose history is compacted in-turn."""
    store = _db_store()
    conversation_id = None
    for index in range(1, 9):
        turn = _turn(store, f"user says FACT-{index}", f"k{index}", conversation_id)
        conversation_id = str(turn["conversation_id"])
        store.delta(turn, f"assistant ack FACT-{index}")
        store.finish(turn)
    ninth = _turn(store, "current request FACT-9", "k9", conversation_id)
    messages = list(store.history_for_turn(ninth))
    runtime = AgenticTurnRuntime(store=store, provider=FactSummarizer(), limits=AgenticLimits(max_context_tokens=1_000))
    runtime._pinned_index = len(messages) - 1
    if force:
        runtime._force_compact(messages, ninth, [])
    else:
        assert runtime._run_compaction(messages, ninth) is True  # the default keep_units
    store.delta(ninth, "done")
    store.finish(ninth)
    store.test_conversation_id = conversation_id
    return store


def _facts_in_next_turn(store: PostgresChatStore) -> set[int]:
    conversation_id = store.test_conversation_id
    tenth = _turn(store, "next request FACT-10", "k10", conversation_id)
    return _fact_numbers(store.history_for_turn(tenth))


def test_auto_compaction_never_drops_retained_turns() -> None:
    # The newest six units stay verbatim in memory; the boundary must not claim them as summarised.
    store = _nine_turns_then_compact(force=False)
    assert _facts_in_next_turn(store) == set(range(1, 11))


def test_reactive_compaction_never_drops_retained_turns() -> None:
    # The reactive path keeps only two units verbatim.
    store = _nine_turns_then_compact(force=True)
    assert _facts_in_next_turn(store) == set(range(1, 11))


def test_a_compaction_that_folds_no_stored_message_persists_no_boundary() -> None:
    # Nothing folded has a stored sequence, so no history row is summarized: writing a boundary would be a lie.
    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=Summarizer(), limits=AgenticLimits(max_context_tokens=1_000))
    messages: list[dict[str, object]] = [{"role": "user", "content": f"step {index} " + "x" * 600} for index in range(10)]
    messages.append({"role": "user", "content": "current request"})
    runtime._pinned_index = len(messages) - 1
    assert runtime._run_compaction(messages, {"turn_id": "t"}) is True
    assert store.boundaries == []


class _Loads:
    """The chat store, plus the turn lookup the runtime expects from a worker's store."""

    def __init__(self, store: PostgresChatStore, turn: dict) -> None:
        self._store, self._turn = store, turn

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def load(self, turn_id: str) -> dict:
        return self._turn


def test_a_manual_compaction_still_covers_the_whole_history() -> None:
    store = _db_store()
    conversation_id = None
    for index in range(1, 4):
        turn = _turn(store, f"user FACT-{index}", f"k{index}", conversation_id)
        conversation_id = str(turn["conversation_id"])
        store.delta(turn, f"assistant ack FACT-{index}")
        store.finish(turn)
    command = _turn(store, "/compact", "kc", conversation_id)
    runtime = AgenticTurnRuntime(store=_Loads(store, command), provider=FactSummarizer(), limits=AgenticLimits(max_context_tokens=1_000), compact_request="")
    assert runtime.run(str(command["turn_id"])).state == "completed"
    nxt = _turn(store, "next request FACT-9", "kn", conversation_id)
    history = store.history_for_turn(nxt)
    assert history[0]["role"] == "system" and _fact_numbers(history[:1]) == {1, 2, 3}
    assert not [item for item in history[1:] if "FACT-1" in str(item["content"]) or "FACT-2" in str(item["content"]) or "FACT-3" in str(item["content"])]
