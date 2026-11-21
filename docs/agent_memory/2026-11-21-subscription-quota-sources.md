# Spike passivo: fontes locais de cota das assinaturas (WS-E5)

- **Data:** 2026-11-21
- **Escopo:** inspeção passiva (sem gastar cota de modelo, sem registrar tokens nem valores sensíveis) das fontes locais existentes na máquina para Claude CLI, Codex CLI e Antigravity CLI.

## Achados estruturais

1. **Claude CLI (`~/.claude.json`):**
   - Arquivo presente (~99 KB).
   - `oauthAccount.hasExtraUsageEnabled`: chave presente (booleano).
   - `cachedGrowthBookFeatures.tengu_usage_overage_included_models`: chave presente (lista de nomes de modelos cobertos por overage).
   - `cachedUsageUtilization`: **ausente** na foto atual do arquivo (confirmando que se trata de chave interna de cache sem contrato estável — presente quando o CLI atualizou o cache recentemente, mas pode não existir). O `ClaudeAccountReader` e o `ClaudeUsageCacheReader` devem tratar ausência sem exceção e nunca ler nem reter chaves fora dessas três.
   - `rate_limit_event` (`providers/claude_cli/stream.py`): `unifiedWindows.{five_hour, seven_day}.{utilization, resetsAt}` usa fração (`0.0..1.0`, ex.: `0.45`, `1.0`) nos fixtures existentes, enquanto `cachedUsageUtilization` traz percentuais (`0..100`, ex.: `77`). O normalizador (`agentos.quota.normalize`) trata explicitamente as duas escalas.

2. **Codex CLI (`~/.codex/sessions/AAAA/MM/DD/rollout-*.jsonl`):**
   - Pasta presente com 111 arquivos `rollout-*.jsonl`.
   - Os 3 rollouts mais recentes contêm registros `type: "event_msg"` com `payload.type: "token_count"` e `payload.rate_limits`.
   - Chaves observadas em `payload.rate_limits`: `credits`, `individual_limit`, `limit_id`, `limit_name`, `plan_type`, `primary`, `rate_limit_reached_type`, `secondary`, `spend_control_reached`.
   - `primary` (`resets_at`, `used_percent`, `window_minutes: 300` -> `five_hour`) e `secondary` (`resets_at`, `used_percent`, `window_minutes: 10080` -> `seven_day`). A janela é identificada por `window_minutes`, não pela posição `primary`/`secondary`.

3. **Antigravity CLI (`agy`):**
   - Sem subcomando ou arquivo local com percentuais de cota.
   - Coleta passiva restrita a erros 429/quota (`source="error"`, `status="rejected"`, com `resets_at` quando extraível). Sem erro recente, o serviço reporta `confidence="unknown"` e `windows=[]`, nunca inventando percentual.

## Decisão de coleta

- Coleta passiva ativa com zero custo de cota:
  - Claude CLI: `rate_limit_event` no stream + `cachedUsageUtilization` via `ClaudeAccountReader` + erros 429/quota.
  - Codex CLI: leitura incremental/reversa dos últimos 2 MB dos 3 `rollout-*.jsonl` mais recentes + erros 429/quota.
  - Antigravity CLI: somente erros 429/quota; fora isso `unknown`.
  - Todos os provedores: agregação `orin_usage` (tokens e chamadas em 5h e 7d) a partir de `conversation_agent_usage`.
