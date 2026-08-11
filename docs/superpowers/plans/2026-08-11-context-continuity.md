# Context Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o agente começar cada turn sabendo o que já fez, onde está e como é o ambiente — eliminando releitura redundante entre turns e a rodada gasta em `list_files`.

**Architecture:** Um registro durável e compacto de tools executadas (`conversation_tool_records`) alimenta um "ledger" injetado no system prompt da turn seguinte. O prompt também passa a carregar a árvore do workspace e os fatos do ambiente (SO, shell, executáveis). Nada disso entra no histórico de mensagens: fica no prefixo do prompt, que é justamente a parte cacheada pelo Plano 2.

**Tech Stack:** Python 3.12, SQLAlchemy Core, Alembic, pytest.

## Global Constraints

- Nome público do produto é **Orin**; identificadores internos permanecem `agentos`. A tabela nova chama-se `conversation_tool_records`.
- `conversation_messages` tem `CheckConstraint("role IN ('user','assistant')")`: **não** persistir tool results ali. A tabela nova existe exatamente por isso.
- A migração nova é `0029_conversation_tool_records`, com `down_revision = "0028_projects"`.
- Falha ao gravar o ledger nunca pode derrubar a turn — é projeção, como a activity.
- Todo módulo novo/alterado começa com `from __future__ import annotations`.
- Rodar testes com `uv run pytest <caminho> -v`.

**Depende de:** Plano 1 Task 3 (`ConversationWorkspace.list_entries(depth=...)`, usado na Task 3) e Plano 2 Task 2 (a Task 2 deste plano edita o `_life(..., "tool_finished", ...)` dentro do `_run_toolset` já reescrito lá).

---

### Task 1: Tabela e escrita do registro de tools

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py` (após o bloco `conversation_turns`, linha ~529)
- Create: `src/agentos/persistence/postgres/migrations/versions/0029_conversation_tool_records.py`
- Modify: `src/agentos/conversations/chat.py` (`PostgresChatStore`)
- Test: `tests/unit/conversations/test_tool_records.py`

**Interfaces:**
- Consumes: `Engine`, tabela `conversation_turns`
- Produces:
  - tabela `conversation_tool_records(id, record_id, conversation_id, turn_id, user_id, sequence, tool_name, arguments, status, summary, created_at)`
  - `PostgresChatStore.record_tool_call(turn, *, tool_name: str, arguments: Mapping[str, object], status: str, summary: str) -> None`

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/conversations/test_tool_records.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import conversation_tool_records, metadata


@pytest.fixture()
def store() -> PostgresChatStore:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "execution_id": "execution-1", "agent_id": "agent-1",
    }


def test_a_tool_call_is_recorded_with_an_increasing_sequence(store: PostgresChatStore) -> None:
    store.record_tool_call(_turn(), tool_name="write_file", arguments={"path": "a.md"}, status="succeeded", summary="Escreveu a.md")
    store.record_tool_call(_turn(), tool_name="read_file", arguments={"path": "a.md"}, status="succeeded", summary="Leu a.md")

    with store._engine.connect() as connection:
        rows = connection.execute(select(conversation_tool_records).order_by(conversation_tool_records.c.sequence)).mappings().all()

    assert [row["tool_name"] for row in rows] == ["write_file", "read_file"]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert rows[0]["status"] == "succeeded"


def test_recording_never_raises_when_the_payload_is_not_serializable(store: PostgresChatStore) -> None:
    store.record_tool_call(_turn(), tool_name="write_file", arguments={"handle": object()}, status="succeeded", summary="ok")

    with store._engine.connect() as connection:
        rows = connection.execute(select(conversation_tool_records)).mappings().all()

    assert len(rows) == 1
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/conversations/test_tool_records.py -v`
Expected: FAIL — `ImportError: cannot import name 'conversation_tool_records'`

- [ ] **Step 3: Declarar a tabela**

Em `src/agentos/persistence/postgres/schema.py`, logo depois do bloco `conversation_turns` / `Index("ix_conversation_turns_conversation", ...)`:

```python
conversation_tool_records = Table(
    "conversation_tool_records", metadata,
    Column("id", Integer, primary_key=True), Column("record_id", String(255), nullable=False, unique=True),
    Column("conversation_id", String(255), nullable=False), Column("turn_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("sequence", Integer, nullable=False),
    Column("tool_name", String(64), nullable=False), Column("arguments", String(1000), nullable=False),
    Column("status", String(16), nullable=False), Column("summary", String(512), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("conversation_id", "sequence", name="uq_conversation_tool_record_sequence"),
)
Index("ix_conversation_tool_records_conversation", conversation_tool_records.c.conversation_id, conversation_tool_records.c.sequence)
```

