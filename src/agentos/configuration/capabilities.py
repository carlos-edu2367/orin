"""What this instance offers. The single source of truth for every gate.

The gateway, the agent runtime, the MCP service, the hook engine and the web
client all read this object instead of re-deriving policy from the mode, so a
later stage can flip one field (e.g. ``shell`` once a sandbox exists) without
touching the places that consult it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from .mode import RuntimeMode


class CapabilityUnavailable(RuntimeError):
    def __init__(self, capability: str) -> None:
        super().__init__(f"capability '{capability}' is not available on this instance")
        self.capability = capability


@dataclass(frozen=True, slots=True)
class InstanceCapabilities:
    shell: bool
    mcp_stdio: bool
    plugin_hooks: bool
    omniroute: bool
    host_folders: bool
    profile_files: bool
    open_in_desktop_app: bool
    ui_updater: bool
    user_admin: bool

    @classmethod
    def for_mode(cls, mode: RuntimeMode) -> "InstanceCapabilities":
        if mode is RuntimeMode.SERVER:
            # Everything that would start a process on the host stays closed
            # until the per-profile sandbox (stage 2) exists.
            return cls(
                shell=False, mcp_stdio=False, plugin_hooks=False, omniroute=False,
                host_folders=False, profile_files=True, open_in_desktop_app=False,
                ui_updater=False, user_admin=True,
            )
        return cls(
            shell=True, mcp_stdio=True, plugin_hooks=True, omniroute=True,
            host_folders=True, profile_files=False, open_in_desktop_app=True,
            ui_updater=True, user_admin=False,
        )

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)

    def require(self, name: str) -> None:
        if name not in {item.name for item in fields(self)}:
            raise KeyError(name)
        if not getattr(self, name):
            raise CapabilityUnavailable(name)


__all__ = ["CapabilityUnavailable", "InstanceCapabilities"]
