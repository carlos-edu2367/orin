"""Match a lifecycle event to the hooks that asked for it, and run them.

A hook failure is contained here: the engine always returns outcomes and never
raises into the turn that triggered it. Nothing a hook returns can deny an
action — see ``HookOutcome``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from threading import RLock

from .hook_executor import HookExecutor, HookOutcome
from .models import HookContribution


@dataclass(frozen=True, slots=True)
class RegisteredHook:
    plugin_id: str
    install_path: Path
    contribution: HookContribution


class HookEngine:
    def __init__(self, *, executor=None) -> None:
        self.executor = executor or HookExecutor()
        self._hooks: dict[str, list[RegisteredHook]] = {}
        self._lock = RLock()

    def register(self, *, user_id: str, plugin_id: str, install_path: Path, hooks, enabled: bool) -> None:
        with self._lock:
            registry = self._hooks.setdefault(user_id, [])
            registry[:] = [item for item in registry if item.plugin_id != plugin_id]
            if not enabled:
                return
            registry.extend(RegisteredHook(plugin_id, Path(install_path), item) for item in hooks)

    def unregister(self, *, user_id: str, plugin_id: str) -> None:
        with self._lock:
            registry = self._hooks.get(user_id, [])
            registry[:] = [item for item in registry if item.plugin_id != plugin_id]

    def dispatch(self, *, user_id: str, event: str, payload: dict) -> tuple[HookOutcome, ...]:
        with self._lock:
            candidates = [item for item in self._hooks.get(user_id, []) if item.contribution.event == event]
        tool_name = str(payload.get("tool_name") or "")
        outcomes: list[HookOutcome] = []
        for item in candidates:
            matcher = item.contribution.matcher
            if matcher and not re.search(matcher, tool_name):
                continue
            try:
                outcomes.append(self.executor.run(
                    command=item.contribution.command, install_path=item.install_path,
                    payload={"event": event, **payload}, timeout_seconds=item.contribution.timeout_seconds,
                    hook_id=item.contribution.hook_id,
                ))
            except Exception as error:  # noqa: BLE001 - a hook never breaks the turn
                outcomes.append(HookOutcome(
                    item.contribution.hook_id, "failed", "", "", None, f"{type(error).__name__}: {error}"
                ))
        return tuple(outcomes)
