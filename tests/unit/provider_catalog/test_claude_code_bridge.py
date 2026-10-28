from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.provider_stream import (
    NormalizedStreamItem,
    ProviderKeyRejected,
    StreamKind,
)
from agentos.provider_catalog.claude_code import (
    ANTHROPIC_CLOUD_BASE_URL,
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    ClaudeCodeModelCatalogClient,
    ProviderTransportError,
    StreamUsage,
    _build_claude_cli_command,
    _resolve_claude_binary,
    build_isolated_claude_env,
    check_claude_cli_status,
    is_claude_cli_bridge_url,
    normalize_anthropic_base_url,
    stream_claude_code_bridge,
)


def test_url_helpers_and_ssrf_protection() -> None:
    assert DEFAULT_CLAUDE_CLI_BRIDGE_URL == "http://127.0.0.1:18447"
    assert ANTHROPIC_CLOUD_BASE_URL == "https://api.anthropic.com"

    assert is_claude_cli_bridge_url("http://127.0.0.1:18447") is True
    assert is_claude_cli_bridge_url("http://localhost:18447") is True
    assert is_claude_cli_bridge_url("http://127.0.0.1:18447/") is True
    assert is_claude_cli_bridge_url(None) is False
    assert is_claude_cli_bridge_url("") is False
    assert is_claude_cli_bridge_url("https://api.anthropic.com") is False
    assert is_claude_cli_bridge_url("https://api.anthropic.com/v1") is False

    assert normalize_anthropic_base_url("http://127.0.0.1:18447/") == "http://127.0.0.1:18447"
    assert normalize_anthropic_base_url("http://localhost:18447/v1") == "http://localhost:18447"
    assert normalize_anthropic_base_url("https://api.anthropic.com/") == "https://api.anthropic.com"
    assert normalize_anthropic_base_url("https://api.anthropic.com/v1") == "https://api.anthropic.com"

    malicious_urls = [
        "",
        "   ",
        "ftp://127.0.0.1:18447",
        "file:///C:/Windows/System32/cmd.exe",
        "http://user:pass@127.0.0.1:18447",
        "http://127.0.0.1:18447?token=secret",
        "http://127.0.0.1:18447#frag",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.50:18447",
        "http://10.0.0.1:18447",
        "http://api.anthropic.com",  # Cloud must use HTTPS
        "https://evil-anthropic.com",
        "https://attacker.example.com",
    ]
    for bad_url in malicious_urls:
        with pytest.raises(ValueError):
            normalize_anthropic_base_url(bad_url)


def test_build_isolated_claude_env_strips_api_keys_and_bare_triggers() -> None:
    dirty_env = {
        "PATH": r"C:\Windows\System32;C:\Users\User\AppData\Roaming\npm",
        "USERPROFILE": r"C:\Users\User",
        "APPDATA": r"C:\Users\User\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\User\AppData\Local",
        "SystemRoot": r"C:\Windows",
        "HOME": "/home/user",
        "ANTHROPIC_API_KEY": "sk-ant-api03-leaked-secret",
        "ANTHROPIC_AUTH_TOKEN": "bearer-secret",
        "ANTHROPIC_BASE_URL": "https://proxy.attacker.example",
        "ANTHROPIC_CUSTOM_HEADERS": "X-Evil: 1",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "custom-sonnet",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "custom-opus",
        "CLAUDE_CODE_SIMPLE": "1",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLAUDE_CODE_USE_FOUNDRY": "1",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
    }

    isolated = build_isolated_claude_env(dirty_env)

    stripped_keys = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_SIMPLE",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
    )
    for key in stripped_keys:
        assert key not in isolated

    for preserved in ("PATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SystemRoot", "HOME"):
        assert isolated[preserved] == dirty_env[preserved]

    assert isolated["PYTHONIOENCODING"] == "utf-8"
    assert isolated["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert isolated["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] == "1"


def test_resolve_claude_binary_prefers_native_exe_over_cmd_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    npm_dir = appdata / "npm"
    native_bin_dir = npm_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
    native_bin_dir.mkdir(parents=True)

    cmd_wrapper = npm_dir / "claude.cmd"
    cmd_wrapper.write_text("@echo off\r\n", encoding="utf-8")

    native_exe = native_bin_dir / "claude.exe"
    native_exe.write_bytes(b"MZ-fake-pe")

    monkeypatch.setattr("shutil.which", lambda name, **kw: str(cmd_wrapper))

    resolved = _resolve_claude_binary({"APPDATA": str(appdata)})
    assert resolved == str(native_exe)
    assert not resolved.lower().endswith(".cmd")


