"""Opt-in background consolidation (``autoDreamEnabled``): durable facts, episodic lessons, and SKILL.md proposals.

Runs after a completed turn, at most once a day per user and only after a few completed
turns, with the turn's own model. Facts and episodic memories land in the typed memory store
with source ``consolidation`` and confidence ``0.75`` (emitting Undo cards when created);
candidate ``SKILL.md`` procedures undergo strict dry-run validation and are stored as
``status="pending"`` proposals requiring explicit user approval.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentos.file_lock import file_lock
from agentos.memory.friend_persona import FriendReflectionConsolidator
from agentos.memory.security import validate_memory_content
from agentos.settings.paths import orin_user_dir
from agentos.skills.proposals import SkillProposalStore

CONSOLIDATION_INTERVAL = timedelta(hours=24)
MIN_TURNS_BETWEEN_RUNS = 5
MAX_TRANSCRIPT_CHARS = 24_000
MAX_FACTS = 8
MAX_FACT_CHARS = 300
MAX_STRUCTURED_FACT_CHARS = 4_000
MAX_SKILL_PROPOSALS_PER_RUN = 3

VALID_MEMORY_KINDS = frozenset({"fact", "preference", "architecture", "operational", "episodic"})
VALID_MEMORY_SCOPES = frozenset({"user", "project"})

CONSOLIDATION_INSTRUCTION = (
    "Read the verified conversation digest below and extract durable knowledge that will still be true and useful "
    "in future conversations with this user: stated preferences, corrections, project architecture, operational "
    "commands/gotchas, and non-trivial episodic engineering lessons (symptom, root cause, fix, and how it was "
    "verified). Skip anything that only matters for a single task, anything obvious from reading a single file, "
    "and anything secret (passwords, keys, tokens).\n\n"
    "Reply with a JSON object matching this schema and nothing else:\n"
    "{\n"
    '  "memories": [\n'
    '    {"fact": "...", "kind": "preference|fact|architecture|operational|episodic", "scope": "user|project", "tags": ["..."]}\n'
    "  ],\n"
    '  "supersedes": [\n'
    '    {"old_memory_id": "mem_...", "new_fact_index": 0, "reason": "direct factual contradiction"}\n'
    "  ],\n"
    '  "skill_proposals": [\n'
    "    {\n"
    '      "action": "create|edit",\n'
    '      "target_skill_id": null,\n'
    '      "name": "...",\n'
    '      "description": "...",\n'
    '      "when_to_use": ["..."],\n'
    '      "when_not_to_use": ["..."],\n'
    '      "requires_tools": ["read_file", "run_command"],\n'
    '      "tags": ["..."],\n'
    '      "instructions": "## Workflow\\n1. ...\\n\\n## Validation\\n1. ..."\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Only include `supersedes` when a new memory directly contradicts an existing active memory ID listed in the prompt. "
    "Only include `skill_proposals` when a reusable, verified multi-step procedure was proven during the session and is not "
    "already covered by existing skills."
)

_SECRET = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|bearer|sk-[A-Za-z0-9]{10,}|-----BEGIN)")
_PROMPT_INJECTION_RE = re.compile(
    r"(?ix)(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|policy|prompts|rules)|"
    r"<\s*/?\s*system\s*>|system\s+prompt|developer\s+message)"
)
_UNTRUSTED_EXTERNAL_TOOLS = frozenset({
    "fetch_url",
    "web_fetch",
    "webfetch",
    "web_search",
    "websearch",
    "call_mcp_tool",
    "callmcptool",
    "read_mcp_resource",
    "readmcpresource",
    "read_mcp_resource_dir",
    "readmcpresourcedir",
    "inspect_mcp_server",
    "inspectmcpserver",
    "browse_page",
    "browser_navigate",
    "inspect_plugin",
    "inspectplugin",
    "search_plugin",
    "searchplugin",
    "run_subagent",
    "runsubagent",
    "agent",
    "ask_agent",
    "askagent",
    "ask_agents",
    "askagents",
})
_VERIFIED_LOCAL_TOOLS = frozenset({
    "edit_file",
    "write_file",
    "notebook_edit",
    "run_command",
    "powershell",
    "verify_project",
    "read_file",
    "list_files",
    "search_files",
    "glob_files",
    "search_code",
    "project_map",
    "edit",
    "write",
    "notebookedit",
    "bash",
    "verifyproject",
    "read",
    "listfiles",
    "grep",
    "glob",
    "searchcode",
    "projectmap",
})

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
        if fact and not _SECRET.search(fact) and not _PROMPT_INJECTION_RE.search(fact) and fact not in facts:
            facts.append(fact)
        if len(facts) >= MAX_FACTS:
            break
    return facts


def parse_consolidation_output(text: str) -> dict[str, Any]:
    """Parse structured consolidation JSON object with graceful fallback to legacy JSON array."""
    raw = str(text or "").strip()
    if not raw:
        return {"memories": [], "supersedes": [], "skill_proposals": []}

    obj_start, obj_end = raw.find("{"), raw.rfind("}")
    arr_start = raw.find("[")
    if obj_start != -1 and obj_end > obj_start and (arr_start == -1 or obj_start < arr_start):
        try:
            parsed_obj = json.loads(raw[obj_start : obj_end + 1])
        except ValueError:
            parsed_obj = None
        if isinstance(parsed_obj, Mapping) and any(
            key in parsed_obj for key in ("memories", "supersedes", "skill_proposals")
        ):
            raw_memories = parsed_obj.get("memories")
            raw_supersedes = parsed_obj.get("supersedes")
            raw_proposals = parsed_obj.get("skill_proposals")
            memories_out: list[dict[str, Any]] = []
            if isinstance(raw_memories, list):
                for item in raw_memories[:MAX_FACTS]:
                    if isinstance(item, str):
                        memories_out.append({
                            "fact": item,
                            "kind": "fact",
                            "scope": None,
                            "tags": ["auto-dream"],
                            "_legacy": True,
                        })
                    elif isinstance(item, Mapping):
                        memories_out.append({
                            "fact": item.get("fact"),
                            "kind": item.get("kind"),
                            "scope": item.get("scope"),
                            "tags": item.get("tags"),
                            "_legacy": False,
                        })
            supersedes_out = (
                [dict(item) for item in raw_supersedes if isinstance(item, Mapping)]
                if isinstance(raw_supersedes, list)
                else []
            )
            proposals_out = (
                [dict(item) for item in raw_proposals[:MAX_SKILL_PROPOSALS_PER_RUN] if isinstance(item, Mapping)]
                if isinstance(raw_proposals, list)
                else []
            )
            return {
                "memories": memories_out,
                "supersedes": supersedes_out,
                "skill_proposals": proposals_out,
            }

    legacy_facts = parse_facts(raw)
    return {
        "memories": [
            {"fact": fact, "kind": "fact", "scope": None, "tags": ["auto-dream"], "_legacy": True}
            for fact in legacy_facts
        ],
        "supersedes": [],
        "skill_proposals": [],
    }


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


def _is_untrusted_tool_name(tool_name: str) -> bool:
    lowered = str(tool_name or "").strip().lower()
    if not lowered:
        return False
    return (
        lowered in _UNTRUSTED_EXTERNAL_TOOLS
        or lowered.startswith("mcp__")
        or lowered.startswith("browser")
    )


def _has_untrusted_external_input(
    history: Sequence[Mapping[str, Any]],
    tool_ledger: Sequence[Mapping[str, Any]] = (),
) -> bool:
    for item in history:
        if not isinstance(item, Mapping):
            continue
        if item.get("tainted") is True:
            return True
        source = str(item.get("source") or "").strip().lower()
        if source in {"web", "mcp"}:
            return True
        if _is_untrusted_tool_name(str(item.get("tool_name") or item.get("name") or "")):
            return True
        tool_calls = item.get("tool_calls")
        if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes)):
            for tc in tool_calls:
                if isinstance(tc, Mapping):
                    fn = tc.get("function") if isinstance(tc.get("function"), Mapping) else tc
                    if _is_untrusted_tool_name(str(fn.get("name") or tc.get("name") or "")):
                        return True
    for entry in tool_ledger:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("tainted") is True:
            return True
        if _is_untrusted_tool_name(str(entry.get("tool_name") or entry.get("name") or "")):
            return True
    return False


def _sanitize_digest_text(text: str) -> str:
    clean_lines: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _SECRET.search(stripped) or _PROMPT_INJECTION_RE.search(stripped):
            continue
        clean_lines.append(stripped)
    return "\n".join(clean_lines).strip()


def build_verified_consolidation_digest(
    history: Sequence[Mapping[str, Any]],
    tool_ledger: Sequence[Mapping[str, Any]] = (),
    *,
    had_untrusted_external_input: bool | None = False,
) -> str:
    """Build a clean turn digest for ``auto-dream`` consolidation.

    - Always includes untainted ``role == "user"`` messages (filtered against secrets and prompt injection).
    - When ``had_untrusted_external_input`` is True (or detected from ``history`` / ``tool_ledger`` via web/MCP
      tools or tainted messages), assistant messages and tool outputs are strictly excluded so indirect prompt
      injections from web pages or external MCP servers can never be consolidated.
    - When ``had_untrusted_external_input`` is False, includes a structured summary of verified local tool
      actions (``edit_file``, ``write_file``, ``run_command``, ``verify_project``, etc.) and clean assistant
      conclusions, all sanitized against ``_SECRET`` and ``_PROMPT_INJECTION_RE``.
    """
    untrusted = bool(had_untrusted_external_input) or _has_untrusted_external_input(history, tool_ledger)
    if untrusted:
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
            sanitized = _sanitize_digest_text(content)
            if not sanitized:
                continue
            line = f"user: {sanitized}"
            if len(line) > remaining:
                line = line[-remaining:]
            parts.append(line)
            remaining -= len(line)
            if remaining <= 0:
                break
        return "\n\n".join(reversed(parts))[-MAX_TRANSCRIPT_CHARS:]

    action_lines: list[str] = []
    for entry in list(tool_ledger)[-20:]:
        if not isinstance(entry, Mapping):
            continue
        tool_name = str(entry.get("tool_name") or "").strip()
        if not tool_name or tool_name.lower() not in _VERIFIED_LOCAL_TOOLS:
            continue
        status = str(entry.get("status") or "succeeded").strip()
        raw_args = _sanitize_digest_text(str(entry.get("arguments") or ""))[:160]
        raw_summary = _sanitize_digest_text(str(entry.get("summary") or ""))[:200]
        if _SECRET.search(tool_name):
            continue
        detail = f"{tool_name}({raw_args})" if raw_args else tool_name
        if raw_summary:
            action_lines.append(f"- [{status}] {detail} -> {raw_summary}")
        else:
            action_lines.append(f"- [{status}] {detail}")

    actions_block = ""
    if action_lines:
        actions_block = "## Verified Local Actions\n" + "\n".join(action_lines)

    budget = max(1_000, MAX_TRANSCRIPT_CHARS - len(actions_block) - 4)
    turn_parts: list[str] = []
    remaining = budget
    for item in reversed(list(history)):
        role, content = item.get("role"), item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        if item.get("tainted") is True or item.get("tool_call_id") is not None:
            continue
        if str(item.get("source") or "").lower() in {"tool", "mcp", "web", "system", "subagent"}:
            continue
        sanitized = _sanitize_digest_text(content)
        if not sanitized:
            continue
        line = f"{role}: {sanitized}"
        if len(line) > remaining:
            line = line[-remaining:]
        turn_parts.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break

    chronological = "\n\n".join(reversed(turn_parts))
    if not chronological:
        return ""
    if actions_block:
        combined = f"{chronological}\n\n{actions_block}"
    else:
        combined = chronological
    return combined[-MAX_TRANSCRIPT_CHARS:]


class MemoryConsolidator:
    def __init__(
        self,
        provider: object,
        *,
        provider_name: str,
        model_id: str,
        memory_store: object,
        skill_library: object | None = None,
        proposal_store: SkillProposalStore | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        on_memory_learned: Callable[[Mapping[str, Any], Mapping[str, Any]], None] | None = None,
        on_skill_proposed: Callable[[Mapping[str, Any]], None] | None = None,
        available_tools: Collection[str] | None = None,
    ) -> None:
        self.provider = provider
        self.provider_name = provider_name
        self.model_id = model_id
        self.memory_store = memory_store
        self.skill_library = skill_library
        self.proposal_store = proposal_store
        self.user_id = user_id
        self.project_id = project_id
        self.conversation_id = conversation_id
        self.on_memory_learned = on_memory_learned
        self.on_skill_proposed = on_skill_proposed
        self.available_tools = available_tools

    def _collect_existing_memories(
        self,
        explicit: Sequence[Mapping[str, Any]] | None,
    ) -> tuple[list[str], set[str]]:
        rows: list[Mapping[str, Any]] = []
        if explicit is not None:
            rows = [r for r in explicit if isinstance(r, Mapping)]
        else:
            list_fn = getattr(self.memory_store, "list", None)
            recent_fn = getattr(self.memory_store, "recent", None)
            all_fn = getattr(self.memory_store, "_all", None)
            if callable(list_fn):
                try:
                    raw = list_fn()
                    if isinstance(raw, Sequence):
                        rows = [r for r in raw if isinstance(r, Mapping)]
                except Exception:  # noqa: BLE001
                    rows = []
            elif callable(recent_fn):
                try:
                    raw = recent_fn(limit=30)
                    if isinstance(raw, Sequence):
                        rows = [r for r in raw if isinstance(r, Mapping)]
                except Exception:  # noqa: BLE001
                    rows = []
            elif callable(all_fn):
                try:
                    raw = all_fn()
                    if isinstance(raw, Sequence):
                        rows = [r for r in raw if isinstance(r, Mapping)]
                except Exception:  # noqa: BLE001
                    rows = []
        lines: list[str] = []
        ids: set[str] = set()
        for row in rows[:30]:
            if row.get("superseded_by") is not None:
                continue
            mid = str(row.get("memory_id") or "").strip()
            fact = " ".join(str(row.get("fact") or "").split())[:180]
            if not mid or not fact:
                continue
            kind = str(row.get("kind") or "fact").strip()
            scope = str(row.get("scope") or row.get("scope_type") or "user").strip()
            ids.add(mid)
            lines.append(f"- [{mid}] ({kind}/{scope}): {fact}")
        return lines, ids

    def _collect_existing_skills(
        self,
        explicit: Sequence[Mapping[str, Any]] | None,
    ) -> tuple[list[str], set[str]]:
        items: list[Mapping[str, Any]] = []
        effective_user = self.user_id or str(getattr(self.memory_store, "_user_id", "") or "").strip() or "local-user"
        if explicit is not None:
            items = [s for s in explicit if isinstance(s, Mapping)]
        elif self.skill_library is not None:
            list_fn = getattr(self.skill_library, "list", None)
            if callable(list_fn):
                try:
                    res = list_fn({"user_id": effective_user, "limit": 50})
                    raw_items = res.get("items") if isinstance(res, Mapping) else None
                    if isinstance(raw_items, Sequence):
                        items = [s for s in raw_items if isinstance(s, Mapping)]
                except Exception:  # noqa: BLE001
                    items = []
        lines: list[str] = []
        names_or_ids: set[str] = set()
        for item in items[:50]:
            sid = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            desc = " ".join(str(item.get("description") or "").split())[:140]
            source = str(item.get("source") or "custom").strip()
            if sid:
                names_or_ids.add(sid.lower())
            if name:
                names_or_ids.add(name.lower())
            if sid and name:
                lines.append(f"- [{sid}] ({source}) {name}: {desc}")
        return lines, names_or_ids

    def _save_memory_item(
        self,
        fact: str,
        tags: tuple[str, ...],
        *,
        kind: str,
        scope: str | None,
        is_legacy: bool,
    ) -> dict[str, Any] | None:
        confidence = 0.75
        if not is_legacy and scope in VALID_MEMORY_SCOPES:
            try:
                res = self.memory_store.save(
                    fact,
                    tags,
                    kind=kind,
                    confidence=confidence,
                    source="consolidation",
                    scope_type=scope,
                )
                return dict(res) if isinstance(res, Mapping) else {"created": True}
            except TypeError:
                pass
        res = self.memory_store.save(
            fact,
            tags,
            kind=kind,
            confidence=confidence,
            source="consolidation",
        )
        return dict(res) if isinstance(res, Mapping) else {"created": True}

    def run(
        self,
        history: Sequence[Mapping[str, Any]],
        *,
        tool_ledger: Sequence[Mapping[str, Any]] = (),
        had_untrusted_external_input: bool | None = False,
        existing_memories: Sequence[Mapping[str, Any]] | None = None,
        existing_skills: Sequence[Mapping[str, Any]] | None = None,
    ) -> int:
        digest = build_verified_consolidation_digest(
            history,
            tool_ledger,
            had_untrusted_external_input=bool(had_untrusted_external_input),
        )
        if not digest:
            return 0

        mem_lines, known_mem_ids = self._collect_existing_memories(existing_memories)
        skill_lines, existing_skill_keys = self._collect_existing_skills(existing_skills)

        prompt_sections = [digest]
        if mem_lines:
            prompt_sections.append("## Existing Active Memories\n" + "\n".join(mem_lines))
        if skill_lines:
            prompt_sections.append("## Existing Skills\n" + "\n".join(skill_lines))
        user_content = "\n\n".join(prompt_sections)

        events = self.provider.stream({
            "turn_id": "auto-dream",
            "provider": self.provider_name,
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": CONSOLIDATION_INSTRUCTION},
                {"role": "user", "content": user_content},
            ],
            "tools": [],
            "max_output_tokens": 4096,
        })
        text = "".join(
            str(getattr(event, "text", "") or "")
            for event in events
            if str(getattr(event, "kind", "")) == "text"
        )
        parsed = parse_consolidation_output(text)

        saved = 0
        seen_facts: set[str] = set()
        saved_by_index: dict[int, dict[str, Any]] = {}
        effective_project_id = (
            self.project_id
            or getattr(self.memory_store, "project_id", None)
            or getattr(self.memory_store, "_project_id", None)
        )

        for idx, item in enumerate(parsed["memories"][:MAX_FACTS]):
            is_legacy = bool(item.get("_legacy"))
            raw_fact = item.get("fact")
            if not isinstance(raw_fact, str):
                continue
            max_chars = MAX_FACT_CHARS if is_legacy else MAX_STRUCTURED_FACT_CHARS
            fact = " ".join(raw_fact.split())[:max_chars]
            if not fact or fact in seen_facts:
                continue
            if _SECRET.search(fact) or _PROMPT_INJECTION_RE.search(fact):
                continue
            try:
                validate_memory_content(fact)
            except ValueError:
                continue

            raw_kind = str(item.get("kind") or "fact").strip().lower()
            kind = raw_kind if raw_kind in VALID_MEMORY_KINDS else "fact"

            raw_scope = str(item.get("scope") or "").strip().lower()
            scope = raw_scope if raw_scope in VALID_MEMORY_SCOPES else None

            raw_tags = item.get("tags")
            if is_legacy or not isinstance(raw_tags, (list, tuple)):
                tags_tuple: tuple[str, ...] = ("auto-dream",)
            else:
                cleaned_tags = [
                    str(t).strip()
                    for t in raw_tags
                    if isinstance(t, str)
                    and str(t).strip()
                    and not _SECRET.search(str(t))
                    and not _PROMPT_INJECTION_RE.search(str(t))
                ]
                tags_tuple = tuple(dict.fromkeys(["auto-dream", *cleaned_tags])) if cleaned_tags else ("auto-dream",)

            try:
                receipt = self._save_memory_item(
                    fact,
                    tags_tuple,
                    kind=kind,
                    scope=scope,
                    is_legacy=is_legacy,
                )
            except Exception:  # noqa: BLE001 - one bad fact never stops the rest
                continue

            seen_facts.add(fact)
            saved += 1
            effective_scope = scope or ("project" if effective_project_id else "user")
            info = {
                "receipt": receipt or {},
                "fact": fact,
                "kind": kind,
                "scope": effective_scope,
                "project_id": effective_project_id if effective_scope == "project" else None,
                "tags": tags_tuple,
                "source": "consolidation",
            }
            saved_by_index[idx] = info

            if (
                self.on_memory_learned is not None
                and isinstance(receipt, Mapping)
                and receipt.get("created")
                and receipt.get("memory_id")
            ):
                try:
                    self.on_memory_learned(receipt, info)
                except Exception:  # noqa: BLE001 - UI card callback never breaks consolidation
                    pass

        supersede_fn = getattr(self.memory_store, "supersede_memory", None)
        if callable(supersede_fn):
            for sup in parsed["supersedes"]:
                old_id = str(sup.get("old_memory_id") or "").strip()
                new_idx = sup.get("new_fact_index")
                if not old_id or isinstance(new_idx, bool) or not isinstance(new_idx, int):
                    continue
                if known_mem_ids and old_id not in known_mem_ids:
                    continue
                target_saved = saved_by_index.get(new_idx)
                if target_saved is None:
                    continue
                new_id = str((target_saved.get("receipt") or {}).get("memory_id") or "").strip()
                if not new_id or new_id == old_id:
                    continue
                try:
                    supersede_fn(old_id, new_id)
                except Exception:  # noqa: BLE001
                    continue

        raw_proposals = parsed["skill_proposals"]
        if raw_proposals:
            effective_user = (
                self.user_id
                or str(getattr(self.memory_store, "_user_id", "") or "").strip()
                or "local-user"
            )
            store = self.proposal_store or SkillProposalStore()
            for raw_prop in raw_proposals:
                action = str(raw_prop.get("action") or "create").strip().lower()
                prop_name = str(raw_prop.get("name") or "").strip().lower()
                if action == "create" and prop_name and prop_name in existing_skill_keys:
                    continue
                proposed = store.propose_safe(
                    effective_user,
                    raw_prop,
                    conversation_id=self.conversation_id,
                    skill_library=self.skill_library,
                    available_tools=self.available_tools,
                )
                if (
                    self.on_skill_proposed is not None
                    and isinstance(proposed, Mapping)
                    and proposed.get("proposal_id")
                ):
                    try:
                        self.on_skill_proposed(proposed)
                    except Exception:  # noqa: BLE001 - callback never breaks consolidation
                        pass

        return saved


__all__ = [
    "CONSOLIDATION_INTERVAL",
    "MIN_TURNS_BETWEEN_RUNS",
    "ConsolidationState",
    "FriendReflectionConsolidator",
    "MemoryConsolidator",
    "build_verified_consolidation_digest",
    "parse_consolidation_output",
    "parse_facts",
    "transcript_excerpt",
]
