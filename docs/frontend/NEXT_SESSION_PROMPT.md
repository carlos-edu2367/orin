Prompt da próxima sessão — Fases E, F, H e Verificação + Documentação final

Você é o agente responsável por implementar, nesta sessão, exatamente as Fases E, F, H e a Fase Verificação + Documentação descritas em `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md`. As Fases 0, B, C e D já estão concluídas e verificadas nesta branch (commit `688cb0b`, `feat(frontend-backend): compose production execution/security/event surface and bridge Fases B and D`): Security/Execution/ClientEventStream reais sobre Postgres (Fase 0), a ponte tool_runtime/multi_agent → `ClientEventStream` (Fase B), a Fase C fechada como limitação documentada (`result_ref` não é resolvível hoje — não reabra sem grep real novo que contradiga a investigação já registrada), e `ProviderConfigurationApplication` em produção (Fase D). Não refaça, não redesenhe nenhuma delas — apenas construa sobre o que já existe. Esta sessão fecha o escopo completo do `PROJECT_CLOSEOUT_ROADMAP.md`.

## Regra de conclusão para este escopo

Não finalize, não entregue resposta parcial e não declare sucesso enquanto:

* a Fase E (UI de input para `WAITING_USER`) não estiver implementada e comprovada por teste unitário + E2E, seguindo o padrão de fake documentado já estabelecido em `execution-controls.spec.ts`;
* a Fase F (bug de sobreposição no `AgentRail`) não estiver corrigida com o menor diff possível, e o workaround registrado em `reduced-motion.spec.ts` (clique extra "fecha antes de expandir") não tiver sido removido e o teste reexecutado sem ele;
* a Fase H (teste flaky `agentGraphProjection`) não tiver sua causa raiz investigada (rodando isolado vs. na suíte completa, com `--reporter=verbose`, múltiplas vezes) e corrigida — não apenas mitigada por timeout maior;
* a suíte completa do backend (`python -m pytest -q`, com e sem `AGENTOS_TEST_POSTGRES_DSN=postgresql://agentos@localhost:5433/agentos` setado — o Postgres do `docker-compose.yml` deste projeto já roda nessa porta) não estiver verde;
* `npm run test`, `npm run test:e2e` (specs novos/tocados rodados 2–3× isolados), `npm run test:visual`, `npm run lint` e `npm run build` (em `frontend/`) não estiverem verdes;
* a segunda passagem read-only independente (autorização/ownership dos adapters de B/C/D, ausência de fallback in-memory em produção, sanitização de payload na ponte de eventos, ausência de vazamento no result/input resolvido) não tiver sido feita e registrada;
* `docs/frontend/IMPLEMENTATION_PLAN.md` (checkboxes reais das Fases 0–6 + "Decisões locais" de E/F/H), `BACKEND_DISCOVERY.md`, `BACKEND_CAPABILITY_MATRIX.md` e `BACKEND_UI_MAPPING.md` não estiverem atualizados com o que foi de fato implementado.

Cada gap que sobrar dentro de E, F ou H precisa de uma nota de limitação real e específica, nunca uma pendência silenciosa.

## Autonomia obrigatória

Não faça perguntas ao usuário. Toda ambiguidade de contrato de payload, nome de campo ou decisão de design deve ser resolvida lendo o código real (domínio, componentes, testes existentes) e escolhendo a alternativa mais aderente ao que já existe — nunca inventando um endpoint, campo, evento ou comportamento que o domínio/frontend não sustente. Registre cada decisão local, com a razão, na seção "Decisões locais" da fase correspondente em `IMPLEMENTATION_PLAN.md`.

Preserve integralmente o worktree existente. Não use `git reset --hard`, `git checkout --`, remoções amplas ou qualquer operação que descarte trabalho. Não faça commit, push ou PR sem pedido explícito do usuário (a menos que o usuário já tenha pedido nesta própria mensagem, como fez na sessão anterior).

## Leitura obrigatória antes de alterar código

Nesta ordem:

