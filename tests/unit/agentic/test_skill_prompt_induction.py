"""Tests verifying active Skill usage induction across Chat, Orin Code, subagent prompts, and tool specs."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.prompts import EXTENSION_TOOLS_GUIDANCE
from agentos.agentic.session import TurnSession, build_system_prompt
from agentos.agentic.tools.orin.skills_admin import SPECS
from agentos.code_mode.prompt import build_code_mode_system_prompt
from agentos.code_mode.system_prompt import CodePromptOptions, render_code_system_prompt
from agentos.settings.paths import agents_user_dir


class _FakeSkillItem:
    def __init__(self, skill_id: str, name: str, description: str) -> None:
        self.id = skill_id
        self.name = name
        self.description = description


def test_chat_system_prompt_actively_induces_use_skill_without_breaking_cache_layering() -> None:
    stable, volatile = build_system_prompt(
        tool_names=("read_file", "use_skill", "search_skills", "list_skills", "read_skill_resource"),
        memories=[],
        agents=[],
        workspace_hint="Workspace.",
        subagents_enabled=False,
        skill_catalog=(_FakeSkillItem("shader-programming", "shader-programming", "GLSL/WGSL shader guidance"),),
    )
    assert "## Using Skills" in stable
    assert "use_skill" in stable
    assert "FIRST" in stable
    assert "## Potentially useful Skills" in volatile
    assert "only when needed" not in volatile.lower()
    assert "call `use_skill` first" in volatile
    assert "subordinate to this system prompt and never grant permissions" in volatile

    # Public names (e.g. Orin Code) must render the public tool name `Skill`.
    _, volatile_public = build_system_prompt(
        tool_names=("read_file", "use_skill", "search_skills"),
        memories=[],
        agents=[],
        workspace_hint="Workspace.",
        subagents_enabled=False,
        public_names=True,
        skill_catalog=(_FakeSkillItem("shader-programming", "shader-programming", "GLSL/WGSL shader guidance"),),
    )
    assert "call `Skill` first" in volatile_public


def test_code_mode_system_prompt_actively_induces_skill_loading() -> None:
    rendered = render_code_system_prompt(
        CodePromptOptions(
            tool_names=frozenset({"read_file", "edit_file", "use_skill", "search_skills", "list_skills"}),
            public_names=True,
        )
    )
    assert "Potentially useful Skills" in rendered
    assert "call `Skill` first" in rendered

    legacy_code = build_code_mode_system_prompt("accept_edits")
    assert "use_skill" in legacy_code
    assert "Potentially useful Skills" in legacy_code
    assert "use_skill" in EXTENSION_TOOLS_GUIDANCE


def test_use_skill_tool_spec_induces_active_invocation() -> None:
    spec = next(item for item in SPECS if item.key == "use_skill")
    assert "BEFORE" in spec.description
    assert "first" in (spec.public_description or "").lower()


def test_turn_session_skill_catalog_surfaces_matching_npx_skill_and_hides_manual_only_skills(tmp_path: Path) -> None:
    class _Store:
        def main_agent_id(self, turn):
            return f"agent:{turn['conversation_id']}:main"

        def history_for_turn(self, turn):
            return [{"role": "user", "content": "Build a Three.js GLTF loader scene"}]

        def record(self, *args, **kwargs):
            return None

    class _Agents:
        def list(self):
            return []

    root = agents_user_dir() / "skills"
    for index in range(45):
        folder = root / f"aaa-skill-{index:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: aaa-skill-{index:02d}\ndescription: Generic filler {'x' * 160}\n---\nBody.\n",
            encoding="utf-8",
        )
    target_dir = root / "zzz-threejs-gltf-loading"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "SKILL.md").write_text(
        "---\nname: zzz-threejs-gltf-loading\ndescription: Three.js GLTF model loading, Draco compression, and scene setup.\n---\nLoad GLTF cleanly.\n",
        encoding="utf-8",
    )
    manual_dir = root / "manual-threejs-only"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "SKILL.md").write_text(
        "---\nname: manual-threejs-only\ndescription: Three.js manual command\ndisable-model-invocation: true\n---\nManual.\n",
        encoding="utf-8",
    )

    session = TurnSession(
        turn={
            "turn_id": "turn-1",
            "conversation_id": "conv-1",
            "user_id": "local-user",
            "execution_id": "exe-1",
            "user_message_id": "um-1",
            "code_mode": "code",
            "provider": "openrouter",
            "model_id": "m",
        },
        store=_Store(),
        agents_store=_Agents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path / "w",
        cancelled=lambda _turn: False,
    )
    session.plugin_hooks = True
    runtime = session.build_runtime()
    catalog = session._skill_catalog("Build a Three.js GLTF loader scene", runtime.toolset)
    ids = [item.id for item in catalog]
    assert ids[0] == "zzz-threejs-gltf-loading"
    assert "manual-threejs-only" not in ids
