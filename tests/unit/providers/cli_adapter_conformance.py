from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pytest

from agentos.providers.cli_adapter import (
    CliAgentAdapter,
    CliErrorKind,
    CliEvent,
    CliEventKind,
    CliStreamTranslator,
    CliTurnRequest,
)


@dataclass(frozen=True)
class StreamScenario:
    lines: Sequence[str]
    expected_text: str
    expected_tool_calls: tuple[tuple[str, dict[str, Any]], ...] = ()
    expected_session_id: str | None = None


@dataclass(frozen=True)
class ErrorScenario:
    lines: Sequence[str]
    expected_kind: CliErrorKind
    expected_retryable: bool


@dataclass(frozen=True)
class AdapterFixtures:
    adapter: CliAgentAdapter
    simple_text: StreamScenario
    tool_call: StreamScenario
    error_scenarios: Sequence[ErrorScenario]


class FakeToolServerHandle:
    url = "http://127.0.0.1:43123/mcp"
    token = "secret-bearer-token-xyz"
    server_name = "orin"

    def mcp_config_json(self) -> str:
        import json

        return json.dumps(
            {
                "mcpServers": {
                    self.server_name: {
                        "type": "http",
                        "url": self.url,
                        "headers": {"Authorization": f"Bearer {self.token}"},
                    }
                }
            }
        )


def load_jsonl(path: Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _run(
    translator: CliStreamTranslator, lines: Sequence[str]
) -> list[CliEvent]:
    events: list[CliEvent] = []
    for line in lines:
        events.extend(translator.feed_line(line))
    events.extend(translator.flush_pending())
    return events


class CliAdapterConformance:
    """Subclasses implement `adapter_fixtures` (fixture or method)."""

    def get_fixtures(self) -> AdapterFixtures:
        return self.adapter_fixtures()  # type: ignore[attr-defined]

    def test_implements_protocol(self) -> None:
        fx = self.get_fixtures()
        assert isinstance(fx.adapter, CliAgentAdapter)
        assert fx.adapter.name
        assert fx.adapter.models()

    def test_command_redacts_secrets_and_omits_prompt_text(
        self, tmp_path: Path
    ) -> None:
        fx = self.get_fixtures()
        sys_file = tmp_path / "sys.txt"
        sys_file.write_text("TOP_SECRET_SYSTEM_PROMPT", encoding="utf-8")
        cfg = fx.adapter.write_tool_server_config(
            FakeToolServerHandle(), tmp_path
        )
        req = CliTurnRequest(
            binary="/usr/bin/fake-cli",
            system_prompt_file=sys_file,
            model=fx.adapter.models()[0].id,
            max_turns=4,
            tool_server_config_file=cfg,
        )
        argv = fx.adapter.build_command(req)
        assert "TOP_SECRET_SYSTEM_PROMPT" not in " ".join(argv)
        assert FakeToolServerHandle.token not in " ".join(argv)
        logged = fx.adapter.loggable_command(argv)
        assert str(sys_file) not in " ".join(logged)
        assert str(cfg) not in " ".join(logged)

    def test_environment_strips_cloud_api_keys(self) -> None:
        fx = self.get_fixtures()
        dirty = {
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-ant-leak",
            "OPENAI_API_KEY": "sk-openai-leak",
            "CLAUDE_API_KEY": "sk-claude-leak",
        }
        clean = fx.adapter.environment(
            dirty, max_output_tokens=4096, tool_timeout_ms=60_000
        )
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_API_KEY"):
            assert key not in clean

    def test_stream_translates_simple_text(self) -> None:
        fx = self.get_fixtures()
        tr = fx.adapter.stream_translator()
        events = _run(tr, fx.simple_text.lines)
        text = "".join(
            e.text or "" for e in events if e.kind == CliEventKind.TEXT_DELTA
        )
        assert text == fx.simple_text.expected_text
        assert tr.result is not None and not tr.result.is_error
        if fx.simple_text.expected_session_id:
            assert tr.init is not None
            assert tr.init.session_id == fx.simple_text.expected_session_id

    def test_stream_translates_tool_call_with_deltas_before_end(self) -> None:
        fx = self.get_fixtures()
        tr = fx.adapter.stream_translator()
        events = _run(tr, fx.tool_call.lines)
        kinds = [e.kind for e in events]
        assert CliEventKind.TOOL_USE_START in kinds
        assert CliEventKind.TOOL_USE_END in kinds
        assert kinds.index(CliEventKind.TOOL_USE_START) < kinds.index(
            CliEventKind.TOOL_USE_END
        )
        completed = [
            (e.tool_name, dict(e.tool_input or {}))
            for e in events
            if e.kind == CliEventKind.TOOL_USE_END
        ]
        assert tuple(completed) == fx.tool_call.expected_tool_calls

    def test_stream_ignores_malformed_lines(self) -> None:
        fx = self.get_fixtures()
        tr = fx.adapter.stream_translator()
        assert tr.feed_line("") == []
        assert tr.feed_line("not-json {{{") == []
        assert tr.feed_line("42") == []

    def test_error_classification_matches_scenarios(self) -> None:
        fx = self.get_fixtures()
        for scenario in fx.error_scenarios:
            tr = fx.adapter.stream_translator()
            _run(tr, scenario.lines)
            assert tr.result is not None and tr.result.is_error
            info = fx.adapter.classify_error(tr.result, tr.rate_limit)
            assert info.kind == scenario.expected_kind
            assert info.retryable is scenario.expected_retryable
            assert info.user_message

    def test_context_window_known_and_unknown_models(self) -> None:
        fx = self.get_fixtures()
        first = fx.adapter.models()[0]
        assert fx.adapter.context_window(first.id) == first.context_window
        assert fx.adapter.context_window("unknown-model-xyz") > 0

    @pytest.mark.parametrize(
        "version,expected",
        [
            (None, False),
            ((1, 0, 0), False),
        ],
    )
    def test_version_gate_rejects_none_and_ancient(
        self, version: tuple[int, int, int] | None, expected: bool
    ) -> None:
        fx = self.get_fixtures()
        assert fx.adapter.version_supported(version) is expected