- [ ] **Step 4: Escrever a migração**

Criar `src/agentos/persistence/postgres/migrations/versions/0029_conversation_tool_records.py`:

```python
"""persist a compact ledger of the tools an agent ran in a conversation.

Revision ID: 0029_tool_records
Revises: 0028_projects
"""
from alembic import op
import sqlalchemy as sa


revision = "0029_tool_records"
down_revision = "0028_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_tool_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("record_id", sa.String(255), nullable=False, unique=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("turn_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_conversation_tool_record_sequence"),
    )
    op.create_index("ix_conversation_tool_records_conversation", "conversation_tool_records", ["conversation_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_conversation_tool_records_conversation", table_name="conversation_tool_records")
    op.drop_table("conversation_tool_records")
```

- [ ] **Step 5: Implementar a escrita**

Em `src/agentos/conversations/chat.py`, acrescentar `conversation_tool_records` ao import de tabelas vindo de `agentos.persistence.postgres.schema` (mesma linha/bloco onde `conversation_turns` já é importado), e adicionar o método a `PostgresChatStore`, logo depois de `_next_activity_sequence`:

```python
    def record_tool_call(self, turn: Mapping[str, object], *, tool_name: str, arguments: Mapping[str, object], status: str, summary: str) -> None:
        """Append one line to the conversation's durable tool ledger.

        This is a projection, exactly like the activity log: it must never be
        able to roll back or fail the turn it is describing.
        """
        try:
            rendered = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)[:1000]
        except (TypeError, ValueError):
            rendered = str(arguments)[:1000]
        now = datetime.now(UTC)
        for _ in range(6):
            try:
                with self._engine.connect() as connection:
                    current = connection.execute(
                        select(func.max(conversation_tool_records.c.sequence)).where(conversation_tool_records.c.conversation_id == turn["conversation_id"])
                    ).scalar()
                sequence = int(current or 0) + 1
                with self._engine.begin() as connection:
                    connection.execute(insert(conversation_tool_records).values(
                        record_id=f"tool:{turn['conversation_id']}:{sequence}",
                        conversation_id=str(turn["conversation_id"]), turn_id=str(turn["turn_id"]),
                        user_id=str(turn["user_id"]), sequence=sequence, tool_name=str(tool_name)[:64],
                        arguments=rendered, status=str(status)[:16], summary=str(summary)[:512] or str(tool_name)[:512],
                        created_at=now,
                    ))
                return
            except IntegrityError:
                continue
            except Exception:
                return
```

Confirmar que `json`, `func`, `insert`, `select`, `datetime`, `UTC` e `IntegrityError` já estão importados no topo do arquivo; se `IntegrityError` não estiver, acrescentar:

```python
from sqlalchemy.exc import IntegrityError
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/conversations/test_tool_records.py -v`
Expected: PASS

- [ ] **Step 7: Verificar a migração**

Run: `uv run alembic heads`
Expected: uma única head, `0029_tool_records`

- [ ] **Step 8: Commit**

```bash
git add src/agentos/persistence/postgres/schema.py src/agentos/persistence/postgres/migrations/versions/0029_conversation_tool_records.py src/agentos/conversations/chat.py tests/unit/conversations/test_tool_records.py
git commit -m "feat(conversations): persist a durable tool ledger"
```

---

### Task 2: Ledger no prompt da turn seguinte

**Files:**
- Modify: `src/agentos/conversations/chat.py` (`PostgresChatStore`)
- Modify: `src/agentos/agentic/session.py:42-93` (`build_system_prompt`), `:203-272` (`emit_lifecycle`), `:387-413` (`build_runtime`)
- Test: `tests/unit/conversations/test_tool_records.py`, `tests/unit/agentic/test_turn_session.py`

**Interfaces:**
- Consumes: `PostgresChatStore.record_tool_call` (Task 1)
- Produces:
  - `PostgresChatStore.tool_ledger(turn, *, limit: int = 20) -> list[dict[str, str]]` — mais recentes primeiro na consulta, devolvidos em ordem cronológica, cada item `{"tool_name", "arguments", "status", "summary"}`
  - `build_system_prompt(..., tool_ledger: tuple[Mapping[str, str], ...] = ())` — nova seção `## What you already did in this conversation`
  - `TurnSession.emit_lifecycle` grava cada `tool_finished` no ledger

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/conversations/test_tool_records.py`:

```python
def test_the_ledger_returns_recent_entries_in_chronological_order(store: PostgresChatStore) -> None:
    for index in range(5):
        store.record_tool_call(_turn(), tool_name=f"tool_{index}", arguments={}, status="succeeded", summary=f"passo {index}")

    ledger = store.tool_ledger(_turn(), limit=3)

    assert [item["tool_name"] for item in ledger] == ["tool_2", "tool_3", "tool_4"]
