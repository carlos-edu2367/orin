# Agentic Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformar a home numa conversa persistida e em streaming, com worker real e estado de execution recuperável.

**Architecture:** Persistir conversations/messages/turns/dispatches e eventos públicos no PostgreSQL. Um publisher enfileira IDs duráveis em Redis/ARQ; worker separado executa o provider configurado e publica deltas. O React usa histórico + SSE e rotas de chat, não a execution técnica.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Redis/ARQ, httpx, React/Vite/Vitest/Playwright.

## Global Constraints

- Não expor credenciais, task refs, IDs internos ou payload upstream no browser.
- Manter alterações locais não relacionadas intactas.
- Todo envio é idempotente; fila sem worker resulta em falha/retry acionável.

---

### Task 1: Persistência e runtime de conversa

**Files:** schema, migration, `agentos.conversations`, worker e testes unitários.

- [ ] Escrever RED para um turn persistido ser adquirido, receber deltas e terminar, e para watchdog falhar fila não adquirida.
- [ ] Criar tabelas/repositório, dispatcher/outbox, worker ARQ e health operacional.
- [ ] Executar testes unitários focados e integração PostgreSQL/Redis.

### Task 2: API e reconexão

**Files:** gateway/contracts, testes API e cliente frontend.

- [ ] Escrever RED de create/list/history/send/SSE/retry autorizados e idempotentes.
- [ ] Implementar rotas e DTOs sanitizados; reidratação precede stream.
- [ ] Executar API/unitários e smoke com dependências locais.

### Task 3: Shell e chat

**Files:** rotas, sidebar, `ChatPage`, composer, estilos e testes React/E2E/a11y.

- [ ] Escrever RED para navegação `/chats/:conversationId`, mensagem imediata, streaming e retry.
- [ ] Implementar shell responsivo, overview e manter deep link técnico.
- [ ] Executar Vitest, build, Playwright, a11y e visual.
