from __future__ import annotations

import json
from pathlib import Path

from agentos.skills.models import SkillScope, SkillSource
from agentos.skills.parser import SkillParseError, parse_skill_file

from .commands import parse_commands
from .hooks_manifest import parse_hooks
from .manifest import parse_mcp_config, parse_plugin_manifest
from .models import AgentContribution, PluginInspection, PluginRef, SkillContribution, McpServerContribution, plugin_id_from_name


def _manifest(path: Path):
    target = path / ".claude-plugin" / "plugin.json"
    if not target.exists():
        target = path / "plugin.json"
    return parse_plugin_manifest(json.loads(target.read_text(encoding="utf-8")))


def inspect_plugin_package(path: Path, *, package_digest: str) -> PluginInspection:
    path = Path(path)
    manifest = _manifest(path)
    warnings: list[str] = []
    skills: list[SkillContribution] = []
    skill_root = path / "skills"
    if skill_root.is_dir():
        for skill_file in sorted(skill_root.glob("*/SKILL.md"))[:200]:
            try:
                skill = parse_skill_file(
                    skill_file,
                    include_instructions=False,
                    source=SkillSource.PLUGIN,
                    scope=SkillScope.USER,
                    required_fields=frozenset({"name", "description"}),
                    default_version=manifest.version,
                )
            except SkillParseError:
                warnings.append(f"skill quebrada ignorada: {skill_file.parent.name}")
                continue
            # Plugin skill identities are namespaced by the plugin and use the
            # skill's public name.  Do not let an optional local `id` field in
            # SKILL.md change that cross-plugin reference.
            skill_id = f"{manifest.plugin_id}:{plugin_id_from_name(skill.name)}"
            skills.append(SkillContribution(skill_id, skill.name, skill.description, skill_file.relative_to(path).as_posix()))
        if len(list(skill_root.glob("*/SKILL.md"))) > 200:
            warnings.append("o plugin declara mais de 200 skills; o restante foi ignorado")
    def _namespaced(item: McpServerContribution) -> McpServerContribution:
        return McpServerContribution(
            f"{manifest.plugin_id}-{item.slug}", item.display_name, item.transport,
            item.command, item.args, item.url, item.secret_names,
        )

    # A separate .mcp.json is the more specific declaration, so it wins a slug
    # collision with a server declared inline in plugin.json.
    merged: dict[str, McpServerContribution] = {item.slug: item for item in manifest.mcp_servers}
    mcp_path = path / ".mcp.json"
    if mcp_path.exists():
        for item in parse_mcp_config(json.loads(mcp_path.read_text(encoding="utf-8"))):
            merged[item.slug] = item
    mcp_servers = tuple(_namespaced(item) for item in list(merged.values())[:16])
    agents: list[AgentContribution] = []
    agent_root = path / "agents"
    for item in sorted(agent_root.glob("*.md"))[:64] if agent_root.is_dir() else ():
        try:
            document = item.read_text(encoding="utf-8")
            if not document.startswith("---\n"):
                raise ValueError
            header, body = document[4:].split("\n---\n", 1)
            values = dict(line.split(":", 1) for line in header.splitlines() if ":" in line)
            name = values.get("name", item.stem).strip()
            description = values.get("description", "").strip()
            agents.append(AgentContribution(f"{manifest.plugin_id}:{plugin_id_from_name(name)}", name, body.strip() or description, item.relative_to(path).as_posix()))
        except (OSError, ValueError):
            warnings.append(f"subagente quebrado ignorado: {item.name}")
    commands, command_warnings = parse_commands(path / manifest.commands_path, plugin_id=manifest.plugin_id)
    warnings.extend(command_warnings)
    hooks, hook_warnings = parse_hooks(path / manifest.hooks_path, plugin_id=manifest.plugin_id)
    warnings.extend(hook_warnings)
    return PluginInspection(
        PluginRef(manifest.plugin_id, manifest.version), manifest.display_name, manifest.description,
        manifest.author, manifest.homepage, package_digest, skills=tuple(skills), mcp_servers=mcp_servers,
        agents=tuple(agents), commands=commands, hooks=hooks, warnings=tuple(warnings),
    )
