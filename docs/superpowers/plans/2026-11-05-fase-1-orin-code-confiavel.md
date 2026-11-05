# Fase 1 — Orin Code confiável — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir os defeitos do Orin Code que independem de provedor (R1.1–R1.11 da spec): edit em CRLF, shell real no Windows, seção de ambiente, leitura honesta, stubs e imagens depois do envelhecimento, estado de leitura por conversa, lote coerente, validação com referência, nada do Orin no git do usuário e mensagem só com anexo.

**Architecture:** Cada requisito vira uma tarefa TDD pequena no módulo que já é dono do comportamento (ferramentas em `agentic/tools/`, runtime em `agentic/runtime.py`, sessão em `agentic/session.py`). Código novo só onde não existe dono: `agentos/host_shell.py` (descoberta do Git Bash e conversão de caminhos), `agentic/git_exclude.py` (linha `.orin/` no exclude do git), `agentic/read_state.py` (estado de leitura por conversa), `agentic/tools/files/newlines.py` (final de linha) e `code_mode/verification_baseline.py` (referência da primeira verificação). As tarefas vão das correções locais e de baixo risco para as que mexem em shell, prompt e estágios.

**Tech Stack:** Python 3.12+, Pytest (`uv run pytest`), Windows 11 com Git Bash em `C:\Program Files\Git\bin\bash.exe` nesta máquina.

**Spec:** `docs/superpowers/specs/2026-11-05-claude-assinatura-nativa-e-orin-code-design.md` (Fase 1, R1.1–R1.11, decisões D8 e D9). Evidência: `docs/audits/2026-11-05-claude-assinatura-e-orin-code.md`.

## Global Constraints

- Nada da Fase 2: não tocar em `provider_catalog/claude_code.py`, `opencode.py`, `_LEGACY_CLAUDE_MODEL_MAP` nem no teto de 200k de `workers/chat.py:938-941`. Não invocar a CLI `claude`.
- D8: anexos e estado do Orin vivem em `<workspace>/.orin/` (`uploads` vira `.orin/uploads/`); o Orin garante a linha `.orin/` em `.git/info/exclude` quando o workspace é um repositório git. Anexos antigos em `uploads/` continuam legíveis (migração não destrutiva).
- D9: no Windows, `run_command` (público "Bash") roda pelo Git Bash (`bash.exe -c`), com fallback para cmd.exe só quando o Git Bash não existe, e o prompt diz o shell real.
- Mensagem exata da recusa no lote: `"You read this file in the same batch; look at the read result before editing it."`
- `verify_project` não roda `pip install` por padrão; usa `uv run` com `uv.lock`, `poetry run` com `poetry.lock`, venv local quando existe.
- O repositório usa CRLF (`core.autocrlf=true`): testes de final de linha gravam e conferem **bytes** (`write_bytes`/`read_bytes`), nunca `write_text`.
- TDD em toda tarefa: teste falhando → implementação → teste passando → commit. Rodar só os testes da tarefa; a suíte inteira roda uma vez, na Tarefa 13.
- Textos para o modelo em inglês (como o resto dos prompts de ferramenta); textos de UI/histórico em pt-BR, como o código vizinho.

## Review Focus

1. **Arquivo com finais de linha mistos** (parte CRLF, parte LF): editar a região LF não pode converter o arquivo nem a região editada para CRLF. Teste `test_edit_of_an_lf_region_in_a_mixed_file_touches_only_that_region` na Tarefa 1.
2. **Arquivo minificado (uma linha maior que o orçamento de caracteres)**: o `read_file` precisa devolver parte da linha, registrar só a linha 1 e indicar `offset=2`. Teste `test_a_single_line_longer_than_the_budget_is_cut_inside_the_line` na Tarefa 2.
3. **Caminho POSIX com espaço no `cd`** (`cd "/c/Users/Some Name/proj"`): o pré-check e o cwd persistido têm de entender o caminho. Testes `test_posix_cd_with_spaces_is_understood` e `test_run_command_runs_through_git_bash` na Tarefa 9.
4. **Workspace que é worktree do git ou subpasta de um repo**: o `.orin/` tem de ir para o `info/exclude` do diretório comum do repo. Testes `test_a_worktree_writes_the_common_exclude` e `test_a_subfolder_workspace_writes_the_repo_exclude` na Tarefa 8.
5. **Arquivo alterado fora do Orin entre dois turnos**: o estado carregado não pode autorizar edit nem stub para esse arquivo. Teste `test_a_carried_state_drops_changed_files_and_releases_stubs` na Tarefa 7.

---

## Mapa de arquivos

| Arquivo | Papel nesta fase |
|---|---|
| `src/agentos/agentic/tools/files/newlines.py` (novo) | Detectar e aplicar o final de linha de um texto. |
| `src/agentos/agentic/tools/files/edit.py`, `write.py` | Usam `newlines.py` (R1.1). |
| `src/agentos/agentic/tools/files/read.py` | Orçamento de caracteres por linha inteira (R1.4), `forget_read_content`/`read_key`/`has_read` (R1.5, R1.8), prefixos de upload (R1.10). |
| `src/agentos/conversations/chat.py` | Linha de instrução para mensagem só com anexos (R1.11). |
| `src/agentos/agentic/runtime.py` | Cache sem imagem (R1.6), lote coerente (R1.8), liberação de stubs (R1.5). |
| `src/agentos/agentic/read_state.py` (novo) | Estado de leitura por conversa, cópia para fork, poda no início do turno (R1.7). |
| `src/agentos/agentic/session.py` | Recebe o estado (R1.7), exclude do git (R1.10), `environment_facts` (R1.2), prompt de subagente (R1.2), contexto do code mode (R1.3), estágios (R1.9). |
| `src/agentos/workers/chat.py` | Mantém o registro de estados de leitura do processo (R1.7). |
| `src/agentos/agentic/git_exclude.py` (novo) | Achar o diretório git e garantir `.orin/` no exclude (R1.10). |
| `src/agentos/agentic/workspace.py`, `uploads/promotion.py`, `permissions/paths.py` | `.orin/uploads` (R1.10). |
| `src/agentos/host_shell.py` (novo) | Git Bash: descoberta, argv, conversão de caminhos (R1.2). |
| `src/agentos/agentic/tools/shell/bash.py`, `permissions/shell.py` | Usam o shell real (R1.2). |
| `src/agentos/code_mode/system_prompt.py` | Seção `# Environment` completa (R1.3). |
| `src/agentos/code_mode/verification_baseline.py` (novo) | Impressão digital de falhas e referência da primeira verificação (R1.9). |
| `src/agentos/agentic/diagnostics.py`, `tools/orin/verify.py` | Executor do projeto, sem `pip install` por padrão, cancelamento (R1.9). |

---

### Task 1: Edit e Write preservam o final de linha (R1.1)

**Files:**
- Create: `src/agentos/agentic/tools/files/newlines.py`
- Modify: `src/agentos/agentic/tools/files/edit.py:69-76` (laço de validação de `edit_file`)
- Modify: `src/agentos/agentic/tools/files/write.py:34-38` (ramo `overwrite`)
- Test: `tests/unit/agentic/tools/test_line_endings.py` (novo)

**Interfaces:**
- Produces: `dominant_newline(text: str) -> str` (`"\r\n"` quando há mais `\r\n` que `\n` solto, senão `"\n"`); `with_newline(text: str, newline: str) -> str` (normaliza tudo para `\n` e, se `newline == "\r\n"`, converte para CRLF).

- [ ] **Step 1: Escrever os testes que falham**

```python
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, permission_mode="full_autonomy")


def _seed(toolset: AgentToolset, name: str, data: bytes) -> Path:
    target = toolset.workspace.root / name
    target.write_bytes(data)
    assert toolset.invoke("read_file", {"path": name}).status == "succeeded"
    return target


def test_edit_finds_lf_old_text_in_a_crlf_file_and_keeps_crlf(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"one\r\ntwo\r\nthree\r\n")
    outcome = toolset.invoke("edit_file", {"path": "a.txt", "old_text": "one\ntwo", "new_text": "uno\ndos\ntres"})
    assert outcome.status == "succeeded", outcome.content
    assert target.read_bytes() == b"uno\r\ndos\r\ntres\r\nthree\r\n"


def test_a_single_line_old_text_with_a_multiline_replacement_uses_crlf(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"a\r\nb\r\n")
    assert toolset.invoke("edit_file", {"path": "a.txt", "old_text": "a", "new_text": "x\ny"}).status == "succeeded"
    assert target.read_bytes() == b"x\r\ny\r\nb\r\n"


def test_edit_of_an_lf_region_in_a_mixed_file_touches_only_that_region(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"a\r\nb\r\nc\nd\n")
    assert toolset.invoke("edit_file", {"path": "a.txt", "old_text": "c\nd", "new_text": "x\ny"}).status == "succeeded"
    assert target.read_bytes() == b"a\r\nb\r\nx\ny\n"


def test_edit_of_an_lf_file_is_unchanged_behaviour(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"a\nb\n")
    assert toolset.invoke("edit_file", {"path": "a.txt", "old_text": "a\nb", "new_text": "c\nd"}).status == "succeeded"
    assert target.read_bytes() == b"c\nd\n"


def test_write_file_over_a_crlf_file_keeps_crlf(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"old\r\nfile\r\n")
    assert toolset.invoke("write_file", {"path": "a.txt", "content": "new\ncontent\n"}).status == "succeeded"
    assert target.read_bytes() == b"new\r\ncontent\r\n"


def test_write_file_of_a_new_file_keeps_what_the_model_sent(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    assert toolset.invoke("write_file", {"path": "n.txt", "content": "a\nb\n"}).status == "succeeded"
    assert (toolset.workspace.root / "n.txt").read_bytes() == b"a\nb\n"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/tools/test_line_endings.py -v`
Expected: FAIL — os três primeiros com `old_text was not found` ou bytes com `\n` solto; `test_write_file_over_a_crlf_file_keeps_crlf` com `b"new\ncontent\n"`.

- [ ] **Step 3: Implementar `newlines.py` e usar em `edit_file` e `write_file`**

Em `edit_file`, para cada operação, escolher o fragmento e o final de linha da troca assim (o resto do laço, contagem e `replace_all`, fica igual):

```python
current_used, target_newline = current, None
matches = draft.count(current)
if matches == 0 and "\n" in current:
    for candidate_newline in ("\r\n", "\n"):
        candidate = with_newline(current, candidate_newline)
        if candidate != current and draft.count(candidate):
            current_used, target_newline, matches = candidate, candidate_newline, draft.count(candidate)
            break
if target_newline is None:
    if "\r\n" in current_used:
        target_newline = "\r\n"
    elif "\n" not in current_used and dominant_newline(draft) == "\r\n":
        target_newline = "\r\n"
    else:
        target_newline = "\n"
replacement_used = with_newline(replacement, target_newline)
```

