# Task 1 — Criar fundação editorial

Leia este arquivo primeiro: ele é a fonte completa dos requisitos desta tarefa.

## Arquivos a criar

- `docs/architecture/000-overview.md`
- `docs/architecture/050-design-principles.md`
- `docs/architecture/060-glossary-and-conventions.md`

## Objetivo

Criar a base normativa de todo o acervo arquitetural do AgentOS em PT-BR. O projeto é um Sistema Operacional para Agentes: Runtime é o Kernel, agentes são processos inteligentes, Tools são syscalls, Capabilities são programas compostos, Context é RAM, Artifact Storage é disco, Workspace é filesystem e Event Bus são eventos do Kernel.

## Requisitos obrigatórios

1. Não implementar produto: não criar código de produção, scaffolding, endpoints, schemas ORM ou configuração executável. Pseudocódigo tipado só pode explicar contratos.
2. `000-overview.md` deve declarar missão, escopo, arquitetura geral, analogia de sistema operacional, camadas, fluxos transversais, leitura recomendada e índice navegável de todas as RFCs e ADRs planejadas.
3. `050-design-principles.md` deve formalizar os vinte invariantes do handoff e as fronteiras permitidas/proibidas. DEVE incluir: tudo é Execution; Runtime não conhece FastAPI, React, Playwright ou banco; API não executa agentes; Browser roda em workers próprios; Providers não vazam; Tools não chamam Tools; Capabilities coordenam Tools; Contexto é temporário; Memória é persistente; todo módulo depende de interfaces públicas; tudo é observável e substituível.
4. `060-glossary-and-conventions.md` deve definir Agent, Execution, Task, Event, Tool, Capability, Resource, Workspace, Context, Memory, Artifact e Provider; além de convenções de nomenclatura, IDs, tempo, correlação, ownership, termos normativos, referências de RFC/ADR, eventos e pseudocódigo.
5. Eventos são fatos no passado, usam PascalCase, possuem identidade, instante, origem e correlação. Eventos vinculados a execução usam `execution_id`.
6. Entidades devem ser preparadas para multiusuário por `user_id`; entidades de projeto usam `workspace_id` quando aplicável. O lançamento continua single-user.
7. Documentos devem indicar relações com as RFCs futuras, sem presumir conteúdo não definido.
8. Cada documento deve ter objetivo, responsabilidades quando aplicável, invariantes, extensibilidade/futuro e fora de escopo.

## Verificação

- Conferir que os três arquivos são Markdown em PT-BR e que não há marcadores pendentes.
- Conferir que o índice lista exatamente as 33 RFCs e 10 ADRs do plano em `docs/superpowers/plans/2026-08-05-agentos-backend-architecture-documentation.md`.

## Relatório exigido

Ao concluir, criar `.superpowers/sdd/2026-08-05-agentos-backend-architecture-documentation/task-1-report.md` usando `apply_patch`. Informe: status `DONE` ou `DONE_WITH_CONCERNS`, arquivos criados, verificações feitas, decisões interpretativas e qualquer preocupação. Na resposta, retorne apenas status, lista curta dos arquivos e resumo de verificação.
