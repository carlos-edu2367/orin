"""Red team round 5: a limit breach closes the real claude -p process from inside the dispatch loop."""
from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.provider import CLAUDE_CODE_LOCAL_BASE_URL, CliSlots, ClaudeCliProvider
from tests.unit.agentic.test_native_turn import FakeStore, _make_toolset

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process checks")


_SCRIPT = r'''
import json, sys, time, os, httpx
from pathlib import Path

def emit(p):
    sys.stdout.write(json.dumps(p) + "\n"); sys.stdout.flush()

if "--version" in sys.argv:
    print("2.1.309 (Claude Code)"); sys.exit(0)
argv = sys.argv[1:]
cfg = argv[argv.index("--mcp-config") + 1]
srv = json.loads(Path(cfg).read_text())["mcpServers"]
name, conf = next(iter(srv.items()))
url, headers = conf["url"], conf.get("headers") or {}
sys.stdin.readline()
httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, headers=headers, timeout=10)
emit({"type": "system", "subtype": "init", "session_id": "s", "model": "claude-opus-5-5", "apiKeySource": "none",
      "mcp_servers": [{"name": name, "status": "connected"}], "tools": []})
emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1", "usage": {"input_tokens": 900000, "output_tokens": 1}}}})
emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}})
emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ANTES "}}})
emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}})
emit({"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 500}}})
# Still alive after the breach: a second paid model call, then hang.
emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m2", "usage": {"input_tokens": 900500, "output_tokens": 1}}}})
time.sleep(8)
Path(os.environ["BREACH_MARKER"]).write_text("survived")
'''


def test_breach_kills_the_real_process_releases_the_slot_and_ends_with_the_limit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "fake_breach_cli.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    marker = tmp_path / "survived.txt"
    monkeypatch.setenv("BREACH_MARKER", str(marker))
    slots = CliSlots(1)
    provider = ClaudeCliProvider(base_url=CLAUDE_CODE_LOCAL_BASE_URL, model="claude-opus-5-5", binary=str(script), slots=slots)
    store = FakeStore()
    rt = AgenticTurnRuntime(
        store=store, provider=provider, toolset=_make_toolset(ConversationWorkspace(tmp_path / "ws", "c1")),
        limits=AgenticLimits(max_iterations=10, max_actions=10, max_provider_tokens=500_000, context_window_tokens=1_000_000),
    )
    t0 = time.monotonic()
    try:
        out = rt.run("t1", turn=store.turn)
    finally:
        provider.close()
    elapsed = time.monotonic() - t0
    assert out.state == "failed" and out.error_code == "PROVIDER_TOKEN_LIMIT", out
    assert elapsed < 6.0, f"turn took {elapsed:.1f}s after the breach"
    assert slots.acquire(cancel_check=lambda: False) is True, "CLI slot not released"
    slots.release()
    time.sleep(8.5 - min(elapsed, 8.0))
    assert not marker.exists(), "claude -p process kept running after the breach"
    # m2 had already started (message_start) when Orin read the breach: it is billed from what is known, once.
    assert [u["input_tokens"] for u in store.usages] == [900000, 900500], store.usages
