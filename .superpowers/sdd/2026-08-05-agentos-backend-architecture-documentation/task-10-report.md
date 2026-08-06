# Task 10 — Relatório

## Status

Concluído e verificado no escopo documental solicitado. As três RFCs de extensibilidade foram criadas e a RFC 802 foi alinhada ao contrato canônico de agendamento de Skills, exclusivamente em Markdown. Nenhum backend, endpoint, schema, migração, adapter, configuração executável ou código de produção foi implementado.

## Arquivos

- `docs/architecture/900-extensibility/901-plugin-sdk.md`
- `docs/architecture/900-extensibility/902-skills.md`
- `docs/architecture/900-extensibility/903-mcp-future.md`
- `docs/architecture/800-operations/802-scheduler.md` — integração contratual de Skill agendada

## Resumo

- A RFC 901 define manifesto imutável, descoberta sem execução, registro, resolução, versionamento, permissões por interseção, isolamento, compatibilidade, lifecycle, drain, quarentena, desativação e observabilidade de plugins.
- A RFC 902 define Skills como workflows declarativos versionados que criam uma `Execution` raiz e Executions filhas para subtrabalho independente, com contexto mínimo, grants explícitos, Artifacts por referência, checkpoints e integração exclusiva com o Scheduler.
- A RFC 903 mantém MCP fora do lançamento inicial e o posiciona como protocolo de borda opcional atrás de adapters, com portas tipadas, mapeamento para contratos atuais, limites de segurança, cancelamento cooperativo e dez critérios obrigatórios de adoção.
- As três RFCs cobrem contexto sensível com `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`, além de eventos, fluxo normal, falhas, cancelamento, segurança, observabilidade, invariantes, extensibilidade e futuro.

## Correções da revisão

- P1 — Agendamento de Skill: `SkillScheduleBinding` passou a ser o tipo canônico persistido dentro de `ScheduledSkillTarget`, consumido por `CreateSchedule.target` e `UpdateSchedule.replacement_target`. O binding inclui snapshot do template de Task, snapshots de inputs, limites de Execution/Skill e snapshots das policies de Context, Artifact e autorização.
- P1 — Resolução por ocorrência: `ScheduledSkillSelector` discrimina `PINNED` de `RESOLVE_AT_FIRE`. O segundo persiste `skill_id`, `SkillVersionConstraint` e `SkillResolutionPolicySnapshotRef`; cada ocorrência grava `MaterializedScheduledSkillTarget` com `SkillRef` exata, digests, Task, inputs, limites, policies e evidência antes de criar a Execution.
- P1 — Resolução fail-closed: `SkillRegistry.resolve` retorna `SkillResolutionOutcome`, distinguindo `SkillResolved`, deny, disabled, incompatible, not found e indeterminate. Somente `SkillResolved` permite iniciar ou materializar; `StartScheduledSkill` consome a Execution e o target já materializados sem duplicá-los.
- P1 — Lifecycle MCP: `McpBinding` agora possui versão, owner, Workspace, purposes, estado/version, evidence, policy de validação e prazo de revalidação. `McpBindingRegistry` administra registro, validação, ativação, suspensão, desativação, quarentena, resolução e inspeção com requests, receipts, Events e transições auditáveis.
- P2 — Resource MCP: a operação genérica `read_resource` e os tipos soltos associados foram removidos. Resource remoto só pode implementar uma porta especializada existente após `ResourceManager.acquire/authorize` produzir `AuthorizedResourceHandle`; sem contrato local compatível, o binding permanece indisponível.
- Correção residual — Policy canônica de Schedule: `Schedule.configuration_snapshot_policy` passou a usar `ConfigurationSnapshotPolicy` como fonte persistida única. `CreateSchedule.configuration_snapshot_policy` aceita valor explícito ou `DERIVE_FROM_TARGET`; o serviço deriva pelo selector, persiste apenas `PINNED`/`RESOLVE_AT_FIRE` e rejeita contradição com `ScheduledSkillSelector.resolution_policy`. `UpdateSchedule` altera target e policy atomicamente e não conserva combinação incompatível.

## Verificação

- RFCs 802, 902 e 903 verificadas após as correções: 628, 490 e 555 linhas, respectivamente, com fences balanceados e nenhum marcador provisório literal.
- 21 de 21 contratos direcionados encontrados: binding persistível, snapshots, selector constraint/policy, materialização por ocorrência, outcomes de Skill, start agendado, lifecycle/evidence de MCP e alinhamento ao Resource Manager.
- Contratos obsoletos verificados como ausentes: `ScheduledSkillTarget.immutable_target_ref: SkillRef`, `read_resource(request: ReadMcpResource)`, `ResourceReadOutcome` e `ReadMcpResource`.
- 39 links relativos verificados nas três RFCs alteradas; nenhum destino ausente.
- Correção residual verificada: 12 de 12 invariantes presentes, policy canônica confirmada na entidade/CreateSchedule, update atômico e 26 links relativos válidos nas RFCs 802/902.
- Resultado da verificação automatizada final: 0 falhas.
- Nenhum arquivo de backend foi criado ou alterado por esta tarefa.
