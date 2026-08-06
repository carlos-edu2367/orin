# RFC 050 — Princípios de design e fronteiras

**Estado:** Fundação normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](000-overview.md), [RFC 060 — Glossário e convenções](060-glossary-and-conventions.md)

## Objetivo

Formalizar os invariantes arquiteturais do AgentOS e as dependências permitidas e proibidas entre seus subsistemas. Qualquer RFC futura deve conservar estes princípios ou registrar uma alteração explícita da fundação arquitetural.

## Responsabilidades

- definir as vinte regras que limitam decisões locais;
- estabelecer dependência por portas públicas e adapters substituíveis;
- separar Kernel, API, workers, Browser, Providers, Tools, Capabilities, contexto, memória e persistência;
- tornar violações de fronteira identificáveis em revisão arquitetural.

## Os vinte invariantes

1. **Tudo é uma Execution.** Toda ação que produz trabalho — inclusive mensagem, Skill, navegação, pesquisa, geração, criação de agente ou análise — DEVE existir como uma `Execution` identificável; não há caminho lateral de execução.
2. **O Runtime é o Kernel.** Regras de negócio, coordenação do ciclo de vida e aplicação dos contratos de execução pertencem ao Runtime e aos serviços de domínio acessados por portas públicas.
3. **O Runtime é independente de entrega e infraestrutura.** Ele NÃO DEVE conhecer FastAPI, React, HTTP, SSE, Playwright, banco de dados, ORM, fila ou SDK concreto de Provider.
4. **Todo módulo depende de interfaces públicas.** Dependências entre módulos DEVEM atravessar portas documentadas; detalhes internos, tabelas, clientes concretos e estado privado NÃO DEVEM ser importados por consumidores.
5. **A API não executa agentes.** A borda PODE autenticar, autorizar, validar, criar ou consultar uma Execution e solicitar cancelamento, mas NÃO DEVE executar o loop do agente nem trabalho pesado.
6. **Workers executam trabalho pesado.** Execuções assíncronas, longas ou intensivas DEVEM ser despachadas para o pool apropriado, com identidade, correlação, cancelamento e recuperação preservados.
7. **Browser roda em workers próprios.** Playwright e recursos equivalentes DEVEM permanecer em Browser Workers isolados; Runtime, API e workers genéricos NÃO DEVEM controlar browser diretamente.
8. **Providers não vazam.** Tipos, payloads, exceções, nomes de modelo e semântica proprietária DEVEM ser traduzidos no adapter e NÃO DEVEM contaminar contratos do Runtime, de Agent ou de Context.
9. **Tools são atômicas.** Uma Tool DEVE ter responsabilidade operacional única, contrato explícito, limites de recurso e resultado observável.
10. **Tools não chamam Tools.** Composição, sequência, repetição ou decisão entre Tools NÃO DEVE ser implementada dentro de uma Tool.
11. **Capabilities coordenam Tools.** Trabalho composto DEVE pertencer a uma Capability, a um Agent ou ao Orchestrator, conforme o contrato da RFC responsável, mantendo cada Tool atômica.
12. **Contexto é temporário.** Context é montado para uma Execution, limitado por política e descartável; NÃO DEVE ser tratado como fonte durável de verdade.
13. **Memória é persistente.** Memory registra conhecimento durável com ownership, escopo, proveniência e política de retenção; NÃO DEVE ser confundida com a janela de contexto corrente.
14. **Compartilhamento prefere referências.** Agentes DEVEM compartilhar Artifacts, Memory e resultados por referências e handoffs estruturados; copiar históricos brutos indiscriminadamente é PROIBIDO.
15. **Eventos são fatos observáveis.** Mudanças relevantes DEVEM publicar eventos no passado, correlacionáveis e com origem explícita; consumidores NÃO DEVEM reinterpretar comandos como fatos concluídos.
16. **Ownership e isolamento são explícitos.** Entidades DEVEM estar preparadas para múltiplos usuários por `user_id`; entidades de projeto DEVEM usar `workspace_id` quando aplicável, mesmo no lançamento single-user.
17. **Cancelamento e falha são de primeira classe.** Operações longas DEVEM definir propagação de cancelamento, timeout, falha e recuperação; nenhuma fronteira PODE silenciar erro ou reportar sucesso inexistente.
18. **Persistência fica atrás de portas.** PostgreSQL, Redis, filesystem e Artifact Storage são adapters com responsabilidades distintas; domínio e Runtime NÃO DEVEM acessar banco ou cache diretamente.
19. **Tudo é observável.** Cada subsistema DEVE emitir os eventos, logs, métricas e correlação necessários para reconstruir o fluxo sem expor segredos ou conteúdo além do autorizado.
20. **Tudo é substituível.** Providers, stores, filas, recursos, Browser, Memory e demais integrações DEVEM ser trocáveis por adapters compatíveis, sem alteração das regras do Kernel nem `switch/case` distribuído por implementações concretas.

