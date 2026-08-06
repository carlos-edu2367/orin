# Prompt da próxima sessão — Runtime do AgentOS

Você vai implementar o próximo subsistema do backend do AgentOS: o núcleo de execução do `Runtime`, conforme a RFC 101.

Leia integralmente antes de editar:

- `C:\Users\reali\Documents\AgentOS\docs\architecture\000-overview.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\050-design-principles.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\060-glossary-and-conventions.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\101-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\102-execution-lifecycle.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\103-event-system.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\104-context-pipeline.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\501-provider-api.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\502-model-catalog.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\601-persistence.md`

Inspecione também o pacote já implementado em:

- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\execution\`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\2026-08-06-runtime-next-session-prompt.md`

## Objetivo

Implementar exclusivamente o domínio backend do `Runtime`: seu contrato público, loop normativo de uma `Execution`, coordenação por portas públicas, limites, cancelamento cooperativo, pausa, falha, resultado e checkpoints por referência.

O `Runtime` deve governar uma única `Execution` por vez, sem conhecer tecnologias concretas. Ele deve consumir a `ExecutionControl` já existente para toda mutação de estado, uso, resultado, falha, cancelamento e checkpoint associado.

## Escopo obrigatório

- Definir e implementar a porta `Runtime` com `execute(request: RuntimeRequest) -> RuntimeOutcome`.
- Definir tipos para `RuntimeRequest`, resultados `CompletedOutcome`, `WaitingOutcome`, `FailedOutcome` e `CancelledOutcome`.
- Definir erros categóricos do Runtime, retryability, limites, uso e decisão de continuação.
- Definir portas Protocol mínimas para:
  - `ContextManager`;
  - `ModelResolver`;
  - `ProviderPort`;
  - `ToolCapabilityPort`;
  - `CheckpointPort` somente para leitura/recuperação;
  - relógio e política de budget.
- Implementar o loop normativo da RFC 101:
  1. carregar e validar ownership da Execution;
  2. adquirir `QUEUED -> STARTING` e iniciar `STARTING -> RUNNING`;
  3. montar Context;
  4. resolver modelo por atributos públicos;
  5. invocar Provider abstrato;
  6. tratar resposta final ou pedido de Tool/Capability;
  7. transicionar para `WAITING_TOOL`, reconciliar o resultado e retornar a `RUNNING`;
  8. criar checkpoint somente em limite seguro;
  9. respeitar `WAITING_USER`, `PAUSED`, cancelamento e limites;
  10. finalizar em `COMPLETED`, `FAILED` ou `CANCELLED`.
- Verificar antes e depois de cada efeito externo:
  - cancelamento;
  - pausa;
  - timeout;
  - limite de iterações;
  - limite de custo;
  - limite de tokens quando houver medição pública.
- Preservar `user_id`, `workspace_id` quando aplicável, `agent_id`, `execution_id`, `correlation_id` e `purpose` em todas as solicitações sensíveis às portas.
- Garantir que retries e recuperação só repitam efeitos quando houver idempotência ou reconciliação explícita.
- Usar apenas referências para resultado, Context, checkpoint e ações; não carregar payloads sensíveis no Runtime.
- Criar fakes/stubs somente dentro dos testes para Context Manager, Model Resolver, Provider, Tool/Capability, Checkpoint e Clock.

## Restrições inegociáveis

- Backend Python 3.13+ somente.
- Não criar FastAPI, endpoints, SSE, workers, ARQ, Redis, PostgreSQL, ORM ou Alembic.
- Não importar FastAPI, Playwright, Redis, SQLAlchemy, SDKs de IA ou SDKs de Provider.
- Não implementar Context Manager real, Provider, Model Catalog, Tool, Capability, Browser, Resource, Memory, Event Bus real ou persistência concreta.
- Não chamar `TransactionalPersistence` diretamente; o Runtime deve usar `ExecutionControl`.
- Não publicar eventos diretamente; a confirmação deve atravessar `ExecutionControl` e a outbox existente.
- Não persistir Context como Memory.
- Não usar `switch/case` por fornecedor, tecnologia ou adapter.
- Não abrir uma segunda Execution nem executar duas iterações concorrentes para a mesma aquisição.
- Não reabrir estado terminal.
- Não converter timeout ou falha em sucesso.
- Não registrar segredos, prompts completos, argumentos privados, tokens, credenciais ou payloads proprietários.

## Processo obrigatório

1. Inspecione a estrutura atual e leia integralmente as RFCs listadas.
2. Apresente um desenho curto, incluindo fronteiras e 2–3 alternativas, e aguarde aprovação antes de editar.
3. Depois da aprovação, registre uma especificação e um plano curto de arquivos.
4. Escreva testes que falham antes de qualquer código de produção.
5. Implemente o mínimo necessário para os testes passarem.
6. Execute a suíte relevante após cada ciclo RED/GREEN.
7. Faça auto-revisão contra as RFCs 050, 060, 101, 102, 103, 104, 501, 502 e 601.
8. Antes de declarar conclusão, execute verificação fresca da suíte, compilação e busca de imports proibidos.

## Testes obrigatórios

Cubra pelo menos:

- execução final simples em `COMPLETED`;
- resposta que solicita Tool e fluxo `RUNNING -> WAITING_TOOL -> RUNNING`;
- espera de entrada do usuário em `WAITING_USER`;
- pausa em limite seguro e retorno `PAUSED`;
- cancelamento antes de Provider, durante retorno externo e após retorno externo;
- timeout de Execution, Provider e ação, mantendo-os distintos de cancelamento;
- limite de iterações, custo e tokens;
- falha de seleção de modelo, Provider, ação, Context e checkpoint;
- resultado tardio após cancelamento sem reabrir a Execution;
- aquisição duplicada e execução concorrente impedidas por `ExecutionControl`;
- recuperação a partir de checkpoint sem repetir efeito já confirmado;
- retry permitido somente com operação idempotente/reconciliável;
- `ExecutionControl` sendo a única porta mutante;
- ausência de publicação direta de Event pelo Runtime;
- propagação de ownership, correlação e finalidade;
- nenhum segredo ou payload sensível nos tipos, eventos, logs de teste ou outcomes.

## Resultado esperado

Ao final, entregue:

- arquivos criados/modificados;
- testes executados e resultado;
- decisões de interpretação da RFC 101;
- limitações e pontos fora de escopo;
- confirmação explícita de que `Execution` continua sendo a única fonte de mutação de estado e que não foi criado nenhum adapter tecnológico.

Não avance para Context Manager real, Providers, Tools, Capabilities, workers, Event Bus real ou persistência PostgreSQL nesta sessão.
