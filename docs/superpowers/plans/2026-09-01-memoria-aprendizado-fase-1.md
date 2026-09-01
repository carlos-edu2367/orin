# Memória e aprendizado — Fase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o Orin gravar memórias sozinho e usar as que já tem, sem nenhuma chamada extra de modelo por turno.

**Architecture:** Um ledger mecânico observa o turno e deriva memórias de sinais estruturados (hoje: um comando que falhou e foi resolvido por outro). O store de memória ganha tipo, confiança, origem e supersessão. A injeção no prompt troca recência pura por relevância. E a instrução de `remember`, hoje um bullet órfão pendurado no bloco de subagentes, vira uma seção própria com gatilhos concretos.

**Tech Stack:** Python 3.13, SQLAlchemy Core + Alembic, pytest, React + TypeScript (Vite).

**Spec:** [docs/superpowers/specs/2026-09-01-memoria-e-aprendizado-design.md](../specs/2026-09-01-memoria-e-aprendizado-design.md)

## Global Constraints

- **Nenhuma ferramenta nova.** `tests/unit/agentic/test_phase_controller.py::test_every_phase_publishes_a_set_a_small_model_can_navigate` limita cada fase a 16 ferramentas com o contrato `{"files","terminal"}`, e ORIENT/EXECUTE estão exatamente em 16/16. `remember` e `recall` já existem e permanecem inalterados.
- **Nenhum caminho de aprendizado pode falhar um turno.** Todo ponto de integração no runtime é `try/except Exception` largo, no padrão de `_settle_quality` ([runtime.py:1369](../../../src/agentos/agentic/runtime.py)).
- **Fase 1 não faz nenhuma chamada de provedor.** Toda extração é mecânica. A reflexão é Fase 2.
- **Nada de embeddings.** A recuperação é léxica, reusando `_terms` de [agent_memory.py](../../../src/agentos/persistence/postgres/agent_memory.py).
- Comandos de teste: `uv run python -m pytest <caminho> -v`.
- `kind` ∈ `{"preference", "fact", "operational", "correction"}`; `source` ∈ `{"user_explicit", "reflection", "mechanical"}`; `confidence` é `float` em `[0.0, 1.0]`.

## Escopo mecânico da Fase 1 — leia antes de começar

O spec lista seis sinais. **A Fase 1 implementa exatamente um deles como memória automática:** `tool_failure_resolved` sobre `run_command`. Isso é deliberado, não um corte de escopo por preguiça:

- `run_command` carrega o comando como argumento estruturado, então "`npm install` falhou, `pnpm install` funcionou" pode virar texto sem nenhum julgamento de modelo.
- `verify_project` deriva o comando da receita detectada, então ele nunca muda entre duas chamadas no mesmo turno — não existe "resolução" para detectar ali.
- `report_verification` devolve `findings` em texto livre escrito pelo modelo. Transformar isso em memória exige um modelo que saiba resumir, e é por isso que ele é Fase 2.

A Fase 1 tem então **duas** fontes de memória nova: o detector mecânico acima, e o próprio `remember` finalmente sendo instruído direito (Task 6). A terceira perna — fazer a memória existente ser efetivamente usada — é a troca de `recent()` por `relevant()` (Task 4).

---

### Task 1: O ledger de aprendizado

**Files:**
- Create: `src/agentos/agentic/learning.py`
- Test: `tests/unit/agentic/test_learning_ledger.py`

**Interfaces:**
- Consumes: nada. Módulo folha, sem imports do projeto.
- Produces: `LearnedMemory` (dataclass congelada com `fact: str`, `kind: str`, `scope: str`, `confidence: float`, `source: str`, `tags: tuple[str, ...]`); `TurnLearningLedger` com `note_tool_outcome(name: str, arguments: Mapping[str, object], status: str) -> None` e `mechanical_memories(scope: str) -> tuple[LearnedMemory, ...]`.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/unit/agentic/test_learning_ledger.py`:

```python
from agentos.agentic.learning import LearnedMemory, TurnLearningLedger


def test_a_command_resolved_by_a_sibling_command_becomes_one_operational_memory():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": "npm install"}, "failed")
    ledger.note_tool_outcome("run_command", {"command": "pnpm install"}, "succeeded")

    memories = ledger.mechanical_memories("project")

    assert memories == (
        LearnedMemory(
            fact="Neste workspace, `pnpm install` funciona onde `npm install` falha.",
            kind="operational",
            scope="project",
            confidence=0.7,
            source="mechanical",
            tags=("comando",),
        ),
    )


def test_unrelated_commands_are_not_treated_as_a_resolution():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": "npm install"}, "failed")
    ledger.note_tool_outcome("run_command", {"command": "git status"}, "succeeded")

    assert ledger.mechanical_memories("project") == ()


def test_the_same_command_succeeding_later_is_a_retry_not_a_lesson():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": "npm test"}, "failed")
    ledger.note_tool_outcome("run_command", {"command": "npm test"}, "succeeded")

    assert ledger.mechanical_memories("project") == ()


def test_only_run_command_is_mined_in_this_phase():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("read_file", {"path": "a.txt"}, "failed")
    ledger.note_tool_outcome("read_file", {"path": "b.txt"}, "succeeded")

    assert ledger.mechanical_memories("project") == ()


def test_a_resolution_is_reported_once_even_if_the_command_runs_again():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": "npm install"}, "failed")
    ledger.note_tool_outcome("run_command", {"command": "pnpm install"}, "succeeded")
    ledger.note_tool_outcome("run_command", {"command": "pnpm install"}, "succeeded")

    assert len(ledger.mechanical_memories("project")) == 1


def test_malformed_arguments_never_raise():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": None}, "failed")
    ledger.note_tool_outcome("run_command", {}, "succeeded")
    ledger.note_tool_outcome("run_command", "not a mapping", "succeeded")  # type: ignore[arg-type]

    assert ledger.mechanical_memories("project") == ()
```

- [ ] **Step 2: Rode o teste e confirme que falha**

Run: `uv run python -m pytest tests/unit/agentic/test_learning_ledger.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.agentic.learning'`

- [ ] **Step 3: Escreva a implementação mínima**

Crie `src/agentos/agentic/learning.py`:

