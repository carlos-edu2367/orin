from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentos.quota.active_probe import (
    ACTIVE_SOURCES,
    AgyUsageCommandProbe,
    ClaudeUsageCommandProbe,
    CodexAppServerProbe,
    CommandResult,
    ProbeError,
    clean_agy_group_name,
    codex_rate_limits_rpc,
    parse_agy_usage,
    parse_claude_usage_text,
    parse_codex_rate_limits,
)
from agentos.quota.claude_account import ClaudeAccountReader
from agentos.quota.claude_reader import ClaudeUsageCacheReader

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 23, 12, 30, 0, tzinfo=UTC)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeRunner:
    def __init__(self, stdout: str = "", *, timed_out: bool = False, error: str | None = None) -> None:
        self.stdout = stdout
        self.timed_out = timed_out
        self.error = error
        self.calls: list[tuple[list[str], str, float]] = []

    def __call__(self, argv, env, cwd, timeout) -> CommandResult:
        self.calls.append((list(argv), cwd, timeout))
        return CommandResult(0, self.stdout, "", self.timed_out, self.error)


def _claude_probe(tmp_path: Path, runner: FakeRunner, *, claude_json: str | None = None) -> ClaudeUsageCommandProbe:
    path = tmp_path / "claude.json"
    path.write_text(claude_json or "{}", encoding="utf-8")
    return ClaudeUsageCommandProbe(
        reader=ClaudeUsageCacheReader(ClaudeAccountReader(path)),
        runner=runner,
        resolve_binary=lambda: "claude-bin",
        env_factory=lambda: {},
        workdir=lambda: str(tmp_path),
        clock=lambda: NOW,
    )


def _claude_cache(fetched_at: datetime, *, week: int = 38) -> str:
    return json.dumps(
        {
            "cachedUsageUtilization": {
                "fetchedAtMs": int(fetched_at.timestamp() * 1000),
                "utilization": {
                    "five_hour": None,
                    "seven_day": {"utilization": week, "resets_at": "2026-11-24T17:59:59+00:00"},
                    "limits": [
                        {"kind": "weekly_all", "percent": week, "severity": "normal", "resets_at": "2026-11-24T17:59:59+00:00"},
                        {
                            "kind": "weekly_scoped",
                            "percent": 3,
                            "severity": "normal",
                            "resets_at": "2026-11-24T17:59:59+00:00",
                            "scope": {"model": {"display_name": "Fable"}},
                        },
                    ],
                },
            }
        }
    )


# --- Claude -----------------------------------------------------------------------------------


def test_claude_probe_uses_the_refreshed_cache_and_the_cheap_non_persistent_command(tmp_path: Path):
    runner = FakeRunner((FIXTURES / "claude-usage-command.json").read_text(encoding="utf-8"))
    probe = _claude_probe(tmp_path, runner, claude_json=_claude_cache(NOW))

    snaps = probe.read()

    argv, cwd, timeout = runner.calls[0]
    assert argv[:4] == ["claude-bin", "-p", "/usage", "--output-format"]
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--model") + 1] == "haiku"
    assert cwd == str(tmp_path)
    assert timeout == 30.0
    by_key = {(s.window_kind, s.scope_model): s for s in snaps}
    assert by_key[("seven_day", None)].used_percent == 38.0
    assert by_key[("weekly_scoped", "Fable")].used_percent == 3.0
    assert {s.source for s in snaps} == {"claude_usage_command"}
    assert "claude_usage_command" in ACTIVE_SOURCES


def test_claude_probe_falls_back_to_the_command_text_when_the_cache_stays_stale(tmp_path: Path):
    stale = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    runner = FakeRunner((FIXTURES / "claude-usage-command.json").read_text(encoding="utf-8"))
    probe = _claude_probe(tmp_path, runner, claude_json=_claude_cache(stale, week=77))

    snaps = probe.read()

    by_key = {(s.window_kind, s.scope_model): s for s in snaps}
    assert by_key[("seven_day", None)].used_percent == 38.0
    assert by_key[("weekly_scoped", "Fable")].used_percent == 3.0
    # "Nov 24, 2:59pm (America/Sao_Paulo)" -> 17:59 UTC
    assert by_key[("seven_day", None)].resets_at == datetime(2026, 11, 24, 17, 59, tzinfo=UTC)
    assert all(s.observed_at == NOW for s in snaps)


