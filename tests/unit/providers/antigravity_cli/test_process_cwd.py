"""Every agy process runs in a folder of its own (a shared one crossed tool calls between turns)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence

import pytest

from agentos.providers.antigravity_cli import AntigravityCliAdapter
from agentos.providers.claude_cli import command as claude_command
from agentos.providers.claude_cli.command import (
    PROCESS_CWD_RE,
    create_process_cwd,
    remove_process_cwd,
    sweep_orphan_process_cwds,
)
from agentos.providers.claude_cli.errors import NativeLoopUnavailable
from agentos.providers.claude_cli.native_loop import (
    NativeTurnRequest,
    start_native_turn_for_provider,
)
from agentos.providers.claude_cli.provider import ClaudeCliProvider
from agentos.providers.cli_tools_mcp import McpToolSpec, ToolCallRequest, ToolCallResult

FAKE_ANTIGRAVITY_SCRIPT = str(Path(__file__).with_name("fake_antigravity.py"))


class _Sink:
    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: None


class _TaggedExecutor:
    """Answers with its own tag, so a call answered by another turn's server is visible."""

    def __init__(self, tag: str, log: list[tuple[str, str]], lock: threading.Lock) -> None:
        self.tag = tag
        self._log = log
        self._lock = lock

    def list_tools(self) -> Sequence[McpToolSpec]:
        return [
            McpToolSpec(
                name="echo",
                description="Echo text back",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                read_only=True,
            )
        ]

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        with self._lock:
            self._log.append((self.tag, str(request.arguments.get("text"))))
        return ToolCallResult(text=f"SERVER_{self.tag}")


def _request(tag: str, executor: Any, cwd: Path, text: str = "Hello") -> NativeTurnRequest:
    return NativeTurnRequest(
        model="gemini-3.8-flash-low",
        system_prompt=f"You are Orin {tag}.",
        user_content=[{"type": "text", "text": text}],
        executor=executor,
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 60.0,
        max_turns=5,
        cwd=cwd,
    )


def _run_dirs(base: Path) -> list[Path]:
    return [p for p in base.iterdir() if PROCESS_CWD_RE.match(p.name)] if base.exists() else []


def test_concurrent_agy_processes_get_distinct_folders_and_their_own_tool_server(tmp_path: Path) -> None:
    base = tmp_path / "workdir"
    base.mkdir()
    log: list[tuple[str, str]] = []
    lock = threading.Lock()
    adapter = AntigravityCliAdapter()
    tags = [f"T{i}" for i in range(4)]
    handles: dict[str, Any] = {}
    barrier = threading.Barrier(len(tags))
    errors: list[BaseException] = []

    def _start(tag: str) -> None:
        try:
            provider = ClaudeCliProvider(binary=FAKE_ANTIGRAVITY_SCRIPT, adapter=adapter)
            barrier.wait(timeout=10)
            handles[tag] = start_native_turn_for_provider(
                provider, _request(tag, _TaggedExecutor(tag, log, lock), base)
            )
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=_start, args=(tag,)) for tag in tags]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    try:
        folders = {tag: handle.process_cwd for tag, handle in handles.items()}
        assert all(folder is not None for folder in folders.values())
        assert len(set(folders.values())) == len(tags)
        for folder in folders.values():
            assert folder.parent == base and PROCESS_CWD_RE.match(folder.name)
            assert (folder / ".agents" / "mcp_config.json").is_file()
            assert (folder / ".git").is_dir()
        outputs: dict[str, str] = {}
        for tag, handle in handles.items():
            handle.run_until_result(_Sink())  # turn 1: plain text
            handle.send_user([{"type": "text", "text": f"CALL_MCP REREAD_MCP_CONFIG {tag}"}])
            outputs[tag] = handle.run_until_result(_Sink()).final_text.strip()
    finally:
        for handle in handles.values():
            handle.close()

    assert outputs == {tag: f"SERVER_{tag}" for tag in tags}
    assert sorted(server for server, _ in log) == sorted(tags)
    assert _run_dirs(base) == []


def test_process_folder_is_removed_when_the_start_fails(tmp_path: Path) -> None:
    base = tmp_path / "workdir"
    base.mkdir()

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("spawn failed")

    provider = ClaudeCliProvider(
        binary=FAKE_ANTIGRAVITY_SCRIPT, adapter=AntigravityCliAdapter(), popen_factory=_boom
    )
    with pytest.raises(NativeLoopUnavailable):
        start_native_turn_for_provider(
            provider, _request("T", _TaggedExecutor("T", [], threading.Lock()), base)
        )
    assert _run_dirs(base) == []


