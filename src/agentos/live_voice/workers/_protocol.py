"""JSON-lines protocol shared by the isolated voice engine workers.

A worker runs inside its own virtual environment (never Orin's), so it may
import only the standard library, numpy and its engine. One request per line
on stdin, one response per line on stdout; everything libraries print goes to
stderr, which the host keeps as the engine log.
"""
from __future__ import annotations

import base64
import json
import sys
import traceback
from collections.abc import Callable
from typing import Any

Handler = Callable[[dict[str, Any]], dict[str, Any]]


def pcm16_base64(samples: Any) -> str:
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    return base64.b64encode(pcm.tobytes()).decode("ascii")


def pcm16_from_base64(data: str) -> Any:
    import numpy as np

    raw = base64.b64decode(data or "")
    if len(raw) % 2:
        raw = raw[:-1]
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def serve(handlers: dict[str, Handler]) -> None:
    out = sys.stdout
    sys.stdout = sys.stderr
    handlers = {"ping": lambda _req: {"pong": True}, **handlers}
    out.write(json.dumps({"ready": True}) + "\n")
    out.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            handler = handlers.get(str(request.get("op") or ""))
            if handler is None:
                raise ValueError(f"unknown op {request.get('op')!r}")
            response = {"id": request_id, "ok": True, **handler(request)}
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the host
            traceback.print_exc()
            response = {"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
        out.write(json.dumps(response) + "\n")
        out.flush()
