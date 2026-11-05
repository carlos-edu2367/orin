# Fase 3 — Paridade com o Claude Code e base para outras CLIs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deixar o Orin Code com o comportamento do Claude Code (ferramentas sob demanda via `ToolSearch`, leitura e busca no padrão dele, conclusão sem portão forçado, todos intactos, lint enxuto, fila justa de CLI) e extrair o contrato `CliAgentAdapter` com uma suíte de conformidade em que o adapter do Claude é a primeira implementação.

**Architecture:** O conjunto de ferramentas do modo código passa a ter um núcleo sempre publicado (15 ferramentas) e um conjunto adiado; o `AgentToolset` guarda as ferramentas descobertas no turno (como já faz com `_active_mcp_tool_names`) e o runtime publica núcleo + descobertas a cada iteração. O portão `code_completion_gate` vira um status (`verified`/`unverified`/`not_applicable`) que só é mostrado na UI. Do lado dos provedores, um módulo neutro `providers/cli_adapter.py` define o contrato e o vocabulário de eventos; `providers/cli_queue.py` substitui o semáforo global por uma fila justa por conversa; o pacote `claude_cli/` da Fase 2 passa a ser consumido através do adapter.

**Tech Stack:** Python (versão do `pyproject.toml`; pytest via `uv run pytest`), React 18 + TypeScript (Vitest via `npx vitest run` em `frontend/`).

**Spec:** `docs/superpowers/specs/2026-11-05-claude-assinatura-nativa-e-orin-code-design.md` (Fase 3, R3.1–R3.7, D10). Auditoria: `docs/audits/2026-11-05-claude-assinatura-e-orin-code.md`.

## Global Constraints

- Núcleo sempre carregado no modo código (spec R3.1, literal): Read, Write, Edit, Glob, Grep, Bash, TodoWrite, Agent, AskUserQuestion, SearchCode, ProjectMap, VerifyProject, PreviewStart/PreviewStop, ToolSearch. As demais são encontradas por `ToolSearch`.
- Meta: `tools_tokens` **abaixo de 4.000** no primeiro contexto do turno (medido por `AgenticTurnRuntime._context_usage`, `runtime.py:1479-1547`).
- `read_file`: padrão de **2.000 linhas**, limite por tokens no lugar do corte fixo de 12.000 caracteres. `Grep`: **diferencia maiúsculas por padrão** e usa **`files_with_matches`** como modo padrão.
- O `code_completion_gate` **deixa de recusar** a resposta final; o card mostra **"não verificado"** quando nenhuma verificação rodou.
- `complete_remaining_todos` deixa de marcar itens como concluídos no fim do turno (backend **e** o `settleTodos` do frontend).
- Fila de CLI: limite padrão **3** (o valor do semáforo atual), configurável; evento "aguardando vaga" visível na UI.
- Nada importa `provider_catalog/opencode.py` (D1). Antigravity e Codex **não** são implementados aqui (D10): só o contrato e a suíte.
- Fora do escopo (spec §4): mudanças visuais além dos eventos de progresso já existentes — o "não verificado" e o "aguardando vaga" usam o card do Modo Code, a timeline e o `AgentPulse` que já existem.
- Nenhum teste automatizado invoca a CLI `claude`; testes usam linhas de stream-json gravadas.
- Fase 1 e Fase 2 vêm antes. Fase 1 mexe em `read.py`, `system_prompt.py`, `runtime.py` e `session.py`; ao abrir cada tarefa, rode `git log --oneline -5 -- <arquivo>` e reconfira as linhas citadas aqui (foram medidas no commit `fe8c992`).
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Nomes fixos da Fase 2 (consumidos aqui)

Fonte: `docs/superpowers/plans/2026-11-05-fase-2-adapter-claude-cli-nativo.md` (blocos Interfaces das Tasks 2, 3, 6, 7, 8 e 9).

- `claude_cli/command.py`: `build_claude_command(*, binary, system_prompt_file, model=None, effort=None, fallback_model=None, max_turns=1, mcp_config_file=None, mcp_server_name="orin", session_id=None, resume_session_id=None, persist_session=False, autocompact=None) -> list[str]`, `build_isolated_claude_env(base_env=None, *, max_output_tokens=None, mcp_tool_timeout_ms=None) -> dict[str, str]`, `resolve_claude_binary(env=None) -> str | None`, `claude_cli_version(binary, *, timeout=5.0) -> tuple[int, int, int] | None`, `redact_argv(argv) -> list[str]`.
- `claude_cli/catalog.py`: `CatalogModel(id, display_name, context_window)`, `discover_cached_models(*, org_id=None, cache_dir=None) -> list[CatalogModel]`, `context_window_for(model_id, models) -> int | None`.
- `claude_cli/stream.py`: `CliUsage`, `CliInit`, `CliResult`, `CliEventKind`, `CliEvent`, `StreamJsonParser` (`feed_line(line) -> list[CliEvent]`; `init`, `result`, `rate_limit`). Fixtures reais em `tests/unit/providers/claude_cli/fixtures/out-2.jsonl`, `out-3.jsonl`, `out-5.jsonl`.
- `claude_cli/errors.py`: `ProviderTransportError`, `RateLimitState`, `classify_result_error(result, rate_limit, *, now, tz=None) -> ProviderTransportError`, `NativeLoopUnavailable`.
- `claude_cli/provider.py`: `ClaudeCliProvider` (`stream(request, *, cancel_check=None)`, `start_native_turn(request) -> NativeTurnHandle`, `native_turn_available(turn)`), `CLI_SLOTS: CliSlots` (substitui o `_CLAUDE_CLI_SEMAPHORE` de `provider_catalog/claude_code.py:51`; sem limite por padrão, `ORIN_CLI_MAX_PROCESSES` opcional; adquirido no `stream`, no `start_native_turn` e readquirido depois de cada `call_tool` do executor embrulhado).
- `claude_cli/native_loop.py`: `NativeTurnRequest`, `NativeTurnSink`, `NativeTurnOutcome`, `NativeTurnHandle`.
- `cli_tools_mcp/types.py`: `McpToolSpec`, `ToolCallRequest`, `ToolCallResult(text, is_error=False, images=())`, `ToolExecutor` (`list_tools()`, `call_tool(request)`). `cli_tools_mcp/server.py`: `TurnToolServer(executor, *, server_name="orin", host="127.0.0.1")`, `async start(*, port=0) -> TurnToolServerHandle` (`.url`, `.token`, `.server_name`, `.mcp_config_json()`, `.in_flight`, `refuse_calls(reason)`, `async close()`), `BackgroundTurnToolServer`. O servidor só aceita POST (GET → 405) e responde JSON.
- `agentic/native_turn.py`: `NativeTurnRunner`, `RuntimeToolExecutor(runtime, turn, *, tool_schemas, correlate)`. O runner também chama `code_completion_gate` e `_settle_remaining_todos` no fim do turno nativo — as Tarefas 6 e 8 mexem nele.
- Se algum nome acima mudou durante a execução da Fase 2, vale o código: ajuste só a delegação dentro de `claude_cli/adapter.py` (Tarefa 10); o contrato de `cli_adapter.py` não muda.

## Review Focus

1. **Ferramenta adiada chamada sem `ToolSearch`** (o modelo lembra `WebFetch` de um turno anterior): a chamada roda e a ferramenta passa a ser publicada na iteração seguinte — nunca "Unknown tool". Teste na Tarefa 1.
2. **Espera na fila de CLI por mais de 180 s** (três subagentes de outra conversa ocupando as vagas): a espera não dispara o `MAX_STREAM_KEEPALIVE_STALL_SECONDS` (`runtime.py:51`, checado em `:983-992`), porque `WAITING` conta como progresso. Teste na Tarefa 12.
3. **Usuário cancela enquanto espera vaga**: o ticket sai da fila, nenhuma vaga vaza e o próximo da fila é atendido. Teste na Tarefa 12.
4. **Linter ausente** (`python -m ruff` responde `No module named ruff`, que hoje é anexado como "encontrou problemas" — confirmado no `.venv` do repo): nada é anexado ao resultado do edit. Teste na Tarefa 9.
5. **Arquivo com uma linha gigante** (JS minificado com 300 mil caracteres numa linha): a linha é cortada, o resultado respeita o orçamento de tokens e o registro de leitura só marca as linhas entregues. Teste na Tarefa 4.

---

### Task 1: Núcleo do modo código e ferramentas descobertas (R3.1, parte 1)

**Files:**
- Create: `src/agentos/agentic/tool_deferral.py`
- Modify: `src/agentos/agentic/runtime.py:79-96` (o conjunto vai para o módulo novo e é reexportado), `src/agentos/agentic/runtime.py:1445-1462` (`_tool_schemas`, ramo do modo código)
- Modify: `src/agentos/agentic/agent_tools.py:445` (estado novo ao lado de `_active_mcp_tool_names`), `:462-463` (propriedades), `:680-691` (`invoke`: descoberta automática)
- Test: `tests/unit/agentic/test_tool_deferral.py`
- Modify (testes que assumiam o conjunto inteiro publicado): `tests/unit/agentic/test_extension_tools_exposure.py:213-219`, `tests/unit/test_orin_code_phase_a.py:328-333`

**Interfaces:**
- Consumes: `CODE_MODE_CORE_TOOLS` (conteúdo atual de `runtime.py:79-96`, sem mudança).
- Produces (em `agentos.agentic.tool_deferral`):
  - `CODE_MODE_CORE_TOOLS: frozenset[str]` — movido literalmente; `runtime.py` passa a fazer `from .tool_deferral import CODE_MODE_CORE_TOOLS` (os imports em `session.py:56,4122,4324` e `cli_code/headless.py:313` continuam funcionando).
  - `ALWAYS_LOADED_CODE_TOOLS: frozenset[str] = frozenset({"read_file", "write_file", "edit_file", "glob_files", "search_files", "run_command", "todo_write", "run_subagent", "ask_user", "search_code", "project_map", "verify_project", "preview_start", "preview_stop", "search_mcp_tools"})` (`search_mcp_tools` é o público `ToolSearch`, `public_names.py:46`).
  - `FOLLOW_UP_TOOLS: Mapping[str, tuple[str, ...]] = {"run_command": ("read_process_output", "stop_process"), "powershell": ("read_process_output", "stop_process"), "run_subagent": ("check_subagent",), "enter_plan_mode": ("exit_plan_mode",), "preview_start": ("preview_logs",)}`
  - `TOOL_SEARCH_ENV = "ORIN_CODE_TOOL_SEARCH"`; `tool_search_enabled(env: Mapping[str, str] = os.environ) -> bool` — falso só para `off`, `0`, `false`, `no` (sem diferenciar maiúsculas).
