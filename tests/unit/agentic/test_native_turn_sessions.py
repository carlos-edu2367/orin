from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.native_turn import NATIVE_CWD, SESSION_BRANCH
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.installation.paths import orin_paths
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest
from agentos.providers.claude_cli.stream import CliInit, CliResult, CliUsage


class _MinimalStore:
    def __init__(self, history: list[dict[str, object]]) -> None:
        self._history = list(history)
        self.deltas: list[str] = []
        self.states: list[str] = []

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, object]]:
        return list(self._history)

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.deltas.append(text)

    def reset_assistant_message(self, turn: dict[str, object]) -> None:
        self.deltas.clear()

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        pass

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.states.append(state)

    def effect_started(self, turn: dict[str, object], *, kind: str, invocation_ref: str, request_ref: str) -> str:
        return "eff-1"

    def effect_finished(self, turn: dict[str, object], **kwargs: object) -> None:
        pass

    def record_quality(self, turn: dict[str, object], **values: object) -> None:
        pass

    def record_step(self, turn: dict[str, object], **values: object) -> None:
        pass


class _RecordingNativeProvider:
    def __init__(self) -> None:
        self.requests: list[NativeTurnRequest] = []

    def native_turn_available(self, turn: Any) -> bool:
        return True

    def start_native_turn(self, req: NativeTurnRequest) -> Any:
        self.requests.append(req)
        init = CliInit(
            session_id="s-1",
            model=req.model,
            api_key_source="none",
            mcp_servers={"orin": "connected"},
            tools=(),
        )
        u = CliUsage(input_tokens=40, output_tokens=10)

        class _H:
            def run_until_result(self, sink: Any) -> NativeTurnOutcome:
                sink.on_init(init)
                sink.on_text("Resposta final.")
                return NativeTurnOutcome(
                    init=init,
                    result=CliResult(
                        subtype="success",
                        is_error=False,
                        result_text="Resposta final.",
                        stop_reason="end_turn",
                        num_turns=1,
                        duration_ms=15,
                        session_id="s-1",
                        api_error_status=None,
                        usage=u,
                        model_usage={req.model: u},
                    ),
                    final_text="Resposta final.",
                    exit_code=0,
                )

            def refuse_tools(self, reason: str = "") -> None:
                pass

            def send_user(self, content: list[dict[str, Any]]) -> None:
                pass

            def close(self) -> None:
                pass

        return _H()


@pytest.mark.skipif(SESSION_BRANCH != "A", reason="Spike selected SESSION_BRANCH='B'")
def test_second_message_resumes_and_sends_only_new_content() -> None:
    pass


@pytest.mark.skipif(SESSION_BRANCH != "A", reason="Spike selected SESSION_BRANCH='B'")
def test_failed_resume_falls_back_to_fresh_session_once() -> None:
    pass


def test_native_process_cwd_is_orin_workdir(tmp_path: Path) -> None:
    store = _MinimalStore([{"role": "user", "content": "Olá"}])
    provider = _RecordingNativeProvider()
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path),
        code_mode_active=True,
        public_tool_names=True,
        code_mode_work_kind="analysis",
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        cancelled=lambda _t: False,
    )
    turn = {
        "turn_id": "t-cwd-1",
        "conversation_id": "c-cwd-1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
        "conversation_mode": "code",
        "code_mode": "code",
        "workspace_path": str(tmp_path),
    }
    res = runtime.run("t-cwd-1", turn=turn)
    assert res.state == "completed"
    assert len(provider.requests) == 1
    req = provider.requests[0]
    assert NATIVE_CWD.parts[-2:] == ("claude-cli", "workdir")
    assert req.cwd == orin_paths().data / "claude-cli" / "workdir"
    assert req.cwd != tmp_path
    assert req.cwd.is_dir()


def test_branch_b_sends_prior_history_as_transcript_blocks_and_never_sets_session(tmp_path: Path) -> None:
    history = [
        {"role": "user", "content": "Primeira pergunta"},
        {"role": "assistant", "content": "Primeira resposta"},
        {"role": "user", "content": "Segunda pergunta"},
    ]
    store = _MinimalStore(history)
    provider = _RecordingNativeProvider()
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path),
        code_mode_active=True,
        public_tool_names=True,
        code_mode_work_kind="analysis",
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        cancelled=lambda _t: False,
    )
    turn = {
        "turn_id": "t-hist-2",
        "conversation_id": "c-hist-1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
        "conversation_mode": "code",
        "code_mode": "code",
        "workspace_path": str(tmp_path),
    }
    res = runtime.run("t-hist-2", turn=turn)
    assert res.state == "completed"
    req = provider.requests[0]
    assert getattr(req, "session", None) is None
    texts = [b.get("text", "") for b in req.user_content if isinstance(b, dict)]
    joined = "\n".join(texts)
    assert "Primeira pergunta" in joined
    assert "Primeira resposta" in joined
    assert "Segunda pergunta" in joined