def test_build_claude_cli_command_never_uses_bare_and_enforces_token_isolation(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "system.prompt.txt"
    prompt_file.write_text("You are Orin.", encoding="utf-8")

    cmd = _build_claude_cli_command(
        binary=r"C:\path\to\claude.exe",
        system_prompt_file=prompt_file,
        model="claude-sonnet-4-6",
        effort="high",
    )

    assert "--bare" not in cmd
    assert "-p" in cmd
    assert "--safe-mode" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--disable-slash-commands" in cmd
    assert "--no-chrome" in cmd

    tools_idx = cmd.index("--tools")
    assert cmd[tools_idx + 1] == ""

    perm_idx = cmd.index("--permission-prompts")
    assert cmd[perm_idx + 1] == "none"

    assert "--no-session-persistence" in cmd

    turns_idx = cmd.index("--max-turns")
    assert cmd[turns_idx + 1] == "1"

    sys_idx = cmd.index("--system-prompt-file")
    assert cmd[sys_idx + 1] == str(prompt_file)

    in_idx = cmd.index("--input-format")
    assert cmd[in_idx + 1] == "stream-json"

    out_idx = cmd.index("--output-format")
    assert cmd[out_idx + 1] == "stream-json"

    assert "--verbose" in cmd
    assert "--include-partial-messages" in cmd

    # Red-Team: reject argument injection and shell metacharacters in model, effort, and system_prompt_file
    for bad_model in ("--settings=evil.json", "claude; rm -rf /", "claude & calc.exe", "model\nname"):
        with pytest.raises(ValueError):
            _build_claude_cli_command(
                binary="claude.exe",
                system_prompt_file=prompt_file,
                model=bad_model,
            )

    for bad_effort in ("--bare", "ultra;whoami", "invalid_level"):
        with pytest.raises(ValueError):
            _build_claude_cli_command(
                binary="claude.exe",
                system_prompt_file=prompt_file,
                model="claude-sonnet-4-6",
                effort=bad_effort,
            )

    with pytest.raises(ValueError):
        _build_claude_cli_command(
            binary="claude.exe",
            system_prompt_file="--bare",
            model="claude-sonnet-4-6",
        )


def test_check_claude_cli_status_validates_oauth_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    class _CompletedProcess:
        def __init__(self, returncode: int, stdout: str, stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    # 1. Valid OAuth subscription
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: _CompletedProcess(
            0,
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "subscriptionType": "max",
                    "email": "dev@example.com",
                }
            ),
        ),
    )
    status = check_claude_cli_status()
    assert status["loggedIn"] is True
    assert status["authMethod"] == "claude.ai"
    assert status["subscriptionType"] == "max"
    assert status["email"] == "dev@example.com"

    # 2. Not logged in
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: _CompletedProcess(
            0,
            json.dumps({"loggedIn": False, "authMethod": "none"}),
        ),
    )
    with pytest.raises((ProviderTransportError, ProviderKeyRejected)):
        check_claude_cli_status()

    # 3. Logged in via api_key instead of claude.ai OAuth subscription
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: _CompletedProcess(
            0,
            json.dumps({"loggedIn": True, "authMethod": "api_key"}),
        ),
    )
    with pytest.raises((ProviderTransportError, ProviderKeyRejected)):
        check_claude_cli_status()

    # 4. Invalid JSON output
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: _CompletedProcess(0, "not-valid-json"),
    )
    with pytest.raises((ProviderTransportError, ProviderKeyRejected)):
        check_claude_cli_status()

    # 5. Binary missing
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: None,
    )
    with pytest.raises((ProviderTransportError, ProviderKeyRejected)):
        check_claude_cli_status()


