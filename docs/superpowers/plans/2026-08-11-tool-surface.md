# Tool Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar ao agente busca de conteúdo, leitura paginada, listagem recursiva e edição em lote, eliminando a maior fonte de rodadas desperdiçadas e o loop de erro do `edit_file`.

**Architecture:** Toda a capacidade nova nasce em `ConversationWorkspace` (que já é o único ponto que valida caminhos contra o sandbox) e é exposta por novas `ToolDefinition` em `AgentToolset`. Nenhuma tool nova toca disco fora do workspace, e nenhuma delas altera o contrato de `ToolOutcome`.

**Tech Stack:** Python 3.12, `pathlib`, `re`, pytest.

## Global Constraints

- Nome público do produto é **Orin**; identificadores internos permanecem `agentos`.
- Nenhuma tool pode ler ou escrever fora de `ConversationWorkspace.root`; todo caminho passa por `ConversationWorkspace.resolve`.
- Todo módulo novo/alterado começa com `from __future__ import annotations`.
- Resultados devolvidos ao modelo continuam limitados por `MAX_TOOL_RESULT_CHARS` (12.000).
- Descrições de tool em inglês (o restante do catálogo é em inglês); `summary` de `ToolOutcome` em português (padrão atual da UI).
- Rodar testes com `uv run pytest <caminho> -v`.

---

### Task 1: Busca de conteúdo no workspace

**Files:**
- Modify: `src/agentos/agentic/workspace.py`
- Modify: `src/agentos/agentic/agent_tools.py:201-228`
- Test: `tests/unit/agentic/test_workspace_search.py`

**Interfaces:**
- Consumes: `ConversationWorkspace.resolve`, `ConversationWorkspace.relative`
- Produces:
  - `ConversationWorkspace.search(pattern: str, *, glob: str = "**/*", max_results: int = 50, ignore_case: bool = True) -> list[dict[str, object]]` — cada item `{"path": str, "line": int, "text": str}`
  - tool `search_files` com argumentos `{pattern, glob?, max_results?, ignore_case?}`

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/agentic/test_workspace_search.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError


@pytest.fixture()
def workspace(tmp_path: Path) -> ConversationWorkspace:
    return ConversationWorkspace(tmp_path, "chat_search")