```

E em `tests/unit/agentic/test_turn_session.py`:

```python
def test_the_system_prompt_lists_what_the_agent_already_did() -> None:
    from agentos.agentic.session import build_system_prompt

    prompt = build_system_prompt(
        tool_names=("read_file",), memories=[], agents=[], workspace_hint="hint",
        subagents_enabled=False,
        tool_ledger=({"tool_name": "write_file", "arguments": '{"path": "report.md"}', "status": "succeeded", "summary": "Escreveu report.md"},),
    )

    assert "What you already did in this conversation" in prompt
    assert "write_file" in prompt
    assert "report.md" in prompt


def test_the_ledger_section_is_absent_when_nothing_was_done() -> None:
    from agentos.agentic.session import build_system_prompt

    prompt = build_system_prompt(tool_names=("read_file",), memories=[], agents=[], workspace_hint="hint", subagents_enabled=False)

    assert "What you already did" not in prompt
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/conversations/test_tool_records.py tests/unit/agentic/test_turn_session.py -k "ledger or already_did" -v`
Expected: FAIL — `AttributeError: 'PostgresChatStore' object has no attribute 'tool_ledger'` e `TypeError: build_system_prompt() got an unexpected keyword argument 'tool_ledger'`

- [ ] **Step 3: Implementar a leitura do ledger**

Em `src/agentos/conversations/chat.py`, logo depois de `record_tool_call`:

```python
    def tool_ledger(self, turn: Mapping[str, object], *, limit: int = 20) -> list[dict[str, str]]:
        """The most recent tool steps of this conversation, oldest first."""
        bounded = max(1, min(int(limit), 50))
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    conversation_tool_records.c.tool_name, conversation_tool_records.c.arguments,
                    conversation_tool_records.c.status, conversation_tool_records.c.summary,
                )
                .where(conversation_tool_records.c.conversation_id == turn["conversation_id"])
                .order_by(conversation_tool_records.c.sequence.desc())
                .limit(bounded)
            ).mappings().all()
        return [
            {"tool_name": str(row["tool_name"]), "arguments": str(row["arguments"]), "status": str(row["status"]), "summary": str(row["summary"])}
            for row in reversed(rows)
        ]
```

- [ ] **Step 4: Acrescentar a seção ao prompt**

Em `src/agentos/agentic/session.py`, na assinatura de `build_system_prompt`, acrescentar o parâmetro depois de `skill_catalog`:

```python
    tool_ledger: tuple[Mapping[str, str], ...] = (),
```

E, imediatamente antes do bloco `if memories:`, inserir:

```python
    if tool_ledger:
        lines += [
            "",
            "## What you already did in this conversation",
            "These steps already happened. Do not repeat them just to see their result — read the file again only if you expect it to have changed.",
        ]
        lines += [
            f"- {item['tool_name']}({item['arguments'][:120]}) → {item['status']}: {item['summary'][:120]}"
            for item in tool_ledger
        ]
```

- [ ] **Step 5: Gravar cada tool no ledger**

Em `src/agentos/agentic/session.py`, dentro de `emit_lifecycle`, no ramo `if state == "tool_finished":`, logo depois da chamada a `self._record(AgentActivityEventType.TOOL_FINISHED, ...)`, acrescentar:

```python
            ledger = getattr(self.store, "record_tool_call", None)
            if callable(ledger):
                ledger(
                    self.turn, tool_name=name, arguments=dict(payload.get("tool_arguments") or {}),
                    status=status, summary=summary,
                )
```

Para que `tool_arguments` exista, em `src/agentos/agentic/runtime.py`, no `_life(turn, "tool_finished", ...)` de `_run_toolset`, acrescentar o argumento:

```python
            self._life(
                turn, "tool_finished", tool_name=name, invocation_id=call_id, status=outcome.status,
                summary=outcome.summary, error_code=outcome.error_code, tool_payload=dict(outcome.payload),
                tool_arguments=dict(arguments),
            )
```

- [ ] **Step 6: Passar o ledger ao construir o prompt**

Em `src/agentos/agentic/session.py`, em `build_runtime`, logo depois de `agents = self.agents_store.list() ...`:

```python
        reader = getattr(self.store, "tool_ledger", None)
        ledger = tuple(reader(self.turn, limit=20)) if callable(reader) else ()
