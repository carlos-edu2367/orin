"""Hooks declared under the ``hooks`` key of the settings layers (Claude Code schema)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentos.agentic.lifecycle import HookEvent
from agentos.settings import EffectiveSettings

HANDLER_TYPES = ("command", "http", "prompt", "agent")
DEFAULT_TIMEOUTS = {"command": 60, "http": 30, "prompt": 30, "agent": 120}
MAX_TIMEOUT = 600
MAX_HOOKS = 200
MAX_TEXT = 8_000
_PRECEDENCE = ("managed", "cli", "local", "project", "user")


@dataclass(frozen=True, slots=True)
class HookSpec:
    event: HookEvent
    matcher: str
    type: str
    source: str
    timeout: float
    command: str = ""
    url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    allowed_env_vars: tuple[str, ...] = ()
    prompt: str = ""
    model: str = ""
    run_async: bool = False
    on_failure: str = "ignore"
    status_message: str = ""

    def identity(self) -> tuple[Any, ...]:
        return (self.event, self.type, self.command, self.url, self.prompt, self.matcher)

    def label(self) -> str:
        if self.type == "command":
            return self.command[:120]
        if self.type == "http":
            return self.url[:120]
        return f"{self.type}: {self.prompt[:80]}"


@dataclass(frozen=True, slots=True)
class HookConfigError:
    source: str
    message: str


def _timeout(raw: Any, kind: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return float(DEFAULT_TIMEOUTS[kind])
    return float(min(MAX_TIMEOUT, raw))


def _entry(event: HookEvent, matcher: str, source: str, raw: Any) -> HookSpec | str:
    if not isinstance(raw, Mapping):
        return "hook entry must be an object"
    kind = str(raw.get("type") or "command")
    if kind not in HANDLER_TYPES:
        return f"unsupported hook type '{kind}'"
    common = {
        "event": event, "matcher": matcher, "type": kind, "source": source,
        "timeout": _timeout(raw.get("timeout"), kind),
        "on_failure": "block" if raw.get("onFailure") == "block" else "ignore",
        "status_message": str(raw.get("statusMessage") or "")[:200],
    }
    if kind == "command":
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_TEXT:
            return "command hook needs a non-empty 'command' (at most 8000 characters)"
        return HookSpec(**common, command=command.strip(), run_async=raw.get("async") is True)
    if kind == "http":
        url = raw.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://", url.strip(), re.IGNORECASE) or len(url) > 2048:
            return "http hook needs an http(s) 'url'"
        headers = raw.get("headers") if isinstance(raw.get("headers"), Mapping) else {}
        pairs = tuple((str(key)[:200], str(value)[:4000]) for key, value in headers.items() if isinstance(value, (str, int, float)))
        allowed = tuple(str(item) for item in raw.get("allowedEnvVars") or () if isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item))
        return HookSpec(**common, url=url.strip(), headers=pairs, allowed_env_vars=allowed)
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_TEXT:
        return f"{kind} hook needs a non-empty 'prompt'"
    model = raw.get("model") if isinstance(raw.get("model"), str) else ""
    return HookSpec(**common, prompt=prompt.strip(), model=model.strip()[:200])


def load_hook_specs(settings: EffectiveSettings) -> tuple[tuple[HookSpec, ...], tuple[HookConfigError, ...]]:
    layers = list(settings.layers)
    # W2-R3: "disableAllHooks" is decided per layer, never on the merged value. Only managed settings
    # can switch off managed hooks. A user or CLI switch switches off every non-managed layer; a
    # repository switch counts only once the workspace is trusted (the loader drops it before that).
    if any(layer.name == "managed" and layer.data.get("disableAllHooks") is True for layer in layers):
        return (), ()
    switchable = {"user", "cli"} | ({"project", "local"} if settings.trusted else set())
    non_managed_off = any(layer.name in switchable and layer.data.get("disableAllHooks") is True for layer in layers)
    managed_only = any(layer.name == "managed" and layer.data.get("allowManagedHooksOnly") is True for layer in layers)
    ordered = sorted(layers, key=lambda layer: _PRECEDENCE.index(layer.name) if layer.name in _PRECEDENCE else len(_PRECEDENCE))
    specs: list[HookSpec] = []
    errors: list[HookConfigError] = []
    for layer in ordered:
        if (managed_only or non_managed_off) and layer.name != "managed":
            continue
        declared = layer.data.get("hooks")
        if declared is None:
            continue
        source = layer.name
        if not isinstance(declared, Mapping):
            errors.append(HookConfigError(source, "'hooks' must be an object"))
            continue
        for event_name, groups in declared.items():
            try:
                event = HookEvent(str(event_name))
            except ValueError:
                errors.append(HookConfigError(source, f"unknown hook event '{event_name}'"))
                continue
            if not isinstance(groups, list):
                errors.append(HookConfigError(source, f"hooks.{event_name} must be a list"))
                continue
            for group in groups:
                if not isinstance(group, Mapping):
                    errors.append(HookConfigError(source, f"hooks.{event_name} entries must be objects"))
                    continue
                matcher = str(group.get("matcher") or "")
                if len(matcher) > 500:
                    errors.append(HookConfigError(source, "matcher is longer than 500 characters"))
                    continue
                if matcher and matcher != "*":
                    try:
                        re.compile(matcher)
                    except re.error:
                        errors.append(HookConfigError(source, f"invalid matcher '{matcher[:60]}'"))
                        continue
                entries = group.get("hooks")
                if not isinstance(entries, list):
                    errors.append(HookConfigError(source, f"hooks.{event_name}[].hooks must be a list"))
                    continue
                for raw in entries:
                    parsed = _entry(event, matcher, source, raw)
                    if isinstance(parsed, str):
                        errors.append(HookConfigError(source, parsed))
                    else:
                        specs.append(parsed)
    if len(specs) > MAX_HOOKS:
        errors.append(HookConfigError("settings", f"more than {MAX_HOOKS} hooks are declared; the rest are ignored"))
        specs = specs[:MAX_HOOKS]
    return tuple(specs), tuple(errors)
