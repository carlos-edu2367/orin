# Prompt da próxima sessão — Provider API e Model Catalog do AgentOS

Você vai implementar o próximo subsistema do backend do AgentOS: a porta pública de Provider e o Model Catalog das RFCs 501 e 502.

Não comece editando código. Leia integralmente os documentos abaixo e inspecione o código/testes existentes antes de definir o desenho:

- `C:\Users\reali\Documents\AgentOS\docs\architecture\000-overview.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\050-design-principles.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\060-glossary-and-conventions.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\101-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\102-execution-lifecycle.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\103-event-system.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\104-context-pipeline.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\300-context-memory\301-memory.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\300-context-memory\303-context-sharing.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\501-provider-api.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\502-model-catalog.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\601-persistence.md`

Inspecione também:

- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\runtime\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\context\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\execution\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\runtime\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\context\`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-runtime-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-context-pipeline-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-context-pipeline.md`

## Estado atual

O backend possui:

- `ExecutionControl` com ciclo de vida, ownership, idempotência, cancelamento, pausa, falha, resultado, uso e persistência em memória para testes;
- `RuntimeService` com loop síncrono, limites, checkpoints por referência, cancelamento cooperativo, pause/resume, Tool round-trip e recuperação;
- pacote canônico `agentos.context` com contratos RFC 104, montagem determinística, sanitização, orçamento, proveniência, manifestos, updates de turno e descarte efêmero;
- adaptador `RuntimeContextManagerAdapter` para a superfície mínima que o Runtime já consome;
- 93 testes unitários passando;
- nenhum Provider, Model Catalog, SDK de IA, banco, Event Bus, API, worker ou adapter tecnológico concreto.

O Runtime ainda recebe um `ModelResolver` Protocol simplificado e um `ProviderPort` Protocol. Este subsistema deve substituir os fakes conceituais por contratos de domínio completos e adapters injetáveis, sem implementar SDKs ou Providers reais.

## Objetivo

Implementar completamente o domínio backend das RFCs 501 e 502:

1. contratos públicos de Provider, invocação, streaming conceitual, uso, custo, capacidades, erros, cancelamento e retryability;
2. descriptors versionados de Provider e Model;
3. Model Catalog com registro, revisão imutável, status, perfis, pricing, disponibilidade e seleção explicável;
4. `ModelResolver` compatível com o Runtime e com o Context Pipeline;
5. snapshots aprovados de requisitos e seleção, imutáveis e revalidáveis;
6. fallback explícito, limitado e materializado;
7. preservação de ownership, finalidade, classificação, correlação e budget;
8. normalização de respostas, falhas, timeout, cancelamento, custo e uso;
9. integração do contrato completo com `RuntimeService`, sem importar Provider concreto;
10. testes determinísticos usando apenas fakes de portas.

## Escopo obrigatório

### Provider API — RFC 501

Defina e implemente contratos públicos para:

