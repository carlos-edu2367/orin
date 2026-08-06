# RFC 000 — Visão geral da arquitetura do AgentOS

**Estado:** Fundação normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 050 — Princípios de design](050-design-principles.md), [RFC 060 — Glossário e convenções](060-glossary-and-conventions.md)

## Objetivo

Este documento estabelece a missão, o escopo e o mapa conceitual do acervo arquitetural do AgentOS. Ele orienta a leitura das RFCs e ADRs sem antecipar os contratos que cabem a cada documento especializado.

## Missão

O AgentOS é uma plataforma open source para executar agentes persistentes, colaborativos e orientados a eventos. Sua missão é evoluir de assistente pessoal para um Sistema Operacional para Agentes, preservando modularidade, observabilidade, extensibilidade, isolamento e independência entre componentes.

## Escopo

O acervo define a arquitetura do backend antes da implementação: responsabilidades, fronteiras, contratos públicos, ownership, fluxos, eventos, persistência conceitual, falhas, segurança e pontos de extensão. O primeiro lançamento opera para um único usuário, mas entidades e contratos são preparados para múltiplos usuários por `user_id`; dados associados a projeto usam `workspace_id` quando aplicável.

## Responsabilidades deste documento

- fornecer uma visão comum do sistema e de suas camadas;
- registrar a analogia normativa de sistema operacional;
- identificar os fluxos transversais que todas as RFCs devem respeitar;
- definir a ordem recomendada de leitura;
- manter o índice canônico dos 33 documentos de arquitetura concluídos e das 14 decisões aceitas.

Este documento não substitui as RFCs especializadas nem decide detalhes reservados a elas.

## Analogia de sistema operacional

| AgentOS | Sistema operacional | Papel arquitetural |
| --- | --- | --- |
| Runtime | Kernel | aplica regras de execução por interfaces públicas |
| Agent | Processo inteligente | recebe objetivos e produz trabalho dentro de uma Execution |
| Tool | Syscall | realiza uma operação atômica e controlada |
| Capability | Programa composto | coordena Tools para entregar uma capacidade de nível superior |
| Context | RAM | estado temporário montado para a execução corrente |
| Artifact Storage | Disco | armazenamento durável de conteúdos e resultados endereçáveis |
| Workspace | Filesystem | limite lógico de arquivos, recursos e colaboração de um projeto |
| Event Bus | Eventos do Kernel | distribui fatos observáveis e correlacionáveis do sistema |
| Resource | Dispositivo | capacidade externa protegida por uma interface e uma política |
| Interface de cliente | Interface gráfica | controla e observa o sistema sem conter suas regras de negócio |

A analogia orienta as fronteiras, mas não implica reproduzir APIs ou mecanismos de um sistema operacional tradicional.

## Arquitetura geral e camadas

```text
Clientes e interfaces
        │ comandos / consultas / eventos
Borda de API e segurança
        │ solicitações de Execution
Orquestração, filas e workers
        │ despacho / cancelamento / recuperação
Kernel: Runtime, Execution, Context e Event Bus
        │ portas públicas
Domínios: Agents, Memory, Tools, Capabilities e Models
        │ portas públicas
Recursos e adapters: Providers, Browser, Terminal, Filesystem e Storage
        │
Infraestrutura externa
```

As dependências apontam para contratos públicos. O Kernel não importa frameworks de entrega, automação de browser ou persistência concreta. A API cria, consulta ou solicita operações; ela não executa agentes. Trabalho pesado é despachado para workers, e automação de browser ocorre somente em Browser Workers próprios.

### Camadas

1. **Clientes e interfaces:** apresentam estado e enviam intenções; não são fonte de regras de negócio.
2. **Borda de API e segurança:** autentica, autoriza, valida transporte e traduz protocolos para portas da aplicação.
3. **Orquestração e operação:** enfileira, agenda, despacha, supervisiona e recupera trabalho.
4. **Kernel:** governa toda `Execution`, seu ciclo de vida, contexto, cancelamento e eventos.
5. **Domínios:** definem agentes, memória, Tools, Capabilities e seleção abstrata de modelos.
6. **Recursos e adapters:** conectam portas públicas a providers e recursos concretos sem fazer suas particularidades vazarem para o domínio.
7. **Infraestrutura externa:** oferece transporte, persistência, filas, modelos e dispositivos; permanece substituível.

## Fluxos transversais

### Execução

Toda ação que produz trabalho é representada por uma `Execution`. Uma intenção entra por uma porta pública, recebe identidade e ownership, é despachada, executada pelo Runtime e termina em estado explícito. O contrato detalhado pertence às RFCs 101 e 102.

