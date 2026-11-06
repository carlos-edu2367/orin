"""Red team round 4: claude -p isolation (apiKeySource, init, cwd, flags, env, test guard)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agentos.providers.claude_cli.command import (
    FORBIDDEN_FLAGS,
    build_claude_command,
    build_isolated_claude_env,
    ensure_isolated_cwd,
    resolve_claude_binary,
)
from agentos.providers.claude_cli.errors import ProviderTransportError
from agentos.providers.claude_cli.provider import DEFAULT_CLAUDE_CLI_BRIDGE_URL, ClaudeCliProvider
from agentos.providers.claude_cli.stream import StreamJsonParser
from tests.unit.providers.claude_cli.fakes import FakeClaudePopen


def _init(**extra: Any) -> str:
    packet = {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-opus-5-5"}
    packet.update(extra)
    return json.dumps(packet)


def _result(text: str = "OK") -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                       "stop_reason": "end_turn", "num_turns": 1, "duration_ms": 1, "session_id": "s1",
                       "usage": {"input_tokens": 1, "output_tokens": 1}})


def _text_provider(lines: list[str]) -> ClaudeCliProvider:
    return ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=lambda cmd, **kw: FakeClaudePopen(cmd, ndjson_lines=lines, env=kw.get("env")),
    )


def _visible_text(provider: ClaudeCliProvider) -> str:
    out: list[str] = []
    try:
        for item in provider.stream({"messages": [{"role": "user", "content": "Oi"}]}):
            if getattr(item, "text", None):
                out.append(str(item.text))
    finally:
        provider.close()
    return "".join(out)


def test_parser_does_not_turn_a_missing_api_key_source_into_none() -> None:
    parser = StreamJsonParser()
    parser.feed_line(_init(mcp_servers=[{"name": "orin", "status": "connected"}]))
    assert parser.init is not None
    assert parser.init.api_key_source.lower() != "none", "an init without apiKeySource passes both isolation checks"


def test_text_path_aborts_when_init_has_no_api_key_source() -> None:
    provider = _text_provider([_init(), _result("Resposta paga?")])
    with pytest.raises(ProviderTransportError, match="apiKeySource"):
        _visible_text(provider)


def test_text_path_aborts_when_the_cli_sends_a_result_without_any_init() -> None:
    """The fix guards TEXT/THINKING/TOOL_USE_END before init, but a bare `result` is shown as the answer."""
    provider = _text_provider([_result("Texto sem init")])
    with pytest.raises(ProviderTransportError):
        text = _visible_text(provider)
        pytest.fail(f"user-visible text without init check: {text!r}")


@pytest.mark.parametrize("source", ["ANTHROPIC_API_KEY", " ANTHROPIC_API_KEY ", "apiKeyHelper", "", "null-ish", "NONE?"])
def test_text_path_aborts_on_any_other_api_key_source(source: str) -> None:
    provider = _text_provider([_init(apiKeySource=source), _result("x")])
    with pytest.raises(ProviderTransportError, match="apiKeySource"):
        _visible_text(provider)


def test_text_path_aborts_on_null_api_key_source() -> None:
    provider = _text_provider([_init(apiKeySource=None), _result("x")])
    with pytest.raises(ProviderTransportError, match="apiKeySource"):
        _visible_text(provider)


def test_forbidden_flags_never_built_and_session_kwargs_refused(tmp_path: Path) -> None:
    cmd = build_claude_command(binary="claude", system_prompt_file=tmp_path / "p.txt", mcp_config_file=tmp_path / "m.json",
                               model="claude-opus-5-5", fallback_model="claude-sonnet-5-5", effort="max", max_turns=3)
    assert not FORBIDDEN_FLAGS & set(cmd)
    assert "--no-session-persistence" in cmd and "--restricted" in cmd and "--strict-mcp-config" in cmd
    for kw in ({"session_id": "x"}, {"resume_session_id": "x"}, {"resume": "x"}, {"persist_session": True}):
        with pytest.raises(ValueError):
            build_claude_command(binary="claude", system_prompt_file=tmp_path / "p.txt", **kw)
    with pytest.raises(ValueError):
        build_claude_command(binary="claude", system_prompt_file=tmp_path / "p.txt", model="--resume")


def test_env_scrub_is_case_insensitive_and_covers_prefixes() -> None:
    env = build_isolated_claude_env({
        "anthropic_api_key": "k", "Anthropic_Auth_Token": "t", "ANTHROPIC_BASE_URL": "u", "CLAUDE_CONFIG_DIR": "c",
        "claude_code_use_bedrock": "1", "CLAUDECODE": "1", "MCP_TOOL_TIMEOUT": "1", "PATH": "/bin",
    })
    assert set(env) >= {"PATH", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"}
    lowered = {k.lower() for k in env}
    for bad in ("anthropic_api_key", "anthropic_auth_token", "anthropic_base_url", "claude_config_dir",
                "claude_code_use_bedrock", "claudecode", "mcp_tool_timeout"):
        assert bad not in lowered


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_isolated_cwd_does_not_keep_a_planted_dot_claude_it_failed_to_remove(tmp_path: Path) -> None:
    """ensure_isolated_cwd deletes `.claude` with rmtree(ignore_errors=True). A `.claude` it cannot delete (here a
    symlink; on a shared /tmp, a directory owned by another user) silently stays in the CLI's cwd."""
    base = tmp_path / "orin-claude-cli-cwd"
    base.mkdir()
    planted = tmp_path / "planted"
    (planted).mkdir()
    (planted / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
    (base / ".claude").symlink_to(planted, target_is_directory=True)
    ensure_isolated_cwd(base)
    assert not (base / ".claude").exists(), "planted .claude survives in the CLI cwd"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_isolated_cwd_never_deletes_outside_itself_through_a_symlinked_root(tmp_path: Path) -> None:
    """The text path uses the fixed shared name <tmp>/orin-claude-cli-cwd. If that name is a symlink (to the
    home folder, say), ensure_isolated_cwd follows it and rmtree's the target's `.claude` (~/.claude: login)."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    (home / "CLAUDE.md").write_text("mine", encoding="utf-8")
    link = tmp_path / "orin-claude-cli-cwd"
    link.symlink_to(home, target_is_directory=True)
    try:
        ensure_isolated_cwd(link)
    except Exception:
        pass  # refusing the symlinked root is an acceptable fix
    assert (home / ".claude" / ".credentials.json").exists(), "deleted the symlink target's .claude"
    assert (home / "CLAUDE.md").exists(), "deleted the symlink target's CLAUDE.md"


def test_real_claude_guard_covers_the_binary_production_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tests/conftest.py refuses only the realpath of `which claude|claude.exe|claude.cmd`. resolve_claude_binary
    prefers the npm sibling `<bin>/node_modules/@anthropic-ai/claude-code/bin/claude.exe` (the Windows npm layout:
    PATH has npm\\claude.cmd, the exe is not on PATH), which the guard never sees. Nothing is spawned here."""
    from tests.conftest import _real_claude_binaries

    bin_dir = tmp_path / "npm"
    bin_dir.mkdir()
    # Windows only finds the npm shim through PATHEXT, so it needs the .cmd suffix there.
    shim = bin_dir / ("claude.cmd" if os.name == "nt" else "claude")
    shim.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    shim.chmod(0o755)
    exe = bin_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("not a real binary", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir))
    # The resolver checks these install folders before PATH; on a machine with the real CLI they would win.
    for name in ("APPDATA", "USERPROFILE", "LOCALAPPDATA", "HOME"):
        monkeypatch.delenv(name, raising=False)
    guarded = _real_claude_binaries()
    resolved = resolve_claude_binary({"PATH": str(bin_dir)})
    assert resolved is not None
    assert os.path.normcase(os.path.realpath(resolved)) in guarded, (resolved, sorted(guarded))


