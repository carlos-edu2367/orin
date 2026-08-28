# Trilha A: por que o agente era pouco assertivo, e o que mudou

Data: 2026-08-28 · Base: `v0.2.11` (`876bd01`)

## O achado que explica quase tudo

`ChatApplication.history_for_turn` devolvia só mensagens `user`/`assistant`. Nenhuma `tool_call` ou `tool_result` sobrevivia ao fim do turno — a `CheckConstraint` de `conversation_messages` só admite esses dois papéis. O resíduo era o `tool_ledger`: 20 linhas com argumentos cortados em 120 caracteres.

Consequência: "reformule esse orçamento" começava sem saber o que foi lido, o que foi escrito, nem com que números. O agente redescobria tudo. É a origem principal do excesso de chamadas que o usuário relatava.

## Os quatro amplificadores

1. **Orçamento ausente.** O padrão de `max_iterations` é `None`, convertido em `max_actions=None`, com deadline de 3600s. O loop só parava quando o modelo decidia parar.
2. **Janela travada em 60k.** `min(60_000, window - 12_000)` dava 60k a um modelo de 200k e 60k a um de 1M. Compactação disparava a ~49k.
3. **O sistema instruía o desperdício.** O marcador de corte dizia literalmente "re-read files or re-run searches"; o cabeçalho da compactação dizia "use os arquivos e ferramentas para confirmar detalhes".
4. **~50 ferramentas em toda requisição.** Sem disclosure progressivo.

## O que ficou

- `conversation_turn_steps` (migration 0042): trajetória durável, provider-neutra, reidratada com 40% da janela do turno, do mais recente para o mais antigo, em unidades `tool_call`+`tool_result` atômicas.
- `turn_quality_metrics` (migration 0041): a linha de base. `redundant_tool_calls` conta repetição **bem-sucedida** — repetição de falha já é curto-circuitada pelo runtime e contá-la inflaria a própria métrica.
- Fases determinísticas com toolset por fase, contrato de tarefa pinado fora da lista de mensagens, compactação em quatro seções fixas.

## Decisões que valem lembrar

- **`num_ctx` do Ollama não acompanha a janela liberada.** Aquele número é VRAM na máquina da pessoa, não tokens cobrados por um provider. Manter os dois orçamentos separados foi deliberado; um teste antigo afirmava a identidade entre eles e foi reescrito para o invariante real.
- **`orient` carrega as ferramentas de trabalho.** Fases disjuntas teriam encarecido a tarefa simples de 2 para 4–5 chamadas. Ver §15.1 do spec.
- **Verificação obrigatória depende de mudar o streaming da resposta**, e por isso ficou para a Trilha C. Ver §15.3.

## Pendências conhecidas

- O alvo de "queda de 50% em chamadas por tarefa" só é comprovável rodando `scripts/agent_bench.py` com credencial real. A máquina de medição existe; o número não foi produzido.
- Trilha B (cache, prompt estático, dedup de sucesso, tiering de MCP) não foi iniciada. O maior item lá: `_age_tool_results` muta mensagens no lugar **depois** de cada requisição, então o prefixo cacheado diverge a cada iteração e o cache do Anthropic nunca acerta.