### Eventos e correlação

Mudanças relevantes publicam fatos no passado. Todo evento tem identidade, instante, origem e correlação; eventos pertencentes a uma execução carregam `execution_id`. Consumidores observam eventos sem adquirir ownership do estado da fonte. O contrato detalhado pertence à RFC 103.

### Contexto, memória e artefatos

O Context é temporário e montado para uma Execution. Memory é persistente e possui ownership explícito. Conteúdo volumoso ou durável é armazenado como Artifact e compartilhado por referência sempre que possível. As RFCs 104, 301, 303 e 602 especializam esses limites.

### Cancelamento, timeout e recuperação

Cancelamento e timeout atravessam as camadas por contratos explícitos. Operações longas devem ser observáveis, interrompíveis nos limites seguros e recuperáveis segundo sua RFC de origem; nenhuma camada pode ocultar falhas convertendo-as em sucesso.

### Isolamento e ownership

Toda entidade atribuível a uma pessoa carrega `user_id`. Entidades de projeto carregam `workspace_id` quando aplicável. Acesso a recursos, memória, arquivos, artefatos e eventos respeita esses limites, ainda que o lançamento inicial seja single-user.

### Observabilidade e substituibilidade

Subsistemas expõem eventos, logs, métricas e correlação adequados à sua responsabilidade. Dependências concretas ficam atrás de interfaces públicas para que adapters possam ser substituídos sem alterar o Kernel.

## Invariantes

- Tudo que realiza trabalho é uma `Execution`.
- O Runtime conhece somente interfaces públicas e não conhece FastAPI, React, Playwright ou banco de dados.
- A API não executa agentes; workers executam trabalho pesado; Browser Workers executam automação de browser.
- Tools são atômicas e não chamam Tools; Capabilities coordenam Tools.
- Providers não vazam tipos ou semântica proprietária para o domínio.
- Context é temporário; Memory é persistente; compartilhamento prefere referências.
- Todo estado possui ownership, correlação e isolamento explícitos.
- Todo subsistema é observável e substituível dentro de sua fronteira pública.

A formulação normativa completa encontra-se na [RFC 050](050-design-principles.md).

## Leitura recomendada

1. Leia esta RFC para entender missão, camadas e navegação.
2. Leia a [RFC 050](050-design-principles.md) antes de propor qualquer contrato ou dependência.
3. Use a [RFC 060](060-glossary-and-conventions.md) para linguagem, IDs, eventos, ownership e pseudocódigo.
4. Prossiga para Kernel (101–104), pois seus contratos condicionam os demais subsistemas.
5. Leia as áreas 200–900 conforme a necessidade, seguindo as relações declaradas em cada RFC.
6. Consulte ADRs para entender por que uma decisão estrutural foi adotada; RFCs continuam sendo a fonte dos contratos.

## Índice navegável do acervo concluído

Todos os 33 documentos abaixo existem e compõem o acervo concluído desta etapa arquitetural. O estado editorial declarado em cada RFC continua determinando sua força normativa; “concluído” aqui significa documento entregue e navegável, não implementação de backend concluída.

### RFCs de arquitetura — 33

#### Fundamentos

1. [RFC 000 — Visão geral](000-overview.md) — mapa do acervo.
2. [RFC 050 — Princípios de design](050-design-principles.md) — invariantes e fronteiras.
3. [RFC 060 — Glossário e convenções](060-glossary-and-conventions.md) — linguagem normativa comum.

#### Kernel

4. [RFC 101 — Runtime](100-kernel/101-runtime.md) — contrato do Kernel.
5. [RFC 102 — Ciclo de vida da Execution](100-kernel/102-execution-lifecycle.md) — estados e transições.
6. [RFC 103 — Sistema de eventos](100-kernel/103-event-system.md) — Event Bus e envelope.
7. [RFC 104 — Pipeline de contexto](100-kernel/104-context-pipeline.md) — montagem e descarte.

#### Agentes

8. [RFC 201 — Agent](200-agents/201-agent.md) — identidade e contrato.
9. [RFC 202 — Orchestrator](200-agents/202-orchestrator.md) — coordenação.
10. [RFC 203 — Multi-agent](200-agents/203-multi-agent.md) — delegação e handoff.

#### Contexto e memória

11. [RFC 301 — Memory](300-context-memory/301-memory.md) — persistência de memória.
12. [RFC 302 — Blackboard](300-context-memory/302-blackboard.md) — conhecimento colaborativo.
13. [RFC 303 — Compartilhamento de contexto](300-context-memory/303-context-sharing.md) — referências e handoffs.