A checagem de ambiguidade (`matches > 1`) e o `draft.replace(...)` passam a usar `current_used` e `replacement_used`. `tool_output.oldString`/`newString` continuam com o texto que o modelo mandou. Em `write_file` modo `overwrite`, quando `old_content` não é `None` e `dominant_newline(old_content) == "\r\n"`, gravar `with_newline(content, "\r\n")` (e usar esse texto em `new_content`). O modo `append` não muda.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/tools/test_line_endings.py tests/unit/agentic/test_agent_tools.py -k "edit or write" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/tools/files/newlines.py src/agentos/agentic/tools/files/edit.py src/agentos/agentic/tools/files/write.py tests/unit/agentic/tools/test_line_endings.py
git commit -m "fix(tools): edit and write keep the file's own line endings"
```

---

### Task 2: Leitura honesta — o registro guarda só o que o modelo recebeu (R1.4)

Hoje `read_file` registra a janela pedida (`read.py:402-408`) e só depois `AgentToolset.invoke` corta o texto em 12.000 caracteres (`agent_tools.py:1052-1075`, limite `MAX_RESULT_CHARS_DEFAULT` de `tools/spec.py:24`). A correção é cortar **dentro** do `read_file`, por linha inteira, antes de registrar.

**Files:**
- Modify: `src/agentos/agentic/tools/files/read.py:306-455` (`read_file`)
- Test: `tests/unit/agentic/tools/test_read_parity.py` (acrescentar)

**Interfaces:**
- Consumes: `MAX_RESULT_CHARS_DEFAULT` de `agentos.agentic.tools.spec`; `LARGE_READ_RESULT_CHARS` (já em `read.py:38`).
- Produces: payload do `read_file` ganha `truncated_by_chars: bool`; `tool_output.file.truncatedByTokenCap` passa a ser `True` também nesse caso. O `ReadFileRecord.limit` gravado é o número de linhas devolvidas quando houve corte por caracteres (senão continua a janela pedida, para o stub "inalterado" seguir funcionando).

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_a_read_cut_by_the_character_budget_records_only_the_lines_it_returned(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "wide.txt").write_bytes(b"".join(b"x" * 100 + b"\n" for _ in range(400)))
    outcome = toolset.invoke("read_file", {"path": "wide.txt"})
    last = outcome.payload["end_line"]
    assert 50 < last < 400
    assert len(outcome.content) <= 12_000
    assert outcome.payload["truncated"] is False  # cut by read_file itself, not by the generic bound
    assert outcome.payload["truncated_by_chars"] is True
    assert f"offset={last + 1}" in outcome.content
    assert toolset._read_file_state["wide.txt"].limit == last
    following = toolset.invoke("read_file", {"path": "wide.txt", "offset": last + 1})
    assert f"{last + 1:6}\t" in following.content


def test_a_reread_after_a_cut_returns_content_not_the_unchanged_stub(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "wide.txt").write_bytes(b"".join(b"x" * 100 + b"\n" for _ in range(400)))
    toolset.invoke("read_file", {"path": "wide.txt"})
    again = toolset.invoke("read_file", {"path": "wide.txt"})
    assert again.data["tool_output"]["type"] == "text"


def test_a_single_line_longer_than_the_budget_is_cut_inside_the_line(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "min.js").write_bytes(b"y" * 50_000 + b"\nz\n")
    outcome = toolset.invoke("read_file", {"path": "min.js"})
    assert outcome.status == "succeeded"
    assert "[line 1 truncated at" in outcome.content
    assert outcome.payload["end_line"] == 1
    assert "offset=2" in outcome.content
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/tools/test_read_parity.py -v -k "budget or cut"`
Expected: FAIL — `KeyError: 'truncated_by_chars'` / `truncated` é `True` / limite registrado 400.

- [ ] **Step 3: Implementar o corte por linha em `read_file`**

Depois de `self.workspace.read_lines(...)` e **antes** de `_record_file_read`: `cap = LARGE_READ_RESULT_CHARS if effective_allow_large else MAX_RESULT_CHARS_DEFAULT`; `budget = cap - 400 - len(path)` (reserva para as dicas). Montar as linhas numeradas (`f"{n:6}\t{line}"`) uma a uma enquanto o total com `\n` couber no orçamento; se nem a primeira couber, devolver a primeira cortada em `budget` caracteres seguida de `f"\n[line {first} truncated at {budget} characters]"`. `lines` passa a ser só as linhas mantidas, `cut_by_chars = len(kept) < len(lines_originais)` ou primeira linha cortada. Registrar com `limit=len(kept) if cut_by_chars else norm_limit`. Quando `cut_by_chars`, a dica final é exatamente:

```python
f"\n\n[stopped at line {end_line} to stay under {cap} characters; {total - end_line} more lines; continue with read_file(path=\"{path}\", offset={end_line + 1})]"
```

(com `Read(file_path=...)` no lugar quando `self.name_translator is not None`, como a dica existente em `read.py:420-424`). Sem corte, a dica atual não muda.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/tools/test_read_parity.py tests/unit/agentic/test_agent_tools.py -k "read" -v`
Expected: PASS (inclusive `test_an_unchanged_reread_is_reported_as_file_unchanged` e `test_allow_large_reads_past_the_default_caps`).

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/tools/files/read.py tests/unit/agentic/tools/test_read_parity.py
git commit -m "fix(read): record only the lines the model actually received"
```

---

### Task 3: Mensagem só com anexo vira instrução (R1.11)

**Investigação (feita):** o commit `a9494ca` não deixa a mensagem vazia — `Home.tsx` e `ChatPage.tsx` mandam `buildOversizedPromptMessage(...)` ("…Enviei o prompt completo no arquivo prompt.txt anexado…") junto com `prompt.txt`. A sessão `chat_2186…` tinha mensagem vazia e o anexo `writing-block.md`: é um envio **só com anexo**, que `PostgresChatStore.create` aceita (`conversations/chat.py:840`). Em `history_for_turn` (`conversations/chat.py:2281-2283`) essa mensagem vira só a lista `[anexos enviados pela pessoa: …]`, e o reminder de `inject_text_attachments_into_history` (`code_mode/mentions.py`) entra como `<system-reminder>`; o modelo não recebe nenhuma instrução do usuário. A correção é no histórico (vale também para conversas antigas), sem mudar o que a UI mostra.

**Files:**
- Modify: `src/agentos/conversations/chat.py:68-82` (novo helper ao lado de `_attachment_marker`) e `:2281-2283`
- Test: `tests/unit/conversations/test_chat_attachments.py` (acrescentar)

**Interfaces:**
- Produces: `_attachment_only_instruction(records: Sequence[Mapping[str, object]]) -> str` em `conversations/chat.py`.

- [ ] **Step 1: Escrever os testes que falham**

```python
TEXT_ATTACHMENT = {
    "path": ".orin/uploads/writing-block.md", "original_name": "writing-block.md",
    "media_type": "text/markdown", "kind": "text", "bytes": 4096,
}


def _history_for(message: str, attachments):
    store = _store()
    receipt = store.create(user_id="user-1", message=message, provider="anthropic", model_id="m",
                           idempotency_key="k1", attachments=attachments)
    return store.history_for_turn(store.claim(receipt.turn_id))


def test_an_attachment_only_message_tells_the_model_the_text_file_is_the_instruction():
    first_line = _history_for("", [TEXT_ATTACHMENT])[0]["content"].splitlines()[0]
    assert first_line == (
        "[mensagem sem texto: a pessoa enviou só anexos. O conteúdo de .orin/uploads/writing-block.md "
        "é a instrução desta mensagem: leia e siga.]"
    )


def test_an_attachment_only_message_with_visual_files_points_to_them():
    first_line = _history_for("", [ATTACHMENT])[0]["content"].splitlines()[0]
    assert first_line == (
        "[mensagem sem texto: a pessoa enviou só anexos (uploads/nota.pdf). "
        "Abra-os e trate o que pedem como a instrução desta mensagem.]"
    )


def test_a_message_with_text_keeps_its_text_first():
    assert _history_for("veja isto", [TEXT_ATTACHMENT])[0]["content"].startswith("veja isto")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/conversations/test_chat_attachments.py -v`
Expected: FAIL — a primeira linha é vazia (o conteúdo começa com `\n\n[anexos enviados…`).

- [ ] **Step 3: Implementar `_attachment_only_instruction` e usar no histórico**

Texto exato: com pelo menos um anexo `kind == "text"`, `"[mensagem sem texto: a pessoa enviou só anexos. O conteúdo de {paths} é a instrução desta mensagem: leia e siga.]"` com `paths` = caminhos dos anexos de texto unidos por `", "`; sem anexo de texto, `"[mensagem sem texto: a pessoa enviou só anexos ({paths}). Abra-os e trate o que pedem como a instrução desta mensagem.]"` com todos os caminhos. Em `history_for_turn`, quando `records` não é vazio, `row["role"] == "user"` e `content.strip() == ""`, o conteúdo fica `instrução + _attachment_marker(records)`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/conversations/test_chat_attachments.py tests/unit/conversations/test_chat_application.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/conversations/chat.py tests/unit/conversations/test_chat_attachments.py
git commit -m "fix(conversations): an attachment-only message tells the model the file is the instruction"
```

---

### Task 4: Reabrir imagem traz os pixels de novo (R1.6)

O cache de leituras do turno (`runtime.py:2370-2371`) guarda só `outcome.content`; a segunda `view_file` da mesma imagem é servida por `cached_read` (`runtime.py:2238-2262`) sem imagem. (O stub "inalterado" não se aplica a imagens: `read_file` delega para `view_file` antes da checagem, `read.py:327-338`.)

**Files:**
- Modify: `src/agentos/agentic/runtime.py:2369-2371` (dentro de `invoke` em `_run_toolset`)
- Test: `tests/unit/agentic/test_successful_call_dedup.py` (acrescentar)

**Interfaces:** nenhuma nova.

- [ ] **Step 1: Escrever o teste que falha**

```python
class _ImageToolset(_Toolset):
    READ_ONLY = {"view_file"}

    def invoke(self, name, arguments):
        self.invocations.append((name, str(arguments)))
        return ToolOutcome("succeeded", "visto", "[imagem]", {}, images=[{"type": "image", "media_type": "image/png", "data": "AAAA"}])