def test_parse_claude_usage_text_reads_session_week_and_scoped_lines():
    text = (
        "Current session: 12% used · resets 3pm (America/Sao_Paulo)\n"
        "Current week (all models): 38% used · resets Nov 24, 2:59pm (America/Sao_Paulo)\n"
        "Current week (Fable): 3% used\n"
        "Extra usage: not enabled"
    )
    snaps = {(s.window_kind, s.scope_model): s for s in parse_claude_usage_text(text, now=NOW)}
    assert snaps[("five_hour", None)].used_percent == 12.0
    assert snaps[("five_hour", None)].resets_at == datetime(2026, 11, 23, 18, 0, tzinfo=UTC)
    assert snaps[("seven_day", None)].used_percent == 38.0
    assert snaps[("weekly_scoped", "Fable")].resets_at is None
    assert len(snaps) == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"num_turns": 1, "local_command": "usage", "total_cost_usd": 0},
        {"num_turns": 0, "total_cost_usd": 0, "result": "hi"},
        {"num_turns": 0, "local_command": "usage", "total_cost_usd": 0.012},
        {"num_turns": 0, "local_command": "usage"},
    ],
)
def test_claude_probe_turns_itself_off_when_the_command_was_not_local(tmp_path: Path, payload: dict, caplog):
    runner = FakeRunner(json.dumps({**payload, "is_error": False}))
    probe = _claude_probe(tmp_path, runner, claude_json=_claude_cache(NOW))

    with caplog.at_level("WARNING", logger="agentos.quota.active_probe"):
        assert probe.read() == []

    assert probe.disabled_reason is not None
    assert any("desligada" in record.message for record in caplog.records)
    # Off until restart: no second spawn.
    assert probe.read() == []
    assert len(runner.calls) == 1


def test_claude_probe_timeout_is_transient_not_a_trip(tmp_path: Path):
    runner = FakeRunner("", timed_out=True)
    probe = _claude_probe(tmp_path, runner, claude_json=_claude_cache(NOW))

    assert probe.read() == []
    assert probe.disabled_reason is None


def test_probe_is_unavailable_without_binary_or_connection(tmp_path: Path):
    runner = FakeRunner("")
    probe = ClaudeUsageCommandProbe(
        reader=ClaudeUsageCacheReader(ClaudeAccountReader(tmp_path / "missing.json")),
        runner=runner,
        resolve_binary=lambda: "claude-bin",
    )
    assert probe.available() is False  # ~/.claude.json absent: not connected
    assert probe.read() == []
    assert runner.calls == []

    no_binary = CodexAppServerProbe(resolve_binary=lambda: None, is_connected=lambda: True)
    assert no_binary.available() is False
    assert no_binary.read() == []


# --- Codex ------------------------------------------------------------------------------------


def test_parse_codex_rate_limits_maps_windows_and_scoped_limits():
    snaps = parse_codex_rate_limits(_fixture("codex-rate-limits-read.json"), observed_at=NOW)

    by_key = {(s.window_kind, s.scope_model): s for s in snaps}
    assert by_key[("five_hour", None)].used_percent == 0.0
    assert by_key[("seven_day", None)].used_percent == 4.0
    assert by_key[("five_hour", None)].resets_at == datetime.fromtimestamp(1795454519, UTC)
    assert by_key[("seven_day", "GPT-5.3-Codex-Spark")].used_percent == 6.0
    assert by_key[("five_hour", "GPT-5.3-Codex-Spark")].used_percent == 12.0
    assert {s.source for s in snaps} == {"codex_app_server"}
    assert {s.status for s in snaps} == {"allowed"}


def test_parse_codex_rate_limits_marks_rejected():
    payload = _fixture("codex-rate-limits-read.json")
    payload["ordinaryUsageAllowed"] = False
    snaps = parse_codex_rate_limits(payload, observed_at=NOW)
    main = [s for s in snaps if s.scope_model is None]
    assert main and {s.status for s in main} == {"rejected"}

    payload = _fixture("codex-rate-limits-read.json")
    payload["rateLimitsByLimitId"]["codex_bengalfox"]["rateLimitReachedType"] = "primary"
    snaps = parse_codex_rate_limits(payload, observed_at=NOW)
    assert {s.status for s in snaps if s.scope_model} == {"rejected"}
    assert {s.status for s in snaps if s.scope_model is None} == {"allowed"}


def test_codex_probe_returns_rpc_result_as_snapshots():
    calls: list[list[str]] = []

    def rpc(argv, env, cwd, timeout):
        calls.append(list(argv))
        return _fixture("codex-rate-limits-read.json")

    probe = CodexAppServerProbe(
        rpc=rpc,
        resolve_binary=lambda: "codex-bin",
        is_connected=lambda: True,
        env_factory=lambda: {},
        workdir=lambda: ".",
        clock=lambda: NOW,
    )
    snaps = probe.read()
    assert calls == [["codex-bin", "app-server"]]
    assert len(snaps) == 4
    assert all(s.observed_at == NOW for s in snaps)