- Produces (em `AgentToolset`):
  - `discovered_tool_names -> frozenset[str]` (propriedade)
  - `deferred_tool_names -> frozenset[str]` (propriedade): definidas ∩ `CODE_MODE_CORE_TOOLS` − `ALWAYS_LOADED_CODE_TOOLS` − descobertas; vazio fora do modo código.
  - `discover(names: Iterable[str]) -> list[str]` — aceita chave interna, nome público ou alias (via `key_for`), guarda só o que está em `CODE_MODE_CORE_TOOLS` e definido, devolve as chaves internas recém-descobertas na ordem recebida.
- Produces (em `AgenticTurnRuntime`): `_tool_schemas` no modo código publica `CODE_MODE_CORE_TOOLS ∩ (ALWAYS_LOADED_CODE_TOOLS ∪ toolset.discovered_tool_names)` quando a deferência está ativa. Ativa = `tool_search_enabled()` **e** `self.offered_tools is None` (o `--tools` do `orin code` continua publicando exatamente o que foi pedido) **e** o argumento novo `allow_deferral: bool = True` de `_tool_schemas(self, turn, *, allow_deferral=True)` (a Tarefa 11 passa `False` no caminho nativo quando a CLI não relê a lista).

- [ ] **Step 1: Escrever os testes que falham** em `tests/unit/agentic/test_tool_deferral.py`, com um `AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, run_subagent=..., check_subagent=..., retrieval=object())` e um `AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)`; `_names(runtime)` = nomes de `runtime._tool_schemas({"code_mode": "code"})`.

```python
def test_code_mode_publishes_only_the_always_loaded_core(toolset, runtime):
    assert _names(runtime) == ALWAYS_LOADED_CODE_TOOLS & {d.name for d in toolset.definitions()}
    assert "fetch_url" in toolset.deferred_tool_names and "list_files" in toolset.deferred_tool_names

def test_a_discovered_tool_is_published_from_the_next_request(toolset, runtime):
    assert toolset.discover(["WebFetch", "nope", "read_file"]) == ["fetch_url"]
    assert "fetch_url" in _names(runtime) and "fetch_url" not in toolset.deferred_tool_names

def test_calling_a_deferred_tool_directly_runs_it_and_publishes_it(toolset, runtime, tmp_path):
    (tmp_path / "a.txt").write_text("x\n")
    outcome = toolset.invoke("ListFiles", {})
    assert outcome.status == "succeeded"
    assert "list_files" in _names(runtime)

def test_follow_up_tools_are_discovered_after_their_parent_succeeds(toolset, runtime):
    toolset.invoke("run_command", {"command": "echo hi"})
    assert {"read_process_output", "stop_process"} <= _names(runtime)

def test_headless_tools_option_disables_deferral(toolset, runtime):
    runtime.offered_tools = frozenset({"read_file", "fetch_url"})
    assert _names(runtime) == {"read_file", "fetch_url"}

def test_env_off_publishes_the_whole_code_mode_set(toolset, runtime, monkeypatch):
    monkeypatch.setenv("ORIN_CODE_TOOL_SEARCH", "off")
    assert "fetch_url" in _names(runtime) and "list_files" in _names(runtime)

def test_deferral_can_be_turned_off_per_call(toolset, runtime):
    names = {s["function"]["name"] for s in runtime._tool_schemas({"code_mode": "code"}, allow_deferral=False)}
    assert "fetch_url" in names
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_tool_deferral.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.agentic.tool_deferral'`.

- [ ] **Step 3: Implementar** `tool_deferral.py` com os nomes acima; em `AgentToolset.__init__` criar `self._discovered_tool_names: set[str] = set()`; as duas propriedades e `discover`. Em `invoke` (`agent_tools.py:680`), depois de resolver `internal`: se `internal in self.deferred_tool_names`, chamar `self.discover([internal])`; depois de um resultado `succeeded`, `self.discover(FOLLOW_UP_TOOLS.get(internal, ()))`. Fazer o mesmo no ramo sem lifecycle (`bus is None`, `:689-690`). No runtime, trocar `allowed = set(CODE_MODE_CORE_TOOLS)` (`:1450`) pela interseção descrita, mantendo as linhas seguintes (`offered_tools`, `withheld_tools`, MCP ativos) como estão.

- [ ] **Step 4: Ajustar os dois testes antigos que esperavam tudo publicado.** Em `test_extension_tools_exposure.py:213-219`, trocar `published` por `published | runtime.toolset.deferred_tool_names` (as ferramentas continuam ofertadas, agora via `ToolSearch`). Em `test_orin_code_phase_a.py:330-333`, tirar `list_files` do conjunto exigido e afirmar `"list_files" in runtime.toolset.deferred_tool_names`.

- [ ] **Step 5: Rodar**

Run: `uv run pytest tests/unit/agentic/test_tool_deferral.py tests/unit/agentic/test_extension_tools_exposure.py tests/unit/test_orin_code_phase_a.py tests/unit/cli_code -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/tool_deferral.py src/agentos/agentic/runtime.py src/agentos/agentic/agent_tools.py tests/unit/agentic/test_tool_deferral.py tests/unit/agentic/test_extension_tools_exposure.py tests/unit/test_orin_code_phase_a.py
git commit -m "feat(code): publish only the core tool set in code mode and track discovered tools"
```

---

### Task 2: `ToolSearch` encontra as ferramentas nativas adiadas (R3.1, parte 2)

**Files:**
- Modify: `src/agentos/agentic/tools/mcp/admin.py:115-185` (`search_mcp_tools`), `:400-436` (`ToolSpec` de `search_mcp_tools`: descrição pública e `available`)
- Modify: `src/agentos/code_mode/system_prompt.py:15` (`PROMPT_VERSION`), `:23-30` (`CodePromptOptions`), `:96-97` (linha do `search_mcp_tools`)
- Modify: `src/agentos/agentic/session.py:4139-4144` (passa `deferred_tool_names`)
- Test: `tests/unit/agentic/tools/test_tool_search.py` (acrescentar), `tests/unit/agentic/test_code_prompt.py` (acrescentar)

**Interfaces:**
- Consumes: `AgentToolset.deferred_tool_names`, `AgentToolset.discover(...)` (Tarefa 1); `McpToolSearchEngine`, `McpToolSearchDocument.from_descriptor` (`agentos/mcp/search.py:171-216,266`).
- Produces:
  - `search_mcp_tools(query, server=None, limit=10, activate=True)` também busca nas adiadas nativas; resultados nativos vêm primeiro no `tool_output["matches"]` pelo **nome público** quando `name_translator` está ligado (senão a chave interna), e cada item de `payload["tools"]` nativo é `{"name": <nome que o modelo usa>, "description": ..., "input_schema": ..., "tool_kind": "native"}`, com o schema já traduzido por `self.name_translator.schemas_out` quando houver tradutor.
  - `select:` com nome nativo adiado → descoberto e listado em `matches`; nome do núcleo ou já descoberto → `already_available` (comportamento atual); inexistente → `not_found`.
  - `ToolSpec.available` de `search_mcp_tools`: `self._mcp_provider is not None or self._code_mode_active`. Sem provedor MCP, a busca é só nativa (não lança mais `"No active MCP servers..."`).
  - `CodePromptOptions.deferred_tool_names: frozenset[str] = frozenset()`.

- [ ] **Step 1: Testes que falham** em `test_tool_search.py` (toolset em modo código, sem `mcp_provider`, `public_tool_names=True`):

```python
def test_select_loads_a_deferred_native_tool(tmp_path):
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, public_tool_names=True)
    output = toolset.invoke("ToolSearch", {"query": "select:WebFetch,Read,nope"}).data["tool_output"]
    assert output["matches"] == ["WebFetch"]
    assert output["already_available"] == ["Read"] and output["not_found"] == ["nope"]
    assert "fetch_url" in toolset.discovered_tool_names

def test_keyword_search_returns_schemas_of_native_tools(tmp_path):
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, public_tool_names=True)
    outcome = toolset.invoke("ToolSearch", {"query": "web page fetch"})
    payload = json.loads(outcome.content)
    tool = next(item for item in payload["tools"] if item["name"] == "WebFetch")
    assert tool["input_schema"]["type"] == "object" and tool["tool_kind"] == "native"

def test_mcp_and_native_results_coexist(tmp_path):
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, mcp_provider=_Provider())
    output = toolset.invoke("ToolSearch", {"query": "select:mcp__github__create_issue,fetch_url"}).data["tool_output"]
    assert set(output["matches"]) == {"mcp__github__create_issue", "fetch_url"}
```

Em `test_code_prompt.py`:

```python
def test_prompt_lists_deferred_tools_and_how_to_load_them():
    text = render_code_system_prompt(CodePromptOptions(
        tool_names=frozenset({"read_file", "search_mcp_tools", "fetch_url"}), public_names=True,
        deferred_tool_names=frozenset({"fetch_url", "web_search"}),
    ))
    assert "`ToolSearch`" in text and "select:" in text
    assert "WebFetch, WebSearch" in text
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/tools/test_tool_search.py tests/unit/agentic/test_code_prompt.py -v`
Expected: FAIL (`No active MCP servers` / `unexpected keyword argument 'deferred_tool_names'`).

- [ ] **Step 3: Implementar a busca nativa.** Os documentos nativos são `McpToolSearchDocument.from_descriptor(server_slug="orin", server_name="Orin", descriptor=McpToolDescriptor(name=<nome público ou interno>, description=<public_description ou description>, input_schema=<parameters>))` para cada chave em `deferred_tool_names`, com um mapa `qualified_name -> chave interna` para voltar. Rodar o mesmo motor (`search`, `listing`, `+termos`) sobre os documentos nativos e, quando há provedor, sobre os MCP; concatenar nativos + MCP e cortar em `limit`. Chamar `self.discover(...)` com as chaves nativas encontradas **também em modo somente leitura** (as definições já foram filtradas para leitura em `definitions()`); a ativação MCP mantém a regra atual (`activate and not self._read_only_mode`). Descrição pública nova: `"Find deferred tools (Orin's own and connected MCP servers') by keyword, or load them by name with 'select:Name1,Name2', and get their schemas so they can be called."`.

- [ ] **Step 4: Implementar o prompt.** Substituir a linha `:96-97` por: quando `deferred_tool_names` não é vazio, `- Other tools load on demand: {lista ordenada de rótulos públicos}. Load one with {t('search_mcp_tools')} (\`select:Name\`) before you call it; connected MCP servers' tools load the same way.`; sem adiadas, manter o texto atual. Subir `PROMPT_VERSION` para a versão seguinte à que estiver no arquivo (a Fase 1 pode ter subido para `w3.2`; então `w3.3`). Em `session.py:4139`, passar `deferred_tool_names=toolset.deferred_tool_names if tool_search_enabled() and (self.cli_options is None or self.cli_options.tools is None) else frozenset()`.