1. `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md` completo, seções "Fase E", "Fase F", "Fase H" e "Fase Verificação + Documentação" — é o plano desta sessão.
2. `docs/frontend/IMPLEMENTATION_PLAN.md`, seções "Fase B", "Fase C" e "Fase D" (recém-fechadas) e suas "Decisões locais" — contrato e decisões que E/F/H devem respeitar (em especial: `result_ref`/`input_ref` continuam opacos, Fase C não mudou isso; nenhuma rota HTTP compõe `ToolRuntimeService`/`MultiAgentCoordinatorService` em produção ainda, então `ExecutionRoute` continua sem popular `events` a partir de um binding real).
3. `frontend/src/api/executions.ts` (`provideExecutionInput`) e `frontend/src/features/executions/ExecutionPage.tsx`/`ExecutionControls.tsx`/`ExecutionRoute.tsx` — onde o composer de input da Fase E entra; reusar o padrão de `Idempotency-Key` por intenção já estabelecido em `ExecutionControls`.
4. `frontend/tests/e2e/execution-controls.spec.ts` — o padrão de fake documentado no topo do arquivo que a Fase E deve seguir para o novo E2E.
5. `frontend/src/features/agents/AgentRail.tsx`, `AgentGlyph.tsx` e `frontend/src/styles/index.css` (classes `.agent-glyph__detail`, `.agent-rail__glyphs`, `.agent-rail__expand`) — o bug de sobreposição da Fase F, já diagnosticado como z-index/position quando o painel de detalhe expande.
6. `frontend/tests/e2e/reduced-motion.spec.ts` — contém hoje o workaround "fecha antes de expandir" que existe só por causa do bug da Fase F; remover após corrigir.
7. `frontend/tests/unit/agentGraphProjection.test.ts` — o teste "opens the lazy-loaded 3D scene from the 'Expandir grafo' affordance..." já identificado como instável (suspeita registrada: race entre fake timers/`act()` e o `lazy()` do `OrchestrationScene`, ver "Decisões locais" da Fase 5/6 em `IMPLEMENTATION_PLAN.md`).
8. `docs/frontend/BACKEND_DISCOVERY.md`, `BACKEND_CAPABILITY_MATRIX.md`, `BACKEND_UI_MAPPING.md` — já atualizados nesta última sessão para refletir B/C/D; qualquer hipótese que E/F/H contradiga deve ser corrigida também.

Faça uma leitura read-only completa do que listar acima antes de qualquer edição.

## Escopo obrigatório

### Fase E — UI de input para `WAITING_USER`

Ver `PROJECT_CLOSEOUT_ROADMAP.md`, seção "Fase E". Componente mínimo em `ExecutionPage`/`ExecutionRoute`, visível só quando `execution.state === 'WAITING_USER'`, reusando `ExecutionControls`/`Disclosure` e o padrão de `Idempotency-Key` por intenção. `input_ref` continua opaco (Fase C não resolveu nada aqui — confirmado, não reabrir). TDD: unit test do componente (`frontend/tests/unit/`) + E2E seguindo o padrão de fake já documentado em `execution-controls.spec.ts`. Rodar o novo E2E isolado 2–3× antes de considerar fechado.

### Fase F — Bug de sobreposição no `AgentRail`

Ver `PROJECT_CLOSEOUT_ROADMAP.md`, seção "Fase F". Independente de E/H. Investigar `.agent-glyph__detail`, `.agent-rail__glyphs`, `.agent-rail__expand` — provavelmente `z-index`/`position` quando o painel de detalhe expande sobre elementos abaixo. Menor diff possível; sem redesenhar a tela. Depois de corrigido: remover o clique extra "fecha antes de expandir" de `reduced-motion.spec.ts` e confirmar que passa sem o workaround.

### Fase H — Estabilizar teste flaky `agentGraphProjection`

Ver `PROJECT_CLOSEOUT_ROADMAP.md`, seção "Fase H". Independente de E/F. Rodar `agentGraphProjection.test.ts` isolado várias vezes com `--reporter=verbose`, depois na suíte completa, para confirmar a hipótese de race antes de mexer. Corrigir a causa raiz (provavelmente aguardar a resolução do lazy-import antes de avançar os timers, ou usar `findBy*` em vez de `getBy*` no ponto de transição). Não apenas aumentar timeout.

