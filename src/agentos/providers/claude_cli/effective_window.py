"""Effective context-window resolver (E-RF28, E-RF31).

Pure module without database or storage imports: learned limits, overrides, and account
states are passed in as plain values so `providers/` stays cleanly separated from storage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentos.quota.claude_account import ClaudeAccountState

MIN_OVERRIDE_TOKENS = 50_000
MAX_OVERRIDE_TOKENS = 1_000_000
DEFAULT_FALLBACK_WINDOW = 200_000
ACCOUNT_CREDITS_CLAMP_WINDOW = 200_000
LONG_CONTEXT_1M_WINDOW = 1_000_000

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CLAUDE_CLI_PROVIDERS: frozenset[str] = frozenset({"anthropic", "anthropic-cli", "claude-cli", "claude_cli"})


@dataclass(frozen=True, slots=True)
class EffectiveWindow:
    tokens: int
    source: str
    detail: str
    rule_index: int
    cli_model_arg: str | None = None


def _format_tokens_pt(tokens: int) -> str:
    if tokens >= 1_000_000 and tokens % 1_000_000 == 0:
        return f"{tokens // 1_000_000} M"
    if tokens >= 1_000 and tokens % 1_000 == 0:
        return f"{tokens // 1_000} mil"
    return f"{tokens:,}".replace(",", ".")


def _bare_model_id(model_id: str) -> str:
    raw = str(model_id or "").strip()
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1].strip()
    return raw


def _strip_1m_suffix(model_id: str) -> str:
    bare = _bare_model_id(model_id)
    if bare.lower().endswith("[1m]"):
        return bare[:-4]
    return bare


def _normalize_model_tokens(value: str) -> tuple[str, ...]:
    cleaned = _strip_1m_suffix(value).lower()
    tokens = _TOKEN_RE.findall(cleaned)
    if tokens and tokens[0] == "claude":
        tokens = tokens[1:]
    return tuple(tokens)


def is_overage_model(model_id: str, overage_models: Iterable[str]) -> bool:
    """Match a model id (e.g. ``claude-fable-5-2``) against GrowthBook overage entries (e.g. ``"Fable 5.2"``)."""
    model_tokens = _normalize_model_tokens(model_id)
    if not model_tokens:
        return False
    for raw_entry in overage_models:
        entry_tokens = _normalize_model_tokens(str(raw_entry or ""))
        if not entry_tokens:
            continue
        if len(entry_tokens) <= len(model_tokens) and model_tokens[: len(entry_tokens)] == entry_tokens:
            return True
    return False


def account_credits_enabled(account_state: ClaudeAccountState | Any | None) -> bool | None:
    """Return whether extra usage / credits are enabled on the Claude account, or None if unknown."""
    if account_state is None:
        return None
    direct = getattr(account_state, "extra_usage_enabled", None)
    if isinstance(direct, bool):
        return direct
    usage_cache = getattr(account_state, "usage_cache", None)
    if isinstance(usage_cache, Mapping):
        util = usage_cache.get("utilization")
        if isinstance(util, Mapping):
            extra = util.get("extra_usage")
            if isinstance(extra, Mapping):
                for key in ("is_enabled", "enabled"):
                    val = extra.get(key)
                    if isinstance(val, bool):
                        return val
    return None


def _lookup_override(
    provider: str,
    model_id: str,
    window_overrides: Mapping[str, Any] | None,
) -> int | None:
    if not isinstance(window_overrides, Mapping) or not window_overrides:
        return None
    prov = str(provider or "").strip().lower()
    mdl = str(model_id or "").strip().lower()
    bare = _strip_1m_suffix(mdl).lower()

    candidate_keys = {
        key
        for key in (
            f"{prov}/{bare}" if prov and bare else "",
            f"{prov}/{mdl}" if prov and mdl else "",
            bare,
            mdl,
        )
        if key
    }
    if not candidate_keys:
        return None

    matched: list[int] = []
    for raw_k, raw_v in window_overrides.items():
        if not isinstance(raw_k, str):
            continue
        if raw_k.strip().lower() not in candidate_keys:
            continue
        if isinstance(raw_v, int) and not isinstance(raw_v, bool):
            if MIN_OVERRIDE_TOKENS <= raw_v <= MAX_OVERRIDE_TOKENS:
                matched.append(int(raw_v))
    return min(matched) if matched else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return int(value)
    return None


def effective_window_for(
    provider: str,
    model_id: str,
    *,
    window_overrides: Mapping[str, int] | None = None,
    project_window_overrides: Mapping[str, int] | None = None,
    managed_window_overrides: Mapping[str, int] | None = None,
    learned_limit: int | None = None,
    learned_source: str | None = None,
    account_state: ClaudeAccountState | Any | None = None,
    long_context_with_credits: bool = False,
    cli_catalog_window: int | None = None,
    fixed_catalog_window: int | None = None,
) -> EffectiveWindow:
    """Resolve the effective context window applying the 6 rules of E-RF28 in strict order."""
    prov = str(provider or "").strip().lower()
    bare = _strip_1m_suffix(model_id)

    # Rule 1a: Managed override ALWAYS wins (cannot be raised or lowered by user/project)
    managed_override = _lookup_override(prov, model_id, managed_window_overrides)
    if managed_override is not None:
        return EffectiveWindow(
            tokens=managed_override,
            source="override",
            detail=f"{_format_tokens_pt(managed_override)}, definido pela organização",
            rule_index=1,
            cli_model_arg=None,
        )

    project_override = _lookup_override(prov, model_id, project_window_overrides)

    # Rule 1b: Operator manual override (`context.windowOverrides` from user/cli, 50k..1M).
    # A project/local override may only lower an operator override, never raise it.
    override = _lookup_override(prov, model_id, window_overrides)
    if override is not None:
        tokens = min(override, project_override) if project_override is not None else override
        return EffectiveWindow(
            tokens=tokens,
            source="override",
            detail=f"{_format_tokens_pt(tokens)}, definido manualmente",
            rule_index=1,
            cli_model_arg=None,
        )

    # Compute base effective window from Rules 2..6 before applying any project-only lower bound.
    base_window: EffectiveWindow | None = None

    # Rule 2: Active learned window from overflow error (E-RF13)
    learned = _positive_int(learned_limit)
    if learned is not None:
        if str(learned_source or "").strip().lower() == "estimate":
            detail = f"{_format_tokens_pt(learned)}, estimado após erro do modelo"
        else:
            detail = f"{_format_tokens_pt(learned)}, aprendido após erro do modelo"
        base_window = EffectiveWindow(
            tokens=learned,
            source="learned",
            detail=detail,
            rule_index=2,
            cli_model_arg=None,
        )

    # Rule 3: Claude CLI account credits rule (E-RF28.3 & E-RF31)
    cli_win = _positive_int(cli_catalog_window)
    if base_window is None and prov in _CLAUDE_CLI_PROVIDERS and account_state is not None:
        overage_models = getattr(account_state, "overage_models", ()) or ()
        credits_on = account_credits_enabled(account_state)
        if overage_models and is_overage_model(bare, overage_models):
            if credits_on is False:
                base_window = EffectiveWindow(
                    tokens=ACCOUNT_CREDITS_CLAMP_WINDOW,
                    source="account_credits",
                    detail="200 mil, limite da conta sem créditos",
                    rule_index=3,
                    cli_model_arg=None,
                )
            elif credits_on is True and bool(long_context_with_credits):
                tokens_1m = cli_win if cli_win is not None else LONG_CONTEXT_1M_WINDOW
                base_window = EffectiveWindow(
                    tokens=tokens_1m,
                    source="credits_1m",
                    detail=f"{_format_tokens_pt(tokens_1m)}, créditos de uso ativos (1M)",
                    rule_index=3,
                    cli_model_arg=f"{bare}[1m]" if bare else None,
                )

    # Rule 4: Cached CLI catalog (`runtime.max_input_tokens`)
    if base_window is None and cli_win is not None:
        base_window = EffectiveWindow(
            tokens=cli_win,
            source="cli_catalog",
            detail=f"{_format_tokens_pt(cli_win)}, catálogo do CLI",
            rule_index=4,
            cli_model_arg=None,
        )

    # Rule 5: Orin fixed catalog table
    fixed_win = _positive_int(fixed_catalog_window)
    if base_window is None and fixed_win is not None:
        base_window = EffectiveWindow(
            tokens=fixed_win,
            source="fixed_catalog",
            detail=f"{_format_tokens_pt(fixed_win)}, catálogo do Orin",
            rule_index=5,
            cli_model_arg=None,
        )

    # Rule 6: Conservative fallback (200k)
    if base_window is None:
        base_window = EffectiveWindow(
            tokens=DEFAULT_FALLBACK_WINDOW,
            source="fallback",
            detail="200 mil, padrão",
            rule_index=6,
            cli_model_arg=None,
        )

    # A project/local override can NEVER raise the effective window above Rules 2..6; it may only lower it.
    if project_override is not None and project_override < base_window.tokens:
        return EffectiveWindow(
            tokens=project_override,
            source="override",
            detail=f"{_format_tokens_pt(project_override)}, limitado pelo projeto",
            rule_index=1,
            cli_model_arg=None,
        )

    return base_window


__all__ = [
    "ACCOUNT_CREDITS_CLAMP_WINDOW",
    "DEFAULT_FALLBACK_WINDOW",
    "EffectiveWindow",
    "LONG_CONTEXT_1M_WINDOW",
    "MAX_OVERRIDE_TOKENS",
    "MIN_OVERRIDE_TOKENS",
    "account_credits_enabled",
    "effective_window_for",
    "is_overage_model",
]
