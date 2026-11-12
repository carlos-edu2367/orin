from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

from agentos.providers.cli_adapter import CliTurnRequest, ToolServerWiring
from agentos.providers.codex_cli.command import (
    DEFAULT_CODEX_MODEL,
    MAX_CODEX_MAJOR,
    MIN_CODEX_VERSION,
    ORIN_MCP_TOKEN_ENV,
    VALID_EFFORT_LEVELS,
    build_codex_command,
    build_isolated_codex_env,
    clean_codex_model_id,
    codex_version,
    CODEX_NEUTRAL_CONFIG,
    CODEX_OMITTED_MEDIA_NOTE,
    encode_codex_user_turn,
    is_codex_version_supported,
    redact_codex_argv,
    resolve_codex_binary,
    wire_codex_tool_server,
)


class _FakeMcpHandle:
    url = "http://127.0.0.1:54321/mcp"
    token = "super-secret-orin-mcp-token-999"


def test_build_codex_command_new_ephemeral_turn(tmp_path: Path) -> None:
    prompt_file = tmp_path / "system_prompt.md"
    prompt_file.write_text("System instructions", encoding="utf-8")
    wiring = wire_codex_tool_server(_FakeMcpHandle(), server_name="orin")

    req = CliTurnRequest(
        binary="C:/codex/codex.exe",
        system_prompt_file=prompt_file,
        model="openai-codex/gpt-6.1-sol",
        effort="xhigh",
        persist_session=False,
        tool_server_wiring=wiring,
    )

    argv = build_codex_command(
        req,
        supported_efforts=("low", "medium", "high", "xhigh", "max", "ultra"),
    )

    import json

    expected = [
        "C:/codex/codex.exe",
        "exec",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--ephemeral",
        "-m",
        "gpt-6.1-sol",
        "-c",
        f"model_instructions_file={json.dumps(str(prompt_file))}",
        "-c",
        "features.shell_tool=false",
        "-c",
        'web_search="disabled"',
        "-c",
        "features.view_image=false",
        "-c",
        "features.image_generation=false",
        "-c",
        "features.multi_agent=false",
        "-c",
        'sandbox_mode="read-only"',
        "-c",
        'approval_policy="never"',
        *[item for value in CODEX_NEUTRAL_CONFIG for item in ("-c", value)],
        "-c",
        'model_reasoning_effort="xhigh"',
        "-c",
        'mcp_servers.orin.url="http://127.0.0.1:54321/mcp"',
        "-c",
        f'mcp_servers.orin.bearer_token_env_var="{ORIN_MCP_TOKEN_ENV}"',
        "-c",
        "mcp_servers.orin.required=true",
        "-c",
        'mcp_servers.orin.default_tools_approval_mode="approve"',
        "-",
    ]
    assert argv == expected


def test_build_codex_command_resume_persistent_turn(tmp_path: Path) -> None:
    prompt_file = tmp_path / "system_prompt.md"
    prompt_file.write_text("System instructions", encoding="utf-8")
    wiring = wire_codex_tool_server(_FakeMcpHandle(), server_name="orin")

    req = CliTurnRequest(
        binary="/usr/local/bin/codex",
        system_prompt_file=prompt_file,
        model=None,
        resume_session_id="019cfe2a-1111-7222-8333-444455556666",
        persist_session=True,
    )

    argv = build_codex_command(req, wiring=wiring)

    assert argv[:7] == [
        "/usr/local/bin/codex",
        "exec",
        "resume",
        "019cfe2a-1111-7222-8333-444455556666",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
    ]
    assert "--skip-git-repo-check" in argv
    assert "--ephemeral" not in argv
    m_idx = argv.index("-m")
    assert argv[m_idx + 1] == DEFAULT_CODEX_MODEL
    assert argv[-1] == "-"


