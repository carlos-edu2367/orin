from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from agentos.skills.models import SkillScope, SkillSource
from agentos.skills.parser import parse_skill_file

from .models import PluginInspection


class ActivationFailed(RuntimeError):
    pass


class PluginActivator:
    def __init__(self, *, skill_library, mcp_service, agent_templates=None, command_library=None, commands_path: str = "commands", hook_engine=None) -> None:
        self.skill_library = skill_library
        self.mcp_service = mcp_service
        self.agent_templates = agent_templates
        self.command_library = command_library
        self.commands_path = commands_path
        self.hook_engine = hook_engine

    def activate(self, *, user_id: str, install_path: Path, inspection: PluginInspection):
        installed_skills = []
        server_ids: list[str] = []
        contributions: list[dict[str, Any]] = []
        try:
            for item in inspection.skills:
                # Claude Code plugin skills version the plugin in plugin.json,
                # rather than repeating a version in every SKILL.md.  The
                # inspector already validated that shape and synthesized the
                # version from the manifest; activation must use the same
                # contract when it loads the full instructions.
                skill = parse_skill_file(
                    Path(install_path) / item.relative_path,
                    include_instructions=True,
                    source=SkillSource.PLUGIN,
                    scope=SkillScope.USER,
                    required_fields=frozenset({"name", "description"}),
                    default_version=inspection.ref.version,
                )
                installed_skills.append(replace(skill, id=item.skill_id, package_path=Path(install_path) / item.relative_path))
            self.skill_library.install_plugin_skills(user_id=user_id, plugin_id=inspection.ref.plugin_id, skills=tuple(installed_skills))
            contributions.extend({"kind": "skill", "reference": item.skill_id, "display_name": item.name} for item in inspection.skills)
            for item in inspection.mcp_servers:
                proposed = self.mcp_service.propose({
                    "user_id": user_id, "slug": item.slug, "display_name": item.display_name,
                    "transport": item.transport, "command": item.command, "args": list(item.args), "url": item.url,
                    "secret_names": list(item.secret_names), "catalog_id": None, "tool_allowlist": None,
                })
                server_id = str(proposed["server_id"])
                server_ids.append(server_id)
                contributions.append({"kind": "mcp_server", "reference": server_id, "display_name": item.display_name})
            for item in inspection.agents:
                contribution = {"kind": "agent", "reference": item.agent_id, "display_name": item.name}
                contributions.append(contribution)
                if self.agent_templates is not None and hasattr(self.agent_templates, "register"):
                    self.agent_templates.register(user_id=user_id, plugin_id=inspection.ref.plugin_id, agent_id=item.agent_id, name=item.name, role=item.role, path=str(Path(install_path) / item.relative_path))
            if inspection.commands:
                contributions.extend(
                    {"kind": "command", "reference": item.command_id, "display_name": item.slug}
                    for item in inspection.commands
                )
                if self.command_library is not None:
                    self.command_library.install_plugin_commands(
                        user_id=user_id, plugin_id=inspection.ref.plugin_id,
                        install_path=Path(install_path), commands=inspection.commands,
                        commands_path=self.commands_path,
                    )
            if inspection.hooks:
                # Installing is not authorizing execution: hook rows start
                # disabled and only the explicit consent action turns them on.
                contributions.extend(
                    {"kind": "hook", "reference": item.hook_id, "display_name": item.event, "enabled": False}
                    for item in inspection.hooks
                )
                if self.hook_engine is not None:
                    self.hook_engine.register(
                        user_id=user_id, plugin_id=inspection.ref.plugin_id,
                        install_path=Path(install_path), hooks=inspection.hooks, enabled=False,
                    )
            return type("ActivationResult", (), {"contributions": contributions})()
        except Exception as error:
            for server_id in reversed(server_ids):
                try:
                    self.mcp_service.remove(user_id, server_id)
                except Exception:
                    pass
            if self.command_library is not None:
                try:
                    self.command_library.remove_plugin_commands(user_id=user_id, plugin_id=inspection.ref.plugin_id)
                except Exception:
                    pass
            if self.hook_engine is not None:
                try:
                    self.hook_engine.unregister(user_id=user_id, plugin_id=inspection.ref.plugin_id)
                except Exception:
                    pass
            try:
                self.skill_library.remove_plugin_skills(user_id=user_id, plugin_id=inspection.ref.plugin_id)
            except Exception:
                pass
            raise ActivationFailed("plugin contributions could not be activated") from error

    def deactivate(self, *, user_id: str, plugin_id: str, contributions) -> None:
        for item in reversed(tuple(contributions)):
            if item.get("kind") == "mcp_server":
                try:
                    self.mcp_service.remove(user_id, str(item["reference"]))
                except Exception:
                    pass
        if self.command_library is not None:
            self.command_library.remove_plugin_commands(user_id=user_id, plugin_id=plugin_id)
        if self.hook_engine is not None:
            self.hook_engine.unregister(user_id=user_id, plugin_id=plugin_id)
        self.skill_library.remove_plugin_skills(user_id=user_id, plugin_id=plugin_id)
