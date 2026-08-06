# Task 3 — Documentar agentes e orquestração

Crie apenas as RFCs Markdown abaixo e o relatório exigido. Não implemente backend. Leia antes as fundações (`docs/architecture/000-overview.md`, `050-design-principles.md`, `060-glossary-and-conventions.md`) e o Kernel (`docs/architecture/100-kernel/`).

## Arquivos

- `docs/architecture/200-agents/201-agent.md`
- `docs/architecture/200-agents/202-orchestrator.md`
- `docs/architecture/200-agents/203-multi-agent.md`

## Requisitos vinculantes

1. Agentes são persistentes e não são chats. Definir identidade, configuração, modelo, prompt, avatar/cor, tools, capabilities, skills, memória privada, owner, workspace e ciclo de vida administrativo sem contradizer persistência.
2. Orchestrator/Kernel coordena criação de agentes, criação de Executions, dependências, agendamento, distribuição mínima de contexto, timeout, cancelamento, estado e eventos. Não executa LLM diretamente nem vaza adapters.
3. Multiagente define criação, mensagens, delegação, espera, resultados, cancelamento e compartilhamento de contexto por referências e handoffs estruturados, nunca cópia indiscriminada de histórico.
4. Toda delegação e trabalho assíncrono é uma Execution; comunicacão tem `correlation_id`, ownership, autorização, deadlines, idempotência, propagação de falha e auditoria.
5. Definir contratos tipados não executáveis, entidades/dados, eventos no passado, fluxos normal/falha/cancelamento, segurança, observabilidade, invariantes, extensibilidade, futuro e fora de escopo em cada RFC.
6. Multiusuário preparado com `user_id`, escopo por `workspace_id` quando aplicável; o lançamento ainda é single-user.
7. Usar links relativos; não criar endpoint, tabela ORM, fila concreta ou código de produção.

## Verificação

Garantir coerência com estados de Execution, EventBus e ContextManager; garantir que agentes não são removidos como consequência de conversa e que nenhuma regra quebra o isolamento de agente/workspace/usuário.

## Relatório

Criar `.superpowers/sdd/2026-08-05-agentos-backend-architecture-documentation/task-3-report.md` com status, arquivos, verificações, interpretações e preocupações. Responda somente status, arquivos e verificação curta.
