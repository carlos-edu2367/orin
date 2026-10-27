"""Zero-token-waste 3-Tier milestone bridge between Orin Friend and Orin Code.

Classifies `Orin Code` activity events into:
- **Tier 0 (Silent / UI-Only):** High-frequency events (`filesystem.read`, `filesystem.write`,
  `assistant.delta`, `agent.thought_delta`, `terminal.output_summary`) emit only UI progress
  updates (`code_session.progress`) with 0 LLM tokens and 0 TTS chunks.
- **Tier 1 (Direct-to-TTS + Compact Reminder Receipt):** Structural milestones (`todo_write`
  progress/final stretch, `code_mode.stage_changed`, `code_mode.validation_started`) generate
  a persona-calibrated PT-BR phrase sent directly to TTS (`speak.chunk_ready`) with a 30s
  cooldown and a `<= 25 token` receipt in `ReminderQueue`, using 0 LLM calls.
- **Tier 2 (Critical Wakeup with `<150 Token` Digest):** Terminal or blocking events
  (`turn.waiting_user`, `code_mode.decision_required`, `code_mode.plan_ready`, `turn.failed`,
  `turn.completed`, `code_mode.completed`) build a sanitized `CompactCodeDigest` (`<= 600` chars,
  `< 150` tokens) and trigger the Orin Friend wakeup callback.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
import json
import re
import threading
import time
from typing import Any

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.agentic.reminders import ReminderQueue


MAX_DIGEST_CHARS = 600
MAX_DIGEST_TOKENS = 150
MAX_DIGEST_FILES = 8
DEFAULT_TIER1_COOLDOWN_SECONDS = 30.0

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DANGEROUS_TAGS_RE = re.compile(
    r"<\s*/?\s*(?:system(?:[-_\s]*reminder)?|speak|prompt|instruction|developer)\b[^>]*>",
    re.IGNORECASE,
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?ix)(?:"
    r"ignore\s+(?:all\s+)?(?:previous|prior|above|todas\s+as)\s+(?:instructions|policy|prompts|rules|instru[cç][oõ]es|regras)"
    r"|override\s+(?:all\s+)?(?:system\s+)?(?:policy|instructions|rules)"
    r"|system\s+prompt"
    r"|developer\s+message"
    r"|you\s+are\s+now\b"
    r"|rm\s+-rf\s+/"
    r")"
)


class MilestoneTier(IntEnum):
    TIER_0 = 0
    TIER_1 = 1
    TIER_2 = 2
    SILENT = 0
    DIRECT_TTS = 1
    WAKEUP = 2


def sanitize_code_digest_text(text: str | None, *, max_chars: int = 120) -> str | None:
    """Strip tags, control chars, and prompt-injection patterns, then bound length."""
    if text is None:
        return None
    cleaned = str(text)
    cleaned = _ANSI_ESCAPE_RE.sub("", cleaned)
    cleaned = _CONTROL_CHARS_RE.sub(" ", cleaned)
    cleaned = _DANGEROUS_TAGS_RE.sub(" ", cleaned)
    cleaned = _PROMPT_INJECTION_RE.sub("[redacted]", cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        return None
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max(1, max_chars - 1)].rstrip() + "…"
    return cleaned


def _compact_file_label(path: str, *, max_chars: int = 36) -> str:
    norm = str(path or "").strip().replace("\\", "/")
    norm = _CONTROL_CHARS_RE.sub("", norm)
    if len(norm) <= max_chars:
        return norm
    parts = [p for p in norm.split("/") if p]
    if len(parts) >= 2:
        tail = "/".join(parts[-2:])
        if len(tail) <= max_chars:
            return tail
    return norm[-max_chars:]


@dataclass(slots=True)
class CompactCodeDigest:
    """Strictly bounded (`<150 tokens`, `<=600 chars`) digest of an Orin Code session."""

    code_conversation_id: str
    event_kind: str
    title: str
    todos_summary: str = "0/0"
    files_touched: tuple[str, ...] = ()
    pending_prompt_or_error: str | None = None
    state: str = "running"
    stage: str = "implementing"
    todos_completed: int = 0
    todos_total: int = 0
    active_todo: str | None = None

    def __post_init__(self) -> None:
        self.code_conversation_id = (str(self.code_conversation_id or "").strip())[:64]
        self.event_kind = (str(self.event_kind or "status").strip())[:32]
        self.title = sanitize_code_digest_text(self.title, max_chars=72) or "Sessão Orin Code"
        self.state = (str(self.state or "running").strip())[:24]
        self.stage = (str(self.stage or "implementing").strip())[:24]
        self.todos_completed = max(0, int(self.todos_completed or 0))
        self.todos_total = max(0, int(self.todos_total or 0))
        if not self.todos_summary or self.todos_summary == "0/0":
            self.todos_summary = f"{self.todos_completed}/{self.todos_total}"
        else:
            self.todos_summary = str(self.todos_summary)[:16]
        self.active_todo = sanitize_code_digest_text(self.active_todo, max_chars=72)
        deduped_files: list[str] = []
        for raw_file in self.files_touched or ():
            label = _compact_file_label(str(raw_file))
            if label and label not in deduped_files:
                deduped_files.append(label)
            if len(deduped_files) >= MAX_DIGEST_FILES:
                break
        self.files_touched = tuple(deduped_files)
        self.pending_prompt_or_error = sanitize_code_digest_text(
            self.pending_prompt_or_error,
            max_chars=120,
        )

    @property
    def event(self) -> str:
        return self.event_kind

    @property
    def files_modified(self) -> list[str]:
        return list(self.files_touched)

    @property
    def files_changed(self) -> list[str]:
        return list(self.files_touched)

    @property
    def pending_question(self) -> str | None:
        return self.pending_prompt_or_error

    @property
    def token_estimate(self) -> int:
        serialized = self.to_json()
        # Conservative token estimation: max of word-based and char/4 heuristic, capped below 150
        char_tokens = (len(serialized) + 3) // 4
        word_tokens = int(len(serialized.split()) * 1.35)
        return max(1, max(char_tokens, word_tokens))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code_conversation_id": self.code_conversation_id,
            "event_kind": self.event_kind,
            "title": self.title,
            "state": self.state,
            "stage": self.stage,
            "todos_completed": self.todos_completed,
            "todos_total": self.todos_total,
            "todos_summary": self.todos_summary,
            "active_todo": self.active_todo,
            "files_touched": list(self.files_touched),
            "files_modified": list(self.files_touched),
            "pending_prompt_or_error": self.pending_prompt_or_error,
            "pending_question": self.pending_prompt_or_error,
        }

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "code_conversation_id": self.code_conversation_id,
            "event_kind": self.event_kind,
            "title": self.title,
            "state": self.state,
            "stage": self.stage,
            "todos_summary": self.todos_summary,
            "files_touched": list(self.files_touched),
            "pending_prompt_or_error": self.pending_prompt_or_error,
        }
        if self.active_todo:
            payload["active_todo"] = self.active_todo
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) > MAX_DIGEST_CHARS:
            # Progressively tighten optional fields to guarantee <= 600 chars
            if payload.get("pending_prompt_or_error"):
                payload["pending_prompt_or_error"] = str(payload["pending_prompt_or_error"])[:60] + "…"
            payload["title"] = str(payload["title"])[:40]
            payload.pop("active_todo", None)
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) > MAX_DIGEST_CHARS:
            payload["files_touched"] = [Path(f).name[:20] for f in self.files_touched[:MAX_DIGEST_FILES]]
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return serialized[:MAX_DIGEST_CHARS]


@dataclass(slots=True)
class WatcherEventOutcome:
    tier: MilestoneTier
    spoke: bool
    spoken_text: str | None
    speak_event: dict[str, Any] | None
    ui_update: dict[str, Any]
    reminder_receipt: str | None = None
    digest: CompactCodeDigest | None = None
    cooldown_suppressed: bool = False
    llm_wakeup_triggered: bool = False

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


_TIER0_EVENT_TYPES = frozenset({
    AgentActivityEventType.FILESYSTEM_READ.value,
    AgentActivityEventType.FILESYSTEM_WRITE.value,
    AgentActivityEventType.ASSISTANT_DELTA.value,
    AgentActivityEventType.AGENT_THOUGHT_DELTA.value,
    AgentActivityEventType.TERMINAL_OUTPUT_SUMMARY.value,
    AgentActivityEventType.TERMINAL_STARTED.value,
    AgentActivityEventType.TOOL_REQUESTED.value,
    AgentActivityEventType.TOOL_PROGRESSED.value,
    AgentActivityEventType.CONTEXT_UPDATED.value,
    AgentActivityEventType.CONTEXT_COMPACTED.value,
    AgentActivityEventType.MODEL_ROUTING_STARTED.value,
    AgentActivityEventType.TURN_STARTED.value,
    AgentActivityEventType.BROWSER_STARTED.value,
    AgentActivityEventType.BROWSER_NAVIGATED.value,
    AgentActivityEventType.BROWSER_ARTIFACT.value,
})

_TIER1_EVENT_TYPES = frozenset({
    "todo_write",
    "TodoWrite",
    AgentActivityEventType.CODE_MODE_STAGE_CHANGED.value,
    AgentActivityEventType.CODE_MODE_VALIDATION_STARTED.value,
})

_TIER2_EVENT_TYPES = frozenset({
    AgentActivityEventType.TURN_WAITING_USER.value,
    AgentActivityEventType.CODE_MODE_DECISION_REQUIRED.value,
    AgentActivityEventType.CODE_MODE_PLAN_READY.value,
    AgentActivityEventType.TOOL_PERMISSION_REQUIRED.value,
    AgentActivityEventType.CODE_MODE_BLOCKED.value,
    AgentActivityEventType.TURN_FAILED.value,
    AgentActivityEventType.TURN_COMPLETED.value,
    AgentActivityEventType.CODE_MODE_COMPLETED.value,
    AgentActivityEventType.CODE_MODE_COMPLETED_WITH_CAVEATS.value,
})

_STAGE_LABELS_PTBR: dict[str, str] = {
    "planning": "planejamento",
    "implementing": "implementação",
    "validating": "validação e testes",
    "verifying": "verificação",
    "completed": "conclusão",
    "blocked": "aguardando decisão",
}


def _vocative_for_persona(persona: Any) -> str:
    """Select PT-BR vocative based on persona formality trait."""
    if persona is None:
        return "senhor"
    traits: Mapping[str, Any] = {}
    if hasattr(persona, "traits") and isinstance(persona.traits, Mapping):
        traits = persona.traits
    elif isinstance(persona, Mapping):
        raw_traits = persona.get("traits")
        traits = raw_traits if isinstance(raw_traits, Mapping) else persona
    try:
        formality = float(traits.get("formality", 0.35))
    except (TypeError, ValueError):
        formality = 0.35
    if formality >= 0.30:
        return "senhor"
    return "parceiro"


def _lower_first(text: str) -> str:
    cleaned = str(text or "").strip().rstrip(".")
    if not cleaned:
        return ""
    return cleaned[0].lower() + cleaned[1:]


class CodeSessionMilestoneWatcher:
    """Stateful 3-Tier filter that bridges an `Orin Code` session to `Orin Friend` with zero token waste."""

    def __init__(
        self,
        *,
        friend_conversation_id: str,
        code_conversation_id: str,
        user_id: str,
        title: str = "Sessão Orin Code",
        persona: Any = None,
        reminder_queue: ReminderQueue | None = None,
        on_speak_chunk: Callable[[dict[str, Any]], None] | None = None,
        on_ui_update: Callable[[dict[str, Any]], None] | None = None,
        on_wakeup: Callable[[CompactCodeDigest], None] | None = None,
        cooldown_seconds: float = DEFAULT_TIER1_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.friend_conversation_id = str(friend_conversation_id or "").strip()
        self.code_conversation_id = str(code_conversation_id or "").strip()
        self.user_id = str(user_id or "").strip()
        self.title = str(title or "Sessão Orin Code").strip()
        self.persona = persona
        self.reminder_queue = reminder_queue
        self._on_speak_chunk = on_speak_chunk
        self._on_ui_update = on_ui_update
        self._on_wakeup = on_wakeup
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()

        self.state: str = "running"
        self.stage: str = "implementing"
        self.files_read_count: int = 0
        self.files_modified_count: int = 0
        self.files_read: list[str] = []
        self.files_modified: list[str] = []
        self.todos_completed: int = 0
        self.todos_total: int = 0
        self.active_todo: str | None = None
        self.pending_prompt_or_error: str | None = None

        self.speak_chunks: list[dict[str, Any]] = []
        self.ui_updates: list[dict[str, Any]] = []
        self.wakeups: list[CompactCodeDigest] = []
        self.llm_wakeups: int = 0

        self._last_tier1_spoken_at: float | None = None
        self._milestone_seq: int = 0
        self._seen_event_ids: set[str] = set()

    @property
    def files_touched(self) -> list[str]:
        return list(self.files_modified)

    @property
    def pending_question(self) -> str | None:
        return self.pending_prompt_or_error

    @pending_question.setter
    def pending_question(self, value: str | None) -> None:
        self.pending_prompt_or_error = sanitize_code_digest_text(value, max_chars=120) if value else None

    def snapshot_status(self) -> dict[str, Any]:
        return {
            "code_conversation_id": self.code_conversation_id,
            "friend_conversation_id": self.friend_conversation_id,
            "title": self.title,
            "state": self.state,
            "stage": self.stage,
            "todos_completed": self.todos_completed,
            "todos_total": self.todos_total,
            "todos_summary": f"{self.todos_completed}/{self.todos_total}",
            "active_todo": self.active_todo,
            "files_modified": list(self.files_modified),
            "files_read_count": self.files_read_count,
            "pending_question": self.pending_prompt_or_error,
            "last_error": self.pending_prompt_or_error if self.state == "failed" else None,
            "llm_wakeups": self.llm_wakeups,
        }

    def _extract_event_fields(
        self,
        event: Mapping[str, Any] | AgentActivityEvent,
    ) -> tuple[str, str, dict[str, Any], str | None]:
        if isinstance(event, AgentActivityEvent):
            ev_type = event.event_type.value
            summary = event.summary
            payload = dict(event.payload or {})
            ev_id = event.event_id
        elif isinstance(event, Mapping):
            raw_type = event.get("event_type") or event.get("type") or ""
            ev_type = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
            summary = str(event.get("summary") or "")
            raw_payload = event.get("payload")
            payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else dict(event)
            ev_id = str(event.get("event_id") or "") or None
        else:
            raise TypeError("event must be a Mapping or AgentActivityEvent")
        return ev_type.strip(), summary.strip(), payload, ev_id

    def _update_todos_from_payload(self, payload: Mapping[str, Any]) -> tuple[int, int, int, int, str | None]:
        prev_completed = self.todos_completed
        prev_total = self.todos_total
        todos_list = payload.get("todos")
        active_form: str | None = None

        if isinstance(todos_list, Sequence) and not isinstance(todos_list, (str, bytes)):
            completed = 0
            total = 0
            for item in todos_list:
                if not isinstance(item, Mapping):
                    continue
                total += 1
                status = str(item.get("status") or "").strip().lower()
                if status == "completed":
                    completed += 1
                elif status == "in_progress" and active_form is None:
                    raw_af = str(
                        item.get("active_form") or item.get("activeForm") or item.get("content") or ""
                    ).strip()
                    active_form = sanitize_code_digest_text(raw_af, max_chars=72)
            if active_form is None:
                for item in todos_list:
                    if isinstance(item, Mapping) and str(item.get("status") or "").strip().lower() != "completed":
                        raw_af = str(
                            item.get("active_form") or item.get("activeForm") or item.get("content") or ""
                        ).strip()
                        active_form = sanitize_code_digest_text(raw_af, max_chars=72)
                        if active_form:
                            break
            self.todos_completed = min(completed, total) if total > 0 else completed
            self.todos_total = total
            self.active_todo = active_form
        else:
            if "completed" in payload or "todos_completed" in payload:
                try:
                    self.todos_completed = max(
                        0,
                        int(payload.get("completed", payload.get("todos_completed", self.todos_completed)) or 0),
                    )
                except (TypeError, ValueError):
                    pass
            if "total" in payload or "todos_total" in payload:
                try:
                    self.todos_total = max(
                        0,
                        int(payload.get("total", payload.get("todos_total", self.todos_total)) or 0),
                    )
                except (TypeError, ValueError):
                    pass
            if self.todos_total > 0 and self.todos_completed > self.todos_total:
                self.todos_completed = self.todos_total
            raw_active = payload.get("active_form") or payload.get("activeForm") or payload.get("active_todo")
            if raw_active is not None:
                self.active_todo = sanitize_code_digest_text(str(raw_active), max_chars=72)

        return prev_completed, prev_total, self.todos_completed, self.todos_total, self.active_todo

    def _build_ui_update(self, source_event_type: str, summary: str) -> dict[str, Any]:
        return {
            "event_type": "code_session.progress",
            "source_event_type": source_event_type,
            "code_conversation_id": self.code_conversation_id,
            "friend_conversation_id": self.friend_conversation_id,
            "title": self.title,
            "state": self.state,
            "stage": self.stage,
            "files_read_count": self.files_read_count,
            "files_modified_count": self.files_modified_count,
            "files_modified": list(self.files_modified),
            "todos_completed": self.todos_completed,
            "todos_total": self.todos_total,
            "todos_summary": f"{self.todos_completed}/{self.todos_total}",
            "active_todo": self.active_todo,
            "summary": summary[:200],
        }

    def _render_tier1_phrase_and_receipt(
        self,
        ev_type: str,
        payload: Mapping[str, Any],
        summary: str,
    ) -> tuple[str, str]:
        vocative = _vocative_for_persona(self.persona)
        completed = self.todos_completed
        total = self.todos_total
        active = self.active_todo or ""
        active_lower = _lower_first(active)

        if ev_type == AgentActivityEventType.CODE_MODE_VALIDATION_STARTED.value or (
            str(payload.get("tool_name") or "").lower() in {"verify_project", "verifyproject"}
        ):
            phrase = f"Já estamos na reta final do projeto, {vocative}! Executando a validação e os testes agora."
            receipt = f"[Code Milestone Narrated: {completed}/{total} todos done — Executando validação e testes]"
            return phrase, receipt

        if ev_type == AgentActivityEventType.CODE_MODE_STAGE_CHANGED.value:
            raw_stage = str(payload.get("stage") or payload.get("to_stage") or self.stage or "implementing").lower()
            self.stage = raw_stage
            stage_pt = _STAGE_LABELS_PTBR.get(raw_stage, raw_stage)
            if raw_stage in {"validating", "verifying"}:
                phrase = f"Já estamos na reta final do projeto, {vocative}! Iniciando a etapa de {stage_pt}."
            else:
                phrase = f"Avançamos para a etapa de {stage_pt} no projeto, {vocative}."
            receipt = f"[Code Milestone Narrated: stage={raw_stage} ({completed}/{total} todos done)]"
            return phrase, receipt

        # todo_write milestone
        if total > 0 and (completed / total) >= 0.70:
            suffix = f", agora {active_lower}" if active_lower else ""
            phrase = f"Já estamos na reta final do projeto, {vocative}! {completed} de {total} etapas concluídas{suffix}."
            receipt_detail = f" — {active}" if active else ""
            receipt = f"[Code Milestone Narrated: {completed}/{total} todos done{receipt_detail}]"
            return phrase, receipt

        if total > 0 and completed == 0:
            suffix = f", começando por {active_lower}" if active_lower else ""
            phrase = f"Plano estruturado com {total} etapas, {vocative}{suffix}."
            receipt_detail = f" — {active}" if active else ""
            receipt = f"[Code Milestone Narrated: 0/{total} todos planned{receipt_detail}]"
            return phrase, receipt

        if total > 0 and (completed / total) >= 0.50:
            suffix = f", agora {active_lower}" if active_lower else ""
            phrase = f"Já passamos da metade do projeto, {vocative}: {completed} de {total} etapas concluídas{suffix}."
            receipt_detail = f" — {active}" if active else ""
            receipt = f"[Code Milestone Narrated: {completed}/{total} todos done{receipt_detail}]"
            return phrase, receipt

        suffix = f", agora {active_lower}" if active_lower else ""
        phrase = f"Avançamos no projeto, {vocative}: {completed} de {total} etapas concluídas{suffix}."
        receipt_detail = f" — {active}" if active else ""
        receipt = f"[Code Milestone Narrated: {completed}/{total} todos done{receipt_detail}]"
        return phrase, receipt

    def ingest_event(
        self,
        event: Mapping[str, Any] | AgentActivityEvent,
        *,
        now: float | None = None,
    ) -> WatcherEventOutcome:
        ev_type, summary, payload, ev_id = self._extract_event_fields(event)
        current_time = float(now) if now is not None else float(self._clock())

        with self._lock:
            if ev_id:
                if ev_id in self._seen_event_ids:
                    ui_snapshot = self._build_ui_update(ev_type, summary)
                    return WatcherEventOutcome(
                        tier=MilestoneTier.TIER_0,
                        spoke=False,
                        spoken_text=None,
                        speak_event=None,
                        ui_update=ui_snapshot,
                    )
                self._seen_event_ids.add(ev_id)

            tool_name = str(payload.get("tool_name") or "").strip()

            # Check if a tool.finished / tool.started event is actually todo_write or verify_project
            effective_type = ev_type
            if ev_type in {
                AgentActivityEventType.TOOL_FINISHED.value,
                AgentActivityEventType.TOOL_STARTED.value,
            }:
                if tool_name in {"todo_write", "TodoWrite"} or "todos" in payload:
                    effective_type = "todo_write"
                elif tool_name in {"verify_project", "VerifyProject"} and ev_type == AgentActivityEventType.TOOL_STARTED.value:
                    effective_type = AgentActivityEventType.CODE_MODE_VALIDATION_STARTED.value
                elif payload.get("plan_ready") or payload.get("code_approval") or payload.get("wait_for_user"):
                    effective_type = AgentActivityEventType.CODE_MODE_PLAN_READY.value

            # Track file reads and writes
            if ev_type == AgentActivityEventType.FILESYSTEM_READ.value or (
                ev_type == AgentActivityEventType.TOOL_FINISHED.value and tool_name in {"read_file", "view_file", "Read"}
            ):
                self.files_read_count += 1
                read_path = str(payload.get("path") or payload.get("file_path") or "").strip()
                if read_path and read_path not in self.files_read:
                    self.files_read.append(read_path)

            if ev_type == AgentActivityEventType.FILESYSTEM_WRITE.value or (
                ev_type == AgentActivityEventType.TOOL_FINISHED.value
                and tool_name in {"write_file", "edit_file", "notebook_edit", "Write", "Edit"}
            ):
                write_path = str(payload.get("path") or payload.get("file_path") or "").strip()
                if write_path:
                    if write_path not in self.files_modified:
                        self.files_modified.append(write_path)
                    self.files_modified_count = len(self.files_modified)
                else:
                    self.files_modified_count += 1

            # --- TIER 2: Critical / Terminal Events ---
            if effective_type in _TIER2_EVENT_TYPES:
                if effective_type in {
                    AgentActivityEventType.TURN_WAITING_USER.value,
                    AgentActivityEventType.CODE_MODE_DECISION_REQUIRED.value,
                    AgentActivityEventType.CODE_MODE_PLAN_READY.value,
                    AgentActivityEventType.TOOL_PERMISSION_REQUIRED.value,
                    AgentActivityEventType.CODE_MODE_BLOCKED.value,
                }:
                    self.state = "waiting_user"
                    self.stage = "planning" if "plan" in effective_type else "blocked"
                    raw_question = (
                        payload.get("question")
                        or payload.get("plan_summary")
                        or payload.get("reason")
                        or payload.get("summary")
                        or summary
                    )
                    if not raw_question and isinstance(payload.get("questions"), Sequence):
                        qs = payload.get("questions")
                        if qs and isinstance(qs[0], Mapping):
                            raw_question = qs[0].get("question")
                    self.pending_prompt_or_error = sanitize_code_digest_text(
                        str(raw_question or "Aguardando aprovação ou resposta do usuário"),
                        max_chars=120,
                    )
                    event_kind = (
                        "plan_ready"
                        if effective_type == AgentActivityEventType.CODE_MODE_PLAN_READY.value
                        else (
                            "decision_required"
                            if effective_type == AgentActivityEventType.CODE_MODE_DECISION_REQUIRED.value
                            else "waiting_user"
                        )
                    )
                elif effective_type == AgentActivityEventType.TURN_FAILED.value:
                    self.state = "failed"
                    self.stage = "blocked"
                    raw_err = (
                        payload.get("error")
                        or payload.get("message")
                        or payload.get("error_code")
                        or summary
                        or "Falha na execução do turno"
                    )
                    self.pending_prompt_or_error = sanitize_code_digest_text(str(raw_err), max_chars=120)
                    event_kind = "failed"
                else:
                    self.state = "completed"
                    self.stage = "completed"
                    if self.todos_total > 0:
                        self.todos_completed = self.todos_total
                    self.active_todo = None
                    self.pending_prompt_or_error = sanitize_code_digest_text(
                        str(payload.get("summary") or summary or "Execução concluída com sucesso"),
                        max_chars=120,
                    )
                    event_kind = "completed"

                ui_update = self._build_ui_update(ev_type, summary)
                self.ui_updates.append(ui_update)
                if callable(self._on_ui_update):
                    self._on_ui_update(ui_update)

                digest = self.build_digest(event_kind=event_kind)
                self.wakeups.append(digest)
                self.llm_wakeups += 1

                receipt = f"[Code Session Critical Event ({event_kind})]: {digest.to_json()}"
                if self.reminder_queue is not None:
                    self.reminder_queue.add(
                        "code_digest",
                        receipt,
                        key=f"code_digest:{self.code_conversation_id}:{event_kind}:{self.llm_wakeups}",
                    )
                if callable(self._on_wakeup):
                    self._on_wakeup(digest)

                return WatcherEventOutcome(
                    tier=MilestoneTier.TIER_2,
                    spoke=False,
                    spoken_text=None,
                    speak_event=None,
                    ui_update=ui_update,
                    reminder_receipt=receipt,
                    digest=digest,
                    cooldown_suppressed=False,
                    llm_wakeup_triggered=True,
                )

            # --- TIER 1: Milestone Narration (Direct-to-TTS, 0 LLM Tokens) ---
            if effective_type in _TIER1_EVENT_TYPES or "todos" in payload:
                is_milestone = True
                if effective_type in {"todo_write", "TodoWrite"} or "todos" in payload:
                    prev_comp, prev_tot, new_comp, new_tot, _ = self._update_todos_from_payload(payload)
                    in_final_stretch = new_tot > 0 and (new_comp / new_tot) >= 0.70
                    progressed = (new_comp > prev_comp) or (prev_tot == 0 and new_tot > 0) or in_final_stretch
                    is_milestone = progressed
                elif effective_type == AgentActivityEventType.CODE_MODE_VALIDATION_STARTED.value:
                    self.stage = "validating"
                elif effective_type == AgentActivityEventType.CODE_MODE_STAGE_CHANGED.value:
                    raw_stage = str(payload.get("stage") or payload.get("to_stage") or "").strip().lower()
                    if raw_stage:
                        self.stage = raw_stage

                ui_update = self._build_ui_update(ev_type, summary)
                self.ui_updates.append(ui_update)
                if callable(self._on_ui_update):
                    self._on_ui_update(ui_update)

                if not is_milestone:
                    return WatcherEventOutcome(
                        tier=MilestoneTier.TIER_0,
                        spoke=False,
                        spoken_text=None,
                        speak_event=None,
                        ui_update=ui_update,
                    )

                if (
                    self._last_tier1_spoken_at is not None
                    and (current_time - self._last_tier1_spoken_at) < self.cooldown_seconds
                ):
                    return WatcherEventOutcome(
                        tier=MilestoneTier.TIER_1,
                        spoke=False,
                        spoken_text=None,
                        speak_event=None,
                        ui_update=ui_update,
                        cooldown_suppressed=True,
                        llm_wakeup_triggered=False,
                    )

                phrase, receipt = self._render_tier1_phrase_and_receipt(effective_type, payload, summary)
                self._last_tier1_spoken_at = current_time
                self._milestone_seq += 1

                speak_event = {
                    "event_type": "speak.chunk_ready",
                    "code_conversation_id": self.code_conversation_id,
                    "friend_conversation_id": self.friend_conversation_id,
                    "chunk_index": self._milestone_seq - 1,
                    "text": phrase,
                    "clean_text": phrase,
                    "tts_text": phrase,
                    "source": "code_milestone_watcher",
                }
                self.speak_chunks.append(speak_event)
                if callable(self._on_speak_chunk):
                    self._on_speak_chunk(speak_event)

                if self.reminder_queue is not None:
                    self.reminder_queue.add(
                        "code_milestone",
                        receipt,
                        key=f"code_milestone:{self.code_conversation_id}:{self._milestone_seq}",
                    )

                return WatcherEventOutcome(
                    tier=MilestoneTier.TIER_1,
                    spoke=True,
                    spoken_text=phrase,
                    speak_event=speak_event,
                    ui_update=ui_update,
                    reminder_receipt=receipt,
                    cooldown_suppressed=False,
                    llm_wakeup_triggered=False,
                )

            # --- TIER 0: Silent UI-Only Update ---
            ui_update = self._build_ui_update(ev_type, summary)
            self.ui_updates.append(ui_update)
            if callable(self._on_ui_update):
                self._on_ui_update(ui_update)
            return WatcherEventOutcome(
                tier=MilestoneTier.TIER_0,
                spoke=False,
                spoken_text=None,
                speak_event=None,
                ui_update=ui_update,
                cooldown_suppressed=False,
                llm_wakeup_triggered=False,
            )

    # Aliases for ergonomic caller/test usage
    process_event = ingest_event
    handle_event = ingest_event
    observe_event = ingest_event

    def build_digest(
        self,
        event_kind: str = "status",
        *,
        state: str | None = None,
        pending_prompt_or_error: str | None = None,
    ) -> CompactCodeDigest:
        return CompactCodeDigest(
            code_conversation_id=self.code_conversation_id,
            event_kind=event_kind,
            title=self.title,
            state=state or self.state,
            stage=self.stage,
            todos_completed=self.todos_completed,
            todos_total=self.todos_total,
            todos_summary=f"{self.todos_completed}/{self.todos_total}",
            active_todo=self.active_todo,
            files_touched=tuple(self.files_modified[:MAX_DIGEST_FILES]),
            pending_prompt_or_error=(
                pending_prompt_or_error if pending_prompt_or_error is not None else self.pending_prompt_or_error
            ),
        )

    def sync_from_conversation(self, conv: Mapping[str, Any]) -> CompactCodeDigest:
        """Synchronize state from a `PostgresChatStore.get()` conversation snapshot without emitting audio."""
        if isinstance(conv.get("title"), str) and conv["title"].strip():
            self.title = str(conv["title"]).strip()
        if isinstance(conv.get("state"), str) and conv["state"].strip():
            self.state = str(conv["state"]).strip()
        activities = conv.get("activities")
        if isinstance(activities, Sequence):
            saved_speak_cb = self._on_speak_chunk
            saved_wakeup_cb = self._on_wakeup
            try:
                self._on_speak_chunk = None
                self._on_wakeup = None
                for act in activities:
                    if isinstance(act, Mapping):
                        self.ingest_event(act)
            finally:
                self._on_speak_chunk = saved_speak_cb
                self._on_wakeup = saved_wakeup_cb
        if isinstance(conv.get("state"), str) and conv["state"].strip():
            self.state = str(conv["state"]).strip()
        return self.build_digest(event_kind="status")


class CodeSessionMilestoneWatcherRegistry:
    """Thread-safe registry mapping `code_conversation_id` to its `CodeSessionMilestoneWatcher`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_code_id: dict[str, CodeSessionMilestoneWatcher] = {}
        self._by_friend_id: dict[str, list[str]] = {}

    def register(
        self,
        watcher: CodeSessionMilestoneWatcher | None = None,
        *,
        friend_conversation_id: str = "",
        code_conversation_id: str = "",
        user_id: str = "",
        title: str = "Sessão Orin Code",
        persona: Any = None,
        reminder_queue: ReminderQueue | None = None,
        on_speak_chunk: Callable[[dict[str, Any]], None] | None = None,
        on_ui_update: Callable[[dict[str, Any]], None] | None = None,
        on_wakeup: Callable[[CompactCodeDigest], None] | None = None,
        cooldown_seconds: float = DEFAULT_TIER1_COOLDOWN_SECONDS,
    ) -> CodeSessionMilestoneWatcher:
        if watcher is None:
            watcher = CodeSessionMilestoneWatcher(
                friend_conversation_id=friend_conversation_id,
                code_conversation_id=code_conversation_id,
                user_id=user_id,
                title=title,
                persona=persona,
                reminder_queue=reminder_queue,
                on_speak_chunk=on_speak_chunk,
                on_ui_update=on_ui_update,
                on_wakeup=on_wakeup,
                cooldown_seconds=cooldown_seconds,
            )
        with self._lock:
            self._by_code_id[watcher.code_conversation_id] = watcher
            bucket = self._by_friend_id.setdefault(watcher.friend_conversation_id, [])
            if watcher.code_conversation_id not in bucket:
                bucket.append(watcher.code_conversation_id)
        return watcher

    def get(self, code_conversation_id: str, *, user_id: str | None = None) -> CodeSessionMilestoneWatcher | None:
        with self._lock:
            watcher = self._by_code_id.get(str(code_conversation_id or "").strip())
            if watcher is None:
                return None
            if user_id is not None and watcher.user_id != user_id:
                return None
            return watcher

    def for_friend(self, friend_conversation_id: str, *, user_id: str | None = None) -> list[CodeSessionMilestoneWatcher]:
        with self._lock:
            code_ids = list(self._by_friend_id.get(str(friend_conversation_id or "").strip(), ()))
            watchers = [self._by_code_id[cid] for cid in code_ids if cid in self._by_code_id]
        if user_id is not None:
            watchers = [w for w in watchers if w.user_id == user_id]
        return watchers

    def remove(self, code_conversation_id: str) -> None:
        with self._lock:
            watcher = self._by_code_id.pop(str(code_conversation_id or "").strip(), None)
            if watcher is not None:
                bucket = self._by_friend_id.get(watcher.friend_conversation_id)
                if bucket and watcher.code_conversation_id in bucket:
                    bucket.remove(watcher.code_conversation_id)


DEFAULT_MILESTONE_WATCHER_REGISTRY = CodeSessionMilestoneWatcherRegistry()


def get_milestone_watcher_registry() -> CodeSessionMilestoneWatcherRegistry:
    return DEFAULT_MILESTONE_WATCHER_REGISTRY


__all__ = [
    "CodeSessionMilestoneWatcher",
    "CodeSessionMilestoneWatcherRegistry",
    "CompactCodeDigest",
    "DEFAULT_MILESTONE_WATCHER_REGISTRY",
    "DEFAULT_TIER1_COOLDOWN_SECONDS",
    "MAX_DIGEST_CHARS",
    "MAX_DIGEST_FILES",
    "MAX_DIGEST_TOKENS",
    "MilestoneTier",
    "WatcherEventOutcome",
    "get_milestone_watcher_registry",
    "sanitize_code_digest_text",
]