```

E acrescentar `tool_ledger=ledger,` à chamada de `build_system_prompt`.

- [ ] **Step 7: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/conversations tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/agentos/conversations/chat.py src/agentos/agentic/session.py src/agentos/agentic/runtime.py tests/unit/conversations/test_tool_records.py tests/unit/agentic/test_turn_session.py
git commit -m "feat(agentic): carry the tool ledger into the next turn's prompt"
```

---

### Task 3: Ambiente e árvore do workspace no prompt

**Files:**
- Modify: `src/agentos/agentic/session.py:42-93` (`build_system_prompt`), `:387-413` (`build_runtime`)
- Test: `tests/unit/agentic/test_turn_session.py`

**Interfaces:**
- Consumes: `ConversationWorkspace.list_entries(path, depth=...)` (Plano 1 Task 3)
- Produces:
  - `agentos.agentic.session.environment_facts() -> dict[str, str]` — `{"os", "shell", "python"}`
  - `build_system_prompt(..., environment: Mapping[str, str] = {}, workspace_tree: tuple[str, ...] = ())`

**Por quê:** hoje `workspace_hint` é a string fixa *"It starts empty unless a previous turn created files."* e o agente não sabe se `run_command` cai em `cmd.exe`, PowerShell ou `sh` — o que produz uma classe inteira de retries de sintaxe.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_turn_session.py`:

```python
def test_environment_facts_name_the_shell_and_the_operating_system() -> None:
    from agentos.agentic.session import environment_facts

    facts = environment_facts()

    assert facts["os"]
    assert facts["shell"]
    assert facts["python"].startswith("3.")


def test_the_prompt_states_the_environment_and_the_workspace_tree() -> None:
    from agentos.agentic.session import build_system_prompt

    prompt = build_system_prompt(
        tool_names=("run_command",), memories=[], agents=[], workspace_hint="hint", subagents_enabled=False,
        environment={"os": "Windows 11", "shell": "cmd.exe", "python": "3.12.4"},
        workspace_tree=("d src", "f src/app.py"),
    )

    assert "cmd.exe" in prompt
    assert "src/app.py" in prompt


def test_an_empty_workspace_says_so_instead_of_printing_an_empty_tree() -> None:
    from agentos.agentic.session import build_system_prompt

    prompt = build_system_prompt(
        tool_names=("run_command",), memories=[], agents=[], workspace_hint="hint", subagents_enabled=False,
        environment={"os": "Windows 11", "shell": "cmd.exe", "python": "3.12.4"},
        workspace_tree=(),
    )

    assert "empty" in prompt.lower()
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_turn_session.py -k "environment or workspace_tree or empty_workspace" -v`
Expected: FAIL — `ImportError: cannot import name 'environment_facts'`

- [ ] **Step 3: Implementar os fatos do ambiente**

Em `src/agentos/agentic/session.py`, acrescentar aos imports do topo:

```python
import os
import platform
import shutil
```

`Path` já está importado no topo do arquivo (`from pathlib import Path`).

E a função, logo antes de `build_system_prompt`:

```python
def environment_facts() -> dict[str, str]:
    """Describe the machine the agent's commands actually run on.

    ``run_command`` uses ``shell=True``, so the interpreter is the platform's
    default. Telling the model which one it is removes a whole class of retries
    caused by guessing cmd vs PowerShell vs sh syntax.
    """
    if os.name == "nt":
        shell = os.environ.get("COMSPEC") or "cmd.exe"
    else:
        shell = os.environ.get("SHELL") or "/bin/sh"
    tooling = ", ".join(name for name in ("git", "node", "npm", "uv", "docker") if shutil.which(name))
    return {
        "os": f"{platform.system()} {platform.release()}".strip(),
        "shell": Path(shell).name,
        "python": platform.python_version(),
        "available": tooling or "none detected",
    }
```

- [ ] **Step 4: Acrescentar as seções ao prompt**

Em `build_system_prompt`, acrescentar os parâmetros depois de `tool_ledger`:

```python
    environment: Mapping[str, str] = MappingProxyType({}),
    workspace_tree: tuple[str, ...] = (),
```

Acrescentar ao topo do arquivo:

```python
from types import MappingProxyType
```

Substituir o bloco `## Workspace` existente por:

