"""Tests for ``npx skills add`` cross-harness discovery, safe link handling, auto-sync, and unified skill tools."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.postgres.skills import PostgresSkillLibraryService
from agentos.settings.paths import agents_user_dir, claude_user_dir, orin_user_dir
from agentos.skills.builtins import load_builtin_skills
from agentos.skills.disk import CATALOG_BUDGET_CHARS, load_disk_skills
from agentos.skills.registry import SkillRegistry


def _write_skill(skills_root: Path, name: str, body: str, *, description: str | None = None, extra_frontmatter: str = "") -> Path:
    folder = skills_root / name
    folder.mkdir(parents=True, exist_ok=True)
    desc_line = f"description: {description}\n" if description is not None else f"description: Skill {name}\n"
    fm = f"---\nname: {name}\n{desc_line}{extra_frontmatter}---\n{body}\n"
    path = folder / "SKILL.md"
    path.write_bytes(fm.encode("utf-8"))
    return path


def test_discovers_user_skills_across_npx_skills_harness_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("ORIN_SKILLS_HOME", str(fake_home))
    monkeypatch.setenv("ORIN_AGENTS_HOME", str(fake_home / ".agents"))

    _write_skill(orin_user_dir() / "skills", "orin-native", "Orin native instructions.")
    _write_skill(fake_home / ".agents" / "skills", "agents-canonical", "Canonical npx skills add.")
    _write_skill(fake_home / ".config" / "agents" / "skills", "xdg-agent-skill", "XDG agents skill.")
    _write_skill(claude_user_dir() / "skills", "claude-skill", "Claude Code skill.")
    _write_skill(fake_home / ".codex" / "skills", "codex-skill", "Codex skill.")
    _write_skill(fake_home / ".cursor" / "skills", "cursor-skill", "Cursor skill.")
    _write_skill(fake_home / ".gemini" / "antigravity" / "skills", "antigravity-skill", "Antigravity skill.")
    _write_skill(fake_home / ".config" / "opencode" / "skills", "opencode-skill", "OpenCode skill.")
    _write_skill(fake_home / ".copilot" / "skills", "copilot-skill", "Copilot skill.")
    _write_skill(fake_home / ".codeium" / "windsurf" / "skills", "windsurf-skill", "Windsurf skill.")

    # Hidden harness metadata directories (.trash, .system) must be ignored cleanly.
    (claude_user_dir() / "skills" / ".trash").mkdir(parents=True, exist_ok=True)
    (fake_home / ".codex" / "skills" / ".system").mkdir(parents=True, exist_ok=True)

    catalog = load_disk_skills(workspace_root=None, trusted=False, personal=True)
    assert {
        "orin-native",
        "agents-canonical",
        "xdg-agent-skill",
        "claude-skill",
        "codex-skill",
        "cursor-skill",
        "antigravity-skill",
        "opencode-skill",
        "copilot-skill",
        "windsurf-skill",
    } <= set(catalog.skills)
    assert not catalog.errors


def test_discovers_project_skills_across_harness_directories_only_when_trusted(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _write_skill(workspace / ".agents" / "skills", "proj-agents", "Project canonical skill.")
    _write_skill(workspace / ".claude" / "skills", "proj-claude", "Project claude skill.")
    _write_skill(workspace / ".codex" / "skills", "proj-codex", "Project codex skill.")
    _write_skill(workspace / ".cursor" / "skills", "proj-cursor", "Project cursor skill.")
    _write_skill(workspace / ".opencode" / "skills", "proj-opencode", "Project opencode skill.")
    _write_skill(workspace / ".github" / "skills", "proj-github", "Project github skill.")
    _write_skill(workspace / ".windsurf" / "skills", "proj-windsurf", "Project windsurf skill.")

    untrusted = load_disk_skills(workspace_root=workspace, trusted=False, personal=True)
    assert not (set(untrusted.skills) & {"proj-agents", "proj-claude", "proj-codex", "proj-cursor"})

    trusted = load_disk_skills(workspace_root=workspace, trusted=True, personal=True)
    assert {
        "proj-agents",
        "proj-claude",
        "proj-codex",
        "proj-cursor",
        "proj-opencode",
        "proj-github",
        "proj-windsurf",
    } <= set(trusted.skills)


def test_precedence_prefers_orin_then_agents_then_claude(tmp_path: Path) -> None:
    _write_skill(orin_user_dir() / "skills", "shared", "from orin")
    _write_skill(agents_user_dir() / "skills", "shared", "from agents")
    _write_skill(claude_user_dir() / "skills", "shared", "from claude")
    _write_skill(agents_user_dir() / "skills", "agents-wins", "from agents")
    _write_skill(claude_user_dir() / "skills", "agents-wins", "from claude")

    catalog = load_disk_skills(workspace_root=None, trusted=False, personal=True)
    assert catalog.get("shared").body == "from orin"
    assert catalog.get("agents-wins").body == "from agents"


def test_directory_symlink_inside_known_skill_roots_loads_and_dedupes_while_outside_symlink_is_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    canonical = workspace / ".agents" / "skills" / "vercel-react"
    _write_skill(workspace / ".agents" / "skills", "vercel-react", "React best practices.")

    claude_dir = workspace / ".claude" / "skills"
    claude_dir.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / "outside-secret"
    _write_skill(tmp_path, "outside-secret", "Should never load.")

    try:
        os.symlink(canonical, claude_dir / "vercel-react", target_is_directory=True)
        os.symlink(outside_dir, claude_dir / "evil-link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create directory symbolic links")

    catalog = load_disk_skills(workspace_root=workspace, trusted=True, personal=False)
    assert catalog.get("vercel-react") is not None
    assert catalog.get("evil-link") is None
    # Same canonical SKILL.md exposed in .agents and .claude must not produce a shadowed conflict.
    assert not any(name == "vercel-react" for name, _, _ in catalog.shadowed)


def test_catalog_items_ranks_task_matching_skills_first_even_beyond_budget(tmp_path: Path) -> None:
    root = agents_user_dir() / "skills"
    for index in range(50):
        _write_skill(root, f"aaa-filler-{index:02d}", "Filler body.", description="Filler description " + "x" * 180)
    _write_skill(
        root,
        "zzz-unreal-cpp-gameplay",
        "Unreal C++ gameplay patterns.",
        description="Unreal Engine 5 C++ gameplay architecture, actors, components and replication.",
    )

    catalog = load_disk_skills(workspace_root=None, trusted=False, personal=True)
    unranked = catalog.catalog_items(budget=CATALOG_BUDGET_CHARS)
    assert "zzz-unreal-cpp-gameplay" not in {item.id for item in unranked}

    ranked = catalog.catalog_items(budget=CATALOG_BUDGET_CHARS, query="Help me debug Unreal Engine C++ gameplay actors")
    assert ranked[0].id == "zzz-unreal-cpp-gameplay"


def test_postgres_skill_library_auto_syncs_updates_and_removes_external_disk_skills(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)

    # 1. Install a skill into ~/.agents/skills (as ``npx skills add -g`` does).
    skill_path = _write_skill(
        agents_user_dir() / "skills",
        "nextjs-performance",
        "# Workflow\nProfile LCP and bundle size.\n# Validation\nRun Lighthouse.",
        description="Optimize Next.js rendering and bundle size.",
        extra_frontmatter="tags:\n  - nextjs\n  - performance\n",
    )

    listed = service.list({"user_id": "u1", "limit": 100})
    ids = {item["id"]: item for item in listed["items"]}
    assert "nextjs-performance" in ids
    assert ids["nextjs-performance"]["source"] == "imported"
    detail = service.get({"user_id": "u1", "skill_id": "nextjs-performance"})
    assert "Profile LCP" in detail["instructions"]
    assert detail["versions"] == ["1.0.0"]

    # 2. Update the SKILL.md on disk (e.g. ``npx skills update``) -> next read publishes patch version.
    skill_path.write_text(
        "---\nname: nextjs-performance\ndescription: Optimize Next.js 16 rendering and bundle size.\n---\n# Workflow\nUpdated workflow.\n",
        encoding="utf-8",
    )
    # Bump mtime explicitly in case filesystem granularity matches the previous write.
    stat = skill_path.stat()
    os.utime(skill_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    updated_detail = service.get({"user_id": "u1", "skill_id": "nextjs-performance"})
    assert "Updated workflow." in updated_detail["instructions"]
    assert updated_detail["version"] == "1.0.1"
    assert updated_detail["versions"] == ["1.0.1", "1.0.0"]

    # 3. Remove the skill from disk (e.g. ``npx skills remove``) -> next list removes unpinned imported skill.
    skill_path.unlink()
    skill_path.parent.rmdir()
    listed_after = service.list({"user_id": "u1", "limit": 100})
    assert "nextjs-performance" not in {item["id"] for item in listed_after["items"]}


def test_auto_sync_never_overwrites_user_custom_skill_and_respects_server_mode_flag(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    service = PostgresSkillLibraryService(engine)

    service.create({
        "user_id": "u1",
        "name": "Deploy Check",
        "description": "My custom deploy check.",
        "version": "1.0.0",
        "tags": ["deploy"],
        "instructions": "# Custom instructions",
    })
    _write_skill(agents_user_dir() / "skills", "deploy-check", "# External instructions")

    detail = service.get({"user_id": "u1", "skill_id": "deploy-check"})
    assert detail["source"] == "custom"
    assert detail["instructions"] == "# Custom instructions"

    # Server mode (sync_personal_disk_skills=False) must not import personal host skills.
    _write_skill(agents_user_dir() / "skills", "host-only-skill", "# Host only")
    server_service = PostgresSkillLibraryService(engine, sync_personal_disk_skills=False)
    listed = server_service.list({"user_id": "u2", "limit": 100})
    assert "host-only-skill" not in {item["id"] for item in listed["items"]}


def test_agent_skill_tools_search_list_use_and_read_resource_on_disk_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    skill_path = _write_skill(
        workspace / ".agents" / "skills",
        "shader-programming",
        "Write clean WGSL/GLSL shaders. See references/noise.md.",
        description="GLSL and WGSL shader programming patterns.",
        extra_frontmatter="tags:\n  - shaders\n  - graphics\n",
    )
    refs_dir = skill_path.parent / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    (refs_dir / "noise.md").write_text("# Simplex Noise\nUse 2D simplex gradients.\n", encoding="utf-8")

    toolset = AgentToolset(
        ConversationWorkspace.at_root(workspace),
        skills=SkillRegistry(load_builtin_skills()),
    )
    toolset._disk_skills = load_disk_skills(workspace_root=workspace, trusted=True, personal=False)

    searched = toolset.invoke("search_skills", {"query": "WGSL shader programming"})
    assert searched.status == "succeeded"
    found_ids = [item["id"] for item in json.loads(searched.content)]
    assert "shader-programming" in found_ids

    listed = toolset.invoke("list_skills", {"tag": "shaders"})
    assert listed.status == "succeeded"
    assert "shader-programming" in [item["id"] for item in json.loads(listed.content)]

    loaded = toolset.invoke("use_skill", {"skill_id": "shader-programming"})
    assert loaded.status == "succeeded"
    assert "Write clean WGSL/GLSL shaders." in loaded.content

    resource = toolset.invoke("read_skill_resource", {"skill_id": "shader-programming", "resource_path": "references/noise.md"})
    assert resource.status == "succeeded"
    assert "Simplex Noise" in resource.content

    traversal = toolset.invoke("read_skill_resource", {"skill_id": "shader-programming", "resource_path": "../SKILL.md"})
    assert traversal.status == "failed"


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions are Windows-specific")
def test_windows_junction_created_by_npx_skills_loads_and_dedupes_while_outside_junction_is_blocked(tmp_path: Path) -> None:
    import subprocess

    workspace = tmp_path / "repo"
    canonical = workspace / ".agents" / "skills" / "vercel-react"
    _write_skill(workspace / ".agents" / "skills", "vercel-react", "React best practices.")

    claude_dir = workspace / ".claude" / "skills"
    claude_dir.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / "outside-secret"
    _write_skill(tmp_path, "outside-secret", "Should never load.")

    for link, target in ((claude_dir / "vercel-react", canonical), (claude_dir / "evil-junction", outside_dir)):
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if proc.returncode != 0:
            pytest.skip(f"mklink /J unavailable: {proc.stderr}")

    catalog = load_disk_skills(workspace_root=workspace, trusted=True, personal=False)
    assert catalog.get("vercel-react") is not None
    assert catalog.get("evil-junction") is None
    assert not any(name == "vercel-react" for name, _, _ in catalog.shadowed)
