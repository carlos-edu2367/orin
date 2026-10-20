"""A scripted Ollama server for offline end-to-end runs of the real Orin runtime (W6).

It speaks the part of Ollama's native API Orin uses: ``/api/tags`` and ``/api/show`` for the catalog, ``/api/chat``
(NDJSON) for turns. What it answers is chosen by a marker in the person's latest prompt:

- no marker: two text chunks, ``Hello from fake.``;
- ``[[write:<path>]]``: a ``Write`` call creating ``<path>`` with ``hi`` and, once its result is back, ``Done.``;
- ``[[slow]]``: forty words, one every 0.25 s, so a test can interrupt the turn while it streams;
- ``[[think]]``: a thinking chunk, then text;
- ``[[say:<text>]]``: exactly ``<text>`` (used for structured output).
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "fake-coder:latest"
_MARKER = re.compile(r"\[\[(write|slow|think|say)(?::([^\]]*))?\]\]")


def _done(prompt: int = 12, completion: int = 3) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
            "prompt_eval_count": prompt, "eval_count": completion}


def _text(*chunks: str) -> list[dict[str, Any]]:
    return [{"message": {"role": "assistant", "content": chunk}, "done": False} for chunk in chunks]


def _latest_prompt(messages: list[dict[str, Any]]) -> tuple[str, bool]:
    """The person's latest prompt (reminders skipped) and whether a tool result came after it."""
    index = None
    for position in range(len(messages) - 1, -1, -1):
        message = messages[position]
        content = str(message.get("content") or "")
        if message.get("role") == "user" and not content.lstrip().startswith("<system-reminder>"):
            index = position
            break
    if index is None:
        return "", False
    after = messages[index + 1:]
    return str(messages[index].get("content") or ""), any(item.get("role") == "tool" for item in after)


def script(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """``(chunks, seconds between chunks)`` for one ``/api/chat`` request."""
    prompt, answered = _latest_prompt(messages)
    found = _MARKER.search(prompt)
    kind, value = (found.group(1), found.group(2) or "") if found else ("", "")
    if kind == "write":
        if answered:
            return [*_text("Done."), _done(20, 2)], 0.0
        call = {"function": {"name": "Write", "arguments": {"file_path": value or "hello.txt", "content": "hi\n"}}}
        return [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "done": False}, _done(10, 5)], 0.0
    if kind == "slow":
        return [*_text(*(f"word{number} " for number in range(40))), _done(10, 40)], 0.25
    if kind == "think":
        return [{"message": {"role": "assistant", "content": "", "thinking": "Considering the request."}, "done": False},
                *_text("Thought it through."), _done(10, 6)], 0.0
    if kind == "say":
        return [*_text(value), _done(10, 4)], 0.0
    return [*_text("Hello ", "from fake."), _done()], 0.0


class _Handler(BaseHTTPRequestHandler):
    server: "FakeOllama"

    def log_message(self, *args: object) -> None:  # quiet
        pass

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.startswith("/api/tags"):
            return self._json({"models": [{"name": MODEL, "model": MODEL}]})
        if self.path.startswith("/api/version"):
            return self._json({"version": "0.0-fake"})
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/api/show"):
            return self._json({"capabilities": ["completion", "tools"], "model_info": {"llama.context_length": 32768}})
        if not self.path.startswith("/api/chat"):
            return self._json({"error": "not found"}, 404)
        with self.server.lock:
            self.server.requests.append(body)
        chunks, pause = script(list(body.get("messages") or []))
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write((json.dumps({"model": MODEL, **chunk}) + "\n").encode("utf-8"))
                self.wfile.flush()
                if pause:
                    time.sleep(pause)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the turn was interrupted: the runtime closed the stream


class FakeOllama(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> "FakeOllama":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


__all__ = ["MODEL", "FakeOllama", "script"]
