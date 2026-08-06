# AgentOS Backend Documentation Design

**Idioma:** PT-BR  
**Estado:** Concluído  
**Escopo:** arquitetura do backend; sem implementação, scaffolding, endpoints ou modelos concretos.

## Objetivo

Produzir um acervo arquitetural completo e navegável para o backend do AgentOS antes da implementação. O acervo deve transformar a visão de alto nível e o handoff em contratos de subsistemas, decisões rastreáveis e invariantes verificáveis.

## Estrutura do acervo

```text
docs/
├── architecture/
│   ├── 000-overview.md
│   ├── 050-design-principles.md
│   ├── 100-kernel/
│   ├── 200-agents/
│   ├── 300-context-memory/
│   ├── 400-tools-resources/
│   ├── 500-providers-models/
│   ├── 600-platform-data/
│   ├── 700-api-security/
│   ├── 800-operations/
│   └── 900-extensibility/
├── adr/
│   └── NNN-<decision>.md
└── superpowers/specs/
    └── 2026-08-05-agentos-backend-documentation-design.md
```

Os documentos em `architecture/` são RFCs normativas por subsistema. Os documentos em `adr/` registram decisões arquiteturais estáveis, seu contexto e consequências. A numeração representa a ordem conceitual de dependência; não representa prioridade de entrega.

## Mapa de RFCs

| Área | RFCs concluídas |
| --- | --- |
| Fundamentos | `000-overview`, `050-design-principles`, `060-glossary-and-conventions` |
| Kernel | `101-runtime`, `102-execution-lifecycle`, `103-event-system`, `104-context-pipeline` |
| Agentes | `201-agent`, `202-orchestrator`, `203-multi-agent` |
| Contexto e memória | `301-memory`, `302-blackboard`, `303-context-sharing` |
| Tools e recursos | `401-tool-runtime`, `402-resource-manager`, `403-filesystem`, `404-terminal`, `405-browser`, `406-capabilities` |
| Providers | `501-provider-api`, `502-model-catalog` |
| Plataforma e dados | `601-persistence`, `602-artifact-storage`, `603-workspaces`, `604-configuration` |
| API e segurança | `701-api-sse`, `702-security` |
| Operação | `801-workers`, `802-scheduler`, `803-observability` |
| Extensibilidade | `901-plugin-sdk`, `902-skills`, `903-mcp-future` |

## Modelo obrigatório para uma RFC

Cada RFC deve conter, quando aplicável:

1. objetivo e fora de escopo;
2. responsabilidades e não responsabilidades;
3. arquitetura e dependências permitidas/proibidas;
4. fluxos normais, falhas, timeout, cancelamento e recuperação;
5. contratos públicos em pseudocódigo tipado, com pré e pós-condições;
6. entidades, ownership, persistência, retenção e índices conceituais;
7. eventos publicados/consumidos e requisitos de ordenação/correlação;
8. segurança, isolamento de workspace e tratamento de segredos;
9. observabilidade, extensibilidade, invariantes e evolução futura.

## Convenções normativas

- Termos obrigatórios usam **DEVE**, **NÃO DEVE**, **PODE** e **RECOMENDADO** conforme o seu sentido normativo.
- Eventos são fatos no passado e usam `PascalCase`, por exemplo `ExecutionStarted`.
- Todo evento possui identidade, instante, correlação e origem; o formato será definido pela RFC de eventos.
- Toda entidade persistente é preparada para multiusuário com `user_id`; entidades vinculadas a projeto carregam `workspace_id` quando aplicável.
- Estados de longa duração são máquinas de estado explícitas com transições proibidas e comportamento de recuperação.
- O Kernel conhece apenas interfaces: Runtime não conhece FastAPI, React, Playwright nem acesso direto ao banco.
- Tools executam uma única responsabilidade; Capabilities orquestram Tools; Tools não chamam Tools.
- Contexto é temporário; memória é persistente; referências são preferidas a cópia de históricos brutos.
- Pseudocódigo descreve contratos e não constitui implementação Python.

## ADRs aceitas

1. `001-arq-workers`
2. `002-postgresql-as-system-of-record`
3. `003-sse-for-client-event-streaming`
4. `004-playwright-browser-workers`
5. `005-local-workspaces`
6. `006-single-user-multi-tenant-ready`
7. `007-server-side-sessions`
8. `008-artifact-storage-abstraction`
9. `009-redis-for-ephemeral-coordination`
10. `010-provider-ports-and-model-catalog`
11. `011-fastapi-api-adapter`
12. `012-sqlalchemy-alembic-persistence-adapters`
13. `013-asyncio-concurrency-runtime`
14. `014-pydantic-boundary-validation`

## Critérios de conclusão

- Toda responsabilidade listada no handoff possui uma RFC primária.
- As dependências entre RFCs são explícitas e não contradizem os invariantes.
- Os fluxos transversais — execução, eventos, cancelamento, isolamento, memória e recuperação — são consistentes em todo o acervo.
- Toda decisão estrutural da stack possui ADR correspondente.
- Nenhum documento introduz implementação de backend, código de produção, endpoint ou modelo ORM.

## Fora de escopo

- Implementação do backend ou frontend.
- Especificação visual do cliente desktop.
- Integrações de plugins concretos além dos contratos de extensão.
- Suporte operacional de produção, SLOs e infraestrutura de deploy específicos; a arquitetura mantém os pontos de extensão necessários.

## Revisão interna

Esta especificação cobre os 16 tópicos do roadmap original e as áreas adicionais indicadas pelo handoff. O acervo de 33 RFCs e 14 ADRs foi entregue; não há destinos ausentes. FastAPI, SQLAlchemy/Alembic, asyncio e Pydantic v2 possuem decisões próprias nas ADRs 011–014 e permanecem confinados às bordas definidas por essas decisões, sem transformar pseudocódigo arquitetural em implementação.
