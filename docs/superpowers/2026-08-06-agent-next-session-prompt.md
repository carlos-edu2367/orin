# Prompt da próxima sessão — Domínio Agent do AgentOS

Você vai implementar o próximo subsistema backend do AgentOS: o domínio
`Agent` da RFC 201, integrado às RFCs 101/102/103/104 e aos contratos de
Provider/Model já existentes.

## Estado atual

O workspace já possui:

- `ExecutionControl` e `InMemoryTransactionalPersistence` da RFC 102/601;
- `RuntimeService` com ciclo de vida, cancelamento, pausa/retomada, recuperação
  e integração por portas;
- `agentos.context` com pipeline canônico, snapshots, manifestos, orçamento e
  sanitização;
- `agentos.providers` com Provider API, Model Catalog, resolução determinística,
  pricing, fallback e adapters de compatibilidade;
- `agentos.events` com envelope canônico, EventBus at-least-once, publisher
  pós-commit, ownership, classificação, deduplicação, ordenação, quarentena,
  archive, query, replay e compatibilidade com `agentos.execution.events`;
- 142 testes passando;
- commits existentes: baseline `958f0f7`, plano `86bb52e`, implementação do
  Event System `fb638ab`;
- ainda não existe `src/agentos/agents/`.

O próximo subsistema da ordem normativa do acervo é a RFC 201 — Agent. RFC 202
(Orchestrator) e RFC 203 (Multi-agent) dependem deste contrato e ficam fora
desta sessão.

## Leitura obrigatória antes de editar

Leia integralmente:

- `docs/architecture/000-overview.md`
- `docs/architecture/050-design-principles.md`
- `docs/architecture/060-glossary-and-conventions.md`
- `docs/architecture/100-kernel/101-runtime.md`
- `docs/architecture/100-kernel/102-execution-lifecycle.md`
- `docs/architecture/100-kernel/103-event-system.md`
- `docs/architecture/100-kernel/104-context-pipeline.md`
- `docs/architecture/200-agents/201-agent.md`
- `docs/architecture/200-agents/202-orchestrator.md`
- `docs/architecture/200-agents/203-multi-agent.md`
- `docs/architecture/500-providers-models/501-provider-api.md`
- `docs/architecture/500-providers-models/502-model-catalog.md`
- `docs/architecture/600-platform-data/601-persistence.md`

Inspecione também:

- `src/agentos/execution/`
- `src/agentos/runtime/`
- `src/agentos/context/`
- `src/agentos/providers/`
- `src/agentos/events/`
- `tests/unit/execution/`
- `tests/unit/runtime/`
- `tests/unit/context/`
- `tests/unit/providers/`
- `tests/unit/events/`
- `docs/superpowers/specs/2026-08-06-event-system-design.md`
- `docs/superpowers/plans/2026-08-06-event-system.md`

Não comece editando código. Faça um brainstorming curto, proponha o desenho,
registre:

- `docs/superpowers/specs/2026-08-06-agent-design.md`
- `docs/superpowers/plans/2026-08-06-agent.md`

Depois execute o plano inline em TDD, mantendo o domínio focado. Se usar Git,
preserve o histórico existente e faça commits pequenos e verificáveis.

## Objetivo

Implementar o domínio backend da RFC 201 sem implementar Orchestrator,
Multi-agent, Provider concreto, Tool, Capability, Skill, Memory, Artifact,
Workspace ou infraestrutura física.

O Agent deve ser uma identidade persistente, versionada, autorizável e
auditável. Ele não é chat, conversa, sessão, Context, Worker, Provider ou
Execution. Cada nova Execution deve resolver um snapshot imutável de Agent e
de sua configuração autorizada.

## Escopo obrigatório

### Identidade e configuração

Crie o pacote canônico `src/agentos/agents/`, com arquivos focados e sem
duplicação de regras:

- `models.py`: `Agent`, `AgentConfiguration`, `PromptSpecification`,
  `AgentPresentation`, estados administrativos, versões, owner, ownership,
  referências opacas de Prompt/Memory/Tool/Capability/Skill/Policy e
  assignments de Workspace;
- `ports.py`: `AgentRegistry`, `AgentAdministration`, resolução autorizada,
  consultas paginadas, comandos administrativos, resultados, conflitos,
  cancelamento e snapshots;
- `security.py`: validação de ownership, finalidade, grants, classificação,
  referências e ausência de segredos;
