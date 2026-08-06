# Domínio Agent do AgentOS — Especificação de Design

**Data:** 2026-08-06  
**Base normativa:** RFC 050, RFC 060, RFC 101, RFC 102, RFC 103, RFC 104, RFC 201, RFC 202, RFC 203, RFC 501, RFC 502 e RFC 601  
**Estado:** Aprovada para implementação inline

## Objetivo

Implementar o domínio `Agent` como identidade persistente, versionada,
autorizável e auditável, sem confundi-lo com `Execution`, Runtime, Context,
Provider, conversa ou Worker. Cada nova Execution deve carregar um snapshot
imutável da configuração autorizada do Agent.

## Decisões de design

### Fronteira do pacote

O pacote canônico será `src/agentos/agents/`, dividido por responsabilidade:

- `models.py` concentra entidades imutáveis, referências opacas, estados,
  políticas e snapshots públicos;
- `ports.py` concentra Protocols, comandos, consultas, resultados e a porta
  transacional estreita do domínio;
- `security.py` concentra validação de ownership, finalidade, grants,
  classificação, referências e redaction de erros;
- `in_memory.py` fornece somente adapters substituíveis para domínio e testes;
- `compat.py` adapta o snapshot do Agent para Execution, Context, Providers e
  Events sem expor adapters concretos;
- `__init__.py` expõe somente contratos públicos estáveis.

O domínio não importará FastAPI, HTTP, SDK de Provider, ORM, banco, Redis,
filesystem, broker ou Artifact Storage.

### Identidade e configuração

`Agent` será uma dataclass congelada e versionada com `agent_id`, `user_id`,
`workspace_id` opcional quando o escopo for estritamente do usuário, owner,
`display_name`, estado administrativo, configuração vigente, referência do
escopo de Memory privada, timestamps e referências de auditoria.

`AgentConfiguration` será imutável. Cada reconfiguração cria uma versão
positiva, declara `supersedes_version` e preserva as versões anteriores.
Prompts, perfis de modelo, Tools, Capabilities, Skills, Memory e Policies serão
somente referências opacas, nunca conteúdo livre. Apresentação ficará separada
da autorização.

Os estados administrativos serão `ACTIVE`, `SUSPENDED` e `ARCHIVED`. A máquina
permitirá apenas criação em `ACTIVE`, `ACTIVE -> SUSPENDED`,
`SUSPENDED -> ACTIVE` e `ACTIVE|SUSPENDED -> ARCHIVED`. `ARCHIVED` é somente
leitura e não possui reativação nesta versão.

### Execução administrativa e persistência

Toda mutação administrativa será solicitada por uma porta injetada de
`AdministrativeExecutionRequester`; o domínio não escreverá diretamente estado
de `Execution`. A confirmação da intenção administrativa será coordenada por
uma porta transacional estreita, com estados `COMMITTED`, `NOT_COMMITTED` e
`UNKNOWN`, compatível conceitualmente com a RFC 601. O adapter em memória será a
única implementação desta sessão.

Antes da confirmação, cancelamento ou falha não cria nem altera Agent. Depois
da confirmação, cancelamento tardio não desfaz o fato; qualquer reversão exige
novo comando válido. A mesma chave de idempotência com o mesmo fingerprint
retorna a mesma referência/resultado. Fingerprint diferente retorna conflito
sanitizado. Conflito de versão exige nova intenção e nunca sobrescreve
configuração.

### Registry e resolução

`AgentRegistry.get`, `resolve_for_execution` e `list` serão Protocols síncronos.
`AgentResolutionRequest` carregará `agent_id`, `user_id`, `workspace_id`, versão
opcional, `purpose` e `correlation_id`.

A resolução validará existência sem revelar cross-owner/cross-Workspace,
estado `ACTIVE`, ownership, Workspace assignment, grants, classificação,
finalidade e políticas atuais. A versão vigente ou uma versão explicitamente
autorizada será congelada em `ResolvedAgent`, com referências públicas/opacas e
políticas resolvidas. O resultado será independente de reconfigurações futuras.

Paginação usará cursor opaco vinculado ao escopo do usuário/Workspace e não
retornará Agents de outro owner ou Workspace.

### Events e outbox

As mutações confirmadas criarão envelopes mínimos do contrato canônico de
`agentos.events`: `AgentCreated`, `AgentConfigurationChanged`,
`AgentSuspended`, `AgentResumed`, `AgentArchived`, `AgentWorkspaceAssigned` e
`AgentWorkspaceUnassigned`.

Cada envelope carregará ownership, `agent_id`, versão, `execution_id`,
`correlation_id`, causa, classificação e sequence quando vinculado à Execution.
Payload conterá apenas códigos, versões e referências opacas. Prompt, Memory,
credenciais, Provider proprietário, conteúdo de Tool/Skill, cookies, tokens e
exceções concretas serão rejeitados. O Agent nunca publicará diretamente no
EventBus; a confirmação apenas adicionará o Event à unidade transacional/outbox
conceitual para publicação posterior pelo `OutboxPublisher`.

### Compatibilidade

`Execution` receberá somente um campo opcional e retrocompatível,
`agent_config_version`, preservando construtores e snapshots existentes. A
compatibilidade de Context será por referência/snapshot mínimo e não alterará a
semântica de Context temporário. Providers receberão somente referências
autorizadas, por meio das portas já existentes. Runtime continuará dependendo
de Protocols e não importará o registry ou adapter concreto de Agent.

## Fluxos de erro e cancelamento

- Agent inexistente, owner divergente, Workspace incompatível, assignment
  ausente, grant revogado, classificação incompatível ou finalidade inválida
  falham fechado e não revelam existência cross-scope.
- `SUSPENDED` e `ARCHIVED` são rejeitados para novas Executions.
- Falha, `NOT_COMMITTED` ou cancelamento antes da confirmação não deixam Agent
  parcial.
- `UNKNOWN` exige inspeção autorizada antes de repetir ou afirmar o resultado.
- Evento confirmado não é desfeito por falha de entrega posterior.
- Reconfiguração com versão esperada divergente retorna conflito, sem overwrite.
- Nenhum erro público inclui payload de prompt, Memory, credencial, Provider,
  Tool, Skill ou exceção tecnológica.

## Estratégia de testes

A implementação seguirá TDD estrito: cada comportamento terá teste escrito,
execução RED observada, implementação mínima GREEN e refatoração somente com
verde. A suíte cobrirá contratos e timestamps; configuração e versões; estados
e transições; idempotência e conflitos; confirmações/cancelamentos; resolução
autorizada e snapshots imutáveis; paginação; eventos e outbox; ausência de
segredos; compatibilidade com Execution/Context; independência do Runtime; e
regressão das suítes existentes.

## Fora de escopo

Não serão implementados Orchestrator, Multi-agent, Tool, Capability, Skill,
Memory, Artifact, Workspace, Provider concreto, loop de LLM, chat, endpoints,
workers, FastAPI, PostgreSQL, Redis, SQLAlchemy, migrations, broker,
filesystem, exclusão física, anonimização, retenção legal ou restauração de
`ARCHIVED`.
