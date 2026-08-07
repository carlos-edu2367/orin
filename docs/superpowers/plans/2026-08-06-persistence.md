# Persistência transacional do AgentOS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Completar a porta RFC 601, o adapter PostgreSQL isolado e o adapter em memória com atomicidade, autorização, idempotência, concorrência otimista e reconciliação de commit.

**Architecture:** `agentos.persistence` mantém contratos independentes de tecnologia; `execution_compat.py` é a única ponte legada. `persistence.postgres` contém SQLAlchemy/Alembic e grava registros, auditoria, outbox e idempotência na mesma transação; o adapter em memória fornece o mesmo comportamento como referência.

**Tech Stack:** Python 3.13+, dataclasses congeladas, `Protocol`, `pytest`, SQLAlchemy 2, Alembic, SQLite apenas para harness, PostgreSQL opcional via `AGENTOS_TEST_POSTGRES_DSN`, `compileall`, `rg`.

## Global Constraints

- A porta canônica expõe somente `transact`, `read`, `scan` e `inspect_commit`.
- `COMMITTED` implica estado, auditoria, outbox e idempotência confirmados no mesmo commit.
- `UNKNOWN` exige `inspect_commit` antes de retry.
- Ownership, contexto, classificação, fingerprint, versão e filtros são revalidados no adapter.
- SQLAlchemy e Alembic aparecem somente em `src/agentos/persistence/postgres` e migrations.
- Migrations são aplicadas somente por `upgrade()` explícito.
- Não criar Redis, broker, worker, scheduler, API, Artifact Storage ou domínio fora da persistência.
- Preservar todas as alterações pré-existentes do working tree; usar `git add` somente com caminhos explícitos.

---

### Task 1: Registrar desenho e matriz de requisitos

**Files:**
- Create: `docs/superpowers/specs/2026-08-06-persistence-design.md`
- Create: `docs/superpowers/plans/2026-08-06-persistence.md`
- Create: `docs/superpowers/2026-08-06-persistence-requirement-matrix.md`

- [x] **Step 1: Registrar a especificação**

Documentar fronteira, fluxo, segurança, limitações e estratégia de testes.

- [x] **Step 2: Registrar o plano**

Mapear cada mudança a arquivos, testes e gates executáveis.

- [x] **Step 3: Registrar a matriz**

Relacionar RFC/ADR, contrato/arquivo, teste, lacuna, correção e evidência sem afirmar cobertura que não tenha teste.

---

### Task 2: Fechar contratos e adapter em memória com TDD

**Files:**
- Modify: `src/agentos/persistence/models.py`
- Modify: `src/agentos/persistence/in_memory.py`
- Modify: `src/agentos/persistence/security.py`
- Modify: `tests/unit/persistence/test_contracts.py`
- Modify: `tests/unit/persistence/test_in_memory_transactions.py`
- Modify: `tests/unit/persistence/test_in_memory_authorization.py`
- Modify: `tests/unit/persistence/test_security_regressions.py`

- [x] **Step 1: Escrever testes RED**

Cobrir contexto completo, fingerprint divergente, conflito de versão, rollback de validação, `UNKNOWN`/inspeção, outbox duplicada, ceiling, cursor inválido após revisão e mensagens sem segredo.

- [x] **Step 2: Rodar os testes focados e confirmar RED**

Executar `python -m pytest -q tests/unit/persistence/test_in_memory_transactions.py tests/unit/persistence/test_in_memory_authorization.py tests/unit/persistence/test_security_regressions.py` e confirmar falha pela lacuna esperada.

- [x] **Step 3: Implementar o mínimo**

Corrigir somente validação, atomicidade, cursor, idempotência e sanitização necessárias; preservar resultados públicos tipados.

- [x] **Step 4: Rodar GREEN e refatorar**

Executar os mesmos testes, depois `python -m pytest -q tests/unit/persistence`.

---

### Task 3: Fechar ponte explícita com Execution

**Files:**
- Modify: `src/agentos/persistence/execution_compat.py`
- Modify: `tests/unit/persistence/test_execution_persistence_compat.py`
- Modify: `tests/unit/execution/test_in_memory_persistence.py` somente se uma regressão de compatibilidade demonstrada exigir ajuste

- [x] **Step 1: Escrever teste RED**

Provar que `ExecutionControlService` continua usando apenas a ponte, preserva idempotência e traduz `UNKNOWN`, conflito e autorização sem expor o contrato canônico ao Runtime.