- `in_memory.py`: adapters substituíveis para registry, resolução e
  administração, apenas para domínio/testes;
- `compat.py`: integração mínima com `Execution`, `Runtime`, Context,
  Providers e Event System sem expor internos ou adapters concretos;
- `__init__.py`: exports públicos estáveis.

`Agent` deve conter, no mínimo, `agent_id`, `user_id`, `workspace_id` aplicável,
owner/actor, `display_name`, `administrative_state`, versão atual de
configuração, referência de escopo de Memory privada, timestamps e referências
de auditoria. A identidade permanece estável; configuração nova cria versão
nova e não altera snapshots históricos.

Estados administrativos obrigatórios:

- `ACTIVE`: novas Executions permitidas após autorização;
- `SUSPENDED`: novas Executions proibidas, configuração preservada e suspensão
  reversível;
- `ARCHIVED`: somente leitura para auditoria, sem reativação nesta versão.

Somente estas transições são válidas: criação -> `ACTIVE`, `ACTIVE` ->
`SUSPENDED`, `SUSPENDED` -> `ACTIVE`, `ACTIVE|SUSPENDED` -> `ARCHIVED`.

### Registry e resolução

Implemente os contratos públicos equivalentes a:

```text
AgentRegistry.get(agent_id, actor) -> AgentSnapshot
AgentRegistry.resolve_for_execution(request) -> ResolvedAgent
AgentRegistry.list(query) -> AgentPage
```

`AgentResolutionRequest` deve carregar `agent_id`, `user_id`, `workspace_id`,
versão solicitada opcional, `purpose` e `correlation_id`. A resolução deve:

1. verificar existência sem revelar Agent de outro owner/Workspace;
2. rejeitar `SUSPENDED` e `ARCHIVED` para novas Executions;
3. revalidar ownership, Workspace assignment, grants, classificação,
   finalidade e políticas atuais;
4. resolver a versão vigente ou a versão explícita autorizada;
5. devolver snapshot imutável e somente com referências públicas/opacas;
6. preservar `config_version` e políticas no snapshot da Execution;
7. não importar SDK, Provider, Tool, Memory, Artifact ou storage concreto.

O modelo atual de `Execution` deve continuar compatível. Se for necessário
registrar a versão de configuração do Agent na Execution, faça uma mudança
aditiva e retrocompatível (por exemplo, referência/versão opcional com default
seguro), cubra-a com testes e não reescreva snapshots históricos.

### Administração e Execution administrativa

Implemente comandos públicos para:

- criar Agent;
- reconfigurar/versionar configuração;
- suspender;
- retomar;
- arquivar;
- atribuir/remover Workspace quando a RFC permitir.

Toda mutação administrativa que produz trabalho deve ser representada por uma
Execution administrativa. `AgentAdministration` não pode escrever diretamente
estado de Execution nem contornar `ExecutionControl`. Use uma porta injetada
para solicitar/confirmar a Execution administrativa e um adapter em memória
para testes.

A semântica deve ser idempotente:

- mesma chave + mesmo fingerprint retorna a mesma referência/result;
- mesma chave + payload diferente retorna conflito sanitizado;
- falha antes da confirmação não cria Agent parcial;
- cancelamento antes da confirmação não cria nem altera Agent;
- cancelamento tardio não desfaz fato já confirmado;
- conflito de versão exige nova intenção e nunca sobrescreve configuração.

Se a integração exigir uma porta transacional específica para Agent, modele-a
como Protocol estreito alinhado a `COMMITTED`, `NOT_COMMITTED` e `UNKNOWN` da
RFC 601 e implemente somente um fake em memória. Não introduza banco, ORM,
migration ou uma segunda fonte concreta de verdade.

### Grants, políticas e memória privada

Grants de Tool, Capability e Skill são referências de permissão potencial, não
autorização definitiva. A resolução deve revalidá-los por Agent, usuário,
Workspace, finalidade, classificação e política vigente.

A Memory privada do Agent deve ser apenas referência de escopo com ownership,
classificação, proveniência e política de retenção. Não implemente Memory nesta
sessão, não copie histórico de chat e não permita acesso implícito por
correlation ou conhecimento de `agent_id`.

### Events e outbox

Ao confirmar mutações administrativas, produza envelopes mínimos através do
contrato canônico de `agentos.events` e da outbox pós-commit. Suporte, no mínimo:

