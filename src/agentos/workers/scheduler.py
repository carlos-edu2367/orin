"""Polling process for durable scheduled chats."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine

from agentos.bootstrap.production import ProductionSettings
from agentos.conversations.chat import PostgresChatStore
from agentos.memory.instruction_lint import (
    INSTRUCTION_LINT_INTERVAL,
    InstructionLinter,
    InstructionLintState,
    LintReport,
    is_instruction_lint_enabled,
)
from agentos.memory.promotion import PROPOSAL_EXPIRATION_DAYS, MemoryPromotionGate
from agentos.persistence.postgres.instruction_lint_runtime import build_instruction_linter
from agentos.persistence.postgres.memory_lifecycle import MemoryLifecycle
from agentos.persistence.postgres.quota import QuotaSnapshotStore
from agentos.persistence.postgres.schema import projects, workspace_roots
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatService
from agentos.settings.loader import load_effective_settings
from agentos.settings.registry import resolve as resolve_setting

POLL_SECONDS = 1.0
HEARTBEAT_REFRESH_SECONDS = 10.0
ACTIVITY_PRUNE_BATCH_SIZE = 2000
ACTIVITY_PRUNE_MAX_BATCHES = 10
ACTIVITY_PRUNE_BACKLOG_INTERVAL_SECONDS = 60.0
ACTIVITY_PRUNE_IDLE_INTERVAL_SECONDS = 6 * 3600.0
QUOTA_SNAPSHOT_RETENTION_DAYS = 14
DEFAULT_TOMBSTONE_RETENTION_DAYS = 90


def _resolve_tombstone_retention_days(override: int | None = None) -> int:
    if override is not None:
        return max(1, int(override))
    try:
        effective = load_effective_settings()
        resolved = resolve_setting(effective, "memory.tombstoneRetentionDays")
        return max(1, int(resolved.value))
    except Exception:
        return DEFAULT_TOMBSTONE_RETENTION_DAYS


def _maybe_prune_activity_events(
    chat_store: PostgresChatStore,
    *,
    now_mono: float,
    next_prune_mono: float,
    batch_size: int = ACTIVITY_PRUNE_BATCH_SIZE,
    max_batches: int = ACTIVITY_PRUNE_MAX_BATCHES,
    quota_store: QuotaSnapshotStore | None = None,
    memory_lifecycle: MemoryLifecycle | None = None,
    tombstone_retention_days: int | None = None,
    promotion_gate: MemoryPromotionGate | None = None,
) -> float:
    if now_mono < next_prune_mono:
        return next_prune_mono
    deleted = chat_store.prune_transient_activity_events(
        batch_size=batch_size,
        max_batches=max_batches,
    )
    if quota_store is not None:
        try:
            quota_store.prune(datetime.now(UTC) - timedelta(days=QUOTA_SNAPSHOT_RETENTION_DAYS))
        except Exception:
            logging.getLogger("agentos.workers.scheduler").exception("quota snapshot prune failed")
    if memory_lifecycle is not None:
        try:
            retention_days = _resolve_tombstone_retention_days(tombstone_retention_days)
            memory_lifecycle.purge_tombstones(datetime.now(UTC) - timedelta(days=retention_days))
        except Exception:
            logging.getLogger("agentos.workers.scheduler").exception("memory tombstone purge failed")
    if promotion_gate is not None:
        try:
            promotion_gate.expire_proposals(datetime.now(UTC) - timedelta(days=PROPOSAL_EXPIRATION_DAYS))
        except Exception:
            logging.getLogger("agentos.workers.scheduler").exception("memory proposal expiration failed")
    interval = (
        ACTIVITY_PRUNE_BACKLOG_INTERVAL_SECONDS
        if deleted >= batch_size * max_batches
        else ACTIVITY_PRUNE_IDLE_INTERVAL_SECONDS
    )
    return now_mono + interval


def _discover_lint_targets(engine: Engine) -> list[tuple[str, Path, str | None]]:
    """Return active `(user_id, workspace_root, project_id)` pairs from bound project workspaces."""
    targets: list[tuple[str, Path, str | None]] = []
    seen: set[tuple[str, str]] = set()
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    projects.c.user_id,
                    projects.c.project_id,
                    workspace_roots.c.root_path,
                )
                .join(
                    workspace_roots,
                    (workspace_roots.c.workspace_id == projects.c.workspace_id)
                    & (workspace_roots.c.user_id == projects.c.user_id),
                )
                .where(projects.c.archived_at.is_(None))
            ).mappings().all()
    except Exception:  # noqa: BLE001
        return targets

    for row in rows:
        uid = str(row.get("user_id") or "").strip()
        pid = str(row.get("project_id") or "").strip() or None
        raw_root = str(row.get("root_path") or "").strip()
        if not uid or not raw_root:
            continue
        root_path = Path(raw_root)
        if not root_path.is_dir():
            continue
        key = (uid, str(root_path))
        if key in seen:
            continue
        seen.add(key)
        targets.append((uid, root_path, pid))
    return targets


def _maybe_run_instruction_lint(
    engine: Engine,
    *,
    targets: Sequence[tuple[str, Path, str | None]] | None = None,
    now: datetime | None = None,
    enabled_override: bool | None = None,
    state: InstructionLintState | None = None,
    linter: InstructionLinter | None = None,
) -> list[LintReport]:
    """Optional periodic instruction lint: runs only when `memory.instructionLint.enabled` is True and at most 1x/24h."""
    enabled = (
        bool(enabled_override)
        if enabled_override is not None
        else is_instruction_lint_enabled()
    )
    if not enabled:
        return []

    current = now or datetime.now(UTC)
    lint_state = state or InstructionLintState()
    resolved_targets = list(targets) if targets is not None else _discover_lint_targets(engine)
    if not resolved_targets:
        return []

    runner = linter or build_instruction_linter(engine)
    reports: list[LintReport] = []
    logger = logging.getLogger("agentos.workers.scheduler")
    for user_id, workspace_root, project_id in resolved_targets:
        scope_key = f"{user_id}:{Path(workspace_root).as_posix()}"
        if not lint_state.claim_run(scope_key, current, interval=INSTRUCTION_LINT_INTERVAL):
            continue
        try:
            report = runner.run(
                user_id,
                workspace_root,
                project_id=project_id,
                dry_run=False,
            )
            reports.append(report)
            if report.contradictions:
                logger.info(
                    "instruction lint found %d contradiction(s) for user=%s workspace=%s",
                    len(report.contradictions),
                    user_id,
                    workspace_root,
                )
        except Exception:
            logger.exception(
                "periodic instruction lint failed for user=%s workspace=%s",
                user_id,
                workspace_root,
            )
    return reports


def main() -> None:
    import time

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ProductionSettings()
    engine = create_local_engine(settings.DATABASE_URL)
    service = ScheduledChatService(engine)
    chat_store = PostgresChatStore(engine)
    quota_store = QuotaSnapshotStore(engine)
    memory_lifecycle = MemoryLifecycle(engine)
    promotion_gate = MemoryPromotionGate(engine)
    lint_state = InstructionLintState()
    next_prune_mono = 0.0
    last_heartbeat_mono: float | None = None
    while True:
        try:
            now_mono = time.monotonic()
            if last_heartbeat_mono is None or now_mono - last_heartbeat_mono >= HEARTBEAT_REFRESH_SECONDS:
                service.heartbeat("scheduled-chat-worker")
                last_heartbeat_mono = now_mono
            service.run_due(worker_id="scheduled-chat-worker", due_before=datetime.now(UTC))
            next_prune_mono = _maybe_prune_activity_events(
                chat_store,
                now_mono=time.monotonic(),
                next_prune_mono=next_prune_mono,
                quota_store=quota_store,
                memory_lifecycle=memory_lifecycle,
                promotion_gate=promotion_gate,
            )
            _maybe_run_instruction_lint(engine, state=lint_state)
        except Exception:
            logging.getLogger("agentos.workers.scheduler").exception("scheduler tick failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