- `ProviderPort`/`ProviderInvocationPort` para geração e, quando previsto pela RFC, streaming conceitual;
- `ProviderDescriptor`, `ProviderModelBinding` e revisões imutáveis;
- `ProviderInvocationRequest`, `ProviderInvocationOutcome` e resultados finais, Tool requests, user-input requests, falhas e cancelamento;
- `ProviderUsage`, `ProviderCost`, limites e medição pública;
- `ProviderErrorCategory`, códigos sanitizados e retryability;
- `CancellationSignal`/cancelamento cooperativo;
- `ProviderOperationContext` com `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- referências opacas para seleção, requisitos aprovados, invocação, resposta e diagnóstico.

A porta pública NÃO pode expor SDK, headers, credenciais, binding tecnológico, payloads proprietários ou exceções de fornecedor.

### Model Catalog — RFC 502

Defina e implemente:

- `ModelDescriptor`, `ModelRevision`, capabilities públicas, tipos de input/output e limites de contexto;
- `ProviderDescriptor`, `ProviderStatus`, `ModelStatus` e transições válidas;
- `ModelProfile`, constraints, preferências, versionamento e policy;
- pricing versionado e custo desconhecido tratado como desconhecido;
- disponibilidade e validade de seleção;
- `ModelRequirements` e `ApprovedModelRequirementsSnapshot`;
- `ModelSelection`, `SelectedModel`, fallbacks materializados e `SelectionExplanation`;
- `ModelCatalogPort` para registrar/listar/consultar revisões, status, profiles, pricing e selections;
- `ModelResolver.resolve` e `resolve_fallback` com hard constraints antes de qualquer score;
- rejeições categóricas e ausência de candidato compatível.

### Resolução normativa

A resolução deve seguir esta ordem:

1. validar ownership, Agent, Execution, finalidade, classificação, budget e cancelamento;
2. fixar `catalog_version`, `policy_version`, revisões de descriptor/profile/pricing e disponibilidade;
3. formar candidatos autorizados;
4. remover Provider/modelo incompatível ou `DISABLED`/`RETIRED`;
5. aplicar hard constraints: classificação, região, contexto, input/output/total, capabilities, streaming, cancelamento, response format, allow/deny e budget;
6. calcular custo comparável sem tratar preço ausente como zero;
7. ordenar candidatos compatíveis de forma determinística e estável;
8. materializar seleção primária, fallbacks permitidos, explicação e validade;
9. persistir ou registrar a seleção por uma porta, antes de entregá-la ao Runtime;
10. revalidar seleção e snapshot aprovado imediatamente antes da invocação.

Hard constraints nunca podem virar preferência. Fallback não pode ampliar Provider, classificação, finalidade, capability ou budget.

## Integração com Runtime e Context

- Substitua o `ModelResolver` fake por uma implementação de domínio que satisfaça a porta pública completa.
- Preserve a superfície do Runtime quando necessário por adapter de compatibilidade, como feito para Context.
- O Runtime deve receber apenas `ModelSelection`, `ApprovedModelRequirementsSnapshot` e referências públicas mínimas.
- O Runtime não deve conhecer catálogo interno, binding, pricing proprietário, SDK ou adapter concreto.
- Toda solicitação a resolver/provider deve preservar os seis campos sensíveis e `purpose`.
- A seleção deve ser compatível com os limites e classificação usados pelo Context Pipeline.
- Não faça chamadas reais a Provider; crie somente fakes de teste e portas substituíveis.
- Não adicione retry implícito: retry/fallback só pode ocorrer quando retryability, idempotência, política e budget permitirem.

## Fora de escopo

Nesta sessão NÃO implementar:

- SDK OpenAI, Anthropic, Google ou qualquer SDK de fornecedor;
- HTTP client, credenciais, secrets manager ou autenticação concreta;
- streaming de rede real;
- tokenizer específico de fornecedor;
- PostgreSQL, Redis, ORM, Alembic, Event Bus ou outbox concreta;
- Memory, Artifact Storage, filesystem, Tool Runtime, Browser ou Resource;
- embeddings, ranking aprendido, benchmark ou roteamento opaco;
- FastAPI, SSE, workers, filas ou endpoints;
- fallback implícito para modelo sucessor ou “mais recente”.

## Requisitos de segurança e isolamento

- conhecer `provider_ref`, `model_ref`, `selection_ref`, `binding_ref` ou `invocation_ref` nunca concede acesso;
- ownership é revalidado em catalog queries, selection load, fallback e invocação;
- `RETIRED` nunca pode iniciar nova invocação;
- `DISABLED` não entra em nova seleção;
- depreciação não substitui modelo silenciosamente;
- credenciais, tokens, headers, prompts completos, respostas completas e bindings não aparecem em snapshot, manifest, log, Event ou `repr` público;
- preço confidencial é representado por classe/referência autorizada quando necessário;
- falha de Provider não pode ser reportada como sucesso;
- uso/custo de todas as tentativas deve ser preservado e monotônico;
- cancelamento antes da invocação impede novo efeito; cancelamento durante a invocação produz outcome explícito;
- uma seleção expirada exige nova resolução, nunca suposição de validade.

## Testes obrigatórios

Use TDD: escreva cada teste antes da implementação, execute-o falhando pelo motivo correto e só então implemente o mínimo necessário.

Cubra pelo menos:

- validação de todos os campos sensíveis e `purpose`;
- descriptors/revisões imutáveis e transições de status;
- registros idempotentes e conflitos de versão;
- hard constraints antes de score;
- custo ausente não tratado como zero;
- classificação e região incompatíveis;
- capabilities/input/output/context limit incompatíveis;
- seleção determinística sob o mesmo snapshot;
- explicação com rejeições sanitizadas;
- snapshot aprovado imutável e revalidado;
- fallback desabilitado, explícito e policy-based;
- fallback que não amplia permissões ou budget;
- Provider timeout, policy failure, auth failure, rate limit, invalid request, cancellation e indeterminate outcome;
- retry apenas para outcomes permitidos e idempotentes;
- uso/custo acumulados de tentativas primária e fallback;
- Provider/modelo desabilitado ou retirado antes da invocação;
- preservação de contexto sensível em Resolver, Provider, Runtime e Recorder;
- ausência de segredos e payload proprietário em `repr`, erros, manifestos e outcomes;
- Runtime integrado ao resolver completo sem dependência concreta;
- suíte existente de Execution, Runtime e Context sem regressões.

## Processo obrigatório da sessão

1. Leia integralmente as RFCs e o código listado acima.
2. Examine o estado inicial dos testes.
3. Faça um brainstorming curto e proponha o desenho do subsistema.
4. Registre a especificação em `docs/superpowers/specs/2026-08-06-provider-model-design.md`.
5. Registre o plano em `docs/superpowers/plans/2026-08-06-provider-model.md`.
6. Execute o plano em ciclos TDD, mantendo arquivos focados e interfaces públicas.
7. Não introduza dependências concretas ou infraestrutura fora do escopo.
8. Execute:

~~~
python -m pytest -q
python -m compileall -q src tests
~~~

9. Faça uma varredura para garantir que `src/agentos/providers` ou o pacote escolhido não importe SDK, FastAPI, banco, Redis, filesystem ou adapter concreto.
10. Só declare conclusão com evidência fresca dos comandos e uma auditoria explícita requisito por requisito contra RFC 501 e RFC 502.

## Critérios de conclusão

A sessão só está concluída quando:

- Provider API e Model Catalog possuem pacotes canônicos e contratos públicos estáveis;
- `ModelResolver` completo está integrado ao Runtime por porta/adaptador;
- seleção, snapshots, constraints, pricing, status, fallback, retryability, uso, custo, cancelamento e falhas estão cobertos;
- todas as operações sensíveis preservam ownership, correlação e finalidade;
- nenhum segredo, binding ou payload proprietário atravessa fronteiras públicas;
- não há Provider tecnológico real nem persistência concreta;
- testes novos e existentes passam integralmente;
- a especificação, plano e implementação estão coerentes com RFCs 050, 060, 101, 104, 501, 502 e 601.