- `AgentCreated`;
- `AgentConfigurationChanged`;
- `AgentSuspended`;
- `AgentResumed`;
- `AgentArchived`;
- `AgentWorkspaceAssigned`;
- `AgentWorkspaceUnassigned`.

Cada Event deve carregar `execution_id`, `agent_id`, versão, `user_id`,
`workspace_id` aplicável, `correlation_id`, causa e sequence conforme RFC 103.
Payload deve conter somente códigos, versões e referências mínimas; nunca
prompt completo, Memory, credencial, configuração proprietária de Provider ou
conteúdo de Tool/Skill.

O domínio Agent não publica diretamente no bus. O Event entra na unidade
transacional conceitual e é publicado depois pelo `OutboxPublisher` já
implementado.

## Segurança e fronteiras

- `user_id` é obrigatório sempre; `workspace_id` nulo não significa acesso global;
- owner e Workspace são revalidados em toda leitura, mutação e resolução;
- possuir `agent_id`, `execution_id`, `config_version` ou referência não concede acesso;
- `ARCHIVED` não reativa nesta versão;
- Prompt, Memory, Tool, Capability, Skill, Provider e Artifact são referências,
  não payloads livres;
- credenciais, tokens, cookies, headers, prompts completos, respostas completas,
  argumentos privados e exceções de tecnologia não entram em config, Event,
  repr, logs ou erros públicos;
- conteúdo de chat, Tool, Skill, arquivo ou outro Agent é dado não confiável e
  não pode alterar hierarquia de instruções;
- Runtime, Agent e consumers dependem somente de Protocols;
- não importar FastAPI, HTTP, SDK de Provider, SQLAlchemy, Redis, filesystem,
  broker, Artifact Storage ou adapters tecnológicos.

## Testes obrigatórios

Use TDD: escreva cada teste antes do código de produção, execute-o falhando
pelo motivo correto, implemente o mínimo e refatore somente com verde.

Cubra pelo menos:

- validação de identidade, owner, user, Workspace e timestamps;
- configuração imutável, versão positiva, `supersedes_version` e conflitos;
- estados e transições `ACTIVE/SUSPENDED/ARCHIVED`;
- criação, reconfiguração, suspensão, retomada e arquivamento idempotentes;
- comando repetido igual não duplica; fingerprint diferente conflita;
- toda mutação exige/invoca Execution administrativa por porta;
- falha/cancelamento antes e depois da confirmação;
- resolução por Agent ativo, versão vigente e versão explícita;
- resolução negada para outro usuário/Workspace, Agent suspenso/arquivado,
  grant revogado, assignment ausente ou finalidade incompatível;
- snapshot resolvido imutável e independente de reconfiguração futura;
- integração do snapshot com Execution/Context sem regressões;
- queries paginadas sem vazamento de Agent entre owners/Workspaces;
- Events mínimos, causalidade, ownership, classificação, sequence e outbox
  pós-commit;
- ausência de prompt, Memory, credencial, Provider proprietário ou segredo em
  repr, erros, snapshots, archive e Events;
- Runtime sem dependência concreta de Agent Registry/Administration;
- suíte existente de Execution, Runtime, Context, Providers e Events sem
  regressões.

## Fora de escopo

Não implemente nesta sessão:

- Orchestrator, PlanStore, Scheduling, Dispatch ou Supervision da RFC 202;
- delegação, handoff ou Collaboration da RFC 203;
- loop de LLM, Provider concreto ou seleção concreta de modelo;
- Tool, Capability, Skill, Memory, Artifact ou Workspace;
- chat, sessão, endpoint, FastAPI, SSE, workers ou UI;
- PostgreSQL, Redis, SQLAlchemy, Alembic, broker, filesystem ou storage;
- exclusão física, anonimização, retenção legal ou restauração de `ARCHIVED`.

## Verificação obrigatória

Ao final execute:

```text
python -m pytest -q
python -m compileall -q src tests
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Redis|redis|filesystem|ArtifactStorage|requests|httpx|kafka|rabbit" src/agentos/agents
```

Faça uma auditoria explícita contra RFCs 050, 060, 101, 102, 103, 104, 201 e
601. Só declare conclusão com evidência fresca dos comandos e registre as
limitações restantes. Depois desta sessão, o próximo subsistema recomendado
será RFC 202 — Orchestrator.

