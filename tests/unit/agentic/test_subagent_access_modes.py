"""Unit tests for subagent 3 access modes (prompt_only, read_only, full) and monotonicity (Frente B)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentic.agent_definitions import AgentCatalog, AgentDefinition
from agentos.agentic.agent_tools import READ_ONLY_FILE_TOOLS, AgentToolset
from agentos.agentic.session import SubagentCaller, TurnSession
from agentos.agentic.tools.public_names import arguments_in, arguments_out
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _RecordingStore, _text_batch


class _StaticProvider:
    def __init__(self, reply: str = "OK") -> None:
        self.reply = reply
        self.requests: list[object] = []

    def stream(self, request):
        self.requests.append(request)
        return iter(_text_batch(self.reply))


def _make_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_sees_images: bool = True,
):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'submodes.db').as_posix()}")
    metadata.create_all(engine)
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    store = _RecordingStore()
    child_provider = _StaticProvider("Verdict: approved.")
    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: child_provider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _model: child_provider,
        model_sees_images=model_sees_images,
    )
    builds: list[dict] = []
    prompts: list[str] = []
    limits: list[dict] = []
    orig_toolset = TurnSession._toolset
    orig_prompt = TurnSession._subagent_prompt
    orig_limits = TurnSession._subagent_limits

    def rec_toolset(self, **kwargs):
        ts = orig_toolset(self, **kwargs)
        builds.append({**kwargs, "toolset": ts})
        return ts

    def rec_prompt(self, record, task, toolset):
        p = orig_prompt(self, record, task, toolset)
        prompts.append(p)
        return p

    def rec_limits(self, **kwargs):
        limits.append(kwargs)
        return orig_limits(self, **kwargs)

    monkeypatch.setattr(TurnSession, "_toolset", rec_toolset)
    monkeypatch.setattr(TurnSession, "_subagent_prompt", rec_prompt)
    monkeypatch.setattr(TurnSession, "_subagent_limits", rec_limits)
    return session, store, child_provider, builds, prompts, limits, engine


def test_toolset_prompt_only_has_zero_definitions_and_hard_guards_every_tool(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path)
    ts = AgentToolset(ws, access_mode="prompt_only", enable_terminal=True)
    assert ts.definitions() == ()
    assert ts.schemas() == []

    for tool_name, args in (
        ("read_file", {"path": "a.txt"}),
        ("write_file", {"path": "a.txt", "content": "x"}),
        ("run_command", {"command": "echo hi"}),
        ("list_files", {}),
    ):
        outcome = ts.invoke(tool_name, args)
        assert outcome.status == "failed"
        assert outcome.error_code == "PROMPT_ONLY_SUBAGENT"


def test_toolset_read_only_restricts_strictly_to_read_only_file_tools(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path)
    (ws.root / "hello.txt").write_text("hello world\n", encoding="utf-8")
    ts = AgentToolset(ws, access_mode="read_only", enable_terminal=True)
    published = {item.name for item in ts.definitions()}
    assert published <= READ_ONLY_FILE_TOOLS
    assert {"read_file", "list_files", "glob_files", "search_files"} <= published
    assert not {"write_file", "edit_file", "run_command", "web_search", "fetch_url", "run_subagent"} & published

    ok = ts.invoke("read_file", {"path": "hello.txt"})
    assert ok.status == "succeeded"

    for blocked_name, args in (
        ("write_file", {"path": "x.txt", "content": "nope"}),
        ("run_command", {"command": "echo nope"}),
        ("fetch_url", {"url": "https://example.com", "prompt": "hi"}),
        ("todo_write", {"todos": []}),
    ):
        blocked = ts.invoke(blocked_name, args)
        assert blocked.status == "failed"
        assert blocked.error_code == "READ_ONLY_SUBAGENT"


def test_public_names_access_mode_alias_is_idempotent_and_preserves_attachments() -> None:
    raw = {
        "description": "Judge",
        "prompt": "Compare images",
        "subagent_type": "general-purpose",
        "accessMode": "prompt_only",
        "attachments": ["a.png", "b.png"],
    }
    first = arguments_in("run_subagent", raw)
    assert first["access_mode"] == "prompt_only"
    assert "accessMode" not in first
    assert first["attachments"] == ["a.png", "b.png"]

    second = arguments_in("run_subagent", first)
    assert second == first

    out = arguments_out("run_subagent", first)
    assert out["access_mode"] == "prompt_only"
    assert out["attachments"] == ["a.png", "b.png"]


def test_run_subagent_prompt_only_execution_prompt_and_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, store, _provider, builds, prompts, limits, engine = _make_session(tmp_path, monkeypatch)
    try:
        outcome = session._run_subagent(
            subagent_type="general",
            task="Classify sentiment: Great product!",
            description="Sentiment judge",
            access_mode="prompt_only",
        )
        assert outcome.status == "succeeded", outcome.content
        child = [b for b in builds if b.get("browser_agent_key")][-1]
        assert child["access_mode"] == "prompt_only"
        assert child["toolset"].definitions() == ()
        assert limits[-1]["max_actions"] == 0

        # Prompt must stay isolated: no shell, write_file, or workspace tree leakage.
        prompt_text = prompts[-1]
        assert "prompt-only mode" in prompt_text.lower()
        assert "run_command" not in prompt_text
        assert "write_file" not in prompt_text
        assert "## Workspace and environment" not in prompt_text

        sent_events = [payload for et, _s, payload, _a in store.activity if str(getattr(et, "value", et)) == "agent.message_sent"]
        assert sent_events and sent_events[-1]["access_mode"] == "prompt_only"
    finally:
        engine.dispose()


def test_run_subagent_read_only_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session, store, _provider, builds, prompts, _limits, engine = _make_session(tmp_path, monkeypatch)
    try:
        outcome = session._run_subagent(
            subagent_type="general",
            task="Inspect src/",
            description="Read-only audit",
            access_mode="read_only",
        )
        assert outcome.status == "succeeded", outcome.content
        child = [b for b in builds if b.get("browser_agent_key")][-1]
        assert child["access_mode"] == "read_only"
        assert {item.name for item in child["toolset"].definitions()} <= READ_ONLY_FILE_TOOLS
        assert "strictly read-only" in prompts[-1].lower()
        sent_events = [payload for et, _s, payload, _a in store.activity if str(getattr(et, "value", et)) == "agent.message_sent"]
        assert sent_events[-1]["access_mode"] == "read_only"
    finally:
        engine.dispose()


def test_monotonicity_blocks_escalation_across_caller_builtins_and_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _store, _provider, builds, _prompts, _limits, engine = _make_session(tmp_path, monkeypatch)
    try:
        # 1. Read-only caller cannot start full subagent; narrowing to prompt_only is allowed.
        ro_caller = SubagentCaller(
            agent_id="agent-ro",
            name="explore",
            depth=1,
            permission_mode="plan_mode",
            read_only=True,
            access_mode="read_only",
        )
        escalated = session._run_subagent(
            subagent_type="general", task="Edit files", access_mode="full", caller=ro_caller
        )
        assert escalated.status == "failed"
        assert escalated.error_code == "ACCESS_MODE_ESCALATION_DENIED"

        narrowed = session._run_subagent(
            subagent_type="general", task="Classify", access_mode="prompt_only", caller=ro_caller
        )
        assert narrowed.status == "succeeded", narrowed.content
        assert [b for b in builds if b.get("browser_agent_key")][-1]["access_mode"] == "prompt_only"

        # 2. Prompt-only caller cannot delegate at all.
        po_caller = SubagentCaller(
            agent_id="agent-po",
            name="judge",
            depth=1,
            permission_mode="plan_mode",
            read_only=True,
            access_mode="prompt_only",
        )
        po_delegate = session._run_subagent(subagent_type="explore", task="Search", caller=po_caller)
        assert po_delegate.status == "failed"
        assert po_delegate.error_code == "ACCESS_MODE_ESCALATION_DENIED"

        # 3. Reserved builtins Explore, Plan, verifier reject explicit access_mode="full".
        for builtin_name in ("explore", "plan", "verifier"):
            res = session._run_subagent(subagent_type=builtin_name, task="Check", access_mode="full")
            assert res.status == "failed"
            assert res.error_code == "ACCESS_MODE_ESCALATION_DENIED"

        # Explore can still be narrowed to prompt_only.
        explore_po = session._run_subagent(subagent_type="explore", task="Answer", access_mode="prompt_only")
        assert explore_po.status == "succeeded", explore_po.content
        assert [b for b in builds if b.get("browser_agent_key")][-1]["access_mode"] == "prompt_only"

        # 4. Custom AgentDefinition with access_mode="prompt_only" or "read_only" rejects escalation.
        catalog = AgentCatalog(
            agents={
                "visual-judge": AgentDefinition(
                    name="visual-judge",
                    description="Judges images",
                    prompt="Judge the images.",
                    origin="user",
                    access_mode="prompt_only",
                ),
                "auditor": AgentDefinition(
                    name="auditor",
                    description="Audits code",
                    prompt="Audit code.",
                    origin="user",
                    access_mode="read_only",
                ),
            }
        )
        session._agent_definitions = catalog

        for target, bad_mode in (("visual-judge", "full"), ("visual-judge", "read_only"), ("auditor", "full")):
            denied = session._run_subagent(subagent_type=target, task="Check", access_mode=bad_mode)
            assert denied.status == "failed"
            assert denied.error_code == "ACCESS_MODE_ESCALATION_DENIED"

        # Omitting access_mode inherits the definition's access_mode.
        inherited = session._run_subagent(subagent_type="visual-judge", task="Judge now")
        assert inherited.status == "succeeded", inherited.content
        assert [b for b in builds if b.get("browser_agent_key")][-1]["access_mode"] == "prompt_only"

        # Narrowing read_only definition -> prompt_only succeeds.
        narrowed_def = session._run_subagent(
            subagent_type="auditor", task="Summarize text", access_mode="prompt_only"
        )
        assert narrowed_def.status == "succeeded", narrowed_def.content
        assert [b for b in builds if b.get("browser_agent_key")][-1]["access_mode"] == "prompt_only"

        # _defined_agents_text announces restricted access modes.
        announced = session._defined_agents_text()
        assert "[access: prompt_only]" in announced
        assert "[access: read_only]" in announced
    finally:
        engine.dispose()


def test_incompatibilities_worktree_and_fork_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _store, _provider, _builds, _prompts, _limits, engine = _make_session(tmp_path, monkeypatch)
    try:
        for mode in ("prompt_only", "read_only"):
            wt_res = session._run_subagent(
                subagent_type="general", task="Work", isolation="worktree", access_mode=mode
            )
            assert wt_res.status == "failed"
            assert wt_res.error_code == "INVALID_ARGUMENTS"

        fork_res = session._run_subagent(
            subagent_type="fork", task="Forked prompt only", access_mode="prompt_only"
        )
        assert fork_res.status == "failed"
        assert fork_res.error_code == "INVALID_ARGUMENTS"
    finally:
        engine.dispose()
