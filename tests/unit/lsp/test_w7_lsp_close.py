"""W7 Batch F (W6-R17): closing a language server ends its input, and never raises."""
from __future__ import annotations

import sys
from pathlib import Path

from agentos.lsp.client import LspClient
from agentos.lsp.config import LspServerConfig

from .test_w6_lsp_fixes import _FRAMING

# Answers initialize, ignores shutdown and exit, and leaves only when its input ends.
WAITS_FOR_EOF = _FRAMING + r'''
while True:
    message = read()  # exits with 0 at the end of input
    if message.get("method") == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
'''


def _client(tmp_path: Path) -> LspClient:
    script = tmp_path / "server.py"
    script.write_text(WAITS_FOR_EOF, encoding="utf-8")
    config = LspServerConfig(name="kit:eof", plugin_id="kit", command=sys.executable, args=(str(script),),
                             extensions=((".txt", "plaintext"),), startup_timeout=20)
    client = LspClient(config, workspace_root=tmp_path)
    client.start()
    return client


def test_closing_ends_the_servers_input_so_it_exits_on_its_own(tmp_path: Path) -> None:
    client = _client(tmp_path)
    process = client._process
    assert process is not None
    client.close()
    assert process.wait(timeout=5) == 0  # it left by itself; a killed server would not exit with 0


def test_closing_a_server_that_already_died_does_not_raise(tmp_path: Path) -> None:
    client = _client(tmp_path)
    process = client._process
    assert process is not None
    process.kill()
    process.wait(timeout=5)
    client.close()  # must not raise
    client.close()  # nor a second time
