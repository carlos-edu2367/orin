# Projetos do AgentOS — especificação de desenho

**Data:** 2026-08-11  
**Status:** aprovado para planejamento  
**Escopo:** projetos persistentes que agrupam chats independentes por workspace e memória compartilhados.

## Objetivo

Um Projeto representa uma unidade persistente de trabalho de um usuário. Ele possui exatamente um workspace compartilhado e um escopo isolado de memória; vários chats podem pertencer ao mesmo projeto, mantendo histórico, turns, executions, agentes e seleção de modelo independentes.

```text
User
├── Standalone chat
│   ├── histórico/executions próprios
│   └── workspace efetivo atual do chat
└── Project
    ├── shared workspace
    ├── project memory
    ├── Chat A (histórico e executions próprios)
    └── Chat B (histórico e executions próprios)
```

O invariante é: **um Project equivale a um workspace persistente compartilhado, um escopo de memória isolado e muitos chats independentes.**

## Estado atual e decisão de integração

O produto persiste conversas em `conversations`; a execução de um turno é criada em `conversation_turns` e o worker compõe um `TurnSession`. Hoje, `TurnSession` cria `ConversationWorkspace(workspace_root, conversation_id)`, portanto o workspace efetivo é o diretório derivado da conversa. A memória usada pelo runtime é `PostgresAgentMemoryStore`, hoje filtrada por usuário e carregada no prompt por `recent()`.

Não será criado um sistema paralelo de chats, arquivos ou memória. A implementação estenderá essas fronteiras:

- `projects` será a fonte de verdade de `workspace_id` para chats de projeto;
- `conversations.project_id` será opcional; `NULL` mantém o fluxo standalone;
- a consulta de turn incluirá metadata de projeto e o worker resolverá o workspace efetivo antes de criar `TurnSession`;
- a store atual de memória ganhará escopo explícito e continuará sendo a fonte usada pela ferramenta `remember` e pelo prompt;
- APIs e rotas seguirão o estilo atual de FastAPI/gateway e o frontend continuará usando React Router, cliente tipado e Motion.

## Modelo e persistência

### Project

`projects` terá:

- `project_id` opaco e imutável;
- `user_id` obrigatório;
- `workspace_id` obrigatório, opaco e imutável depois da criação;
- `name` limitado e não vazio;
- `description` opcional e limitada;
- `created_at`, `updated_at` e `archived_at`.

`conversations` ganhará `project_id` nullable e indexado junto do usuário e data de atualização. A associação é criada somente pelo serviço de projetos. Um chat de projeto não persistirá uma segunda cópia de `workspace_id`: o workspace efetivo é obtido por `Project.workspace_id`. Para standalone, o comportamento atual é preservado: o identificador de conversa continua determinando seu workspace efetivo.

A migração deixa todas as conversas existentes com `project_id = NULL`. Uma nova tabela/estrutura de projeto será criada dentro da mesma migration de domínio, com índices para listagem de projetos ativos e chats por projeto.

O serviço cria o registro de projeto e seu identificador de workspace dentro da mesma transação. O diretório local é provisionado de forma idempotente no primeiro uso sob esse ID; se a resolução/provisionamento não puder provar o vínculo, um chat de projeto falha fechado em vez de receber outro workspace.

## Serviço e segurança

Um `ProjectApplication`/store dedicado concentra ownership e lifecycle:

- criar, listar, obter, atualizar e arquivar/restaurar projetos;
- criar chats de projeto, garantindo que o projeto pertença ao principal;
- listar projetos já acompanhados dos respectivos chats, sem N+1;
- resolver a associação `conversation -> project -> workspace` para o worker;
- rejeitar IDs de projeto que não pertencem ao usuário e URLs que misturem chat/projeto.

Criar um chat dentro de projeto recebe `project_id`; o cliente nunca escolhe o workspace. Chats standalone continuam com a API e o fluxo atuais. Não haverá "mover chat para projeto" nem mover entre projetos na primeira versão, pois isso exigiria uma migração explícita de arquivos/artifacts e não pode fazer merge silencioso de workspaces.

Arquivar é preferido à exclusão real. Arquivar remove o projeto das consultas principais, preserva chats, workspace e memórias; restauração os torna acessíveis novamente. Excluir um chat nunca afeta o projeto nem seus recursos compartilhados.

## Workspace e execução

O worker receberá no turn `project_id` e `project_workspace_id` quando aplicável. `TurnSession` passará a aceitar um `workspace_id` efetivo, usando:

```text
project_workspace_id, quando há projeto
conversation_id, quando é standalone
```

`ConversationWorkspace` continuará sendo a única sandbox para ferramentas de arquivo e terminal. Main agent e subagentes reutilizam a mesma instância da sessão, logo ambos usam o workspace de projeto sem cópia manual de arquivos. Atividades/executions incluirão o `workspace_id` resolvido e `project_id` quando houver, para auditoria.

