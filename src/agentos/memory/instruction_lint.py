"""Read-only contradiction linter between active memories and instruction files (WS-D7, D-RF11).

Loads workspace instruction files via ``agentos.memory_files.loader.MemoryFiles`` (including
``CLAUDE.md``, ``ORIN.md``, ``AGENTS.md``, ``.orin/ORIN.md``, ``@path`` imports, rule dirs and
subdirectory instruction files), selects active memories sharing significant terms with those
files, and optionally queries a configured lightweight model to flag direct factual
contradictions.

Every item returned by the model is strictly verified against the real active memory list and
the real 1-based line content of the loaded instruction file; unverified or hallucinated items
are discarded. The linter never modifies memories or instruction files.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentos.file_lock import file_lock as _file_lock
from agentos.memory_files.loader import DIRECTORY_FILES, MAX_FILE_BYTES, MemoryFile, MemoryFiles
from agentos.settings.loader import load_effective_settings
from agentos.settings.paths import orin_user_dir
from agentos.settings.registry import resolve as resolve_setting

INSTRUCTION_LINT_INTERVAL = timedelta(hours=24)
MAX_MEMORIES_PER_CALL = 25
MAX_FILES_CHARS_PER_CALL = 24_000
MAX_LINT_CALLS = 6
MAX_SUBDIR_DEPTH = 4
MAX_SUBDIR_FILES = 32
MAX_REASON_CHARS = 500

NO_MODEL_WARNING = (
    "Nenhum modelo configurado ou disponível para executar a verificação de contradições."
)
NO_WORKSPACE_WARNING = (
    "Nenhum workspace associado para verificar arquivos de instrução."
)

_IGNORED_SUBDIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    "target",
    "worktrees",
})

_COMPOUND_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9._/-]{2,}", re.UNICODE)
_WORD_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]{3,}", re.UNICODE)
_STOPWORDS = frozenset({
    # English
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "you", "your",
    "have", "has", "had", "not", "but", "all", "can", "use", "using", "used", "only", "when",
    "where", "what", "which", "into", "over", "under", "must", "should", "always", "never",
    "also", "any", "each", "more", "most", "some", "than", "then", "them", "they", "their",
    "will", "would", "could", "about", "after", "before", "between",
    # Portuguese
    "que", "com", "para", "uma", "uns", "umas", "dos", "das", "por", "sobre", "como", "meu",
    "minha", "sua", "seu", "seus", "suas", "nos", "nas", "pelo", "pela", "pelos", "pelas",
    "este", "esta", "estes", "estas", "esse", "essa", "esses", "essas", "isso", "isto",
    "aquilo", "mais", "menos", "muito", "muita", "sempre", "nunca", "usar", "usando", "usado",
    "fazer", "feito", "deve", "devem", "pode", "podem", "quando", "onde", "qual", "quais",
    "antes", "depois", "entre", "apenas", "todos", "todas", "todo", "toda", "nao", "não",
    "ser", "estar", "ter", "tem", "sao", "são", "foi", "era", "pela", "pelo",
})

_LIGHTWEIGHT_MODEL_HINTS = ("haiku", "mini", "flash", "nano", "lite", "small", "fast")

LINT_SYSTEM_PROMPT = (
    "You audit active user/project memories against workspace instruction files "
    "(such as CLAUDE.md, ORIN.md, AGENTS.md, .orin/ORIN.md) to detect direct factual contradictions.\n\n"
    "Rules:\n"
    "1. Report ONLY direct factual contradictions (e.g., conflicting model names, commands, "
    "policies, rules, or settings between an active memory and a numbered line of an instruction file).\n"
    "2. Do NOT rewrite files or memories. Do NOT report vague overlap, style nitpicks, or non-conflicting details.\n"
    "3. Cite the EXACT file path, the EXACT 1-based line number, and a VERBATIM quote copied from that exact line.\n"
    "4. Reply ONLY with a valid JSON array matching this schema (or [] if no contradiction exists):\n"
    '[{"memory_id": "...", "file": "...", "line": 1, "quoted_line": "...", "reason": "..."}]\n'
    "Write each reason clearly and concisely in Portuguese (pt-BR)."
)

_STATE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class LintContradiction:
    memory_id: str
    memory_fact: str
    file: str
    line: int
    quoted_line: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_fact": self.memory_fact,
            "file": self.file,
            "line": self.line,
            "quoted_line": self.quoted_line,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LintReport:
    contradictions: list[LintContradiction]
    files_checked: list[str]
    memories_checked: int
    estimated_calls: int
    dry_run: bool
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradictions": [item.to_dict() for item in self.contradictions],
            "files_checked": list(self.files_checked),
            "memories_checked": self.memories_checked,
            "estimated_calls": self.estimated_calls,
            "dry_run": self.dry_run,
            "warning": self.warning,
        }


@dataclass(frozen=True, slots=True)
class _LoadedInstructionFile:
    label: str
    path: Path
    lines: tuple[str, ...]
    tokens: frozenset[str]

    def numbered_block(self) -> str:
        numbered = "\n".join(f"{idx}: {line}" for idx, line in enumerate(self.lines, start=1))
        return f"### FILE: {self.label}\n{numbered}"


ModelCaller = Callable[[str, str], str]
ProviderResolver = Callable[[str], ModelCaller | None]
MemoryLoader = Callable[[str, str | None], Sequence[Mapping[str, Any]]]


def _runtime_adapter() -> Any:
    return importlib.import_module("agentos.persistence.postgres.instruction_lint_runtime")


class InstructionLintState:
    """Tracks the last periodic instruction-lint run per scope (max 1x per 24h)."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else orin_user_dir() / "state" / "instruction-lint.json"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: Mapping[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            try:
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _due(record: Any, now: datetime, interval: timedelta = INSTRUCTION_LINT_INTERVAL) -> bool:
        if not isinstance(record, dict):
            return True
        raw_last = record.get("last_run")
        if not raw_last:
            return True
        try:
            last = datetime.fromisoformat(str(raw_last))
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        return (current - last) >= interval

    def due(self, scope_key: str, now: datetime, *, interval: timedelta = INSTRUCTION_LINT_INTERVAL) -> bool:
        with _STATE_LOCK, _file_lock(self.path) as locked:
            if not locked:
                return False
            return self._due(self._read().get(scope_key), now, interval)

    def claim_run(self, scope_key: str, now: datetime, *, interval: timedelta = INSTRUCTION_LINT_INTERVAL) -> bool:
        with _STATE_LOCK, _file_lock(self.path) as locked:
            if not locked:
                return False
            data = self._read()
            if not self._due(data.get(scope_key), now, interval):
                return False
            current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
            data[scope_key] = {"last_run": current.isoformat()}
            self._write(data)
            return True

    def mark_run(self, scope_key: str, now: datetime) -> None:
        with _STATE_LOCK, _file_lock(self.path) as locked:
            if not locked:
                return
            data = self._read()
            current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
            data[scope_key] = {"last_run": current.isoformat()}
            self._write(data)


