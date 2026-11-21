"""Passive reader for OpenAI Codex CLI rollout session files (`~/.codex/sessions`)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import QuotaSnapshot
from .normalize import (
    normalize_percent,
    normalize_resets_at,
    normalize_status,
    normalize_window_kind,
)

DEFAULT_MAX_ROLLOUT_FILES = 3
DEFAULT_MAX_TAIL_BYTES = 2 * 1024 * 1024  # 2 MB per file


def _read_tail_lines(path: Path, max_tail_bytes: int) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size <= 0:
                return []
            start = max(0, size - max_tail_bytes)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return []

    if start > 0:
        # Discard the partial first line when seeking mid-file
        nl = data.find(b"\n")
        if nl == -1:
            return []
        data = data[nl + 1 :]
    return data.decode("utf-8", errors="replace").splitlines()


def _extract_rate_limits_from_file(path: Path, max_tail_bytes: int) -> list[QuotaSnapshot]:
    lines = _read_tail_lines(path, max_tail_bytes)
    for line in reversed(lines):
        if "token_count" not in line or "rate_limits" not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, Mapping) or obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, Mapping) or payload.get("type") != "token_count":
            continue
        rate_limits = payload.get("rate_limits")
        if not isinstance(rate_limits, Mapping):
            continue

        observed_at = normalize_resets_at(obj.get("timestamp"))
        if observed_at is None:
            try:
                observed_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            except OSError:
                observed_at = datetime.now(UTC)

        snapshots = _build_snapshots_from_rate_limits(rate_limits, observed_at)
        if snapshots:
            return snapshots
    return []


def _build_snapshots_from_rate_limits(
    rate_limits: Mapping[str, Any],
    observed_at: datetime,
) -> list[QuotaSnapshot]:
    snapshots: list[QuotaSnapshot] = []
    reached_type = str(rate_limits.get("rate_limit_reached_type") or "").strip().lower()

    for slot_name in ("primary", "secondary"):
        entry = rate_limits.get(slot_name)
        if not isinstance(entry, Mapping):
            continue
        used_percent = normalize_percent(entry.get("used_percent"), scale="percent")
        window_minutes = entry.get("window_minutes")
        kind = normalize_window_kind(
            slot_name if window_minutes is None else None,
            window_minutes=window_minutes if isinstance(window_minutes, (int, float)) else None,
        )
        resets_at = normalize_resets_at(entry.get("resets_at"))
        raw_status: str | None = None
        if reached_type and (reached_type == slot_name or reached_type == kind):
            raw_status = "rejected"
        status = normalize_status(raw_status, used_percent=used_percent)
        if used_percent is None and resets_at is None:
            continue

        raw_meta: dict[str, Any] = {
            "slot": slot_name,
            "window_minutes": window_minutes,
            "used_percent": used_percent,
            "resets_at": entry.get("resets_at"),
        }
        plan_type = rate_limits.get("plan_type")
        if isinstance(plan_type, str) and plan_type.strip():
            raw_meta["plan_type"] = plan_type.strip()
        limit_id = rate_limits.get("limit_id")
        if isinstance(limit_id, str) and limit_id.strip():
            raw_meta["limit_id"] = limit_id.strip()

        snapshots.append(
            QuotaSnapshot(
                provider="openai-codex",
                window_kind=kind,
                used_percent=used_percent,
                status=status,
                resets_at=resets_at,
                source="codex_rollout",
                observed_at=observed_at,
                raw=raw_meta,
            )
        )
    return snapshots


class CodexQuotaReader:
    """Reads the latest `rate_limits` entry from recent Codex rollout files."""

    def __init__(
        self,
        sessions_dir: Path | None = None,
        *,
        max_files: int = DEFAULT_MAX_ROLLOUT_FILES,
        max_tail_bytes: int = DEFAULT_MAX_TAIL_BYTES,
    ) -> None:
        self._sessions_dir = (
            Path(sessions_dir)
            if sessions_dir is not None
            else Path.home() / ".codex" / "sessions"
        )
        self._max_files = max(1, int(max_files))
        self._max_tail_bytes = max(1024, int(max_tail_bytes))

    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir

    def _recent_rollout_files(self) -> list[Path]:
        if not self._sessions_dir.exists() or not self._sessions_dir.is_dir():
            return []
        scored: list[tuple[float, Path]] = []
        try:
            for candidate in self._sessions_dir.rglob("rollout-*.jsonl"):
                try:
                    st = candidate.stat()
                except OSError:
                    continue
                if st.st_size > 0:
                    scored.append((st.st_mtime, candidate))
        except OSError:
            return []
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [path for _, path in scored[: self._max_files]]

    def read(self) -> list[QuotaSnapshot]:
        for path in self._recent_rollout_files():
            snapshots = _extract_rate_limits_from_file(path, self._max_tail_bytes)
            if snapshots:
                return snapshots
        return []


__all__ = [
    "CodexQuotaReader",
    "DEFAULT_MAX_ROLLOUT_FILES",
    "DEFAULT_MAX_TAIL_BYTES",
]