def test_effort_omitted_when_unsupported_by_model(tmp_path: Path) -> None:
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("Prompt", encoding="utf-8")

    req = CliTurnRequest(
        binary="codex",
        system_prompt_file=prompt_file,
        model="gpt-6-luna",
        effort="ultra",
        persist_session=False,
    )
    # gpt-6-luna does not support 'ultra'
    argv = build_codex_command(
        req,
        supported_efforts=("low", "medium", "high", "xhigh", "max"),
    )
    assert not any("model_reasoning_effort" in item for item in argv)


def test_mcp_token_only_in_env_never_in_argv_or_logs(tmp_path: Path) -> None:
    prompt_file = tmp_path / "secret_prompt.txt"
    prompt_file.write_text("TOP_SECRET_PROMPT_BODY", encoding="utf-8")
    handle = _FakeMcpHandle()

    wiring = wire_codex_tool_server(handle, server_name="orin")
    assert isinstance(wiring, ToolServerWiring)
    assert wiring.env == {ORIN_MCP_TOKEN_ENV: handle.token}
    assert wiring.files == ()
    assert handle.token not in " ".join(wiring.argv)

    req = CliTurnRequest(
        binary="codex",
        system_prompt_file=prompt_file,
        model="gpt-6.1-sol",
        tool_server_wiring=wiring,
    )
    argv = build_codex_command(req)
    assert handle.token not in " ".join(argv)

    logged = redact_codex_argv(argv)
    assert handle.token not in " ".join(logged)
    assert str(prompt_file) not in " ".join(logged)
    assert 'mcp_servers.orin.url="http://127.0.0.1:54321/mcp"' in logged
    assert "mcp_servers.orin.bearer_token_env_var" not in " ".join(logged)


def test_build_isolated_codex_env_strips_api_keys_and_allowlists() -> None:
    dirty_env = {
        "PATH": "/usr/bin:/bin",
        "SYSTEMROOT": "C:\\Windows",
        "USERPROFILE": "C:\\Users\\test",
        "CODEX_HOME": "C:\\Users\\test\\.codex",
        "OPENAI_API_KEY": "sk-proj-secret-openai",
        "CODEX_API_KEY": "sk-codex-secret",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "CLAUDE_API_KEY": "sk-claude-secret",
        "GEMINI_API_KEY": "AIza-secret",
        "GOOGLE_API_KEY": "AIza-google-secret",
        "UNRELATED_SECRET_VAR": "should-not-copy",
    }

    isolated = build_isolated_codex_env(dirty_env, mcp_token="mcp-tok-123")

    assert isolated["PATH"] == "/usr/bin:/bin"
    assert isolated["SYSTEMROOT"] == "C:\\Windows"
    assert isolated["USERPROFILE"] == "C:\\Users\\test"
    assert isolated["CODEX_HOME"] == "C:\\Users\\test\\.codex"
    assert isolated["PYTHONIOENCODING"] == "utf-8"
    assert isolated[ORIN_MCP_TOKEN_ENV] == "mcp-tok-123"

    for forbidden in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "UNRELATED_SECRET_VAR",
    ):
        assert forbidden not in isolated


def test_resolve_codex_binary_prefers_path_and_npm_vendor_exe(tmp_path: Path) -> None:
    bin_dir = tmp_path / "npm_bin"
    bin_dir.mkdir()
    shim = bin_dir / ("codex.cmd" if os.name == "nt" else "codex")
    shim.write_text("@echo off\r\n", encoding="utf-8")
    shim.chmod(0o755)

    env = {"PATH": str(bin_dir), "PATHEXT": ".COM;.EXE;.BAT;.CMD"}
    resolved_shim = resolve_codex_binary(env, windows_apps_dirs=())
    assert resolved_shim is not None
    assert Path(resolved_shim) == shim

    # Now create the sibling npm native vendor PE binary next to the .cmd shim
    if os.name == "nt":
        vendor_exe = (
            bin_dir
            / "node_modules"
            / "@openai"
            / "codex"
            / "node_modules"
            / "@openai"
            / "codex-win32-x64"
            / "vendor"
            / "x86_64-pc-windows-msvc"
            / "bin"
            / "codex.exe"
        )
        vendor_exe.parent.mkdir(parents=True)
        vendor_exe.write_bytes(b"MZ-fake-pe")

        resolved_vendor = resolve_codex_binary(env, windows_apps_dirs=())
        assert resolved_vendor == str(vendor_exe)