def test_claude_code_model_catalog_client_lists_subscription_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_calls: list[int] = []

    def fake_status(**kwargs: Any) -> dict[str, object]:
        status_calls.append(1)
        return {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "subscriptionType": "max",
            "email": "dev@example.com",
        }

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code.check_claude_cli_status",
        fake_status,
    )

    client = ClaudeCodeModelCatalogClient()
    models = client.fetch("", base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL)

    assert len(status_calls) == 1
    by_id = {str(m["id"]): m for m in models}
    for expected_id in (
        "claude-opus-5-5",
        "claude-fable-5-1",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-1-20250805",
        "claude-opus-4-0",
        "claude-sonnet-4-0",
        "claude-3-7-sonnet-20250219",
    ):
        assert expected_id in by_id
        entry = by_id[expected_id]
        supported_params = entry.get("supported_parameters") or []
        assert "tools" in supported_params
        assert "vision" in supported_params
        assert "reasoning" in supported_params


class _FakePopen:
    def __init__(
        self,
        cmd: list[str],
        *,
        ndjson_lines: list[str],
        stderr_text: str = "",
        returncode: int = 0,
        block_after_lines: int | None = None,
    ) -> None:
        self.args = cmd
        self.pid = 42424
        self.returncode: int | None = None
        self._final_returncode = returncode
        self.stdin = io.StringIO()
        self.terminated = False
        self.killed = False
        self._lines = ndjson_lines
        self._block_after = block_after_lines
        self.stdout = self._make_stdout()
        self.stderr = io.StringIO(stderr_text)

    def _make_stdout(self) -> Any:
        parent = self

        class _LineIter:
            def __iter__(self) -> Any:
                for idx, line in enumerate(parent._lines):
                    if parent.terminated or parent.killed:
                        return
                    yield line if line.endswith("\n") else f"{line}\n"
                    if parent._block_after is not None and idx + 1 >= parent._block_after:
                        import time

                        for _ in range(50):
                            if parent.terminated or parent.killed:
                                return
                            time.sleep(0.02)
                parent.returncode = parent._final_returncode

            def close(self) -> None:
                pass

        return _LineIter()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._final_returncode
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_stream_claude_code_bridge_streams_thinking_text_tool_calls_and_usage_without_duplication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ndjson_stream = [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "ses_1",
                "apiKeySource": "none",
                "model": "claude-sonnet-4-6",
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "message_start",
                    "message": {
                        "usage": {
                            "input_tokens": 10,
                            "cache_creation_input_tokens": 596,
                            "cache_read_input_tokens": 100,
                            "output_tokens": 1,
                        }
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "Pensando..."},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "text_delta",
                        "text": (
                            "Vou ler o arquivo.\n"
                            '<orin_tool_call>{"name": "read_file", "arguments": {"path": "README.md"}}</orin_tool_call>'
                        ),
                    },
                },
            }
        ),
        # Snapshot frame containing identical text & tool call — MUST NOT be emitted a second time
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Pensando..."},
                        {
                            "type": "text",
                            "text": (
                                "Vou ler o arquivo.\n"
                                '<orin_tool_call>{"name": "read_file", "arguments": {"path": "README.md"}}</orin_tool_call>'
                            ),
                        },
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed",
                    "resetsAt": 1793206800,
                    "unifiedWindows": {
                        "five_hour": {"utilization": 0.45, "resetsAt": 1793206800},
                        "seven_day": {"utilization": 0.34, "resetsAt": 1793451600},
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Vou ler o arquivo.",
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 596,
                    "cache_read_input_tokens": 100,
                    "output_tokens": 25,
                },
            }
        ),
    ]

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    items = list(
        stream_claude_code_bridge(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model="claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": "Você é o Orin."},
                {"role": "user", "content": "Leia o README.md"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
            popen_factory=lambda cmd, **kw: _FakePopen(cmd, ndjson_lines=ndjson_stream),
        )
    )

    thinking_items = [i for i in items if i.kind is StreamKind.THINKING]
    text_items = [i for i in items if i.kind is StreamKind.TEXT]
    tool_items = [i for i in items if i.kind is StreamKind.TOOL_CALL]
    finish_items = [i for i in items if i.kind is StreamKind.FINISH]

    assert len(thinking_items) == 1
    assert thinking_items[0].thinking == "Pensando..."

    combined_text = "".join(i.text or "" for i in text_items).strip()
    assert combined_text == "Vou ler o arquivo."
    assert "<orin_tool_call>" not in combined_text

    assert len(tool_items) == 1
    assert tool_items[0].tool_name == "read_file"
    assert json.loads(tool_items[0].arguments_delta or "{}") == {"path": "README.md"}

    assert len(finish_items) == 1
    finish_item = finish_items[0]
    assert finish_item.usage is not None
    assert isinstance(finish_item.usage, StreamUsage)
    assert finish_item.usage.input_tokens == 706
    assert finish_item.usage.output_tokens == 25
    assert finish_item.usage.cache_read_input_tokens == 100
    assert finish_item.usage.cache_creation_input_tokens == 596
    assert finish_item.cost == 0.0


