"""Polling process for durable scheduled chats."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from agentos.bootstrap.production import ProductionSettings
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.quota import QuotaSnapshotStore
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatService

POLL_SECONDS = 1.0
HEARTBEAT_REFRESH_SECONDS = 10.0
ACTIVITY_PRUNE_BATCH_SIZE = 2000
ACTIVITY_PRUNE_MAX_BATCHES = 10
ACTIVITY_PRUNE_BACKLOG_INTERVAL_SECONDS = 60.0
ACTIVITY_PRUNE_IDLE_INTERVAL_SECONDS = 6 * 3600.0
QUOTA_SNAPSHOT_RETENTION_DAYS = 14


def _maybe_prune_activity_events(
    chat_store: PostgresChatStore,
    *,
    now_mono: float,
    next_prune_mono: float,
    batch_size: int = ACTIVITY_PRUNE_BATCH_SIZE,
    max_batches: int = ACTIVITY_PRUNE_MAX_BATCHES,
    quota_store: QuotaSnapshotStore | None = None,
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
    interval = (
        ACTIVITY_PRUNE_BACKLOG_INTERVAL_SECONDS
        if deleted >= batch_size * max_batches
        else ACTIVITY_PRUNE_IDLE_INTERVAL_SECONDS
    )
    return now_mono + interval


def main() -> None:
    import time

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ProductionSettings()
    engine = create_local_engine(settings.DATABASE_URL)
    service = ScheduledChatService(engine)
    chat_store = PostgresChatStore(engine)
    quota_store = QuotaSnapshotStore(engine)
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
            )
        except Exception:
            logging.getLogger("agentos.workers.scheduler").exception("scheduler tick failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