- [ ] **Step 5: Rodar**

Run: `uv run pytest tests/unit/agentic/tools/test_tool_search.py tests/unit/agentic/test_code_prompt.py tests/unit/agentic/test_agent_tools_mcp.py tests/unit/agentic/tools -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/tools/mcp/admin.py src/agentos/code_mode/system_prompt.py src/agentos/agentic/session.py tests/unit/agentic/tools/test_tool_search.py tests/unit/agentic/test_code_prompt.py
git commit -m "feat(code): ToolSearch loads deferred native tools and the prompt lists them"
```

---

### Task 3: Medir `tools_tokens` < 4.000 (R3.1, aceite)

**Files:**
- Test: `tests/unit/agentic/test_tools_tokens_budget.py`
- Modify (só se o teste reprovar): `public_description` das specs do núcleo (`tools/search/grep.py:481`, `tools/orin/verify.py:147+`, `tools/interaction/ask_user.py:118+`, `tools/agents/agent.py:173`)

**Interfaces:**
- Consumes: `AgenticTurnRuntime._tool_schemas`, `._context_usage` (`runtime.py:1479`), `._names.schemas_out` (o que vai de fato ao provedor, `runtime.py:806-807`).

Medição de referência feita para este plano (commit `fe8c992`, toolset de modo código com subagentes e `retrieval`): **5.158** tokens com 32 ferramentas publicadas; o núcleo de 15 soma cerca de **2.900**.

- [ ] **Step 1: Escrever o teste** (falha até a Tarefa 1 estar pronta; se rodar depois dela, já deve passar):

```python
def test_first_context_of_a_code_turn_spends_under_4000_tool_tokens(tmp_path):
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, public_tool_names=True,
        run_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        check_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        retrieval=object(),
    )
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset,
                                 tool_kinds={d.name: d.kind for d in toolset.definitions()})
    schemas = runtime._names.schemas_out(runtime._tool_schemas({"code_mode": "code"}))
    usage = runtime._context_usage([], [], schemas)
    assert usage["tools_tokens"] < 4000
    assert {s["function"]["name"] for s in schemas} >= {"Read", "Edit", "Bash", "Grep", "Agent"}
```

- [ ] **Step 2: Rodar**

Run: `uv run pytest tests/unit/agentic/test_tools_tokens_budget.py -v`
Expected: PASS (≈2.900 + `ToolSearch`). Se passar de 4.000, encurtar as `public_description` mais longas da lista acima até passar, sem remover parâmetros.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/agentic/test_tools_tokens_budget.py
git commit -m "test(code): pin tools_tokens under 4000 for the first code-mode context"
```

---

### Task 4: `read_file` com 2.000 linhas e orçamento de tokens (R3.2, parte 1)

**Files:**
- Modify: `src/agentos/agentic/tools/files/read.py:36-38` (constantes), `:306-455` (`read_file`), `:591` (`ReadInput.limit`), `:660-672` (spec)
- Test: `tests/unit/agentic/tools/test_read_token_budget.py`

**Interfaces:**
- Produces (em `read.py`): `DEFAULT_READ_LINES = 2_000`, `MAX_READ_TOKENS = 25_000`, `MAX_READ_LINE_CHARS = 2_000`; `_fit_lines_to_token_budget(lines: Sequence[str], first: int, budget_tokens: int) -> int` — quantas linhas, a partir da primeira, cabem renderizadas como `f"{n:6}\t{line}"` com o mesmo estimador do runtime (`len(texto) // 4`), reservando 200 tokens para o rodapé de continuação; sempre pelo menos 1.
- O resultado sempre leva `"max_result_chars": MAX_READ_TOKENS * 4` (100.000 = `LARGE_RESULT_CHARS`, `agent_tools.py:167`), para o corte genérico de 12.000 (`tools/spec.py:24`, aplicado em `agent_tools.py:1052-1058`) não valer para leitura.

Decisões: `limit` padrão 2.000 e máximo 2.000 para qualquer arquivo (some o caso especial de `uploads/`, `:342-345`); `max_bytes` passa a ser sempre `LARGE_READ_BYTES` (2 MB), porque o orçamento de tokens limita a saída e hoje um `offset` além dos primeiros 200 KB não pode ser lido; `allow_large` fica aceito e sem efeito (compatibilidade). Linha maior que 2.000 caracteres é cortada com o sufixo `" … [line truncated]"`. Quando o orçamento corta a janela, o rodapé usa o texto de continuação que já existe (`:415-419`) a partir da primeira linha não entregue, e o registro de leitura (`_record_file_read`, `:402-408`) recebe `limit=<linhas entregues>`. Se a Fase 1 já trocou esse registro por um cálculo de "linhas entregues após o corte de 12.000", substitua o orçamento de caracteres dela por `_fit_lines_to_token_budget` — o comportamento do registro é o mesmo.

- [ ] **Step 1: Testes que falham**

```python
def test_default_read_returns_up_to_2000_lines(toolset, workspace):
    workspace.write_text("big.txt", "".join(f"l{i}\n" for i in range(1, 2501)))
    out = toolset.invoke("Read", {"file_path": "big.txt"}).data["tool_output"]["file"]
    assert out["numLines"] == 2000 and out["startLine"] == 1

def test_token_budget_cuts_whole_lines_and_points_to_the_next_offset(toolset, workspace):
    workspace.write_text("wide.txt", "".join("x" * 400 + "\n" for _ in range(2000)))
    outcome = toolset.invoke("Read", {"file_path": "wide.txt"})
    shown = outcome.data["tool_output"]["file"]["numLines"]
    assert shown < 2000 and len(outcome.content) // 4 <= 25_000
    assert f"offset={shown + 1}" in outcome.content
    assert "output truncated at" not in outcome.content

def test_a_giant_single_line_is_truncated_and_recorded_as_read(toolset, workspace):
    workspace.write_text("min.js", "a" * 300_000)
    outcome = toolset.invoke("Read", {"file_path": "min.js"})
    assert "[line truncated]" in outcome.content and len(outcome.content) < 4_000
    assert toolset.invoke("Edit", {"file_path": "min.js", "old_string": "aaa", "new_string": "b", "replace_all": False}).error_code != "FILE_NOT_READ"

def test_offset_past_the_first_200kb_is_readable(toolset, workspace):
    workspace.write_text("log.txt", "".join(f"{i:08d}" + "y" * 100 + "\n" for i in range(4000)))
    out = toolset.invoke("Read", {"file_path": "log.txt", "offset": 3990}).data["tool_output"]["file"]
    assert out["startLine"] == 3990 and out["numLines"] == 11
```