def test_stream_claude_code_bridge_supports_native_tool_use_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ndjson_stream = [
        json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none"}),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_native_1",
                        "name": "mcp__orin__read_file",
                        "input": {},
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"path": "src/main.py"}',
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_stop", "index": 0},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_native_1",
                            "name": "mcp__orin__read_file",
                            "input": {"path": "src/main.py"},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_native_2",
                            "name": "Read",
                            "input": {"file_path": "pyproject.toml"},
                        },
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 50, "output_tokens": 15},
            }
        ),
    ]

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    items = list(
        stream_claude_code_bridge(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Inspect files"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
            popen_factory=lambda cmd, **kw: _FakePopen(cmd, ndjson_lines=ndjson_stream),
        )
    )

    tool_items = [i for i in items if i.kind is StreamKind.TOOL_CALL]
    assert len(tool_items) == 2
    assert tool_items[0].tool_call_id == "toolu_native_1"
    assert tool_items[0].tool_name == "read_file"
    assert json.loads(tool_items[0].arguments_delta or "{}") == {"path": "src/main.py"}

    assert tool_items[1].tool_call_id == "toolu_native_2"
    assert tool_items[1].tool_name == "read_file"
    assert json.loads(tool_items[1].arguments_delta or "{}") == {"path": "pyproject.toml"}


def test_stream_claude_code_bridge_handles_session_limit_and_api_key_leak_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    # Case 1: apiKeySource != "none" -> abort immediately to prevent token billing
    leaked_key_stream = [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "apiKeySource": "ANTHROPIC_API_KEY",
            }
        ),
    ]
    with pytest.raises(ProviderTransportError, match="apiKeySource|ANTHROPIC_API_KEY|OAuth"):
        list(
            stream_claude_code_bridge(
                base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": "Oi"}],
                popen_factory=lambda cmd, **kw: _FakePopen(cmd, ndjson_lines=leaked_key_stream),
            )
        )

    # Case 2: 5-hour session limit reached
    session_limit_stream = [
        json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none"}),
        json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected",
                    "resetsAt": 1793206800,
                    "rateLimitType": "five_hour",
                    "unifiedWindows": {
                        "five_hour": {"utilization": 1.0, "resetsAt": 1793206800},
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "You've hit your session limit · resets 5pm",
            }
        ),
    ]
    with pytest.raises(ProviderTransportError, match="session limit"):
        list(
            stream_claude_code_bridge(
                base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": "Oi"}],
                popen_factory=lambda cmd, **kw: _FakePopen(cmd, ndjson_lines=session_limit_stream),
            )
        )


def test_stream_claude_code_bridge_kills_process_tree_on_cancel_and_cleans_temp_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    captured_popens: list[_FakePopen] = []
    captured_prompt_files: list[Path] = []
    killed_pids: list[int] = []

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._kill_process_tree",
        lambda proc: (killed_pids.append(proc.pid), proc.kill()),
    )

    ndjson_stream = [
        json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none"}),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Primeira parte..."},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Segunda parte nunca emitida"},
                },
            }
        ),
    ]

    cancelled = {"flag": False}

    def cancel_check() -> bool:
        return cancelled["flag"]

    def popen_factory(cmd: list[str], **kwargs: Any) -> _FakePopen:
        sys_idx = cmd.index("--system-prompt-file")
        prompt_path = Path(cmd[sys_idx + 1])
        assert prompt_path.is_file()
        captured_prompt_files.append(prompt_path)
        proc = _FakePopen(cmd, ndjson_lines=ndjson_stream, block_after_lines=2)
        captured_popens.append(proc)
        return proc

    emitted: list[NormalizedStreamItem] = []
    for item in stream_claude_code_bridge(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "Cancele no meio"}],
        cancel_check=cancel_check,
        popen_factory=popen_factory,
    ):
        emitted.append(item)
        if item.kind is StreamKind.TEXT:
            cancelled["flag"] = True

    assert len(captured_popens) == 1
    assert captured_popens[0].killed or captured_popens[0].terminated or 42424 in killed_pids
    assert len(captured_prompt_files) == 1
    assert not captured_prompt_files[0].exists()