```python
    lines += [
        "",
        "## Workspace",
        f"- You have a private working directory for this conversation. All file paths are relative to it. {workspace_hint}",
        "- Commands run with that directory as the working directory.",
    ]
    if workspace_tree:
        lines += ["- It currently contains:"]
        lines += [f"  {item}" for item in workspace_tree]
    else:
        lines += ["- It is currently empty."]
    if environment:
        lines += [
            "",
            "## Environment",
            f"- Operating system: {environment.get('os', 'unknown')}",
            f"- run_command executes through: {environment.get('shell', 'unknown')} — use that shell's syntax, not another one's.",
            f"- Python: {environment.get('python', 'unknown')}. Also on PATH: {environment.get('available', 'unknown')}.",
        ]
```

- [ ] **Step 5: Alimentar prompt em `build_runtime`**

Em `build_runtime`, logo depois de obter `ledger`:

```python
        try:
            tree = tuple(f"{item['kind'][:1]} {item['path']}" for item in self.workspace.list_entries(depth=3))[:60]
        except Exception:
            # A prompt enrichment must never be the reason a turn cannot start.
            tree = ()
```

E acrescentar à chamada de `build_system_prompt`:

```python
            environment=environment_facts(),
            workspace_tree=tree,
```

Substituir também o `workspace_hint` fixo por algo que não contradiga a árvore:

```python
            workspace_hint="Files you create there persist for the whole conversation.",
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agentos/agentic/session.py tests/unit/agentic/test_turn_session.py
git commit -m "feat(agentic): tell the agent its environment and workspace contents"
```

---

### Task 4: Instruir batching de tools no prompt

**Files:**
- Modify: `src/agentos/agentic/session.py:51-62` (bloco `## How you work`)
- Test: `tests/unit/agentic/test_turn_session.py`

**Interfaces:**
- Consumes: nada novo
- Produces: `build_system_prompt` passa a instruir chamadas independentes na mesma resposta e a apontar `search_files` antes de leitura exploratória.

**Por quê:** o texto atual (*"Chain tools when needed"*) induz o modelo a serializar. O runtime já aceita várias tool calls por iteração e, depois do Plano 2 Task 2, executa as de leitura em paralelo — falta pedir.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_turn_session.py`:

```python
def test_the_prompt_asks_for_independent_calls_in_one_response() -> None:
    from agentos.agentic.session import build_system_prompt

    prompt = build_system_prompt(tool_names=("read_file", "search_files"), memories=[], agents=[], workspace_hint="hint", subagents_enabled=False)

    assert "same response" in prompt
    assert "search_files" in prompt
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_turn_session.py -k independent_calls -v`
Expected: FAIL — `assert "same response" in prompt`

- [ ] **Step 3: Reescrever o bloco de orientação**

Em `src/agentos/agentic/session.py`, substituir as linhas do bloco `## How you work` por:

```python
    lines = [
        "You are the main agent of Orin, a local-first agent workspace running on the user's own machine.",
        "Answer in the language the user writes in. Be direct and concrete; skip filler and self-description.",
        "",
        "## How you work",
        "- You act, you do not only advise. If a task can be done with a tool, use the tool instead of describing it.",
        "- Request every independent tool call in the same response. Reading three files, or searching and listing at once, is one step — not three.",
        "- Only wait for a result before calling the next tool when the next call genuinely depends on it: read before you edit, verify after you write, check a command's output before reporting success.",
        "- When you do not know where something is, call search_files first. Do not go directory by directory.",
        "- Read long files with read_file offset/limit instead of hoping the whole file fits in one result.",
        "- Never claim you did something you did not actually do with a tool. If a tool failed, say so and what you tried.",
        "- If a tool fails twice the same way, change the approach instead of repeating the call.",
        "- Keep the final answer for the user short and useful; the interface already shows every tool step you took.",
        "- When you create a useful workspace file, link it in your final answer as [filename](workspace://relative/path). Prioritize final deliverables and include generator scripts when useful.",
        "- Use run_command only for commands that finish. To start a local server, call run_command with background=true.",
    ]
```

Nota: a linha do `run_command` perde a menção a `nohup`/`&` porque a seção `## Environment` (Task 3) agora informa o shell real.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS — se algum teste existente afirmava a string "AgentOS" no prompt, atualizá-lo para "Orin"; o nome visível do produto é Orin (identificadores internos não mudam)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/session.py tests/unit/agentic/test_turn_session.py
git commit -m "feat(agentic): instruct parallel tool batching and search-first exploration"
```

---

## Verificação final do plano

- [ ] `uv run pytest tests/unit/agentic tests/unit/conversations tests/unit/workers -v` — PASS
- [ ] `uv run alembic heads` — uma única head (`0029_tool_records`)
- [ ] `uv run alembic upgrade head` seguido de `uv run alembic downgrade -1` em base descartável — sem erro
- [ ] Conferir no diff que `conversation_messages` não foi alterada
