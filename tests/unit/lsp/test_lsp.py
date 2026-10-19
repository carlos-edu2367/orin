"""W5: a plugin's language server reports diagnostics after Write and Edit."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from agentos.lsp.client import LspClient
from agentos.lsp.config import LspServerConfig, lsp_servers
from agentos.lsp.manager import LspManager, format_diagnostics
from agentos.plugins.runtime import active_plugins

# A tiny language server: every line containing TODO is a warning, every line containing FIXME an error. On the
# first document it asks the client for its configuration and reports the answer as a hint on line 1.
FAKE_SERVER = r'''
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

asked = False
while True:
    message = read()
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {"textDocumentSync": 1}}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "exit":
        sys.exit(0)
    elif method in ("textDocument/didOpen", "textDocument/didChange"):
        document = message["params"]["textDocument"]
        text = document.get("text") if method == "textDocument/didOpen" else message["params"]["contentChanges"][0]["text"]
        if not asked:
            asked = True
            send({"jsonrpc": "2.0", "id": "cfg-1", "method": "workspace/configuration", "params": {"items": [{"section": "fake"}]}})
            reply = read()
        diagnostics = []
        for number, line in enumerate(text.splitlines()):
            if "TODO" in line:
                diagnostics.append({"range": {"start": {"line": number, "character": line.index("TODO")}, "end": {"line": number, "character": 0}}, "severity": 2, "message": "a TODO is left"})
            if "FIXME" in line:
                diagnostics.append({"range": {"start": {"line": number, "character": 0}, "end": {"line": number, "character": 0}}, "severity": 1, "message": "a FIXME is left", "code": "F1"})
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": document["uri"], "diagnostics": diagnostics}})
'''


def _config(tmp_path: Path) -> LspServerConfig:
    script = tmp_path / "fake_lsp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return LspServerConfig(name="kit:fake", plugin_id="kit", command=sys.executable, args=(str(script),),
                           extensions=((".txt", "plaintext"),), settings={"fake": True}, startup_timeout=20)


def test_the_client_sends_a_change_and_returns_its_diagnostics(tmp_path: Path) -> None:
    client = LspClient(_config(tmp_path), workspace_root=tmp_path)
    client.start()
    try:
        target = tmp_path / "notes.txt"
        found = client.diagnostics(target, "fine\nTODO: later\n", "plaintext", timeout=20)
        assert [(item["severity"], item["range"]["start"]["line"]) for item in found] == [(2, 1)]
        found = client.diagnostics(target, "FIXME now\n", "plaintext", timeout=20)
        assert [item["message"] for item in found] == ["a FIXME is left"]
    finally:
        client.close()


# A server that sends one malformed publish (its diagnostics are a number, not a list) before a good one.
MALFORMED_SERVER = r'''
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

while True:
    message = read()
    if message.get("method") == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif message.get("method") == "textDocument/didSave":
        uri = message["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": 5}})
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": [
            {"range": {"start": {"line": 0, "character": 0}}, "severity": 1, "message": "still read"}]}})
    elif message.get("method") == "exit":
        sys.exit(0)
'''


def test_a_malformed_message_does_not_stop_the_reader(tmp_path: Path) -> None:
    script = tmp_path / "malformed_lsp.py"
    script.write_text(MALFORMED_SERVER, encoding="utf-8")
    config = LspServerConfig(name="kit:bad", plugin_id="kit", command=sys.executable, args=(str(script),),
                             extensions=((".txt", "plaintext"),), startup_timeout=20)
    client = LspClient(config, workspace_root=tmp_path)
    client.start()
    try:
        found = client.diagnostics(tmp_path / "notes.txt", "x\n", "plaintext", timeout=5)
        assert found is not None and [item["message"] for item in found] == ["still read"]
    finally:
        client.close()


def test_diagnostics_become_reminder_text() -> None:
    text = format_diagnostics("src/a.py", "kit:pyright", [
        {"range": {"start": {"line": 2, "character": 4}}, "severity": 1, "message": "Undefined  name 'x'", "code": "reportUndefinedVariable"},
        {"range": {"start": {"line": 0, "character": 0}}, "severity": 4, "message": "a hint"},
    ])
    assert text == ("Diagnostics from the language server kit:pyright for src/a.py after your change:\n"
                    "- line 3, column 5 · error · Undefined name 'x' [reportUndefinedVariable]")
    assert format_diagnostics("a", "s", [{"severity": 3, "message": "info"}]) is None


def test_a_server_that_cannot_start_is_turned_off(tmp_path: Path) -> None:
    broken = LspServerConfig(name="kit:none", plugin_id="kit", command=str(tmp_path / "no-such-server"), extensions=((".txt", "plaintext"),))
    manager = LspManager([broken], workspace_root=tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    name, found, failure = manager.check(tmp_path / "a.txt")
    assert name == "kit:none" and found is None and "could not start" in failure
    assert manager.check(tmp_path / "a.txt") == ("kit:none", None, "")  # reported once, then off
    assert manager.check(tmp_path / "b.md") == ("", None, "")


class _Plugins:
    def __init__(self, rows):
        self.rows = rows

    def list(self, user_id):
        return list(self.rows)


def _plugin(root: Path, lsp: dict) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "kit", "version": "1.0.0"}), encoding="utf-8")
    (root / ".lsp.json").write_text(json.dumps(lsp), encoding="utf-8")
    return root


def _row(root: Path, consented: bool) -> dict:
    hooks = [{"kind": "hook", "reference": "kit:PreToolUse:0", "enabled": True}] if consented else []
    return {"plugin_id": "kit", "state": "active", "install_path": str(root), "contributions": hooks}


def test_a_plugins_lsp_json_is_read_once_its_code_may_run(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "kit", {
        "fake": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/fake", "args": ["--stdio"], "extensionToLanguage": {".TXT": "plaintext"}, "startupTimeout": 3000},
        "broken": {"command": "x", "extensionToLanguage": {"txt": "plaintext"}},
    })
    servers, problems = lsp_servers(active_plugins(_Plugins([_row(root, True)]), "u1", {})[0], user_id="u1")
    assert [(item.name, item.extensions, item.startup_timeout) for item in servers] == [("kit:fake", ((".txt", "plaintext"),), 3.0)]
    assert servers[0].command == f"{root}/bin/fake" and any("broken" in problem for problem in problems)
    waiting, problems = lsp_servers(active_plugins(_Plugins([_row(root, False)]), "u1", {})[0], user_id="u1")
    assert waiting == [] and any("allow" in problem for problem in problems)


def test_writing_a_file_brings_its_diagnostics_to_the_model(tmp_path: Path) -> None:
    from tests.unit.agentic.test_disk_skills_session import _session

    script = tmp_path / "fake_lsp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    root = _plugin(tmp_path / "kit", {"fake": {"command": sys.executable, "args": [str(script)], "extensionToLanguage": {".txt": "plaintext"}}})
    session = _session(tmp_path, "hi")
    session.plugin_service = _Plugins([_row(root, True)])
    session.plugin_hooks = True
    runtime = session.build_runtime()
    try:
        assert runtime.toolset.invoke("write_file", {"path": "notes.txt", "content": "ok\nFIXME soon\n"}).status == "succeeded"
        deadline = time.monotonic() + 30
        texts: list[str] = []
        while time.monotonic() < deadline and not texts:
            texts = [item.text for item in session._reminders.drain() if item.kind == "diagnostics"]
            time.sleep(0.1)
        assert texts and "notes.txt" in texts[0] and "line 2, column 1 · error · a FIXME is left [F1]" in texts[0]
    finally:
        session.close_background_subagents()