def test_search_finds_matches_with_path_and_line_number(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.py", "import os\nDEBUG = True\n")
    workspace.write_text("docs/readme.md", "nothing here\n")

    results = workspace.search("DEBUG")

    assert results == [{"path": "src/app.py", "line": 2, "text": "DEBUG = True"}]


def test_search_respects_the_glob_filter(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.py", "target\n")
    workspace.write_text("docs/readme.md", "target\n")

    results = workspace.search("target", glob="**/*.md")

    assert [item["path"] for item in results] == ["docs/readme.md"]


def test_search_is_case_insensitive_by_default_and_can_be_made_exact(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.py", "Debug\n")

    assert workspace.search("debug")
    assert workspace.search("debug", ignore_case=False) == []


def test_search_caps_the_number_of_results(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.py", "hit\n" * 40)

    assert len(workspace.search("hit", max_results=5)) == 5


def test_search_rejects_an_invalid_regular_expression(workspace: ConversationWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.search("[unclosed")


def test_search_rejects_a_glob_that_tries_to_escape_the_workspace(workspace: ConversationWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.search("anything", glob="../**/*")
```

- [ ] **Step 2: Rodar o teste e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_workspace_search.py -v`
Expected: FAIL com `AttributeError: 'ConversationWorkspace' object has no attribute 'search'`

- [ ] **Step 3: Implementar `search` no workspace**

Em `src/agentos/agentic/workspace.py`, adicionar as constantes junto das existentes (após `MAX_SNAPSHOT_FILES = 1_000`):

```python
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_FILE_BYTES = 2_000_000
MAX_SEARCH_LINE_CHARS = 400
```

E o método dentro de `ConversationWorkspace`, depois de `list_entries`:

```python
    def search(self, pattern: str, *, glob: str = "**/*", max_results: int = 50, ignore_case: bool = True) -> list[dict[str, object]]:
        """Scan workspace text files for a regular expression.

        The glob is validated before it reaches ``Path.glob`` because a pattern
        containing ``..`` would otherwise walk out of the sandbox that every
        other method is careful to enforce.
        """
        if not isinstance(pattern, str) or not pattern.strip():
            raise WorkspaceError("pattern must be a non-blank string")
        if not isinstance(glob, str) or not glob.strip() or ".." in glob or glob.startswith("/") or "\\" in glob:
            raise WorkspaceError("glob must be a relative pattern without '..'")
        try:
            expression = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as error:
            raise WorkspaceError(f"pattern is not a valid regular expression: {error}") from error
        limit = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
        matches: list[dict[str, object]] = []
        for item in sorted(self.root.glob(glob)):
            if len(matches) >= limit:
                break
            try:
                resolved = item.resolve()
                relative = resolved.relative_to(self.root).as_posix()
                if not resolved.is_file() or resolved.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                text = resolved.read_bytes()[:MAX_SEARCH_FILE_BYTES].decode("utf-8", "replace")
            except (OSError, ValueError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if len(matches) >= limit:
                    break
                if expression.search(line):
                    matches.append({"path": relative, "line": number, "text": line.strip()[:MAX_SEARCH_LINE_CHARS]})
        return matches
```

Atualizar `__all__` no fim do arquivo:

```python
__all__ = [
    "ConversationWorkspace",
    "MAX_READ_BYTES",
    "MAX_SEARCH_FILE_BYTES",
    "MAX_SEARCH_LINE_CHARS",
    "MAX_SEARCH_RESULTS",
    "MAX_SNAPSHOT_FILES",
    "MAX_WRITE_BYTES",
    "WorkspaceError",
]
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_workspace_search.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Escrever o teste da tool `search_files`**

Acrescentar em `tests/unit/agentic/test_agent_tools.py`:

```python
def test_search_files_returns_matches_the_model_can_read(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "src/app.py", "content": "import os\nDEBUG = True\n"})

    outcome = toolset.invoke("search_files", {"pattern": "DEBUG"})

    assert outcome.status == "succeeded"
    assert "src/app.py:2" in outcome.content
    assert outcome.payload["count"] == 1


def test_search_files_reports_no_match_without_failing(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "src/app.py", "content": "nothing\n"})

    outcome = toolset.invoke("search_files", {"pattern": "DEBUG"})

    assert outcome.status == "succeeded"
    assert outcome.payload["count"] == 0
```

- [ ] **Step 6: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py -k search_files -v`
Expected: FAIL — `outcome.error_code == "UNKNOWN_TOOL"`

- [ ] **Step 7: Registrar a tool**

Em `src/agentos/agentic/agent_tools.py`, dentro de `definitions()`, acrescentar ao final da lista `items` inicial (logo após a entrada de `list_files`):

```python
            ToolDefinition(
                "search_files",
                "Search file contents in the conversation workspace with a regular expression. Use this before reading files when you do not know where something is.",
                _schema({
                    "pattern": {**_TEXT, "description": "Python regular expression."},
                    "glob": {**_TEXT, "description": "Relative glob filter, e.g. '**/*.py'. Defaults to every file."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                    "ignore_case": {"type": "boolean"},
                }, ("pattern",)),
                self.search_files, "filesystem",
            ),
```

E o handler, logo após `list_files`:

```python
    def search_files(self, pattern: str, glob: str = "**/*", max_results: int = 50, ignore_case: bool = True) -> dict[str, Any]:
        matches = self.workspace.search(pattern, glob=glob, max_results=int(max_results), ignore_case=bool(ignore_case))
        if not matches:
            body = "[no match]"
        else:
            body = "\n".join(f"{item['path']}:{item['line']}: {item['text']}" for item in matches)
        return {
            "summary": f"Buscou '{pattern[:40]}': {len(matches)} {'ocorrência' if len(matches) == 1 else 'ocorrências'}",
            "content": body,
            "payload": {"pattern": pattern[:200], "count": len(matches), "label": pattern[:80]},
        }
```

- [ ] **Step 8: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py -v`
Expected: PASS (todos, incluindo os pré-existentes)

- [ ] **Step 9: Commit**

```bash
git add src/agentos/agentic/workspace.py src/agentos/agentic/agent_tools.py tests/unit/agentic/test_workspace_search.py tests/unit/agentic/test_agent_tools.py
git commit -m "feat(tools): add workspace content search"
```

---

### Task 2: Leitura paginada de arquivos

**Files:**
- Modify: `src/agentos/agentic/workspace.py`
- Modify: `src/agentos/agentic/agent_tools.py:203-207` (definição de `read_file`) e `:329-335` (handler)
- Test: `tests/unit/agentic/test_workspace_search.py` (mesmo arquivo, seção de leitura)

**Interfaces:**
- Consumes: `ConversationWorkspace.resolve`, `MAX_READ_BYTES`
- Produces:
  - `ConversationWorkspace.read_lines(path: str, *, offset: int = 1, limit: int = 400) -> tuple[list[str], int, int, bool]` — `(linhas, primeira_linha_1based, total_de_linhas, truncado_por_bytes)`
  - tool `read_file` com argumentos `{path, offset?, limit?}`; conteúdo vem numerado como `   12\ttexto`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_workspace_search.py`:

```python
def test_read_lines_returns_a_window_and_the_total(workspace: ConversationWorkspace) -> None:
    workspace.write_text("big.txt", "\n".join(f"line {index}" for index in range(1, 101)) + "\n")

    lines, first, total, truncated = workspace.read_lines("big.txt", offset=10, limit=3)

    assert lines == ["line 10", "line 11", "line 12"]
    assert (first, total, truncated) == (10, 100, False)


def test_read_lines_clamps_an_offset_past_the_end(workspace: ConversationWorkspace) -> None:
    workspace.write_text("small.txt", "only\n")

    lines, first, total, _ = workspace.read_lines("small.txt", offset=99)

    assert lines == []
    assert (first, total) == (99, 1)


def test_read_lines_rejects_a_non_file(workspace: ConversationWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.read_lines("missing.txt")
```

E em `tests/unit/agentic/test_agent_tools.py`:

```python
def test_read_file_numbers_lines_and_paginates(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "big.txt", "content": "\n".join(f"line {index}" for index in range(1, 51)) + "\n"})

    outcome = toolset.invoke("read_file", {"path": "big.txt", "offset": 3, "limit": 2})

    assert outcome.status == "succeeded"
    assert "     3\tline 3" in outcome.content
    assert "line 5" not in outcome.content
    assert outcome.payload["total_lines"] == 50


def test_read_file_tells_the_model_how_to_read_the_rest(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "big.txt", "content": "\n".join(f"line {index}" for index in range(1, 51)) + "\n"})

    outcome = toolset.invoke("read_file", {"path": "big.txt", "offset": 1, "limit": 2})

    assert "offset=3" in outcome.content
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_workspace_search.py tests/unit/agentic/test_agent_tools.py -k "read_lines or read_file" -v`
Expected: FAIL com `AttributeError: ... 'read_lines'` e falha de asserção nos testes de tool

- [ ] **Step 3: Implementar `read_lines`**

Em `src/agentos/agentic/workspace.py`, adicionar a constante junto das outras:

```python
MAX_READ_LINES = 800
```

E o método logo após `read_text`:

```python
    def read_lines(self, path: str, *, offset: int = 1, limit: int = 400) -> tuple[list[str], int, int, bool]:
        """Return a bounded line window plus the total, so the model can page.

        ``read_text`` stays as the whole-file accessor used by ``edit_file``;
        this is the accessor used by the model, which must be able to ask for
        the part of a file it has not seen yet.
        """
        content, truncated = self.read_text(path)
        lines = content.splitlines()
        start = max(1, int(offset))
        window = max(1, min(int(limit), MAX_READ_LINES))
        return lines[start - 1: start - 1 + window], start, len(lines), truncated
```

Acrescentar `"MAX_READ_LINES"` ao `__all__`.

- [ ] **Step 4: Reescrever a tool `read_file`**

Em `src/agentos/agentic/agent_tools.py`, substituir a `ToolDefinition` de `read_file` por:

```python
            ToolDefinition(
                "read_file",
                "Read a UTF-8 text file from the conversation workspace. Output is line-numbered. Use offset/limit to read a long file in windows instead of guessing.",
                _schema({
                    "path": {**_TEXT, "description": "Workspace-relative path, e.g. notes/plan.md"},
                    "offset": {"type": "integer", "minimum": 1, "description": "First line to return (1-based). Defaults to 1."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 800, "description": "How many lines to return. Defaults to 400."},
                }, ("path",)),
                self.read_file, "filesystem",
            ),
```

E substituir o handler `read_file` por:

```python
    def read_file(self, path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
        lines, first, total, truncated = self.workspace.read_lines(path, offset=int(offset), limit=int(limit))
        if not lines:
            body = "[empty file]" if total == 0 else f"[no lines at offset {first}; the file has {total} lines]"
        else:
            body = "\n".join(f"{first + index:6}\t{line}" for index, line in enumerate(lines))
        next_line = first + len(lines)
        if lines and next_line <= total:
            body += f"\n\n[{total - next_line + 1} more lines; continue with read_file(path=\"{path}\", offset={next_line})]"
        if truncated:
            body += "\n\n[file exceeds the workspace read limit; the tail was not loaded]"
        return {
            "summary": f"Leu {path}",
            "content": body,
            "payload": {"path": path, "first_line": first, "returned_lines": len(lines), "total_lines": total, "truncated": truncated, "label": path},
        }
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_workspace_search.py tests/unit/agentic/test_agent_tools.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/workspace.py src/agentos/agentic/agent_tools.py tests/unit/agentic/test_workspace_search.py tests/unit/agentic/test_agent_tools.py
git commit -m "feat(tools): paginate read_file with line numbers"
```

---

### Task 3: Listagem recursiva

**Files:**
- Modify: `src/agentos/agentic/workspace.py` (`list_entries`)
- Modify: `src/agentos/agentic/agent_tools.py:218-222` e handler `list_files`
- Test: `tests/unit/agentic/test_workspace_search.py`

**Interfaces:**
- Consumes: `ConversationWorkspace.resolve`, `ConversationWorkspace.relative`
- Produces: `ConversationWorkspace.list_entries(path: str = "", *, depth: int = 1) -> list[dict[str, object]]` — `depth=1` mantém o comportamento atual; `depth>1` desce recursivamente. Tool `list_files` ganha argumento `depth`.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_workspace_search.py`:

```python
def test_list_entries_is_shallow_by_default(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/deep/app.py", "x\n")

    paths = [item["path"] for item in workspace.list_entries()]

    assert paths == ["src"]


def test_list_entries_descends_when_asked(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/deep/app.py", "x\n")

    paths = [item["path"] for item in workspace.list_entries(depth=3)]

    assert paths == ["src", "src/deep", "src/deep/app.py"]
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_workspace_search.py -k list_entries -v`
Expected: FAIL — `TypeError: list_entries() got an unexpected keyword argument 'depth'`

- [ ] **Step 3: Implementar a recursão**

Em `src/agentos/agentic/workspace.py`, adicionar a constante:

```python
MAX_LIST_DEPTH = 5
```

E substituir `list_entries` inteiro por:

```python
    def list_entries(self, path: str = "", *, depth: int = 1) -> list[dict[str, object]]:
        target = self.resolve(path) if path.strip() else self.root
        if not target.is_dir():
            raise WorkspaceError(f"'{path}' is not a directory in this workspace")
        levels = max(1, min(int(depth), MAX_LIST_DEPTH))
        entries: list[dict[str, object]] = []
        self._collect(target, levels, entries)
        return entries

    def _collect(self, directory: Path, levels: int, entries: list[dict[str, object]]) -> None:
        for item in sorted(directory.iterdir(), key=lambda value: (value.is_file(), value.name.lower())):
            if len(entries) >= 500:
                return
            is_file = item.is_file()
            entries.append({
                "path": self.relative(item),
                "kind": "file" if is_file else "directory",
                "size_bytes": item.stat().st_size if is_file else None,
            })
            if not is_file and levels > 1:
                self._collect(item, levels - 1, entries)
```

Acrescentar `"MAX_LIST_DEPTH"` ao `__all__`.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_workspace_search.py -v`
Expected: PASS

- [ ] **Step 5: Expor `depth` na tool**

Em `src/agentos/agentic/agent_tools.py`, substituir a `ToolDefinition` de `list_files` por:

```python
            ToolDefinition(
                "list_files", "List files and directories in the conversation workspace. Use depth to see a whole subtree in one call.",
                _schema({
                    "path": {**_TEXT, "description": "Workspace-relative directory; omit for the root."},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 5, "description": "How many directory levels to descend. Defaults to 1."},
                }),
                self.list_files, "filesystem",
            ),
```

E o handler:

```python
    def list_files(self, path: str = "", depth: int = 1) -> dict[str, Any]:
        entries = self.workspace.list_entries(path, depth=int(depth))
        if not entries:
            listing = "[empty directory]"
        else:
            listing = "\n".join(f"{item['kind'][:1]} {item['path']}" for item in entries)
        return {
            "summary": f"Listou {len(entries)} {'item' if len(entries) == 1 else 'itens'}",
            "content": listing,
            "payload": {"path": path or "/", "count": len(entries), "depth": int(depth), "label": path or "/"},
        }
```

- [ ] **Step 6: Rodar a suíte agentic inteira**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agentos/agentic/workspace.py src/agentos/agentic/agent_tools.py tests/unit/agentic/test_workspace_search.py
git commit -m "feat(tools): recursive workspace listing"
```

---

### Task 4: Edição em lote

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py:213-217` (definição) e `:345-363` (handler `edit_file`)
- Test: `tests/unit/agentic/test_agent_tools.py`

**Interfaces:**
- Consumes: `ConversationWorkspace.read_text`, `ConversationWorkspace.write_text`
- Produces: tool `edit_file` com argumentos `{path, old_text?, new_text?, edits?, replace_all?}`. `edits` é `list[{old_text, new_text}]` aplicada em ordem. Ou se passa `old_text`+`new_text`, ou se passa `edits` — nunca ambos. Nenhuma edição é gravada se qualquer uma falhar.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_agent_tools.py`:

```python
def test_edit_file_applies_a_batch_of_edits_in_one_call(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "app.py", "content": "alpha\nbeta\ngamma\n"})

    outcome = toolset.invoke("edit_file", {"path": "app.py", "edits": [
        {"old_text": "alpha", "new_text": "one"},
        {"old_text": "gamma", "new_text": "three"},
    ]})

    assert outcome.status == "succeeded"
    assert toolset.invoke("read_file", {"path": "app.py"}).payload["total_lines"] == 3
    assert "one" in outcome.content or outcome.payload["edits_applied"] == 2


def test_edit_file_writes_nothing_when_one_edit_in_the_batch_fails(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "app.py", "content": "alpha\nbeta\n"})

    outcome = toolset.invoke("edit_file", {"path": "app.py", "edits": [
        {"old_text": "alpha", "new_text": "one"},
        {"old_text": "missing", "new_text": "x"},
    ]})

    assert outcome.status == "failed"
    assert "alpha" in toolset.invoke("read_file", {"path": "app.py"}).content


def test_edit_file_can_replace_every_occurrence_when_asked(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "app.py", "content": "same\nsame\n"})

    outcome = toolset.invoke("edit_file", {"path": "app.py", "old_text": "same", "new_text": "done", "replace_all": True})

    assert outcome.status == "succeeded"
    assert "same" not in toolset.invoke("read_file", {"path": "app.py"}).content


def test_edit_file_refuses_mixing_the_single_and_batch_forms(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "app.py", "content": "alpha\n"})

    outcome = toolset.invoke("edit_file", {"path": "app.py", "old_text": "alpha", "new_text": "one", "edits": [{"old_text": "alpha", "new_text": "two"}]})

    assert outcome.status == "failed"
    assert outcome.error_code == "TOOL_REFUSED"
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py -k edit_file -v`
Expected: FAIL — `edit_file() got an unexpected keyword argument 'edits'` (reportado como `INVALID_ARGUMENTS`)

- [ ] **Step 3: Substituir a definição da tool**

Em `src/agentos/agentic/agent_tools.py`:

```python
            ToolDefinition(
                "edit_file",
                "Replace text fragments in a UTF-8 workspace file. Read the file first. Pass 'edits' to apply several replacements in one call; the whole batch is rejected if any fragment is missing or ambiguous.",
                _schema({
                    "path": _TEXT,
                    "old_text": _TEXT,
                    "new_text": _TEXT,
                    "edits": {
                        "type": "array",
                        "description": "Batch form. Each item replaces one fragment; they are applied in order.",
                        "items": _schema({"old_text": _TEXT, "new_text": _TEXT}, ("old_text", "new_text")),
                    },
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring a unique one."},
                }, ("path",)),
                self.edit_file, "filesystem",
            ),
```

- [ ] **Step 4: Substituir o handler**

```python
    @staticmethod
    def _normalized_edits(old_text: str | None, new_text: str | None, edits: list[Mapping[str, Any]] | None) -> list[tuple[str, str]]:
        single = old_text is not None or new_text is not None
        if single and edits:
            raise AgentToolError("provide either old_text/new_text or edits, not both.")
        if single:
            if not isinstance(old_text, str) or not old_text:
                raise AgentToolError("old_text must be a non-blank string")
            if not isinstance(new_text, str):
                raise AgentToolError("new_text must be a string")
            return [(old_text, new_text)]
        if not isinstance(edits, list) or not edits:
            raise AgentToolError("provide old_text/new_text or a non-empty edits array.")
        normalized: list[tuple[str, str]] = []
        for index, item in enumerate(edits):
            if not isinstance(item, Mapping):
                raise AgentToolError(f"edits[{index}] must be an object with old_text and new_text.")
            current, replacement = item.get("old_text"), item.get("new_text")
            if not isinstance(current, str) or not current:
                raise AgentToolError(f"edits[{index}].old_text must be a non-blank string")
            if not isinstance(replacement, str):
                raise AgentToolError(f"edits[{index}].new_text must be a string")
            normalized.append((current, replacement))
        return normalized

    def edit_file(self, path: str, old_text: str | None = None, new_text: str | None = None, edits: list[Mapping[str, Any]] | None = None, replace_all: bool = False) -> dict[str, Any]:
        operations = self._normalized_edits(old_text, new_text, edits)
        content, truncated = self.workspace.read_text(path)
        if truncated:
            raise AgentToolError("file is too large to edit safely; split the edit into a smaller file or rewrite it deliberately.")
        # Every edit is validated against the running draft before anything is
        # written, so a bad fragment late in the batch cannot leave the file
        # half-edited.
        draft = content
        for index, (current, replacement) in enumerate(operations):
            matches = draft.count(current)
            if matches == 0:
                raise AgentToolError(f"edit {index + 1}: old_text was not found; read the file and provide the exact fragment.")
            if matches > 1 and not replace_all:
                raise AgentToolError(f"edit {index + 1}: old_text occurs {matches} times; add surrounding text or set replace_all=true.")
            draft = draft.replace(current, replacement) if replace_all else draft.replace(current, replacement, 1)
        written = self.workspace.write_text(path, draft)
        return {
            "summary": f"Editou {path}",
            "content": f"Applied {len(operations)} edit(s) to {path} ({written} bytes).",
            "payload": {"path": path, "bytes_written": written, "edits_applied": len(operations), "label": path, "artifacts": [self.workspace.file_metadata(path)]},
        }
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py -v`
Expected: PASS — inclusive `test_edit_file_refuses_ambiguous_fragments`, que continua válido porque `replace_all` é `False` por padrão

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/agent_tools.py tests/unit/agentic/test_agent_tools.py
git commit -m "feat(tools): batch edits and replace_all in edit_file"
```

---

### Task 5: Truncamento instrutivo e catálogo memoizado

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py:170-198` (`__init__`), `:201-286` (`definitions`), `:291-295` (`resolve`), `:316-325` (`invoke`), `:582-592` (remover `activity_for`)
- Modify: `src/agentos/agentic/events.py` (só se `activity_for` for reexportado de lá — verificar antes de remover)
- Test: `tests/unit/agentic/test_agent_tools.py`

**Interfaces:**
- Consumes: `ToolDefinition`, `ToolOutcome`
- Produces: `AgentToolset.definitions()` passa a retornar uma tupla memoizada (`self._definitions`); `invoke` acrescenta orientação de continuação ao truncar.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_agent_tools.py`:

```python
def test_truncated_output_tells_the_model_how_to_narrow_it(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "huge.txt", "content": "x" * 20_000 + "\n"})

    outcome = toolset.invoke("read_file", {"path": "huge.txt"})

    assert outcome.payload["truncated"] is True
    assert "narrow" in outcome.content.lower() or "offset" in outcome.content.lower()


def test_definitions_are_built_once_per_toolset(toolset: AgentToolset) -> None:
    assert toolset.definitions() is toolset.definitions()
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py -k "truncated or definitions_are_built" -v`
Expected: FAIL — o segundo teste falha porque `definitions()` retorna uma tupla nova a cada chamada

- [ ] **Step 3: Memoizar `definitions` e indexar `resolve`**

Em `src/agentos/agentic/agent_tools.py`, no fim do `__init__` de `AgentToolset`, acrescentar:

```python
        self._definitions: tuple[ToolDefinition, ...] | None = None
        self._by_name: dict[str, ToolDefinition] = {}
```

Renomear o método atual `definitions` para `_build_definitions` (só o `def`; o corpo não muda) e adicionar:

```python
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """The tool set is fixed for the lifetime of a turn, so build it once."""
        if self._definitions is None:
            self._definitions = self._build_definitions()
            self._by_name = {item.name: item for item in self._definitions}
        return self._definitions

    def resolve(self, name: str) -> ToolDefinition:
        self.definitions()
        definition = self._by_name.get(name)
        if definition is None:
            raise AgentToolError(f"Unknown tool '{name}'.")
        return definition
```

- [ ] **Step 4: Tornar o truncamento instrutivo**

Em `invoke`, substituir o bloco final (a partir de `content, truncated = _bounded(...)`) por:

```python
        content, truncated = _bounded(str(result.get("content", "")))
        payload = dict(result.get("payload") or {})
        payload.setdefault("tool_kind", definition.kind)
        if truncated:
            payload["truncated"] = True
            content += (
                f"\n\n[output truncated at {MAX_TOOL_RESULT_CHARS} characters — "
                "narrow the request instead of repeating it: use read_file with offset/limit, "
                "search_files with a tighter pattern, or a command that prints less]"
            )
        return ToolOutcome("succeeded", str(result.get("summary", f"{name} concluído"))[:240], content, payload)
```

- [ ] **Step 5: Remover o código morto**

Excluir a função `activity_for` inteira (`src/agentos/agentic/agent_tools.py:582-592`) — todos os ramos retornam `AgentActivityEventType.TOOL_FINISHED`, então ela não decide nada. Remover também `"activity_for"` do `__all__` se estiver listada, e o import agora não usado:

```python
from .events import AgentActivityEventType
```

Antes de remover o import, confirmar que nada mais no arquivo usa `AgentActivityEventType`:

Run: `uv run python -c "import pathlib,re; print([n for n,l in enumerate(pathlib.Path('src/agentos/agentic/agent_tools.py').read_text(encoding='utf-8').splitlines(),1) if 'AgentActivityEventType' in l])"`
Expected: lista vazia depois da remoção; se não estiver vazia, manter o import.

Confirmar que ninguém importa `activity_for`:

Run: `uv run python -m pytest tests/unit/agentic -v` e `git grep -n "activity_for"`
Expected: `git grep` sem resultados fora do histórico

- [ ] **Step 6: Rodar a suíte completa do pacote**

Run: `uv run pytest tests/unit/agentic tests/unit/workers -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agentos/agentic/agent_tools.py tests/unit/agentic/test_agent_tools.py
git commit -m "perf(tools): memoize tool catalog and make truncation actionable"
```

---

## Verificação final do plano

- [ ] `uv run pytest tests/unit/agentic -v` — PASS
- [ ] `uv run pytest tests/unit -q` — sem regressão nova
- [ ] `git grep -n "def definitions" src/agentos/agentic/agent_tools.py` — um único método público
- [ ] Revisar o diff: nenhuma tool nova escreve fora de `ConversationWorkspace.root`
