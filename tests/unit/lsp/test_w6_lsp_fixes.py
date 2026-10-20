"""W6 (W5-D3): a late publish answers nothing, a malformed one ends the wait, and stderr's tail reaches the notice."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from agentos.lsp.client import LspClient
from agentos.lsp.config import LspServerConfig
from agentos.lsp.manager import LspManager

_FRAMING = r'''
import json, sys

def read():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        line = line.decode().strip()
        if not line:
            break
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1])
    return json.loads(sys.stdin.buffer.read(length))

def send(message):
    body = json.dumps(message).encode()
    sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    sys.stdout.buffer.flush()
'''

# On every save it publishes for version 1 only: from the second write on, that answer is late.
STALE = _FRAMING + r'''
while True:
    message = read()
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif method == "textDocument/didSave":
        uri = message["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": uri, "version": 1,
              "diagnostics": [{"range": {"start": {"line": 0, "character": 0}}, "severity": 1, "message": "computed for version 1"}]}})
    elif method == "exit":
        sys.exit(0)
'''

MALFORMED_ONLY = _FRAMING + r'''
while True:
    message = read()
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif method == "textDocument/didSave":
        uri = message["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": "oops"}})
    elif method == "exit":
        sys.exit(0)
'''

CRASHING = r'''
import sys
sys.stdin.buffer.readline()
sys.stderr.write("fatal: the toolchain for this project is missing\n")
sys.stderr.flush()
sys.exit(3)
'''


def _client(tmp_path: Path, source: str, name: str) -> LspClient:
    script = tmp_path / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    config = LspServerConfig(name=f"kit:{name}", plugin_id="kit", command=sys.executable, args=(str(script),),
                             extensions=((".txt", "plaintext"),), startup_timeout=20)
    client = LspClient(config, workspace_root=tmp_path)
    client.start()
    return client


def test_a_publish_for_an_older_version_answers_nothing(tmp_path: Path) -> None:
    client = _client(tmp_path, STALE, "stale")
    try:
        target = tmp_path / "a.txt"
        first = client.diagnostics(target, "x\n", "plaintext", timeout=10)
        assert first is not None and first[0]["message"] == "computed for version 1"
        assert client.diagnostics(target, "y\n", "plaintext", timeout=1) is None
    finally:
        client.close()


def test_a_malformed_publish_ends_the_wait_at_once(tmp_path: Path) -> None:
    client = _client(tmp_path, MALFORMED_ONLY, "malformed")
    try:
        started = time.monotonic()
        assert client.diagnostics(tmp_path / "a.txt", "x\n", "plaintext", timeout=5) is None
        assert time.monotonic() - started < 3
    finally:
        client.close()


def test_the_failure_notice_carries_the_servers_last_error_output(tmp_path: Path) -> None:
    script = tmp_path / "crash.py"
    script.write_text(CRASHING, encoding="utf-8")
    config = LspServerConfig(name="kit:crash", plugin_id="kit", command=sys.executable, args=(str(script),),
                             extensions=((".txt", "plaintext"),), startup_timeout=20)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    name, found, failure = LspManager([config], workspace_root=tmp_path).check(tmp_path / "a.txt")
    assert name == "kit:crash" and found is None and "toolchain for this project is missing" in failure


def test_one_malformed_diagnostic_does_not_hide_the_others() -> None:
    from agentos.lsp.manager import format_diagnostics

    text = format_diagnostics("a.py", "kit:x", [
        {"range": "nonsense", "severity": 1, "message": "bad range"},
        {"range": {"start": {"line": "two"}}, "severity": 1, "message": "bad line"},
        {"range": {"start": {"line": 1, "character": 0}}, "severity": 1, "message": "kept"},
    ])
    assert text is not None and "kept" in text and "bad" not in text