def test_api_key_source_security_abort_is_not_retried_by_the_classic_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The text path raises ProviderTransportError('Security abort: ... apiKeySource=...') with no status_code.
    retry.is_retryable() treats status None as retryable, so the classic loop spawns `claude -p` again (and the CLI
    may already have sent the request on the API key before Orin reads init and kills it)."""
    from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
    from agentos.agentic.workspace import ConversationWorkspace
    from tests.unit.agentic.test_native_turn import FakeStore, _make_toolset

    spawns: list[list[str]] = []

    def _popen(cmd: list[str], **kw: Any) -> FakeClaudePopen:
        spawns.append(list(cmd))
        return FakeClaudePopen(cmd, ndjson_lines=[_init(apiKeySource="ANTHROPIC_API_KEY"), _result("pago")], env=kw.get("env"))

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL, model="claude-opus-5-5", binary="C:/fake/claude.exe", popen_factory=_popen,
    )
    store = FakeStore()
    store.turn["conversation_mode"] = "chat"
    store.turn["code_mode"] = "chat"
    rt = AgenticTurnRuntime(
        store=store, provider=provider, toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=False),
        limits=AgenticLimits(max_iterations=5, max_actions=5, max_provider_retries=3, context_window_tokens=1_000_000),
    )
    # Only skips the backoff sleep: the retry decision (is_retryable) is made before _retry_wait is called.
    monkeypatch.setattr(rt, "_retry_wait", lambda *a, **k: "retry", raising=False)
    try:
        out = rt.run("t1", turn=store.turn)
    finally:
        provider.close()
    assert out.state == "failed", out
    assert len(spawns) == 1, f"claude -p spawned {len(spawns)} times after an apiKeySource security abort"


def test_native_api_key_source_abort_fails_the_turn_instead_of_respawning_on_the_text_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Native init with apiKeySource != none raises NativeLoopUnavailable(401). The runner treats it like any
    'native unavailable before output' and returns None, so the classic loop spawns the text path with the very same
    credentials (which aborts again and, being status-less, is retried)."""
    import agentos.providers.claude_cli.provider as provider_mod
    from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
    from agentos.agentic.workspace import ConversationWorkspace
    from tests.unit.agentic.test_native_turn import FakeStore, _make_toolset

    monkeypatch.setattr(provider_mod, "claude_cli_version", lambda *a, **k: (2, 1, 309))
    spawns: list[str] = []

    def _popen(cmd: list[str], **kw: Any) -> FakeClaudePopen:
        spawns.append("native" if "--mcp-config" in cmd else "text")
        return FakeClaudePopen(
            cmd,
            ndjson_lines=[_init(apiKeySource="ANTHROPIC_API_KEY", mcp_servers=[{"name": "orin", "status": "connected"}]), _result("pago")],
            env=kw.get("env"),
        )

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL, model="claude-opus-5-5", binary=str(tmp_path / "claude-fake-bin"), popen_factory=_popen,
    )
    (tmp_path / "claude-fake-bin").write_text("x", encoding="utf-8")
    store = FakeStore()
    rt = AgenticTurnRuntime(
        store=store, provider=provider, toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
        limits=AgenticLimits(max_iterations=5, max_actions=5, max_provider_retries=0, context_window_tokens=1_000_000),
    )
    try:
        out = rt.run("t1", turn=store.turn)
    finally:
        provider.close()
    assert "native" in spawns, spawns
    assert out.state == "failed", out
    assert spawns == ["native"], f"spawns after a native apiKeySource abort: {spawns}"


def test_native_cwd_is_outside_any_git_checkout_in_the_development_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_isolated_cwd promises 'an empty directory outside any project repository'. In the development layout
    (`uv run orin` from a checkout, the Task 14 replay setup) orin_paths().data is <repo>/data, so the native
    claude -p runs in <repo>/data/claude-cli/workdir and the CLI walks up into the repository (CLAUDE.md, git)."""
    from agentos.agentic.native_turn import _resolve_native_cwd
    from agentos.installation.paths import find_repository_root

    for name in ("ORIN_HOME", "ORIN_DATA_DIR", "ORIN_CONFIG_DIR", "ORIN_LOGS_DIR", "ORIN_CACHE_DIR", "ORIN_RUN_DIR"):
        monkeypatch.delenv(name, raising=False)
    if find_repository_root() is None:
        pytest.skip("not running from a checkout")
    from agentos.installation.paths import reset_cached_paths

    reset_cached_paths()
    native_cwd = _resolve_native_cwd()
    git_ancestors = [p for p in native_cwd.parents if (p / ".git").exists()]
    assert not git_ancestors, f"native cwd {native_cwd} is inside the git checkout {git_ancestors[0]}"