def test_resolve_codex_binary_windows_apps_dynamic_without_cache(
    tmp_path: Path,
) -> None:
    empty_path_dir = tmp_path / "empty_path"
    empty_path_dir.mkdir()
    win_apps = tmp_path / "WindowsApps"
    win_apps.mkdir()

    v165 = (
        win_apps
        / "OpenAI.Codex_0.165.0.0_x64__2p2nqsd0c76g0"
        / "app"
        / "resources"
        / "codex.exe"
    )
    v165.parent.mkdir(parents=True)
    v165.write_bytes(b"MZ-165")

    v167 = (
        win_apps
        / "OpenAI.Codex_0.167.0.0_x64__2p2nqsd0c76g0"
        / "app"
        / "resources"
        / "codex.exe"
    )
    v167.parent.mkdir(parents=True)
    v167.write_bytes(b"MZ-167")

    env = {"PATH": str(empty_path_dir)}
    first = resolve_codex_binary(env, windows_apps_dirs=[win_apps])
    assert first == str(v167)

    # Dynamically add 0.170.0.0 in the same test without clearing any cache
    v170 = (
        win_apps
        / "OpenAI.Codex_0.170.0.0_x64__2p2nqsd0c76g0"
        / "app"
        / "resources"
        / "codex.exe"
    )
    v170.parent.mkdir(parents=True)
    v170.write_bytes(b"MZ-170")

    second = resolve_codex_binary(env, windows_apps_dirs=[win_apps])
    assert second == str(v170)


def test_version_gate_and_codex_version_script(tmp_path: Path) -> None:
    assert MIN_CODEX_VERSION == (0, 167, 0)
    assert MAX_CODEX_MAJOR == 1
    assert is_codex_version_supported(None) is False
    assert is_codex_version_supported((0, 166, 9)) is False
    assert is_codex_version_supported((0, 167, 0)) is True
    assert is_codex_version_supported((0, 199, 5)) is True
    assert is_codex_version_supported((1, 0, 0)) is False

    fake_cli = tmp_path / "fake_codex.py"
    fake_cli.write_text(
        'import sys\nprint("codex-cli 0.167.0")\nsys.exit(0)\n',
        encoding="utf-8",
    )
    ver = codex_version(str(fake_cli))
    assert ver == (0, 167, 0)
    assert is_codex_version_supported(ver) is True


def test_encode_codex_user_turn_keeps_line_breaks_utf8() -> None:
    encoded = encode_codex_user_turn(
        [
            {"type": "text", "text": "olá\nmundo"},
            {"type": "text", "text": "segunda linha\r\nfinal\r\r"},
        ]
    )
    assert isinstance(encoded, bytes)
    assert encoded.decode("utf-8") == "olá\nmundo\nsegunda linha\nfinal\n"
    assert encode_codex_user_turn("# Session\n- a\n\n[User]\noi").decode("utf-8") == "# Session\n- a\n\n[User]\noi\n"