### Fase Verificação + Documentação (fecha o escopo completo do roadmap)

Só depois de E, F e H:

1. `python -m pytest -q` (com e sem `AGENTOS_TEST_POSTGRES_DSN` setado).
2. `python -m compileall -q src tests`, `git diff --check`.
3. `npm run test`, `npm run test:e2e` (specs novos/tocados 2–3× isolados), `npm run test:visual`, `npm run lint`, `npm run build` (em `frontend/`).
4. Segunda passagem read-only independente focada em: autorização/ownership dos adapters de B/C/D (`PostgresMultiAgentEventRecorder`, `PostgresToolActivitySink`, `PostgresProviderConfigurationAdapter`), ausência de fallback in-memory em produção (`create_production_app`'s guard continua passando), sanitização de payload na ponte de eventos (B.3 — nenhum campo sensível vazando no `ClientEvent` projetado), ausência de vazamento no result/input resolvido (C permanece sem `display_text`, nunca inventado).
5. Atualizar `IMPLEMENTATION_PLAN.md` (checkboxes reais das Fases 0–6 que E/F fecham, novas "Decisões locais" de E/F/H), `BACKEND_DISCOVERY.md`, `BACKEND_CAPABILITY_MATRIX.md`, `BACKEND_UI_MAPPING.md`.
6. Preencher o "Registro de encerramento" abaixo.

## Restrições

- Não invente endpoint, campo de payload, evento, componente ou DTO que o backend/domínio não sustente — sempre grounded em código real lido nesta sessão.
- Não quebre nenhum teste já verde: backend `701 passed, 2 skipped` com `AGENTOS_TEST_POSTGRES_DSN` setado (`663 passed, 40 skipped` sem ela); frontend conforme o estado atual de `npm run test`/`test:e2e`/`test:visual` antes de suas mudanças — rode-os primeiro para ter a baseline exata antes de tocar em código.
- Siga TDD sem exceção: teste primeiro, RED confirmado, implementação mínima, GREEN.
- Menor diff possível na Fase F — é uma correção de CSS/layout, não uma oportunidade de redesenhar `AgentRail`.

## Verificação obrigatória antes da conclusão

```
AGENTOS_TEST_POSTGRES_DSN=postgresql://agentos@localhost:5433/agentos python -m pytest -q
python -m pytest -q
python -m compileall -q src tests
git diff --check
```

```
cd frontend
npm run test
npm run test:e2e
npm run test:visual
npm run lint
npm run build
```

## Documentação obrigatória antes do fechamento

- `docs/frontend/IMPLEMENTATION_PLAN.md`: novas seções "Decisões locais registradas" para E, F e H, no mesmo padrão já usado nas Fases 0–D.
- `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md`: marcar E, F, H e Verificação como concluídos com evidência real (arquivo de teste, comando rodado), atualizar a tabela de estado atual no topo.
- `docs/frontend/BACKEND_DISCOVERY.md`, `BACKEND_CAPABILITY_MATRIX.md`, `BACKEND_UI_MAPPING.md`: remover qualquer hipótese que E/F/H contradigam.
- Este arquivo: acrescentar o "Registro de encerramento" abaixo ao concluir.

## Relatório final obrigatório

Ao concluir, informe: arquivos alterados; decisões de desenho e alternativas rejeitadas com a razão; evidência do componente de input de `WAITING_USER` funcionando (unit + E2E) (Fase E); evidência da correção do bug de sobreposição, incluindo a remoção do workaround em `reduced-motion.spec.ts` (Fase F); evidência da causa raiz do teste flaky corrigida, não apenas mitigada (Fase H); resultado de todos os comandos de verificação (backend com e sem Postgres, frontend completo); limitações legítimas remanescentes, apenas as que a investigação real comprovou.

## Registro de encerramento — a ser preenchido pelo agente executor

Ao fechar este escopo (E, F, H, Verificação), acrescente aqui a evidência real de implementação, testes, decisões e limitações legítimas. Não deixe este registro vazio, genérico ou baseado em intenção.