## Project Memory

`agent_memories` será evoluída para usar uma identidade de escopo reutilizável:

- `scope_type`: `user` ou `project`;
- `scope_id`: identificador de usuário ou de projeto;
- `project_id` nullable como chave/origem de integridade e para joins eficientes;
- `source_conversation_id`, `source_message_id` e `source_execution_id` nullable;
- campos existentes de fato, tags e timestamps.

O modelo mantém a memória global de usuário existente. Para um chat de projeto, a store carrega resultados relevantes de ambos os escopos: memória `user` do proprietário e memória `project` daquele projeto exato. Para chat standalone, somente memória `user` participa. Busca, listagem, edição e remoção sempre aplicam `user_id` e, quando for escopo de projeto, o `project_id`; conhecer um ID não concede acesso.

A recuperação continua bounded e ranqueada pela store existente (termos/tags, atualização e limite). A implementação não despeja todas as memórias no prompt. Falhas de leitura de memória são degradadas de modo seguro: a execução continua sem as memórias opcionais, registrando o problema; falha de resolução de workspace é fatal.

A descrição curta do projeto poderá ser incluída no system prompt com limite próprio, como metadata de contexto, e é distinta de memória e de futuras instruções de projeto.

## API

Seguindo os endpoints atuais de conversas, a superfície será:

- `POST /v1/projects`, `GET /v1/projects`, `GET/PATCH /v1/projects/{project_id}`;
- `POST /v1/projects/{project_id}/archive` e restauração quando exposta;
- `GET /v1/projects/sidebar`, resposta agrupada para a sidebar;
- `POST /v1/projects/{project_id}/conversations`, que cria o chat no projeto;
- `GET/POST/PATCH/DELETE /v1/projects/{project_id}/memories` para gestão manual.

As respostas de conversa passam a expor `project_id` e metadata mínima do projeto quando houver. A consulta padrão de conversas passa a retornar somente standalone chats; a consulta agrupada é a autoridade para a visualização de projetos. Cada operação usa o principal autenticado e valida ownership no backend.

## Interface e rotas

O frontend terá um único componente de navegação de conversas com duas visualizações locais:

- **Chats:** somente conversas standalone;
- **Projetos:** projetos ativos, cada qual colapsável e acompanhado de seus chats.

O estado de colapso e a última aba serão mantidos localmente. A lista não recarrega a página e usa motion curta, respeitando redução de movimento. O modo Projetos oferece criação em modal compacto e, em hover/menu, novo chat, renomear, configurações e arquivar.

Rotas explícitas impedem inferência por workspace:

```text
/projects/:projectId
/projects/:projectId/chats/:conversationId
/chats/:conversationId
```

A página de projeto é pequena: nome/descrição, ação de novo chat, chats recentes, acesso ao workspace compartilhado e gerenciamento de memória. O header de um chat de projeto mostra breadcrumb discreto. Não haverá dashboard de métricas nem uma segunda sidebar.

## Eventos e compatibilidade

Operações confirmadas emitem fatos compactos de projeto (`project.created`, `project.updated`, `project.archived`, `project.chat.created`) no mecanismo de eventos existente. Não carregam paths físicos, conteúdo de memória ou segredos.

Providers, streaming, seleção de modelo, skills, agents e subagentes mantêm seus contratos. A nova metadata é aditiva; conversas históricas sem projeto continuam listadas e executadas como antes.

## Testes e validação

Os testes começarão pelas fronteiras de domínio e cobrirão:

- criação transacional, update, archive/restore e ownership;
- criação de chat de projeto e compatibilidade de chat standalone;
- mesmo workspace em dois chats do Projeto A e isolamento de Projeto B/standalone;
- memória de Projeto A recuperável somente por chats de A, nunca B ou standalone;
- persistência após reinício/migração;
- contratos API, agrupamento sem N+1 e validação de rota chat/projeto;
- worker/subagente usando o workspace efetivo do projeto;
- toggle, colapso, modal de criação, criação de chat, rename e archive no frontend.

O fluxo end-to-end local cria A, escreve arquivo e memória no Chat A, confirma ambos no Chat B, confirma isolamento em B/standalone e verifica sobrevivência após reinício. A bateria Python e Vitest, migrações, typecheck/lint e verificação visual relevante serão executados antes de declarar conclusão.

## Limitações deliberadas da primeira versão

- Sem mover chats entre standalone/projetos ou entre projetos.
- Sem deleção física implícita de workspace, arquivos, artifacts ou memória ao arquivar projeto.
- Sem busca vetorial/embeddings nova; a recuperação usa o mecanismo bounded existente.
- "Project Instructions" permanece uma extensão futura, semanticamente separada de Project Memory.
