"""The service side of the multi-call binary.

``orin internal-service backend`` and friends are how the supervisor starts the
runtime. They are hidden verbs, not public commands: the contract is between the
launcher and itself, which is exactly what lets the same code be a console script
today and a frozen ``orin.exe`` later without the supervisor changing at all.

Each function runs one service **in this process** and does not return until it
stops. Configuration arrives entirely through the inherited environment.
"""

from __future__ import annotations

import os
import sys

from .ports import DEFAULT_PORT

SERVICES = ("backend", "publisher", "worker")


def run_backend() -> int:
    """HTTP, SSE, and the built web interface. Never calls a provider."""
    import uvicorn

    host = os.getenv("ORIN_BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("ORIN_BACKEND_PORT", str(DEFAULT_PORT)))
    uvicorn.run(
        "agentos.api.asgi:app",
        host=host,
        port=port,
        # The local profile authenticates the loopback peer itself. Trusting
        # forwarded headers here would let a proxy claim any address it liked.
        proxy_headers=False,
        forwarded_allow_ips=None,
        log_level=os.getenv("ORIN_LOG_LEVEL", "info"),
        access_log=os.getenv("ORIN_ACCESS_LOG", "").strip().lower() in {"1", "true", "yes"},
    )
    return 0


def run_publisher() -> int:
    """Moves durable pending turns onto the queue, and sweeps lost ones."""
    import asyncio

    from agentos.workers.publisher import main

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        return 0
    return 0


def run_worker() -> int:
    """Claims a turn and runs the agent loop."""
    from arq import run_worker

    from agentos.workers.chat import WorkerSettings

    try:
        run_worker(WorkerSettings)  # type: ignore[arg-type]
    except KeyboardInterrupt:
        return 0
    return 0


_RUNNERS = {"backend": run_backend, "publisher": run_publisher, "worker": run_worker}


def run_service(name: str) -> int:
    runner = _RUNNERS.get(name)
    if runner is None:
        sys.stderr.write(f"unknown service '{name}'; expected one of {', '.join(SERVICES)}\n")
        return 2
    return runner()


__all__ = ["SERVICES", "run_service"]
