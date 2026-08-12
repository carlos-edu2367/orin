# Pasta local como workspace do chat/projeto — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que a pessoa aponte um chat ou projeto para uma pasta da própria máquina, escolhida pelo seletor nativo do sistema operacional, e que o agente passe a trabalhar nela no lugar da pasta gerenciada pelo Orin.

**Architecture:** Uma pasta local é uma raiz alternativa gravada contra o `workspace_id` efetivo (conversa solta → `conversation_id`; chat de projeto → `workspace:<project_id>`) numa tabela nova `workspace_roots`. `ConversationWorkspace` ganha um construtor que usa um caminho como raiz final, sem anexar `workspace_id`, e todo o containment existente passa a valer relativo à pasta escolhida. O gateway ganha três rotas (inspecionar, anexar, desanexar); o seletor nativo roda em subprocesso com timeout, nunca no processo da API. Nenhuma pasta é bloqueada: risco alto muda a confirmação, não a permissão.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy Core, Alembic, pytest, React 19 + TypeScript, Vite, Vitest, Testing Library.

**Spec:** [docs/superpowers/specs/2026-08-12-local-workspace-folder-design.md](../specs/2026-08-12-local-workspace-folder-design.md)

---

## Estrutura de arquivos

**Criar:**

| Arquivo | Responsabilidade |
|---|---|
| `src/agentos/local_workspace/__init__.py` | Superfície pública do pacote |
| `src/agentos/local_workspace/paths.py` | Normalizar caminho, classificar risco, inspecionar pasta |
| `src/agentos/local_workspace/picker.py` | Abrir o seletor nativo em subprocesso com timeout |
| `src/agentos/local_workspace/store.py` | CRUD de `workspace_roots` |
| `src/agentos/persistence/postgres/migrations/versions/0030_workspace_roots.py` | Migração da tabela |
| `frontend/src/api/workspace.ts` | Cliente das três rotas |
| `frontend/src/features/conversations/WorkspaceFolderButton.tsx` | Botão do composer e seu popover |

**Modificar:**

| Arquivo | Mudança |
|---|---|
| `src/agentos/persistence/postgres/schema.py` | Tabela `workspace_roots` |
| `src/agentos/agentic/workspace.py` | `ConversationWorkspace.at_root` e `resolve_workspace` |
| `src/agentos/agentic/session.py` | Raiz local no turno e `workspace_hint` |
| `src/agentos/conversations/chat.py` | `claim()` traz `workspace_root_path` |
| `src/agentos/api/gateway.py` | `local_workspaces` em `ApiServices`, três rotas, bloco `workspace` no GET |
| `frontend/src/features/conversations/ChatPage.tsx` | Renderiza o botão no slot `settings` do `Composer` |
| `frontend/src/styles/agentos.css` | Estilos do botão e do popover |

O pacote se chama `local_workspace` e não `workspaces` porque `agentos.workspaces` já é o `WorkspaceManager` da RFC 603, uma abstração diferente e mais pesada. Misturar os dois confundiria quem lê.

---

### Task 1: Tabela `workspace_roots`

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py:498-505` (logo após o bloco `projects`)
- Create: `src/agentos/persistence/postgres/migrations/versions/0030_workspace_roots.py`
- Create: `tests/unit/local_workspace/__init__.py`
- Test: `tests/unit/local_workspace/test_workspace_roots_schema.py`

- [ ] **Step 1: Criar o pacote de testes**

Crie `tests/unit/local_workspace/__init__.py` vazio (0 bytes).

- [ ] **Step 2: Escrever o teste que falha**

Crie `tests/unit/local_workspace/test_workspace_roots_schema.py`:

```python
from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from agentos.persistence.postgres.schema import metadata, workspace_roots


def test_workspace_roots_stores_one_root_per_workspace() -> None:
    """The effective workspace id is the key; a second row for it must fail."""
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(workspace_roots).values(workspace_id="chat_a", user_id="owner", root_path="/tmp/one", created_at=now, updated_at=now))
    with engine.connect() as connection:
        assert connection.execute(select(workspace_roots.c.root_path)).scalar_one() == "/tmp/one"
    try:
        with engine.begin() as connection:
            connection.execute(insert(workspace_roots).values(workspace_id="chat_a", user_id="owner", root_path="/tmp/two", created_at=now, updated_at=now))
    except IntegrityError:
        return
    raise AssertionError("workspace_id must be unique")
