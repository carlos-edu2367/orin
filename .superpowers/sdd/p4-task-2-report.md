# Plan 4 Task 2 — Relatório de implementação

## Resultado

Implementada a delegação paralela de subagentes para partes independentes da mesma rodada, mantendo o comportamento de `ask_agent` para uma tarefa única e preservando os limites explícitos de Task 1.

## Alterações

- `TurnSession` agora mantém `_subagent_lock`, protegendo a verificação e o incremento de `_subagent_runs` como uma operação atômica.
- Adicionado `TurnSession._ask_agents(requests)` com:
  - validação de lista não vazia e de cada item `{name, task}`;
  - caminho direto para uma única tarefa, preservando `ask_agent`;
  - execução concorrente via `ThreadPoolExecutor`;
  - máximo de workers limitado por `MAX_SUBAGENTS_PER_TURN`;
  - agregação determinística na ordem original dos pedidos;
  - relatório único com contagem de solicitados/concluídos e códigos de falha.
- `AgentToolset` agora aceita `delegate_batch`, expõe `ask_agents` somente quando subagentes estão habilitados e mantém `ask_agent` separado.
- O schema de `ask_agents` exige `tasks`, um array de objetos com `name` e `task`, incluindo a orientação de que o subagente não vê a conversa.
- A orientação de prompt informa quando usar `ask_agents` para tarefas independentes e mantém as regras existentes sobre contexto, criação e uso econômico de subagentes.
- Não foram alterados os limites de output, ações ou contexto de Task 1, nem o ciclo de estados, ledger, cópia pública Orin ou ordenação de resultados provider/tool.
- Todos os módulos Python alterados começam com `from __future__ import annotations`.

## TDD e verificação

1. Adicionados os dois testes especificados em `tests/unit/agentic/test_turn_session.py`.
2. Execução RED confirmada:
   - `2 failed, 29 deselected`;
   - falha esperada: `AttributeError: 'TurnSession' object has no attribute '_ask_agents'`.
3. Após a implementação, o focused suite passou:
   - `2 passed, 29 deselected`.
4. Suite agentic passou:
   - `111 passed, 2 skipped`.
5. `git diff --check` não encontrou erros de whitespace.

## Self-review

- O contador é reservado sob lock antes de iniciar o runtime, portanto uma chamada em lote não excede o orçamento por corrida entre threads.
- `pool.map` conserva a ordem dos resultados mesmo quando os runtimes concluem em ordem diferente.
- O executor é limitado a quatro workers; tarefas acima desse limite são rejeitadas individualmente pelo orçamento já protegido.
- A tool batch não aparece no toolset de subagentes, pois `_toolset(subagents=False)` não injeta nenhum delegate.
- A implementação é restrita aos três arquivos da tarefa, além deste relatório.

## Concerns

- Os dois skips existentes são testes de symlink condicionais ao ambiente Windows; não são introduzidos por esta mudança.
- O `ThreadPoolExecutor` aguarda a conclusão de todas as tarefas do lote antes de retornar, conforme o contrato de `ask_agents`.

## Round 1 — correção do review

### Problemas corrigidos

- Cada worker do lote agora captura exceções inesperadas de `_ask_agent` e as converte em um `ToolOutcome` falho com `SUBAGENT_EXCEPTION`. Assim, `ThreadPoolExecutor.map` não aborta a coleta e os resultados das outras tarefas continuam no relatório, na ordem original.
- O status agregado agora é `succeeded` somente quando todas as tarefas são bem-sucedidas. Um lote misto permanece `failed`, conserva os conteúdos bem-sucedidos e falhos e usa `SUBAGENT_FAILED`; o caso em que todas as falhas são de orçamento continua usando `SUBAGENT_LIMIT`.

### Testes de regressão

- `test_a_worker_exception_is_reported_without_discarding_other_batch_results`
- `test_a_mixed_batch_reports_success_and_failure_as_failed`

O ciclo TDD foi confirmado: os dois testes falharam antes da correção — exceção escapando em `pool.map` e status incorretamente `succeeded` — e passaram após a implementação (`2 passed, 31 deselected`).