```python
"""What one turn taught, derived mechanically from what the turn actually did.

Sibling of ``quality.py`` and held to the same rule: this observes a turn, it
never influences one. A counter that cannot make sense of an argument still
counts the call, and a ledger that cannot make sense of a command simply
learns nothing from it.

Only ``run_command`` is mined here, and only for one shape: a command that
failed, followed by a *different* command that did the same job and worked.
That shape is worth storing because the argument is structured -- the command
string is the fact. Free-form evidence (a verification's findings, a user's
correction) needs a model to shape it into a sentence, which is a later phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# Two commands serve the same purpose when most of their words agree. The
# npm/pnpm case shares "install" and differs only in the runner, which is
# exactly the lesson worth keeping; "npm install" and "git status" share
# nothing and are two unrelated steps of the same turn.
_SAME_PURPOSE_OVERLAP = 0.5


@dataclass(frozen=True, slots=True)
class LearnedMemory:
    """One fact a turn produced, ready to be committed to the memory store."""

    fact: str
    kind: str
    scope: str
    confidence: float
    source: str
    tags: tuple[str, ...] = ()


def _command_of(arguments: object) -> str:
    if not isinstance(arguments, Mapping):
        return ""
    value = arguments.get("command")
    return " ".join(value.split()) if isinstance(value, str) else ""


def _words(command: str) -> frozenset[str]:
    return frozenset(part.lower() for part in command.split() if part)


def _same_purpose(failed: str, succeeded: str) -> bool:
    """Whether two commands are two attempts at one job, not two different jobs."""
    if failed == succeeded:
        return False
    left, right = _words(failed), _words(succeeded)
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= _SAME_PURPOSE_OVERLAP


@dataclass(slots=True)
class TurnLearningLedger:
    """Mutable tally for one turn. Never raises, never blocks the turn."""

    _failed_commands: list[str] = field(default_factory=list, repr=False)
    _resolutions: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def note_tool_outcome(self, name: str, arguments: Mapping[str, object], status: str) -> None:
        if name != "run_command":
            return
        command = _command_of(arguments)
        if not command:
            return
        if status == "failed":
            if command not in self._failed_commands:
                self._failed_commands.append(command)
            return
        if status != "succeeded":
            return
        for failed in self._failed_commands:
            if _same_purpose(failed, command) and (failed, command) not in self._resolutions:
                self._resolutions.append((failed, command))

    def mechanical_memories(self, scope: str) -> tuple[LearnedMemory, ...]:
        return tuple(
            LearnedMemory(
                fact=f"Neste workspace, `{succeeded}` funciona onde `{failed}` falha.",
                kind="operational",
                scope=scope,
                confidence=0.7,
                source="mechanical",
                tags=("comando",),
            )
            for failed, succeeded in self._resolutions
        )


__all__ = ["LearnedMemory", "TurnLearningLedger"]
```

- [ ] **Step 4: Rode os testes e confirme que passam**

Run: `uv run python -m pytest tests/unit/agentic/test_learning_ledger.py -v`
Expected: PASS, 6 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/learning.py tests/unit/agentic/test_learning_ledger.py
git commit -m "feat(agentic): derive operational memory from resolved commands"
```

---

### Task 2: Schema e migração

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py` (bloco `agent_memories`, ~linha 777)
- Create: `src/agentos/persistence/postgres/migrations/versions/0044_memory_learning.py`
- Test: `tests/unit/persistence/test_memory_learning_schema.py`

**Interfaces:**
- Consumes: nada da Task 1.
- Produces: colunas `kind`, `confidence`, `source`, `hit_count`, `last_used_at`, `superseded_by` em `agent_memories`; constraint única passa a ser `uq_agent_memories_scope_fact` sobre `(user_id, scope_type, project_id, fact)`.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/unit/persistence/test_memory_learning_schema.py`:

```python
from sqlalchemy import create_engine

from agentos.persistence.postgres.schema import agent_memories, metadata


def test_agent_memories_carries_the_learning_columns():
    names = set(agent_memories.c.keys())
    assert {"kind", "confidence", "source", "hit_count", "last_used_at", "superseded_by"} <= names


def test_the_same_fact_can_exist_in_two_different_projects():
    """The old constraint was (user_id, fact), which made this raise."""
    from datetime import UTC, datetime
    from sqlalchemy import insert, select

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime.now(UTC)
    row = {
        "user_id": "user:1", "scope_type": "project", "scope_id": "p", "fact": "o build é pnpm",
        "tags": [], "created_at": now, "updated_at": now, "kind": "operational",
        "confidence": 0.7, "source": "mechanical", "hit_count": 0,
    }
    with engine.begin() as connection:
        connection.execute(insert(agent_memories).values(memory_id="m1", project_id="project:a", **row))
        connection.execute(insert(agent_memories).values(memory_id="m2", project_id="project:b", **row))
    with engine.connect() as connection:
        assert len(connection.execute(select(agent_memories)).mappings().all()) == 2
