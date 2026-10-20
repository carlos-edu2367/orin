"""W6 Task 7: the worker's session applies an `orin code` turn's options."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.cli_turn import capped_input, capped_text, cli_settings_layer, tool_selection
from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
from agentos.agentic.session import TurnSession
from agentos.code_mode.cli_options import CliOptionsError, CliTurnOptions
from agentos.code_mode.models import CodePermissionMode
from agentos.settings.paths import orin_user_dir


class _Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.recorded: list[tuple[object, str, dict]] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "go"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        pass

    def finish(self, turn, *, failed=False, code=None):
        pass

    def record(self, turn, event_type, summary, payload=None, **kwargs):
        self.recorded.append((event_type, summary, dict(payload or {})))

    def main_agent_id(self, turn):
        return "agent:c:main"


class _Agents:
    def list(self):
        return []


def _session(tmp_path: Path, options: CliTurnOptions | None = None, **turn) -> TurnSession:
    store = _Store()
    extra = {"cli_options": options.dumps()} if options is not None else {}
    return TurnSession(
        turn={**store.turn, "code_mode": "code", **extra, **turn}, store=store, agents_store=_Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
    )


def test_tool_selection_and_the_cli_layer() -> None:
    options = CliTurnOptions(tools=["Read", "Grep"], settings={"permissions": {"deny": ["Bash", "Edit(src/**)"]}})
    offered, withheld = tool_selection(options, CODE_MODE_CORE_TOOLS)
    assert offered == frozenset({"read_file", "search_files"})
    assert "run_command" in withheld and "write_file" in withheld and "read_file" not in withheld
    layer = cli_settings_layer(options, CODE_MODE_CORE_TOOLS)
    assert "Write" in layer["permissions"]["deny"] and "Edit(src/**)" in layer["permissions"]["deny"]
    assert options.settings["permissions"]["deny"] == ["Bash", "Edit(src/**)"]  # the stored options are not mutated
    assert tool_selection(None, CODE_MODE_CORE_TOOLS) == (None, frozenset())
    assert cli_settings_layer(CliTurnOptions(), CODE_MODE_CORE_TOOLS) is None


def test_transcript_values_are_capped() -> None:
    assert capped_text("x" * 10, limit=100) == "x" * 10
    assert len(capped_text("x" * 1000, limit=100)) == 100 and capped_text("x" * 1000, limit=100).endswith("[truncated by Orin]")
    assert capped_input({"path": "a"}) == {"path": "a"}
    big = capped_input({"content": "y" * 200}, limit=100)
    assert set(big) == {"_truncated"} and len(big["_truncated"]) == 100
    assert capped_input("not a mapping") == {}


def test_no_prompts_makes_the_turn_non_interactive(tmp_path: Path) -> None:
    session = _session(tmp_path, CliTurnOptions(permission_prompts="none"), interactive_permissions=True)
    assert session.turn["interactive_permissions"] is False
    runtime = session.build_runtime()
    assert runtime.toolset._interactive_permissions is False
    assert runtime.toolset._non_interactive_ask_allowed is False  # W1-R9: what would ask is refused
    assert runtime.toolset._permission_broker is session._active_permission_broker  # the CLI layer still decides


def test_the_cli_layer_reaches_the_loop_limits_and_the_permission_engine(tmp_path: Path) -> None:
    options = CliTurnOptions(settings={"maxTurns": 2, "permissions": {"deny": ["Bash"]}})
    runtime = _session(tmp_path, options).build_runtime()
    assert runtime.limits.max_turns == 2
    engine = runtime.toolset._permission_broker.engine()
    assert engine.decide("run_command", {"command": "ls"}, CodePermissionMode.FULL_AUTONOMY).behavior == "deny"


def test_tools_are_offered_and_withheld(tmp_path: Path) -> None:
    session = _session(tmp_path, CliTurnOptions(tools=["Read", "Grep"], settings={"permissions": {"deny": ["Glob"]}}))
    runtime = session.build_runtime()
    names = {str((schema.get("function") or {}).get("name") or schema.get("name")) for schema in runtime._tool_schemas(session.turn)}
    assert names == {"read_file", "search_files"}
    write = runtime.toolset._permission_broker.engine().decide("write_file", {"path": "a.txt", "content": "x"}, CodePermissionMode.FULL_AUTONOMY)
    assert write.behavior == "deny"


def test_a_bare_disallowed_tool_is_not_offered(tmp_path: Path) -> None:
    session = _session(tmp_path, CliTurnOptions(settings={"permissions": {"deny": ["Bash"]}}))
    runtime = session.build_runtime()
    names = {str((schema.get("function") or {}).get("name") or schema.get("name")) for schema in runtime._tool_schemas(session.turn)}
    assert "read_file" in names and "run_command" not in names  # internal names: the translator renames them after


def test_the_system_prompt_flags(tmp_path: Path) -> None:
    replaced = _session(tmp_path / "a", CliTurnOptions(system_prompt="You are a terse reviewer.", append_system_prompt="Answer in Portuguese.")).build_runtime()
    assert replaced.system_prompt.startswith("You are a terse reviewer.")
    assert "# Using tools" not in replaced.system_prompt
    assert "# Instructions from the command line\nAnswer in Portuguese." in replaced.volatile_prompt
    kept = _session(tmp_path / "b", CliTurnOptions(system_prompt="")).build_runtime()
    assert "# Using tools" in kept.system_prompt  # an SDK host's empty --system-prompt keeps Orin's prompt


def test_a_headless_turn_skips_the_verification_second_pass(tmp_path: Path) -> None:
    assert _session(tmp_path / "a", CliTurnOptions()).build_runtime().completion_gate is False
    assert _session(tmp_path / "b").build_runtime().completion_gate is True


def test_setting_sources_limit_the_file_layers(tmp_path: Path) -> None:
    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"maxTurns": 7}), encoding="utf-8")
    assert _session(tmp_path / "a", CliTurnOptions()).build_runtime().limits.max_turns == 7
    runtime = _session(tmp_path / "b", CliTurnOptions(setting_sources=["project"])).build_runtime()
    assert runtime.limits.max_turns is None
    assert runtime.toolset._permission_broker.setting_sources == ("project",)


def test_tool_events_point_at_the_transcript_only_for_a_cli_turn(tmp_path: Path) -> None:
    # Activity payloads are sanitized (512-character strings, redacted keys such as "prompt"): the input and the
    # result are written to the turn's transcript and the events carry its id (Lote W6, Task 12).
    from agentos.agentic.cli_transcript import TranscriptStore

    session = _session(tmp_path, CliTurnOptions())
    long_result = "line\n" * 2000
    session.emit_lifecycle(session.turn, "tool_started", tool_name="run_subagent", invocation_id="c1",
                           tool_input={"prompt": "review the diff", "subagent_type": "general"})
    session.emit_lifecycle(session.turn, "tool_finished", tool_name="run_subagent", invocation_id="c1", status="succeeded",
                           summary="ok", tool_payload={}, tool_content=long_result)
    payloads = {str(event): payload for event, _, payload in session.store.recorded}
    started = next(payload for event, payload in payloads.items() if event.endswith("TOOL_STARTED") or event == "tool.started")
    finished = next(payload for event, payload in payloads.items() if event.endswith("TOOL_FINISHED") or event == "tool.finished")
    assert "tool_input" not in started and "tool_result" not in finished
    store = TranscriptStore("c")
    assert store.read(started["transcript_id"]) == {"tool_input": {"prompt": "review the diff", "subagent_type": "general"}}
    assert store.read(finished["transcript_id"]) == {"tool_result": long_result}
    plain = _session(tmp_path / "ui")
    plain.emit_lifecycle(plain.turn, "tool_started", tool_name="read_file", invocation_id="c1", tool_input={"path": "a.txt"})
    assert all("transcript_id" not in payload and "tool_input" not in payload for _, _, payload in plain.store.recorded)


def test_corrupt_stored_options_stop_the_turn(tmp_path: Path) -> None:
    with pytest.raises(CliOptionsError):
        _session(tmp_path, cli_options='{"tools": 3}')