#### Tools e recursos

14. [RFC 401 — Tool Runtime](400-tools-resources/401-tool-runtime.md) — execução atômica.
15. [RFC 402 — Resource Manager](400-tools-resources/402-resource-manager.md) — acesso a recursos.
16. [RFC 403 — Filesystem](400-tools-resources/403-filesystem.md) — política de arquivos.
17. [RFC 404 — Terminal](400-tools-resources/404-terminal.md) — sessões de terminal.
18. [RFC 405 — Browser](400-tools-resources/405-browser.md) — automação isolada.
19. [RFC 406 — Capabilities](400-tools-resources/406-capabilities.md) — composição de Tools.

#### Providers e modelos

20. [RFC 501 — Provider API](500-providers-models/501-provider-api.md) — porta uniforme.
21. [RFC 502 — Model Catalog](500-providers-models/502-model-catalog.md) — resolução de perfis.

#### Plataforma e dados

22. [RFC 601 — Persistência](600-platform-data/601-persistence.md) — limites de dados.
23. [RFC 602 — Artifact Storage](600-platform-data/602-artifact-storage.md) — armazenamento durável.
24. [RFC 603 — Workspaces](600-platform-data/603-workspaces.md) — isolamento de projeto.
25. [RFC 604 — Configuração](600-platform-data/604-configuration.md) — fontes e precedência.

#### API e segurança

26. [RFC 701 — API e SSE](700-api-security/701-api-sse.md) — borda de transporte.
27. [RFC 702 — Segurança](700-api-security/702-security.md) — identidade, sessão e segredos.

#### Operação

28. [RFC 801 — Workers](800-operations/801-workers.md) — pools e execução assíncrona.
29. [RFC 802 — Scheduler](800-operations/802-scheduler.md) — agendamento e watchdogs.
30. [RFC 803 — Observabilidade](800-operations/803-observability.md) — sinais operacionais.

#### Extensibilidade

31. [RFC 901 — Plugin SDK](900-extensibility/901-plugin-sdk.md) — extensões registráveis.
32. [RFC 902 — Skills](900-extensibility/902-skills.md) — empacotamento de Skills.
33. [RFC 903 — MCP](900-extensibility/903-mcp-future.md) — contrato de integração futura.

### ADRs — 14

1. [ADR 001 — Arquitetura com ARQ workers](../adr/001-arq-workers.md).
2. [ADR 002 — PostgreSQL como system of record](../adr/002-postgresql-as-system-of-record.md).
3. [ADR 003 — SSE para eventos destinados ao cliente](../adr/003-sse-for-client-event-streaming.md).
4. [ADR 004 — Playwright em Browser Workers](../adr/004-playwright-browser-workers.md).
5. [ADR 005 — Workspaces locais](../adr/005-local-workspaces.md).
6. [ADR 006 — Single-user preparado para multiusuário](../adr/006-single-user-multi-tenant-ready.md).
7. [ADR 007 — Sessões server-side](../adr/007-server-side-sessions.md).
8. [ADR 008 — Abstração de Artifact Storage](../adr/008-artifact-storage-abstraction.md).
9. [ADR 009 — Redis para coordenação efêmera](../adr/009-redis-for-ephemeral-coordination.md).
10. [ADR 010 — Portas de Provider e Model Catalog](../adr/010-provider-ports-and-model-catalog.md).
11. [ADR 011 — FastAPI como adapter inicial da borda HTTP](../adr/011-fastapi-api-adapter.md).
12. [ADR 012 — SQLAlchemy e Alembic nos adapters de persistência](../adr/012-sqlalchemy-alembic-persistence-adapters.md).
13. [ADR 013 — asyncio como base de concorrência do processo](../adr/013-asyncio-concurrency-runtime.md).
14. [ADR 014 — Pydantic v2 para validação nas bordas](../adr/014-pydantic-boundary-validation.md).

## Extensibilidade e futuro

O mapa reserva contratos para plugins, Skills e MCP sem escolher antecipadamente seus formatos. Novos subsistemas devem entrar por interfaces públicas, declarar ownership e eventos, respeitar isolamento e receber RFC ou ADR quando alterarem contratos ou decisões estruturais. A numeração expressa dependência conceitual, não prioridade de entrega.

## Fora de escopo

- implementação de backend ou frontend;
- endpoints, schemas ORM, migrations e configuração executável;
- desenho visual do cliente;
- contratos detalhados pertencentes às RFCs especializadas;
- infraestrutura específica de deploy, SLOs ou operação de produção.