def test_reopening_an_image_reaches_the_tool_again_and_brings_the_pixels() -> None:
    toolset = _ImageToolset()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)
    call = {"id": "c1", "name": "view_file", "arguments": '{"path": "dot.png"}'}
    runtime._run_toolset(_turn(), [call])
    second = runtime._run_toolset(_turn(), [{**call, "id": "c2"}])
    assert len(toolset.invocations) == 2
    assert second[0]["images"]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_successful_call_dedup.py -v -k image`
Expected: FAIL — `assert 1 == 2`.

- [ ] **Step 3: Implementar**

Gravar em `self._succeeded_reads` só quando `read_only and outcome.status == "succeeded" and not outcome.images`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/test_successful_call_dedup.py tests/unit/agentic/test_view_file_vision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py tests/unit/agentic/test_successful_call_dedup.py
git commit -m "fix(runtime): never serve an image result from the read cache"
```

---

### Task 5: Lote coerente — repetição roda uma vez, edit após leitura no mesmo lote é recusado (R1.8)

**Files:**
- Modify: `src/agentos/agentic/runtime.py:2165-2487` (`_run_toolset`: listas pré-calculadas ao lado de `duplicate`/`repeated`, ramo `if not eligible[index]`, condição de `_failed_signatures` em `:2467`)
- Modify: `src/agentos/agentic/tools/files/read.py` (dois métodos novos em `ReadTools`)
- Test: `tests/unit/agentic/test_batch_coherence.py` (novo)

**Interfaces:**
- Produces: `ReadTools.read_key(self, path: str) -> str | None` (chave normalizada do registro, `workspace.key(workspace.resolve(path))`, `None` se não resolver); `ReadTools.has_read(self, path: str) -> bool` (há registro para a chave). Código de erro `READ_IN_SAME_BATCH`; constante `SAME_BATCH_READ_MESSAGE = "You read this file in the same batch; look at the read result before editing it."` em `runtime.py`.

- [ ] **Step 1: Escrever os testes que falham**

```python
import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace

TURN = {"turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1", "provider": "openrouter"}


def _call(index: int, name: str, arguments: dict) -> dict[str, str]:
    return {"id": f"c{index}", "name": name, "arguments": json.dumps(arguments)}


class _CountingToolset:
    READ_ONLY = {"search_files", "read_process_output"}

    def __init__(self) -> None:
        self.invocations: list[str] = []

    def is_read_only(self, name): return name in self.READ_ONLY
    def argument_names(self, name): return None

    def invoke(self, name, arguments):
        self.invocations.append(name)
        return ToolOutcome("succeeded", "ok", f"result {len(self.invocations)}", {})


def test_identical_calls_in_one_batch_run_once_and_share_the_result() -> None:
    toolset = _CountingToolset()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)
    results = runtime._run_toolset(TURN, [
        _call(1, "search_files", {"pattern": "x"}), _call(2, "search_files", {"pattern": "x"}),
        _call(3, "run_command", {"command": "pytest"}), _call(4, "run_command", {"command": "pytest"}),
    ])
    assert toolset.invocations == ["search_files", "run_command"]
    assert [item["id"] for item in results] == ["c1", "c2", "c3", "c4"]
    assert results[1]["content"].startswith(results[0]["content"])
    assert results[3]["content"].startswith(results[2]["content"])
    assert runtime.counters.redundant_tool_calls == 2


def test_polling_calls_are_never_merged() -> None:
    toolset = _CountingToolset()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)
    runtime._run_toolset(TURN, [_call(1, "read_process_output", {"pid": 1}), _call(2, "read_process_output", {"pid": 1})])
    assert toolset.invocations == ["read_process_output", "read_process_output"]


def _real(tmp_path: Path) -> tuple[AgentToolset, AgenticTurnRuntime]:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, permission_mode="full_autonomy")
    (toolset.workspace.root / "a.txt").write_bytes(b"one\n")
    return toolset, AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)


def test_an_edit_of_a_file_read_only_in_the_same_batch_is_refused(tmp_path: Path) -> None:
    toolset, runtime = _real(tmp_path)
    edit = {"path": "a.txt", "old_text": "one", "new_text": "two"}
    results = runtime._run_toolset(TURN, [_call(1, "read_file", {"path": "a.txt"}), _call(2, "edit_file", edit)])
    assert results[1]["status"] == "failed"
    assert results[1]["error_code"] == "READ_IN_SAME_BATCH"
    assert results[1]["content"] == "You read this file in the same batch; look at the read result before editing it."
    assert (toolset.workspace.root / "a.txt").read_bytes() == b"one\n"
    again = runtime._run_toolset(TURN, [_call(3, "edit_file", edit)])
    assert again[0]["status"] == "succeeded"  # the refusal is not remembered as a failed signature


def test_an_edit_of_a_file_read_in_an_earlier_batch_runs(tmp_path: Path) -> None:
    toolset, runtime = _real(tmp_path)
    runtime._run_toolset(TURN, [_call(1, "read_file", {"path": "a.txt"})])
    results = runtime._run_toolset(TURN, [
        _call(2, "read_file", {"path": "a.txt"}),
        _call(3, "edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}),
    ])
    assert results[1]["status"] == "succeeded"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_batch_coherence.py -v`
Expected: FAIL — invocações duplicadas e edit aplicado.

- [ ] **Step 3: Implementar**

1. `read_key`/`has_read` em `ReadTools` (com `self._read_file_lock`).
2. Em `_run_toolset`, depois de `repeated`: `first_index: list[int | None]` — para cada `i` sem erro, sem `duplicate[i]` e sem `repeated[i]`, cujo nome não esteja em `{"check_subagent", "read_process_output", "preview_logs"}`, o primeiro `j < i` com a mesma `self._signature(name, arguments)`. Esses `i` saem de `eligible`.
3. `same_batch_read: list[bool]` — calculado **antes** de executar qualquer chamada: `True` para `edit_file`, `notebook_edit` e `write_file` com `mode != "append"` cujo `read_key(path)` é igual ao de um `read_file` anterior no lote e `has_read(path)` é `False` no início do lote. Só quando o toolset tem `read_key`/`has_read` (fakes antigos ficam iguais).
4. No ramo `if not eligible[index]`, antes de `duplicate`: `same_batch_read[index]` → `ToolOutcome("failed", "Edição recusada: arquivo lido neste mesmo lote", SAME_BATCH_READ_MESSAGE, {}, "READ_IN_SAME_BATCH")`; `first_index[index] is not None` → cópia do resultado `outcomes[first_index[index]]` (`interrupted_outcome()` se não existir) com `content + "\n\n[identical to an earlier call in this batch; it ran once]"`.
5. Em `:2467`, não gravar em `_failed_signatures` quando `outcome.error_code == "READ_IN_SAME_BATCH"`.

`redundant_tool_calls` não precisa de código: `TurnQualityCounters.note_call` (`quality.py:51-62`) já conta a repetição bem-sucedida.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/test_batch_coherence.py tests/unit/agentic/test_successful_call_dedup.py tests/unit/agentic/test_runtime_hooks.py tests/unit/agentic/test_turn_quality_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py src/agentos/agentic/tools/files/read.py tests/unit/agentic/test_batch_coherence.py
git commit -m "fix(runtime): run identical calls once per batch and refuse an edit of a file read in the same batch"
```

---

### Task 6: Stub "inalterado" só quando o conteúdo ainda está no contexto (R1.5)

**Files:**
- Modify: `src/agentos/agentic/tools/files/read.py` (`ReadTools.forget_read_content`)
- Modify: `src/agentos/agentic/runtime.py`: `results.append` em `:2474-2479` (campo `read_path`), `_tool_result_messages` `:2971-2987`, `_age_tool_results` `:2122-2143`, `_microcompact` `:1587-1633`, `_force_compact` `:1635-1646`, `_maybe_compact` `:1648-1662`, `_run_compaction` `:1681-1771`, e a chamada de envelhecimento em `:1310-1311`
- Test: `tests/unit/agentic/test_read_stub_release.py` (novo)

**Interfaces:**
- Consumes: `ReadTools.read_key(path) -> str | None` (Task 5); `ReadFileRecord.explicit_read` (o stub em `read.py:346-397` só existe com `explicit_read=True`; o guarda de edit só exige o registro e o hash).
- Produces: `ReadTools.forget_read_content(self, path: str) -> bool` (põe `explicit_read=False` no registro; `True` se mudou); resultado de `_run_toolset` ganha `"read_path": str | None` (`outcome.data["path"]` para `read_file` bem-sucedido e não `unchanged`); mensagens de resultado ganham as chaves privadas `_read_path`, `_content_shrunk`, `_read_released` (as chaves com `_` já são removidas antes do provedor, `provider_stream.py:27,844`); `AgenticTurnRuntime._release_read_stubs(self, messages: Iterable[dict[str, object]], *, folded: bool = False) -> int`.

- [ ] **Step 1: Escrever os testes que falham**

```python
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace


class _ForgetfulToolset:
    def __init__(self) -> None:
        self.forgotten: list[str] = []

    def forget_read_content(self, path: str) -> bool:
        self.forgotten.append(path)
        return True


def _runtime(toolset) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)


