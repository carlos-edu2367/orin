# RFC 406 — Capabilities: closeout

**Data:** 2026-08-07  
**Estado:** fechado no escopo normativo implementável deste repositório, após revisão independente e verificação final

## Entrega

- `src/agentos/capabilities/models.py`: contextos completos, refs versionadas, descriptors, limites, programa/steps imutáveis, estados, outcomes, usage, retry/effect e checkpoint.
- `src/agentos/capabilities/registry.py`: registry determinístico versionado, imutável por versão, scoped, auditável por idempotência, allowlists e bootstrap único.
- `src/agentos/capabilities/scheduler.py`: validação topológica, ciclo/duplicidade e seleção determinística bounded por paralelismo declarado.
- `src/agentos/capabilities/ports.py`: Tool port, Child Execution port, autorização, state/checkpoint facade, bounded events/outbox e adapter em memória de referência.
- `src/agentos/capabilities/service.py`: `start`, `run`, `resume`, `request_cancel`, `inspect`, mapeamento canônico por `ExecutionControl`, Tool/child boundaries, limits, retry/UNKNOWN, checkpoint, compensação e cancelamento.
- `tests/unit/capabilities` e `tests/integration/capabilities`: 37 testes passando; boundary, lifecycle, registry, segurança, limits, retry, UNKNOWN, checkpoint, child, compensation, cancelamento e outbox.

## Decisões e alternativas rejeitadas

1. Programa é tupla tipada imutável de `CapabilityStep`; DSL, interpretador arbitrário e marketplace ficaram fora do RFC 406.
2. Execution continua unidade canônica; máquina paralela e transições diretas foram rejeitadas. Todas as mudanças usam `ExecutionControl`.
3. `CapabilityToolPort` é a única dependência de Tool. O repositório ainda não contém um pacote Tool Runtime RFC 401; criar uma implementação paralela teria violado a fronteira, então a porta estrutural foi entregue e testada.
4. Child recebe `ChildExecutionContext` mínimo, refs de input, causalidade e policy própria; herança de Context integral, grants ou segredos foi rejeitada.
5. State/checkpoint usa `CapabilityStatePort` e `InMemoryCapabilityState` como referência determinística. Banco, fila, ORM e adapter concreto de persistência não foram inventados; a composição com `TransactionalPersistence`/outbox RFC 601 permanece na aplicação que conecta a porta.
6. Retry de `UNKNOWN` é bloqueado; compensação é sequência declarada e falível, sem rollback global ou apagamento de fatos.

## Integrações comprovadas

- **RFC 101/102:** `ExecutionControl`, `CreateExecution`, `TransitionExecution`, `CommitExecutionChanges`, `Pause/ResumeExecution` e `CancelExecution` governam a Execution; `WAITING_CHILD` só pausa depois do checkpoint e retorna à fila antes de nova execução.
- **RFC 401:** cada Tool é chamada por `CapabilityToolPort` com versão exata, contexto, finalidade, bindings, idempotency key e limites. Capability não recebe Tool concreta, Registry de Tool ou adapter e não duplica Tool events.
- **RFC 103:** `CapabilityEvent` representa outbox bounded com correlação, causalidade implícita por run/step, versão, refs, outcome, uso e razão sanitizada; publicação é posterior ao fato confirmado pela fachada state/outbox.
- **RFC 601:** `CapabilityStatePort` é a fachada limitada de persistência; o adapter de referência só guarda IDs, refs, versões, states, steps, effect state, usage, timestamps e eventos bounded. Não há SQL/ORM no pacote.
- **RFC 402/403/602/405:** nenhuma importação direta ou acesso efetivo; Resource, Filesystem, Artifact e Browser permanecem grants/refs encaminhados ao Tool Runtime/ports existentes.

## Evidência executada

```text
python -m pytest -q
637 passed, 6 skipped in 6.00s

python -m pytest -q tests/unit/capabilities tests/integration/capabilities tests/integration/persistence/test_capability_postgres_optional.py
37 passed, 1 skipped

python -m compileall -q src tests
exit code 0

git diff --check
exit code 0
```

O skip PostgreSQL foi real: `AGENTOS_TEST_POSTGRES_DSN` não está configurado. O teste opcional foi executado diretamente e não simula sucesso. Os demais skips da regressão são condicionais já existentes no repositório.

Scans executados:

```text
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|requests|httpx|kafka|rabbit|broker|scheduler|subprocess|adapter|database|orm|runtime|tool|capability|execution|checkpoint|child|compensation|secret|handle|payload|input|output|permission|authorization" src/agentos/capabilities
rg -n "ToolRuntime|ToolRegistry|Playwright|Browser|Filesystem|ResourceManager|ArtifactStorage|TransactionalPersistence|EventBus|Runtime|ExecutionControl" src/agentos/capabilities
rg -n "\bpass\b|TODO|TBD" src/agentos/capabilities
```

Os dois primeiros scans retornam somente nomes de contratos e ports previstos no RFC 406, além de `ExecutionControl`; não há importação de adapter concreto, banco, fila, Provider, Browser, Filesystem, Resource, Artifact ou Tool concreta. O terceiro scan não retorna ocorrências.

## Revisão independente

Foi feita uma segunda passagem read-only independente sobre `3854a9d..5c9454b`, cobrindo registry/versioning, ownership/context, autorização, Tool/Execution boundaries, child inheritance, scheduler, limits, checkpoint, retry/UNKNOWN, compensation, cancellation, state/outbox, events e bypass. Findings tratados com ciclos RED/GREEN: contexto de child reduzido, checkpoint persistido/escopado, espera de Tool/Child mapeada, descriptor desabilitado revalidado, timeout efetivo, falha de Tool contabilizada, limits pós-efeito, compensation outcome e campos de ownership/version no evento. A verificação completa foi executada novamente após esses ajustes.

## Commits do gate

- `3854a9d` — `docs: define RFC 406 capabilities design`
- `5c9454b` — `feat: implement RFC 406 capabilities boundary`
- `6d14a21` — `docs: close RFC 406 capabilities gate` (inclui ajustes finais, matriz, closeout e prompt)

## Limitações legítimas

O RFC 401 não possui implementação de Tool Runtime no estado-base; por isso o pacote entrega a porta technology-neutral e não fabrica um runtime ou adapter de teste em produção. O RFC 601 possui portas/adapters próprios fora de Capabilities; o pacote não replica schema, ORM, outbox publisher ou persistência concreta. Essas limitações são precisamente as fronteiras de escopo do RFC 406 e não deixam implementação obrigatória de Capability para agente futuro.

## Próximo gate

A sequência documental disponível após RFC 406 não define um RFC seguinte. Os RFCs 403/402/404/405 estão documentados como concluídos; não será inventado um próximo RFC. A ausência de próximo gate é uma decisão documental, não backlog de implementação.

## Estado final

Com a matriz e os comandos acima, o Gate RFC 406 está 100% completo, funcional, integrado às portas normativas disponíveis, testado, documentado e sem pendências futuras de implementação dentro do escopo do RFC.