def test_codex_rpc_talks_json_rpc_to_a_child_process(tmp_path: Path):
    server = FIXTURES / "fake_codex_app_server.py"
    result = codex_rate_limits_rpc(
        [sys.executable, str(server), str(FIXTURES / "codex-rate-limits-read.json")],
        {"PATH": ""},
        str(tmp_path),
        15.0,
    )
    assert result["rateLimits"]["secondary"]["usedPercent"] == 4


def test_codex_rpc_times_out_and_kills_a_silent_server(tmp_path: Path):
    server = FIXTURES / "fake_codex_app_server.py"
    started = time.monotonic()
    with pytest.raises(ProbeError):
        codex_rate_limits_rpc(
            [sys.executable, str(server), str(FIXTURES / "codex-rate-limits-read.json"), "silent"],
            {"PATH": ""},
            str(tmp_path),
            1.0,
        )
    assert time.monotonic() - started < 10.0


def test_codex_probe_swallows_rpc_failures():
    def rpc(argv, env, cwd, timeout):
        raise ProbeError("codex app-server timed out")

    probe = CodexAppServerProbe(
        rpc=rpc, resolve_binary=lambda: "codex-bin", is_connected=lambda: True, env_factory=lambda: {}, workdir=lambda: "."
    )
    assert probe.read() == []
    assert probe.disabled_reason is None


# --- Antigravity ------------------------------------------------------------------------------


def test_parse_agy_usage_makes_one_scope_per_model_group():
    snaps = parse_agy_usage(_fixture("agy-usage-command.json"), observed_at=NOW)

    by_key = {(s.scope_model, s.window_kind): s for s in snaps}
    assert set(by_key) == {
        ("Gemini", "five_hour"),
        ("Gemini", "seven_day"),
        ("Claude e GPT", "five_hour"),
        ("Claude e GPT", "seven_day"),
    }
    assert by_key[("Gemini", "seven_day")].used_percent == pytest.approx(0.05, abs=0.001)
    assert by_key[("Claude e GPT", "seven_day")].used_percent == 9.0
    assert by_key[("Claude e GPT", "five_hour")].used_percent == 0.0
    assert by_key[("Gemini", "five_hour")].resets_at == datetime(2026, 11, 23, 17, 20, 26, tzinfo=UTC)
    models = by_key[("Claude e GPT", "seven_day")].raw["models"]
    assert models == ["Claude Opus 5.5", "Claude Sonnet 5.5", "GPT-OSS 120B"]
    assert "Gemini 3.8 Flash" in by_key[("Gemini", "five_hour")].raw["models"]
    assert by_key[("Gemini", "five_hour")].raw["group"] == "Gemini Models"
    assert {s.source for s in snaps} == {"agy_usage_command"}


def test_clean_agy_group_name():
    assert clean_agy_group_name("Gemini Models") == "Gemini"
    assert clean_agy_group_name("Claude and GPT models") == "Claude e GPT"


def test_agy_probe_runs_the_cheap_local_command(tmp_path: Path):
    runner = FakeRunner((FIXTURES / "agy-usage-command.json").read_text(encoding="utf-8"))
    probe = AgyUsageCommandProbe(
        runner=runner,
        resolve_binary=lambda: "agy-bin",
        is_connected=lambda: True,
        env_factory=lambda: {},
        workdir=lambda: str(tmp_path),
        clock=lambda: NOW,
    )
    snaps = probe.read()
    argv, cwd, _timeout = runner.calls[0]
    assert argv == ["agy-bin", "-p", "/usage", "--output-format", "json", "--model", "gemini-3.8-flash-low"]
    assert cwd == str(tmp_path)
    assert len(snaps) == 4


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(num_turns=2),
        lambda p: p.pop("command"),
        lambda p: p["command"].update(name="other"),
        lambda p: p.update(total_cost_usd=0.5),
    ],
)
def test_agy_probe_turns_itself_off_when_the_command_was_not_local(tmp_path: Path, mutate):
    payload = _fixture("agy-usage-command.json")
    mutate(payload)
    runner = FakeRunner(json.dumps(payload))
    probe = AgyUsageCommandProbe(
        runner=runner, resolve_binary=lambda: "agy-bin", is_connected=lambda: True, env_factory=lambda: {}, workdir=lambda: "."
    )

    assert probe.read() == []
    assert probe.disabled_reason is not None
    assert probe.read() == []
    assert len(runner.calls) == 1


def test_agy_probe_timeout_does_not_trip(tmp_path: Path):
    probe = AgyUsageCommandProbe(
        runner=FakeRunner("", timed_out=True),
        resolve_binary=lambda: "agy-bin",
        is_connected=lambda: True,
        env_factory=lambda: {},
        workdir=lambda: ".",
    )
    assert probe.read() == []
    assert probe.disabled_reason is None
