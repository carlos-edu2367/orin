from __future__ import annotations

from pathlib import Path

from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.cli_adapter import CliErrorKind
from tests.unit.providers.cli_adapter_conformance import (
    AdapterFixtures,
    CliAdapterConformance,
    ErrorScenario,
    StreamScenario,
    load_jsonl,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestClaudeCliAdapterConformance(CliAdapterConformance):
    def adapter_fixtures(self) -> AdapterFixtures:
        return AdapterFixtures(
            adapter=ClaudeCliAdapter(),
            simple_text=StreamScenario(
                lines=load_jsonl(FIXTURES / "out-2.jsonl"),
                expected_text="OK",
                expected_session_id="sess-2",
            ),
            tool_call=StreamScenario(
                lines=load_jsonl(FIXTURES / "out-3.jsonl"),
                expected_text="Pronto: echo:A",
                expected_tool_calls=(("mcp__orin__echo", {"text": "A"}),),
                expected_session_id="sess-3",
            ),
            error_scenarios=(
                ErrorScenario(
                    lines=load_jsonl(FIXTURES / "error-404.jsonl"),
                    expected_kind=CliErrorKind.MODEL_NOT_FOUND,
                    expected_retryable=False,
                ),
                ErrorScenario(
                    lines=load_jsonl(FIXTURES / "rate-limited.jsonl"),
                    expected_kind=CliErrorKind.RATE_LIMITED,
                    expected_retryable=False,
                ),
            ),
        )