def test_stream_claude_code_bridge_handles_fragmented_tool_tags_and_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    fragmented_stream = [
        json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none"}),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Analisando... <orin_tool_"},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": 'call>{"name": "read_file", "arguments": {"path": "a.py"}}</orin_tool_',
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": (
                            'call><orin_tool_call>{"name": "evil_shell", "arguments": {}}</orin_tool_call>'
                            "<orin_tool_call>{bad json</orin_tool_call>"
                            '<tool_call>{"name": "read_file", "arguments": {"path": "b.py"}}</tool_call>'
                        ),
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 5, "output_tokens": 5},
            }
        ),
    ]

    items = list(
        stream_claude_code_bridge(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Teste"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
            popen_factory=lambda cmd, **kw: _FakePopen(cmd, ndjson_lines=fragmented_stream),
        )
    )

    text_items = [i for i in items if i.kind is StreamKind.TEXT]
    tool_items = [i for i in items if i.kind is StreamKind.TOOL_CALL]
    assert "".join(i.text or "" for i in text_items).strip() == "Analisando..."
    assert len(tool_items) == 2
    assert tool_items[0].tool_name == "read_file"
    assert json.loads(tool_items[0].arguments_delta or "{}") == {"path": "a.py"}
    assert tool_items[1].tool_name == "read_file"
    assert json.loads(tool_items[1].arguments_delta or "{}") == {"path": "b.py"}

    # Non-zero exit code without result packet -> raises ProviderTransportError
    with pytest.raises(ProviderTransportError, match="exited unexpectedly"):
        list(
            stream_claude_code_bridge(
                base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": "Crash"}],
                popen_factory=lambda cmd, **kw: _FakePopen(
                    cmd,
                    ndjson_lines=[],
                    stderr_text="Error: OAuth session expired",
                    returncode=1,
                ),
            )
        )


_TEST_CIPHER_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def test_anthropic_registered_in_base_url_and_optional_key_sets() -> None:
    from agentos.provider_catalog.models import (
        PROVIDERS_WITH_BASE_URL,
        PROVIDERS_WITH_OPTIONAL_KEY,
    )

    assert "anthropic" in PROVIDERS_WITH_BASE_URL
    assert "anthropic" in PROVIDERS_WITH_OPTIONAL_KEY


