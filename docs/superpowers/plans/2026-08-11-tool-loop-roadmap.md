# Orin — Roadmap de Correção do Fluxo de Tools

> **Índice de planos.** Não execute este arquivo: ele aponta para os planos executáveis.

**Contexto:** auditoria do fluxo de tools do agente conversacional (2026-08-11). 16 problemas
confirmados e 14 melhorias propostas, agrupados em 5 planos independentes. Cada plano entrega
software funcionando e testado por conta própria e pode ser executado isoladamente.

## Ordem recomendada

| Ordem | Plano | Entrega | Depende de |
|---|---|---|---|
| 1 | [Tool Surface](2026-08-11-tool-surface.md) | busca, leitura paginada, edição em lote, listagem recursiva | — |
| 2 | [Turn Loop Efficiency](2026-08-11-turn-loop-efficiency.md) | prompt caching, execução paralela, fecho gracioso, orçamento de contexto | Plano 1 |
| 3 | [Context Continuity](2026-08-11-context-continuity.md) | histórico de tools entre turns, ambiente e árvore no prompt | Planos 1 e 2 |
| 4 | [Subagent Delegation](2026-08-11-subagent-delegation.md) | subagentes paralelos, limites corretos | Planos 1, 2 e 3 |
| 5 | [Agent Capabilities](2026-08-11-agent-capabilities.md) | web search, browser, execução com política | Planos 1 e 2 |

**A ordem é sequencial, não paralela.** Os planos compartilham três pontos de contato concretos:
`_build_definitions` (renomeado no Plano 1 Task 5 e editado pelos Planos 4 e 5), `search_files`
(criada no Plano 1 e marcada `read_only` no Plano 2) e `_run_toolset` (reescrito no Plano 2 e
editado no Plano 3). Executar fora de ordem exige reconciliar esses três à mão.

## Rastreamento problema → tarefa

| # | Problema auditado | Plano.Tarefa |
|---|---|---|
| 1 | Não existe busca de conteúdo no workspace | 1.1 |
| 2 | `read_file` sem paginação; corte silencioso em 12k chars | 1.2 |
| 3 | `list_files` não é recursivo | 1.3 |
| 4 | `edit_file` faz uma substituição por chamada | 1.4 |
| 5 | Truncamento não ensina como continuar | 1.5 |
| 6 | Zero prompt caching | 2.1 |
| 7 | `stream_options.include_usage` ausente (usage perdido em stream OpenAI) | 2.1 |
| 8 | Tool calls executam em série | 2.2 |
| 9 | `ITERATION_LIMIT` descarta o trabalho da turn | 2.3 |
| 10 | Nada impede repetir a mesma chamada falha | 2.4 |
| 11 | Janela fixa de 32 mensagens derruba o pedido do usuário | 2.5 |
| 12 | Resultados de tool ficam inteiros no contexto até o fim da turn | 2.6 |
| 13 | Tool results não persistem entre turns | 3.1, 3.2 |
| 14 | Agente não conhece o ambiente (OS/shell) nem o workspace | 3.3 |
| 15 | Prompt induz serialização em vez de batching | 3.4 |
| 16 | Subagentes travados em 1024 tokens de saída | 4.1 |
| 17 | `ask_agent` é bloqueante e serial | 4.2 |
| 18 | Sem web search | 5.1 |
| 19 | Browser existe mas não é exposto ao agente | 5.2 |
| 20 | Dois catálogos de tools desconectados | 5.3 |
| 21 | `activity_for` é código morto | 1.5 (limpeza) |
| 22 | `definitions()` reconstruído a cada `resolve` | 1.5 (cache) |

## Convenções válidas para todos os planos

- Nome público do produto é **Orin**; identificadores internos permanecem `agentos`. Nenhum plano
  renomeia módulo, chave de storage, env var ou nome de API.
- Python 3.12+, `from __future__ import annotations` no topo de todo módulo novo.
- Testes com `pytest`, em `tests/unit/<pacote>/test_<assunto>.py`, seguindo o estilo de
  `tests/unit/agentic/test_agent_tools.py` (fixture `toolset`, asserts sobre `ToolOutcome`).
- Rodar a suíte de um arquivo: `uv run pytest tests/unit/agentic/test_agent_tools.py -v`
- Commits pequenos, um por tarefa, prefixo `feat:`/`fix:`/`refactor:`/`perf:`.
