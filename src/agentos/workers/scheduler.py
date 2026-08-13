"""Polling process for durable scheduled chats."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import create_engine

from agentos.bootstrap.production import ProductionSettings
from agentos.scheduler.scheduled_chats import ScheduledChatService

POLL_SECONDS = 1.0


def main() -> None:
    import time
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ProductionSettings()
    service = ScheduledChatService(create_engine(settings.DATABASE_URL, pool_pre_ping=True))
    while True:
        try:
            service.run_due(worker_id="scheduled-chat-worker", due_before=datetime.now(UTC))
        except Exception:
            logging.getLogger("agentos.workers.scheduler").exception("scheduler tick failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
