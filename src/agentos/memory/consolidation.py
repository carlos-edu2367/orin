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
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            pass

    def note_completed_turn(self, user_id: str) -> None:
        with _LOCK:
            data = self._read()
            record = data.get(user_id) if isinstance(data.get(user_id), dict) else {}
            turns = record.get("turns") if isinstance(record.get("turns"), int) else 0
            data[user_id] = {**record, "turns": turns + 1}
            self._write(data)

    def due(self, user_id: str, now: datetime) -> bool:
        with _LOCK:
            record = self._read().get(user_id)
        if not isinstance(record, dict):
            return False
        turns = record.get("turns") if isinstance(record.get("turns"), int) else 0
        if turns < MIN_TURNS_BETWEEN_RUNS:
            return False
        try:
            last = datetime.fromisoformat(str(record["last_run"])) if record.get("last_run") else None
        except ValueError:
            last = None
        return last is None or now - last >= CONSOLIDATION_INTERVAL

    def mark_run(self, user_id: str, now: datetime) -> None:
        with _LOCK:
            data = self._read()
            data[user_id] = {"turns": 0, "last_run": now.isoformat()}
            self._write(data)


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
    """The newest user/assistant text that fits the excerpt budget, in order."""
    parts: list[str] = []
    remaining = MAX_TRANSCRIPT_CHARS
    for item in reversed(list(history)):
        role, content = item.get("role"), item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
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
    "CONSOLIDATION_INTERVAL", "MIN_TURNS_BETWEEN_RUNS", "ConsolidationState", "MemoryConsolidator",
    "parse_facts", "transcript_excerpt",
]
