"""Questions a turn asks the SDK host behind ``orin code`` (W6): SDK hook callbacks and SDK MCP messages.

The worker cannot reach the host directly: the CLI between them follows the conversation's events. So a question
is a pending file (owner-only, in ``<ORIN_DATA_DIR>/cli-host-requests/<conversation>``), its body goes to the turn's
CLI transcript (activity payloads are sanitized), and a ``host.request`` activity event tells the CLI it exists. The
CLI asks the host over the control protocol and posts the answer back through the API, which writes the reply file
the worker is waiting for. A question nobody answers in time, or one the turn is cancelled during, gets ``None``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cli_transcript import TranscriptStore

MAX_REPLY_BYTES = 262_144
_ID = re.compile(r"^host_[0-9a-f]{32}$")


def host_requests_root() -> Path:
    from agentos.installation.paths import orin_paths

    return orin_paths().data / "cli-host-requests"


class HostRequests:
    def __init__(self, conversation_id: str, *, emit: Callable[[dict[str, Any]], None] | None = None,
                 cancelled: Callable[[], bool] | None = None, root: Path | None = None,
                 transcript: TranscriptStore | None = None, poll_seconds: float = 0.1,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        if not conversation_id:
            raise ValueError("a host request needs its conversation")
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]
        self.folder = (root or host_requests_root()) / digest
        self._emit = emit
        self._cancelled = cancelled or (lambda: False)
        self._transcript = transcript
        self._poll = max(0.01, float(poll_seconds))
        self._clock = clock
        self._sleep = sleep

    def _pending(self, request_id: str) -> Path:
        return self.folder / f"{request_id}.json"

    def _reply(self, request_id: str) -> Path:
        return self.folder / f"{request_id}.reply.json"

    def ask(self, kind: str, request: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
        """Ask the host and wait for its answer; None on timeout, cancellation or when the question cannot be asked."""
        if self._emit is None or self._transcript is None:
            return None
        request_id = f"host_{uuid.uuid4().hex}"
        entry = self._transcript.write({"host_request": {"kind": kind, "request": request}})
        if entry is None:
            return None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self._pending(request_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"kind": kind}))
        except OSError:
            return None
        try:
            self._emit({"request_id": request_id, "kind": kind, "transcript_id": entry})
            deadline = self._clock() + max(0.1, float(timeout))
            while self._clock() < deadline:
                if self._cancelled():
                    return None
                answer = self._take(request_id)
                if answer is not None:
                    return answer
                self._sleep(self._poll)
            return self._take(request_id)
        finally:
            for path in (self._pending(request_id), self._reply(request_id)):
                try:
                    path.unlink()
                except OSError:
                    pass

    def _take(self, request_id: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._reply(request_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def answer(self, request_id: str, response: dict[str, Any]) -> bool:
        """The CLI's answer (through the API): written only for a question still waiting."""
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id) or not isinstance(response, dict):
            return False
        if not self._pending(request_id).is_file():
            return False
        data = json.dumps(response, ensure_ascii=False)
        if len(data.encode("utf-8")) > MAX_REPLY_BYTES:
            return False
        temporary = self.folder / f".{request_id}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(data)
            os.replace(temporary, self._reply(request_id))
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
            return False
        return True


__all__ = ["HostRequests", "MAX_REPLY_BYTES", "host_requests_root"]