def test_anthropic_model_catalog_client_delegates_to_claude_cli_bridge_in_local_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    import httpx
    from agentos.provider_catalog.http import AnthropicModelCatalogClient
    from agentos.provider_catalog.models import ProviderCatalogContext
    from agentos.provider_catalog.service import ProviderModelCatalogService

    def forbid_http_get(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("HTTP GET should not be called in local Claude CLI bridge mode")

    monkeypatch.setattr(httpx, "get", forbid_http_get)
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code.check_claude_cli_status",
        lambda **kwargs: {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "subscriptionType": "max",
        },
    )

    client = AnthropicModelCatalogClient()
    raw_models = client.fetch("", base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL)
    model_ids = [str(m["id"]) for m in raw_models]
    assert "claude-opus-4-7" in model_ids
    assert "claude-sonnet-4-6" in model_ids

    class _FakeRepo:
        def __init__(self) -> None:
            self.saved: list[Any] = []

        def credential(self, context: ProviderCatalogContext, provider: str) -> dict[str, object] | None:
            return {
                "enabled": True,
                "api_key": "",
                "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            }

        def replace(
            self,
            context: ProviderCatalogContext,
            provider: str,
            records: list[Any],
            refreshed_at: datetime,
        ) -> None:
            self.saved = list(records)

        def list(
            self,
            context: ProviderCatalogContext,
            provider: str,
            favorites_only: bool = False,
        ) -> list[Any]:
            return list(self.saved)

    repo = _FakeRepo()
    service = ProviderModelCatalogService(
        repo,  # type: ignore[arg-type]
        {"anthropic": client},
        now=lambda: datetime(2026, 10, 28, tzinfo=UTC),
    )
    ctx = ProviderCatalogContext(user_id="user-1", purpose="provider.catalog.refresh")
    receipt = service.refresh(ctx, "anthropic")
    assert receipt.count >= 18

    records = service.list(ctx, "anthropic")
    assert len(records) >= 18
    for record in records:
        assert "tools" in record.capabilities
        assert "vision" in record.capabilities
        assert "reasoning" in record.capabilities
        assert record.supports_tools is True
        assert record.supports_vision is True
        assert record.supports_thinking is True


def test_provider_configuration_allows_keyless_local_claude_cli_and_requires_key_for_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.api.contracts import ProviderCredentialRejectedError
    from agentos.persistence.postgres.provider_configuration import (
        PostgresProviderConfigurationAdapter,
    )
    from agentos.persistence.postgres.schema import metadata
    from agentos.persistence.provider_secrets import ProviderSecretCipher
    from agentos.persistence.sqlite import create_local_engine

    engine = create_local_engine(f"sqlite:///{tmp_path / 'anthropic_cfg.db'}")
    metadata.create_all(engine)
    cipher = ProviderSecretCipher(_TEST_CIPHER_KEY)
    adapter = PostgresProviderConfigurationAdapter(engine, cipher=cipher)

    # 1. Local CLI Bridge mode: keyless save succeeds
    local_cfg = adapter.configure(
        {
            "provider": "anthropic",
            "user_id": "user-1",
            "enabled": True,
            "api_key": "",
            "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        }
    )
    assert local_cfg["enabled"] is True
    assert local_cfg["base_url"] == DEFAULT_CLAUDE_CLI_BRIDGE_URL
    assert local_cfg["has_secret"] is False

    # 2. Cloud mode without API key raises ValueError("provider API key is required")
    with pytest.raises(ValueError, match="provider API key is required"):
        adapter.configure(
            {
                "provider": "anthropic",
                "user_id": "user-2",
                "enabled": True,
                "api_key": "",
                "base_url": None,
            }
        )

    with pytest.raises(ValueError, match="provider API key is required"):
        adapter.configure(
            {
                "provider": "anthropic",
                "user_id": "user-2",
                "enabled": True,
                "api_key": "",
                "base_url": ANTHROPIC_CLOUD_BASE_URL,
            }
        )

    # 3. Cloud mode with API key and base_url=None keeps base_url as None
    cloud_cfg = adapter.configure(
        {
            "provider": "anthropic",
            "user_id": "user-2",
            "enabled": True,
            "api_key": "sk-ant-valid-key",
            "base_url": None,
        }
    )
    assert cloud_cfg["enabled"] is True
    assert cloud_cfg["has_secret"] is True
    assert cloud_cfg.get("base_url") is None

    # 4. test_connection succeeds when check_claude_cli_status succeeds
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code.check_claude_cli_status",
        lambda **kwargs: {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "subscriptionType": "max",
        },
    )
    conn_ok = adapter.test_connection(
        {
            "provider": "anthropic",
            "user_id": "user-1",
            "api_key": "",
            "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        }
    )
    assert conn_ok["connected"] is True
    assert conn_ok["models_available"] >= 18
    assert conn_ok["base_url"] == DEFAULT_CLAUDE_CLI_BRIDGE_URL

    # 5. test_connection raises ProviderCredentialRejectedError or ValueError when CLI status fails
    def fail_status(**kwargs: Any) -> dict[str, object]:
        raise ProviderTransportError("Not logged in")

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code.check_claude_cli_status",
        fail_status,
    )
    with pytest.raises((ProviderCredentialRejectedError, ValueError)):
        adapter.test_connection(
            {
                "provider": "anthropic",
                "user_id": "user-1",
                "api_key": "",
                "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            }
        )


def test_http_provider_stream_transport_delegates_local_anthropic_to_claude_code_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.agentic.provider_stream import HTTPProviderStreamTransport

    captured: dict[str, Any] = {}

    def fake_stream_bridge(
        request: Any = None,
        *,
        base_url: str = "",
        model: str = "",
        messages: Any = (),
        tools: Any = (),
        tool_choice: Any = None,
        effort: str | None = None,
        **kwargs: Any,
    ) -> Any:
        captured["base_url"] = base_url
        captured["model"] = model
        captured["messages"] = list(messages)
        captured["effort"] = effort
        yield NormalizedStreamItem(StreamKind.TEXT, 1, text="pong from claude cli")

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code.stream_claude_code_bridge",
        fake_stream_bridge,
    )

    transport = HTTPProviderStreamTransport(
        provider="anthropic",
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        api_key="",
        model="claude-sonnet-4-6",
    )
    items = list(
        transport.stream(
            {
                "messages": [{"role": "user", "content": "ping"}],
                "effort": "high",
            }
        )
    )
    assert [i.text for i in items if i.kind is StreamKind.TEXT] == ["pong from claude cli"]
    assert captured["base_url"] == DEFAULT_CLAUDE_CLI_BRIDGE_URL
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["effort"] == "high"


def test_chat_worker_anthropic_transport_uses_local_bridge_when_configured_and_requires_key_for_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    from sqlalchemy import insert, update
    from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
    from agentos.agentic.provider_stream import HTTPProviderStreamTransport
    from agentos.persistence.postgres.schema import metadata, provider_configurations
    from agentos.persistence.sqlite import create_local_engine
    from agentos.workers.chat import PROVIDER_BASE_URLS, ChatWorker

    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", _TEST_CIPHER_KEY)
    engine = create_local_engine(f"sqlite:///{tmp_path / 'worker_anthropic.db'}")
    metadata.create_all(engine)
    now = datetime.now(UTC)

    with engine.begin() as conn:
        conn.execute(
            insert(provider_configurations).values(
                user_id="user-1",
                provider="anthropic",
                enabled=True,
                base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
                secret_ref="ref-1",
                key_cooldown_seconds=60,
                catalog_refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": engine})()

    # Local CLI bridge mode with 0 keys
    transport = worker._provider_transport(
        {"provider": "anthropic", "user_id": "user-1", "model_id": "claude-sonnet-4-6"}
    )
    assert isinstance(transport, HTTPProviderStreamTransport)
    assert transport.base_url == DEFAULT_CLAUDE_CLI_BRIDGE_URL

    direct = worker._transport_for("user-1", "anthropic", "claude-sonnet-4-6")
    assert isinstance(direct, HTTPProviderStreamTransport)
    assert direct.base_url == DEFAULT_CLAUDE_CLI_BRIDGE_URL

    # Cloud mode (base_url=None)
    assert (
        worker._base_url_for("anthropic", {"base_url": None, "api_key": "sk-ant-123"})
        == PROVIDER_BASE_URLS["anthropic"]
    )

    with engine.begin() as conn:
        conn.execute(
            update(provider_configurations)
            .where(
                provider_configurations.c.user_id == "user-1",
                provider_configurations.c.provider == "anthropic",
            )
            .values(base_url=None)
        )

    with pytest.raises(ValueError, match="provider credential is missing"):
        worker._transport_for("user-1", "anthropic", "claude-sonnet-4-6")

    cloud_transport = worker._provider_transport(
        {"provider": "anthropic", "user_id": "user-1", "model_id": "claude-sonnet-4-6"}
    )
    assert isinstance(cloud_transport, MultiKeyProviderStreamTransport)


def test_gateway_anthropic_test_and_configure_endpoints() -> None:
    from fastapi.testclient import TestClient
    from agentos.api import (
        ApiServices,
        AuthenticatedPrincipal,
        InMemorySecurityService,
        create_app,
    )

    class _FakeAnthropicConfig:
        def __init__(self) -> None:
            self.configured: list[dict[str, object]] = []
            self.tested: list[dict[str, object]] = []

        def configure(self, command: dict[str, object]) -> dict[str, object]:
            self.configured.append(command)
            return {
                "provider": "anthropic",
                "enabled": bool(command["enabled"]),
                "base_url": command.get("base_url"),
                "secret_ref": "ref-1",
                "has_secret": bool(command.get("api_key")),
                "key_cooldown_seconds": 60,
            }

        def test_connection(self, command: dict[str, object]) -> dict[str, object]:
            self.tested.append(command)
            return {
                "connected": True,
                "models_available": 4,
                "base_url": command.get("base_url") or DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            }

    security = InMemorySecurityService()
    security.add_pat(
        "pat-test",
        AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})),
    )
    fake_cfg = _FakeAnthropicConfig()
    client = TestClient(
        create_app(
            ApiServices(
                security=security,
                provider_configuration=fake_cfg,  # type: ignore[arg-type]
            )
        )
    )
    headers = {"Authorization": "Bearer pat-test", "Idempotency-Key": "ant-1"}

    # 1. POST /v1/providers/anthropic/test succeeds in CLI bridge mode
    test_resp = client.post(
        "/v1/providers/anthropic/test",
        headers=headers,
        json={"base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL},
    )
    assert test_resp.status_code == 200
    assert test_resp.json() == {
        "connected": True,
        "models_available": 4,
        "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    }

    # 2. PUT /v1/providers/anthropic keyless save succeeds when base_url is local bridge
    save_local = client.put(
        "/v1/providers/anthropic",
        headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "ant-2"},
        json={"enabled": True, "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL},
    )
    assert save_local.status_code == 200
    assert save_local.json()["base_url"] == DEFAULT_CLAUDE_CLI_BRIDGE_URL

    # 3. PUT /v1/providers/anthropic without api_key and without local bridge URL raises ValueError
    with pytest.raises(ValueError, match="API key is required"):
        client.put(
            "/v1/providers/anthropic",
            headers={"Authorization": "Bearer pat-test", "Idempotency-Key": "ant-3"},
            json={"enabled": True},
        )





def test_claude_code_model_catalog_reads_live_cache_directory_and_auto_upgrades_legacy_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    from agentos.provider_catalog.models import ProviderCatalogContext, ProviderModelRecord
    from agentos.provider_catalog.service import ProviderModelCatalogService

    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code.check_claude_cli_status",
        lambda **kwargs: {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "subscriptionType": "max",
            "orgId": "org-live-test",
        },
    )

    cache_dir = tmp_path / "model-catalog"
    cache_dir.mkdir(parents=True)
    (cache_dir / "org-live-test-cc.json").write_text(
        json.dumps(
            {
                "version": 2,
                "fetchedAt": 1793201481996,
                "organizationUuid": "org-live-test",
                "catalog": {
                    "surface": "cc",
                    "config": {
                        "id": "cc",
                        "models": [
                            {
                                "id": "claude-opus-6-0",
                                "name": "Opus 6.0",
                                "runtime": {"max_input_tokens": 2_000_000},
                            }
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    client = ClaudeCodeModelCatalogClient(cache_dir=cache_dir)
    models = client.fetch("", base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL)
    by_id = {str(m["id"]): m for m in models}
    assert "claude-opus-6-0" in by_id
    assert by_id["claude-opus-6-0"]["display_name"] == "Claude Opus 6.0 (Assinatura CLI)"
    assert by_id["claude-opus-6-0"]["max_input_tokens"] == 2_000_000

    # Verify service.list() auto-upgrades a legacy 4-item cache in CLI bridge mode
    class _LegacyRepo:
        def __init__(self) -> None:
            self.saved: list[Any] = [
                ProviderModelRecord(
                    provider="anthropic",
                    model_id="claude-opus-4-7",
                    display_name="Claude Opus 4.7 (Assinatura CLI)",
                    context_window=200_000,
                    capabilities=("tools", "reasoning"),
                    input_modalities=("text",),
                    output_modalities=("text",),
                    pricing=None,
                    refreshed_at=datetime(2026, 10, 28, tzinfo=UTC),
                )
            ]

        def credential(self, context: ProviderCatalogContext, provider: str) -> dict[str, object] | None:
            return {
                "enabled": True,
                "api_key": "",
                "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            }

        def replace(
            self,
            context: ProviderCatalogContext,
            provider: str,
            records: list[Any],
            refreshed_at: datetime,
        ) -> None:
            self.saved = list(records)

        def list(
            self,
            context: ProviderCatalogContext,
            provider: str,
            favorites_only: bool = False,
        ) -> list[Any]:
            return list(self.saved)

    repo = _LegacyRepo()
    service = ProviderModelCatalogService(
        repo,  # type: ignore[arg-type]
        {"anthropic": client},
        now=lambda: datetime(2026, 10, 28, tzinfo=UTC),
    )
    ctx = ProviderCatalogContext(user_id="user-1", purpose="provider.catalog.list")
    upgraded = service.list(ctx, "anthropic")
    assert len(upgraded) >= 19
    assert any(r.model_id == "claude-opus-5-5" for r in upgraded)
    assert any(r.model_id == "claude-opus-6-0" for r in upgraded)