def is_instruction_lint_enabled(project_root: Path | None = None, *, trusted: bool = False) -> bool:
    """Return the effective boolean value of ``memory.instructionLint.enabled`` (defaults to False)."""
    try:
        effective = load_effective_settings(project_root, trusted=trusted)
        resolved = resolve_setting(effective, "memory.instructionLint.enabled")
        return bool(resolved.value)
    except Exception:  # noqa: BLE001
        return False


def _extract_tokens(text: str) -> frozenset[str]:
    raw = str(text or "")
    tokens: set[str] = set()
    for match in _COMPOUND_TOKEN_RE.findall(raw):
        cleaned = match.strip(".,;:()[]{}\"'`").lower()
        if len(cleaned) >= 3 and cleaned not in _STOPWORDS:
            tokens.add(cleaned)
    for match in _WORD_TOKEN_RE.findall(raw):
        cleaned = match.lower()
        if len(cleaned) >= 3 and cleaned not in _STOPWORDS:
            tokens.add(cleaned)
    return frozenset(tokens)


def _normalize_for_quote(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _strip_outer_quotes(text: str) -> str:
    cleaned = text.strip()
    while len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'`":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _read_raw_lines(item: MemoryFile) -> tuple[str, ...]:
    try:
        if item.path.is_file():
            with item.path.open("rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
            if b"\x00" not in data:
                raw_text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
                return tuple(raw_text.splitlines())
    except OSError:
        pass
    return tuple(str(item.content or "").splitlines())


def _display_label(path: Path, workspace_root: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def _discover_subdirectory_instruction_files(workspace_root: Path) -> list[Path]:
    """Bounded scan for nested CLAUDE.md/ORIN.md/AGENTS.md files inside ``workspace_root``."""
    found: list[Path] = []
    try:
        root_resolved = workspace_root.resolve()
        if not root_resolved.is_dir():
            return found
    except OSError:
        return found

    target_names = set(DIRECTORY_FILES)
    root_depth = len(root_resolved.parts)

    for dirpath, dirnames, filenames in os.walk(root_resolved, topdown=True):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth
        # Prune ignored directories and enforce max depth
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _IGNORED_SUBDIRS
            and not (current.name == ".orin" and name == "worktrees")
            and depth < MAX_SUBDIR_DEPTH
        )
        if depth == 0:
            continue
        for rel_name in sorted(target_names):
            if "/" in rel_name:
                candidate = current / rel_name
                if candidate.is_file():
                    found.append(candidate)
            elif rel_name in filenames:
                found.append(current / rel_name)
            if len(found) >= MAX_SUBDIR_FILES:
                return found
    return found


def _wrap_provider_as_caller(provider: Any, provider_name: str, model_id: str) -> ModelCaller:
    def _call(system_prompt: str, user_prompt: str) -> str:
        events = provider.stream({
            "turn_id": f"instruction-lint-{uuid.uuid4().hex[:8]}",
            "provider": provider_name,
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "tools": [],
            "max_output_tokens": 2048,
        })
        return "".join(
            str(getattr(event, "text", "") or "")
            for event in events
            if str(getattr(event, "kind", "")) == "text"
        )

    return _call


def _choose_lightweight_model(candidates: Sequence[tuple[str, str]]) -> tuple[str, str] | None:
    if not candidates:
        return None
    for hint in _LIGHTWEIGHT_MODEL_HINTS:
        for prov, model_id in candidates:
            if hint in model_id.lower():
                return prov, model_id
    return candidates[0]


def resolve_lightweight_model_caller(engine: Any | None, user_id: str) -> ModelCaller | None:
    """Delegate lightweight model resolution to the persistence runtime adapter."""
    if engine is None or not str(user_id or "").strip():
        return None
    return _runtime_adapter().resolve_lightweight_model_caller(engine, user_id)


def _parse_raw_contradictions(raw_text: str) -> list[dict[str, Any]]:
    """Extract a list of candidate contradiction dicts from raw model output."""
    text = str(raw_text or "").strip()
    if not text:
        return []

    # Strip optional markdown code fences
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    parsed: Any = None
    try:
        parsed = json.loads(text)
    except ValueError:
        arr_start, arr_end = text.find("["), text.rfind("]")
        obj_start, obj_end = text.find("{"), text.rfind("}")
        candidates: list[str] = []
        if obj_start != -1 and obj_end > obj_start and (arr_start == -1 or obj_start < arr_start):
            candidates.append(text[obj_start : obj_end + 1])
        if arr_start != -1 and arr_end > arr_start:
            candidates.append(text[arr_start : arr_end + 1])
        for snippet in candidates:
            try:
                parsed = json.loads(snippet)
                break
            except ValueError:
                continue

    if isinstance(parsed, Mapping):
        inner = parsed.get("contradictions")
        if isinstance(inner, list):
            return [dict(item) for item in inner if isinstance(item, Mapping)]
        return []
    if isinstance(parsed, list):
        return [dict(item) for item in parsed if isinstance(item, Mapping)]
    return []


class InstructionLinter:
    """Checks active user/project memories against workspace instruction files without mutating either."""

    def __init__(
        self,
        *,
        engine: Any | None = None,
        memory_loader: MemoryLoader | None = None,
        memory_store: Any | None = None,
        model_caller: ModelCaller | None = None,
        provider: Any | None = None,
        provider_name: str | None = None,
        model_id: str | None = None,
        provider_resolver: ProviderResolver | None = None,
        walk_up: bool = True,
    ) -> None:
        self._engine = engine
        self._memory_loader = memory_loader
        self._memory_store = memory_store
        self._model_caller = model_caller
        self._provider = provider
        self._provider_name = provider_name
        self._model_id = model_id
        self._provider_resolver = provider_resolver
        self._walk_up = walk_up

    def _load_instruction_files(
        self,
        workspace_root: Path,
        *,
        trusted: bool = False,
    ) -> list[_LoadedInstructionFile]:
        loader = MemoryFiles(
            workspace_root,
            trusted=trusted,
            walk_up=self._walk_up,
        )
        for sub_file in _discover_subdirectory_instruction_files(workspace_root):
            loader.on_file_read(sub_file)

        loaded: list[_LoadedInstructionFile] = []
        seen_labels: set[str] = set()
        for item in loader.files:
            label = _display_label(item.path, workspace_root)
            if label in seen_labels:
                continue
            lines = _read_raw_lines(item)
            if not lines or not any(line.strip() for line in lines):
                continue
            seen_labels.add(label)
            tokens = _extract_tokens("\n".join(lines))
            loaded.append(
                _LoadedInstructionFile(
                    label=label,
                    path=item.path,
                    lines=lines,
                    tokens=tokens,
                )
            )
        return loaded

    def _load_active_memories(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if self._memory_loader is not None:
            return [dict(row) for row in self._memory_loader(user_id, project_id)]

        if self._engine is not None:
            return list(
                _runtime_adapter().load_active_memories_for_lint(
                    self._engine,
                    user_id,
                    project_id=project_id,
                )
            )

        if self._memory_store is not None:
            raw_rows: Sequence[Mapping[str, Any]] = ()
            for attr in ("_all", "list", "recent"):
                fn = getattr(self._memory_store, attr, None)
                if callable(fn):
                    try:
                        res = fn()
                        if isinstance(res, Sequence):
                            raw_rows = [r for r in res if isinstance(r, Mapping)]
                            break
                    except Exception:  # noqa: BLE001
                        continue
            filtered: list[dict[str, Any]] = []
            for row in raw_rows:
                if row.get("superseded_by") is not None:
                    continue
                status = str(row.get("status") or "active").strip().lower()
                if status != "active":
                    continue
                if str(row.get("scope_type") or "user").strip().lower() == "friend":
                    continue
                row_uid = row.get("user_id")
                if row_uid is not None and str(row_uid) != user_id:
                    continue
                filtered.append(dict(row))
            return filtered

        return []

    @staticmethod
    def _select_relevant_memories(
        memories: Sequence[Mapping[str, Any]],
        files: Sequence[_LoadedInstructionFile],
    ) -> list[dict[str, Any]]:
        if not memories or not files:
            return []
        instruction_tokens: set[str] = set()
        for f in files:
            instruction_tokens.update(f.tokens)
        if not instruction_tokens:
            return []

        scored: list[tuple[int, int, dict[str, Any]]] = []
        for idx, row in enumerate(memories):
            mid = str(row.get("memory_id") or "").strip()
            fact = str(row.get("fact") or "").strip()
            if not mid or not fact:
                continue
            mem_tokens = _extract_tokens(fact)
            overlap = len(mem_tokens & instruction_tokens)
            if overlap > 0:
                scored.append((overlap, -idx, {"memory_id": mid, "fact": fact, "kind": str(row.get("kind") or "fact")}))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored]

    @staticmethod
    def _plan_batches(
        files: Sequence[_LoadedInstructionFile],
        relevant_memories: Sequence[Mapping[str, Any]],
    ) -> list[tuple[list[_LoadedInstructionFile], list[Mapping[str, Any]]]]:
        if not files or not relevant_memories:
            return []

        # Partition instruction files by character budget
        file_chunks: list[list[_LoadedInstructionFile]] = []
        current_files: list[_LoadedInstructionFile] = []
        current_chars = 0
        for item in files:
            block_len = len(item.numbered_block())
            if current_files and current_chars + block_len > MAX_FILES_CHARS_PER_CALL:
                file_chunks.append(current_files)
                current_files = []
                current_chars = 0
            current_files.append(item)
            current_chars += block_len
        if current_files:
            file_chunks.append(current_files)

        # Partition memories by count budget
        mem_chunks = [
            list(relevant_memories[i : i + MAX_MEMORIES_PER_CALL])
            for i in range(0, len(relevant_memories), MAX_MEMORIES_PER_CALL)
        ]

        batches: list[tuple[list[_LoadedInstructionFile], list[Mapping[str, Any]]]] = []
        for f_chunk in file_chunks:
            for m_chunk in mem_chunks:
                batches.append((f_chunk, m_chunk))
                if len(batches) >= MAX_LINT_CALLS:
                    return batches
        return batches

    def _resolve_caller(self, user_id: str) -> ModelCaller | None:
        if self._model_caller is not None:
            return self._model_caller
        if self._provider is not None and self._model_id:
            return _wrap_provider_as_caller(
                self._provider,
                self._provider_name or "default",
                self._model_id,
            )
        if self._provider_resolver is not None:
            try:
                return self._provider_resolver(user_id)
            except Exception:  # noqa: BLE001
                return None
        return resolve_lightweight_model_caller(self._engine, user_id)

    @staticmethod
    def _build_user_prompt(
        files: Sequence[_LoadedInstructionFile],
        memories: Sequence[Mapping[str, Any]],
    ) -> str:
        file_sections = "\n\n".join(item.numbered_block() for item in files)
        memory_lines = "\n".join(
            f"- [{m['memory_id']}] ({m.get('kind', 'fact')}): {m['fact']}"
            for m in memories
        )
        return (
            "## Instruction Files (1-based line numbers)\n"
            f"{file_sections}\n\n"
            "## Active Memories\n"
            f"{memory_lines}\n\n"
            "Return ONLY the JSON array of direct factual contradictions."
        )

    @staticmethod
    def _validate_candidates(
        candidates: Sequence[Mapping[str, Any]],
        *,
        files_by_label: Mapping[str, _LoadedInstructionFile],
        unique_basenames: Mapping[str, _LoadedInstructionFile],
        memories_by_id: Mapping[str, str],
    ) -> list[LintContradiction]:
        verified: list[LintContradiction] = []
        seen_keys: set[tuple[str, str, int]] = set()

        for item in candidates:
            memory_id = str(item.get("memory_id") or "").strip()
            if not memory_id or memory_id not in memories_by_id:
                continue

            raw_file = str(item.get("file") or "").strip().replace("\\", "/")
            if raw_file.startswith("./"):
                raw_file = raw_file[2:]
            target_file = files_by_label.get(raw_file)
            if target_file is None:
                target_file = unique_basenames.get(raw_file.rsplit("/", 1)[-1])
            if target_file is None:
                continue

            raw_line = item.get("line")
            if isinstance(raw_line, bool) or not isinstance(raw_line, int):
                continue
            if raw_line < 1 or raw_line > len(target_file.lines):
                continue

            raw_quote = item.get("quoted_line")
            if not isinstance(raw_quote, str) or not raw_quote.strip():
                continue

            actual_line = target_file.lines[raw_line - 1]
            norm_actual = _normalize_for_quote(actual_line)
            norm_quote = _normalize_for_quote(_strip_outer_quotes(raw_quote))
            if not norm_actual or not norm_quote:
                continue
            if len(norm_quote) < min(3, len(norm_actual)):
                continue
            if norm_quote not in norm_actual:
                continue

            reason = " ".join(str(item.get("reason") or "").split())[:MAX_REASON_CHARS]
            if not reason:
                continue

            dedup_key = (memory_id, target_file.label, raw_line)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            verified.append(
                LintContradiction(
                    memory_id=memory_id,
                    memory_fact=memories_by_id[memory_id],
                    file=target_file.label,
                    line=raw_line,
                    quoted_line=actual_line.strip(),
                    reason=reason,
                )
            )
        return verified

    def run(
        self,
        user_id: str,
        workspace_root: Path | str,
        *,
        project_id: str | None = None,
        dry_run: bool = False,
        trusted: bool = False,
    ) -> LintReport:
        root = Path(workspace_root)
        loaded_files = self._load_instruction_files(root, trusted=trusted)
        files_checked = [f.label for f in loaded_files]

        active_memories = self._load_active_memories(user_id, project_id=project_id)
        relevant = self._select_relevant_memories(active_memories, loaded_files)
        batches = self._plan_batches(loaded_files, relevant)
        estimated_calls = len(batches)

        if dry_run:
            return LintReport(
                contradictions=[],
                files_checked=files_checked,
                memories_checked=len(relevant),
                estimated_calls=estimated_calls,
                dry_run=True,
                warning=None,
            )

        if estimated_calls == 0:
            return LintReport(
                contradictions=[],
                files_checked=files_checked,
                memories_checked=len(relevant),
                estimated_calls=0,
                dry_run=False,
                warning=None,
            )

        caller = self._resolve_caller(user_id)
        if caller is None:
            return LintReport(
                contradictions=[],
                files_checked=files_checked,
                memories_checked=len(relevant),
                estimated_calls=estimated_calls,
                dry_run=False,
                warning=NO_MODEL_WARNING,
            )

        files_by_label = {f.label: f for f in loaded_files}
        basename_counts: dict[str, int] = {}
        for f in loaded_files:
            base = f.label.rsplit("/", 1)[-1]
            basename_counts[base] = basename_counts.get(base, 0) + 1
        unique_basenames = {
            f.label.rsplit("/", 1)[-1]: f
            for f in loaded_files
            if basename_counts.get(f.label.rsplit("/", 1)[-1], 0) == 1
        }
        memories_by_id = {m["memory_id"]: m["fact"] for m in relevant}

        raw_candidates: list[dict[str, Any]] = []
        warning: str | None = None
        for f_chunk, m_chunk in batches:
            user_prompt = self._build_user_prompt(f_chunk, m_chunk)
            try:
                response_text = caller(LINT_SYSTEM_PROMPT, user_prompt)
            except Exception as exc:  # noqa: BLE001
                warning = f"Não foi possível concluir a verificação pelo modelo: {exc}"
                break
            raw_candidates.extend(_parse_raw_contradictions(response_text))

        contradictions = self._validate_candidates(
            raw_candidates,
            files_by_label=files_by_label,
            unique_basenames=unique_basenames,
            memories_by_id=memories_by_id,
        )
        return LintReport(
            contradictions=contradictions,
            files_checked=files_checked,
            memories_checked=len(relevant),
            estimated_calls=estimated_calls,
            dry_run=False,
            warning=warning,
        )


__all__ = [
    "INSTRUCTION_LINT_INTERVAL",
    "LINT_SYSTEM_PROMPT",
    "NO_MODEL_WARNING",
    "NO_WORKSPACE_WARNING",
    "InstructionLintState",
    "InstructionLinter",
    "LintContradiction",
    "LintReport",
    "is_instruction_lint_enabled",
    "resolve_lightweight_model_caller",
]
