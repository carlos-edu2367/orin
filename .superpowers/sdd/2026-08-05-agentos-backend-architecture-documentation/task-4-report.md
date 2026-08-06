# Relatório da tarefa 4 — Memória e conhecimento compartilhado

## Status

Re-revisada após correções contratuais. A entrega da tarefa contém as três RFCs solicitadas e este relatório em Markdown. As alterações desta tarefa não implementam backend, endpoint, modelo ORM ou busca vetorial.

## Arquivos

- `docs/architecture/300-context-memory/301-memory.md`
- `docs/architecture/300-context-memory/302-blackboard.md`
- `docs/architecture/300-context-memory/303-context-sharing.md`
- `.superpowers/sdd/2026-08-05-agentos-backend-architecture-documentation/task-4-report.md`

## Verificações

- As três RFCs contêm objetivo, fora de escopo, responsabilidades e não responsabilidades, arquitetura, contratos tipados não executáveis, entidades, eventos no passado, fluxos normal/falha/cancelamento, segurança, observabilidade, extensibilidade, invariantes e futuro.
- Todos os links Markdown relativos das três RFCs foram resolvidos contra arquivos existentes: zero links inválidos.
- A RFC 301 separa explicitamente `MemoryManager` de `ContextManager`: Context é temporário e específico de Execution/turno; Memory é persistente e possui lifecycle próprio.
- A RFC 301 cobre Private, Workspace, User e Semantic Memory, incluindo ownership, proveniência, escrita/leitura, retenção, invalidamento, consolidação, busca, auditoria, proteção de dados e Events.
- A RFC 302 define decisões, descobertas, bugs, tarefas, contratos e arquitetura, com itens, versões imutáveis, conflitos, autoria, visibilidade, referências, auditoria e expiração.
- A RFC 302 proíbe que o Blackboard substitua fonte transacional de verdade ou conceda acesso a Private Memory.
- A RFC 303 exige compartilhamento referencial, autorização no uso, snapshots mínimos e handoffs estruturados; copiar centenas de mensagens, histórico bruto ou Context completo está explicitamente proibido.
- Os comandos revisados de retenção, leitura sensível, mutação, compartilhamento, revogação e expiração declaram `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e finalidade nos pontos aplicáveis.
- As três RFCs negam acesso cross-user, cross-workspace e cross-agent por padrão e exigem autorização explícita.
- Nenhuma RFC define modelo ORM, endpoint ou escolha concreta de implementação de busca vetorial; esses termos aparecem somente para delimitar o fora de escopo.

## Fix da revisão

- A verificação inicial comprovava presença de seções e termos, mas não a completude de todos os tipos referenciados pelas interfaces. A primeira correção também não verificou todas as definições de tipos de saída, patch, filtro e referência.
- A RFC 301 recebeu contratos tipados para `ApplyMemoryRetention` e `RetentionReceipt`; `InvalidateMemory` agora declara integralmente ownership, Agent, Execution e finalidade.
- A RFC 302 recebeu contratos tipados para `GetBlackboardItem`, `LinkBlackboardItems`, `DeclareBlackboardConflict` e `ExpireBlackboardItem`, além dos recibos relacionados. `PublishBlackboardItem`, `ReviseBlackboardItem` e `ResolveBlackboardConflict` passaram a declarar finalidade e escopo completos.
- `BlackboardItemVersion` agora captura diretamente proveniência, visibilidade, classificação e base de autorização de cada versão; esses dados não são herdados implicitamente do head mutável.
- A RFC 303 recebeu o contrato `ExpireContextShare`, recibos de revogação/expiração e campos explícitos de usuário, Workspace, Agents, Executions e finalidade nos comandos de referência, snapshot, handoff, resolução e revogação.
- O lifecycle de `ContextShareGrant` agora diferencia `SINGLE_USE` de `MULTI_USE_UNTIL_TERMINAL` e permite transições explícitas de `CONSUMED` para `REVOKED` ou `EXPIRED`, sem retorno a `ACTIVE`.
- A alegação anterior de que as lacunas de tipos estavam completamente corrigidas era incorreta: a checagem executada validava campos de comandos, não todos os tipos públicos usados nas assinaturas. A alegação foi retirada e substituída pelos resultados verificáveis abaixo.

## Fix da re-revisão

- A RFC 301 define localmente `AuthorizedMemory`, `MemoryConsolidationReceipt`, `MemoryMatch` e `MemoryFilter`; o filtro enumera escopos, tipos, estados, fontes, autoria, referências, intervalos, confiança e classificação filtráveis.
- A RFC 302 define localmente `BlackboardItemReference`, `BlackboardItemPatch`, `BlackboardRelationType` e `ItemExpectedVersion`; o patch enumera somente campos mutáveis e declara identidade, ownership e autoria original como imutáveis.
- A RFC 303 define localmente `ContextShareFilter`, `MinimalSnapshotRequest`, `SharedKindBudget`, `SharedContextExclusion`, `AuthorizedSourceReference`, `MinimalContextSnapshotRef`, `HandoffRef`, `DelegatedGrantRef` e os tipos estruturais usados pelo handoff.
- `ResolveSharedContext` agora usa `expected_resolution_count` e `idempotency_key`; sucesso incrementa `resolution_count` exatamente uma vez e a primeira resolução confirma `ACTIVE -> CONSUMED`.
- Verificação direcionada pós-fix: 20 tipos requeridos definidos, 9 grupos de campos mutáveis de patch presentes, semântica de resolução explícita e cercas de código balanceadas.

## Interpretações

- Private, Workspace e User Memory foram modeladas como escopos de ownership. Semantic Memory foi tratada como natureza/capacidade de recuperação que obrigatoriamente conserva um escopo base, evitando um índice sem tenancy.
- Persistência significa durabilidade além do Context, não retenção infinita; Memory continua sujeita a expiração, invalidamento, exclusão autorizada e tombstones mínimos.
- Blackboard é conhecimento compartilhado e versionado, mas seus itens apontam para a fonte canônica externa quando houver uma. Itens `TASK`, `BUG`, `CONTRACT` e `ARCHITECTURE` não governam os estados dos sistemas de origem.
- Snapshot mínimo é excepcional, curto e orçado. Ele não é Memory, não é fonte de verdade e não dispensa reautorização pelo destino.
- Revogação impede novas resoluções e invalida material temporário, mas não afirma apagar informação já processada; derivados persistentes são tratados por lineage e pelo domínio proprietário.

## Preocupações

- Compartilhamento futuro entre usuários, Workspaces ou organizações exigirá um contrato dedicado de Grants, residency e auditoria; não foi presumido nesta tarefa.
- Políticas concretas de consentimento, retenção legal, remoção física, classificação e criptografia dependerão das RFCs de segurança e persistência.
- Limites numéricos de mensagens, referências, unidades de resumo e duração de Grants deverão ser definidos por configuração/política versionada, sem enfraquecer a proibição de histórico bruto.
- Não foi possível usar `git status` porque o diretório de trabalho informado não é um repositório Git; a verificação de escopo foi feita diretamente sobre os caminhos criados.
