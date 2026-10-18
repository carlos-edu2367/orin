from __future__ import annotations

import json
import re
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
from agentos.agentic.session import TurnSession
from agentos.agentic.tools.public_names import PUBLIC_NAMES, arguments_in, public_text
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.system_prompt import CodePromptOptions, render_code_system_prompt, render_code_turn_context
from agentos.settings.paths import orin_user_dir


def test_public_text_rewrites_only_tool_ids() -> None:
    assert public_text("Read it first with read_file before writing.") == "Read it first with Read before writing."
    assert public_text("use `edit_file` or run_command(...)") == "use `Edit` or Bash(...)"
    assert public_text("see src/read_file.py and read_file.py") == "see src/read_file.py and read_file.py"
    assert public_text("my_read_file_helper") == "my_read_file_helper"
    assert public_text("no tools here") == "no tools here"


def test_the_agent_model_reaches_the_subagent() -> None:
    assert arguments_in("run_subagent", {"prompt": "x", "model": "haiku"})["model_id"] == "haiku"


def _options(public: bool = True, **values) -> CodePromptOptions:
    return CodePromptOptions(tool_names=frozenset(CODE_MODE_CORE_TOOLS), public_names=public, **values)


def test_the_code_prompt_has_its_sections_and_speaks_public_names() -> None:
    prompt = render_code_system_prompt(_options())
    for heading in ("# Tone and style", "# Doing tasks", "# Using tools", "# Planning", "# Safety and reversibility", "# Git", "# Orin tools"):
        assert heading in prompt
    for name in ("`Read`", "`Edit`", "`Write`", "`Bash`", "`TodoWrite`", "`Agent`", "`ExitPlanMode`"):
        assert name in prompt
    leaked = [internal for internal in PUBLIC_NAMES if "_" in internal and re.search(rf"\b{re.escape(internal)}\b", prompt)]
    assert leaked == []


def test_internal_names_without_the_flag() -> None:
    prompt = render_code_system_prompt(_options(public=False))
    assert "`read_file`" in prompt and "`Read`" not in prompt


def test_the_stable_prompt_is_deterministic_and_carries_no_turn_data() -> None:
    first = render_code_system_prompt(_options())
    assert first == render_code_system_prompt(_options())
    assert not re.search(r"\d{4}-\d{2}-\d{2}", first)


def test_sections_follow_the_available_tools() -> None:
    prompt = render_code_system_prompt(CodePromptOptions(tool_names=frozenset({"read_file"}), public_names=True))
    assert "`Bash`" not in prompt and "TodoWrite" not in prompt and "# Planning" not in prompt


def test_the_turn_context_names_the_mode_environment_and_git() -> None:
    environment = {"os": "Windows 10", "shell": "cmd.exe", "python": "3.13", "available": "git"}
    text = render_code_turn_context(
        permission_mode="accept_edits", plan_path=None, permits_push=True, environment=environment,
        workspace_root="C:/work", workspace_is_local=True, git_status="## main\n M a.py", public_names=True,
    )
    for expected in ("code autonomy", "Windows 10", "cmd.exe", "## main", "git push", "C:/work"):
        assert expected in text
    plan = render_code_turn_context(
        permission_mode="plan_mode", plan_path=".orin/plans/p.md", permits_push=False, environment=environment,
        workspace_root="C:/work", workspace_is_local=False, git_status="", public_names=True,
    )
    assert ".orin/plans/p.md" in plan and "`ExitPlanMode`" in plan and "# Git status" not in plan


class _Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }

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

    def record(self, *args, **kwargs):
        pass

    def main_agent_id(self, turn):
        return "agent:c:main"


class _Agents:
    def list(self):
        return []


def _session(tmp_path: Path, **turn) -> TurnSession:
    store = _Store()
    return TurnSession(
        turn={**store.turn, **turn}, store=store, agents_store=_Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
    )


def test_code_mode_speaks_public_names_by_default(tmp_path: Path) -> None:
    runtime = _session(tmp_path, code_mode="code").build_runtime()
    assert runtime.toolset.name_translator is not None
    assert "# Using tools" in runtime.system_prompt and "`Read`" in runtime.system_prompt
    assert "code autonomy" in runtime.volatile_prompt


def test_the_user_can_turn_public_names_off(tmp_path: Path) -> None:
    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"experimental": {"claudeToolNames": False}}), encoding="utf-8")
    runtime = _session(tmp_path, code_mode="code").build_runtime()
    assert runtime.toolset.name_translator is None
    assert "`read_file`" in runtime.system_prompt


def test_chat_mode_keeps_internal_names(tmp_path: Path) -> None:
    runtime = _session(tmp_path).build_runtime()
    assert runtime.toolset.name_translator is None
    assert "# Using tools" not in runtime.system_prompt


def test_orin_authored_errors_and_hints_use_public_names(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-public")
    (workspace.root / "a.txt").write_text("x\n" * 1000, encoding="utf-8")
    toolset = AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy", public_tool_names=True)
    refused = toolset.invoke("Edit", {"file_path": "a.txt", "old_string": "x", "new_string": "y"})
    assert refused.error_code == "FILE_NOT_READ"
    assert "read_file" not in refused.content and "Read" in refused.content
    page = toolset.invoke("Read", {"file_path": "a.txt", "limit": 10})
    assert 'Read(file_path="a.txt", offset=11)' in page.content
