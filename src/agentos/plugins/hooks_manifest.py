"""Read a plugin's ``hooks.json`` in the Claude Code shape.

Only the three events Orin dispatches are recognized. Everything else — a
different event, a non-command hook type, ``async: true`` — is warned rather
than silently dropped, so the author's declared intent stays visible to the
person deciding whether to authorize execution.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from .models import HookContribution

SUPPORTED_EVENTS = ("PostCompact", "PostToolUse", "SessionStart")
MAX_HOOKS = 32
MIN_TIMEOUT, DEFAULT_TIMEOUT, MAX_TIMEOUT = 1, 10, 30


def parse_hooks(root: Path, *, plugin_id: str) -> tuple[tuple[HookContribution, ...], tuple[str, ...]]:
    target = Path(root) / "hooks.json"
    if not target.is_file():
        return (), ()
    warnings: list[str] = []
    try:
        payload: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (), ("hooks.json não pôde ser lido; nenhum hook foi declarado",)
    declared = payload.get("hooks") if isinstance(payload, Mapping) else None
    if not isinstance(declared, Mapping):
        return (), ("hooks.json não declara um objeto 'hooks'",)

    hooks: list[HookContribution] = []
    for event in sorted(str(key) for key in declared):
        if event not in SUPPORTED_EVENTS:
            warnings.append(f"evento '{event}' declarado; não suportado nesta versão e não será executado")
            continue
        groups = declared[event]
        index = 0
        for group in groups if isinstance(groups, list) else ():
            if not isinstance(group, Mapping):
                continue
            matcher = str(group.get("matcher") or "")
            try:
                re.compile(matcher)
            except re.error:
                warnings.append(f"matcher inválido em '{event}'; o hook foi ignorado")
                continue
            for entry in group.get("hooks") if isinstance(group.get("hooks"), list) else ():
                if not isinstance(entry, Mapping):
                    continue
                if str(entry.get("type") or "") != "command":
                    warnings.append(f"hook de tipo '{entry.get('type')}' em '{event}' não é suportado")
                    continue
                command = str(entry.get("command") or "").strip()
                if not command:
                    continue
                if entry.get("async") is True:
                    warnings.append(f"hook com \"async\" em '{event}' será executado de forma síncrona, não assíncrona")
                raw_timeout = entry.get("timeout")
                timeout = int(raw_timeout) if isinstance(raw_timeout, (int, float)) else DEFAULT_TIMEOUT
                hooks.append(HookContribution(
                    f"{plugin_id}:{event}:{index}", event, matcher, command[:2048],
                    max(MIN_TIMEOUT, min(MAX_TIMEOUT, timeout)),
                ))
                index += 1
    if len(hooks) > MAX_HOOKS:
        warnings.append(f"o plugin declara mais de {MAX_HOOKS} hooks; o restante foi ignorado")
    return tuple(hooks[:MAX_HOOKS]), tuple(warnings)
