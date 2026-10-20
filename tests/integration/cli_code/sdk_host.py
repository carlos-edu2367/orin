"""A minimal SDK host for the live tests: drives `orin-code` over stream-json the way claude-agent-sdk does."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .live_runtime import LiveRuntime


class Host:
    def __init__(self, runtime: LiveRuntime, cwd: Path, *args: str,
                 on_request: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None) -> None:
        executable = Path(sys.executable).parent / ("orin-code.exe" if os.name == "nt" else "orin-code")
        self.process = subprocess.Popen(
            [str(executable), "--output-format", "stream-json", "--verbose", "--input-format", "stream-json", *args],
            cwd=str(cwd), env=runtime.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.messages: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._on_request = on_request
        self._counter = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            if not line.strip():
                continue
            message = json.loads(line)
            if message.get("type") == "control_request":
                self.requests.append(message)
                answer = self._on_request(message["request"]) if self._on_request else None
                response = ({"subtype": "success", "request_id": message["request_id"], "response": answer}
                            if answer is not None else
                            {"subtype": "error", "request_id": message["request_id"], "error": "no handler"})
                self.send({"type": "control_response", "response": response})
                continue
            self.messages.append(message)
            self._inbox.put(message)
        self._inbox.put(None)

    def send(self, message: dict[str, Any]) -> None:
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def control(self, request: dict[str, Any]) -> str:
        self._counter += 1
        request_id = f"req_{self._counter}"
        self.send({"type": "control_request", "request_id": request_id, "request": request})
        return request_id

    def prompt(self, text: str, **extra: Any) -> None:
        self.send({"type": "user", "session_id": "", "message": {"role": "user", "content": text}, "parent_tool_use_id": None, **extra})

    def wait_for(self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 120) -> dict[str, Any]:
        for message in self.messages:
            if predicate(message):
                return message
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self._inbox.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if message is None:
                raise AssertionError(f"orin-code exited: {self.process.stderr.read()[-2000:]}")
            if predicate(message):
                return message
        raise AssertionError(f"timed out; got {[item.get('type') for item in self.messages]}")

    def response_to(self, request_id: str) -> dict[str, Any]:
        found = self.wait_for(lambda m: m.get("type") == "control_response" and m["response"].get("request_id") == request_id)
        return found["response"]

    def results(self) -> list[dict[str, Any]]:
        return [item for item in self.messages if item.get("type") == "result"]

    def close(self, timeout: float = 120) -> int:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        return self.process.wait(timeout=timeout)


__all__ = ["Host"]