def test_encode_codex_user_turn_turns_media_into_a_note_and_never_raises() -> None:
    inline = encode_codex_user_turn(
        [
            {"type": "text", "text": "veja"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        ]
    ).decode("utf-8")
    assert inline == f"veja\n{CODEX_OMITTED_MEDIA_NOTE}\n"
    with_path = encode_codex_user_turn(
        [{"type": "input_image", "image_url": "data:image/png;base64,AAAA", "path": "C:/ws/foto.png"}]
    ).decode("utf-8")
    assert "C:/ws/foto.png" in with_path and "AAAA" not in with_path
    url_only = encode_codex_user_turn([{"type": "image_url", "image_url": {"url": "https://x.test/a.png"}}])
    assert CODEX_OMITTED_MEDIA_NOTE in url_only.decode("utf-8")
    assert encode_codex_user_turn([object()]).decode("utf-8").strip() == CODEX_OMITTED_MEDIA_NOTE  # type: ignore[list-item]


def test_build_codex_command_neutralises_codex_sandbox_notice_but_keeps_sandbox(tmp_path: Path) -> None:
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("prompt", encoding="utf-8")
    req = CliTurnRequest(binary="codex", system_prompt_file=prompt_file, model="gpt-6.1-sol")
    argv = build_codex_command(req)
    pairs = [argv[i + 1] for i, item in enumerate(argv) if item == "-c"]
    for key in (
        "include_permissions_instructions=false",
        "include_environment_context=false",
        "include_apps_instructions=false",
        "include_collaboration_mode_instructions=false",
        "features.unified_exec=false",
        "features.unified_exec_tty=false",
        "features.computer_use=false",
        "features.browser_use=false",
        "features.apps=false",
        "features.plugins=false",
        "features.sleep_tool=false",
        "features.skill_mcp_dependency_install=false",
        "features.skip_host_skill_discovery=true",
        "suppress_unstable_features_warning=true",
    ):
        assert key in pairs
    # The sandbox is what holds back the native apply_patch: it stays read-only, never loosened.
    assert 'sandbox_mode="read-only"' in pairs and 'approval_policy="never"' in pairs
    assert not any("danger-full-access" in item or "workspace-write" in item for item in pairs)
    assert not any(item.startswith(("features.code_mode_host", "multi_agent_v2")) for item in pairs)


@pytest.mark.parametrize(
    "bad_model",
    [
        "-m",
        "--exec",
        "gpt-6.1-sol\n--evil",
        "gpt-6.1-sol\r--evil",
        "gpt-6.1-sol\x00evil",
        "gpt-6.1-sol --flag",
        "openai-codex/-flag",
        "openai-codex/gpt 6",
        "openai-codex/ gpt-6",
        "model;rm -rf",
        "a" * 129,
    ],
)
def test_clean_codex_model_id_rejects_adversarial_inputs(bad_model: str) -> None:
    with pytest.raises(ValueError):
        clean_codex_model_id(bad_model)


def test_resolve_codex_binary_windows_apps_numeric_version_order_and_skips_broken(
    tmp_path: Path,
) -> None:
    empty_path = tmp_path / "empty_bin"
    empty_path.mkdir()
    win_apps = tmp_path / "WindowsApps"
    win_apps.mkdir()

    # 0.9.0.0 vs 0.167.0.0: string sort would pick 0.9.0.0, numeric sort must pick 0.167.0.0
    v9 = (
        win_apps
        / "OpenAI.Codex_0.9.0.0_x64__2p2nqsd0c76g0"
        / "app"
        / "resources"
        / "codex.exe"
    )
    v9.parent.mkdir(parents=True)
    v9.write_bytes(b"MZ-9")

    v167 = (
        win_apps
        / "OpenAI.Codex_0.167.0.0_x64__2p2nqsd0c76g0"
        / "app"
        / "resources"
        / "codex.exe"
    )
    v167.parent.mkdir(parents=True)
    v167.write_bytes(b"MZ-167")

    # Broken 0.999.0.0 folder without codex.exe
    broken = (
        win_apps
        / "OpenAI.Codex_0.999.0.0_x64__2p2nqsd0c76g0"
        / "app"
        / "resources"
    )
    broken.mkdir(parents=True)

    env = {
        "PATH": str(empty_path),
        "ORIN_WINDOWS_APPS_DIR": str(win_apps),
    }
    assert resolve_codex_binary(env) == str(v167)


def _fake_codex_store_install(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A Store install whose package folder is known only through an alias (WindowsApps is not listable)."""
    package = tmp_path / "Program Files" / "WindowsApps" / "OpenAI.Codex_26.1103.7773.0_x64__2p2nqsd0c76g0"
    codex_exe = package / "app" / "resources" / "codex.exe"
    codex_exe.parent.mkdir(parents=True)
    codex_exe.write_bytes(b"MZ-codex")
    alias_root = tmp_path / "Local" / "Microsoft" / "WindowsApps"
    alias = alias_root / "OpenAI.Codex_2p2nqsd0c76g0" / "chatgpt-desktop.exe"
    alias.parent.mkdir(parents=True)
    alias.write_bytes(b"")
    (alias_root / "unrelated.exe").write_bytes(b"")
    return alias_root, alias, codex_exe


def test_codex_package_dirs_from_aliases_reads_alias_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentos.providers.codex_cli import command as codex_command

    alias_root, alias, codex_exe = _fake_codex_store_install(tmp_path)
    package = codex_exe.parent.parent.parent
    targets = {str(alias): str(package / "app" / "ChatGPT.exe")}
    monkeypatch.setattr(codex_command, "_read_app_exec_link_target", lambda path: targets.get(str(path)))

    assert codex_command._codex_package_dirs_from_aliases(alias_root) == [package]
    assert codex_command._codex_package_dirs_from_aliases(tmp_path / "missing") == []


@pytest.mark.skipif(sys.platform != "win32", reason="app execution aliases exist only on Windows")
def test_resolve_codex_binary_finds_store_install_through_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentos.providers.codex_cli import command as codex_command

    alias_root, alias, codex_exe = _fake_codex_store_install(tmp_path)
    package = codex_exe.parent.parent.parent
    monkeypatch.setattr(
        codex_command,
        "_read_app_exec_link_target",
        lambda path: str(package / "app" / "ChatGPT.exe") if str(path) == str(alias) else None,
    )
    empty_path = tmp_path / "empty_bin"
    empty_path.mkdir()
    env = {"PATH": str(empty_path), "LOCALAPPDATA": str(tmp_path / "Local")}
    assert resolve_codex_binary(env) == str(codex_exe)


@pytest.mark.parametrize(
    "bad_resume",
    [
        "",
        "   ",
        "-flag",
        "--ephemeral",
        "session id with spaces",
        "session\nid",
        "session\rid",
        "session\x00id",
        "session;evil",
    ],
)
def test_build_codex_command_rejects_invalid_resume_session_id(
    tmp_path: Path, bad_resume: str
) -> None:
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("prompt", encoding="utf-8")
    req = CliTurnRequest(
        binary="codex",
        system_prompt_file=prompt_file,
        model="gpt-6.1-sol",
        resume_session_id=bad_resume,
    )
    with pytest.raises(ValueError):
        build_codex_command(req)


@pytest.mark.parametrize(
    "bad_url,bad_token,bad_server",
    [
        ("", "tok", "orin"),
        ("http://127.0.0.1/mcp\nevil", "tok", "orin"),
        ("http://127.0.0.1/mcp\x00", "tok", "orin"),
        ("http://127.0.0.1/mcp", "", "orin"),
        ("http://127.0.0.1/mcp", "tok\nevil", "orin"),
        ("http://127.0.0.1/mcp", "tok", "orin.bad"),
        ("http://127.0.0.1/mcp", "tok", "orin space"),
        ("http://127.0.0.1/mcp", "tok", ""),
    ],
)
def test_wire_codex_tool_server_rejects_adversarial_inputs(
    bad_url: str, bad_token: str, bad_server: str
) -> None:
    class _BadHandle:
        url = bad_url
        token = bad_token

    with pytest.raises(ValueError):
        wire_codex_tool_server(_BadHandle(), server_name=bad_server)


def test_redact_codex_argv_redacts_all_sensitive_variants() -> None:
    raw = [
        "codex",
        "exec",
        "-c",
        'model_instructions_file="C:/tmp/secret_prompt.md"',
        "-c",
        'mcp_servers.orin.bearer_token_env_var="ORIN_MCP_TOKEN"',
        "-c",
        "mcp_servers.orin.api_key=sk-12345",
        "--config",
        "auth.password=hunter2",
        "-c=my_secret_value=abc",
        "--config=authorization_header=Bearer xyz",
        "-c",
        'mcp_servers.orin.url="http://127.0.0.1:43123/mcp"',
        "-c",
        "features.shell_tool=false",
    ]
    redacted = redact_codex_argv(raw)
    assert redacted == [
        "codex",
        "exec",
        "-c",
        "<redacted>",
        "-c",
        "<redacted>",
        "-c",
        "<redacted>",
        "--config",
        "<redacted>",
        "-c=<redacted>",
        "--config=<redacted>",
        "-c",
        'mcp_servers.orin.url="http://127.0.0.1:43123/mcp"',
        "-c",
        "features.shell_tool=false",
    ]


def test_build_isolated_codex_env_rejects_control_chars_in_mcp_token() -> None:
    with pytest.raises(ValueError):
        build_isolated_codex_env({"PATH": "/bin"}, mcp_token="bad\ntoken")


def test_redact_codex_argv_handles_chained_flags_and_attached_c_values() -> None:
    raw = [
        "codex",
        "exec",
        "-c",
        "-c",
        "mcp_servers.orin.token=SECRET_TOKEN_VALUE",
        "--mcp-config",
        "--system-prompt-file",
        "/tmp/secret_prompt.txt",
        "-cmcp_servers.orin.bearer_token_env_var=ORIN_MCP_TOKEN",
        '-cmodel_instructions_file="/tmp/secret_prompt.txt"',
    ]
    redacted = redact_codex_argv(raw)
    joined = " ".join(redacted)
    assert "SECRET_TOKEN_VALUE" not in joined
    assert "/tmp/secret_prompt.txt" not in joined
    assert "ORIN_MCP_TOKEN" not in joined
    assert redacted == [
        "codex",
        "exec",
        "-c",
        "-c",
        "<redacted>",
        "--mcp-config",
        "--system-prompt-file",
        "<redacted>",
        "-c<redacted>",
        "-c<redacted>",
    ]


@pytest.mark.parametrize(
    "bad_wiring_argv",
    [
        ("-c", "features.shell_tool=true"),
        ("-c", 'sandbox_mode="danger-full-access"'),
        ("-c", 'approval_policy="on-request"'),
        ("-c", 'web_search="enabled"'),
        ("-c", "features.view_image=true"),
        ("-c", "features.image_generation=true"),
        ("-c", "include_permissions_instructions=true"),
        ("-c", "include_environment_context=true"),
        ("-c", "include_apps_instructions=true"),
        ("-c", "include_collaboration_mode_instructions=true"),
        ("-c", "features.unified_exec=true"),
        ("-c", "features.computer_use=true"),
        ("-c", "features.skip_host_skill_discovery=false"),
        ("-c", 'model_instructions_file="/etc/passwd"'),
        ("--dangerously-bypass-approvals-and-sandbox",),
        ("--full-auto",),
    ],
)
def test_build_codex_command_rejects_wiring_overriding_locked_keys_or_raw_flags(
    tmp_path: Path, bad_wiring_argv: tuple[str, ...]
) -> None:
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("prompt", encoding="utf-8")
    req = CliTurnRequest(
        binary="codex",
        system_prompt_file=prompt_file,
        model="gpt-6.1-sol",
        tool_server_wiring=ToolServerWiring(argv=bad_wiring_argv),
    )
    with pytest.raises(ValueError):
        build_codex_command(req)


def test_resolve_codex_binary_isolated_env_does_not_leak_host_windows_apps(
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty_bin"
    empty_dir.mkdir()
    assert resolve_codex_binary({"PATH": str(empty_dir)}) is None


