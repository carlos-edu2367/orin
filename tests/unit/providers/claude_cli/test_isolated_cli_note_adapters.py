from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.claude_cli.native_loop import (
    NativeTurnRequest,
    isolated_cli_note,
    start_native_turn_for_provider,
    with_isolated_cli_note,
)
from agentos.providers.claude_cli.provider import ClaudeCliProvider
from agentos.providers.cli_tools_mcp import McpToolSpec, ToolCallRequest, ToolCallResult
from agentos.providers.codex_cli.adapter import CodexCliAdapter

FAKE_CLAUDE_SCRIPT = str(Path(__file__).with_name("fake_claude.py"))


class _NoopExecutor:
    def list_tools(self) -> Sequence[McpToolSpec]:
        return [
            McpToolSpec(
                name="echo",
                description="Echo text",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                read_only=True,
            )
        ]

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(text=str(request.arguments.get("text") or ""))


def test_none_claude_and_unknown_adapters_preserve_byte_identical_note(tmp_path: Path) -> None:
    baseline_plain = isolated_cli_note("orin")
    assert isolated_cli_note("orin", adapter_name=None) == baseline_plain
    assert isolated_cli_note("orin", adapter_name="claude") == baseline_plain
    assert isolated_cli_note("orin", adapter_name="unknown-cli") == baseline_plain

    baseline_with_session = isolated_cli_note(
        "orin",
        permission_mode="full_autonomy",
        workspace_root=tmp_path,
    )
    assert (
        isolated_cli_note(
            "orin",
            permission_mode="full_autonomy",
            workspace_root=tmp_path,
            adapter_name=None,
        )
        == baseline_with_session
    )
    assert (
        isolated_cli_note(
            "orin",
            permission_mode="full_autonomy",
            workspace_root=tmp_path,
            adapter_name="claude",
        )
        == baseline_with_session
    )

    wrapped_none = with_isolated_cli_note(
        "You are Orin.",
        "orin",
        permission_mode="accept_edits",
        workspace_root=tmp_path,
        adapter_name=None,
    )
    wrapped_claude = with_isolated_cli_note(
        "You are Orin.",
        "orin",
        permission_mode="accept_edits",
        workspace_root=tmp_path,
        adapter_name="claude",
    )
    assert wrapped_claude == wrapped_none
    assert "## Codex functions.exec" not in wrapped_claude
    assert "## Parallel subagents on this CLI" not in wrapped_claude


def test_codex_adapter_note_teaches_functions_exec_images_and_promise_all(tmp_path: Path) -> None:
    note = isolated_cli_note(
        "orin",
        permission_mode="full_autonomy",
        workspace_root=tmp_path,
        adapter_name="codex",
    )
    assert "## Codex functions.exec" in note
    assert "functions.exec" in note
    assert "tools.mcp__orin__<Name>(args)" in note
    assert "ALL_TOOLS" in note
    assert "Do not call ToolSearch" in note
    assert "image(b)" in note
    assert "content(r.content)" in note
    assert "Never text(r)" in note
    assert "Promise.all" in note
    assert "read_file" not in note
    assert "## Parallel subagents on this CLI" not in note
    assert "run_in_background=true" not in note

    # Custom server name flows into tools.mcp__<srv>__<Name>(args)
    custom_note = isolated_cli_note("my_srv", adapter_name="codex")
    assert "tools.mcp__my_srv__<Name>(args)" in custom_note

    # Idempotent via with_isolated_cli_note
    wrapped = with_isolated_cli_note(
        "Base prompt.",
        "orin",
        permission_mode="full_autonomy",
        workspace_root=tmp_path,
        adapter_name="codex",
    )
    assert with_isolated_cli_note(
        wrapped,
        "orin",
        permission_mode="full_autonomy",
        workspace_root=tmp_path,
        adapter_name="codex",
    ) == wrapped


def test_antigravity_adapter_note_teaches_background_parallel_subagents(tmp_path: Path) -> None:
    note = isolated_cli_note(
        "orin",
        permission_mode="full_autonomy",
        workspace_root=tmp_path,
        adapter_name="antigravity",
    )
    assert "## Parallel subagents on this CLI" in note
    assert "run_in_background=true" in note
    assert "agent_ids" in note
    assert "CheckSubagent" in note

    agy_section = note.split("## Parallel subagents on this CLI", 1)[1]
    assert "functions.exec" not in agy_section
    assert "## Codex functions.exec" not in note
    assert "Promise.all" not in note
    assert "image(b)" not in note

    wrapped = with_isolated_cli_note(
        "Base prompt.",
        "orin",
        permission_mode="full_autonomy",
        workspace_root=tmp_path,
        adapter_name="antigravity",
    )
    assert with_isolated_cli_note(
        wrapped,
        "orin",
        permission_mode="full_autonomy",
        workspace_root=tmp_path,
        adapter_name="antigravity",
    ) == wrapped


@pytest.mark.parametrize(
    ("adapter_cls", "expected_present", "expected_absent"),
    [
        (
            CodexCliAdapter,
            ("## Codex functions.exec", "Promise.all", "image(b)"),
            ("## Parallel subagents on this CLI",),
        ),
        (
            AntigravityCliAdapter,
            ("## Parallel subagents on this CLI", "run_in_background=true", "agent_ids"),
            ("## Codex functions.exec",),
        ),
        (
            ClaudeCliAdapter,
            ("# Where you work",),
            ("## Codex functions.exec", "## Parallel subagents on this CLI"),
        ),
    ],
)
def test_start_native_turn_for_provider_passes_adapter_name_to_system_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_cls: type[Any],
    expected_present: tuple[str, ...],
    expected_absent: tuple[str, ...],
) -> None:
    captured_prompt: list[str] = []

    class _FakeProc:
        stdin = None
        stdout = None
        stderr = None

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

    class _RecordingAdapter(adapter_cls):  # type: ignore[misc,valid-type]
        capabilities = replace(adapter_cls.capabilities, per_process_cwd=False)

        def build_command(self, req: Any) -> list[str]:
            if req.system_prompt_file is not None:
                captured_prompt.append(Path(req.system_prompt_file).read_text(encoding="utf-8"))
            return [FAKE_CLAUDE_SCRIPT]

        def write_tool_server_wiring(self, handle: Any, temp_dir: Path, **_kw: Any) -> Any:
            from agentos.providers.cli_adapter import ToolServerWiring

            cfg = temp_dir / f"cfg_{self.name}.json"
            cfg.write_text("{}", encoding="utf-8")
            return ToolServerWiring(argv=("--mcp-config", str(cfg)), env={}, files=(cfg,))

        def environment(self, base_env: Mapping[str, str] | None, **_kw: Any) -> dict[str, str]:
            return dict(base_env or {})

    monkeypatch.setattr(
        "agentos.providers.claude_cli.native_loop.ensure_isolated_cwd",
        lambda _p: None,
    )

    adapter = _RecordingAdapter()
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        adapter=adapter,
        popen_factory=lambda *args, **kwargs: _FakeProc(),
    )
    req = NativeTurnRequest(
        model="test-model",
        system_prompt="You are Orin.",
        user_content=[{"type": "text", "text": "hello"}],
        executor=_NoopExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=10**12,
        max_turns=5,
        cwd=tmp_path,
    )
    handle = start_native_turn_for_provider(provider, req)
    try:
        assert len(captured_prompt) == 1
        prompt_text = captured_prompt[0]
        for token in expected_present:
            assert token in prompt_text
        for token in expected_absent:
            assert token not in prompt_text
    finally:
        handle.close()
