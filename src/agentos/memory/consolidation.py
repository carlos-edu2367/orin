"""Opt-in background consolidation (``autoDreamEnabled``): durable facts distilled from recent conversation.

Runs after a completed turn, at most once a day per user and only after a few completed
turns, with the turn's own model. Facts land in the typed memory store with source
``consolidation``; anything that looks like a secret is dropped before saving.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentos.file_lock import file_lock
from agentos.memory.friend_persona import FriendReflectionConsolidator
from agentos.settings.paths import orin_user_dir

CONSOLIDATION_INTERVAL = timedelta(hours=24)
MIN_TURNS_BETWEEN_RUNS = 5
MAX_TRANSCRIPT_CHARS = 24_000
MAX_FACTS = 8
MAX_FACT_CHARS = 300
CONSOLIDATION_INSTRUCTION = (
    "Read the conversation excerpt below and extract at most eight facts that will still be true and useful in "
    "future conversations with this user: stated preferences, corrections, project conventions, decisions with "
    "a lasting effect. Skip anything that only matters for a single task, anything obvious from the code, and "
    "anything secret (passwords, keys, tokens). Reply with a JSON array of short, self-contained sentences and "
    "nothing else; reply [] when nothing is worth keeping."
)
_SECRET = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|bearer|sk-[A-Za-z0-9]{10,}|-----BEGIN)")
_LOCK = threading.Lock()
LOCK_WAIT_SECONDS = 5.0


def _file_lock(path: Path) -> Iterator[bool]:
    """W3-R23: one writer at a time across processes (Orin and its `orin code` runs share the file). Yields False
    when the lock could not be taken in time; the caller then changes nothing. The lock lives in ``agentos.file_lock``."""
    return file_lock(path, wait_seconds=LOCK_WAIT_SECONDS)


class ConsolidationState:
    """Per-user counters in a small JSON file; a corrupt file simply starts over."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else orin_user_dir() / "state" / "auto-dream.json"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: Mapping[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")  # W3-R23
            temporary.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            try:
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass

    def note_completed_turn(self, user_id: str) -> None:
        with _LOCK, _file_lock(self.path) as locked:
            if not locked:
                return  # a lost count only delays consolidation
            data = self._read()
            record = data.get(user_id) if isinstance(data.get(user_id), dict) else {}
            turns = record.get("turns") if isinstance(record.get("turns"), int) else 0
            data[user_id] = {**record, "turns": turns + 1}
            self._write(data)

    def due(self, user_id: str, now: datetime) -> bool:
        with _LOCK, _file_lock(self.path) as locked:
            if not locked:
                return False
            return self._due(self._read().get(user_id), now)

    @staticmethod
    def _due(record: Any, now: datetime) -> bool:
        if not isinstance(record, dict):
            return False
        turns = record.get("turns") if isinstance(record.get("turns"), int) else 0
        if turns < MIN_TURNS_BETWEEN_RUNS:
            return False
        try:
            last = datetime.fromisoformat(str(record["last_run"])) if record.get("last_run") else None
        except ValueError:
            last = None
        if last is not None and last.tzinfo is None:
            # A hand-edited or foreign timestamp without an offset is read as UTC, never a crash.
            last = last.replace(tzinfo=UTC)
        return last is None or now - last >= CONSOLIDATION_INTERVAL

    def mark_run(self, user_id: str, now: datetime) -> None:
        with _LOCK, _file_lock(self.path) as locked:
            if not locked:
                return
            data = self._read()
            data[user_id] = {"turns": 0, "last_run": now.isoformat()}
            self._write(data)

    def claim_run(self, user_id: str, now: datetime) -> bool:
        """W3-R23: whether this caller runs consolidation now; checking and marking are one step, so two processes
        never both run it."""
        with _LOCK, _file_lock(self.path) as locked:
            if not locked:
                return False
            data = self._read()
            if not self._due(data.get(user_id), now):
                return False
            data[user_id] = {"turns": 0, "last_run": now.isoformat()}
            self._write(data)
            return True


def parse_facts(text: str) -> list[str]:
    """A JSON array (or ``- `` lines) of short facts, deduplicated, without secrets, at most eight."""
    raw = str(text or "")
    items: list[Any] = []
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, list):
                items = parsed
        except ValueError:
            items = []
    if not items:
        items = [line.strip()[2:] for line in raw.splitlines() if line.strip().startswith("- ")]
    facts: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        fact = " ".join(item.split())[:MAX_FACT_CHARS]
        if fact and not _SECRET.search(fact) and fact not in facts:
            facts.append(fact)
        if len(facts) >= MAX_FACTS:
            break
    return facts


def transcript_excerpt(history: Sequence[Mapping[str, Any]]) -> str:
    """The newest text of the person's own messages that fits the excerpt budget, in order.

    Assistant text is left out: it can echo tool, web or repository content, and consolidation would
    persist whatever an injected page said as a "fact" (W3-R27). Tainted and tool-sourced messages
    are likewise excluded.
    """
    parts: list[str] = []
    remaining = MAX_TRANSCRIPT_CHARS
    for item in reversed(list(history)):
        role, content = item.get("role"), item.get("content")
        if role != "user" or not isinstance(content, str) or not content.strip():
            continue
        if item.get("tainted") is True or item.get("tool_call_id") is not None:
            continue
        if str(item.get("source") or "").lower() in {"tool", "mcp", "web", "system", "subagent"}:
            continue
        line = f"{role}: {content.strip()}"
        if len(line) > remaining:
            line = line[-remaining:]
        parts.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break
    # Cut from the front: the newest text is what must survive.
    return "\n\n".join(reversed(parts))[-MAX_TRANSCRIPT_CHARS:]


class MemoryConsolidator:
    def __init__(self, provider: object, *, provider_name: str, model_id: str, memory_store: object) -> None:
        self.provider = provider
        self.provider_name = provider_name
        self.model_id = model_id
        self.memory_store = memory_store

    def run(self, history: Sequence[Mapping[str, Any]]) -> int:
        excerpt = transcript_excerpt(history)
        if not excerpt:
            return 0
        events = self.provider.stream({
            "turn_id": "auto-dream", "provider": self.provider_name, "model": self.model_id,
            "messages": [{"role": "system", "content": CONSOLIDATION_INSTRUCTION}, {"role": "user", "content": excerpt}],
            "tools": [], "max_output_tokens": 800,
        })
        text = "".join(str(getattr(event, "text", "") or "") for event in events if str(getattr(event, "kind", "")) == "text")
        saved = 0
        for fact in parse_facts(text):
            try:
                self.memory_store.save(fact, ("auto-dream",), kind="fact", confidence=0.6, source="consolidation")
                saved += 1
            except Exception:  # noqa: BLE001 - one bad fact never stops the rest
                continue
        return saved


__all__ = [
    "CONSOLIDATION_INTERVAL",
    "MIN_TURNS_BETWEEN_RUNS",
    "ConsolidationState",
    "FriendReflectionConsolidator",
    "MemoryConsolidator",
    "parse_facts",
    "transcript_excerpt",
]