def test_a_forgotten_read_comes_back_with_content_and_still_allows_the_edit(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, permission_mode="full_autonomy")
    (toolset.workspace.root / "a.txt").write_bytes(b"one\n")
    toolset.invoke("read_file", {"path": "a.txt"})
    assert toolset.forget_read_content("a.txt") is True
    assert toolset.invoke("read_file", {"path": "a.txt"}).data["tool_output"]["type"] == "text"
    assert toolset.invoke("edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}).status == "succeeded"


def test_tool_result_messages_carry_the_read_path() -> None:
    messages = AgenticTurnRuntime._tool_result_messages(
        {"provider": "openrouter"}, {"id": "c1", "name": "read_file", "content": "x", "read_path": "a.txt"},
    )
    assert messages[0]["_read_path"] == "a.txt"


def test_aging_a_read_result_releases_its_stub_once() -> None:
    toolset = _ForgetfulToolset()
    runtime = _runtime(toolset)
    messages = [
        {"role": "tool", "tool_call_id": "a", "content": "x" * 5000, "_read_path": "a.txt"},
        {"role": "tool", "tool_call_id": "b", "content": "y" * 5000, "_read_path": "b.txt"},
    ]
    AgenticTurnRuntime._age_tool_results(messages, keep_recent=1)
    assert runtime._release_read_stubs(messages) == 1
    assert toolset.forgotten == ["a.txt"]
    assert runtime._release_read_stubs(messages) == 0


def test_a_short_read_that_aging_leaves_intact_keeps_its_stub() -> None:
    toolset = _ForgetfulToolset()
    messages = [{"role": "tool", "tool_call_id": "a", "content": "x" * 100, "_read_path": "a.txt"}]
    AgenticTurnRuntime._age_tool_results(messages, keep_recent=0)
    assert _runtime(toolset)._release_read_stubs(messages) == 0


def test_microcompacting_a_read_result_releases_its_stub() -> None:
    toolset = _ForgetfulToolset()
    runtime = _runtime(toolset)
    call = lambda cid: {"role": "assistant", "content": None, "tool_calls": [{"id": cid, "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}
    messages = [
        {"role": "user", "content": "task"},
        call("c0"), {"role": "tool", "tool_call_id": "c0", "content": "r" * 500, "_read_path": "a.txt"},
        call("c1"), {"role": "tool", "tool_call_id": "c1", "content": "s" * 500, "_read_path": "b.txt"},
    ]
    runtime._microcompact(messages, keep_recent=0)
    assert runtime._release_read_stubs(messages) == 1
    assert toolset.forgotten == ["a.txt"]


def test_folded_reads_are_released_even_without_shrinking() -> None:
    toolset = _ForgetfulToolset()
    folded = [{"role": "tool", "tool_call_id": "a", "content": "x", "_read_path": "a.txt"}]
    assert _runtime(toolset)._release_read_stubs(folded, folded=True) == 1
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_read_stub_release.py -v`
Expected: FAIL — `AttributeError: forget_read_content` / `_release_read_stubs`; `KeyError: '_read_path'`.

- [ ] **Step 3: Implementar**

- `forget_read_content`: resolve a chave como `read_key` (Task 5) e zera `explicit_read` sob `self._read_file_lock`.
- `_tool_result_messages`: quando `result.get("read_path")`, `messages[0]["_read_path"] = result["read_path"]`.
- `_age_tool_results` e `_microcompact`: quando reescrevem o conteúdo de uma mensagem (conteúdo novo diferente do antigo), `message["_content_shrunk"] = True`.
- `_release_read_stubs`: para cada mensagem com `_read_path`, sem `_read_released`, e com `folded=True` ou `_content_shrunk`, chama `self.toolset.forget_read_content(path)` (se existir), marca `_read_released = True` e conta.
- Chamar `self._release_read_stubs(messages)` depois do envelhecimento em `:1311`, no fim de `_force_compact`, depois do `_microcompact` em `_maybe_compact` e depois do `_age_tool_results` de `_run_compaction`; em `_run_compaction`, antes de `messages[:] = retained`, chamar `self._release_read_stubs((messages[i] for i in compact_indices), folded=True)`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/test_read_stub_release.py tests/unit/agentic/test_microcompact.py tests/unit/agentic/test_context_window.py tests/unit/agentic/test_compact_boundary.py tests/unit/agentic/test_runtime_fixes_v076.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py src/agentos/agentic/tools/files/read.py tests/unit/agentic/test_read_stub_release.py
git commit -m "fix(runtime): an aged or compacted read no longer authorizes the unchanged stub"
```

---

### Task 7: Estado de leitura por conversa; fork recebe cópia (R1.7)

`TurnSession` nasce por turno e zera o estado (`session.py:1125`); o worker cria uma sessão por turno (`workers/chat.py:1497`); o subagente recebe o **mesmo** dicionário do pai (`session.py:3287`).

**Files:**
- Create: `src/agentos/agentic/read_state.py`
- Modify: `src/agentos/agentic/session.py:946-994` (kwarg novo de `TurnSession.__init__`), `:1125`, `:3207-3226` (kwarg novo de `_toolset`), `:3287`, `:4463-4466` (chamada do toolset principal)
- Modify: `src/agentos/workers/chat.py:416-450` (`ChatWorker.__init__`) e `:1497-1500` (construção do `TurnSession`)
- Test: `tests/unit/agentic/test_read_state_across_turns.py` (novo), `tests/unit/workers/test_chat.py` (acrescentar)

**Interfaces:**
- Consumes: `ReadFileRecord` (`tools/_shared.py:84-94`).
- Produces (em `read_state.py`): `MAX_TRACKED_CONVERSATIONS = 64`; `MAX_CARRIED_READS = 200`; `class ConversationReadStates` com `__init__(self, max_conversations: int = MAX_TRACKED_CONVERSATIONS)` e `state_for(self, user_id: str, conversation_id: str) -> dict[str, ReadFileRecord]` (o mesmo dicionário para a mesma conversa; LRU com `threading.Lock`); `copy_read_state(state: Mapping[str, object]) -> dict[str, object]` (cópia rasa de cada `ReadFileRecord` via `dataclasses.replace`); `prepare_carried_state(state: MutableMapping[str, object], workspace: ConversationWorkspace) -> int` (mantém os `MAX_CARRIED_READS` registros mais recentes por `read_seq`, remove os de arquivo ausente, maior que 8 MiB ou com sha256 diferente, põe `explicit_read=False` no resto e devolve quantos removeu). `TurnSession(..., read_file_state: dict[str, object] | None = None)`; `TurnSession._toolset(..., share_read_state: bool | None = None)` (`None` = `subagents`).

- [ ] **Step 1: Escrever os testes que falham**

```python
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.read_state import ConversationReadStates, prepare_carried_state
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace


def test_the_registry_keeps_one_state_per_conversation_and_evicts_the_oldest() -> None:
    states = ConversationReadStates(max_conversations=2)
    first = states.state_for("u", "c1")
    assert states.state_for("u", "c1") is first
    assert states.state_for("u", "c2") is not first
    states.state_for("u", "c3")
    assert states.state_for("u", "c1") is not first


def _code_toolset(workspace: ConversationWorkspace, state: dict) -> AgentToolset:
    return AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy", read_file_state=state)


def test_a_carried_state_drops_changed_files_and_releases_stubs(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "same.txt").write_bytes(b"a\n")
    (workspace.root / "changed.txt").write_bytes(b"a\n")
    state: dict = {}
    turn_one = _code_toolset(workspace, state)
    turn_one.invoke("read_file", {"path": "same.txt"})
    turn_one.invoke("read_file", {"path": "changed.txt"})
    (workspace.root / "changed.txt").write_bytes(b"b\n")
    assert prepare_carried_state(state, workspace) == 1
    assert set(state) == {"same.txt"} and state["same.txt"].explicit_read is False


def test_the_next_turn_may_edit_a_file_read_in_the_previous_turn(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "a.txt").write_bytes(b"one\n")
    state: dict = {}
    _code_toolset(workspace, state).invoke("read_file", {"path": "a.txt"})
    prepare_carried_state(state, workspace)
    turn_two = _code_toolset(workspace, state)
    assert turn_two.invoke("read_file", {"path": "a.txt"}).data["tool_output"]["type"] == "text"
    assert turn_two.invoke("edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}).status == "succeeded"


def _session(tmp_path: Path, **kwargs) -> TurnSession:
    class Store:
        def main_agent_id(self, turn): return "agent:c1:main"
        def history_for_turn(self, turn): return []
        def record(self, *args, **kw): return None

    class Agents:
        def list(self): return []

    return TurnSession(
        turn={"turn_id": "t1", "conversation_id": "c1", "user_id": "local-user", "execution_id": "e1",
              "code_mode": "code", "provider": "ollama", "model_id": "m"},
        store=Store(), agents_store=Agents(), memory_store=None, provider_factory=lambda: None,
        workspace_root=tmp_path, cancelled=lambda _turn: False, **kwargs,
    )


def test_turn_session_uses_the_injected_state(tmp_path: Path) -> None:
    state: dict = {}
    assert _session(tmp_path, read_file_state=state)._read_file_state is state


def test_a_subagent_gets_a_copy_of_the_parent_read_state(tmp_path: Path) -> None:
    session = _session(tmp_path)
    (session.workspace.root / "shared.py").write_bytes(b"x = 1\n")
    (session.workspace.root / "other.py").write_bytes(b"y = 2\n")
    session._toolset(subagents=True, share_read_state=True).invoke("read_file", {"path": "shared.py"})
    sub = session._toolset(subagents=False, inherit_context=True)
    assert sub._read_file_state is not session._read_file_state
    assert set(sub._read_file_state) == {"shared.py"}
    sub.invoke("read_file", {"path": "other.py"})
    assert "other.py" not in session._read_file_state
```

Em `tests/unit/workers/test_chat.py`, ao lado de `test_unlimited_runtime_setting_removes_the_action_limit` e com os mesmos `monkeypatch` dele:

```python
def test_turns_of_one_conversation_share_a_read_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # same Settings/SkillLibrary doubles and monkeypatches as the test above
    captured: list[object] = []

    def capture_session(**kwargs):
        captured.append(kwargs["read_file_state"])
        return type("Session", (), {"build_runtime": lambda self: object()})()

    monkeypatch.setattr(chat_module, "TurnSession", capture_session)
    worker = ChatWorker(Store(), runtime_settings=Settings())
    worker._runtime_for(dict(TURN))
    worker._runtime_for(dict(TURN))
    worker._runtime_for({**TURN, "conversation_id": "conversation-2"})
    assert captured[0] is captured[1] and captured[2] is not captured[0]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_read_state_across_turns.py tests/unit/workers/test_chat.py -v -k "read_state or registry or carried or next_turn or subagent_gets or injected"`
Expected: FAIL — `ModuleNotFoundError: agentos.agentic.read_state`.

- [ ] **Step 3: Implementar**

- `read_state.py` como nas Interfaces.
- `TurnSession.__init__`: `self._read_file_state = read_file_state if read_file_state is not None else {}`; se foi injetado e não está vazio, `prepare_carried_state(self._read_file_state, self.workspace)` (em `try`, falha nunca impede o turno).
- `_toolset`: `share = subagents if share_read_state is None else share_read_state`; `read_file_state=self._read_file_state if share else (copy_read_state(self._read_file_state) if inherit_context else {})`. A chamada principal em `:4463` passa `share_read_state=True` (o toolset principal compartilha mesmo com `enable_subagents=False`, porque `inject_text_attachments_into_history` grava no estado da sessão).
- `ChatWorker.__init__`: `self._read_states = ConversationReadStates()`; a construção do `TurnSession` passa `read_file_state=self._read_states.state_for(str(turn["user_id"]), str(turn["conversation_id"]))`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/test_read_state_across_turns.py tests/unit/workers/test_chat.py tests/unit/test_orin_code_phase_b.py tests/unit/test_orin_code_phase_d.py tests/unit/hooks/test_session_hooks_w2.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/read_state.py src/agentos/agentic/session.py src/agentos/workers/chat.py tests/unit/agentic/test_read_state_across_turns.py tests/unit/workers/test_chat.py
git commit -m "fix(session): keep the read state per conversation and give a fork a copy"
```

---

### Task 8: Nada do Orin no git do usuário — `.orin/uploads` e `.git/info/exclude` (R1.10)

Arquivos do Orin dentro do workspace hoje: `uploads/` (`uploads/promotion.py:18,49-59`), `.orin/state/cwd.json` (`tools/shell/bash.py:44,118-127`), `.orin/logs` (`tools/_shared.py:132-133`), `.orin/tool-outputs` (`agent_tools.py:1098-1104`) e `.orin/plans` (`tools/planning/plan_mode.py`). Com uploads dentro de `.orin/` e a linha `.orin/` no exclude, todos somem do `git status`.

**Files:**
- Create: `src/agentos/agentic/git_exclude.py`
- Modify: `src/agentos/agentic/workspace.py:28-38` (constantes e helper de upload)
- Modify: `src/agentos/uploads/promotion.py:18,49-59`
- Modify: `src/agentos/agentic/tools/files/read.py:340-341` e descrições em `:680,692` (`e.g. .orin/uploads/nota.pdf`)
- Modify: `src/agentos/permissions/paths.py:25` (`_ORIN_WRITABLE` ganha `"uploads"`)
- Modify: `src/agentos/agentic/session.py:1097-1101` (logo depois de resolver o workspace)
- Test: `tests/unit/agentic/test_git_exclude.py` (novo), `tests/unit/uploads/test_promotion.py`, `tests/integration/agentic/test_attachment_turn.py:143-149`

**Interfaces:**
- Produces (em `git_exclude.py`): `find_git_dir(start: Path) -> Path | None` (sobe de `start` até a raiz; `.git` diretório → ele; `.git` arquivo `gitdir: <p>` → `p` resolvido relativo ao arquivo); `git_common_dir(git_dir: Path) -> Path` (`git_dir / (commondir)` quando existe o arquivo `commondir`, senão `git_dir`); `ensure_orin_excluded(root: Path) -> bool` (garante a linha `.orin/` em `<common>/info/exclude`, preservando o conteúdo e antecedida de `# Orin: local agent state and attachments`; `True` só quando escreveu; nunca levanta).
- Produces (em `workspace.py`): `UPLOAD_DIRECTORY = ".orin/uploads"`, `LEGACY_UPLOAD_DIRECTORY = "uploads"`, `is_upload_path(key: str) -> bool` (prefixo `.orin/uploads/` ou `uploads/`). `promotion.py` reexporta `UPLOAD_DIRECTORY`.

- [ ] **Step 1: Escrever os testes que falham**

```python
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.git_exclude import ensure_orin_excluded
from agentos.agentic.workspace import ConversationWorkspace
from agentos.permissions.paths import is_protected_path


def _repo(path: Path) -> Path:
    (path / ".git" / "info").mkdir(parents=True)
    return path


def _exclude_lines(git_dir: Path) -> list[str]:
    return (git_dir / "info" / "exclude").read_text(encoding="utf-8").splitlines()


def test_the_exclude_gets_one_orin_line_and_keeps_what_was_there(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / ".git" / "info" / "exclude").write_text("*.log\n", encoding="utf-8")
    assert ensure_orin_excluded(repo) is True
    assert ensure_orin_excluded(repo) is False
    lines = _exclude_lines(repo / ".git")
    assert lines[0] == "*.log" and lines.count(".orin/") == 1


def test_a_folder_that_is_not_a_repository_is_left_alone(tmp_path: Path) -> None:
    assert ensure_orin_excluded(tmp_path) is False
    assert not (tmp_path / ".git").exists()


def test_a_subfolder_workspace_writes_the_repo_exclude(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "packages" / "app").mkdir(parents=True)
    assert ensure_orin_excluded(repo / "packages" / "app") is True
    assert ".orin/" in _exclude_lines(repo / ".git")


def test_a_worktree_writes_the_common_exclude(tmp_path: Path) -> None:
    main = _repo(tmp_path / "main")
    worktree_git = main / ".git" / "worktrees" / "wt"
    worktree_git.mkdir(parents=True)
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {worktree_git}\n", encoding="utf-8")
    assert ensure_orin_excluded(worktree) is True
    assert ".orin/" in _exclude_lines(main / ".git")


def test_uploads_inside_orin_are_not_protected_paths(tmp_path: Path) -> None:
    assert is_protected_path(tmp_path / ".orin" / "uploads" / "a.csv", workspace_root=tmp_path) is False


def test_new_and_legacy_uploads_are_both_read_as_uploads(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    body = b"".join(f"line {i}\n".encode() for i in range(1, 1001))
    for relative in (".orin/uploads/new.txt", "uploads/old.txt"):
        target = toolset.workspace.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        assert toolset.invoke("read_file", {"path": relative}).payload["end_line"] == 1000
```

Em `tests/unit/uploads/test_promotion.py`: trocar `"uploads/foto.png"` por `".orin/uploads/foto.png"`, `workspace.root / "uploads" / "foto.png"` por `workspace.root / ".orin" / "uploads" / "foto.png"`, `"uploads/foto (2).png"` por `".orin/uploads/foto (2).png"`, e acrescentar:

```python
def test_promotion_into_a_repository_excludes_orin_from_git(tmp_path):
    staging = UploadStaging(tmp_path / "staging")
    workspace = ConversationWorkspace.at_root(tmp_path / "repo")
    (workspace.root / ".git" / "info").mkdir(parents=True)
    staged = staging.store("user-1", "foto.png", PNG)
    promote_uploads(staging, workspace, "user-1", [staged.upload_id])
    assert ".orin/" in (workspace.root / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
```

Em `tests/integration/agentic/test_attachment_turn.py:149`, esperar `".orin/uploads/foto.png"` (e corrigir a docstring em `:143`).

Teste de sessão em `test_git_exclude.py` (construtor como o `_session` da Task 7, com `"workspace_root_path": str(repo)` no turno): depois de construir o `TurnSession`, `".orin/" in _exclude_lines(repo / ".git")`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_git_exclude.py tests/unit/uploads/test_promotion.py -v`
Expected: FAIL — `ModuleNotFoundError: agentos.agentic.git_exclude`; caminhos `uploads/…`.

- [ ] **Step 3: Implementar**

- `git_exclude.py` como nas Interfaces (`OSError` → `False`; o texto anexado começa com `\n` quando o arquivo existente não termina em quebra de linha; uma linha já existente igual a `.orin/`, `.orin`, `/.orin/` ou `/.orin` conta como presente).
- `promote_uploads`: `directory = workspace.root / UPLOAD_DIRECTORY`; registros com `f"{UPLOAD_DIRECTORY}/{name}"`; depois do `mkdir`, `ensure_orin_excluded(workspace.root)`.
- `read.py:341`: `is_upload_file = is_upload_path(norm_check)`.
- `_ORIN_WRITABLE = ("plans", "worktrees", "tool-outputs", "logs", "uploads")` — sem isso, todo comando de shell que cita `.orin/uploads/...` pediria aprovação (`permissions/engine.py:336-347`), o que não acontecia com `uploads/`.
- `TurnSession.__init__`, logo depois de `self.workspace = resolve_workspace(...)`: `ensure_orin_excluded(self.workspace.root)` em `try/except Exception`.

`_IGNORED_CHANGE_PREFIXES` (`workspace.py:31-38`) e `_record_changed_paths` (`agent_tools.py:1168-1170`) já ignoram `.orin/` e mantêm `uploads/` para os anexos antigos; o frontend já trata `.orin/` como interno (`ChatPage.tsx:1991-1997`, `TeamActivityView.tsx:446-451`). Nada a mudar ali.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/test_git_exclude.py tests/unit/uploads tests/unit/conversations/test_chat_attachments.py tests/unit/agentic/test_pre_read_attachments.py tests/unit/agentic/test_view_file_tool.py tests/unit/permissions tests/integration/agentic/test_attachment_turn.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/git_exclude.py src/agentos/agentic/workspace.py src/agentos/uploads/promotion.py src/agentos/agentic/tools/files/read.py src/agentos/permissions/paths.py src/agentos/agentic/session.py tests/unit/agentic/test_git_exclude.py tests/unit/uploads/test_promotion.py tests/integration/agentic/test_attachment_turn.py
git commit -m "fix(workspace): keep attachments in .orin/uploads and exclude .orin/ from the user's git"
```

---

### Task 9: `run_command` roda pelo Git Bash no Windows (R1.2)

**Decisões fixadas aqui:**
- **Descoberta do `bash.exe`** (só Windows; resultado em cache por processo), nesta ordem: (1) `ORIN_GIT_BASH_PATH` — o valor `none` desliga o Git Bash; um caminho inexistente é ignorado; (2) `CLAUDE_CODE_GIT_BASH_PATH` (a mesma variável do Claude Code); (3) a partir de `shutil.which("git")`: para cada um dos três primeiros pais de `git.exe` (`<raiz>\cmd\git.exe`, `<raiz>\bin\git.exe`, `<raiz>\mingw64\bin\git.exe`), `<pai>\bin\bash.exe`; (4) `%ProgramFiles%`, `%ProgramW6432%`, `%ProgramFiles(x86)%` e `%LOCALAPPDATA%\Programs` + `\Git\bin\bash.exe`. **Nunca** `shutil.which("bash")` (no Windows costuma ser `C:\Windows\System32\bash.exe`, o WSL) nem nada dentro do workspace. Usa-se `Git\bin\bash.exe` (o lançador que põe `/usr/bin` e `/mingw64/bin` no `PATH`), não `Git\usr\bin\bash.exe`.
- **argv:** `[str(bash), "-c", command]` com `shell=False` (o `Popen` já aceita `argv`, `bash.py:186,237`); sem `-l` (o login lê `/etc/profile`, é lento e muda o cwd sem `CHERE_INVOKING`). O PowerShell continua com o próprio `argv` (`powershell.py:34-39`).
- **Dialeto:** `posix` com Git Bash ou fora do Windows, `cmd` no fallback, `powershell` para a ferramenta PowerShell. O mesmo valor vai para o pré-check de `cd` e para o motor de permissões (`permissions/shell.py:35-36`).
- **`cd`:** separadores por dialeto — `posix`: `&&`, `||`, `;`, quebra de linha; `cmd`: `&&`, `||`, `&`; `powershell`: `;`, `&&`, `||`, quebra de linha. Padrão de `cd` — `posix`: `^cd\s+(.+)$` (sem `/d`, que no bash é a unidade D:); `cmd` e `powershell`: o atual `(?i)^cd(?:\s+/d)?\s+(.+)$`. No dialeto `posix` no Windows, o alvo passa por `posix_to_windows_path` antes de virar `Path`.
- **Conversão de caminhos:** `/c/...` ↔ `C:\...` (letra de unidade única seguida de `/` ou fim), `~` e `~/...` → `Path.home()`; o resto (relativo, `C:\...`, `/tmp`) fica igual.
- **`environment_facts`:** passa a usar o shell real e ganha `platform` (`sys.platform`), `os_version` (`platform.version()`) e `shell_dialect`. Rótulos: `bash (Git Bash, <caminho>)`, `cmd.exe (Git Bash unavailable)`, `sh`.

**Files:**
- Create: `src/agentos/host_shell.py`
- Modify: `src/agentos/agentic/tools/shell/bash.py:81-103` (`_resolve_cwd_after_command`), `:129-137` (`run_command`), `:139-150,168` (`_run_shell`: kwarg `dialect` e o pré-check)
- Modify: `src/agentos/permissions/shell.py:35-36` (`default_shell`)
- Modify: `src/agentos/agentic/session.py:266-282` (`environment_facts`) e `:3016-3023` (prompt do subagente: a frase "on Windows cmd.exe, never run Unix commands…" só quando `shell_dialect == "cmd"`)
- Test: `tests/unit/test_host_shell.py` (novo); ajustar `tests/unit/permissions/test_shell.py:16-19`, `tests/unit/agentic/test_turn_session.py:1083-1090`, `tests/unit/test_orin_code_phase_b.py:449-500`

**Interfaces:**
- Produces (em `host_shell.py`): `@dataclass(frozen=True, slots=True) class HostShell: kind: Literal["git_bash", "cmd", "sh"]; dialect: Literal["posix", "cmd"]; executable: str; label: str`; `_is_windows() -> bool`; `_discover_git_bash(environ: Mapping[str, str], which: Callable[[str], str | None]) -> Path | None`; `find_git_bash() -> Path | None` (`None` fora do Windows; cache com `functools.lru_cache`); `current_host_shell() -> HostShell`; `shell_argv(command: str, shell: HostShell) -> list[str] | None` (`None` = manter `shell=True`); `posix_to_windows_path(text: str) -> str`; `windows_to_posix_path(path: str | Path) -> str`.
- Produces (em `bash.py`): `_resolve_cwd_after_command(self, command: str, starting_cwd: Path, *, precheck: bool, dialect: str | None = None) -> Path` e `_run_shell(..., dialect: str | None = None)` (`None` → `"powershell"` se `tool_name == "powershell"`, senão `"cmd"` no Windows e `"posix"` fora).
- `environment_facts() -> dict[str, str]` com as chaves `os`, `os_version`, `platform`, `shell`, `shell_dialect`, `python`, `available`.

- [ ] **Step 1: Escrever os testes que falham**

```python
from pathlib import Path

import pytest

from agentos import host_shell
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.permissions.shell import default_shell


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_discovery_prefers_the_explicit_setting(tmp_path: Path) -> None:
    bash = _file(tmp_path / "custom" / "bash.exe")
    assert host_shell._discover_git_bash({"ORIN_GIT_BASH_PATH": str(bash)}, which=lambda _n: None) == bash


def test_discovery_derives_bash_from_git_on_path(tmp_path: Path) -> None:
    git = _file(tmp_path / "Git" / "cmd" / "git.exe")
    bash = _file(tmp_path / "Git" / "bin" / "bash.exe")
    assert host_shell._discover_git_bash({}, which=lambda n: str(git) if n == "git" else None) == bash


def test_discovery_falls_back_to_program_files(tmp_path: Path) -> None:
    bash = _file(tmp_path / "PF" / "Git" / "bin" / "bash.exe")
    assert host_shell._discover_git_bash({"ProgramFiles": str(tmp_path / "PF")}, which=lambda _n: None) == bash


def test_discovery_never_uses_the_wsl_launcher(tmp_path: Path) -> None:
    assert host_shell._discover_git_bash({}, which=lambda n: r"C:\Windows\System32\bash.exe" if n == "bash" else None) is None


def test_discovery_can_be_turned_off(tmp_path: Path) -> None:
    _file(tmp_path / "PF" / "Git" / "bin" / "bash.exe")
    environ = {"ORIN_GIT_BASH_PATH": "none", "ProgramFiles": str(tmp_path / "PF")}
    assert host_shell._discover_git_bash(environ, which=lambda _n: None) is None


def test_posix_and_windows_paths_convert() -> None:
    assert host_shell.posix_to_windows_path("/c/Users/Some Name/proj") == r"C:\Users\Some Name\proj"
    assert host_shell.posix_to_windows_path("/d") == "D:\\"
    assert host_shell.posix_to_windows_path("sub/dir") == "sub/dir"
    assert host_shell.windows_to_posix_path(r"C:\Users\Some Name\proj") == "/c/Users/Some Name/proj"


def test_without_git_bash_windows_falls_back_to_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_shell, "_is_windows", lambda: True)
    monkeypatch.setattr(host_shell, "find_git_bash", lambda: None)
    shell = host_shell.current_host_shell()
    assert (shell.kind, shell.dialect) == ("cmd", "cmd") and "cmd.exe" in shell.label
    assert host_shell.shell_argv("echo hi", shell) is None
    assert default_shell() == "cmd"


def test_with_git_bash_the_argv_is_bash_dash_c(monkeypatch: pytest.MonkeyPatch) -> None:
    exe = Path(r"C:\Program Files\Git\bin\bash.exe")
    monkeypatch.setattr(host_shell, "_is_windows", lambda: True)
    monkeypatch.setattr(host_shell, "find_git_bash", lambda: exe)
    shell = host_shell.current_host_shell()
    assert shell.dialect == "posix" and "Git Bash" in shell.label
    assert host_shell.shell_argv("echo hi", shell) == [str(exe), "-c", "echo hi"]
    assert default_shell() == "posix"


def test_command_separators_follow_the_dialect(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    sub = toolset.workspace.root / "sub"
    sub.mkdir()
    root = toolset.workspace.root
    assert toolset._resolve_cwd_after_command("echo x & cd sub", root, precheck=False, dialect="cmd") == sub.resolve()
    assert toolset._resolve_cwd_after_command("true || cd sub", root, precheck=False, dialect="posix") == sub.resolve()


@pytest.mark.skipif(not host_shell._is_windows(), reason="POSIX drive paths exist only on Windows")
def test_posix_cd_with_spaces_is_understood(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    target = toolset.workspace.root / "Some Name"
    target.mkdir()
    command = f'cd "{host_shell.windows_to_posix_path(target)}"'
    assert toolset._resolve_cwd_after_command(command, toolset.workspace.root, precheck=True, dialect="posix") == target.resolve()


@pytest.mark.skipif(host_shell.find_git_bash() is None, reason="needs Git Bash")
def test_run_command_runs_through_git_bash(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    target = toolset.workspace.root / "sub dir"
    target.mkdir()
    outcome = toolset.invoke("run_command", {"command": 'echo "$((2+3))" && printf "%s\\n" "a b"'})
    assert outcome.status == "succeeded" and "5" in outcome.content and "a b" in outcome.content
    moved = toolset.invoke("run_command", {"command": f'cd "{host_shell.windows_to_posix_path(target)}" && pwd'})
    assert moved.status == "succeeded"
    assert toolset._current_cwd == target.resolve()
```

Ajustes em testes existentes que dependiam do cmd.exe implícito nesta máquina: em `tests/unit/permissions/test_shell.py::test_default_shell_matches_platform`, trocar por `monkeypatch.setattr(host_shell, "_is_windows", lambda: False)` e esperar `"posix"`; em `tests/unit/agentic/test_turn_session.py::test_environment_facts_report_bin_sh_on_posix_regardless_of_preferred_shell`, acrescentar `monkeypatch.setattr(host_shell, "_is_windows", lambda: False)`; em `tests/unit/test_orin_code_phase_b.py::test_subagent_isolates_read_file_state_when_inherit_context_false_and_warns_windows_shell`, acrescentar `monkeypatch.setattr(host_shell, "_is_windows", lambda: True)` e `monkeypatch.setattr(host_shell, "find_git_bash", lambda: None)` (o teste ganha o parâmetro `monkeypatch`).

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/test_host_shell.py -v`
Expected: FAIL — `ImportError: cannot import name 'host_shell'`.

- [ ] **Step 3: Implementar**

- `host_shell.py` como nas Decisões e Interfaces; `current_host_shell()` monta `HostShell("git_bash", "posix", str(exe), f"bash (Git Bash, {exe})")`, `HostShell("cmd", "cmd", os.environ.get("COMSPEC") or "cmd.exe", "cmd.exe (Git Bash unavailable)")` ou `HostShell("sh", "posix", "/bin/sh", "sh")`.
- `run_command`: `shell = current_host_shell()`; `argv = shell_argv(command, shell) if isinstance(command, str) and command.strip() else None`; chama `_run_shell(..., argv=argv, dialect=shell.dialect)`.
- `_run_shell`: resolve o `dialect` e passa para as duas chamadas de `_resolve_cwd_after_command` (`:168` e `:578`).
- `_resolve_cwd_after_command`: tabelas de separadores e de padrão de `cd` por dialeto (módulo, compiladas); o pré-check de "comando é só um `cd`" usa o padrão do dialeto.
- `default_shell()`: `from agentos.host_shell import current_host_shell; return current_host_shell().dialect`.
- `environment_facts()`: shell via `current_host_shell()`; mantém `os`, `python` e `available`; acrescenta `os_version`, `platform`, `shell_dialect`. No prompt do subagente (`session.py:3020`), a parte "(on Windows cmd.exe, never run Unix commands like cat, ls, grep, head, or tail)" só entra quando `environment.get("shell_dialect") == "cmd"`; com `posix`, entra "(Unix syntax: forward slashes, /dev/null, $VAR)".

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/test_host_shell.py tests/unit/permissions tests/unit/agentic/test_agent_tools.py tests/unit/agentic/tools/test_bash_parity.py tests/unit/agentic/tools/test_w4_review_fixes.py tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py tests/unit/agentic/test_turn_session.py tests/unit/test_orin_code_phase_b.py tests/unit/test_orin_code_phase_c.py tests/unit/agentic/test_additional_directories.py -v`
Expected: PASS. Se outro teste de `tests/unit/permissions` ou `tests/unit/code_mode` falhar por supor `cmd` no Windows, fixar o dialeto nele (`shell="cmd"` explícito ou `monkeypatch` de `host_shell.find_git_bash`), nunca mudar o comportamento de produção para agradá-lo.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/host_shell.py src/agentos/agentic/tools/shell/bash.py src/agentos/permissions/shell.py src/agentos/agentic/session.py tests/unit/test_host_shell.py tests/unit/permissions/test_shell.py tests/unit/agentic/test_turn_session.py tests/unit/test_orin_code_phase_b.py
git commit -m "feat(shell): run Bash through Git Bash on Windows, with cmd.exe only as a fallback"
```

---

### Task 10: Seção `# Environment` completa no prompt do Orin Code (R1.3)

`render_code_turn_context` (`code_mode/system_prompt.py:156-185`) já tem `# Environment` com diretório, SO, shell, Python e ferramentas. Falta o que D9 pede: se é repositório git, plataforma, versão do SO, data e as regras do shell real; e o prompt estável (`:87`) ainda fala de `&` e `nohup`.

**Files:**
- Modify: `src/agentos/code_mode/system_prompt.py:15` (`PROMPT_VERSION = "orin-code-w3.2"`), `:87`, `:156-185`
- Modify: `src/agentos/agentic/session.py:4147-4157` (`_code_prompt_parts`)
- Test: `tests/unit/agentic/test_code_prompt.py` (acrescentar)

**Interfaces:**
- Consumes: `environment_facts()` (Task 9: `os_version`, `platform`, `shell_dialect`); `find_git_dir` (Task 8); `windows_to_posix_path` (Task 9).
- Produces: `render_code_turn_context(..., is_git_repo: bool = False, today: str = "", workspace_root_in_shell: str = "")`.

- [ ] **Step 1: Escrever os testes que falham**

```python
BASH_ENV = {
    "os": "Windows 11", "os_version": "10.0.26200", "platform": "win32",
    "shell": r"bash (Git Bash, C:\Program Files\Git\bin\bash.exe)", "shell_dialect": "posix",
    "python": "3.12", "available": "git, uv",
}


def _context(environment, **extra):
    return render_code_turn_context(
        permission_mode="accept_edits", plan_path=None, permits_push=False, environment=environment,
        workspace_root=r"C:\work", workspace_is_local=True, git_status="", public_names=True, **extra,
    )


def test_the_environment_section_has_the_facts_claude_code_gives() -> None:
    text = _context(BASH_ENV, is_git_repo=True, today="2026-11-05", workspace_root_in_shell="/c/work")
    for expected in (
        "# Environment", "Is a git repository: yes", "Platform: win32", "OS Version: Windows 11 10.0.26200",
        "Git Bash", "Today's date: 2026-11-05", "/c/work", "/dev/null",
    ):
        assert expected in text


def test_a_cmd_shell_says_unix_commands_do_not_exist() -> None:
    text = _context({**BASH_ENV, "shell": "cmd.exe (Git Bash unavailable)", "shell_dialect": "cmd"}, is_git_repo=False)
    assert "Is a git repository: no" in text
    assert "cmd.exe has no Unix commands" in text and "/dev/null" not in text


def test_the_stable_prompt_names_no_shell_specific_syntax() -> None:
    assert "nohup" not in render_code_system_prompt(_options())
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_code_prompt.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'is_git_repo'`; `nohup` presente.

- [ ] **Step 3: Implementar**

Linhas da seção, nesta ordem (as atuais de diretório, Python e PATH continuam):

```text
- Working directory: {workspace_root} ({where}). File paths are relative to it.   [+ " In Bash it is {workspace_root_in_shell}." quando não vazio]
- Is a git repository: {yes|no}
- Platform: {platform}
- OS Version: {os} {os_version}
- {Bash label} runs through: {shell}; use that shell's syntax.
- [posix no Windows] Use Unix shell syntax: forward slashes, /dev/null (not NUL), $VAR (not %VAR%); quote Windows paths ("C:\...").
- [cmd] cmd.exe has no Unix commands (cat, ls, grep, head, tail, nohup, & for background): use the dedicated file tools, cmd syntax, or run_in_background.
- Python: ... Also on PATH: ...
- Today's date: {today}   [só quando não vazio]
```

No prompt estável (`:87`), "instead of using `&` or `nohup`" vira "instead of backgrounding them in the shell"; `PROMPT_VERSION` sobe para `orin-code-w3.2`. Em `_code_prompt_parts`, passar `is_git_repo=find_git_dir(self.workspace.root) is not None`, `today=date.today().isoformat()` e `workspace_root_in_shell=windows_to_posix_path(self.workspace.root) if os.name == "nt" and environment.get("shell_dialect") == "posix" else ""`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/test_code_prompt.py tests/unit/agentic/test_prompt_snapshot.py tests/unit/agentic/test_prefix_stability.py tests/unit/agentic/test_layered_prompt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/code_mode/system_prompt.py src/agentos/agentic/session.py tests/unit/agentic/test_code_prompt.py
git commit -m "feat(code-mode): complete the Environment section with git, platform, OS version, date and shell rules"
```

---

### Task 11: Estágios do code mode com referência (R1.9, parte 1)

Hoje o estágio vai para "implementing" no **início** de qualquer edit (`session.py:1333-1337`), mesmo que falhe, e para "fixing" em qualquer verificação com falha (`session.py:1467-1479`), mesmo quando a falha já existia antes de qualquer mudança.

**Mecanismo de referência:** por turno, a primeira observação de cada chave de verificação vira a referência. Chaves: `verify:<step>` para cada etapa executada de `verify_project` (passou/falhou e a saída da etapa); `cmd:<comando com espaços normalizados>` para `run_command`/`powershell` que `is_verification_command` reconhece. Se a primeira observação de uma chave acontece **antes** de qualquer edit bem-sucedido do turno, ela é referência limpa: falhou → guarda a impressão digital da falha e **não** vai para "fixing" (falha pré-existente); passou → referência vazia. Se acontece **depois** de um edit, não há referência: falha → "fixing" (comportamento atual). Observações seguintes com referência só vão para "fixing" quando a impressão digital tem algum item fora da referência.

**Impressão digital:** conjunto de identificadores extraídos da saída — pytest `^(FAILED|ERROR) (\S+)`; tsc `(\S+\(\d+,\d+\)): error (TS\d+)`; ruff/flake8 `^(\S+:\d+:\d+): ([A-Z]+\d+)`; go `^--- FAIL: (\S+)`; cargo `^test (\S+) \.\.\. FAILED`. Sem nenhum identificador reconhecido, `{f"exit:{exit_code}"}`.

**Files:**
- Create: `src/agentos/code_mode/verification_baseline.py`
- Modify: `src/agentos/agentic/session.py:1333-1337` (sai o "implementing" do `tool_started`), `:1433-1479` (`tool_finished`)
- Test: `tests/unit/code_mode/test_verification_baseline.py` (novo), `tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py` (acrescentar, reaproveitando `_make_minimal_session`)

**Interfaces:**
- Produces: `failure_fingerprint(output: str, *, exit_code: int | None = None) -> frozenset[str]`; `class VerificationBaseline` com `observe(self, key: str, *, failed: bool, fingerprint: frozenset[str], after_edit: bool) -> bool` (`True` = falha nova → "fixing"); `verification_observations(name: str, payload: Mapping[str, object], content: str, arguments: Mapping[str, object]) -> list[tuple[str, bool, frozenset[str]]]`. Na sessão, atributos criados sob demanda (`getattr` com padrão, porque `_make_minimal_session` usa `object.__new__`): `_verification_baseline: VerificationBaseline` e `_code_mode_edit_succeeded: bool`.

- [ ] **Step 1: Escrever os testes que falham**

Em `tests/unit/code_mode/test_verification_baseline.py`:

```python
from agentos.code_mode.verification_baseline import VerificationBaseline, failure_fingerprint


def test_pytest_failures_become_their_test_ids() -> None:
    output = "FAILED tests/test_x.py::test_a - AssertionError\nERROR tests/test_y.py::test_b\n1 failed"
    assert failure_fingerprint(output) == {"tests/test_x.py::test_a", "tests/test_y.py::test_b"}


def test_unrecognized_output_falls_back_to_the_exit_code() -> None:
    assert failure_fingerprint("boom", exit_code=2) == {"exit:2"}


def test_a_failure_seen_before_any_edit_is_the_reference() -> None:
    baseline = VerificationBaseline()
    old = frozenset({"t::old"})
    assert baseline.observe("cmd:pytest", failed=True, fingerprint=old, after_edit=False) is False
    assert baseline.observe("cmd:pytest", failed=True, fingerprint=old, after_edit=True) is False
    assert baseline.observe("cmd:pytest", failed=True, fingerprint=old | {"t::new"}, after_edit=True) is True


def test_without_a_reference_a_failure_after_an_edit_is_new() -> None:
    assert VerificationBaseline().observe("cmd:pytest", failed=True, fingerprint=frozenset({"t::a"}), after_edit=True) is True
```

Em `tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py`:

```python
def _edit(session, inv: str, status: str) -> None:
    session.emit_lifecycle(session.turn, "tool_started", tool_name="edit_file", invocation_id=inv, path="a.py")
    session.emit_lifecycle(session.turn, "tool_finished", tool_name="edit_file", invocation_id=inv, status=status,
                           summary="edit", tool_payload={}, tool_arguments={"path": "a.py"})


def _pytest(session, inv: str, failed_tests: list[str]) -> None:
    failed = bool(failed_tests)
    content = "exit=1\n" + "\n".join(f"FAILED tests/test_x.py::{t} - AssertionError" for t in failed_tests) if failed else "exit=0\n1 passed"
    session.emit_lifecycle(session.turn, "tool_started", tool_name="run_command", invocation_id=inv, command="pytest -q")
    session.emit_lifecycle(session.turn, "tool_finished", tool_name="run_command", invocation_id=inv, status="succeeded",
                           summary="$ pytest -q", tool_payload={"command": "pytest -q", "failed": failed, "exit_code": 1 if failed else 0},
                           tool_arguments={"command": "pytest -q"}, tool_content=content)


def test_a_failed_edit_does_not_enter_implementing(tmp_path: Path) -> None:
    session, _store, _ = _make_minimal_session(tmp_path)
    session._code_mode_stage = "validating_done"
    _edit(session, "e1", "failed")
    assert session._code_mode_stage == "validating_done"
    _edit(session, "e2", "succeeded")
    assert session._code_mode_stage == "implementing"


def test_a_failure_that_existed_before_any_edit_is_not_fixing(tmp_path: Path) -> None:
    session, store, _ = _make_minimal_session(tmp_path)
    _pytest(session, "v1", ["test_old"])
    assert session._code_mode_stage != "fixing"
    _edit(session, "e1", "succeeded")
    _pytest(session, "v2", ["test_old"])
    assert session._code_mode_stage != "fixing"
    assert store.events[-1][2].get("preexisting") is True


def test_a_new_failure_after_an_edit_is_fixing(tmp_path: Path) -> None:
    session, _store, _ = _make_minimal_session(tmp_path)
    _pytest(session, "v1", ["test_old"])
    _edit(session, "e1", "succeeded")
    _pytest(session, "v2", ["test_old", "test_new"])
    assert session._code_mode_stage == "fixing"


def test_a_failure_with_no_reference_before_the_edit_is_fixing(tmp_path: Path) -> None:
    session, _store, _ = _make_minimal_session(tmp_path)
    _edit(session, "e1", "succeeded")
    _pytest(session, "v1", ["test_a"])
    assert session._code_mode_stage == "fixing"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/code_mode/test_verification_baseline.py tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py -v -k "fingerprint or reference or edit or fixing or failure"`
Expected: FAIL — `ModuleNotFoundError` e estágio "fixing"/"implementing" nos casos errados.

- [ ] **Step 3: Implementar**

- `verification_baseline.py` como no Mecanismo e nas Interfaces. `verification_observations`: `verify_project` sem `scaffold` → uma observação por etapa com `ran=True` (`failed = not passed`, impressão da `output` da etapa); `run_command`/`powershell` reconhecido por `is_verification_command` → uma observação (`failed = status != "succeeded" or payload["failed"]`, impressão de `content` com `payload.get("exit_code")`); outros → `[]`.
- `tool_started` (`:1333-1337`): remove a troca para "implementing" (a de "validating" fica).
- `tool_finished`: edit/write/notebook_edit com `status == "succeeded"` → `_code_mode_edit_succeeded = True` e, se o estágio não for "implementing", troca e registra o mesmo evento `CODE_MODE_STAGE_CHANGED` de hoje. No ramo de verificação (`:1467-1479`, sem mexer no ramo de `scaffold`): `new_failure = any(baseline.observe(key, failed=f, fingerprint=fp, after_edit=edited) for key, f, fp in observations)` (avaliar todas, sem curto-circuito); `cmd_failed` como hoje; `cmd_failed and new_failure` → "fixing" como hoje; `cmd_failed and not new_failure` → estágio "validating_done" e evento `CODE_MODE_VALIDATION_FINISHED` com resumo `"Validação concluída com falhas já existentes"` e payload `{"stage": "validating", "status": "failed", "preexisting": True, "error_code": ...}`; sem falha → como hoje.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/code_mode/test_verification_baseline.py tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py tests/unit/test_orin_code_phase_c.py tests/unit/agentic/test_phase_controller.py tests/unit/agentic/test_phase_runtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/code_mode/verification_baseline.py src/agentos/agentic/session.py tests/unit/code_mode/test_verification_baseline.py tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py
git commit -m "fix(code-mode): enter implementing only after a successful edit and fixing only on a new failure"
```

---

### Task 12: `verify_project` com o executor do projeto, sem `pip install` e cancelável (R1.9, parte 2)

**Files:**
- Modify: `src/agentos/agentic/diagnostics.py:25-35` (`ProjectRecipe`), `:98-114` (`_python_recipe`)
- Modify: `src/agentos/agentic/tools/orin/verify.py:70-90` (laço das etapas)
- Test: `tests/unit/agentic/test_diagnostics.py` (acrescentar), `tests/unit/agentic/tools/test_verify_runner.py` (novo); ajustar `tests/unit/agentic/test_agent_tools.py:124-139`

**Interfaces:**
- Produces: `ProjectRecipe.opt_in_steps: frozenset[str] = frozenset()` (etapas que só rodam quando pedidas em `steps`; `available_steps()` as exclui; `command_for` continua devolvendo); `python_runner(root: Path) -> str` em `diagnostics.py` — `"uv run python"` com `uv.lock`, senão `"poetry run python"` com `poetry.lock`, senão `f'"{rel}"'` para o primeiro existente entre `.venv` e `venv` (`Scripts\python.exe` no Windows, `bin/python` fora; `rel` relativo à raiz, separador do SO), senão `"python"`; `_run_verification_step(command: str, *, cwd: Path, timeout: int, cancelled: Callable[[], bool]) -> tuple[bool, str]` em `verify.py` (levanta `AgentToolError("Interrupted by user: the verification was stopped.", error_code="INTERRUPTED_BY_USER")` ao cancelar).
- Comandos Python: lint `f"{runner} -m ruff check ."`, typecheck `f"{runner} -m mypy ."` / `f"{runner} -m pyright"`, test `f"{runner} -m pytest"`; install (sempre em `opt_in_steps`) `"uv sync"`, `"poetry install"`, `f"{runner} -m pip install -e ."` / `-r requirements.txt` com venv, e os atuais `"pip install -e ."` / `"pip install -r requirements.txt"` sem executor. Node, Go e Rust não mudam.

- [ ] **Step 1: Escrever os testes que falham**

Em `tests/unit/agentic/test_diagnostics.py`:

```python
import os

PYPROJECT = "[project]\nname = \"demo\"\n\n[tool.ruff]\n\n[tool.pytest.ini_options]\n"


def test_a_uv_project_runs_its_checks_through_uv(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    recipe = detect_recipe(tmp_path)
    assert recipe.command_for("test") == "uv run python -m pytest"
    assert recipe.command_for("lint") == "uv run python -m ruff check ."
    assert recipe.command_for("install") == "uv sync"
    assert "install" not in recipe.available_steps()


def test_a_poetry_project_runs_through_poetry(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "poetry.lock").write_text("", encoding="utf-8")
    recipe = detect_recipe(tmp_path)
    assert recipe.command_for("test") == "poetry run python -m pytest"
    assert recipe.command_for("install") == "poetry install"


def test_a_local_venv_is_used_without_a_lockfile(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    relative = Path(".venv", "Scripts", "python.exe") if os.name == "nt" else Path(".venv", "bin", "python")
    (tmp_path / relative).parent.mkdir(parents=True)
    (tmp_path / relative).write_bytes(b"")
    assert detect_recipe(tmp_path).command_for("test") == f'"{relative}" -m pytest'


def test_plain_python_never_installs_by_default(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    recipe = detect_recipe(tmp_path)
    assert "install" not in recipe.available_steps()
    assert recipe.command_for("install") == "pip install -e ."
```

Em `tests/unit/agentic/tools/test_verify_runner.py`:

```python
import sys
import time
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools._shared import AgentToolError
from agentos.agentic.tools.orin import verify as verify_module
from agentos.agentic.workspace import ConversationWorkspace


def test_a_verification_step_reports_pass_and_output(tmp_path: Path) -> None:
    passed, output = verify_module._run_verification_step(
        f'"{sys.executable}" -c "print(\'ok\')"', cwd=tmp_path, timeout=60, cancelled=lambda: False,
    )
    assert passed is True and "ok" in output


def test_a_verification_step_stops_when_the_turn_is_cancelled(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(AgentToolError) as caught:
        verify_module._run_verification_step(
            f'"{sys.executable}" -c "import time; time.sleep(30)"', cwd=tmp_path, timeout=60, cancelled=lambda: True,
        )
    assert caught.value.error_code == "INTERRUPTED_BY_USER"
    assert time.monotonic() - started < 10


def test_verify_project_passes_the_turn_cancel_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def fake_step(command, *, cwd, timeout, cancelled):
        seen.append(cancelled())
        return True, "ok"

    monkeypatch.setattr(verify_module, "_run_verification_step", fake_step)
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), cancel_check=lambda: True)
    (toolset.workspace.root / "pyproject.toml").write_text("[project]\nname = \"demo\"\n\n[tool.ruff]\n", encoding="utf-8")
    toolset.invoke("verify_project", {"steps": ["lint"]})
    assert seen == [True]
```

Ajuste: `test_verify_project_stops_after_a_failing_install_step` (`test_agent_tools.py:124-139`) passa a fazer `monkeypatch.setattr(verify_module, "_run_verification_step", lambda *a, **k: (False, "npm ERR! network timeout"))` no lugar de `agent_tools.subprocess.run`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_diagnostics.py tests/unit/agentic/tools/test_verify_runner.py -v`
Expected: FAIL — comandos `python -m pytest`, `install` em `available_steps()`, `AttributeError: _run_verification_step`.

- [ ] **Step 3: Implementar**

- `ProjectRecipe.opt_in_steps` e `available_steps()` filtrando; `python_runner`; `_python_recipe` como nas Interfaces, com `opt_in_steps=frozenset({"install"})`.
- `_run_verification_step`: `subprocess.Popen(command, shell=True, cwd=str(cwd), stdout=PIPE, stderr=STDOUT, stdin=DEVNULL, env={**os.environ, "CI": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}, creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW no Windows / start_new_session=True fora)`; laço de `communicate(timeout=CANCEL_CHECK_SECONDS)` que, a cada `TimeoutExpired`, consulta `cancelled()` e o prazo; ao cancelar ou estourar o prazo, `_terminate_process_tree(process)` e `communicate()` final; prazo estourado devolve `(False, f"[timed out after {timeout}s]")`. Saída decodificada em UTF-8 com `errors="replace"`.
- `verify_project` (`:73-86`): troca o `subprocess.run` por `_run_verification_step(command, cwd=self.workspace.root, timeout=VERIFY_STEP_TIMEOUT_SECONDS, cancelled=self._cancel_requested)`, mantendo `_bounded_output(..., MAX_VERIFY_STEP_CHARS)` e o resto do laço.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/unit/agentic/test_diagnostics.py tests/unit/agentic/tools/test_verify_runner.py tests/unit/agentic/test_agent_tools.py -k "verify or diagnostic or recipe or python or node" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/diagnostics.py src/agentos/agentic/tools/orin/verify.py tests/unit/agentic/test_diagnostics.py tests/unit/agentic/tools/test_verify_runner.py tests/unit/agentic/test_agent_tools.py
git commit -m "fix(verify): use the project's runner, never pip install by default, and stop when the turn is cancelled"
```

---

### Task 13: Verificação final e aceite da Fase 1

**Files:** nenhum arquivo de código.

- [ ] **Step 1: Rodar a suíte inteira uma vez**

Run: `uv run pytest tests/unit tests/integration -q`
Expected: tudo verde. Uma falha aqui volta para a tarefa dona do arquivo, com teste novo que reproduz o caso.

- [ ] **Step 2: Conferir que o repositório do próprio Orin fica limpo**

Run: `git status --short`
Expected: nenhum `uploads/` nem `.orin/` novo criado pelos testes; `.git/info/exclude` do repositório contém `.orin/` depois de um turno do Orin Code nele.

- [ ] **Step 3: Replay manual das duas tarefas reais (aceite da spec)**

No Orin instalado a partir deste branch, refazer no modo código (a) a análise de upsell do Educly e (b) a investigação de contexto do Orin. Aceite: nenhum `FILE_NOT_READ` falso; nenhum `old_text was not found` em arquivo CRLF; `git status` dos dois repositórios sem `uploads/` nem `.orin/`; o prompt mostra `bash (Git Bash, …)`; `verify_project` não roda `pip install`. Anotar o resultado no PR.

- [ ] **Step 4: Commit (só se algo foi corrigido nos passos anteriores)**

```bash
git status --short
```
