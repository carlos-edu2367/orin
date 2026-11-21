"""Zero-LLM-cost turn change measurement for the independent verifier gate (WS-A3 / A-RF3)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Literal

from agentos.agentic.glob_match import matches_any_glob, normalize_glob_path
from agentos.settings.keys_verifier_thresholds import (
    DEFAULT_IGNORE_GLOBS,
    DEFAULT_SENSITIVE_PATHS,
    DEFAULT_THRESHOLD_FILES,
    DEFAULT_THRESHOLD_LINES,
)
from agentos.settings.loader import EffectiveSettings
from agentos.settings.registry import resolve

TriggerReason = Literal["sensitive_path", "files", "lines"]

TURN_CACHE_KEY = "_change_measure_summary"
TURN_CACHE_VERSION_KEY = "_change_measure_version"
_GIT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ChangeSummary:
    """Measured size and sensitivity of workspace edits made during one turn."""

    files: int
    lines: int
    lines_known: bool
    sensitive_hits: tuple[str, ...]
    changed_files: tuple[str, ...]
    triggered: bool
    reason: TriggerReason | None


def _resolve_workspace_root(workspace_root: Path | str | object | None, checkpoint_store: Any, toolset: Any) -> Path | None:
    candidate: Any = workspace_root
    if candidate is None and checkpoint_store is not None:
        candidate = getattr(checkpoint_store, "workspace_root", None)
    if candidate is None and toolset is not None:
        ws = getattr(toolset, "workspace", None)
        candidate = getattr(ws, "root", ws)
    if candidate is None:
        return None
    if not isinstance(candidate, (Path, str)):
        candidate = getattr(candidate, "root", candidate)
    try:
        return Path(candidate).resolve()
    except Exception:
        return None


def _normalize_workspace_rel_path(raw_path: str, root: Path | None) -> str:
    text = str(raw_path or "").strip()
    if not text:
        return ""
    if root is not None:
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                text = candidate.resolve().relative_to(root).as_posix()
            except ValueError:
                return ""
    norm = normalize_glob_path(text)
    if not norm:
        return ""
    parts = PurePosixPath(norm).parts
    if ".." in parts:
        if root is not None:
            try:
                resolved = (root / norm).resolve()
                return resolved.relative_to(root).as_posix()
            except ValueError:
                return ""
        return ""
    return PurePosixPath(*parts).as_posix()


def _extract_nested(mapping: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:
    if dotted in mapping:
        return True, mapping[dotted]
    current: Any = mapping
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _resolve_thresholds(
    settings: Any,
    *,
    files_threshold: int | None,
    lines_threshold: int | None,
    sensitive_paths: Sequence[str] | None,
    ignore_globs: Sequence[str] | None,
) -> tuple[int, int, tuple[str, ...], tuple[str, ...]]:
    eff_files = DEFAULT_THRESHOLD_FILES
    eff_lines = DEFAULT_THRESHOLD_LINES
    eff_sensitive: tuple[str, ...] = DEFAULT_SENSITIVE_PATHS
    eff_ignore: tuple[str, ...] = DEFAULT_IGNORE_GLOBS

    if isinstance(settings, EffectiveSettings):
        eff_files = int(resolve(settings, "orchestration.verifier.threshold.files").value)
        eff_lines = int(resolve(settings, "orchestration.verifier.threshold.lines").value)
        eff_sensitive = tuple(resolve(settings, "orchestration.verifier.threshold.sensitivePaths").value)
        eff_ignore = tuple(resolve(settings, "orchestration.verifier.threshold.ignore").value)
    elif isinstance(settings, Mapping):
        ok, val = _extract_nested(settings, "orchestration.verifier.threshold.files")
        if ok and isinstance(val, int) and not isinstance(val, bool) and val >= 1:
            eff_files = val
        ok, val = _extract_nested(settings, "orchestration.verifier.threshold.lines")
        if ok and isinstance(val, int) and not isinstance(val, bool) and val >= 1:
            eff_lines = val
        ok, val = _extract_nested(settings, "orchestration.verifier.threshold.sensitivePaths")
        if ok and isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
            eff_sensitive = tuple(str(item) for item in val if isinstance(item, str) and item.strip())
        ok, val = _extract_nested(settings, "orchestration.verifier.threshold.ignore")
        if ok and isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
            eff_ignore = tuple(str(item) for item in val if isinstance(item, str) and item.strip())

    if files_threshold is not None:
        eff_files = max(1, int(files_threshold))
    if lines_threshold is not None:
        eff_lines = max(1, int(lines_threshold))
    if sensitive_paths is not None:
        eff_sensitive = tuple(str(item) for item in sensitive_paths if isinstance(item, str) and item.strip())
    if ignore_globs is not None:
        eff_ignore = tuple(str(item) for item in ignore_globs if isinstance(item, str) and item.strip())

    return eff_files, eff_lines, eff_sensitive, eff_ignore


def _collect_toolset_paths(toolset: Any) -> tuple[str, ...]:
    if toolset is None:
        return ()
    getter = getattr(toolset, "changed_paths", None)
    if callable(getter):
        raw = getter()
        if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
            return tuple(str(item) for item in raw)
    fallback = getattr(toolset, "_changed_paths", None)
    if isinstance(fallback, Iterable) and not isinstance(fallback, (str, bytes)):
        return tuple(str(item) for item in fallback)
    return ()


def _collect_checkpoint_snapshots(
    checkpoint_store: Any,
    turn_id: str,
    root: Path | None,
) -> dict[str, Mapping[str, Any]]:
    if checkpoint_store is None or not turn_id:
        return {}
    getter = getattr(checkpoint_store, "get_checkpoint", None)
    if not callable(getter):
        return {}
    manifest = getter(turn_id)
    if not isinstance(manifest, Mapping):
        return {}

    snapshots: dict[str, Mapping[str, Any]] = {}
    files_by_path = manifest.get("files_by_path")
    if isinstance(files_by_path, Mapping):
        for raw_path, meta in files_by_path.items():
            if not isinstance(meta, Mapping):
                continue
            norm = _normalize_workspace_rel_path(str(raw_path or meta.get("path") or ""), root)
            if norm:
                snapshots[norm] = meta
        return snapshots

    files_list = manifest.get("files")
    if isinstance(files_list, Sequence) and not isinstance(files_list, (str, bytes)):
        for meta in files_list:
            if not isinstance(meta, Mapping):
                continue
            norm = _normalize_workspace_rel_path(str(meta.get("path") or ""), root)
            if norm:
                snapshots[norm] = meta
    return snapshots


def _is_binary_bytes(raw: bytes) -> bool:
    return b"\x00" in raw


def _count_text_lines(path: Path) -> int | None:
    if not path.exists() or not path.is_file():
        return 0
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if _is_binary_bytes(raw):
        return None
    return len(raw.decode("utf-8", errors="replace").splitlines())


def _diff_line_count(before_bytes: bytes, after_bytes: bytes) -> int | None:
    if before_bytes == after_bytes:
        return 0
    if _is_binary_bytes(before_bytes) or _is_binary_bytes(after_bytes):
        return None
    before_lines = before_bytes.decode("utf-8", errors="replace").splitlines()
    after_lines = after_bytes.decode("utf-8", errors="replace").splitlines()
    added = 0
    removed = 0
    matcher = SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            removed += i2 - i1
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "insert":
            added += j2 - j1
    return added + removed


def _diff_snapshot_file(
    root: Path | None,
    checkpoint_store: Any,
    turn_id: str,
    rel_path: str,
    meta: Mapping[str, Any],
) -> int | None:
    if root is None:
        return None
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None

    existed = bool(meta.get("existed"))
    if existed:
        turn_dir_fn = getattr(checkpoint_store, "turn_dir", None)
        if not callable(turn_dir_fn):
            return None
        turn_directory = Path(turn_dir_fn(turn_id)).resolve()
        backup_rel = str(meta.get("backup_rel_path") or f"files/{rel_path}")
        backup_file = (turn_directory / backup_rel).resolve()
        try:
            backup_file.relative_to(turn_directory)
        except ValueError:
            return None
        if not backup_file.exists() or not backup_file.is_file():
            return None
        try:
            before_bytes = backup_file.read_bytes()
        except OSError:
            return None
    else:
        before_bytes = b""

    if target.exists() and target.is_file():
        try:
            after_bytes = target.read_bytes()
        except OSError:
            return None
    else:
        after_bytes = b""

    return _diff_line_count(before_bytes, after_bytes)


def _run_git(root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _parse_numstat(stdout: str, target: dict[str, int | None]) -> None:
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        raw_added, raw_removed, raw_path = parts[0].strip(), parts[1].strip(), parts[2].strip()
        norm = normalize_glob_path(raw_path)
        if not norm:
            continue
        if raw_added == "-" or raw_removed == "-":
            target[norm] = None
            continue
        try:
            delta = int(raw_added) + int(raw_removed)
        except ValueError:
            target[norm] = None
            continue
        prev = target.get(norm)
        if prev is None and norm in target:
            continue
        target[norm] = (prev or 0) + delta


def _git_numstat_for_paths(root: Path | None, paths: Sequence[str]) -> dict[str, int | None]:
    unknown: dict[str, int | None] = {p: None for p in paths}
    if not paths or root is None or not root.exists():
        return unknown

    probe = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if probe is None or probe.returncode != 0 or probe.stdout.strip().lower() != "true":
        return unknown

    parsed: dict[str, int | None] = {}
    diff_head = _run_git(root, ["diff", "--numstat", "--relative", "HEAD", "--", *paths])
    if diff_head is not None and diff_head.returncode == 0:
        _parse_numstat(diff_head.stdout, parsed)
    else:
        # Unborn repository without HEAD: combine working tree and staged diffs.
        diff_wt = _run_git(root, ["diff", "--numstat", "--relative", "--", *paths])
        if diff_wt is not None and diff_wt.returncode == 0:
            _parse_numstat(diff_wt.stdout, parsed)
        diff_idx = _run_git(root, ["diff", "--cached", "--numstat", "--relative", "--", *paths])
        if diff_idx is not None and diff_idx.returncode == 0:
            _parse_numstat(diff_idx.stdout, parsed)

    result: dict[str, int | None] = {}
    remaining: list[str] = []
    for rel_path in paths:
        if rel_path in parsed:
            result[rel_path] = parsed[rel_path]
        else:
            remaining.append(rel_path)

    if remaining:
        untracked_proc = _run_git(root, ["ls-files", "--others", "--exclude-standard", "--", *remaining])
        untracked_set: set[str] = set()
        if untracked_proc is not None and untracked_proc.returncode == 0:
            untracked_set = {
                normalize_glob_path(line)
                for line in untracked_proc.stdout.splitlines()
                if line.strip()
            }
        still_remaining: list[str] = []
        for rel_path in remaining:
            if rel_path in untracked_set:
                result[rel_path] = _count_text_lines(root / rel_path)
            else:
                still_remaining.append(rel_path)

        if still_remaining:
            tracked_proc = _run_git(root, ["ls-files", "--", *still_remaining])
            tracked_set: set[str] = set()
            if tracked_proc is not None and tracked_proc.returncode == 0:
                tracked_set = {
                    normalize_glob_path(line)
                    for line in tracked_proc.stdout.splitlines()
                    if line.strip()
                }
            for rel_path in still_remaining:
                result[rel_path] = 0 if rel_path in tracked_set else None

    return result


def _get_cached_summary(turn: Any, version: Any) -> ChangeSummary | None:
    if isinstance(turn, MutableMapping):
        cached = turn.get(TURN_CACHE_KEY)
        cached_ver = turn.get(TURN_CACHE_VERSION_KEY)
        if isinstance(cached, ChangeSummary) and cached_ver == version:
            return cached
        return None
    if turn is not None and hasattr(turn, "__dict__"):
        cached = getattr(turn, TURN_CACHE_KEY, None)
        cached_ver = getattr(turn, TURN_CACHE_VERSION_KEY, None)
        if isinstance(cached, ChangeSummary) and cached_ver == version:
            return cached
        return None
    return None


def _set_cached_summary(turn: Any, summary: ChangeSummary, version: Any) -> None:
    if isinstance(turn, MutableMapping):
        turn[TURN_CACHE_KEY] = summary
        turn[TURN_CACHE_VERSION_KEY] = version
    elif turn is not None and hasattr(turn, "__dict__"):
        setattr(turn, TURN_CACHE_KEY, summary)
        setattr(turn, TURN_CACHE_VERSION_KEY, version)


def measure_turn_changes(
    toolset: Any,
    checkpoint_store: Any,
    workspace_root: Path | str | object | None,
    *,
    turn_id: str | None = None,
    turn: Any = None,
    settings: Any = None,
    files_threshold: int | None = None,
    lines_threshold: int | None = None,
    sensitive_paths: Sequence[str] | None = None,
    ignore_globs: Sequence[str] | None = None,
    use_cache: bool = True,
) -> ChangeSummary:
    """Measure files, changed lines, and sensitive path hits for the current turn.

    Reads the per-turn checkpoint snapshots (and falls back to ``git diff --numstat``
    for snapshot-less Bash edits inside git repositories). When ``turn`` is supplied
    and ``use_cache=True``, the computed ``ChangeSummary`` is cached on ``turn`` keyed
    by the current mutation version (toolset change events and snapshot count).
    """
    eff_checkpoint_store = checkpoint_store
    if eff_checkpoint_store is None and toolset is not None:
        eff_checkpoint_store = getattr(toolset, "_checkpoint_store", None)

    root = _resolve_workspace_root(workspace_root, eff_checkpoint_store, toolset)
    eff_turn_id = str(turn_id or getattr(toolset, "turn_id", None) or "turn-default")

    eff_files_thr, eff_lines_thr, eff_sensitive, eff_ignore = _resolve_thresholds(
        settings,
        files_threshold=files_threshold,
        lines_threshold=lines_threshold,
        sensitive_paths=sensitive_paths,
        ignore_globs=ignore_globs,
    )

    toolset_paths = _collect_toolset_paths(toolset)
    is_subagent = bool(
        turn.get("is_subagent")
        if isinstance(turn, Mapping)
        else getattr(turn, "is_subagent", False)
    )
    manifest_sig: tuple[int, int] | None = None
    manifest_fn = getattr(eff_checkpoint_store, "manifest_path", None)
    if callable(manifest_fn):
        try:
            mpath = manifest_fn(eff_turn_id)
            if isinstance(mpath, Path) and mpath.exists():
                st = mpath.stat()
                manifest_sig = (st.st_mtime_ns, st.st_size)
        except Exception:
            manifest_sig = None

    cache_version = (
        id(toolset),
        getattr(toolset, "_change_events", None),
        manifest_sig,
        is_subagent,
        eff_files_thr,
        eff_lines_thr,
        eff_sensitive,
        eff_ignore,
    )

    if use_cache and turn is not None:
        cached = _get_cached_summary(turn, cache_version)
        if cached is not None:
            return cached

    snapshots = _collect_checkpoint_snapshots(eff_checkpoint_store, eff_turn_id, root)

    if is_subagent and toolset is not None:
        candidate_set = set()
        for raw_path in toolset_paths:
            norm = _normalize_workspace_rel_path(raw_path, root)
            if norm:
                candidate_set.add(norm)
    else:
        candidate_set = set(snapshots.keys())
        for raw_path in toolset_paths:
            norm = _normalize_workspace_rel_path(raw_path, root)
            if norm:
                candidate_set.add(norm)

    counted_files = tuple(
        sorted(path for path in candidate_set if not matches_any_glob(path, eff_ignore))
    )

    total_lines = 0
    lines_known = True
    missing_snapshot_paths: list[str] = []

    for rel_path in counted_files:
        meta = snapshots.get(rel_path)
        if meta is not None:
            delta = _diff_snapshot_file(root, eff_checkpoint_store, eff_turn_id, rel_path, meta)
            if delta is None:
                lines_known = False
            else:
                total_lines += delta
        else:
            missing_snapshot_paths.append(rel_path)

    if missing_snapshot_paths:
        git_counts = _git_numstat_for_paths(root, missing_snapshot_paths)
        for rel_path in missing_snapshot_paths:
            delta = git_counts.get(rel_path)
            if delta is None:
                lines_known = False
            else:
                total_lines += delta

    sensitive_hits = (
        tuple(
            path
            for path in counted_files
            if matches_any_glob(path, eff_sensitive, case_sensitive=False)
        )
        if eff_sensitive
        else ()
    )

    reason: TriggerReason | None = None
    if sensitive_hits:
        reason = "sensitive_path"
    elif len(counted_files) >= eff_files_thr:
        reason = "files"
    elif total_lines >= eff_lines_thr:
        reason = "lines"

    summary = ChangeSummary(
        files=len(counted_files),
        lines=total_lines,
        lines_known=lines_known,
        sensitive_hits=sensitive_hits,
        changed_files=counted_files,
        triggered=reason is not None,
        reason=reason,
    )

    if turn is not None:
        _set_cached_summary(turn, summary, cache_version)

    return summary


__all__ = [
    "ChangeSummary",
    "DEFAULT_IGNORE_GLOBS",
    "DEFAULT_SENSITIVE_PATHS",
    "DEFAULT_THRESHOLD_FILES",
    "DEFAULT_THRESHOLD_LINES",
    "TURN_CACHE_KEY",
    "TriggerReason",
    "measure_turn_changes",
]
