from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from agentos.skills.models import SkillScope, SkillSource
from agentos.skills.parser import SkillParseError, parse_skill_file

from .commands import parse_commands
from .hooks_manifest import parse_hooks
from .manifest import ManifestRejected, manifest_file, parse_mcp_config, parse_plugin_manifest
from .models import AgentContribution, PluginInspection, PluginRef, SkillContribution, McpServerContribution, plugin_id_from_name


def _manifest(path: Path):
    target = manifest_file(path)
    if target is None:
        raise ManifestRejected("the package has no plugin.json")
    return parse_plugin_manifest(json.loads(target.read_text(encoding="utf-8")))


def inspect_plugin_package(path: Path, *, package_digest: str) -> PluginInspection:
    path = Path(path)
    manifest = _manifest(path)
    warnings: list[str] = []
    skills: list[SkillContribution] = []
    skill_files: list[Path] = []
    for folder in manifest.skills_paths:
        if (path / folder).is_dir():
            skill_files.extend(sorted((path / folder).glob("*/SKILL.md")))
    if len(skill_files) > 200:
        warnings.append("o plugin declara mais de 200 skills; o restante foi ignorado")
    for skill_file in skill_files[:200]:
        try:
            # W5: read leniently, so a Claude Code skill's own frontmatter keys do not reject it.
            skill = parse_skill_file(
                skill_file,
                include_instructions=False,
                source=SkillSource.PLUGIN,
                scope=SkillScope.USER,
                required_fields=frozenset({"name", "description"}),
                default_version=manifest.version,
                lenient=True,
            )
        except SkillParseError:
            warnings.append(f"skill quebrada ignorada: {skill_file.parent.name}")
            continue
        # Plugin skill identities are namespaced by the plugin and use the
        # skill's public name.  Do not let an optional local `id` field in
        # SKILL.md change that cross-plugin reference.
        skill_id = f"{manifest.plugin_id}:{plugin_id_from_name(skill.name)}"
        skills.append(SkillContribution(skill_id, skill.name, skill.description, skill_file.relative_to(path).as_posix()))
    def _namespaced(item: McpServerContribution) -> McpServerContribution:
        return McpServerContribution(
            f"{manifest.plugin_id}-{item.slug}", item.display_name, item.transport,
            item.command, item.args, item.url, item.secret_names,
        )

    # A separate .mcp.json is the more specific declaration, so it wins a slug
    # collision with a server declared inline in plugin.json.
    merged: dict[str, McpServerContribution] = {item.slug: item for item in manifest.mcp_servers}
    mcp_path = path / manifest.mcp_config_path
    if mcp_path.exists():
        for item in parse_mcp_config(json.loads(mcp_path.read_text(encoding="utf-8"))):
            merged[item.slug] = item
    mcp_servers = tuple(_namespaced(item) for item in list(merged.values())[:16])
    from agentos.agentic.agent_definitions import parse_agent_file

    agents: list[AgentContribution] = []
    agent_files = [item for folder in manifest.agents_paths if (path / folder).is_dir() for item in sorted((path / folder).glob("*.md"))]
    for item in agent_files[:64]:
        try:
            definition = parse_agent_file(item, origin="plugin")
        except (OSError, ValueError, UnicodeDecodeError):
            warnings.append(f"subagente quebrado ignorado: {item.name}")
            continue
        agents.append(AgentContribution(
            f"{manifest.plugin_id}:{plugin_id_from_name(definition.name)}", definition.name,
            definition.prompt or definition.description, item.relative_to(path).as_posix(),
        ))
    commands, command_warnings = parse_commands(path / manifest.commands_path, plugin_id=manifest.plugin_id)
    warnings.extend(command_warnings)
    # parse_commands only sees the commands directory, so relative_path comes
    # back relative to it (e.g. "daily.md"). Rewrite it here, relative to the
    # package root instead, matching how skills and agents already report
    # their own relative_path — so a CommandLibrary can resolve
    # install_path / relative_path directly and never needs to separately
    # track (and risk drifting from) the manifest's declared commands path.
    commands = tuple(
        replace(item, relative_path=f"{manifest.commands_path}/{item.relative_path}")
        for item in commands
    )
    hooks, hook_warnings = parse_hooks(path / manifest.hooks_path, plugin_id=manifest.plugin_id)
    warnings.extend(hook_warnings)
    return PluginInspection(
        PluginRef(manifest.plugin_id, manifest.version), manifest.display_name, manifest.description,
        manifest.author, manifest.homepage, package_digest, skills=tuple(skills), mcp_servers=mcp_servers,
        agents=tuple(agents), commands=commands, hooks=hooks, warnings=tuple(warnings),
    )