- [x] **Step 2: Confirmar RED**

Executar `python -m pytest -q tests/unit/persistence/test_execution_persistence_compat.py`.

- [x] **Step 3: Implementar GREEN**

Atualizar apenas traduções de request/result e validações de contexto necessárias.

- [x] **Step 4: Verificar**

Executar `python -m pytest -q tests/unit/persistence/test_execution_persistence_compat.py tests/unit/execution`.

---

### Task 4: Auditar e fechar adapter SQLAlchemy/Alembic com TDD

**Files:**
- Modify: `src/agentos/persistence/postgres/adapter.py`
- Modify: `src/agentos/persistence/postgres/errors.py`
- Modify: `src/agentos/persistence/postgres/schema.py`
- Modify: `src/agentos/persistence/postgres/migrations/versions/0001_initial_persistence.py`
- Modify: `src/agentos/persistence/postgres/migrations/versions/0002_persistence_integrity.py`
- Modify: `tests/unit/persistence/test_postgres_adapter.py`
- Modify: `tests/unit/persistence/test_postgres_schema.py`
- Modify: `tests/unit/persistence/test_migrations.py`
- Modify: `tests/integration/persistence/test_postgres_optional.py` somente para casos opcionais bounded

- [x] **Step 1: Escrever testes RED**

Cobrir commit atômico estado/auditoria/outbox, rollback, constraint/outbox duplicada, inspeção por escopo, cursor vinculado, opções de isolamento/timeout, erro de conexão no commit e ausência de migration implícita.

- [x] **Step 2: Confirmar RED**

Executar `python -m pytest -q tests/unit/persistence/test_postgres_adapter.py tests/unit/persistence/test_migrations.py tests/unit/persistence/test_postgres_schema.py`.

- [x] **Step 3: Implementar o mínimo**

Manter SQLAlchemy/Alembic isolados, normalizar erros sem mensagem de driver, usar constraints/índices e tornar a transação segura sob retry/commit indeterminado.

- [x] **Step 4: Verificar GREEN**

Executar a suíte unitária de persistência; executar integração PostgreSQL apenas se `AGENTOS_TEST_POSTGRES_DSN` existir e registrar o resultado.

---

### Task 5: Auditoria de fronteiras e matriz

**Files:**
- Modify: `docs/superpowers/2026-08-06-persistence-requirement-matrix.md`
- Modify: `tests/unit/persistence/test_requirement_matrix.py` somente se um requisito novo precisar de prova automatizada

- [x] **Step 1: Auditar imports**

Executar scan sem matches de SQLAlchemy/Alembic em Runtime, Execution, Context, Events, Agents e Providers; confirmar tecnologia confinada ao adapter/migrations.

- [x] **Step 2: Atualizar matriz com evidência fresca**

Registrar comandos, contagens, limitações PostgreSQL e qualquer linha ainda fora de escopo.

---

### Task 6: Gates finais e revisão independente

**Files:**
- Modify: arquivos aplicáveis da implementação e matriz após achados da revisão

- [x] **Step 1: Rodar gates antes da revisão**

Executar `python -m pytest -q`, `python -m compileall -q src tests`, scan de fronteiras e `git diff --check`.

- [x] **Step 2: Disparar subagente revisor somente leitura**

Entregar contexto/diff e pedir revisão contra RFC 601 e ADRs 002/009/012, buscando atomicidade, `UNKNOWN`, autorização, vazamento tecnológico e regressões, com severidade e arquivo/linha.

- [x] **Step 3: Tratar achados**

Para cada achado, decidir aplicável/não aplicável com evidência; corrigir os aplicáveis com teste RED/GREEN e atualizar a matriz.

- [x] **Step 4: Repetir todos os gates**

Executar novamente suíte completa, `compileall`, scans, `git diff --check`, status e resultado dos testes PostgreSQL opcionais antes da resposta final.

## Resultado da revisão independente

O subagente revisor somente leitura executou a suíte (`376 passed, 1 skipped` antes das correções), compileall, scan de fronteiras e revisão dos commits `1b36208..e781969`. Encontrou sete achados P1/P2; todos foram analisados e corrigidos com testes: fallback legacy tornou-se opt-in, no-op read-only não avança revisão, consistência eventual é rejeitada, leituras fortes configuram isolamento, outbox PostgreSQL ganhou bridge, inspection aplica ceiling, downgrade remove constraints na ordem correta e audit/outbox usam FK composta de ownership. Os gates foram repetidos após essas correções.