(O terceiro teste aceita qualquer erro do edit que não seja "não leu"; `old_text` com várias ocorrências é esperado.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/tools/test_read_token_budget.py -v`
Expected: FAIL (`numLines == 400`).

- [ ] **Step 3: Implementar** conforme as decisões acima; atualizar o `legacy_schema` (`"maximum": 2000`, `"Defaults to 2000."`), `ReadInput.limit` (`description="How many lines to read (default 2000)."`) e a `public_description` (`"... returns up to 2000 lines by default, bounded by a token budget; use offset and limit to page ..."`).

- [ ] **Step 4: Rodar** a tarefa e os testes de leitura existentes

Run: `uv run pytest tests/unit/agentic/tools/test_read_token_budget.py tests/unit/agentic/tools tests/unit/agentic/test_agent_tools.py tests/unit/agentic/test_view_file_tool.py -q`
Expected: PASS. Testes antigos que fixavam `limit=400` como padrão são atualizados para 2.000 no mesmo commit.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/tools/files/read.py tests/unit/agentic/tools/test_read_token_budget.py tests/unit/agentic
git commit -m "feat(read): 2000-line default window bounded by a 25k-token budget"
```

---

### Task 5: `Grep` com padrões do Claude Code (R3.2, parte 2)

**Files:**
- Modify: `src/agentos/agentic/tools/search/grep.py:43` (`ignore_case`), `:50` (`output_mode`), `:62` (normalização do modo), `:461-465` (descrição do `output_mode` no schema legado)
- Test: `tests/unit/agentic/tools/test_grep_parity.py` (acrescentar)

**Interfaces:**
- Produces: `search_files(..., ignore_case: bool = False, ..., output_mode: str = "files_with_matches", ...)`; `-i: true` e `case_sensitive: false` continuam ligando a busca sem diferenciar maiúsculas.

- [ ] **Step 1: Testes que falham** (reusam `_toolset` do arquivo):

```python
def test_default_mode_lists_files_only(tmp_path):
    output = _toolset(tmp_path).invoke("Grep", {"pattern": "alpha"}).data["tool_output"]
    assert output["mode"] == "files_with_matches" and "content" not in output

def test_search_is_case_sensitive_by_default(tmp_path):
    toolset = _toolset(tmp_path)
    assert toolset.invoke("Grep", {"pattern": "ALPHA"}).data["tool_output"]["numFiles"] == 0
    assert toolset.invoke("Grep", {"pattern": "ALPHA", "-i": True}).data["tool_output"]["numFiles"] == 2
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/tools/test_grep_parity.py -v`
Expected: FAIL (`mode == "content"`).

- [ ] **Step 3: Implementar** as duas trocas de padrão (`str(output_mode or "files_with_matches")` em `:62`).

- [ ] **Step 4: Rodar a suíte de ferramentas**

Run: `uv run pytest tests/unit/agentic tests/unit/permissions tests/unit/cli_code -q`
Expected: PASS. Testes que dependiam do padrão antigo (`rg -l "search_files|\"Grep\"" tests`) passam a mandar `output_mode`/`-i` explícitos.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/tools/search/grep.py tests/unit
git commit -m "feat(grep): case-sensitive files_with_matches by default (Claude Code parity)"
```

---

### Task 6: Conclusão sem portão forçado — backend (R3.3, parte 1)

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py:1239-1260` (`code_completion_gate` → `code_verification_status`)
- Modify: `src/agentos/agentic/runtime.py:218-228` (`AgenticRunResult`), `:245` (atributo `completion_gate`), `:1348-1384` (bloco do portão), `:1424-1435` (conclusão)
- Modify: `src/agentos/agentic/native_turn.py` (Fase 2: o ramo `success` do `NativeTurnRunner` chama `code_completion_gate` antes de concluir)
- Modify: `src/agentos/agentic/events.py:51` (tipo novo), `src/agentos/agentic/session.py:1487-1494` (ramo `code_validation_required` sai; ramo `code_verification` entra), `:4703` (linha do `completion_gate` sai)
- Modify: `src/agentos/workers/chat.py:630` e `:632-658` (`_settle_code_mode` recebe a verificação)
- Modify: `src/agentos/code_mode/system_prompt.py:61` (orientação de verificar)
- Test: `tests/unit/agentic/test_code_completion_without_gate.py`
- Modify (testes do portão antigo): `tests/unit/agentic/test_agent_tools.py:30-45,1154-1169`, `tests/unit/agentic/test_git_change_tracking_baseline.py:88-110`, `tests/unit/test_orin_code_phase_a.py:207-225`, `tests/unit/agentic/test_phase_runtime.py:335-351`, `tests/unit/agentic/test_runtime_fixes_v076.py:405-420`, `tests/unit/agentic/test_lifecycle_runtime.py:110`, `tests/unit/cli_code/test_cli_turn_session.py:124-126`

**Interfaces:**
- Produces:
  - `AgentToolset.code_verification_status() -> Literal["verified", "unverified", "not_applicable"]` — `not_applicable` fora do modo código ou sem `_changed_paths`; `verified` quando `_code_mode_verified`; senão `unverified`. A recusa de "nenhuma ferramenta executada" (`:1243-1255`) é removida.
  - `AgenticRunResult.verification: str | None = None`.
  - Lifecycle `self._life(turn, "code_verification", status=<status>)` emitido uma vez, logo antes de `self._life(turn, "completed")`, só no turno principal em modo código.
  - `AgentActivityEventType.CODE_MODE_UNVERIFIED = "code_mode.unverified"`; o `TurnSession` grava esse evento (resumo `"Entrega não verificada"`, payload `{"verification": "unverified", "changed_paths": <n>}`) apenas quando `status == "unverified"`. Esse tipo não entra no filtro de eventos de turnos encerrados (`conversations/chat.py:1505-1512`), então sobrevive ao recarregar.
  - `_settle_code_mode(turn, result_state, *, error_code, verification: str | None = None)`; `completion_kind` vira `"unverified"` quando `verification == "unverified"`, senão a regra atual.

- [ ] **Step 1: Testes que falham** em `test_code_completion_without_gate.py`, copiando os auxiliares `_Store`, `_ScriptedProvider`, `_tool_call`, `_json_args`, `_ANSWER` de `test_phase_runtime.py:11-62`. `_code_runtime(tmp_path, provider) -> tuple[_Store, AgenticTurnRuntime]` cria `_Store()` com `store.turn["code_mode"] = "code"` e `AgenticTurnRuntime(store=store, provider=provider, toolset=AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True), system_prompt="prompt", limits=AgenticLimits(max_iterations=None, max_actions=None), phase_controller=None)` (a Tarefa 8 usa o mesmo auxiliar):

```python
def test_answer_after_an_edit_without_checks_completes_unverified(tmp_path):
    provider = _ScriptedProvider([_tool_call("c1", "write_file", _json_args({"path": "a.py", "content": "x = 1\n"})), _ANSWER])
    store, runtime = _code_runtime(tmp_path, provider)
    result = runtime.run("turn-1")
    assert result.state == "completed" and result.verification == "unverified"
    assert ("code_verification", {"status": "unverified"}) in store.events
    assert len(provider.requests) == 2  # nenhuma rodada extra pedindo verify_project
    assert not any("Sistema do Modo Code" in str(r["messages"]) for r in provider.requests)

def test_answer_without_tools_is_accepted(tmp_path):
    store, runtime = _code_runtime(tmp_path, _ScriptedProvider([_ANSWER]))
    result = runtime.run("turn-1")
    assert result.state == "completed" and result.verification == "not_applicable"

def test_verification_status_reflects_a_passing_check(tmp_path):
    tools = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True)
    tools.invoke("write_file", {"path": "a.py", "content": "x = 1\n"})
    assert tools.code_verification_status() == "unverified"
    tools.invoke("run_command", {"command": f'"{sys.executable}" -m pytest --version'})
    assert tools.code_verification_status() == "verified"
```

E em `tests/unit/workers/` (ou no teste do worker de chat que já cobre `_settle_code_mode`; ache com `rg -n "_settle_code_mode" tests`): `completion_kind == "unverified"` quando o resultado vem com `verification="unverified"`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_code_completion_without_gate.py -v`
Expected: FAIL (`AgenticRunResult` sem `verification`; segunda requisição com "Sistema do Modo Code").

- [ ] **Step 3: Implementar.** Remover o bloco `:1348-1384` inteiro (e a variável `code_gate_rejections`), o atributo `completion_gate` e a linha `session.py:4703`. Tirar a chamada a `code_completion_gate` do `NativeTurnRunner` (o `_needs_verification` e o hook `STOP` ficam). Os dois caminhos usam um auxiliar novo `AgenticTurnRuntime._code_verification(self, turn) -> str | None` e passam o valor para `AgenticRunResult(..., verification=...)` e para `self._life(turn, "code_verification", status=...)`. O auxiliar devolve `None` se o turno é de subagente (`turn.get("is_subagent")`) ou não é modo código; senão `self.toolset.code_verification_status()` quando existir. Passar `verification=getattr(result, "verification", None)` em `workers/chat.py:630`. Na linha `system_prompt.py:61`, trocar o texto por: `"- Verify your work before you say it is done: run the checks that cover your change (the project's tests, type checks or build, or {VerifyProject} when present). If you could not verify, say so plainly; the interface marks the turn as not verified. Never say a check passed if you did not run it."` (com o rótulo de `verify_project` só quando `has("verify_project")`).

- [ ] **Step 4: Reescrever os testes do portão antigo** para o status novo: os de `test_agent_tools.py`, `test_git_change_tracking_baseline.py` e `test_orin_code_phase_a.py` passam a afirmar `code_verification_status()`; `test_phase_runtime.py:335-351` e `test_runtime_fixes_v076.py:405-420` afirmam que o turno conclui `completed` sem a mensagem "Ressalva de verificação"; o fake de `test_lifecycle_runtime.py:110` perde o método; `test_cli_turn_session.py:124-126` é removido (o atributo não existe mais).

- [ ] **Step 5: Rodar**

Run: `uv run pytest tests/unit/agentic tests/unit/cli_code tests/unit/workers tests/unit/test_orin_code_phase_a.py tests/unit/test_orin_code_phase_b.py -q`
Expected: PASS (inclui `tests/unit/agentic/test_native_turn.py` da Fase 2; o caso dele que esperava a continuação pedida pelo portão passa a esperar conclusão com `verification == "unverified"`).

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic src/agentos/workers/chat.py src/agentos/code_mode/system_prompt.py tests/unit
git commit -m "feat(code): drop the forced completion gate and report unverified deliveries"
```

---

### Task 7: Indicador "não verificado" na UI (R3.3, parte 2)

**Files:**
- Modify: `frontend/src/features/conversations/activityTypes.ts:150` (tipo `'code_mode.unverified'`), `:205` (campo `verification?: 'unverified'`)
- Modify: `frontend/src/api/conversations.ts:586` (lê `payload.verification`)
- Modify: `frontend/src/features/conversations/CodeModeCard.tsx` (rótulo e aviso; exporta `UnverifiedNotice`)
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx:996-1050` (ramo do modo código renderiza o aviso)
- Modify: `frontend/src/features/conversations/ChatPage.tsx:128` (texto da notificação deixa de afirmar "validada")
- Test: `frontend/src/features/conversations/__tests__/CodeUnverified.test.tsx`

**Interfaces:**
- Consumes: evento `code_mode.unverified` com `payload.verification === "unverified"` (Tarefa 6).
- Produces: `export function UnverifiedNotice(): JSX.Element` em `CodeModeCard.tsx`, renderizando `<p className="code-mode-card__notice" data-testid="code-unverified">Não verificado — nenhuma verificação rodou depois das alterações.</p>` (classe já existente, sem CSS novo).

- [ ] **Step 1: Testes que falham** (padrão de `__tests__/TurnTimelineExtensions.test.tsx:87-114`):

```tsx
const unverified: ConversationActivityEvent = {
  eventId: 'evt-unv', cursor: '9', turnId: 'turn-1', type: 'code_mode.unverified', kind: 'lifecycle',
  state: 'completed', agentId: 'agent:main', summary: 'Entrega não verificada', verification: 'unverified',
}

it('shows "não verificado" in the code-mode timeline', () => {
  render(<TurnTimeline mode="code" items={[{ id: 'code-mode:turn-1', kind: 'activity', group: {
    id: 'code-mode:turn-1', kind: 'lifecycle', state: 'completed', label: 'Modo Code', count: 1,
    agentId: 'agent:main', failed: false, events: [unverified] } }]} />)
  expect(screen.getByTestId('code-unverified')).toHaveTextContent('Não verificado')
})

it('labels the code-mode card as not verified', () => {
  const completed = { ...unverified, eventId: 'evt-done', type: 'code_mode.completed', codeStage: 'completed', verification: undefined }
  render(<CodeModeCard group={{ id: 'g', kind: 'lifecycle', state: 'completed', label: 'Modo Code', count: 2,
    agentId: 'agent:main', failed: false, events: [unverified, completed] }} />)
  expect(screen.getByText('Concluído · não verificado')).toBeInTheDocument()
})

it('parses verification from the activity payload', () => {
  const event = parseConversationActivityEvent({
    event_id: 'e1', event_type: 'code_mode.unverified', agent_id: 'agent:main', turn_id: 'turn-1',
    summary: 'Entrega não verificada', payload: { verification: 'unverified', changed_paths: 2 },
  }, 'c1')
  expect(event.verification).toBe('unverified')
})
```

(`parseConversationActivityEvent(value, cursor)` é o normalizador de `api/conversations.ts`; confira no começo da função quais chaves do item ela lê — `event_type`/`type` — e ajuste o objeto do teste a elas.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd frontend && npx vitest run src/features/conversations/__tests__/CodeUnverified.test.tsx`
Expected: FAIL (`Unable to find an element by: [data-testid="code-unverified"]`).

- [ ] **Step 3: Implementar.** `conversations.ts`: `if (payload.verification === 'unverified') event.verification = 'unverified'`. `CodeModeCard`: `const unverified = group.events.some((e) => e.type === 'code_mode.unverified')`; `stateLabel` terminal vira `unverified ? 'Concluído · não verificado' : 'Concluído'`, e `{terminal && unverified && <UnverifiedNotice />}` junto dos outros avisos. `TurnTimeline`, dentro do laço do ramo `code-mode:` (`:1009`): `else if (event.type === 'code_mode.unverified') { flushCodeToolBatch(); renderedNodes.push(<UnverifiedNotice key={event.eventId} />) }`. `ChatPage.tsx:128`: corpo `'A entrega está pronta para sua revisão.'`.

- [ ] **Step 4: Rodar a suíte do frontend da conversa**

Run: `cd frontend && npx vitest run src/features/conversations`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/conversations frontend/src/api/conversations.ts
git commit -m "feat(ui): show 'não verificado' when a code turn ends without checks"
```

---

### Task 8: Todos intactos no fim do turno (R3.4)

**Files:**
- Modify: `src/agentos/agentic/tools/planning/todo.py:78-91` (remove `complete_remaining_todos`)
- Modify: `src/agentos/agentic/runtime.py:1372`, `:1431` (chamadas) e `:2490-2516` (remove `_settle_remaining_todos`); `src/agentos/agentic/native_turn.py` (a chamada do `NativeTurnRunner` no ramo `success`)
- Modify: `frontend/src/features/conversations/activityReducer.ts:135-150` (`settleTodos`)
- Test: `tests/unit/agentic/test_todos_survive_turn_end.py`; `frontend/src/features/conversations/codeFlowFixes.test.tsx:18-30`
- Modify (testes antigos): `tests/unit/agentic/test_agent_tools.py:1170-1186` (remove), `tests/unit/agentic/tools/test_split.py:52` (tupla fica `("todo_write",)`), `tests/unit/test_orin_code_phase_b.py:588-600` (remove as duas asserções de `complete_remaining_todos`, mantém a do estado dos todos)

**Interfaces:**
- Produces: `settleTodos(todos, outcome)` — para `'completed'` devolve `todos` sem mudança; para `'failed'`/`'cancelled'` mantém a regra atual (`in_progress` → `pending`).

- [ ] **Step 1: Testes que falham.** Python (auxiliares de `test_phase_runtime.py:11-62` e o `_code_runtime` da Tarefa 6):

```python
def test_turn_end_keeps_pending_and_in_progress_todos(tmp_path):
    todos = [{"id": "1", "content": "a", "status": "completed", "activeForm": "a"},
             {"id": "2", "content": "b", "status": "in_progress", "activeForm": "b"},
             {"id": "3", "content": "c", "status": "pending", "activeForm": "c"}]
    provider = _ScriptedProvider([_tool_call("t1", "todo_write", _json_args({"todos": todos})), _ANSWER])
    store, runtime = _code_runtime(tmp_path, provider)
    assert runtime.run("turn-1").state == "completed"
    assert [t["status"] for t in runtime.toolset._todos] == ["completed", "in_progress", "pending"]
    assert not any(p.get("invocation_id", "").startswith("auto_todo_settle_") for _, p in store.events)
```

Frontend (`codeFlowFixes.test.tsx:18`, reescrito):

```ts
it('settleTodos leaves todos untouched when the turn completes', () => {
  const todos = [{ id: '1', content: 'a', status: 'in_progress', activeForm: 'a' }, { id: '2', content: 'b', status: 'pending', activeForm: 'b' }]
  expect(settleTodos(todos, 'completed')).toBe(todos)
})
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_todos_survive_turn_end.py -v` e `cd frontend && npx vitest run src/features/conversations/codeFlowFixes.test.tsx`
Expected: FAIL (status `completed` nos três; `toBe` falha).

- [ ] **Step 3: Implementar** as remoções e a regra nova de `settleTodos`.

- [ ] **Step 4: Rodar**

Run: `uv run pytest tests/unit/agentic tests/unit/test_orin_code_phase_b.py -q` e `cd frontend && npx vitest run src/features/conversations`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/tools/planning/todo.py src/agentos/agentic/runtime.py frontend/src/features/conversations tests/unit
git commit -m "fix(code): stop auto-completing todos at the end of a turn"
```

---

### Task 9: Lint depois do edit só nas linhas alteradas, em resumo curto (R3.5)

Decisão: a primeira alternativa da spec (limitar às linhas alteradas e a um resumo curto), mantendo o lint ligado. Sem opção nova nas configurações.

**Files:**
- Modify: `src/agentos/agentic/diagnostics.py:138-155` (`file_diagnostic_command`), `src/agentos/agentic/tools/_shared.py:141` (`MAX_FILE_DIAGNOSTIC_CHARS = 600`)
- Modify: `src/agentos/agentic/agent_tools.py:1112-1153` (`_with_file_diagnostic`)
- Test: `tests/unit/agentic/test_lint_after_edit.py`
- Modify (testes antigos): `tests/unit/agentic/test_agent_tools.py:142-162` (passam a simular a saída JSON do ruff)

**Interfaces:**
- Produces (em `agentos.agentic.diagnostics`):
  - `@dataclass(frozen=True, slots=True) class FileDiagnosticCommand: command: str; format: Literal["ruff_json", "eslint_json"]`
  - `file_diagnostic_command(path: str, *, project_root: Path) -> FileDiagnosticCommand | None` — comandos `python -m ruff check --output-format=json "{path}"` e `npx --no-install eslint --format json "{path}"` (mesmas condições de hoje).
  - `@dataclass(frozen=True, slots=True) class FileDiagnostic: line: int; column: int; code: str; message: str`
  - `parse_file_diagnostics(fmt: str, stdout: str) -> list[FileDiagnostic] | None` — `None` quando a saída não é o JSON esperado (linter ausente, crash). ruff: lista de `{"code", "message", "location": {"row", "column"}}`; eslint: lista de `{"messages": [{"ruleId", "message", "line", "column"}]}`.
  - `changed_line_ranges(payload: Mapping[str, object]) -> list[tuple[int, int]] | None` — de `payload["structured_patch"]` (`newStart`, `newLines`, formato de `_shared.py:239-270`); `None` quando não há patch (arquivo inteiro).
  - `summarize_diagnostics(diagnostics, ranges, *, margin: int = 2, max_items: int = 5) -> str | None` — só os diagnósticos dentro de algum intervalo ± `margin`; `None` quando nenhum. Formato: `"[lint — {k} problema(s) nas linhas alteradas{', +' + str(fora) + ' fora delas' if fora else ''}]"` e até 5 linhas `"{line}:{column} {code} {message}"`, tudo cortado em `MAX_FILE_DIAGNOSTIC_CHARS`.

- [ ] **Step 1: Testes que falham** (monkeypatch de `agent_tools.subprocess.run` devolvendo `stdout` JSON, como em `test_agent_tools.py:149-155`):

```python
RUFF = json.dumps([
    {"code": "F401", "message": "`os` imported but unused", "location": {"row": 1, "column": 8}},
    {"code": "E501", "message": "Line too long", "location": {"row": 40, "column": 89}},
])

def test_only_problems_on_changed_lines_are_reported(toolset, workspace, monkeypatch):
    workspace.write_text("app.py", "".join(f"x{i} = {i}\n" for i in range(1, 51)))
    toolset.invoke("read_file", {"path": "app.py"})
    _fake_run(monkeypatch, RUFF, returncode=1)
    outcome = toolset.invoke("edit_file", {"path": "app.py", "old_text": "x40 = 40", "new_text": "x40 = 'a' * 200"})
    assert "[lint — 1 problema(s) nas linhas alteradas, +1 fora delas]" in outcome.content
    assert "40:89 E501" in outcome.content and "F401" not in outcome.content

def test_clean_or_out_of_range_lint_adds_nothing(toolset, workspace, monkeypatch):
    workspace.write_text("app.py", "".join(f"x{i} = {i}\n" for i in range(1, 51)))
    toolset.invoke("read_file", {"path": "app.py"})
    _fake_run(monkeypatch, json.dumps([RUFF_F401_ONLY]), returncode=1)
    outcome = toolset.invoke("edit_file", {"path": "app.py", "old_text": "x40 = 40", "new_text": "x40 = 41"})
    assert "lint" not in outcome.content and "diagnóstico" not in outcome.content

def test_missing_linter_adds_nothing(toolset, monkeypatch):
    _fake_run(monkeypatch, "", returncode=1, stderr="No module named ruff")
    outcome = toolset.invoke("write_file", {"path": "new.py", "content": "import os\n"})
    assert "lint" not in outcome.content and "No module named" not in outcome.content

def test_new_file_counts_every_line_as_changed(toolset, monkeypatch):
    _fake_run(monkeypatch, json.dumps([RUFF_F401_ONLY]), returncode=1)
    outcome = toolset.invoke("write_file", {"path": "new.py", "content": "import os\n"})
    assert "1:8 F401" in outcome.content
```

(`RUFF_F401_ONLY` é o primeiro item de `RUFF`; `_fake_run` devolve um objeto com `returncode`, `stdout`, `stderr`.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_lint_after_edit.py -v`
Expected: FAIL (`diagnóstico automático` com a saída inteira).

- [ ] **Step 3: Implementar** os quatro helpers e reescrever `_with_file_diagnostic` para: comando → `subprocess.run` (mesmos parâmetros de hoje, `:1137-1141`) → `parse_file_diagnostics` → `None` sai sem anexar → `summarize_diagnostics(diags, changed_line_ranges(outcome.payload))` → `None` sai sem anexar → anexa `"\n\n" + resumo`. Mantém o debounce por caminho (`:1131-1135`).

- [ ] **Step 4: Atualizar** `test_agent_tools.py:142-162` (o primeiro teste simula o JSON do ruff com um F401 na linha 1 e afirma `"[lint —"`; o de debounce afirma uma única chamada) e rodar

Run: `uv run pytest tests/unit/agentic -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/diagnostics.py src/agentos/agentic/tools/_shared.py src/agentos/agentic/agent_tools.py tests/unit/agentic
git commit -m "feat(code): lint after edit reports only changed lines in a short summary"
```

---

### Task 10: Contrato `CliAgentAdapter`, adapter do Claude e suíte de conformidade (R3.7)

**Files:**
- Create: `src/agentos/providers/cli_adapter.py`
- Create: `src/agentos/providers/claude_cli/adapter.py`
- Modify: `src/agentos/providers/claude_cli/stream.py`, `errors.py` (os tipos neutros passam a morar em `cli_adapter.py` e são reexportados daqui, sem mudar nenhum import existente)
- Modify: `src/agentos/providers/claude_cli/native_loop.py`, `provider.py` (`start_native_turn` e `stream` usam `self.adapter`)
- Create: `tests/unit/providers/cli_adapter_conformance.py`
- Create: `tests/unit/providers/claude_cli/test_adapter_conformance.py`
- Create (só se faltar nas fixtures da Fase 2): `tests/unit/providers/claude_cli/fixtures/error-404.jsonl`, `rate-limited.jsonl`

**Interfaces:**
- Consumes (Fase 2): todos os nomes da seção "Nomes fixos da Fase 2".
- Produces (em `agentos.providers.cli_adapter`, que não importa nada de `claude_cli`):
  - Movidos de `claude_cli/stream.py` e `claude_cli/errors.py` sem mudar campos: `CliUsage`, `CliInit`, `CliResult`, `CliEventKind`, `CliEvent`, `RateLimitState`. É o vocabulário que todo adapter de CLI emite.
  - Novos:

```python
class CliErrorKind(StrEnum):
    AUTH = "auth"; RATE_LIMITED = "rate_limited"; MODEL_NOT_FOUND = "model_not_found"
    CONTEXT_OVERFLOW = "context_overflow"; TRANSIENT = "transient"; FATAL = "fatal"

@dataclass(frozen=True, slots=True)
class CliErrorInfo:
    kind: CliErrorKind
    retryable: bool
    exception: Exception            # o que o loop levanta (no Claude, o ProviderTransportError da Fase 2)
    http_status: int | None = None
    resets_at: float | None = None

@dataclass(frozen=True, slots=True)
class CliModelInfo:
    model_id: str
    display_name: str
    context_window_tokens: int

@dataclass(frozen=True, slots=True)
class CliCapabilities:
    native_tool_loop: bool
    tool_list_changed: bool         # a CLI relê tools/list depois de notifications/tools/list_changed
    images_in_tool_results: bool
    persistent_stdin: bool

@dataclass(frozen=True, slots=True)
class CliTurnRequest:
    system_prompt_file: Path
    model: str | None = None
    effort: str | None = None
    fallback_model: str | None = None
    max_turns: int = 1
    mcp_config_file: Path | None = None
    mcp_server_name: str = "orin"
    session_id: str | None = None
    resume_session_id: str | None = None
    persist_session: bool = False
    autocompact: str | None = None

class CliStreamTranslator(Protocol):
    def feed_line(self, line: str) -> list[CliEvent]: ...   # linha vazia ou que não é JSON: [] sem exceção
    init: CliInit | None
    result: CliResult | None
    rate_limit: RateLimitState | None

@runtime_checkable
class CliAgentAdapter(Protocol):
    name: str
    capabilities: CliCapabilities
    def resolve_binary(self, env: Mapping[str, str] | None = None) -> str | None: ...
    def version_supported(self, binary: str) -> bool: ...
    def build_command(self, binary: str, request: CliTurnRequest) -> list[str]: ...
    def write_tool_server_config(self, handle: TurnToolServerHandle, directory: Path) -> Path: ...
    def loggable_command(self, argv: Sequence[str]) -> list[str]: ...
    def environment(self, base: Mapping[str, str] | None = None, *, max_output_tokens: int | None = None,
                    tool_timeout_ms: int | None = None) -> dict[str, str]: ...
    def stream_translator(self) -> CliStreamTranslator: ...
    def classify_error(self, result: CliResult, rate_limit: RateLimitState | None, *, now: float) -> CliErrorInfo: ...
    def models(self) -> tuple[CliModelInfo, ...]: ...
    def context_window(self, model_id: str) -> int: ...
```

  (`TurnToolServerHandle` só sob `TYPE_CHECKING`.)
- Produces (em `agentos.providers.claude_cli.adapter`): `class ClaudeCliAdapter` — `name = "claude"`; cada método delega à função da Fase 2 de mesmo papel (`resolve_claude_binary`; `claude_cli_version` + `VALIDATED_CLI_RANGE`; `build_claude_command(binary=..., **campos do request)`; `redact_argv`; `build_isolated_claude_env(base, max_output_tokens=..., mcp_tool_timeout_ms=tool_timeout_ms)`; `StreamJsonParser()`; `classify_result_error` embrulhado em `CliErrorInfo` com o `kind` derivado de `api_error_status`/`rate_limit` — 401/403 → AUTH, 404 → MODEL_NOT_FOUND, 429 ou `rate_limit.status == "rejected"` → RATE_LIMITED, 5xx/529 → TRANSIENT, `prompt is too long` no texto → CONTEXT_OVERFLOW, resto → FATAL; `discover_cached_models` + `context_window_for`, com `200_000` para modelo desconhecido). `write_tool_server_config` grava `handle.mcp_config_json()` num `tempfile.mkstemp` dentro de `directory` (só o dono lê), exatamente como `start_native_turn` faz hoje, e devolve o caminho. `capabilities = CliCapabilities(native_tool_loop=NATIVE_LOOP_VALIDATED, tool_list_changed=False, images_in_tool_results=<item 4 do spike>, persistent_stdin=PERSISTENT_STDIN_OK)` (a Tarefa 11 decide `tool_list_changed`).
- Produces (em `ClaudeCliProvider`): atributo `adapter: CliAgentAdapter` (padrão `ClaudeCliAdapter()`, injetável no `__init__` para testes); `stream` e `start_native_turn` só chamam `self.adapter.*` para binário, versão, comando, config do MCP, ambiente, parser, erros e log. Nenhuma chamada direta a `command.py`/`stream.py`/`errors.py`/`catalog.py` sobra em `provider.py` ou `native_loop.py`.
- Produces (em `tests/unit/providers/cli_adapter_conformance.py`):

```python
@dataclass(frozen=True)
class StreamScenario:
    lines: tuple[str, ...]
    text: str | None = None                      # texto concatenado esperado (None: não confere)
    tool_uses: tuple[str, ...] = ()              # nomes em TOOL_USE_START, na ordem
    thinking: bool = False
    model: str | None = None                     # init.model esperado
    usage_positive: bool = True                  # result.usage com input e output > 0

@dataclass(frozen=True)
class ErrorScenario:
    lines: tuple[str, ...]
    kind: CliErrorKind
    retryable: bool

@dataclass(frozen=True)
class AdapterFixtures:
    streams: tuple[StreamScenario, ...]
    errors: tuple[ErrorScenario, ...]
    known_model_id: str
    known_context_window: int

@dataclass
class FakeToolServerHandle:
    url: str = "http://127.0.0.1:59999/mcp"
    token: str = "tok-SECRET-123"
    server_name: str = "orin"
    def mcp_config_json(self) -> str: ...        # {"mcpServers": {"orin": {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}}}

def load_jsonl(path: Path) -> tuple[str, ...]: ...

class CliAdapterConformance:
    """Mixin. Cada adapter tem um módulo `test_*` com `class Test<X>Conformance(CliAdapterConformance)`
    que implementa `make_adapter()` e `fixtures()`. Não começa com Test: o pytest não o coleta aqui."""
    def make_adapter(self) -> CliAgentAdapter: raise NotImplementedError
    def fixtures(self) -> AdapterFixtures: raise NotImplementedError
```

- [ ] **Step 1: Escrever a suíte** com estes testes no mixin (só usam o Protocol; `_run(adapter, lines)` devolve `(translator, events)` alimentando `feed_line` linha a linha):

```python
    def test_satisfies_the_protocol(self):
        assert isinstance(self.make_adapter(), CliAgentAdapter)

    def test_command_is_argv_starting_with_the_binary(self, tmp_path):
        argv = self.make_adapter().build_command("/bin/cli", CliTurnRequest(system_prompt_file=tmp_path / "p.txt", model=self.fixtures().known_model_id, max_turns=4))
        assert argv[0] == "/bin/cli" and all(isinstance(a, str) for a in argv)

    def test_tool_server_config_carries_url_and_token_but_argv_never_carries_the_token(self, tmp_path):
        adapter, handle = self.make_adapter(), FakeToolServerHandle()
        config = adapter.write_tool_server_config(handle, tmp_path)
        assert config.is_file() and handle.url in config.read_text(encoding="utf-8")
        argv = adapter.build_command("/bin/cli", CliTurnRequest(system_prompt_file=tmp_path / "p.txt", mcp_config_file=config))
        assert handle.token not in " ".join(argv)
        assert str(config) not in " ".join(adapter.loggable_command(argv))

    def test_environment_returns_a_new_mapping_and_keeps_the_base(self):
        base = {"PATH": "/usr/bin"}
        env = self.make_adapter().environment(base)
        assert env is not base and base == {"PATH": "/usr/bin"}

    def test_streams(self):
        for scenario in self.fixtures().streams:
            translator, events = _run(self.make_adapter(), scenario.lines)
            kinds = [e.kind for e in events]
            if scenario.text is not None:
                assert "".join(e.text for e in events if e.kind is CliEventKind.TEXT_DELTA) == scenario.text
            assert [e.tool_name for e in events if e.kind is CliEventKind.TOOL_USE_START] == list(scenario.tool_uses)
            assert (CliEventKind.THINKING_DELTA in kinds) is scenario.thinking
            assert translator.result is not None and kinds[-1] is CliEventKind.RESULT
            if scenario.model: assert translator.init is not None and translator.init.model == scenario.model
            if scenario.usage_positive: assert translator.result.usage.input_tokens > 0 and translator.result.usage.output_tokens > 0

    def test_garbage_lines_are_ignored(self):
        translator = self.make_adapter().stream_translator()
        assert translator.feed_line("not json") == [] and translator.feed_line("") == []

    def test_errors_are_classified(self):
        for scenario in self.fixtures().errors:
            translator, _ = _run(self.make_adapter(), scenario.lines)
            info = self.make_adapter().classify_error(translator.result, translator.rate_limit, now=0.0)
            assert (info.kind, info.retryable) == (scenario.kind, scenario.retryable)
            assert isinstance(info.exception, Exception)

    def test_context_windows(self):
        adapter, fx = self.make_adapter(), self.fixtures()
        assert adapter.context_window(fx.known_model_id) == fx.known_context_window
        assert adapter.context_window("no-such-model") > 0

    def test_capabilities_are_declared(self):
        assert isinstance(self.make_adapter().capabilities, CliCapabilities)
```

- [ ] **Step 2: Escrever o módulo do Claude** `tests/unit/providers/claude_cli/test_adapter_conformance.py`: `class TestClaudeCliAdapterConformance(CliAdapterConformance)`; `make_adapter` devolve `ClaudeCliAdapter()` apontado para um diretório temporário de catálogo com um `model-catalog/*.json` mínimo contendo `claude-opus-5-5` com janela `1_000_000` (o mesmo mecanismo de `cache_dir` dos testes de `catalog.py` da Fase 2, para não ler `~/.claude`); `fixtures()` usa `out-2.jsonl` (ferramenta `mcp__orin__…` chamada), `out-3.jsonl` (parada no `tool_use`) e `out-5.jsonl` (thinking longo) de `tests/unit/providers/claude_cli/fixtures/`, com os valores esperados lidos dessas linhas, e dois `ErrorScenario`: 404 → `MODEL_NOT_FOUND`/não retentável, limite rejeitado → `RATE_LIMITED`/não retentável. Se a Fase 2 não gravou fixtures de erro, criar `error-404.jsonl` (um `system/init` + `{"type":"result","subtype":"error_during_execution","is_error":true,"api_error_status":404,...}`) e `rate-limited.jsonl` (um evento de limite com `rateLimitType` e `status: "rejected"` + o `result` de erro) no mesmo formato que o `StreamJsonParser` aceita nos testes dele. Não invocar a CLI.

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/unit/providers/claude_cli/test_adapter_conformance.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.providers.cli_adapter'`.

- [ ] **Step 4: Implementar** `cli_adapter.py` (movendo os tipos e reexportando-os de `stream.py`/`errors.py`), `claude_cli/adapter.py`, e trocar as chamadas diretas de `provider.py`/`native_loop.py` por `self.adapter.*`.

- [ ] **Step 5: Rodar** a conformidade e toda a suíte da Fase 2 (o refactor não pode mudar comportamento)

Run: `uv run pytest tests/unit/providers tests/unit/provider_catalog tests/unit/agentic/test_native_turn.py -q`
Expected: PASS. `rg -n "opencode" src/agentos/providers` → nenhuma ocorrência.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/providers tests/unit/providers
git commit -m "feat(providers): CliAgentAdapter contract with a conformance suite; Claude adapter conforms"
```

---

### Task 11: Ferramentas sob demanda no caminho nativo da CLI (R3.1, parte 3)

No caminho nativo (Fase 2, Task 9), o `NativeTurnRunner` calcula `tool_schemas` uma vez antes de subir o processo e o `RuntimeToolExecutor.list_tools()` devolve essa lista fixa; uma ferramenta descoberta por `ToolSearch` no meio do processo só fica chamável se o `tools/list` refletir as descobertas **e** a CLI reler a lista depois de `notifications/tools/list_changed`. O `TurnToolServer` da Fase 2 só aceita POST e responde JSON, então hoje ele não tem como mandar essa notificação.

**Files:**
- Modify: `src/agentos/agentic/native_turn.py` (`RuntimeToolExecutor.list_tools` dinâmico; `tools_changed` no resultado; `allow_deferral`)
- Modify: `src/agentos/providers/cli_tools_mcp/types.py` (`ToolCallResult.tools_changed`), `server.py` (resposta SSE com a notificação)
- Modify: `src/agentos/providers/claude_cli/adapter.py` (`capabilities.tool_list_changed`)
- Test: `tests/unit/agentic/test_native_turn_deferred_tools.py`, `tests/unit/providers/cli_tools_mcp/test_tools_list_changed.py`
- Create: `docs/agent_memory/2026-11-05-claude-cli-tools-list-changed.md` (resultado do spike, curto)

**Interfaces:**
- Consumes: `AgentToolset.discovered_tool_names` (Tarefa 1); `_tool_schemas(turn, *, allow_deferral=...)` (Tarefa 1); `CliCapabilities.tool_list_changed`, `ClaudeCliProvider.adapter` (Tarefa 10).
- Produces:
  - `RuntimeToolExecutor.list_tools()` recalcula `runtime._names.schemas_out(runtime._tool_schemas(turn, allow_deferral=<flag>))` a cada chamada (o parâmetro `tool_schemas` do `__init__` passa a ser só o valor inicial para o `context_updated`).
  - `ToolCallResult.tools_changed: bool = False`; o executor marca `True` quando `toolset.discovered_tool_names` cresceu durante a chamada.
  - `TurnToolServer`: num `tools/call` cujo resultado tem `tools_changed` e cuja requisição aceita `text/event-stream` (header `Accept`), responde em SSE com dois eventos — `{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}` e depois a resposta JSON-RPC; senão, JSON como hoje.
  - `NativeTurnRunner` passa `allow_deferral=provider.adapter.capabilities.tool_list_changed` ao montar o executor e no cálculo inicial.

- [ ] **Step 1: Spike manual (portão de decisão; roda o implementador, não a suíte).** Com a CLI instalada e o `TurnToolServer` já com o Step 4 implementado atrás de uma flag local, rodar o turno nativo da Fase 2 com o prompt "use ToolSearch com select:WebFetch e depois chame WebFetch em https://example.com". Registrar no documento de `docs/agent_memory/`: versão da CLI; se ela mandou `Accept: text/event-stream` no `tools/call`; se pediu `tools/list` de novo depois da notificação; se `WebFetch` foi chamado.

- [ ] **Step 2: Testes que falham**

```python
def test_list_tools_follows_discoveries(native_runtime_fixture):
    executor = RuntimeToolExecutor(runtime, turn, tool_schemas=initial, correlate=_correlate)
    before = {spec.name for spec in executor.list_tools()}
    result = executor.call_tool(ToolCallRequest("ToolSearch", {"query": "select:WebFetch"}, {}))
    assert result.tools_changed is True
    assert "WebFetch" not in before and "WebFetch" in {spec.name for spec in executor.list_tools()}

def test_without_list_changed_support_the_native_path_publishes_everything(native_runtime_fixture):
    # adapter fake com capabilities.tool_list_changed=False → list_tools() já contém WebFetch antes de qualquer ToolSearch

async def test_tools_call_with_tools_changed_streams_the_notification_first():
    # executor fake devolvendo ToolCallResult("ok", tools_changed=True); POST tools/call com Accept: application/json, text/event-stream
    # → content-type text/event-stream; primeiro evento notifications/tools/list_changed, segundo a resposta com o mesmo id

async def test_tools_call_without_sse_accept_answers_json():
    # mesmo executor, Accept: application/json → resposta JSON igual à da Fase 2
```

(`native_runtime_fixture` reusa a montagem de runtime + toolset de `tests/unit/agentic/test_native_turn.py` da Fase 2, com `AgentToolset(code_mode_active=True, public_tool_names=True)`.)

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/unit/agentic/test_native_turn_deferred_tools.py tests/unit/providers/cli_tools_mcp/test_tools_list_changed.py -v`
Expected: FAIL (`ToolCallResult` sem `tools_changed`).

- [ ] **Step 4: Implementar** os três itens de Produces.

- [ ] **Step 5: Fixar a capacidade** em `ClaudeCliAdapter.capabilities.tool_list_changed` com o resultado do Step 1. **Aprovado** → `True`: o caminho nativo também publica só o núcleo. **Reprovado** → `False`: o caminho nativo publica o conjunto completo do modo código, e o documento do spike registra que a meta de `tools_tokens` < 4.000 vale para os provedores de API e para o protocolo de texto (que reenvia `request["tools"]` a cada iteração), não para o nativo, até a CLI tratar a notificação.

- [ ] **Step 6: Rodar**

Run: `uv run pytest tests/unit/agentic/test_native_turn.py tests/unit/agentic/test_native_turn_deferred_tools.py tests/unit/providers -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agentos/agentic/native_turn.py src/agentos/providers tests/unit docs/agent_memory/2026-11-05-claude-cli-tools-list-changed.md
git commit -m "feat(providers): deferred tools on the native CLI path via tools/list_changed"
```

---

### Task 12: Fila justa de CLI por conversa — backend (R3.6, parte 1)

**Files:**
- Create: `src/agentos/providers/cli_queue.py`
- Modify: `src/agentos/agentic/provider_stream.py:41-50` (`StreamKind.WAITING`), `:176-193` (campos novos em `NormalizedStreamItem`)
- Modify: `src/agentos/agentic/runtime.py:831-834` (pedido leva `conversation_id`), `:983-992` (tratamento de `WAITING`)
- Modify: `src/agentos/agentic/session.py:1495` (ramo `provider_waiting`, ao lado de `retrying`)
- Modify: `src/agentos/providers/claude_cli/provider.py` (`CLI_SLOTS` sai: `stream`, `start_native_turn` e o executor embrulhado que devolve/readquire a vaga em volta de `call_tool`), `src/agentos/providers/claude_cli/native_loop.py` (`NativeTurnRequest.on_waiting_slot`), `src/agentos/agentic/native_turn.py` (o runner liga `on_waiting_slot`)
- Test: `tests/unit/providers/test_cli_queue.py`, `tests/unit/agentic/test_provider_waiting.py`, e casos novos nos testes do provider/loop nativo da Fase 2

**Interfaces:**
- Produces (em `agentos.providers.cli_queue`):
  - **Sem limite fixo por padrão (decisão do Carlos: poder rodar 10+ subagentes ao mesmo tempo).** `CLI_MAX_PROCESSES_ENV = "ORIN_CLI_MAX_PROCESSES"`: ausente, vazio, `0` ou inválido → `limit = None` (sem teto de contagem); inteiro ≥ 1 → teto. O paralelismo continua limitado pelo teto de subagentes que já existe (`ORIN_MAX_CONCURRENT_SUBAGENTS`, padrão 16).
  - **Proteção de memória no lugar do teto:** `CLI_MIN_FREE_MEMORY_ENV = "ORIN_CLI_MIN_FREE_MEMORY_MB"` (padrão `1024`; `0` desliga). Um ticket só é concedido se `free_memory_mb()` ≥ esse valor **ou** se não há nenhum processo ativo (nunca trava o primeiro). `free_memory_mb() -> int | None` em `cli_queue.py`: Windows via `ctypes` `GlobalMemoryStatusEx.ullAvailPhys`; Linux via `/proc/meminfo` `MemAvailable`; outros → `None` (sem proteção). Medido a cada concessão, não em cache.
  - Motivo de espera: `CliSlotTicket.wait_reason() -> Literal["limit", "memory"] | None`.
  - `class CliSlotTicket`: `conversation_id: str`; `wait(timeout: float) -> bool` (True quando a vaga foi concedida); `position() -> int` (0 se concedida; senão 1 + quantos esperam à frente pela regra de concessão); `release() -> None` (idempotente; num ticket ainda na fila, só sai dela).
  - `class CliSlotQueue`: `__init__(self, limit: int | None = None, *, min_free_memory_mb: int = 1024, memory_probe: Callable[[], int | None] = free_memory_mb)`; `limit: int | None`; `enqueue(conversation_id: str) -> CliSlotTicket`; `active_count() -> int`; `@classmethod from_env(cls, env: Mapping[str, str] = os.environ) -> CliSlotQueue`. Com espera por memória, a fila reavalia a cada `poll_seconds` (a memória muda sem `release`).
  - `cli_slot_queue() -> CliSlotQueue` — instância única do processo (criada com `from_env()` na primeira chamada), compartilhada por todo adapter de CLI; `set_cli_slot_queue(queue: CliSlotQueue | None) -> None` só para testes.
  - `acquire_cli_slot(conversation_id: str, *, cancel_check: Callable[[], bool] | None, on_wait: Callable[[int, int], None] | None, poll_seconds: float = 0.05) -> CliSlotTicket | None` — bloqueia até a vaga; chama `on_wait(position, limit)` quando a posição muda; devolve `None` (e libera o ticket) se `cancel_check()` ficar verdadeiro.
  - Regra de concessão (thread-safe, `threading.Condition`): sempre que há vaga, concede ao ticket em espera cuja conversa tem **menos vagas ativas**; empate pela ordem de chegada.
- Produces (em `provider_stream`): `StreamKind.WAITING = "waiting"`; `NormalizedStreamItem.queue_position: int | None = None`, `.queue_limit: int | None = None`.
- Produces (no runtime): o dicionário `request` ganha `"conversation_id": str(turn.get("conversation_id") or "")`. Ao receber `WAITING`: `last_progress_at = now_clock` (conta como progresso para o teste de travamento, sem estender o `deadline`), não marca `provider_yielded`, emite `self._life(turn, "provider_waiting", position=..., limit=...)` só quando a posição muda, e `continue`.
- Produces (na sessão): `provider_waiting` grava `TURN_STARTED` (agente principal) ou `TOOL_PROGRESSED` com `parent_agent_id` (subagente, como `retrying` em `session.py:1495-1505`), resumo `f"Aguardando memória livre para abrir outro processo ({position}º na fila)"` quando o motivo é `memory`, ou `f"Aguardando vaga na CLI ({position}º na fila, limite de {limit})"` quando é `limit` e payload `{**base, "status": "queued", "waiting_for_slot": True, "queue_position": position}`.
- Produces (no provider da Fase 2):
  - `stream`: `ticket = cli_slot_queue().enqueue(request.get("conversation_id") or request.get("turn_id") or "")`; enquanto `not ticket.wait(0.5)`: se `cancel_check()` → `ticket.release()` e `return`; senão `yield NormalizedStreamItem(StreamKind.WAITING, queue_position=ticket.position(), queue_limit=queue.limit)`. `ticket.release()` no `finally` onde hoje está o `release` do `CLI_SLOTS`.
  - `start_native_turn` e o executor embrulhado (devolve a vaga antes de `call_tool`, readquire depois): `acquire_cli_slot(request.conversation_id, cancel_check=request.cancel_check, on_wait=request.on_waiting_slot)`.
  - `NativeTurnRequest` ganha `conversation_id: str = ""` e `on_waiting_slot: Callable[[int, int], None] | None = None`; o `NativeTurnRunner` passa `turn["conversation_id"]` e `lambda position, limit: runtime._life(turn, "provider_waiting", position=position, limit=limit)`.
  - `CLI_SLOTS` é apagado.

- [ ] **Step 1: Testes que falham** em `test_cli_queue.py`:

```python
def test_a_conversation_with_slots_yields_to_one_without():
    q = CliSlotQueue(limit=2)
    a1, a2 = q.enqueue("A"), q.enqueue("A")
    assert a1.wait(0) and a2.wait(0)
    a3, b1 = q.enqueue("A"), q.enqueue("B")
    assert b1.position() == 1 and a3.position() == 2
    a1.release()
    assert b1.wait(1) and not a3.wait(0)

def test_ties_are_first_come_first_served():
    q = CliSlotQueue(limit=1)
    first = q.enqueue("A"); assert first.wait(0)
    b, c = q.enqueue("B"), q.enqueue("C")
    first.release()
    assert b.wait(1) and not c.wait(0)

def test_releasing_a_waiting_ticket_leaks_nothing():
    q = CliSlotQueue(limit=1)
    held = q.enqueue("A"); assert held.wait(0)
    gone, next_ = q.enqueue("B"), q.enqueue("C")
    gone.release(); gone.release()
    held.release()
    assert next_.wait(1) and q.active_count() == 1

def test_cancel_while_waiting_returns_none_and_frees_the_place():
    q = CliSlotQueue(limit=1); set_cli_slot_queue(q)
    held = q.enqueue("A"); assert held.wait(0)
    positions = []
    assert acquire_cli_slot("B", cancel_check=lambda: True, on_wait=lambda p, l: positions.append((p, l))) is None
    held.release()
    assert q.active_count() == 0

def test_limit_comes_from_the_environment():
    assert CliSlotQueue.from_env({}).limit is None
    assert CliSlotQueue.from_env({"ORIN_CLI_MAX_PROCESSES": "5"}).limit == 5
    assert CliSlotQueue.from_env({"ORIN_CLI_MAX_PROCESSES": "x"}).limit is None
    assert CliSlotQueue.from_env({"ORIN_CLI_MAX_PROCESSES": "0"}).limit is None

def test_ten_conversations_run_at_once_without_a_limit():
    q = CliSlotQueue(memory_probe=lambda: 16_000)
    tickets = [q.enqueue(f"C{i}") for i in range(10)]
    assert all(t.wait(0) for t in tickets) and q.active_count() == 10

def test_low_memory_holds_new_processes_but_never_the_first():
    free = {"mb": 500}
    q = CliSlotQueue(min_free_memory_mb=1024, memory_probe=lambda: free["mb"])
    first, second = q.enqueue("A"), q.enqueue("B")
    assert first.wait(0) and not second.wait(0.1) and second.wait_reason() == "memory"
    free["mb"] = 4000
    assert second.wait(1)

def test_unknown_memory_means_no_memory_guard():
    q = CliSlotQueue(memory_probe=lambda: None)
    assert all(q.enqueue(str(i)).wait(0) for i in range(5))
```

Em `test_provider_waiting.py` (auxiliares de `test_phase_runtime.py:11-62`; o provider fake devolve primeiro itens `WAITING` com posições 2, 2, 1, 1, 1 e depois texto + FINISH; o relógio do runtime é injetado via `clock=` e avança 60 s a cada item):

```python
def test_waiting_for_a_slot_longer_than_the_stall_limit_does_not_time_out():
    assert runtime.run("turn-1").state == "completed"   # 300 s de espera > 180 s do stall

def test_waiting_is_reported_once_per_position_and_request_carries_the_conversation():
    assert [p["position"] for s, p in store.events if s == "provider_waiting"] == [2, 1]
    assert provider.requests[0]["conversation_id"] == "conversation-1"
```

Nos testes da Fase 2: (a) `stream` com a fila de limite 1 já ocupada emite `WAITING` e, com `cancel_check` verdadeiro, termina com `active_count()` de volta a 1; (b) o teste de "ferramenta do turno 1 inicia o turno 2 com 1 vaga, sem deadlock" continua passando com a fila; (c) `start_native_turn` esperando vaga chama `on_waiting_slot`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/providers/test_cli_queue.py tests/unit/agentic/test_provider_waiting.py -v`
Expected: FAIL (`No module named 'agentos.providers.cli_queue'`).

- [ ] **Step 3: Implementar** a fila, `StreamKind.WAITING`, o tratamento no runtime, o ramo na sessão e as trocas no provider/loop nativo. Conferir com `rg -n "StreamKind\." src/agentos` se algum consumidor trata `StreamKind` de forma exaustiva e precisa ignorar `WAITING`.

- [ ] **Step 4: Rodar**

Run: `uv run pytest tests/unit/providers tests/unit/agentic tests/unit/provider_catalog -q`
Expected: PASS. `rg -n "CLI_SLOTS|BoundedSemaphore" src/agentos/providers src/agentos/provider_catalog` → nenhuma ocorrência ligada à CLI.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/providers src/agentos/agentic tests/unit
git commit -m "feat(providers): fair per-conversation CLI slot queue with a waiting event"
```

---

### Task 13: "Aguardando vaga" visível na UI (R3.6, parte 2)

**Files:**
- Modify: `frontend/src/api/conversations.ts:586` (lê `waiting_for_slot`, `queue_position`)
- Modify: `frontend/src/features/conversations/activityTypes.ts:205` (campos `waitingForSlot?: boolean`, `queuePosition?: number`), `:402` (`stateFor`)
- Modify: `frontend/src/features/conversations/AgentPulse.tsx` (exporta `pulseDetailFromEvents`)
- Modify: `frontend/src/features/conversations/ChatPage.tsx:2755-2759` (passa `detail`)
- Test: `frontend/src/features/conversations/__tests__/CliSlotWaiting.test.tsx`

**Interfaces:**
- Consumes: evento `turn.started` com `status: "queued"`, `waiting_for_slot: true`, `queue_position` (Tarefa 12).
- Produces: `stateFor('turn.started' | 'TURN_STARTED', 'queued') === 'queued'` (rótulo existente "Na fila", `activitySummary.ts:504`); `export function pulseDetailFromEvents(events: ConversationActivityEvent[]): string | undefined` — o `summary` do último evento quando ele tem `waitingForSlot`, senão `undefined`.

- [ ] **Step 1: Testes que falham**

```tsx
const waiting: ConversationActivityEvent = {
  eventId: 'w1', cursor: '1', turnId: 't1', type: 'turn.started', kind: 'lifecycle', state: 'queued',
  agentId: 'agent:main', summary: 'Aguardando vaga na CLI (2º na fila, limite de 3)', status: 'queued',
  waitingForSlot: true, queuePosition: 2,
}

it('maps a queued turn.started to the queued state', () => {
  expect(stateFor('turn.started', 'queued')).toBe('queued')
})

it('shows the waiting summary while the last event is waiting for a slot', () => {
  expect(pulseDetailFromEvents([waiting])).toBe('Aguardando vaga na CLI (2º na fila, limite de 3)')
  expect(pulseDetailFromEvents([waiting, { ...waiting, eventId: 'w2', waitingForSlot: undefined, summary: 'Pensando' }])).toBeUndefined()
  render(<AgentPulse mode="thinking" state="queued" detail={pulseDetailFromEvents([waiting])} />)
  expect(screen.getByText('Aguardando vaga na CLI (2º na fila, limite de 3)')).toBeInTheDocument()
})
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd frontend && npx vitest run src/features/conversations/__tests__/CliSlotWaiting.test.tsx`
Expected: FAIL (`pulseDetailFromEvents is not a function`).

- [ ] **Step 3: Implementar** o parse (`if (payload.waiting_for_slot === true) event.waitingForSlot = true`; `queue_position` numérico), o caso em `stateFor`, `pulseDetailFromEvents` e `detail={pulseDetailFromEvents(activity.events)}` no `<AgentPulse>` do `ChatPage`.

- [ ] **Step 4: Rodar**

Run: `cd frontend && npx vitest run src/features/conversations src/api`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/conversations.ts frontend/src/features/conversations
git commit -m "feat(ui): show 'aguardando vaga' while a turn waits for a CLI slot"
```

---

## Aceite da Fase 3 (spec)

- [ ] `uv run pytest tests/unit/providers/test_claude_cli_adapter_conformance.py -v` → PASS (suíte de conformidade passa para o adapter do Claude).
- [ ] `uv run pytest tests/unit/agentic/test_tools_tokens_budget.py -v` → PASS (`tools_tokens` < 4.000); para o caminho nativo, conforme o documento do spike da Tarefa 11.
- [ ] Suíte inteira: `uv run pytest -q` e `cd frontend && npx vitest run` → verdes.
- [ ] Replay manual das duas tarefas reais da auditoria (análise de upsell do Educly e investigação de contexto do Orin) no modo código: nenhuma mensagem `[Sistema do Modo Code] ... Rode verify_project`, nenhum evento `auto_todo_settle_*`, e o card mostra "não verificado" quando o modelo não verificou.