```

- [ ] **Step 2: Rode o teste e confirme que falha**

Run: `uv run python -m pytest tests/unit/persistence/test_memory_learning_schema.py -v`
Expected: FAIL — o primeiro por `KeyError`/assert das colunas ausentes, o segundo por `IntegrityError: UNIQUE constraint failed`.

- [ ] **Step 3: Altere o schema**

Em `src/agentos/persistence/postgres/schema.py`, substitua o bloco `agent_memories` por:

```python
agent_memories = Table(
    "agent_memories", metadata,
    Column("memory_id", String(255), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("conversation_id", String(255), nullable=True),
    Column("scope_type", String(16), nullable=False, server_default="user"),
    Column("scope_id", String(255), nullable=False, server_default=""),
    Column("project_id", String(255), nullable=True),
    Column("source_message_id", String(255), nullable=True),
    Column("source_execution_id", String(255), nullable=True),
    Column("fact", String(2000), nullable=False),
    Column("tags", JSON, nullable=False, default=list),
    # What the fact is, which decides how it enters the prompt and what it can
    # contradict. A preference outranks an operational note for prompt space.
    Column("kind", String(16), nullable=False, server_default="fact"),
    Column("confidence", Float, nullable=False, server_default="1.0"),
    # Who produced it. Without this, a wrong memory is untraceable.
    Column("source", String(16), nullable=False, server_default="user_explicit"),
    Column("hit_count", Integer, nullable=False, server_default="0"),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    # A contradiction chains instead of deleting, so the history stays auditable.
    Column("superseded_by", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    # Scope belongs in the key: 0028 added project scope but left the old
    # (user_id, fact) constraint in place, so the same fact in two projects
    # raised IntegrityError.
    UniqueConstraint("user_id", "scope_type", "project_id", "fact", name="uq_agent_memories_scope_fact"),
)
Index("ix_agent_memories_user_updated", agent_memories.c.user_id, agent_memories.c.updated_at)
```

Confirme que `Float` e `Integer` estão no import do SQLAlchemy no topo do arquivo; adicione o que faltar.

- [ ] **Step 4: Escreva a migração**

Crie `src/agentos/persistence/postgres/migrations/versions/0044_memory_learning.py`:

```python
"""typed agent memory with provenance and supersession

Revision ID: 0044_memory_learning
Revises: 0043_code_mode
"""
from alembic import op
import sqlalchemy as sa


revision = "0044_memory_learning"
down_revision = "0043_code_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_memories", sa.Column("kind", sa.String(16), nullable=False, server_default="fact"))
    op.add_column("agent_memories", sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("agent_memories", sa.Column("source", sa.String(16), nullable=False, server_default="user_explicit"))
    op.add_column("agent_memories", sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_memories", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_memories", sa.Column("superseded_by", sa.String(255), nullable=True))
    # Every row that predates this migration was written by the model calling
    # `remember` on the user's behalf, which is exactly user_explicit/fact.
    with op.batch_alter_table("agent_memories") as batch:
        batch.drop_constraint("uq_agent_memories_user_fact", type_="unique")
        batch.create_unique_constraint(
            "uq_agent_memories_scope_fact", ["user_id", "scope_type", "project_id", "fact"]
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_memories") as batch:
        batch.drop_constraint("uq_agent_memories_scope_fact", type_="unique")
        batch.create_unique_constraint("uq_agent_memories_user_fact", ["user_id", "fact"])
    op.drop_column("agent_memories", "superseded_by")
    op.drop_column("agent_memories", "last_used_at")
    op.drop_column("agent_memories", "hit_count")
    op.drop_column("agent_memories", "source")
    op.drop_column("agent_memories", "confidence")
    op.drop_column("agent_memories", "kind")
```

- [ ] **Step 5: Rode os testes e confirme que passam**

Run: `uv run python -m pytest tests/unit/persistence/ -v`
Expected: PASS, incluindo os dois testes novos e todos os já existentes.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/persistence/postgres/schema.py src/agentos/persistence/postgres/migrations/versions/0044_memory_learning.py tests/unit/persistence/test_memory_learning_schema.py
git commit -m "feat(persistence): type agent memory and scope its uniqueness"
```

---

### Task 3: `save()` tipado, supersessão e `relevant()`

**Files:**
- Modify: `src/agentos/persistence/postgres/agent_memory.py`
- Test: `tests/unit/persistence/test_agent_memory_store.py` (criar)

**Interfaces:**
- Consumes: as colunas da Task 2.
- Produces: `PostgresAgentMemoryStore.save(fact, tags=(), *, kind="fact", confidence=1.0, source="user_explicit") -> dict` (retorna `{"memory_id", "fact", "created", "superseded"}`); `PostgresAgentMemoryStore.relevant(task: str, *, limit: int = 12) -> list[dict]`. `recent()` permanece, ainda usado por nada no caminho principal depois da Task 4, mas mantido para compatibilidade dos testes existentes.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/unit/persistence/test_agent_memory_store.py`:

```python
from sqlalchemy import create_engine

from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import metadata


def _store(**kwargs):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return PostgresAgentMemoryStore(engine, "user:1", **kwargs)


def test_save_records_the_kind_confidence_and_source():
    store = _store()
    store.save("o build é pnpm", ("comando",), kind="operational", confidence=0.7, source="mechanical")

    row = store.recent(limit=1)[0]
    assert (row["kind"], row["confidence"], row["source"]) == ("operational", 0.7, "mechanical")


def test_a_contradicting_fact_supersedes_the_older_one_instead_of_deleting_it():
    store = _store()
    first = store.save("o gerenciador de pacotes é npm", kind="operational")
    second = store.save("o gerenciador de pacotes é pnpm", kind="operational")

    assert second["superseded"] == [first["memory_id"]]
    assert [row["fact"] for row in store.relevant("gerenciador de pacotes")] == [
        "o gerenciador de pacotes é pnpm"
    ]


def test_a_different_kind_does_not_supersede():
    store = _store()
    store.save("o gerenciador de pacotes é npm", kind="operational")
    second = store.save("o gerenciador de pacotes é npm", kind="preference")

    assert second["superseded"] == []


def test_relevant_always_reserves_room_for_the_strongest_preferences():
    store = _store()
    store.save("prefiro respostas curtas", kind="preference", confidence=0.9)
    for index in range(20):
        store.save(f"o arquivo {index} trata de faturamento", kind="fact")

    facts = [row["fact"] for row in store.relevant("faturamento", limit=12)]

    assert "prefiro respostas curtas" in facts
    assert len(facts) == 12


def test_relevant_ranks_by_the_task_not_by_recency():
    store = _store()
    store.save("o deploy usa fly.io", kind="fact")
    for index in range(15):
        store.save(f"detalhe irrelevante {index}", kind="fact")

    assert "o deploy usa fly.io" in [row["fact"] for row in store.relevant("como fazer o deploy", limit=12)]


def test_relevant_prefers_project_scope_over_global_on_a_tie():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    PostgresAgentMemoryStore(engine, "user:1").save("o build usa make", kind="operational")
    scoped = PostgresAgentMemoryStore(engine, "user:1", project_id="project:a")
    scoped.save("o build usa make aqui", kind="operational")

    assert scoped.relevant("build", limit=1)[0]["fact"] == "o build usa make aqui"
```

- [ ] **Step 2: Rode o teste e confirme que falha**

Run: `uv run python -m pytest tests/unit/persistence/test_agent_memory_store.py -v`
Expected: FAIL com `TypeError: save() got an unexpected keyword argument 'kind'`.

- [ ] **Step 3: Implemente**

Em `src/agentos/persistence/postgres/agent_memory.py`, substitua `save` e acrescente `_supersede` e `relevant`. Mantenha `_terms`, `_WORD`, `_STOPWORDS`, `search`, `recent`, `forget`, `_all` e `_public` como estão, exceto `_public`, que ganha os campos novos:

```python
# How much of two facts' vocabulary must agree before the newer one is taken
# to replace the older. High on purpose: "prefiro npm" and "prefiro pnpm"
# must collide, while two unrelated notes about the same project must not.
_CONTRADICTION_OVERLAP = 0.6


    def save(
        self,
        fact: str,
        tags: tuple[str, ...] = (),
        *,
        kind: str = "fact",
        confidence: float = 1.0,
        source: str = "user_explicit",
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        normalized = " ".join(fact.split())
        scope_predicate = (
            agent_memories.c.project_id == self._project_id
            if self._project_id
            else agent_memories.c.project_id.is_(None)
        )
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(agent_memories.c.memory_id).where(
                    agent_memories.c.user_id == self._user_id,
                    agent_memories.c.fact == normalized,
                    agent_memories.c.scope_type == self._scope_type,
                    scope_predicate,
                )
            ).scalar()
            if existing is not None:
                connection.execute(update(agent_memories).where(agent_memories.c.memory_id == existing).values(
                    updated_at=now, tags=list(tags), kind=kind, confidence=confidence, source=source,
                ))
                return {"memory_id": existing, "fact": normalized, "created": False, "superseded": []}
            memory_id = f"mem_{uuid4().hex}"
            superseded = self._supersede(connection, normalized, kind, memory_id, now)
            connection.execute(insert(agent_memories).values(
                memory_id=memory_id, user_id=self._user_id, conversation_id=self._conversation_id,
                scope_type=self._scope_type, scope_id=self._scope_id, project_id=self._project_id,
                source_message_id=None, source_execution_id=self._execution_id,
                fact=normalized, tags=list(tags), kind=kind, confidence=confidence, source=source,
                hit_count=0, last_used_at=None, superseded_by=None,
                created_at=now, updated_at=now,
            ))
        return {"memory_id": memory_id, "fact": normalized, "created": True, "superseded": superseded}

    def _supersede(self, connection, fact: str, kind: str, replacement: str, now: datetime) -> list[str]:
        """Chain a contradicted memory to its replacement rather than deleting it.

        Two facts contradict when they are the same kind, in the same scope,
        and share most of their vocabulary: "o gerenciador é npm" against "o
        gerenciador é pnpm". Keeping both would put two mutually exclusive
        truths in the same prompt.
        """
        wanted = _terms(fact)
        if not wanted:
            return []
        scope_predicate = (
            agent_memories.c.project_id == self._project_id
            if self._project_id
            else agent_memories.c.project_id.is_(None)
        )
        rows = connection.execute(select(agent_memories).where(
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.scope_type == self._scope_type,
            agent_memories.c.kind == kind,
            agent_memories.c.superseded_by.is_(None),
            scope_predicate,
        )).mappings().all()
        replaced: list[str] = []
        for row in rows:
            other = _terms(str(row["fact"]))
            if not other:
                continue
            if len(wanted & other) / len(wanted | other) >= _CONTRADICTION_OVERLAP:
                replaced.append(str(row["memory_id"]))
        if replaced:
            connection.execute(update(agent_memories).where(
                agent_memories.c.memory_id.in_(replaced)
            ).values(superseded_by=replacement, updated_at=now))
        return replaced

    def relevant(self, task: str, *, limit: int = 12) -> list[dict[str, object]]:
        """Memories worth spending prompt space on for *this* task.

        Recency alone made the thirteenth memory permanently invisible and
        chose nothing for being useful. The budget is split instead: a fixed
        share for the strongest standing preferences, which apply to every
        turn regardless of subject, and the rest by how well a memory matches
        the task at hand. Project scope breaks a tie against global scope,
        because the more specific fact is the one that was learned here.
        """
        preference_slots = min(4, limit)
        rows = [row for row in self._all() if row["superseded_by"] is None]
        preferences = sorted(
            (row for row in rows if row["kind"] == "preference"),
            key=lambda row: (float(row["confidence"] or 0.0), row["updated_at"]),
            reverse=True,
        )[:preference_slots]
        chosen = {str(row["memory_id"]): row for row in preferences}

        wanted = _terms(task)

        def score(row) -> tuple[int, int, float]:
            haystack = _terms(str(row["fact"])) | {str(tag).lower() for tag in (row["tags"] or [])}
            return (
                len(wanted & haystack),
                1 if row["scope_type"] == "project" else 0,
                float(row["confidence"] or 0.0),
            )

        for row in sorted(rows, key=score, reverse=True):
            if len(chosen) >= limit:
                break
            chosen.setdefault(str(row["memory_id"]), row)
        return [self._public(row) for row in chosen.values()]
```

E estenda `_public`:

```python
    @staticmethod
    def _public(row: dict[str, object]) -> dict[str, object]:
        return {
            "memory_id": row["memory_id"],
            "fact": row["fact"],
            "tags": list(row["tags"] or []),
            "kind": row["kind"],
            "confidence": float(row["confidence"] or 0.0),
            "source": row["source"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
```

Por fim, filtre memórias superadas de `search` e `recent`: em `_all`, acrescente `agent_memories.c.superseded_by.is_(None)` à lista `predicate`.

**Sobre `hit_count` e `last_used_at`:** as colunas existem a partir da Task 2, mas **a Fase 1 não as escreve** e `relevant()` não as lê. Incrementá-las tornaria `relevant()` uma escrita no caminho de leitura, uma vez por turno, para alimentar uma política que só existe na Fase 3. A consequência aceita e explícita: quando a Fase 3 começar, ela parte de zero histórico e precisa acumular seu próprio antes de poder decair qualquer coisa. Não trate isso como bug.

- [ ] **Step 4: Rode os testes e confirme que passam**

Run: `uv run python -m pytest tests/unit/persistence/ tests/unit/projects/test_project_memory.py -v`
Expected: PASS. Se `test_project_memory.py` quebrar por causa das colunas novas em `_public`, ajuste as asserções desse arquivo para conferirem os campos que importam ao invés do dicionário inteiro.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/persistence/postgres/agent_memory.py tests/unit/persistence/test_agent_memory_store.py
git commit -m "feat(memory): supersede contradictions and retrieve by relevance"
```

---

### Task 4: A recuperação relevante no prompt do turno

**Files:**
- Modify: `src/agentos/agentic/session.py:1046`
- Test: `tests/unit/agentic/test_session_memory_retrieval.py` (criar)

**Interfaces:**
- Consumes: `PostgresAgentMemoryStore.relevant(task, limit=12)` da Task 3.
- Produces: nada de novo; `build_system_prompt` continua recebendo `memories: list[dict[str, object]]`.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/unit/agentic/test_session_memory_retrieval.py`:

```python
class _Memory:
    def __init__(self):
        self.calls = []

    def relevant(self, task, *, limit=12):
        self.calls.append(("relevant", task, limit))
        return [{"fact": "prefiro respostas curtas"}]

    def recent(self, *, limit=12):
        self.calls.append(("recent", limit))
        return []


def test_the_turn_asks_for_memories_relevant_to_the_task_not_the_most_recent():
    from agentos.agentic.session import memories_for_task

    memory = _Memory()
    result = memories_for_task(memory, "como faço o deploy?")

    assert memory.calls == [("relevant", "como faço o deploy?", 12)]
    assert result == [{"fact": "prefiro respostas curtas"}]


def test_a_store_without_relevance_still_works():
    """An in-memory double from an older test must not break the turn."""
    from agentos.agentic.session import memories_for_task

    class _Old:
        def recent(self, *, limit=12):
            return [{"fact": "algo antigo"}]

    assert memories_for_task(_Old(), "qualquer coisa") == [{"fact": "algo antigo"}]


def test_no_memory_store_yields_no_memories():
    from agentos.agentic.session import memories_for_task

    assert memories_for_task(None, "qualquer coisa") == []


def test_a_failing_store_never_breaks_the_turn():
    from agentos.agentic.session import memories_for_task

    class _Broken:
        def relevant(self, task, *, limit=12):
            raise RuntimeError("database is gone")

    assert memories_for_task(_Broken(), "qualquer coisa") == []
```

- [ ] **Step 2: Rode o teste e confirme que falha**

Run: `uv run python -m pytest tests/unit/agentic/test_session_memory_retrieval.py -v`
Expected: FAIL com `ImportError: cannot import name 'memories_for_task'`.

- [ ] **Step 3: Implemente**

Em `src/agentos/agentic/session.py`, ao lado das outras funções de módulo (antes de `build_system_prompt`), acrescente:

```python
def memories_for_task(memory_store: object | None, task: str) -> list[dict[str, object]]:
    """The memories worth putting in this turn's prompt.

    Relevance when the store can rank, recency when it cannot, and nothing at
    all when it fails: a prompt enrichment must never be the reason a turn
    cannot start.
    """
    if memory_store is None:
        return []
    try:
        ranker = getattr(memory_store, "relevant", None)
        if callable(ranker):
            return list(ranker(task, limit=12))
        return list(memory_store.recent(limit=12))
    except Exception:  # noqa: BLE001 - a prompt enrichment never breaks a turn
        return []
```

Em `build_runtime`, a linha 1046 é hoje:

```python
        memories = self.memory.recent(limit=12) if self.memory is not None else []
```

`task` só é calculada mais abaixo (a partir de `history`). Mova a atribuição de `memories` para **depois** da linha que define `task` e troque por:

```python
        memories = memories_for_task(self.memory, task)
```

- [ ] **Step 4: Rode os testes e confirme que passam**

Run: `uv run python -m pytest tests/unit/agentic/test_session_memory_retrieval.py tests/unit/agentic/test_layered_prompt.py tests/unit/agentic/test_prefix_stability.py -v`
Expected: PASS. `test_prefix_stability` importa: as memórias vivem no bloco volátil, então trocar a seleção não pode mexer no prefixo cacheado.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/session.py tests/unit/agentic/test_session_memory_retrieval.py
git commit -m "feat(agentic): select turn memories by relevance instead of recency"
```

---

### Task 5: O ledger ligado ao runtime

**Files:**
- Modify: `src/agentos/agentic/runtime.py` (~linha 196, ~linha 1167, ~linha 1369)
- Test: `tests/unit/agentic/test_runtime_learning.py` (criar)

**Interfaces:**
- Consumes: `TurnLearningLedger` da Task 1.
- Produces: parâmetro `learning_sink: Callable[[tuple[LearnedMemory, ...]], None] | None = None` no construtor de `AgenticTurnRuntime`; atributo `self.ledger`.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/unit/agentic/test_runtime_learning.py`:

```python
from agentos.agentic.learning import LearnedMemory, TurnLearningLedger


def test_the_sink_receives_what_the_turn_learned():
    from agentos.agentic.runtime import AgenticTurnRuntime

    received: list[tuple[LearnedMemory, ...]] = []
    runtime = object.__new__(AgenticTurnRuntime)
    runtime.ledger = TurnLearningLedger()
    runtime._learning_sink = received.append
    runtime.ledger.note_tool_outcome("run_command", {"command": "npm install"}, "failed")
    runtime.ledger.note_tool_outcome("run_command", {"command": "pnpm install"}, "succeeded")

    AgenticTurnRuntime._commit_learning(runtime, "project")

    assert len(received) == 1
    assert received[0][0].kind == "operational"


def test_committing_twice_only_learns_once():
    from agentos.agentic.runtime import AgenticTurnRuntime

    received: list[tuple[LearnedMemory, ...]] = []
    runtime = object.__new__(AgenticTurnRuntime)
    runtime.ledger = TurnLearningLedger()
    runtime._learning_sink = received.append
    runtime.ledger.note_tool_outcome("run_command", {"command": "npm ci"}, "failed")
    runtime.ledger.note_tool_outcome("run_command", {"command": "pnpm ci"}, "succeeded")

    AgenticTurnRuntime._commit_learning(runtime, "project")
    AgenticTurnRuntime._commit_learning(runtime, "project")

    assert len(received) == 1


def test_a_sink_that_raises_never_escapes():
    from agentos.agentic.runtime import AgenticTurnRuntime

    def explode(_):
        raise RuntimeError("the database is gone")

    runtime = object.__new__(AgenticTurnRuntime)
    runtime.ledger = TurnLearningLedger()
    runtime._learning_sink = explode
    runtime.ledger.note_tool_outcome("run_command", {"command": "npm i"}, "failed")
    runtime.ledger.note_tool_outcome("run_command", {"command": "pnpm i"}, "succeeded")

    AgenticTurnRuntime._commit_learning(runtime, "project")  # must not raise


def test_a_turn_with_no_sink_is_a_no_op():
    from agentos.agentic.runtime import AgenticTurnRuntime

    runtime = object.__new__(AgenticTurnRuntime)
    runtime.ledger = TurnLearningLedger()
    runtime._learning_sink = None

    AgenticTurnRuntime._commit_learning(runtime, "user")  # must not raise
```

- [ ] **Step 2: Rode o teste e confirme que falha**

Run: `uv run python -m pytest tests/unit/agentic/test_runtime_learning.py -v`
Expected: FAIL com `AttributeError: type object 'AgenticTurnRuntime' has no attribute '_commit_learning'`.

- [ ] **Step 3: Implemente**

Em `src/agentos/agentic/runtime.py`:

1. No topo, junto do import de `quality`, acrescente:

```python
from .learning import LearnedMemory, TurnLearningLedger
```

2. No `__init__`, aceite o parâmetro novo (acrescente-o ao final da assinatura, com default, para não quebrar chamador nenhum) e inicialize junto de `self.counters` (~linha 196):

```python
        self.counters = TurnQualityCounters()
        self.ledger = TurnLearningLedger()
        self._learning_sink = learning_sink
        self._learning_committed = False
```

3. Em `_execute_calls`, imediatamente **depois** de `self.counters.note_call(name, arguments, outcome.status)` (~linha 1167), acrescente:

```python
            try:
                self.ledger.note_tool_outcome(name, arguments, outcome.status)
            except Exception:  # noqa: BLE001 - observation never breaks a turn
                pass
```

4. Acrescente o método, ao lado de `_settle_quality`:

```python
    def _commit_learning(self, scope: str) -> None:
        """Hand what this turn taught to whoever knows how to store it.

        Runs once, after the answer has already been delivered, and only for
        a turn that actually reached a terminal state. The runtime deliberately
        does not know what a memory store is: the sink is supplied by the
        session, which owns that dependency.
        """
        if self._learning_committed or self._learning_sink is None:
            return
        self._learning_committed = True
        try:
            learned = self.ledger.mechanical_memories(scope)
            if learned:
                self._learning_sink(learned)
        except Exception:  # noqa: BLE001 - learning never breaks a turn
            pass
```

5. Em `_settle_quality`, logo depois de `self._quality_recorded = True`, acrescente:

```python
        if outcome in {"completed", "completed_with_caveats"}:
            self._commit_learning("project" if turn.get("project_id") else "user")
```

- [ ] **Step 4: Rode os testes e confirme que passam**

Run: `uv run python -m pytest tests/unit/agentic/test_runtime_learning.py tests/unit/agentic/test_agentic_runtime_loop.py tests/unit/agentic/test_phase_runtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py tests/unit/agentic/test_runtime_learning.py
git commit -m "feat(agentic): record what a turn taught and hand it to a sink"
```

---

### Task 6: O sink, o card e a seção `## Memória`

**Files:**
- Modify: `src/agentos/agentic/events.py` (enum `AgentActivityEventType`)
- Modify: `src/agentos/agentic/session.py` (`build_system_prompt` ~linha 323; `build_runtime` ~linha 1129)
- Test: `tests/unit/agentic/test_learning_sink.py` (criar)
- Test: `tests/unit/agentic/test_layered_prompt.py` (estender)

**Interfaces:**
- Consumes: `learning_sink` da Task 5; `save(..., kind=, confidence=, source=)` da Task 3; `LearnedMemory` da Task 1.
- Produces: `AgentActivityEventType.MEMORY_LEARNED = "memory.learned"`, com payload `{"memory_id": str, "fact": str, "kind": str, "scope": str, "project_id": str | None, "source": str}`; `learning_sink_for(memory_store, record, project_id) -> Callable | None`.

Nenhuma dessas chaves cai na lista de redação de `sanitize_public_mapping` ([models.py](../../../src/agentos/agentic/models.py)), então todas chegam íntegras ao frontend. `scope` e `project_id` viajam porque o botão **Desfazer** da Task 8 precisa dos dois para chamar `DELETE /v1/memories/{id}`.

- [ ] **Step 1: Escreva os testes que falham**

Crie `tests/unit/agentic/test_learning_sink.py`:

```python
from agentos.agentic.learning import LearnedMemory


class _Memory:
    def __init__(self):
        self.saved = []

    def save(self, fact, tags=(), *, kind="fact", confidence=1.0, source="user_explicit"):
        self.saved.append((fact, kind, confidence, source))
        return {"memory_id": f"mem_{len(self.saved)}", "fact": fact, "created": True, "superseded": []}


def _learned(fact="o build é pnpm"):
    return LearnedMemory(fact=fact, kind="operational", scope="project", confidence=0.7, source="mechanical", tags=("comando",))


def test_the_sink_stores_the_memory_and_announces_it():
    from agentos.agentic.session import learning_sink_for

    memory, recorded = _Memory(), []
    sink = learning_sink_for(memory, lambda event_type, summary, payload: recorded.append((event_type, summary, payload)), "project:a")
    sink((_learned(),))

    assert memory.saved == [("o build é pnpm", "operational", 0.7, "mechanical")]
    assert recorded[0][0].value == "memory.learned"
    assert recorded[0][2] == {
        "memory_id": "mem_1", "fact": "o build é pnpm", "kind": "operational",
        "scope": "project", "project_id": "project:a", "source": "mechanical",
    }


def test_a_memory_that_was_already_known_is_not_announced_again():
    from agentos.agentic.session import learning_sink_for

    class _Known(_Memory):
        def save(self, fact, tags=(), *, kind="fact", confidence=1.0, source="user_explicit"):
            super().save(fact, tags, kind=kind, confidence=confidence, source=source)
            return {"memory_id": "mem_old", "fact": fact, "created": False, "superseded": []}

    recorded = []
    learning_sink_for(_Known(), lambda *args: recorded.append(args), None)((_learned(),))

    assert recorded == []


def test_a_store_that_raises_never_escapes_the_sink():
    from agentos.agentic.session import learning_sink_for

    class _Broken:
        def save(self, *args, **kwargs):
            raise RuntimeError("the database is gone")

    learning_sink_for(_Broken(), lambda *args: None, None)((_learned(),))  # must not raise


def test_no_memory_store_makes_the_sink_a_no_op():
    from agentos.agentic.session import learning_sink_for

    assert learning_sink_for(None, lambda *args: None, None) is None
```

E acrescente a `tests/unit/agentic/test_layered_prompt.py`:

```python
def test_the_memory_instruction_is_its_own_section_with_concrete_triggers():
    """It used to be a lone bullet appended after the Subagents block, which
    read as an instruction about subagents and never fired."""
    from agentos.agentic.session import build_system_prompt

    stable, _ = build_system_prompt(
        tool_names=("remember", "recall"),
        memories=[], agents=[], workspace_hint="", subagents_enabled=False,
    )

    assert "## Memória" in stable
    section = stable.split("## Memória", 1)[1]
    assert "corrige" in section
    assert "convenção" in section
```

- [ ] **Step 2: Rode os testes e confirme que falham**

Run: `uv run python -m pytest tests/unit/agentic/test_learning_sink.py tests/unit/agentic/test_layered_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name 'learning_sink_for'` e o assert de `## Memória`.

- [ ] **Step 3: Acrescente o tipo de evento**

Em `src/agentos/agentic/events.py`, dentro de `AgentActivityEventType`, logo depois de `AGENT_CREATED`:

```python
    MEMORY_LEARNED = "memory.learned"
```

- [ ] **Step 4: Substitua o bullet órfão pela seção**

Em `src/agentos/agentic/session.py`, remova estas duas linhas de `build_system_prompt` (~linha 322):

```python
    if "remember" in tool_names:
        lines += ["- Use `remember` when the user states a durable preference or fact worth keeping; do not store transient chatter."]
```

E ponha, no lugar, um bloco com cabeçalho próprio:

```python
    if "remember" in tool_names:
        lines += [
            "",
            "## Memória",
            "- Guarde com `remember` no momento em que aprende, não no fim: uma memória é o que continua verdadeiro depois que esta conversa acabar.",
            "- Guarde quando a pessoa corrige você (\"não, é assim\", \"prefiro X\"), quando ela declara como quer trabalhar, e quando você descobre uma convenção do projeto que não estava escrita em lugar nenhum.",
            "- Guarde também o que custou caro descobrir: um comando que só funciona de um jeito, uma armadilha que já te derrubou aqui.",
            "- Escreva a memória como uma frase completa e autoexplicativa, que faça sentido daqui a um mês sem esta conversa junto.",
            "- Não guarde o que já está no código ou nos arquivos do workspace, nem o que só vale para esta tarefa.",
            "- Use `recall` quando desconfiar que já tratou disso antes e o que está acima não bastar.",
        ]
```

- [ ] **Step 5: Escreva o sink**

Em `src/agentos/agentic/session.py`, ao lado de `memories_for_task`:

```python
def learning_sink_for(memory_store, record, project_id: str | None):
    """Turn what a turn learned into stored memory and one visible card.

    The runtime produces ``LearnedMemory`` values and knows nothing about
    storage; this closure owns both the store and the activity recorder. A
    memory that was already known is stored again (which refreshes it) but is
    not announced: the card exists to tell the person something new happened.

    ``scope`` and ``project_id`` travel on the payload because the card's undo
    button addresses the same memory through the public API, and that route
    needs both to find the row.
    """
    if memory_store is None:
        return None

    def sink(learned) -> None:
        for item in learned:
            try:
                receipt = memory_store.save(
                    item.fact, item.tags, kind=item.kind, confidence=item.confidence, source=item.source,
                )
            except Exception:  # noqa: BLE001 - learning never breaks a turn
                continue
            if not receipt.get("created"):
                continue
            try:
                record(
                    AgentActivityEventType.MEMORY_LEARNED,
                    f"Aprendi: {item.fact[:120]}",
                    {
                        "memory_id": receipt["memory_id"], "fact": item.fact, "kind": item.kind,
                        "scope": item.scope, "project_id": project_id, "source": item.source,
                    },
                )
            except Exception:  # noqa: BLE001 - the card never breaks a turn
                pass

    return sink
```

- [ ] **Step 6: Ligue o sink ao runtime principal**

Em `build_runtime` (~linha 1129), acrescente o argumento à construção de `AgenticTurnRuntime`:

```python
            phase_controller=phase_controller,
            learning_sink=learning_sink_for(
                self.memory, self._record,
                str(self.turn["project_id"]) if self.turn.get("project_id") else None,
            ),
```

Deixe a construção do subagente (~linha 798) **sem** o argumento: a trajetória de um subagente não é a da conversa, exatamente como já acontece com `record_step`.

- [ ] **Step 7: Rode os testes e confirme que passam**

Run: `uv run python -m pytest tests/unit/agentic/ -v`
Expected: PASS, incluindo `test_activity_contracts.py` (o contrato de eventos é fechado; `MEMORY_LEARNED` agora é um tipo válido) e `test_phase_controller.py` (nenhuma ferramenta nova).

- [ ] **Step 8: Commit**

```bash
git add src/agentos/agentic/events.py src/agentos/agentic/session.py tests/unit/agentic/test_learning_sink.py tests/unit/agentic/test_layered_prompt.py
git commit -m "feat(agentic): store what a turn learned and show it to the person"
```

---

### Task 7: Editar uma memória (API)

**Files:**
- Modify: `src/agentos/projects/store.py` (ao lado de `delete_memory`, ~linha 161)
- Modify: `src/agentos/api/gateway.py` (ao lado de `delete_managed_memory`, ~linha 723)
- Test: `tests/unit/projects/test_project_memory.py` (estender)

**Interfaces:**
- Consumes: as colunas da Task 2.
- Produces: `PostgresProjectStore.update_memory(project_id, user_id, memory_id, fact, *, scope="project") -> dict | None`; rota `PATCH /v1/memories/{memory_id}` aceitando `{"fact": str}` e devolvendo a memória atualizada.

- [ ] **Step 1: Escreva o teste que falha**

Acrescente a `tests/unit/projects/test_project_memory.py` (use o mesmo helper de engine que o arquivo já usa para os outros testes):

```python
def test_updating_a_memory_rewrites_its_fact_and_returns_it():
    from datetime import UTC, datetime
    from sqlalchemy import create_engine, insert
    from agentos.persistence.postgres.schema import agent_memories, metadata
    from agentos.projects.store import PostgresProjectStore

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(agent_memories).values(
            memory_id="mem_1", user_id="user:1", conversation_id=None, scope_type="user",
            scope_id="user:1", project_id=None, source_message_id=None, source_execution_id=None,
            fact="o build é npm", tags=[], kind="operational", confidence=0.7, source="mechanical",
            hit_count=0, last_used_at=None, superseded_by=None, created_at=now, updated_at=now,
        ))
    store = PostgresProjectStore(engine)

    updated = store.update_memory(None, "user:1", "mem_1", "o build é pnpm", scope="user")

    assert updated["fact"] == "o build é pnpm"
    assert updated["memory_id"] == "mem_1"


def test_updating_a_memory_that_is_not_yours_returns_nothing():
    from sqlalchemy import create_engine
    from agentos.persistence.postgres.schema import metadata
    from agentos.projects.store import PostgresProjectStore

    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    assert PostgresProjectStore(engine).update_memory(None, "user:1", "mem_nope", "x", scope="user") is None
```

- [ ] **Step 2: Rode o teste e confirme que falha**

Run: `uv run python -m pytest tests/unit/projects/test_project_memory.py -v`
Expected: FAIL com `AttributeError: 'PostgresProjectStore' object has no attribute 'update_memory'`.

- [ ] **Step 3: Implemente no store**

Em `src/agentos/projects/store.py`, logo depois de `delete_memory`:

```python
    def update_memory(self, project_id: str | None, user_id: str, memory_id: str, fact: str, *, scope: str = "project") -> dict[str, object] | None:
        """Let the person rewrite a fact the agent got slightly wrong.

        Editing keeps the row's provenance: a memory the agent learned stays
        marked as such even after a human fixed its wording. What changes is
        the text and, because a person vouched for it now, its confidence.
        """
        if scope not in {"user", "project"}:
            raise ValueError("unsupported memory scope")
        if scope == "project" and (not project_id or self.get(project_id, user_id) is None):
            return None
        normalized = " ".join(str(fact).split())[:2000]
        if not normalized:
            raise ValueError("fact must be a non-blank string")
        predicates = [
            agent_memories.c.memory_id == memory_id,
            agent_memories.c.user_id == user_id,
            agent_memories.c.scope_type == scope,
            agent_memories.c.project_id == project_id if scope == "project" else agent_memories.c.project_id.is_(None),
        ]
        with self._engine.begin() as connection:
            result = connection.execute(update(agent_memories).where(*predicates).values(
                fact=normalized, confidence=1.0, updated_at=datetime.now(UTC),
            ))
            if not result.rowcount:
                return None
            row = connection.execute(select(agent_memories).where(agent_memories.c.memory_id == memory_id)).mappings().one()
        return {
            "memory_id": row["memory_id"], "fact": row["fact"], "tags": list(row["tags"] or []),
            "scope": scope, "project_id": row["project_id"], "conversation_id": row["conversation_id"],
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
        }
```

Confirme que `update`, `select`, `UTC` e `datetime` estão importados no topo do arquivo; acrescente o que faltar.

- [ ] **Step 4: Acrescente a rota**

Em `src/agentos/api/gateway.py`, logo depois de `delete_managed_memory`:

```python
    @app.patch("/v1/memories/{memory_id}")
    async def update_managed_memory(memory_id: str, request: Request, scope: str = "user", project_id: str | None = None) -> JSONResponse:
        if scope not in {"user", "project"}:
            raise ApplicationValidationError("invalid memory scope")
        principal = _principal(request)
        services.security.authorize(principal, action="memory.update", resource_id=project_id, purpose="memory.write")
        body = await request.json()
        fact = body.get("fact") if isinstance(body, dict) else None
        if not isinstance(fact, str) or not fact.strip():
            raise ApplicationValidationError("fact must be a non-blank string")
        updated = _require_port(services.projects).update_memory(project_id, principal.user_id, memory_id, fact, scope=scope)
        if updated is None:
            raise ApplicationNotFoundError(memory_id)
        return JSONResponse(updated)
```

Copie o padrão exato de `_principal`, `authorize` e `_require_port` das rotas vizinhas `list_managed_memories`/`delete_managed_memory` — se a assinatura delas divergir do que está acima, a versão do arquivo é a correta.

- [ ] **Step 5: Rode os testes e confirme que passam**

Run: `uv run python -m pytest tests/unit/projects/ tests/unit/api/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/projects/store.py src/agentos/api/gateway.py tests/unit/projects/test_project_memory.py
git commit -m "feat(api): let a person correct a memory instead of only deleting it"
```

---

### Task 8: O card na conversa e a edição na página Memory

**Files:**
- Modify: `frontend/src/features/conversations/activityTypes.ts` (tipo `ConversationActivityEvent` ~linha 28; `stateFor` ~linha 158)
- Modify: `frontend/src/api/conversations.ts` (~linha 281, junto dos outros `assign`)
- Create: `frontend/src/features/conversations/MemoryLearnedCard.tsx`
- Modify: `frontend/src/features/conversations/ActivityStream.tsx` (`renderActivityGroup` ~linha 85)
- Modify: `frontend/src/api/memory.ts`
- Modify: `frontend/src/features/memory/MemoryPage.tsx`

**Interfaces:**
- Consumes: o evento `memory.learned` da Task 6 e a rota `PATCH /v1/memories/{id}` da Task 7.
- Produces: campos `memoryId`, `memoryScope`, `memoryProjectId` em `ConversationActivityEvent`; `updateManagedMemory(client, memoryId, fact, scope, projectId?)`.

**Nota sobre a estrutura:** o evento não carrega um `payload` genérico até o frontend — `conversations.ts` mapeia campo a campo para o tipo `ConversationActivityEvent`. Por isso os três campos novos são explícitos, no mesmo padrão de `codeStage` e `screenshotPath`. E `kindFor` já cai em `'lifecycle'` por default para tipos desconhecidos, então **não** é preciso tocar em `kindFor`; `stateFor`, por outro lado, cai em `'working'`, o que deixaria o card preso em "Trabalhando" para sempre.

- [ ] **Step 1: Acrescente os campos ao tipo e o estado terminal**

Em `frontend/src/features/conversations/activityTypes.ts`, dentro de `ConversationActivityEvent`, logo depois de `codeApproval?: boolean`:

```ts
  /** Set only on `memory.learned`: what the agent stored, and where. */
  memoryId?: string
  memoryScope?: 'user' | 'project'
  memoryProjectId?: string
```

E em `stateFor`, junto das outras linhas terminais (logo depois da de `artifact.created`):

```ts
  if (type === 'memory.learned') return 'completed'
```

- [ ] **Step 2: Normalize o evento**

Em `frontend/src/api/conversations.ts`, junto dos outros `assign` (~linha 281):

```ts
  assign('memoryId', optionalText(payload.memory_id, 255))
  assign('memoryProjectId', optionalText(payload.project_id, 255))
  if (payload.scope === 'user' || payload.scope === 'project') event.memoryScope = payload.scope
```

- [ ] **Step 3: Acrescente a chamada de API**

Em `frontend/src/api/memory.ts`, ao lado de `deleteManagedMemory`:

```ts
export function updateManagedMemory(client: ApiClient, memoryId: string, fact: string, scope: 'user' | 'project', projectId?: string, intent: MutationIntent = client.createMutationIntent()): Promise<ManagedMemory> {
  return client.request({ path: `/v1/memories/${encodeURIComponent(memoryId)}`, query: { scope, project_id: projectId }, method: 'PATCH', body: { fact }, intent, parse: row })
}
```

`row` já existe nesse arquivo, mas é `function row(...)` sem `export` — mantenha assim, a nova função está no mesmo módulo. Confirme em `frontend/src/api/client.ts` como `request` recebe corpo; se o campo não se chamar `body`, use o nome que o cliente usa.

- [ ] **Step 4: Escreva o card**

Crie `frontend/src/features/conversations/MemoryLearnedCard.tsx`:

```tsx
import { useState } from 'react'
import { createBrowserApiClient } from '../../api/client'
import { deleteManagedMemory } from '../../api/memory'
import type { ConversationActivityEvent } from './activityTypes'

/**
 * "Aprendi: …" — the one place a person finds out the agent kept something.
 *
 * It builds its own API client rather than receiving one: `renderActivityGroup`
 * already carries eight parameters, and undoing a memory needs nothing from the
 * conversation beyond what the event itself carries.
 */
export function MemoryLearnedCard({ event }: { event: ConversationActivityEvent }) {
  const [undone, setUndone] = useState(false)
  const [failed, setFailed] = useState(false)

  const undo = () => {
    if (!event.memoryId) return
    deleteManagedMemory(createBrowserApiClient(), event.memoryId, event.memoryScope ?? 'user', event.memoryProjectId)
      .then(() => { setUndone(true); setFailed(false) })
      .catch(() => setFailed(true))
  }

  return (
    <article className="activity-card memory-learned" data-state={undone ? 'cancelled' : 'completed'} data-kind="lifecycle">
      <span className="activity-card__glyph" aria-hidden="true">◈</span>
      <span className="activity-card__label">{undone ? 'Memória descartada' : event.summary}</span>
      {!undone && event.memoryId && (
        <button type="button" className="memory-learned__undo" onClick={undo}>Desfazer</button>
      )}
      {failed && <span role="alert" className="memory-learned__error">Não foi possível desfazer.</span>}
    </article>
  )
}
```

- [ ] **Step 5: Despache o card**

Em `frontend/src/features/conversations/ActivityStream.tsx`, importe o componente e acrescente a linha em `renderActivityGroup`, imediatamente antes de `if (group.kind === 'agent' && first.type === 'agent.created')`:

```tsx
  if (first.type === 'memory.learned') {
    return <>{group.events.map((event) => <MemoryLearnedCard key={event.eventId} event={event} />)}</>
  }
```

O `map` é necessário porque `groupingKey` colapsa eventos de lifecycle do mesmo tipo e turno numa única linha: duas memórias aprendidas no mesmo turno chegam aqui como um grupo de dois, e cada uma precisa do seu próprio botão de desfazer.

- [ ] **Step 6: Estilize o card**

Em `frontend/src/styles/agentos.css`, ao lado das regras `.activity-card` já existentes:

```css
.memory-learned { display: flex; align-items: center; gap: 0.5rem; }
.memory-learned__undo { margin-left: auto; background: none; border: 0; padding: 0; font: inherit; color: inherit; opacity: 0.7; text-decoration: underline; cursor: pointer; }
.memory-learned__undo:hover { opacity: 1; }
.memory-learned__error { color: var(--danger, #f2789f); font-size: 0.85em; }
```

Confirme o nome real da variável de cor de erro no arquivo e use a que existir; `--danger` é um palpite com fallback.

- [ ] **Step 7: Editar na página Memory**

Em `frontend/src/features/memory/MemoryPage.tsx`, o `MemoryList` hoje é uma única linha. Substitua-o por:

```tsx
function MemoryList({ items, scope, projectId, onChanged }: { items: ManagedMemory[]; scope: 'user' | 'project'; projectId?: string; onChanged: () => void }) {
  const client = useMemo(() => createBrowserApiClient(), [])
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  if (items.length === 0) return <p>Nenhuma memória neste escopo.</p>
  return (
    <div className="memory-list">
      {items.map((item) => (
        <article key={item.memory_id}>
          {editing === item.memory_id ? (
            <>
              <input aria-label="Editar memória" value={draft} onChange={(event) => setDraft(event.target.value)} />
              <button type="button" onClick={() => { void updateManagedMemory(client, item.memory_id, draft, scope, projectId).then(() => { setEditing(null); onChanged() }) }} disabled={!draft.trim()}>Salvar</button>
              <button type="button" onClick={() => setEditing(null)}>Cancelar</button>
            </>
          ) : (
            <>
              <p>{item.fact}</p>
              <small>{item.tags.join(' · ') || 'Sem tags'}{item.updated_at ? ` · atualizado ${new Date(item.updated_at).toLocaleDateString()}` : ''}</small>
              <button type="button" onClick={() => { setEditing(item.memory_id); setDraft(item.fact) }}>Editar</button>
              <button type="button" onClick={() => { void deleteManagedMemory(client, item.memory_id, scope, projectId).then(onChanged) }}>Excluir</button>
            </>
          )}
        </article>
      ))}
    </div>
  )
}
```

Atualize a chamada em `controls` de `<MemoryList items={items} onDelete={…} />` para `<MemoryList items={items} scope={scope} projectId={projectId} onChanged={load} />`, e acrescente `updateManagedMemory` ao import de `../../api/memory`. O `client` e o `load` do componente-pai continuam onde estão.

- [ ] **Step 8: Verifique**

Run: `npm --prefix frontend run build`
Expected: build sem erros de TypeScript.

Depois, com o app rodando, faça um turno em que um comando falhe e outro equivalente funcione (por exemplo, peça para rodar `npm install` num projeto que usa pnpm) e confirme na conversa que o card "Aprendi: …" aparece e que **Desfazer** remove a memória da página Memory.

- [ ] **Step 9: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): show what the agent learned and let it be corrected"
```

---

## Verificação final

- [ ] `uv run python -m pytest -q tests/unit`
- [ ] `npm --prefix frontend run build`
- [ ] O teto de ferramentas continua respeitado:

```bash
uv run python -c "import sys; sys.path.insert(0, 'src'); from agentos.agentic.phases import tools_for, Phase; print({p.value: len(tools_for(p, frozenset({'files','terminal'}))) for p in Phase})"
```

Esperado: `orient` e `execute` em **16**, sem alteração.
