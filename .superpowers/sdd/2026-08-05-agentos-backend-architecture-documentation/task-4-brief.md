# Task 4 — Documentar memória e conhecimento compartilhado

Crie somente Markdown, sem implementar backend. Leia fundações, Kernel e agentes em `docs/architecture/`. Crie:

- `docs/architecture/300-context-memory/301-memory.md`
- `docs/architecture/300-context-memory/302-blackboard.md`
- `docs/architecture/300-context-memory/303-context-sharing.md`

## Requisitos

1. `301-memory.md`: Memory Manager separado do ContextManager; definir Private, Workspace, User e Semantic Memory, ownership, proveniência, escopo, escrita/leitura, retenção, invalidamento, consolidação, busca, auditoria, proteção de dados e eventos. Contexto é temporário; memória é permanente.
2. `302-blackboard.md`: definir conhecimento compartilhado de decisões, descobertas, bugs, tarefas, contratos e arquitetura; modelar itens, versionamento, conflitos, autoria, visibilidade, referências, auditoria e expiração. Blackboard não substitui fonte transacional de verdade nem memória privada.
3. `303-context-sharing.md`: definir referências, snapshots mínimos e handoffs estruturados entre agentes; permissões, filtros, orçamento, revogação, ciclo de vida, eventos e falhas. Proibir copiar centenas de mensagens/histórico bruto.
4. Cada RFC inclui objetivo, fora de escopo, responsabilidades/não responsabilidades, arquitetura, contratos tipados não executáveis, dados/entidades, eventos no passado, fluxos normal/falha/cancelamento, segurança, observabilidade, extensibilidade, invariantes e futuro.
5. Usar `user_id`, `workspace_id` quando aplicável, `agent_id`, `execution_id` e `correlation_id` conforme convenções. Nunca permitir vazamento entre workspaces ou agentes sem autorização explícita.
6. Links relativos válidos; não criar modelos ORM, endpoints ou implementação de busca vetorial.

## Verificação e relatório

Verificar que nenhuma RFC mistura memória com contexto e que todo compartilhamento de contexto é referencial/autorizado. Criar `.superpowers/sdd/2026-08-05-agentos-backend-architecture-documentation/task-4-report.md` com status, arquivos, verificações, interpretações e preocupações. Responda somente status, arquivos e verificação curta.
