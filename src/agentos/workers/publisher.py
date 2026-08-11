"""Durable-to-ARQ publisher and recovery loop for chat dispatches.

Run this process beside the ARQ worker. It can be restarted safely because
``conversation_dispatches`` remains the source of truth and jobs contain only
the turn id.

It also runs the recovery sweep. Without it, a turn whose worker died would sit
in ``running`` forever and the UI would show a conversation that never finishes —
the single most confusing failure a local install can have.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from arq.connections import RedisSettings, create_pool
from sqlalchemy import create_engine

from agentos.bootstrap.production import ProductionSettings
from agentos.conversations.chat import PostgresChatStore


_LOGGER = logging.getLogger("agentos.workers.publisher")

POLL_SECONDS = 0.5
# How long a claimed turn may go without finishing before it is considered lost.
# Generous enough for a long tool chain, short enough that a crash is noticed.
STALE_AFTER = timedelta(seconds=600)
# How long an unclaimed turn may wait before it is failed as "no worker".
UNCLAIMED_AFTER = timedelta(seconds=45)
SWEEP_EVERY_SECONDS = 15.0


async def publish_once(store: PostgresChatStore, pool) -> int:
    store.heartbeat("chat-publisher")
    count = 0
    for turn_id in store.pending():
        await pool.enqueue_job("agentos_agent", turn_id, _job_id=f"chat:{turn_id}")
        if store.mark_enqueued(turn_id): count += 1
    return count


def recover_once(store: PostgresChatStore) -> tuple[str, ...]:
    """Requeue turns whose worker vanished and fail the ones nobody claimed."""
    from agentos.workers.chat import ChatWorker

    recovered = ChatWorker(store).watchdog(maximum_age=UNCLAIMED_AFTER)
    stale = store.recover_stale(maximum_age=STALE_AFTER)
    if recovered or stale:
        _LOGGER.info("recovery swept %d unclaimed and %d stale turns", len(recovered), len(stale))
    return tuple({*recovered, *stale})


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ProductionSettings()
    store = PostgresChatStore(create_engine(settings.DATABASE_URL, pool_pre_ping=True))
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    loop = asyncio.get_running_loop()
    next_sweep = loop.time()
    try:
        while True:
            try:
                await publish_once(store, pool)
                if loop.time() >= next_sweep:
                    next_sweep = loop.time() + SWEEP_EVERY_SECONDS
                    # The sweep touches several rows; keep it off the event loop.
                    await asyncio.to_thread(recover_once, store)
            except Exception:
                # A transient database or Redis error must not end the loop; the
                # next tick retries, and the durable tables lost nothing.
                _LOGGER.exception("publisher tick failed")
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
