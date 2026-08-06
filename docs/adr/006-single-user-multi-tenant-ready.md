# ADR 006 — Lançamento single-user com modelo pronto para multi-tenancy

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O primeiro lançamento do AgentOS atende uma única pessoa, mas Agents, `Execution`s, Workspaces, memória, Artifacts, eventos, sessões, quotas e segredos já possuem limites de ownership. Remover esses limites para simplificar o lançamento criaria uma migração posterior perigosa: dados sem dono, chaves efêmeras reutilizáveis, consultas sem filtro, auditoria ambígua e referências capazes de atravessar projetos.

Multi-tenancy futuro não é apenas adicionar login. Ele exige que cada decisão de armazenamento, cache, sessão, cursor, lock, Artifact, recurso, Provider e evento preserve isolamento por usuário e Workspace, inclusive durante falhas, retries, revogações e replays.

## Decisão

Lançar como **single-user na experiência de produto**, mas manter desde o início o modelo e as verificações **multi-tenant-ready**. Toda entidade pertencente a pessoa carrega `user_id`; toda entidade de projeto inclui `workspace_id` quando aplicável. Operações sensíveis carregam contexto com `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e finalidade, e revalidam ownership e versão na porta proprietária.

O modo single-user não autoriza defaults implícitos, filtro somente no cliente ou bypass de ownership. Consultas aplicam escopo no store ou na porta autorizada; referências cross-user e cross-workspace são negadas por padrão. Namespaces de Redis, Artifact, quota, cursor, rate limit, lock e sessão incluem tenancy. A API, Resources e Workers usam grants e referências opacas, não o conhecimento de IDs, para conceder acesso.

Organizações, membros e compartilhamento não fazem parte do lançamento. Eles só poderão ampliar a policy por decisões explícitas e auditáveis, sem remover `user_id`, `workspace_id`, finalidade, versões, revogação ou isolamento existentes.

## Consequências

### Benefícios

- Evita uma migração corretiva de dados e segurança quando colaboração ou múltiplos usuários forem introduzidos.
- Faz o código e a documentação exercitarem as mesmas fronteiras de autorização, cache e recuperação desde o primeiro usuário.
- Reduz risco de enumeração, vazamento por referências ou reaproveitamento de estado efêmero quando novas identidades surgirem.
- Mantém ownership, auditoria e retenção atribuíveis mesmo no ambiente local.

### Custos e falhas aceitas

- Campos, índices, filtros, contextos e testes de isolamento aumentam o custo inicial de desenho e operação.
- Contexto ausente, divergente ou impossível de revalidar falha fechado em vez de assumir o único usuário atual.
- Namespacing eleva cardinalidade e exige monitoramento de quotas, caches, conexões e métricas para não expor identificadores entre escopos.
- A escolha não traz organizações, convite, papéis colaborativos, billing, compartilhamento nem disponibilidade empresarial por si só.

### O que esta decisão não resolve

Esta decisão não define o modelo de organização, ACL de compartilhamento, federação de identidade, convite, cobrança ou migração de dados entre tenants. Também não substitui autenticação, autorização por recurso, criptografia, auditoria ou controles de abuso.

## Alternativas consideradas

- **Adicionar `user_id` somente ao habilitar multiusuário:** rejeitada porque históricos, referências, caches e dados existentes perderiam uma fronteira segura de migração.
- **Assumir implicitamente o único usuário na camada de aplicação:** rejeitada porque APIs internas, Workers e stores poderiam contornar o escopo sem evidência auditável.
- **Usar `agent_id` ou `execution_id` como tenant:** rejeitada porque ambos são recursos subordinados e não representam identidade, Workspace ou política de acesso.
- **Implementar organizações completas antes do lançamento:** adiada; ampliaria produto e operação sem necessidade atual, preservando os invariantes que permitem tal evolução.

## Relações com RFCs

- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) define `user_id`, isolamento, autorização, revogação e namespaces de tenancy.
- [RFC 603 — Workspaces](../architecture/600-platform-data/603-workspaces.md) define o Workspace como fronteira de projeto e ownership.
- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) define a autoridade durável e revalidação de estado.
- [RFC 602 — Artifact Storage](../architecture/600-platform-data/602-artifact-storage.md) isola namespace e referências por usuário e Workspace.
- [RFC 701 — API e SSE](../architecture/700-api-security/701-api-sse.md) exige streams e consultas autorizados por recurso.
- [RFC 801 — Workers e filas](../architecture/800-operations/801-workers.md) exige contexto e reconciliação seguros entre pools.
