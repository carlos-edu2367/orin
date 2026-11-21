"""Coleta passiva, normalização, consulta e política de cota das assinaturas CLI.

Achado do spike passivo (2026-11-21, sem chamadas de modelo nem leitura de tokens):
- Claude CLI (`~/.claude.json`): `oauthAccount.hasExtraUsageEnabled` e
  `cachedGrowthBookFeatures.tengu_usage_overage_included_models` estão presentes na máquina;
  `cachedUsageUtilization` é um cache interno opcional (pode estar ausente entre versões ou
  sessões). No stream `rate_limit_event`, `unifiedWindows.*.utilization` vem em fração
  (`0.0..1.0`), enquanto `cachedUsageUtilization` traz percentuais (`0..100`).
- Codex CLI (`~/.codex/sessions/**/rollout-*.jsonl`): 111 rollouts presentes; os 3 mais
  recentes trazem `type="event_msg"`, `payload.type="token_count"` e
  `payload.rate_limits.{primary,secondary}` com `used_percent`, `resets_at` (epoch em s) e
  `window_minutes` (`300` -> `five_hour`, `10080` -> `seven_day`).
- Antigravity CLI (`agy`): não publica percentual de cota localmente; coleta passiva se
  restringe a erros 429/quota (`source="error"`, `status="rejected"`). Sem erro recente,
  reporta `confidence="unknown"` e `windows=()`.
Ver nota completa em `docs/agent_memory/2026-11-21-subscription-quota-sources.md`.
"""

from __future__ import annotations

from .claude_account import ClaudeAccountReader, ClaudeAccountState
from .claude_reader import ClaudeUsageCacheReader
from .codex_reader import CodexQuotaReader
from .models import (
    Confidence,
    OrinUsageSummary,
    OrinUsageWindow,
    ProviderQuota,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
)

__all__ = [
    "ClaudeAccountReader",
    "ClaudeAccountState",
    "ClaudeUsageCacheReader",
    "CodexQuotaReader",
    "Confidence",
    "OrinUsageSummary",
    "OrinUsageWindow",
    "ProviderQuota",
    "QuotaSnapshot",
    "QuotaStatus",
    "QuotaWindow",
]