def test_process_folder_is_removed_when_the_turn_errors(tmp_path: Path) -> None:
    base = tmp_path / "workdir"
    base.mkdir()
    provider = ClaudeCliProvider(binary=FAKE_ANTIGRAVITY_SCRIPT, adapter=AntigravityCliAdapter())
    handle = start_native_turn_for_provider(
        provider,
        _request("T", _TaggedExecutor("T", [], threading.Lock()), base, text="Run FORBIDDEN_BUILTIN now"),
    )
    folder = handle.process_cwd
    assert folder is not None and folder.is_dir()
    with pytest.raises(Exception):
        handle.run_until_result(_Sink())
    handle.close()
    assert not folder.exists()
    assert _run_dirs(base) == []


def test_orphan_folders_of_dead_owners_are_swept_and_live_ones_kept(tmp_path: Path) -> None:
    base = tmp_path / "workdir"
    base.mkdir()
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        orphan_dead = base / f"run-{dead.pid}-3"
        orphan_own = base / f"run-{os.getpid()}-7"
        live_owner = base / f"run-{live.pid}-0"
        unrelated = base / "keep-me"
        for folder in (orphan_dead, orphan_own, live_owner, unrelated):
            (folder / ".agents").mkdir(parents=True)
            (folder / ".agents" / "mcp_config.json").write_text("{}", encoding="utf-8")

        fresh = create_process_cwd(base)
        try:
            assert fresh.is_dir() and fresh.parent == base
            assert not orphan_dead.exists()
            assert not orphan_own.exists()
            assert live_owner.exists()
            assert unrelated.exists()
            # The folder of a process still running in this Orin is never swept.
            sweep_orphan_process_cwds(base)
            assert fresh.exists()
        finally:
            assert remove_process_cwd(fresh)
        assert not fresh.exists()
    finally:
        live.kill()
        live.wait()


def test_a_closed_slot_is_reused_and_a_held_one_is_not(tmp_path: Path) -> None:
    """agy keeps one cache entry per folder it ran in: names come back instead of growing without end."""
    base = tmp_path / "workdir"
    first = create_process_cwd(base)
    second = create_process_cwd(base)
    assert first != second
    assert remove_process_cwd(first)
    third = create_process_cwd(base)
    assert third == first
    for folder in (second, third):
        assert remove_process_cwd(folder)
    assert _run_dirs(base) == []


def test_remove_process_cwd_only_touches_process_folders(tmp_path: Path) -> None:
    other = tmp_path / "workdir"
    other.mkdir()
    assert remove_process_cwd(other) is False
    assert other.exists()


def test_text_path_gives_each_agy_call_its_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "text-base"
    monkeypatch.setattr(
        "agentos.providers.claude_cli.provider.ensure_isolated_cwd",
        lambda base_arg=None: claude_command.ensure_isolated_cwd(base),
    )
    seen: list[dict[str, Any]] = []

    def _popen(cmd: list[str], **kwargs: Any) -> Any:
        cwd = Path(kwargs["cwd"])
        seen.append(
            {
                "cwd": cwd,
                "agent_md": (cwd / ".agents" / "agents" / "orin.md").is_file(),
            }
        )
        return subprocess.Popen([sys.executable, *cmd], **kwargs)

    provider = ClaudeCliProvider(
        binary=FAKE_ANTIGRAVITY_SCRIPT, adapter=AntigravityCliAdapter(), popen_factory=_popen
    )
    def _request_for(conv: str) -> Mapping[str, Any]:
        return {
            "model": "gemini-3.8-flash-low",
            "messages": [{"role": "user", "content": "hi"}],
            "conversation_id": conv,
        }

    # Two calls in flight at once: the second starts while the first still runs in its folder.
    first_call = provider.stream(dict(_request_for("conv-a")))
    second_call = provider.stream(dict(_request_for("conv-b")))
    first_items = [next(first_call)]
    second_items = [next(second_call)]
    first_items.extend(first_call)
    second_items.extend(second_call)
    assert first_items and second_items

    assert len(seen) == 2
    first, second = seen[0]["cwd"], seen[1]["cwd"]
    assert first != second
    for item in seen:
        assert item["cwd"].parent == base and PROCESS_CWD_RE.match(item["cwd"].name)
        assert item["agent_md"]
        assert not item["cwd"].exists()