```

- [ ] **Step 3: Rodar o teste e ver falhar**

Run: `python -m pytest tests/unit/local_workspace/test_workspace_roots_schema.py -v`
Expected: FAIL com `ImportError: cannot import name 'workspace_roots'`

- [ ] **Step 4: Declarar a tabela**

Em `src/agentos/persistence/postgres/schema.py`, logo depois da linha `Index("ix_projects_user_active", ...)`, adicione:

```python
workspace_roots = Table(
    "workspace_roots", metadata,
    Column("workspace_id", String(255), primary_key=True), Column("user_id", String(255), nullable=False),
    Column("root_path", String(4096), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
```

E acrescente `"workspace_roots",` à lista `__all__` no fim do arquivo, junto de `"projects",`.

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/local_workspace/test_workspace_roots_schema.py -v`
Expected: PASS

- [ ] **Step 6: Escrever a migração**

Crie `src/agentos/persistence/postgres/migrations/versions/0030_workspace_roots.py`:

```python
"""persist a user-chosen local folder as a workspace root.

Revision ID: 0030_workspace_roots
Revises: 0029_conversation_tool_records
"""
from alembic import op
import sqlalchemy as sa


revision = "0030_workspace_roots"
down_revision = "0029_conversation_tool_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_roots",
        sa.Column("workspace_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("root_path", sa.String(4096), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspace_roots_user", "workspace_roots", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_roots_user", table_name="workspace_roots")
    op.drop_table("workspace_roots")
```

Confirme que `0029_conversation_tool_records` é mesmo a última revisão: `python -c "import pathlib,re; print(sorted(p.name for p in pathlib.Path('src/agentos/persistence/postgres/migrations/versions').glob('0*.py')))"`. Se houver revisão mais nova, ajuste `down_revision`.

- [ ] **Step 7: Rodar a suíte de persistência**

Run: `python -m pytest tests/unit/persistence -q`
Expected: PASS (as duas falhas conhecidas do README, se aparecerem, já existiam antes — confirme com `git stash` se houver dúvida)

- [ ] **Step 8: Commit**

```bash
git add src/agentos/persistence/postgres/schema.py src/agentos/persistence/postgres/migrations/versions/0030_workspace_roots.py tests/unit/local_workspace
git commit -m "feat(workspace): persist a local folder as a workspace root"
```

---

### Task 2: Normalização de caminho, risco e inspeção

**Files:**
- Create: `src/agentos/local_workspace/__init__.py`
- Create: `src/agentos/local_workspace/paths.py`
- Test: `tests/unit/local_workspace/test_workspace_paths.py`

- [ ] **Step 1: Escrever os testes que falham**

Crie `tests/unit/local_workspace/test_workspace_paths.py`:

```python
from pathlib import Path

import pytest

from agentos.local_workspace.paths import FolderRejected, classify_risk, inspect_folder, normalize_path


def test_normalize_expands_home_and_resolves(tmp_path: Path) -> None:
    target = tmp_path / "projeto"
    target.mkdir()
    assert normalize_path(f"  {target}  ") == target.resolve()


def test_normalize_rejects_blank_and_relative() -> None:
    with pytest.raises(FolderRejected):
        normalize_path("   ")
    with pytest.raises(FolderRejected):
        normalize_path("projetos/site")


def test_classify_risk_names_the_broad_choices(tmp_path: Path) -> None:
    home = tmp_path / "home"
    orin = tmp_path / "home" / ".orin"
    (tmp_path / "home" / "codigo").mkdir(parents=True)
    orin.mkdir(parents=True)
    root = Path(tmp_path.anchor)

    assert classify_risk(root, home=home, orin_data=orin, system_prefixes=()) == "drive_root"
    assert classify_risk(home, home=home, orin_data=orin, system_prefixes=()) == "home_root"
    assert classify_risk(orin / "workspaces", home=home, orin_data=orin, system_prefixes=()) == "orin_data"
    assert classify_risk(tmp_path / "sys" / "bin", home=home, orin_data=orin, system_prefixes=(tmp_path / "sys",)) == "system"
    assert classify_risk(home / "codigo", home=home, orin_data=orin, system_prefixes=()) == "none"


def test_inspect_reports_a_usable_folder(tmp_path: Path) -> None:
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_text("<h1>oi</h1>", encoding="utf-8")
    (folder / "src").mkdir()

    result = inspect_folder(str(folder), home=tmp_path, orin_data=tmp_path / ".orin")

    assert result.path == str(folder.resolve())
    assert result.exists is True
    assert result.is_directory is True
    assert result.writable is True
    assert result.entry_count == 2
    assert result.entries_truncated is False
    assert result.risk == "none"


def test_inspect_reports_missing_and_non_directory(tmp_path: Path) -> None:
    missing = inspect_folder(str(tmp_path / "nao-existe"), home=tmp_path, orin_data=tmp_path / ".orin")
    assert missing.exists is False and missing.is_directory is False and missing.entry_count == 0

    file_path = tmp_path / "arquivo.txt"
    file_path.write_text("x", encoding="utf-8")
    as_file = inspect_folder(str(file_path), home=tmp_path, orin_data=tmp_path / ".orin")
    assert as_file.exists is True and as_file.is_directory is False


def test_inspect_caps_the_entry_count(tmp_path: Path) -> None:
    folder = tmp_path / "grande"
    folder.mkdir()
    for index in range(505):
        (folder / f"f{index}.txt").write_text("x", encoding="utf-8")

    result = inspect_folder(str(folder), home=tmp_path, orin_data=tmp_path / ".orin")

    assert result.entry_count == 500
    assert result.entries_truncated is True
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/local_workspace/test_workspace_paths.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.local_workspace'`

- [ ] **Step 3: Implementar**

Crie `src/agentos/local_workspace/__init__.py`:

```python
"""A user-chosen local folder used as the working root of a chat or project."""
from .paths import FolderInspection, FolderRejected, classify_risk, inspect_folder, normalize_path

__all__ = ["FolderInspection", "FolderRejected", "classify_risk", "inspect_folder", "normalize_path"]
```

Crie `src/agentos/local_workspace/paths.py`:

```python
"""Normalisation, risk labelling and inspection of a user-chosen folder.

No folder is refused on policy grounds: the machine belongs to the person, and
pointing an agent at a whole disk is a legitimate request. ``classify_risk``
exists so the interface can name the consequence of a broad choice, never to
block it. Only facts block: a path that does not exist, is not a directory, or
cannot be written to.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys

MAX_PATH_CHARS = 4096
MAX_COUNTED_ENTRIES = 500


class FolderRejected(ValueError):
    """The text could not be read as an absolute local path."""


@dataclass(frozen=True, slots=True)
class FolderInspection:
    path: str
    exists: bool
    is_directory: bool
    writable: bool
    entry_count: int
    entries_truncated: bool
    risk: str


def normalize_path(value: str) -> Path:
    if not isinstance(value, str):
        raise FolderRejected("path must be text")
    text = value.strip().strip('"')
    if not text or len(text) > MAX_PATH_CHARS:
        raise FolderRejected("path must be a bounded non-blank value")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise FolderRejected("path must be absolute")
    try:
        return candidate.resolve()
    except OSError as error:
        raise FolderRejected("path could not be resolved") from error


def system_prefixes() -> tuple[Path, ...]:
    if sys.platform.startswith("win"):
        names = (os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        return tuple(Path(name) for name in names if name)
    return tuple(Path(name) for name in ("/etc", "/usr", "/bin", "/sbin", "/var", "/System", "/Library"))


def classify_risk(path: Path, *, home: Path, orin_data: Path, system_prefixes: tuple[Path, ...]) -> str:
    if path == Path(path.anchor):
        return "drive_root"
    for prefix in system_prefixes:
        if path == prefix or _inside(path, prefix):
            return "system"
    if path == home:
        return "home_root"
    if path == orin_data or _inside(path, orin_data):
        return "orin_data"
    return "none"


def inspect_folder(value: str, *, home: Path, orin_data: Path, prefixes: tuple[Path, ...] | None = None) -> FolderInspection:
    path = normalize_path(value)
    risk = classify_risk(path, home=home, orin_data=orin_data, system_prefixes=prefixes if prefixes is not None else system_prefixes())
    exists = path.exists()
    is_directory = path.is_dir()
    writable = is_directory and os.access(path, os.W_OK)
    count, truncated = _count_entries(path) if is_directory else (0, False)
    return FolderInspection(str(path), exists, is_directory, writable, count, truncated, risk)


def _inside(path: Path, prefix: Path) -> bool:
    try:
        return path.is_relative_to(prefix)
    except (OSError, ValueError):
        return False


def _count_entries(path: Path) -> tuple[int, bool]:
    """Count the first level only, bounded, so a huge folder cannot stall the request."""
    count = 0
    try:
        with os.scandir(path) as entries:
            for _ in entries:
                count += 1
                if count >= MAX_COUNTED_ENTRIES:
                    return count, True
    except OSError:
        return 0, False
    return count, False
```

Note que o teste chama `classify_risk(..., system_prefixes=(...))` e `inspect_folder` recebe os prefixos por `prefixes=`; a função `system_prefixes()` só é consultada quando ninguém injeta, o que mantém o teste independente de plataforma.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/local_workspace/test_workspace_paths.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/local_workspace tests/unit/local_workspace/test_workspace_paths.py
git commit -m "feat(workspace): inspect and label a user-chosen folder without blocking it"
```

---

### Task 3: Seletor nativo em subprocesso

**Files:**
- Create: `src/agentos/local_workspace/picker.py`
- Modify: `src/agentos/local_workspace/__init__.py`
- Test: `tests/unit/local_workspace/test_workspace_picker.py`

- [ ] **Step 1: Escrever os testes que falham**

Crie `tests/unit/local_workspace/test_workspace_picker.py`:

```python
import subprocess

from agentos.local_workspace.picker import PickResult, choose_folder


def test_choose_folder_returns_the_selected_path(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="D:\\projetos\\site\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert choose_folder(command=["fake"]) == PickResult(path="D:\\projetos\\site", cancelled=False, available=True)


def test_empty_output_reads_as_cancelled(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="\n", stderr="User canceled")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert choose_folder(command=["fake"]) == PickResult(path=None, cancelled=True, available=True)


def test_missing_binary_reads_as_unavailable(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert choose_folder(command=["fake"]) == PickResult(path=None, cancelled=False, available=False)


def test_timeout_reads_as_unavailable(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert choose_folder(command=["fake"]) == PickResult(path=None, cancelled=False, available=False)


def test_no_command_for_the_platform_reads_as_unavailable() -> None:
    assert choose_folder(command=[]) == PickResult(path=None, cancelled=False, available=False)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/local_workspace/test_workspace_picker.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.local_workspace.picker'`

- [ ] **Step 3: Implementar**

Crie `src/agentos/local_workspace/picker.py`:

```python
"""The operating system's own folder chooser, run out of process.

The browser cannot hand over an absolute path, so the local server opens the
dialog — the same reasoning that already puts ``os.startfile`` behind a
user-initiated route. It runs in a subprocess with a timeout because a dialog
left open behind another window must never hold an API worker.
"""
from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys

DIALOG_TIMEOUT_SECONDS = 180
PROMPT = "Escolha a pasta de trabalho do agente"

_POWERSHELL_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
    f"$dialog.Description = '{PROMPT}'; "
    "$dialog.ShowNewFolderButton = $true; "
    "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }"
)


@dataclass(frozen=True, slots=True)
class PickResult:
    path: str | None
    cancelled: bool
    available: bool


def dialog_command() -> list[str]:
    if sys.platform.startswith("win"):
        return ["powershell", "-NoProfile", "-STA", "-Command", _POWERSHELL_SCRIPT]
    if sys.platform == "darwin":
        return ["osascript", "-e", f'POSIX path of (choose folder with prompt "{PROMPT}")']
    return ["zenity", "--file-selection", "--directory", f"--title={PROMPT}"]


def fallback_command() -> list[str]:
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return []
    return ["kdialog", "--getexistingdirectory", "."]


def choose_folder(*, command: list[str] | None = None, timeout: int = DIALOG_TIMEOUT_SECONDS) -> PickResult:
    commands = [command] if command is not None else [dialog_command(), fallback_command()]
    unavailable = PickResult(path=None, cancelled=False, available=False)
    for candidate in commands:
        if not candidate:
            continue
        try:
            completed = subprocess.run(candidate, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 - fixed platform dialog, no user input in the command
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
        selected = completed.stdout.strip()
        if selected:
            return PickResult(path=selected, cancelled=False, available=True)
        return PickResult(path=None, cancelled=True, available=True)
    return unavailable
```

Atualize `src/agentos/local_workspace/__init__.py` para:

```python
"""A user-chosen local folder used as the working root of a chat or project."""
from .paths import FolderInspection, FolderRejected, classify_risk, inspect_folder, normalize_path
from .picker import PickResult, choose_folder

__all__ = ["FolderInspection", "FolderRejected", "PickResult", "choose_folder", "classify_risk", "inspect_folder", "normalize_path"]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/local_workspace/test_workspace_picker.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/local_workspace
git commit -m "feat(workspace): open the native folder chooser out of process"
```

---

### Task 4: Store da pasta local

**Files:**
- Create: `src/agentos/local_workspace/store.py`
- Modify: `src/agentos/local_workspace/__init__.py`
- Test: `tests/unit/local_workspace/test_local_workspace_store.py`

- [ ] **Step 1: Escrever os testes que falham**

Crie `tests/unit/local_workspace/test_local_workspace_store.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.schema import metadata


def _store() -> PostgresLocalWorkspaceStore:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    return PostgresLocalWorkspaceStore(engine)


def test_set_root_is_an_upsert_and_read_is_scoped_to_the_owner() -> None:
    """Reading another user's root would leak where their files live."""
    store = _store()
    store.set_root("workspace:project_a", "owner", "/tmp/um")
    store.set_root("workspace:project_a", "owner", "/tmp/dois")

    assert store.root_for("workspace:project_a", "owner") == "/tmp/dois"
    assert store.root_for("workspace:project_a", "other") is None
    assert store.root_for("workspace:unknown", "owner") is None


def test_clear_root_removes_it_and_is_idempotent() -> None:
    store = _store()
    store.set_root("chat_a", "owner", "/tmp/um")

    assert store.clear_root("chat_a", "owner") is True
    assert store.root_for("chat_a", "owner") is None
    assert store.clear_root("chat_a", "owner") is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/local_workspace/test_local_workspace_store.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.local_workspace.store'`

- [ ] **Step 3: Implementar**

Crie `src/agentos/local_workspace/store.py`:

```python
"""Durable binding between an effective workspace id and a local folder."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import workspace_roots


class PostgresLocalWorkspaceStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def root_for(self, workspace_id: str, user_id: str) -> str | None:
        statement = select(workspace_roots.c.root_path).where(
            workspace_roots.c.workspace_id == workspace_id,
            workspace_roots.c.user_id == user_id,
        )
        with self._engine.connect() as connection:
            return connection.execute(statement).scalar_one_or_none()

    def set_root(self, workspace_id: str, user_id: str, root_path: str) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            updated = connection.execute(
                update(workspace_roots)
                .where(workspace_roots.c.workspace_id == workspace_id, workspace_roots.c.user_id == user_id)
                .values(root_path=root_path, updated_at=now)
            ).rowcount
            if not updated:
                connection.execute(insert(workspace_roots).values(workspace_id=workspace_id, user_id=user_id, root_path=root_path, created_at=now, updated_at=now))

    def clear_root(self, workspace_id: str, user_id: str) -> bool:
        statement = delete(workspace_roots).where(
            workspace_roots.c.workspace_id == workspace_id,
            workspace_roots.c.user_id == user_id,
        )
        with self._engine.begin() as connection:
            return bool(connection.execute(statement).rowcount)
```

Acrescente ao `__all__` e aos imports de `src/agentos/local_workspace/__init__.py`:

```python
from .store import PostgresLocalWorkspaceStore
```

e `"PostgresLocalWorkspaceStore"` na lista.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/local_workspace -v`
Expected: PASS (todos os testes do pacote)

- [ ] **Step 5: Commit**

```bash
git add src/agentos/local_workspace tests/unit/local_workspace/test_local_workspace_store.py
git commit -m "feat(workspace): store the local root per effective workspace"
```

---

### Task 5: Resolver a raiz do `ConversationWorkspace`

**Files:**
- Modify: `src/agentos/agentic/workspace.py:40-43`
- Test: `tests/unit/agentic/test_workspace_resolution.py`

- [ ] **Step 1: Escrever os testes que falham**

Crie `tests/unit/agentic/test_workspace_resolution.py`:

```python
from pathlib import Path

import pytest

from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError, resolve_workspace


def test_local_root_is_the_root_itself(tmp_path: Path) -> None:
    """A chosen folder must not gain a workspace-id subdirectory below it."""
    chosen = tmp_path / "projeto"
    chosen.mkdir()

    workspace = resolve_workspace("chat_abc", managed_root=tmp_path / "managed", local_root=str(chosen))

    assert workspace.root == chosen.resolve()
    assert not (chosen / "chat_abc").exists()


def test_without_a_local_root_the_managed_layout_is_unchanged(tmp_path: Path) -> None:
    managed = tmp_path / "managed"

    workspace = resolve_workspace("chat_abc", managed_root=managed, local_root=None)

    assert workspace.root == (managed / "chat_abc").resolve()


def test_containment_still_holds_under_a_local_root(tmp_path: Path) -> None:
    chosen = tmp_path / "projeto"
    chosen.mkdir()
    workspace = resolve_workspace("chat_abc", managed_root=tmp_path / "managed", local_root=str(chosen))

    with pytest.raises(WorkspaceError):
        workspace.resolve("../fora.txt")
    assert workspace.resolve("src/app.py") == (chosen / "src" / "app.py").resolve()


def test_at_root_does_not_create_a_missing_folder(tmp_path: Path) -> None:
    missing = tmp_path / "nao-existe"

    workspace = ConversationWorkspace.at_root(missing)

    assert workspace.root == missing.resolve()
    assert not missing.exists()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/agentic/test_workspace_resolution.py -v`
Expected: FAIL com `ImportError: cannot import name 'resolve_workspace'`

- [ ] **Step 3: Implementar**

Em `src/agentos/agentic/workspace.py`, substitua o `__init__` da classe (linhas 40-43) por:

```python
class ConversationWorkspace:
    def __init__(self, root: Path | str, conversation_id: str) -> None:
        self.root = Path(root).resolve() / _directory_name(conversation_id)
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def at_root(cls, root: Path | str) -> "ConversationWorkspace":
        """Use ``root`` as the working root itself.

        A folder the person chose is already the root: appending a workspace id
        below it would put the agent's work in a subdirectory of their project
        instead of in it. Nothing is created here — the folder exists because it
        was inspected before being accepted.
        """
        workspace = cls.__new__(cls)
        workspace.root = Path(root).resolve()
        return workspace
```

E acrescente, depois da classe (antes do bloco `__all__` se houver):

```python
def resolve_workspace(workspace_id: str, *, managed_root: Path | str, local_root: str | None) -> ConversationWorkspace:
    """The one place that decides where a workspace id lives on disk."""
    if isinstance(local_root, str) and local_root.strip():
        return ConversationWorkspace.at_root(local_root.strip())
    return ConversationWorkspace(managed_root, workspace_id)
```

Se o arquivo terminar com uma lista `__all__`, acrescente `"resolve_workspace"` a ela.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/agentic/test_workspace_resolution.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Rodar a suíte agentic inteira, para provar que nada regrediu**

Run: `python -m pytest tests/unit/agentic -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/workspace.py tests/unit/agentic/test_workspace_resolution.py
git commit -m "feat(workspace): resolve a chosen folder as the working root"
```

---

### Task 6: Rotas de inspeção, anexar e desanexar

**Files:**
- Modify: `src/agentos/api/gateway.py:160-190` (construtor de `ApiServices`), `:419-434` (GET e `conversation_workspace`)
- Test: `tests/unit/api/test_local_workspace_api.py`

- [ ] **Step 1: Escrever os testes que falham**

Crie `tests/unit/api/test_local_workspace_api.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.schema import metadata


class _StubConversations:
    """Only the ownership contract the workspace routes depend on."""

    def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
        if user_id != "owner":
            raise LookupError(conversation_id)
        return {"conversation_id": conversation_id, "title": "Chat", "state": "idle", "project_id": None, "messages": [], "turns": []}


def _client(tmp_path: Path) -> tuple[TestClient, PostgresLocalWorkspaceStore]:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    security = InMemorySecurityService()
    security.add_pat("owner", AuthenticatedPrincipal("owner", "cred", frozenset({"api"})))
    security.add_pat("other", AuthenticatedPrincipal("other", "cred", frozenset({"api"})))
    store = PostgresLocalWorkspaceStore(engine)
    services = ApiServices(security=security, conversation_application=_StubConversations(), local_workspaces=store, workspace_root=tmp_path / "managed")
    return TestClient(create_app(services)), store


def test_inspect_reports_a_typed_folder(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_text("x", encoding="utf-8")

    response = client.post("/v1/conversations/chat_a/workspace/inspect", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i1"}, json={"path": str(folder)})

    assert response.status_code == 200
    body = response.json()
    assert body["is_directory"] is True and body["entry_count"] == 1 and body["risk"] == "none"


def test_attach_requires_acknowledgement_only_for_a_risky_folder(tmp_path: Path) -> None:
    """A broad folder stays possible; it just cannot happen by accident."""
    client, store = _client(tmp_path)
    plain = tmp_path / "site"
    plain.mkdir()

    ok = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i2"}, json={"path": str(plain), "acknowledged_risk": False})
    assert ok.status_code == 200
    assert store.root_for("chat_a", "owner") == str(plain.resolve())

    root = Path(tmp_path.anchor)
    refused = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i3"}, json={"path": str(root), "acknowledged_risk": False})
    assert refused.status_code == 409
    assert store.root_for("chat_a", "owner") == str(plain.resolve())

    accepted = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i4"}, json={"path": str(root), "acknowledged_risk": True})
    assert accepted.status_code == 200
    assert store.root_for("chat_a", "owner") == str(root)


def test_attach_refuses_a_missing_folder(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i5"}, json={"path": str(tmp_path / "nao-existe"), "acknowledged_risk": True})

    assert response.status_code == 422


def test_detach_restores_the_managed_folder_and_is_idempotent(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    folder = tmp_path / "site"
    folder.mkdir()
    client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i6"}, json={"path": str(folder), "acknowledged_risk": False})

    first = client.delete("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i7"})
    second = client.delete("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i8"})

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["kind"] == "managed"
    assert store.root_for("chat_a", "owner") is None


def test_another_user_cannot_read_or_attach(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    folder = tmp_path / "site"
    folder.mkdir()

    assert client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer other", "Idempotency-Key": "i9"}, json={"path": str(folder), "acknowledged_risk": False}).status_code == 404
    assert client.get("/v1/conversations/chat_a", headers={"Authorization": "Bearer other"}).status_code == 404


def test_conversation_get_carries_the_workspace_block(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    folder = tmp_path / "site"
    folder.mkdir()

    before = client.get("/v1/conversations/chat_a", headers={"Authorization": "Bearer owner"}).json()
    assert before["workspace"] == {"kind": "managed", "path": None, "folder_name": None, "scope": "chat", "project_name": None}

    client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i10"}, json={"path": str(folder), "acknowledged_risk": False})
    after = client.get("/v1/conversations/chat_a", headers={"Authorization": "Bearer owner"}).json()
    assert after["workspace"]["kind"] == "local" and after["workspace"]["folder_name"] == "site"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/api/test_local_workspace_api.py -v`
Expected: FAIL com `TypeError: ApiServices.__init__() got an unexpected keyword argument 'local_workspaces'`

- [ ] **Step 3: Aceitar o store em `ApiServices`**

Em `src/agentos/api/gateway.py`, no construtor de `ApiServices`, acrescente o parâmetro depois de `projects: object | None = None,`:

```python
        local_workspaces: object | None = None,
```

e a atribuição depois de `self.projects = projects`:

```python
        self.local_workspaces = local_workspaces
```

- [ ] **Step 4: Adicionar os modelos de request**

Em `src/agentos/api/gateway.py`, junto dos outros modelos Pydantic (perto da linha 56, onde está `workspace_id: str | None = Field(...)`), acrescente:

```python
class InspectWorkspaceFolderRequest(BaseModel):
    path: str | None = Field(default=None, max_length=4096)


class AttachWorkspaceFolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    acknowledged_risk: bool = False
```

- [ ] **Step 5: Implementar as rotas**

Em `src/agentos/api/gateway.py`, substitua o bloco que vai de `@app.get("/v1/conversations/{conversation_id}")` até o fim de `conversation_workspace` (linhas 419-434) por:

```python
    def effective_workspace_id(conversation: dict[str, object], principal: AuthenticatedPrincipal) -> tuple[str, str | None]:
        """The workspace id a chat actually resolves to, plus the project's name.

        A chat inside a project shares the project's workspace, so attaching a
        folder there attaches it for every chat of that project. The name comes
        back so the interface can say so before confirming.
        """
        project_id = conversation.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            return str(conversation.get("conversation_id") or ""), None
        project = require_port(services.projects).get(project_id, principal.user_id)
        data = project if isinstance(project, dict) else {"workspace_id": getattr(project, "workspace_id", None), "name": getattr(project, "name", None)}
        workspace_id = data.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ApplicationNotFoundError(str(conversation.get("conversation_id") or ""))
        name = data.get("name")
        return workspace_id, name if isinstance(name, str) else None

    def local_root_for(workspace_id: str, principal: AuthenticatedPrincipal) -> str | None:
        store = services.local_workspaces
        if store is None:
            return None
        return store.root_for(workspace_id, principal.user_id)

    def workspace_state(conversation: dict[str, object], principal: AuthenticatedPrincipal) -> dict[str, object]:
        workspace_id, project_name = effective_workspace_id(conversation, principal)
        root = local_root_for(workspace_id, principal)
        return {
            "kind": "local" if root else "managed",
            "path": root,
            "folder_name": Path(root).name if root else None,
            "scope": "project" if project_name is not None else "chat",
            "project_name": project_name,
        }

    def conversation_record(conversation_id: str, principal: AuthenticatedPrincipal) -> dict[str, object]:
        conversation = _jsonable(require_port(services.conversation_application).get(conversation_id, principal.user_id))
        if not isinstance(conversation, dict):
            raise ApplicationNotFoundError(conversation_id)
        return conversation

    @app.get("/v1/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(principal, action="conversation.read", resource_id=conversation_id, purpose="conversation.read")
        conversation = conversation_record(conversation_id, principal)
        return JSONResponse({**conversation, "workspace": workspace_state(conversation, principal)})

    @app.post("/v1/conversations/{conversation_id}/workspace/inspect")
    async def inspect_conversation_workspace(conversation_id: str, payload: InspectWorkspaceFolderRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.workspace.inspect")
        conversation_record(conversation_id, principal)
        chosen = payload.path
        if chosen is None:
            client_host = request.client.host if request.client is not None else None
            if not _is_loopback_client(client_host):
                return JSONResponse({"dialog_unavailable": True}, status_code=200)
            result = choose_folder()
            if not result.available:
                return JSONResponse({"dialog_unavailable": True}, status_code=200)
            if result.cancelled or not result.path:
                return JSONResponse({"cancelled": True}, status_code=200)
            chosen = result.path
        try:
            inspection = inspect_folder(chosen, home=Path.home(), orin_data=orin_paths().data)
        except FolderRejected as error:
            raise ApplicationValidationError("invalid workspace folder") from error
        return JSONResponse(asdict(inspection))

    @app.put("/v1/conversations/{conversation_id}/workspace")
    async def attach_conversation_workspace(conversation_id: str, payload: AttachWorkspaceFolderRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.workspace.attach")
        conversation = conversation_record(conversation_id, principal)
        try:
            inspection = inspect_folder(payload.path, home=Path.home(), orin_data=orin_paths().data)
        except FolderRejected as error:
            raise ApplicationValidationError("invalid workspace folder") from error
        if not inspection.is_directory:
            raise ApplicationValidationError("workspace folder does not exist")
        if not inspection.writable:
            raise ApplicationValidationError("workspace folder is not writable")
        if inspection.risk != "none" and not payload.acknowledged_risk:
            return _error(409, "CONFLICT", "workspace_risk_acknowledgement_required", retryable=False)
        workspace_id, _ = effective_workspace_id(conversation, principal)
        _require_port(services.local_workspaces).set_root(workspace_id, principal.user_id, inspection.path)
        return JSONResponse(workspace_state(conversation, principal))

    @app.delete("/v1/conversations/{conversation_id}/workspace")
    async def detach_conversation_workspace(conversation_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.workspace.detach")
        conversation = conversation_record(conversation_id, principal)
        workspace_id, _ = effective_workspace_id(conversation, principal)
        _require_port(services.local_workspaces).clear_root(workspace_id, principal.user_id)
        return JSONResponse(workspace_state(conversation, principal))

    def conversation_workspace(conversation_id: str, principal: AuthenticatedPrincipal) -> ConversationWorkspace:
        conversation = conversation_record(conversation_id, principal)
        workspace_id, _ = effective_workspace_id(conversation, principal)
        return resolve_workspace(workspace_id, managed_root=services.workspace_root, local_root=local_root_for(workspace_id, principal))
```

- [ ] **Step 6: Ajustar os imports do gateway**

No topo de `src/agentos/api/gateway.py`:

- troque `from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError` por `from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError, resolve_workspace`
- acrescente `from agentos.local_workspace import FolderRejected, choose_folder, inspect_folder`
- garanta que `from dataclasses import asdict, is_dataclass` inclui `asdict` (já usado por `_jsonable`) e que `from pathlib import Path` e `from agentos.installation.paths import orin_paths` estão presentes — os três já são usados no arquivo; confirme com `grep -n "^from dataclasses\|^from pathlib\|orin_paths" src/agentos/api/gateway.py`

- [ ] **Step 7: Rodar e ver passar**

Run: `python -m pytest tests/unit/api/test_local_workspace_api.py -v`
Expected: PASS (6 testes)

- [ ] **Step 8: Rodar as suítes de API que tocam conversas**

Run: `python -m pytest tests/unit/api tests/unit/projects tests/unit/conversations -q`
Expected: PASS

- [ ] **Step 9: Ligar o store na composição de produção**

Run: `grep -rn "ApiServices(" src/agentos --include=*.py | grep -v __pycache__`

Em cada composição que constrói `ApiServices` com um `Engine` disponível, passe `local_workspaces=PostgresLocalWorkspaceStore(engine)` e importe `from agentos.local_workspace.store import PostgresLocalWorkspaceStore`. Se a composição não tiver engine, deixe o parâmetro de fora — o gateway já trata `None` devolvendo `kind: "managed"`.

- [ ] **Step 10: Commit**

```bash
git add src/agentos/api/gateway.py src/agentos/bootstrap tests/unit/api/test_local_workspace_api.py
git commit -m "feat(api): attach, inspect and detach a local workspace folder"
```

---

### Task 7: O turno usa a pasta local

**Files:**
- Modify: `src/agentos/conversations/chat.py:313-318` (query de `claim`)
- Modify: `src/agentos/agentic/session.py:264` e `:588`
- Test: `tests/unit/conversations/test_chat_local_workspace.py`

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/unit/conversations/test_chat_local_workspace.py`:

```python
from pathlib import Path

from agentos.agentic.session import resolve_effective_workspace_id
from agentos.agentic.workspace import resolve_workspace


def test_a_project_turn_resolves_the_projects_local_folder(tmp_path: Path) -> None:
    """The turn carries the root so the worker never queries it a second time."""
    chosen = tmp_path / "repo"
    chosen.mkdir()
    turn = {"conversation_id": "chat_a", "project_id": "project_a", "project_workspace_id": "workspace:project_a", "workspace_root_path": str(chosen)}

    workspace = resolve_workspace(resolve_effective_workspace_id(turn), managed_root=tmp_path / "managed", local_root=turn["workspace_root_path"])

    assert workspace.root == chosen.resolve()


def test_a_turn_without_a_local_root_keeps_the_managed_folder(tmp_path: Path) -> None:
    turn = {"conversation_id": "chat_a", "project_id": None, "project_workspace_id": None, "workspace_root_path": None}

    workspace = resolve_workspace(resolve_effective_workspace_id(turn), managed_root=tmp_path / "managed", local_root=turn["workspace_root_path"])

    assert workspace.root == (tmp_path / "managed" / "chat_a").resolve()
```

Depois acrescente, no mesmo arquivo, o teste que exige a coluna nova na query:

```python
def test_claim_brings_the_local_root_on_the_turn() -> None:
    from datetime import UTC, datetime

    from sqlalchemy import create_engine, insert
    from sqlalchemy.pool import StaticPool

    from agentos.conversations.chat import PostgresChatStore
    from agentos.persistence.postgres.schema import metadata, workspace_roots

    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(user_id="owner", message="oi", provider="openrouter", model_id="m", idempotency_key="k1")
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(workspace_roots).values(workspace_id=receipt.conversation_id, user_id="owner", root_path="/tmp/escolhida", created_at=now, updated_at=now))

    turn = store.claim(receipt.turn_id)

    assert turn is not None
    assert turn["workspace_root_path"] == "/tmp/escolhida"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/conversations/test_chat_local_workspace.py -v`
Expected: os dois primeiros PASSAM (a Task 5 já os cobre); `test_claim_brings_the_local_root_on_the_turn` FALHA com `KeyError: 'workspace_root_path'`

- [ ] **Step 3: Trazer a raiz na query de `claim`**

Em `src/agentos/conversations/chat.py`, substitua o `select` dentro de `claim` (linhas 313-318) por:

```python
            effective_workspace_id = func.coalesce(projects.c.workspace_id, conversation_turns.c.conversation_id)
            turn = c.execute(
                select(conversation_turns, conversations.c.project_id, projects.c.workspace_id.label("project_workspace_id"), workspace_roots.c.root_path.label("workspace_root_path"))
                .join(conversations, conversations.c.conversation_id == conversation_turns.c.conversation_id)
                .outerjoin(projects, projects.c.project_id == conversations.c.project_id)
                .outerjoin(workspace_roots, workspace_roots.c.workspace_id == effective_workspace_id)
                .where(conversation_turns.c.turn_id == turn_id)
            ).mappings().one()
```

No topo do arquivo, acrescente `func` ao import do SQLAlchemy e `workspace_roots` ao import do schema. Confirme os imports atuais com `grep -n "^from sqlalchemy\|^from agentos.persistence" src/agentos/conversations/chat.py`.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/conversations/test_chat_local_workspace.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Usar a raiz na sessão do turno**

Em `src/agentos/agentic/session.py`, substitua a linha 264:

```python
        self.workspace = ConversationWorkspace(workspace_root or orin_paths().workspaces, resolve_effective_workspace_id(turn))
```

por:

```python
        local_root = turn.get("workspace_root_path")
        self.workspace_is_local = isinstance(local_root, str) and bool(local_root.strip())
        self.workspace = resolve_workspace(
            resolve_effective_workspace_id(turn),
            managed_root=workspace_root or orin_paths().workspaces,
            local_root=local_root if isinstance(local_root, str) else None,
        )
```

Troque o import no topo do arquivo de `from agentos.agentic.workspace import ConversationWorkspace` para incluir `resolve_workspace` (confirme a forma exata com `grep -n "from agentos.agentic.workspace import" src/agentos/agentic/session.py`).

- [ ] **Step 6: Contar ao agente que a pasta é da pessoa**

Em `src/agentos/agentic/session.py`, substitua a linha 588:

```python
            workspace_hint="Files you create there persist for the whole conversation.",
```

por:

```python
            workspace_hint=(
                "This directory is a folder on the user's own machine that they attached to this chat. "
                "Files already in it are theirs: do not reorganise, move or delete anything you were not asked to change."
                if self.workspace_is_local
                else "Files you create there persist for the whole conversation."
            ),
```

- [ ] **Step 7: Rodar as suítes afetadas**

Run: `python -m pytest tests/unit/agentic tests/unit/conversations tests/unit/workers -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/agentos/conversations/chat.py src/agentos/agentic/session.py tests/unit/conversations/test_chat_local_workspace.py
git commit -m "feat(agentic): run a turn inside the attached local folder"
```

---

### Task 8: Cliente HTTP no frontend

**Files:**
- Create: `frontend/src/api/workspace.ts`
- Modify: `frontend/src/api/conversations.ts:20-22` (tipo `Conversation` e `parseConversation`)
- Test: `frontend/tests/unit/workspaceApi.test.ts`

- [ ] **Step 1: Escrever os testes que falham**

Crie `frontend/tests/unit/workspaceApi.test.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { attachWorkspaceFolder, detachWorkspaceFolder, inspectWorkspaceFolder } from '../../src/api/workspace'

function clientWith(response: unknown, status = 200) {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify(response), { status, headers: { 'Content-Type': 'application/json' } }))
  return { client: new ApiClient({ fetchImpl }), fetchImpl }
}

describe('workspace api', () => {
  it('reads an inspection', async () => {
    const { client } = clientWith({ path: 'D:/site', exists: true, is_directory: true, writable: true, entry_count: 3, entries_truncated: false, risk: 'none' })

    const result = await inspectWorkspaceFolder(client, 'chat_a', null)

    expect(result).toEqual({ kind: 'folder', path: 'D:/site', exists: true, isDirectory: true, writable: true, entryCount: 3, entriesTruncated: false, risk: 'none' })
  })

  it('reads a cancelled dialog and an unavailable dialog', async () => {
    const cancelled = clientWith({ cancelled: true })
    expect(await inspectWorkspaceFolder(cancelled.client, 'chat_a', null)).toEqual({ kind: 'cancelled' })

    const unavailable = clientWith({ dialog_unavailable: true })
    expect(await inspectWorkspaceFolder(unavailable.client, 'chat_a', null)).toEqual({ kind: 'unavailable' })
  })

  it('sends the acknowledgement when attaching', async () => {
    const { client, fetchImpl } = clientWith({ kind: 'local', path: 'C:/', folder_name: 'C:', scope: 'chat', project_name: null })

    const state = await attachWorkspaceFolder(client, 'chat_a', 'C:/', true)

    expect(state).toEqual({ kind: 'local', path: 'C:/', folderName: 'C:', scope: 'chat', projectName: null })
    const init = fetchImpl.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({ path: 'C:/', acknowledged_risk: true })
  })

  it('reads the state returned by detach', async () => {
    const { client } = clientWith({ kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null })

    expect(await detachWorkspaceFolder(client, 'chat_a')).toEqual({ kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null })
  })
})
```

Se a assinatura de `fetchImpl` no `ApiClient` receber `(input, init)` em vez de um `Request`, ajuste a última asserção para ler `init.body`. Confirme com `grep -n "fetchImpl(" frontend/src/api/client.ts`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `npm --prefix frontend run test -- workspaceApi`
Expected: FAIL com `Failed to resolve import "../../src/api/workspace"`

- [ ] **Step 3: Implementar**

Crie `frontend/src/api/workspace.ts`:

```typescript
import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type WorkspaceRisk = 'none' | 'drive_root' | 'system' | 'home_root' | 'orin_data'

export type WorkspaceState = {
  kind: 'managed' | 'local'
  path: string | null
  folderName: string | null
  scope: 'chat' | 'project'
  projectName: string | null
}

export type FolderInspection = {
  kind: 'folder'
  path: string
  exists: boolean
  isDirectory: boolean
  writable: boolean
  entryCount: number
  entriesTruncated: boolean
  risk: WorkspaceRisk
}

/** The dialog was closed without a choice, or it could not be opened at all. */
export type InspectionOutcome = FolderInspection | { kind: 'cancelled' } | { kind: 'unavailable' }

export function inspectWorkspaceFolder(client: ApiClient, conversationId: string, path: string | null, intent = client.createMutationIntent()): Promise<InspectionOutcome> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace/inspect`, method: 'POST', intent,
    body: { path },
    parse: parseInspection,
  })
}

export function attachWorkspaceFolder(client: ApiClient, conversationId: string, path: string, acknowledgedRisk: boolean, intent = client.createMutationIntent()): Promise<WorkspaceState> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace`, method: 'PUT', intent,
    body: { path, acknowledged_risk: acknowledgedRisk },
    parse: parseWorkspaceState,
  })
}

export function detachWorkspaceFolder(client: ApiClient, conversationId: string, intent = client.createMutationIntent()): Promise<WorkspaceState> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace`, method: 'DELETE', intent,
    parse: parseWorkspaceState,
  })
}

export function parseWorkspaceState(value: unknown): WorkspaceState {
  const data = record(value)
  const kind = data.kind === 'local' ? 'local' : 'managed'
  return {
    kind,
    path: typeof data.path === 'string' ? data.path : null,
    folderName: typeof data.folder_name === 'string' ? data.folder_name : null,
    scope: data.scope === 'project' ? 'project' : 'chat',
    projectName: typeof data.project_name === 'string' ? data.project_name : null,
  }
}

function parseInspection(value: unknown): InspectionOutcome {
  const data = record(value)
  if (data.cancelled === true) return { kind: 'cancelled' }
  if (data.dialog_unavailable === true) return { kind: 'unavailable' }
  if (typeof data.path !== 'string') throw invalidResponseError()
  return {
    kind: 'folder',
    path: data.path,
    exists: data.exists === true,
    isDirectory: data.is_directory === true,
    writable: data.writable === true,
    entryCount: typeof data.entry_count === 'number' ? data.entry_count : 0,
    entriesTruncated: data.entries_truncated === true,
    risk: RISKS.includes(data.risk as WorkspaceRisk) ? (data.risk as WorkspaceRisk) : 'none',
  }
}

const RISKS: WorkspaceRisk[] = ['none', 'drive_root', 'system', 'home_root', 'orin_data']

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}
```

- [ ] **Step 4: Expor o bloco `workspace` no tipo da conversa**

Em `frontend/src/api/conversations.ts`:

- acrescente ao topo `import { parseWorkspaceState, type WorkspaceState } from './workspace'`
- acrescente `workspace: WorkspaceState` ao tipo `Conversation`
- em `parseConversation`, acrescente ao objeto devolvido `workspace: parseWorkspaceState(data.workspace ?? {})`

Localize `parseConversation` com `grep -n "function parseConversation" -A 20 frontend/src/api/conversations.ts` e mantenha o estilo das outras propriedades.

- [ ] **Step 5: Rodar e ver passar**

Run: `npm --prefix frontend run test -- workspaceApi`
Expected: PASS (4 testes)

- [ ] **Step 6: Rodar tipos e lint**

Run: `npm --prefix frontend run build`
Expected: build sem erros de TypeScript

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/workspace.ts frontend/src/api/conversations.ts frontend/tests/unit/workspaceApi.test.ts
git commit -m "feat(web): call the workspace folder routes"
```

---

### Task 9: Botão de pasta no composer

**Files:**
- Create: `frontend/src/features/conversations/WorkspaceFolderButton.tsx`
- Modify: `frontend/src/styles/agentos.css` (depois de `.composer__settings`, linha 233)
- Test: `frontend/tests/unit/WorkspaceFolderButton.test.tsx`

- [ ] **Step 1: Escrever os testes que falham**

Crie `frontend/tests/unit/WorkspaceFolderButton.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkspaceFolderButton } from '../../src/features/conversations/WorkspaceFolderButton'
import type { InspectionOutcome, WorkspaceState } from '../../src/api/workspace'

const managed: WorkspaceState = { kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null }
const plainFolder: InspectionOutcome = { kind: 'folder', path: 'D:/site', exists: true, isDirectory: true, writable: true, entryCount: 4, entriesTruncated: false, risk: 'none' }
const driveRoot: InspectionOutcome = { kind: 'folder', path: 'C:/', exists: true, isDirectory: true, writable: true, entryCount: 12, entriesTruncated: false, risk: 'drive_root' }

function api(overrides: Partial<Parameters<typeof WorkspaceFolderButton>[0]> = {}) {
  return {
    state: managed,
    onInspect: vi.fn(async () => plainFolder),
    onAttach: vi.fn(async (path: string) => ({ ...managed, kind: 'local' as const, path, folderName: 'site' })),
    onDetach: vi.fn(async () => managed),
    onChange: vi.fn(),
    ...overrides,
  }
}

describe('WorkspaceFolderButton', () => {
  it('labels the managed state and the attached folder', () => {
    const { rerender } = render(<WorkspaceFolderButton {...api()} />)
    expect(screen.getByRole('button', { name: /pasta/i })).toBeInTheDocument()

    rerender(<WorkspaceFolderButton {...api({ state: { kind: 'local', path: 'D:/site', folderName: 'site', scope: 'chat', projectName: null } })} />)
    expect(screen.getByRole('button', { name: /site/ })).toHaveAttribute('title', 'D:/site')
  })

  it('attaches a plain folder after one confirmation', async () => {
    const props = api()
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /pasta/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Escolher pasta…' }))

    await waitFor(() => expect(screen.getByText('D:/site')).toBeInTheDocument())
    expect(screen.getByText(/4 itens/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Usar esta pasta' }))

    await waitFor(() => expect(props.onAttach).toHaveBeenCalledWith('D:/site', false))
  })

  it('names the risk and requires a deliberate click for a broad folder', async () => {
    const props = api({ onInspect: vi.fn(async () => driveRoot) })
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /pasta/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Escolher pasta…' }))

    await waitFor(() => expect(screen.getByText(/criar, editar e apagar arquivos em C:\//)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Usar esta pasta' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Trabalhar em C:/ mesmo assim' }))

    await waitFor(() => expect(props.onAttach).toHaveBeenCalledWith('C:/', true))
  })

  it('falls back to the path field when the dialog is unavailable', async () => {
    const props = api({ onInspect: vi.fn(async (path: string | null) => (path === null ? { kind: 'unavailable' as const } : plainFolder)) })
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /pasta/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Escolher pasta…' }))

    await waitFor(() => expect(screen.getByText(/não foi possível abrir o seletor/i)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Caminho da pasta'), { target: { value: 'D:/site' } })
    fireEvent.click(screen.getByRole('button', { name: 'Usar' }))

    await waitFor(() => expect(screen.getByText('D:/site')).toBeInTheDocument())
  })

  it('says a project folder covers every chat of the project', async () => {
    const props = api({ state: { kind: 'managed', path: null, folderName: null, scope: 'project', projectName: 'Site novo' } })
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /pasta/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Escolher pasta…' }))

    await waitFor(() => expect(screen.getByText(/todos os chats do projeto Site novo/)).toBeInTheDocument())
  })

  it('detaches and reports the new state', async () => {
    const props = api({ state: { kind: 'local', path: 'D:/site', folderName: 'site', scope: 'chat', projectName: null } })
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /site/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remover' }))

    await waitFor(() => expect(props.onDetach).toHaveBeenCalledTimes(1))
    expect(props.onChange).toHaveBeenCalledWith(managed)
  })
})
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `npm --prefix frontend run test -- WorkspaceFolderButton`
Expected: FAIL com `Failed to resolve import "../../src/features/conversations/WorkspaceFolderButton"`

- [ ] **Step 3: Implementar**

Crie `frontend/src/features/conversations/WorkspaceFolderButton.tsx`:

```tsx
import { useState } from 'react'
import type { FolderInspection, InspectionOutcome, WorkspaceRisk, WorkspaceState } from '../../api/workspace'

export type WorkspaceFolderButtonProps = {
  state: WorkspaceState
  onInspect: (path: string | null) => Promise<InspectionOutcome>
  onAttach: (path: string, acknowledgedRisk: boolean) => Promise<WorkspaceState>
  onDetach: () => Promise<WorkspaceState>
  onChange: (state: WorkspaceState) => void
}

const RISK_SENTENCE: Record<Exclude<WorkspaceRisk, 'none'>, string> = {
  drive_root: 'é um drive inteiro',
  system: 'é uma pasta de sistema',
  home_root: 'é a sua pasta pessoal inteira',
  orin_data: 'é a pasta de dados do próprio Orin',
}

/**
 * Attaching a folder is a small action with a large consequence, so the button
 * stays quiet and the panel does the talking. No folder is refused: a broad
 * choice only costs a second, named click, which is what keeps it deliberate
 * instead of accidental.
 */
export function WorkspaceFolderButton({ state, onInspect, onAttach, onDetach, onChange }: WorkspaceFolderButtonProps) {
  const [open, setOpen] = useState(false)
  const [candidate, setCandidate] = useState<FolderInspection | null>(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const label = state.kind === 'local' ? (state.folderName ?? 'Pasta') : 'Pasta'

  async function inspect(path: string | null) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const outcome = await onInspect(path)
      if (outcome.kind === 'cancelled') return
      if (outcome.kind === 'unavailable') { setNotice('Não foi possível abrir o seletor do sistema. Cole o caminho da pasta abaixo.'); return }
      if (!outcome.isDirectory) { setError(outcome.exists ? 'Esse caminho não é uma pasta.' : 'Essa pasta não existe.'); return }
      if (!outcome.writable) { setError('Sem permissão de escrita nessa pasta.'); return }
      setCandidate(outcome)
    } catch {
      setError('Não foi possível inspecionar a pasta.')
    } finally {
      setBusy(false)
    }
  }

  async function attach(inspection: FolderInspection) {
    setBusy(true); setError(null)
    try {
      onChange(await onAttach(inspection.path, inspection.risk !== 'none'))
      setCandidate(null); setOpen(false)
    } catch {
      setError('Não foi possível usar essa pasta.')
    } finally {
      setBusy(false)
    }
  }

  async function detach() {
    setBusy(true); setError(null)
    try {
      onChange(await onDetach())
      setOpen(false)
    } catch {
      setError('Não foi possível remover a pasta.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="workspace-folder">
      <button
        type="button"
        className={`workspace-folder__button${state.kind === 'local' ? ' is-attached' : ''}`}
        onClick={() => setOpen((value) => !value)}
        title={state.path ?? undefined}
        aria-expanded={open}
      >
        <span aria-hidden="true">🗀</span> {label}
      </button>

      {open && (
        <div className="workspace-folder__panel" role="dialog" aria-label="Pasta de trabalho">
          {candidate ? (
            <>
              <p className="workspace-folder__path">{candidate.path}</p>
              <p className="workspace-folder__meta">{candidate.entryCount}{candidate.entriesTruncated ? '+' : ''} itens no primeiro nível</p>
              {state.scope === 'project' && state.projectName && (
                <p className="workspace-folder__meta">Vale para todos os chats do projeto {state.projectName}.</p>
              )}
              {candidate.risk === 'none' ? (
                <button type="button" disabled={busy} onClick={() => void attach(candidate)}>Usar esta pasta</button>
              ) : (
                <>
                  <p className="workspace-folder__risk">
                    Essa pasta {RISK_SENTENCE[candidate.risk]}. O agente vai poder criar, editar e apagar arquivos em {candidate.path}, com shell real, sem pedir permissão a cada passo.
                  </p>
                  <button type="button" className="workspace-folder__risk-action" disabled={busy} onClick={() => void attach(candidate)}>
                    Trabalhar em {candidate.path} mesmo assim
                  </button>
                </>
              )}
              <button type="button" disabled={busy} onClick={() => setCandidate(null)}>Cancelar</button>
            </>
          ) : (
            <>
              {state.kind === 'local' ? (
                <p className="workspace-folder__path">{state.path}</p>
              ) : (
                <p className="workspace-folder__meta">Sem pasta local. O agente trabalha na pasta gerenciada pelo Orin.</p>
              )}
              <button type="button" disabled={busy} onClick={() => void inspect(null)}>Escolher pasta…</button>
              <label className="workspace-folder__field">
                Caminho da pasta
                <input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="D:\projetos\site" />
              </label>
              <button type="button" disabled={busy || !typed.trim()} onClick={() => void inspect(typed.trim())}>Usar</button>
              {state.kind === 'local' && <button type="button" disabled={busy} onClick={() => void detach()}>Remover</button>}
            </>
          )}
          {notice && <p className="workspace-folder__notice" role="status">{notice}</p>}
          {error && <p className="workspace-folder__error" role="alert">{error}</p>}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `npm --prefix frontend run test -- WorkspaceFolderButton`
Expected: PASS (6 testes)

- [ ] **Step 5: Estilos**

Em `frontend/src/styles/agentos.css`, logo depois da linha `.composer__settings { min-width: 0; }`, acrescente:

```css
.workspace-folder { position: relative; }
.workspace-folder__button { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--orin-line); background: transparent; color: var(--orin-muted); font-size: 12px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.workspace-folder__button:hover { color: var(--orin-ink); border-color: var(--orin-accent-soft); }
.workspace-folder__button.is-attached { color: var(--orin-ink); border-color: var(--orin-accent-soft); }
.workspace-folder__panel { position: absolute; bottom: calc(100% + 8px); left: 0; z-index: 20; display: flex; flex-direction: column; gap: 8px; width: 320px; padding: 12px; border-radius: 12px; border: 1px solid var(--orin-line); background: var(--orin-surface); box-shadow: 0 12px 32px rgba(0, 0, 0, 0.28); }
.workspace-folder__path { font-family: ui-monospace, monospace; font-size: 12px; word-break: break-all; color: var(--orin-ink); }
.workspace-folder__meta { font-size: 12px; color: var(--orin-muted); }
.workspace-folder__risk { font-size: 12px; color: var(--danger); }
.workspace-folder__risk-action { border-color: var(--danger); color: var(--danger); }
.workspace-folder__field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--orin-muted); }
.workspace-folder__field input { padding: 6px 8px; border-radius: 8px; border: 1px solid var(--orin-line); background: transparent; color: var(--orin-ink); font-family: ui-monospace, monospace; font-size: 12px; }
.workspace-folder__notice { font-size: 12px; color: var(--orin-muted); }
.workspace-folder__error { font-size: 12px; color: var(--danger); }
```

Confirme que as variáveis `--orin-surface`, `--orin-line`, `--orin-muted`, `--orin-ink`, `--orin-accent-soft` e `--danger` existem: `grep -n "\-\-orin-surface\|--orin-line\|--orin-muted\|--danger" frontend/src/styles/theme.css | head`. Se algum nome divergir, use o equivalente do arquivo.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/conversations/WorkspaceFolderButton.tsx frontend/src/styles/agentos.css frontend/tests/unit/WorkspaceFolderButton.test.tsx
git commit -m "feat(web): add a folder button to the composer"
```

---

### Task 10: Ligar o botão no chat

**Files:**
- Modify: `frontend/src/features/conversations/ChatPage.tsx:319-328`
- Test: `frontend/tests/unit/ChatPage.test.tsx` (acrescentar um caso)

- [ ] **Step 1: Escrever o teste que falha**

Em `frontend/tests/unit/ChatPage.test.tsx`, a conversa de teste vem de `snapshotBody(snapshot)` (por volta da linha 31). Acrescente o bloco `workspace` ao objeto devolvido por ela, com um parâmetro opcional para o caso novo:

```tsx
function snapshotBody(snapshot: Snapshot, workspace: Record<string, unknown> = { kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null }) {
  return {
    conversation_id: CONVERSATION_ID,
    title: 'Conversa de teste',
    state: snapshot.state,
    provider: 'openrouter',
    model_id: 'model-a',
    messages: snapshot.messages,
    turns: [{ turn_id: 'turn-1', state: snapshot.state, created_at: '2026-08-10T20:00:00+00:00', started_at: null, finished_at: null }],
    activities: snapshot.activities,
    activity_cursor: 'a.9',
    workspace,
  }
}
```

Todas as chamadas existentes continuam válidas por causa do valor padrão. Acrescente ao fim do `describe` existente:

```tsx
  it('shows the attached folder next to the composer', async () => {
    const snapshot: Snapshot = { state: 'idle', messages: [], activities: [] }
    const local = { kind: 'local', path: 'D:/site', folder_name: 'site', scope: 'chat', project_name: null }
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
      return new Response(JSON.stringify(snapshotBody(snapshot, local)), { headers: { 'Content-Type': 'application/json' } })
    })

    render(
      <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
        <Routes><Route path="/chats/:conversationId" element={<ChatPage client={new ApiClient({ fetchImpl })} />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('button', { name: /site/ })).toBeInTheDocument()
  })
```

Compare o `render` acima com o helper de renderização que os outros casos do arquivo já usam (`grep -n "MemoryRouter" frontend/tests/unit/ChatPage.test.tsx`) e reaproveite-o em vez de duplicar, se ele aceitar um `fetchImpl` próprio. Importe `ApiClient` de `../../src/api/client` se ainda não estiver importado.

- [ ] **Step 2: Rodar e ver falhar**

Run: `npm --prefix frontend run test -- ChatPage`
Expected: FAIL — o botão não existe na página

- [ ] **Step 3: Ligar o botão**

Em `frontend/src/features/conversations/ChatPage.tsx`:

- importe `import { WorkspaceFolderButton } from './WorkspaceFolderButton'` e `import { attachWorkspaceFolder, detachWorkspaceFolder, inspectWorkspaceFolder, type WorkspaceState } from '../../api/workspace'`
- acrescente o estado, ao lado dos outros `useState` da página:

```tsx
  const [workspace, setWorkspace] = useState<WorkspaceState>({ kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null })
```

- onde a conversa carregada é aplicada ao estado (o mesmo lugar que preenche mensagens e turnos), acrescente `setWorkspace(conversation.workspace)`
- passe o botão pelo slot `settings` do `Composer`, que hoje está livre nesta página:

```tsx
        <Composer
          value={message}
          onChange={setMessage}
          onSubmit={() => void submit()}
          onStop={() => void stop()}
          running={running}
          error={error}
          hint={stopping ? 'parando…' : undefined}
          placeholder="Continue a conversa…"
          settings={
            <WorkspaceFolderButton
              state={workspace}
              onInspect={(path) => inspectWorkspaceFolder(client, conversationId, path)}
              onAttach={(path, acknowledged) => attachWorkspaceFolder(client, conversationId, path, acknowledged)}
              onDetach={() => detachWorkspaceFolder(client, conversationId)}
              onChange={setWorkspace}
            />
          }
        />
```

Confirme os nomes exatos de `client` e `conversationId` na página com `grep -n "const client\|conversationId" frontend/src/features/conversations/ChatPage.tsx | head`.

- [ ] **Step 4: Rodar e ver passar**

Run: `npm --prefix frontend run test -- ChatPage`
Expected: PASS

- [ ] **Step 5: Rodar a suíte inteira do frontend e o build**

Run: `npm --prefix frontend run test`
Expected: PASS

Run: `npm --prefix frontend run build`
Expected: build sem erros

Run: `npm --prefix frontend run lint`
Expected: sem warnings

- [ ] **Step 6: Rodar a suíte Python inteira**

Run: `python -m pytest tests -q`
Expected: PASS, exceto as duas falhas de persistência já documentadas no README. Confirme que são as mesmas de antes com `git stash && python -m pytest tests -q; git stash pop` se houver dúvida.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/conversations/ChatPage.tsx frontend/tests/unit/ChatPage.test.tsx
git commit -m "feat(web): attach a local folder from the chat composer"
```

---

### Task 11: Verificação de ponta a ponta

**Files:** nenhum arquivo novo; esta task é a prova de que a funcionalidade funciona no app real.

- [ ] **Step 1: Aplicar a migração**

Run: `python -m alembic upgrade head`
Expected: `Running upgrade 0029_conversation_tool_records -> 0030_workspace_roots`

- [ ] **Step 2: Subir o Orin**

Run: `.\scripts\run-local.ps1`
Expected: API em `127.0.0.1:8000` e cliente web acessível

- [ ] **Step 3: Anexar uma pasta comum**

Crie uma pasta de teste com um arquivo dentro, abra um chat, clique no botão de pasta, escolha "Escolher pasta…", selecione a pasta e confirme. Peça ao agente "liste os arquivos desta pasta e crie um `notas.md` com o que você encontrou".

Expected: `notas.md` aparece na pasta escolhida, visível pelo Explorer, e não em `orin_paths().workspaces`.

- [ ] **Step 4: Confirmar a segunda confirmação**

No mesmo chat, escolha `C:\` pelo campo de caminho e clique em "Usar".

Expected: o painel mostra a frase de risco e o botão "Trabalhar em C:\ mesmo assim"; nenhum botão "Usar esta pasta" aparece; clicar no botão nomeado anexa a pasta.

- [ ] **Step 5: Remover e conferir a volta**

Clique em "Remover" e peça ao agente para listar os arquivos.

Expected: o chat volta a ver os arquivos que tinha antes de anexar a pasta; a pasta local fica intacta.

- [ ] **Step 6: Conferir o escopo de projeto**

Abra um chat dentro de um projeto, anexe uma pasta e abra outro chat do mesmo projeto.

Expected: o botão do segundo chat já mostra a mesma pasta.

- [ ] **Step 7: Commit da evidência**

Se algum ajuste for necessário nos passos acima, corrija, rode `python -m pytest tests -q` e `npm --prefix frontend run test`, e faça um commit descrevendo o ajuste. Se nada precisar de ajuste, não há commit nesta task.

---

## Notas para quem executa

- O ambiente Python é o `.venv` da raiz. Se `python` não for o do venv, use `.venv\Scripts\python` no PowerShell ou `.venv/Scripts/python` no bash.
- Os testes de API usam SQLite em memória com `metadata.create_all`, não a migração. A migração é verificada separadamente na Task 11, passo 1.
- Nenhum passo deste plano bloqueia uma pasta por política. Se você se pegar escrevendo um `raise` para recusar `C:\`, releia a seção "Risco, sem bloqueio" do spec: a decisão foi que a escolha ampla é possível e só precisa ser deliberada.
- `agentos.workspaces` é outra coisa (RFC 603). Não acrescente nada deste plano lá.