## Regra de dependência: portas e adapters

Uma **porta** é uma interface pública definida pelo módulo que necessita de uma capacidade. Um **adapter** traduz uma tecnologia ou sistema externo para essa porta. O consumidor conhece o contrato; a composição do sistema escolhe o adapter.

```text
Regra de domínio ──depende de──> Porta pública <──implementada por── Adapter ──> Tecnologia
```

São permitidos:

- Runtime chamar uma porta de `EventBus`, `ExecutionRepository`, `Provider` ou `Resource`;
- API chamar uma porta de aplicação para solicitar ou consultar Execution;
- Worker receber um identificador, carregar a Execution por uma porta e invocar o Runtime;
- Capability chamar Tools por meio do Tool Runtime;
- adapter traduzir erros e dados concretos para tipos públicos estáveis.

São proibidos:

- Runtime importar FastAPI, React, Playwright, SQLAlchemy, Redis ou SDK de Provider;
- API instanciar ou executar Agent Runtime em seu processo de requisição;
- Browser adapter executar dentro do processo da API ou do Runtime;
- domínio consultar tabelas, filas, cache ou filesystem diretamente;
- Provider expor respostas, exceções ou objetos de streaming proprietários além de sua porta;
- Tool chamar outra Tool, acessar registro interno de Tools ou assumir um adapter concreto;
- consumidor importar módulo interno para contornar uma porta pública.

## Fronteiras por subsistema

| Origem | Pode depender de | Não pode depender de |
| --- | --- | --- |
| Runtime / Kernel | interfaces públicas de domínio e infraestrutura | FastAPI, React, Playwright, ORM, banco, Redis, SDKs concretos |
| API | portas de aplicação, autenticação e serialização de transporte | loop de Agent, Tool concreta, Browser, acesso a regras internas |
| Worker genérico | fila por adapter, Runtime por porta, stores por porta | detalhes da API, Browser/Playwright |
| Browser Worker | contrato de job, porta de eventos, adapter de Browser | processo da API, internos do Runtime, outro workspace |
| Provider adapter | SDK externo e porta de Provider | tipos internos de Context/Execution além do contrato público |
| Tool | sua porta de Resource e políticas autorizadas | outra Tool, catálogo interno, tecnologia não declarada |
| Capability | Tool Runtime e contratos públicos de Tools | detalhes internos ou adapters concretos das Tools |
| Persistência | modelos de armazenamento internos ao adapter | regra de negócio, controle de Execution, tipos proprietários vazados |

## Fluxos e aplicação das fronteiras

Uma solicitação entra na API e é traduzida para uma intenção de aplicação. A aplicação cria ou modifica uma Execution por uma porta, publica os fatos correspondentes e entrega trabalho à fila. O worker apropriado recupera a Execution e chama o Runtime. O Runtime coordena Agents, Capabilities e Tools apenas por interfaces. Acesso concreto a Provider, armazenamento ou Resource ocorre em adapters. Eventos carregam correlação de ponta a ponta.

Cancelamento segue o caminho inverso por sinal explícito e idempotente. Falhas são traduzidas na fronteira onde surgem, preservando causa e correlação, e são registradas como fatos sem expor segredos.

## Invariantes de revisão

Uma proposta é incompatível com esta RFC se:

- criar trabalho fora de uma Execution;
- introduzir dependência concreta no Kernel ou cruzar ownership sem autorização;
- executar Agent ou Browser na API;
- compor Tools dentro de Tool;
- persistir Context como se fosse Memory;
- omitir `user_id`, `workspace_id` aplicável ou correlação;
- tornar um adapter concreto condição da regra de negócio;
- criar estado ou fluxo que não possa ser observado e substituído por contrato.

## Extensibilidade e futuro

Novos Providers, Tools, Capabilities, Resources, stores e mecanismos de transporte PODEM ser adicionados por registros e adapters. Uma extensão DEVE declarar interfaces consumidas, permissões, ownership, eventos, limites de recursos e compatibilidade. As RFCs 901–903 detalharão mecanismos de extensão sem alterar estes invariantes por implicação.

## Fora de escopo

- escolher assinaturas finais das portas;
- definir modelos de banco, endpoints, filas ou configuração executável;
- estabelecer tecnologia de injeção de dependência;
- detalhar máquinas de estado, envelopes de evento ou políticas específicas de cada Resource;
- implementar validação automática dos invariantes.
