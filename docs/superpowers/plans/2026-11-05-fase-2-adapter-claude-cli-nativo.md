# Fase 2 — Adapter próprio do Claude CLI com loop nativo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar o bridge `claude -p` atual (texto com `<orin_tool_call>`, um processo por iteração, histórico achatado, mapa 5.x→4.x, teto de 200k) por um adapter próprio em `src/agentos/providers/claude_cli/` cujo caminho principal entrega o turno inteiro ao loop nativo de ferramentas da CLI, com as ferramentas do Orin servidas por um MCP HTTP local que executa tudo pelo caminho de execução do runtime.

**Architecture:** O runtime (`AgenticTurnRuntime._run`) pergunta ao provider, antes do laço de iterações, se ele aceita o turno inteiro (`native_turn_available`). Se sim, um `NativeTurnRunner` (novo, `agentic/native_turn.py`) sobe um `TurnToolServer` (MCP em `127.0.0.1`, token por turno) cujo executor chama `AgenticTurnRuntime._run_toolset` — o mesmo caminho de permissões, hooks, journal de efeitos e eventos de atividade de hoje (D3) — e roda **um** `claude -p --max-turns N` por mensagem do usuário, traduzindo o stream-json para os eventos de atividade existentes. Se não (flag `off`, versão não validada, spike reprovado, falha antes de qualquer saída), o laço clássico continua e `ClaudeCliProvider.stream` fala o protocolo de texto corrigido e amigável ao cache (D5, D7), sem nada importado de `opencode.py`.

**Tech Stack:** Python 3.13, `uvicorn`/`starlette` (já dependências via `fastapi`/`uvicorn[standard]`), `subprocess` + threads (o runtime é síncrono), pytest (`uv run pytest`), React/Vitest para uma mudança de lógica (sem mudança visual) no envio de respostas do `ask_user`.

**Spec:** `docs/superpowers/specs/2026-11-05-claude-assinatura-nativa-e-orin-code-design.md` (Fase 2: R2.1–R2.9, decisões D1–D7). Auditoria: `docs/audits/2026-11-05-claude-assinatura-e-orin-code.md`.

## Global Constraints

- D1: nada em `src/agentos/providers/claude_cli/` nem em `src/agentos/providers/cli_tools_mcp/` importa `agentos.provider_catalog.opencode`. Um teste de import garante isso (Task 5).
- D4: comando sempre com `--restricted --strict-mcp-config --tools ""`; **nunca** `--bare` (não lê OAuth) e **nunca** `--safe-mode` (desliga MCP) — salvo se o spike reprovar o item 1, caso em que o caminho de texto mantém `--safe-mode` e o nativo não existe.
- D6: ids de modelo passam sem tradução. Aliases: `opus`→`claude-opus-5-5`, `sonnet`→`claude-sonnet-5-5`, `haiku`→`claude-haiku-5-5`, `fable`→`claude-fable-5-1`, resolvidos pelo catálogo `~/.claude/cache/model-catalog/*.json` quando disponível.
- D7: janela = a do catálogo nos **dois** caminhos (1M para Opus 5.5). `ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET` (padrão `200000`) só vale no fallback de texto e só se o item 7 do spike reprovar.
- Nomes fixos (a Fase 3 depende deles): pacote `src/agentos/providers/claude_cli/` com `command.py` (`build_claude_command(...) -> list[str]`), `catalog.py`, `stream.py`, `errors.py`, `native_loop.py`, `text_protocol.py`, `provider.py` (classe `ClaudeCliProvider`); pacote `src/agentos/providers/cli_tools_mcp/` com `TurnToolServer.start(...) -> TurnToolServerHandle` (`.url`, `.token`, `.mcp_config_json() -> str`, `async close()`).
- Variáveis: `ORIN_CLAUDE_CLI_NATIVE_TOOLS` = `auto` (padrão) | `off`; `ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET` (padrão `200000`).
- A classe de erro continua se chamando `ProviderTransportError` (subclasse de `ProviderKeyRejected`): `runtime.py:1134` reconhece o stall pelo **nome** da classe.
- O token do MCP nunca vai em argv: a config vai para um arquivo temporário (só o dono lê) e o caminho entra em `--mcp-config`. Logs omitem caminhos de config, tokens e o conteúdo do prompt.
- Windows primeiro: `taskkill /F /T /PID` para matar a árvore, `CREATE_NO_WINDOW`, caminhos com `\`.
- UI: nenhuma mudança visual; só eventos de atividade já existentes (`thinking_delta`, `generating_tool_call`, `tool_started`/`tool_finished`, `context_updated`, `waiting_user`, `queued_input`).
- Spike primeiro (R2.1): nenhuma linha de código de produção antes da Task 1 fechar o portão.

## Review Focus

1. **Cancelar no meio de uma ferramenta lenta ou de um `ask_user` aberto** — o processo `claude` e seus filhos morrem, a porta do MCP fecha, nenhuma chamada nova é aceita e o turno termina `cancelled` em poucos segundos (Tasks 8 e 10).
2. **Thinking longo sem nenhuma linha no stdout** — o timeout de inatividade não mata o turno antes do intervalo medido no spike; e não conta enquanto uma chamada MCP está em execução (permissão pendente, subagente, `ask_user`) (Task 8).
3. **Dez subagentes ao mesmo tempo** — sem limite fixo de processos da CLI, 10 subagentes rodam juntos; com um limite configurado, um processo pai que espera uma ferramenta não segura a vaga (Task 8).
4. **Conversa editada, rebobinada ou bifurcada depois de um turno nativo** — a sessão da CLI não é retomada com um histórico que o Orin não tem mais; começa uma sessão nova com o histórico atual (Task 11).
5. **Modelo antigo salvo na conversa (`claude-opus-4-7`) ou alias de subagente (`fable`)** — vai para a CLI exatamente como está / resolvido pelo catálogo, e o uso é gravado com o modelo que a CLI informou no `system/init` (Tasks 2 e 9).

---

## Mapa de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agentos/providers/claude_cli/__init__.py` | Reexporta `ClaudeCliProvider`, `ProviderTransportError`, `build_claude_command`. |
| `.../claude_cli/command.py` | argv, ambiente isolado, binário, versão da CLI, argv redigido para log. |
| `.../claude_cli/catalog.py` | Modelos do cache da CLI (sem tradução), janela por modelo, aliases de família, cliente de catálogo. |
| `.../claude_cli/stream.py` | Parser de linhas stream-json → `CliEvent`/`CliInit`/`CliResult`/`CliUsage`. |
| `.../claude_cli/errors.py` | `ProviderTransportError`, `RateLimitState`, classificação por `api_error_status`/`rateLimitType`. |
| `.../claude_cli/text_protocol.py` | Fallback de texto: prompt de sistema estável, transcript em blocos append-only, parser incremental de `<orin_tool_call>`. |
| `.../claude_cli/native_loop.py` | Sobe MCP + processo, lê o stream, follow-ups no mesmo processo, idle timeout, cancelamento, vagas. |
| `.../claude_cli/sessions.py` | (Task 11, ramo A) registro conversa → sessão da CLI. Módulo extra, fora da lista fixa. |
| `.../claude_cli/provider.py` | `ClaudeCliProvider` (texto via `stream`, nativo via `start_native_turn`), flag, portão de versão, log R2.8. |
| `src/agentos/providers/cli_tools_mcp/{__init__,server.py,types.py}` | MCP HTTP loopback genérico (`TurnToolServer`), sem import do runtime. |
| `src/agentos/agentic/native_turn.py` | `NativeTurnRunner` + `RuntimeToolExecutor`: ponte runtime ↔ loop nativo. |
| `src/agentos/agentic/runtime.py` | Costura em `_run` (antes da linha 785), `_prefix_caching`, "pensando…" com thinking vazio. |
| `src/agentos/agentic/provider_stream.py:881-897` | Despacho do bridge passa a `ClaudeCliProvider.stream`. |
| `src/agentos/provider_catalog/claude_code.py` | Fachada fina (reexports), sem lógica. |
| `src/agentos/agentic/agent_definitions.py:30-35` | `DEFAULT_MODEL_ALIASES` da família 5.x. |
| `src/agentos/agentic/quality.py:68` | `note_usage(..., cache_creation_input_tokens=None)`. |
| `src/agentos/workers/chat.py:932-941, 1104-1205, 1299-1320` | Janela, transporte (`ClaudeCliProvider`), aliases de subagente pelo catálogo. |
| `src/agentos/agentic/session.py:1484` | Estado `waiting_user_in_turn` (Task 10). |
| `frontend/src/features/conversations/ChatPage.tsx:892-930, 1283-1310` | Resposta de `ask_user` de turno vivo vai pela fila (Task 10). |

Testes novos em `tests/unit/providers/claude_cli/`, `tests/unit/providers/cli_tools_mcp/` e `tests/unit/agentic/test_native_turn*.py`. Testes reescritos: `tests/unit/provider_catalog/test_claude_code_context_and_subagents.py`, `test_claude_code_bridge.py`, `test_claude_code_bridge_resilience.py` (Task 5).

---

### Task 1: Spike com portão de decisão (R2.1)

**Files:**
- Create (não versionado): `%TEMP%\orin-claude-spike\` com `spike_mcp_server.py`, `stamp.py`, configs e saídas.
- Create: `docs/agent_memory/2026-11-05-claude-cli-native-spike.md`

**Interfaces:**
- Consumes: CLI 2.1.309 instalada e logada (`claude auth status` com `authMethod: claude.ai`).
- Produces (consumido por Tasks 2, 3, 8, 10, 11, 12): no documento, uma tabela "Item | Resultado | Evidência | Decisão" e estas constantes com valor: `ISOLATION_FLAGS`, `NATIVE_LOOP_VALIDATED` (bool), `IDLE_TIMEOUT_SECONDS`, `MCP_TOOL_TIMEOUT_ENV` (nome e efeito), `TOOL_USE_ID_META_KEY` (ou "ausente"), `PERSISTENT_STDIN_OK` (bool), `SESSION_BRANCH` (`A`|`B`), `SESSION_FILES_DIR`, `TEXT_CACHE_OK` (bool), `VALIDATED_CLI_RANGE`.

Regras do spike: ambiente isolado em toda chamada (remover `ANTHROPIC_*`, `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`; definir `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`), cwd = pasta vazia `%TEMP%\orin-claude-spike\cwd`, chamadas mínimas (Haiku 5.5 onde o modelo não importa, Opus 5.5 nos itens 5 e 7), `--no-session-persistence` exceto no item 6. `stamp.py` lê o stdout da CLI e grava cada linha prefixada com `time.monotonic()` em `out-<item>.jsonl`.

`spike_mcp_server.py` (stdlib `http.server`, ~120 linhas, porta 8765): POST `/mcp` com JSON-RPC (`initialize`, `notifications/initialized`→202, `tools/list`, `tools/call`, `ping`), GET→405, exige `Authorization: Bearer spike-token` (401 sem ele), registra em `server.log` cada requisição com horário, headers e `params` completos (inclui `_meta`). Ferramentas: `echo(text)`, `sleep_then_echo(seconds, text)`, `red_square()` (devolve `{"type":"image","mimeType":"image/png","data":<PNG 64×64 vermelho>}` e o texto "imagem anexada").

`spike-mcp.json`: `{"mcpServers":{"orin":{"type":"http","url":"http://127.0.0.1:8765/mcp","headers":{"Authorization":"Bearer spike-token"}}}}`

Base comum (chamada de `BASE` abaixo):
`claude -p --restricted --strict-mcp-config --tools "" --disable-slash-commands --no-chrome --permission-prompts none --input-format stream-json --output-format stream-json --verbose --include-partial-messages`
A mensagem vai pelo stdin em uma linha `{"type":"user","message":{"role":"user","content":"..."}}`.

- [ ] **Step 1: Item 1 — OAuth com `--restricted`.** Rodar `BASE --model claude-haiku-5-5 --max-turns 1 --no-session-persistence` com "Responda só: ok". Passa se `system/init.apiKeySource == "none"`, `result.is_error == false` e o texto é "ok". Registrar também se `--max-turns` (ausente do `--help`) é aceito sem aviso.
- [ ] **Step 2: Item 2 — MCP conectado.** Subir o servidor; `BASE --mcp-config spike-mcp.json --allowedTools "mcp__orin__*" --max-turns 4 --model claude-haiku-5-5 --no-session-persistence` com "Chame a ferramenta echo com o texto A e me diga o que voltou". Passa se `system/init.mcp_servers` contém `{"name":"orin","status":"connected"}`, `system/init.tools` contém `mcp__orin__echo` e nenhuma ferramenta embutida, e o `server.log` mostra `tools/call` com o header certo. Registrar: chave de `params._meta` com o id do `tool_use` (`TOOL_USE_ID_META_KEY`) ou "ausente"; o que acontece com token errado (status no init).
- [ ] **Step 3: Item 3 — parada em cada `tool_use`.** Mesmo comando, `--max-turns 6`, com "Chame echo com A. Quando receber o resultado, chame echo com o texto recebido seguido de B. Depois responda o último resultado." Passa se o `server.log` mostra a 2ª chamada **depois** da resposta da 1ª, com `text == "AB"`, e o stdout tem `assistant(tool_use)` → `user(tool_result)` → `assistant(tool_use)` → `user(tool_result)` → `assistant(text)`. Repetir com `--max-turns 1` e registrar o `result.subtype` (esperado `error_max_turns`). **3b:** `sleep_then_echo(150, "x")` sem e com `MCP_TOOL_TIMEOUT=3600000` no ambiente; registrar qual variável mantém a chamada viva (`MCP_TOOL_TIMEOUT_ENV`). **3c — paralelismo:** servidor com `slow_echo(seconds, text)` anotado `readOnlyHint: true` e uma cópia `slow_echo_rw` sem a anotação; prompt "Chame slow_echo 3 vezes em paralelo, com 5 s cada, textos A, B, C, numa única resposta". Registrar no `server.log` o instante de chegada de cada `tools/call`: chegaram juntas (< 1 s entre elas) = `CLI_PARALLEL_MCP = true`; anotar se a anotação `readOnlyHint` muda o comportamento (`CLI_PARALLEL_NEEDS_READONLY_HINT`). Reprovar 3c **não** fecha o portão: só desliga o agrupador da Task 9 (chamadas em série, com a limitação registrada no documento do spike).
- [ ] **Step 4: Item 4 — imagem no `tool_result`.** "Chame red_square e diga a cor da imagem." Passa se a resposta diz vermelho.
- [ ] **Step 5: Item 5 — thinking longo.** `--model claude-opus-5-5 --effort max --max-turns 1` com um problema que exige raciocínio longo (ex.: "Prove, passo a passo, quantos números primos menores que 10.000 terminam em 7; não use código"). Pelo `out-5.jsonl`, registrar: maior intervalo entre linhas, se há `thinking_delta` vazios ou eventos de ping, quanto tempo até o primeiro `text_delta`. `IDLE_TIMEOUT_SECONDS = max(180, 3 × maior intervalo)` arredondado para cima em múltiplos de 30.
- [ ] **Step 6: Item 6 — sessão.** (a) `uuid=$(python -c "import uuid;print(uuid.uuid4())")`; `BASE --model claude-haiku-5-5 --max-turns 1 --session-id $uuid` com "Guarde a palavra abacaxi e responda ok"; depois `BASE --model claude-haiku-5-5 --max-turns 1 --resume $uuid` com "Qual palavra?". Passa se responde "abacaxi". Localizar o arquivo (`Get-ChildItem $HOME\.claude -Recurse -Filter "$uuid*"`), anotar o diretório (`SESSION_FILES_DIR`) e se o slug é derivado do cwd (rodar de novo com outro cwd e comparar). Rodar `claude --resume` interativo numa pasta de repositório do usuário e anotar se a sessão aparece. (b) **stdin persistente:** um único processo (`--no-session-persistence`), mandar a 1ª mensagem, esperar o `result`, mandar a 2ª pelo mesmo stdin, esperar o 2º `result`; passa se a 2ª resposta lembra da 1ª (`PERSISTENT_STDIN_OK`). (c) Com `--resume`, passar outro `--system-prompt-file` e anotar qual prompt vale (snapshot).
- [ ] **Step 7: Item 7 — cache no protocolo de texto.** Gerar 12 blocos de texto de ~3.000 tokens cada (trechos de arquivos reais do repo). Chamada A: um processo, `--model claude-opus-5-5 --max-turns 1 --system-prompt-file sys.txt`, user content = os 12 blocos como `[{"type":"text","text":...}, ...]` + bloco final "Responda ok". Chamada B (novo processo, até 4 min depois): os mesmos 13 blocos + 1 bloco novo + "Responda ok". Registrar `usage` das duas (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`). Passa (`TEXT_CACHE_OK = true`) se `B.cache_read_input_tokens >= 0.9 × (A.input_tokens + A.cache_creation_input_tokens + A.cache_read_input_tokens)`. Controle: repetir A/B com tudo achatado num bloco só (esperado: sem leitura). **7b:** repetir B com `"cache_control":{"type":"ephemeral"}` no último bloco e anotar se a CLI aceita e se muda a leitura.
- [ ] **Step 8: Escrever `docs/agent_memory/2026-11-05-claude-cli-native-spike.md`** com a tabela, as constantes acima, os comandos exatos usados e trechos curtos de evidência (linhas de init/result, linhas do `server.log`). Decisão explícita no fim:
  - Itens 1, 2 e 3 passaram → `NATIVE_LOOP_VALIDATED = true`; seguir todas as tasks.
  - Algum de 1–3 falhou → `NATIVE_LOOP_VALIDATED = false`; **pular as Tasks 7, 8, 9, 10, 11 e 13**; a Task 12 só entrega a flag (sempre texto) e a Task 14 mede só o caminho de texto. Se o item 1 falhou, `ISOLATION_FLAGS = ("--safe-mode", "--strict-mcp-config")` e a Task 2 usa isso.
  - Item 4 falhou → nativo segue; a Task 9 marca resultados com imagem com o aviso "a imagem não pode ser exibida neste provedor; caminho: …" e registra a pendência.
  - Item 6 → `SESSION_BRANCH` A (passou e os arquivos ficam num slug que o usuário não abre) ou B; 6b decide follow-ups no mesmo processo.
  - Item 7 → `TEXT_CACHE_OK` decide o padrão do orçamento na Task 6.
  - `VALIDATED_CLI_RANGE = (">=2.1.309", "<2.2")`.
- [ ] **Step 9: Commit**

```bash
git add docs/agent_memory/2026-11-05-claude-cli-native-spike.md
git commit -m "docs(agent_memory): spike do loop nativo do claude -p (fase 2)"
```

---

### Task 2: `command.py` e `catalog.py` — comando isolado e modelos sem tradução (R2.2, R2.4, D4, D6)

**Files:**
- Create: `src/agentos/providers/claude_cli/__init__.py`, `command.py`, `catalog.py`
- Modify: `src/agentos/agentic/agent_definitions.py:30-35`
- Test: `tests/unit/providers/claude_cli/test_command.py`, `tests/unit/providers/claude_cli/test_catalog.py`, `tests/unit/agentic/test_agent_model_aliases.py`

**Interfaces:**
- Consumes: Task 1 (`ISOLATION_FLAGS`, `MCP_TOOL_TIMEOUT_ENV`).
- Produces:
  - `build_claude_command(*, binary: str, system_prompt_file: str | Path, model: str | None = None, effort: str | None = None, fallback_model: str | None = None, max_turns: int = 1, mcp_config_file: str | Path | None = None, mcp_server_name: str = "orin", session_id: str | None = None, resume_session_id: str | None = None, persist_session: bool = False, autocompact: str | None = None) -> list[str]`
  - `build_isolated_claude_env(base_env: Mapping[str, str] | None = None, *, max_output_tokens: int | None = None, mcp_tool_timeout_ms: int | None = None) -> dict[str, str]`
  - `resolve_claude_binary(env: Mapping[str, str] | None = None) -> str | None` (movido de `_resolve_claude_binary`)
  - `claude_cli_version(binary: str, *, timeout: float = 5.0) -> tuple[int, int, int] | None` (cache por binário, `claude --version`)
  - `redact_argv(argv: Sequence[str]) -> list[str]`
  - `check_claude_cli_status(...)` (movido sem mudança de comportamento)
  - `catalog.CatalogModel(id: str, display_name: str, context_window: int)`; `discover_cached_models(*, org_id: str | None = None, cache_dir: Path | None = None) -> list[CatalogModel]`; `context_window_for(model_id: str, models: Sequence[CatalogModel]) -> int | None`; `FAMILY_DEFAULTS: Mapping[str, str]`; `resolve_family_alias(alias: str, models: Sequence[CatalogModel] = ()) -> str | None`; `ClaudeCliModelCatalogClient` (o antigo `ClaudeCodeModelCatalogClient`, mesmo contrato `fetch`/`list_models`).

- [ ] **Step 1: Testes que falham**

```python
def test_command_uses_restricted_isolation_and_never_bare_or_safe_mode():
    argv = build_claude_command(binary="claude.exe", system_prompt_file="C:/t/s.txt", model="claude-opus-5-5")
    assert argv[:2] == ["claude.exe", "-p"]
    assert "--restricted" in argv and "--strict-mcp-config" in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert "--bare" not in argv and "--safe-mode" not in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"          # sem tradução
    assert "--no-session-persistence" in argv and argv[argv.index("--max-turns") + 1] == "1"

def test_command_native_adds_mcp_config_and_allowed_tools():
    argv = build_claude_command(binary="c", system_prompt_file="s", mcp_config_file="C:/t/m.json", max_turns=40)
    assert argv[argv.index("--mcp-config") + 1] == "C:/t/m.json"
    assert argv[argv.index("--allowedTools") + 1] == "mcp__orin__*"
    assert argv[argv.index("--max-turns") + 1] == "40"

def test_command_session_flags_are_exclusive():
    assert "--session-id" in build_claude_command(binary="c", system_prompt_file="s", session_id=UUID, persist_session=True)
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", session_id=UUID, resume_session_id=UUID)
    with pytest.raises(ValueError):
        build_claude_command(binary="c", system_prompt_file="s", session_id="not-a-uuid", persist_session=True)

@pytest.mark.parametrize("bad", ["-x", "a b", "x\x00", "x" * 200])
def test_command_rejects_unsafe_model(bad): ...

def test_env_strips_api_keys_and_sets_output_and_mcp_timeouts():
    env = build_isolated_claude_env({"ANTHROPIC_API_KEY": "k", "CLAUDECODE": "1", "PATH": "p"}, max_output_tokens=8000, mcp_tool_timeout_ms=600000)
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env and env["PATH"] == "p"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
    assert env[MCP_TOOL_TIMEOUT_ENV] == "600000"

def test_redact_argv_hides_mcp_config_and_system_prompt_paths():
    out = redact_argv(["c", "-p", "--mcp-config", "C:/t/m.json", "--system-prompt-file", "C:/t/s.txt", "--model", "m"])
    assert out == ["c", "-p", "--mcp-config", "<redacted>", "--system-prompt-file", "<redacted>", "--model", "m"]

def test_catalog_keeps_5x_ids_and_reads_window(tmp_path):  # arquivo no formato do cache: catalog.config.models[{id,name,runtime.max_input_tokens}]
    models = discover_cached_models(cache_dir=tmp_path)
    assert [m.id for m in models][:1] == ["claude-opus-5-5"]
    assert context_window_for("claude-opus-5-5", models) == 1_000_000

def test_resolve_family_alias_prefers_newest_catalog_member_then_default():
    models = [CatalogModel("claude-opus-5-5", "Opus 5.5", 1_000_000), CatalogModel("claude-opus-5-6", "Opus 5.6", 1_000_000)]
    assert resolve_family_alias("opus", models) == "claude-opus-5-6"
    assert resolve_family_alias("fable", []) == "claude-fable-5-1"
    assert resolve_family_alias("gpt", []) is None

def test_default_aliases_are_the_5x_family():
    assert resolve_agent_model("opus", {}) == "claude-opus-5-5"
    assert resolve_agent_model("sonnet", {}) == "claude-sonnet-5-5"
    assert resolve_agent_model("haiku", {}) == "claude-haiku-5-5"
    assert resolve_agent_model("fable", None) == "claude-fable-5-1"
    assert dict(DEFAULT_MODEL_ALIASES) == dict(FAMILY_DEFAULTS)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/providers/claude_cli/test_command.py tests/unit/providers/claude_cli/test_catalog.py tests/unit/agentic/test_agent_model_aliases.py -v`
Expected: FAIL (`ModuleNotFoundError: agentos.providers.claude_cli`).

- [ ] **Step 3: Implementar `command.py`**

Flags fixas na ordem: `-p`, `*ISOLATION_FLAGS` (padrão `("--restricted", "--strict-mcp-config")`), `--tools ""`, `--disable-slash-commands`, `--no-chrome`, `--permission-prompts none`, `--max-turns N`, `--system-prompt-file F`, `--input-format stream-json`, `--output-format stream-json`, `--verbose`, `--include-partial-messages`; `--no-session-persistence` quando `persist_session` é falso e não há `resume_session_id`; `--mcp-config F --allowedTools mcp__<server>__*` quando há config; `--autocompact` aceita `auto` ou inteiro 100000–1000000. Validações de modelo/effort/fallback reaproveitam as regex de `claude_code.py:56-58`. `MCP_TOOL_TIMEOUT_ENV` e `MCP_TIMEOUT=30000` só entram quando `mcp_tool_timeout_ms` é dado.

- [ ] **Step 4: Implementar `catalog.py`** movendo `_discover_cached_claude_cli_models`, `_fetch_published_claude_cli_models` e `ClaudeCodeModelCatalogClient` (`claude_code.py:421-637`) com estas mudanças: sem `_LEGACY_CLAUDE_MODEL_MAP`; lista estática de último recurso = `FAMILY_DEFAULTS.values()` com janela 200.000, exceto `claude-opus-5-5` = 1.000.000 (D7); `resolve_family_alias` escolhe, entre ids `claude-<família>-<maior>-<menor>` do catálogo, o de maior versão. Em `agent_definitions.py:30-35`, `DEFAULT_MODEL_ALIASES` vira a cópia literal de `FAMILY_DEFAULTS` (sem import: evita ciclo `agentic` → `providers`).

- [ ] **Step 5: Rodar e ver passar** (mesmo comando do Step 2). Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/providers/claude_cli tests/unit/providers/claude_cli src/agentos/agentic/agent_definitions.py tests/unit/agentic/test_agent_model_aliases.py
git commit -m "feat(claude_cli): comando isolado com --restricted e modelos 5.x sem tradução"
```

---

### Task 3: `stream.py` e `errors.py` — parser do stream-json e classificação de erros (R2.6, R2.7)

**Files:**
- Create: `src/agentos/providers/claude_cli/stream.py`, `src/agentos/providers/claude_cli/errors.py`
- Test: `tests/unit/providers/claude_cli/test_stream.py`, `tests/unit/providers/claude_cli/test_errors.py`

**Interfaces:**
- Produces (`stream.py`):
  - `CliUsage(input_tokens: int = 0, output_tokens: int = 0, cache_read_input_tokens: int = 0, cache_creation_input_tokens: int = 0)` com `@property context_tokens -> int` (= input + cache_read + cache_creation) e `to_stream_usage() -> StreamUsage`.
  - `CliInit(session_id: str, model: str, api_key_source: str, mcp_servers: Mapping[str, str], tools: tuple[str, ...])`
  - `CliResult(subtype: str, is_error: bool, result_text: str, stop_reason: str | None, num_turns: int, duration_ms: int, session_id: str, api_error_status: int | None, usage: CliUsage, model_usage: Mapping[str, CliUsage])`
  - `CliEventKind` (`StrEnum`): `INIT, TEXT_DELTA, THINKING_DELTA, THINKING_SIGNATURE, TOOL_USE_START, TOOL_INPUT_DELTA, TOOL_USE_END, ASSISTANT_MESSAGE, TOOL_RESULT, USAGE, RATE_LIMIT, COMPACT_BOUNDARY, RESULT, ERROR`
  - `CliEvent(kind: CliEventKind, text: str = "", index: int | None = None, tool_use_id: str | None = None, tool_name: str | None = None, tool_input: Mapping[str, Any] | None = None, message_id: str | None = None, usage: CliUsage | None = None, raw: Mapping[str, Any] = {})`
  - `class StreamJsonParser:` `feed_line(line: str) -> list[CliEvent]`; atributos `init: CliInit | None`, `result: CliResult | None`, `rate_limit: RateLimitState | None`.
- Produces (`errors.py`):
  - `class ProviderTransportError(ProviderKeyRejected)` (nome mantido)
  - `RateLimitState(status: str, rate_limit_type: str | None, resets_at: float | None)`; `RateLimitState.from_info(info: Mapping) -> RateLimitState`
  - `format_reset_local(resets_at: float, *, tz: tzinfo | None = None) -> str` → `"%d/%m às %H:%M"` no fuso local
  - `classify_result_error(result: CliResult, rate_limit: RateLimitState | None, *, now: float, tz: tzinfo | None = None) -> ProviderTransportError`
  - `class NativeLoopUnavailable(ProviderTransportError)` (usado na Task 8)

- [ ] **Step 1: Testes que falham** — fixtures em `tests/unit/providers/claude_cli/fixtures/*.jsonl` com linhas reais copiadas do spike (`out-2.jsonl`, `out-3.jsonl`, `out-5.jsonl`).

```python
def test_parser_emits_init_with_mcp_status(): ...  # init.mcp_servers == {"orin": "connected"}, init.model == "claude-haiku-5-5"
def test_parser_emits_tool_use_start_and_input_deltas_before_stop():
    kinds = [e.kind for line in FIX3 for e in parser.feed_line(line)]
    assert kinds.index(CliEventKind.TOOL_USE_START) < kinds.index(CliEventKind.TOOL_INPUT_DELTA) < kinds.index(CliEventKind.TOOL_USE_END)
def test_parser_keeps_empty_thinking_deltas():  # thinking_delta com "" vira THINKING_DELTA(text="") — R2.7
def test_parser_reports_usage_once_per_message_id(): ...  # dois chunks do mesmo message_id → um USAGE
def test_parser_tool_result_user_message_becomes_tool_result_event(): ...
def test_parser_ignores_garbage_and_non_object_lines():
    assert parser.feed_line("not json") == [] and parser.feed_line("[1]") == []
def test_result_carries_api_error_status_and_model_usage(): ...  # result.api_error_status == 404; "claude-opus-5-5" in result.model_usage

def test_404_is_not_retryable_and_triggers_fallback():
    err = classify_result_error(result(api_error_status=404, text="model not found"), None, now=T)
    assert err.status_code == 404 and not is_retryable(err)
def test_rate_limit_shows_local_reset_and_retry_after():
    rl = RateLimitState("rejected", "five_hour", T + 3600)
    err = classify_result_error(result(api_error_status=429, text="limit"), rl, now=T, tz=ZoneInfo("America/Sao_Paulo"))
    assert err.status_code == 429 and err.retry_after_seconds == pytest.approx(3600)
    assert "five_hour" in str(err) and format_reset_local(T + 3600, tz=ZoneInfo("America/Sao_Paulo")) in str(err)
def test_auth_errors_exhaust_the_key(): ...      # 401/403 → exhausted=True
def test_overloaded_is_retryable(): ...          # 529 → status 529
def test_prompt_too_long_is_context_length(): ... # 400 + "prompt is too long" → AgenticTurnRuntime._is_context_length_error(err) is True
def test_error_class_name_is_kept():
    assert type(classify_result_error(result(api_error_status=500), None, now=T)).__name__ == "ProviderTransportError"
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/providers/claude_cli/test_stream.py tests/unit/providers/claude_cli/test_errors.py -v` → FAIL (import).
- [ ] **Step 3: Implementar `stream.py`** portando a leitura de pacotes de `claude_code.py:1117-1370` sem nenhuma lógica de `<orin_tool_call>` (isso fica em `text_protocol.py`). `api_error_status` sem valor e `subtype` começando com `error` sem status → `None`. `rate_limit_event.rate_limit_info` alimenta `rate_limit`. `system` com `subtype == "compact_boundary"` → `COMPACT_BOUNDARY`.
- [ ] **Step 4: Implementar `errors.py`**: status → código: 404→404; 401/403→401 com `exhausted=True`; 429 ou `rate_limit.status == "rejected"`→429 com `retry_after_seconds = resets_at - now` (mín. 1); 529→529; 400 com texto de contexto longo → 400 mantendo o texto; demais/None → 500. Mensagem: `"Limite de uso da assinatura ({rateLimitType}) atingido; libera em {format_reset_local(...)}"` quando há reset.
- [ ] **Step 5: Rodar e ver passar.** Expected: PASS.
- [ ] **Step 6: Commit** — `git commit -m "feat(claude_cli): parser do stream-json e classificação por api_error_status"`

---

### Task 4: `text_protocol.py` — fallback corrigido e amigável ao cache (D5, D7)

**Files:**
- Create: `src/agentos/providers/claude_cli/text_protocol.py`
- Test: `tests/unit/providers/claude_cli/test_text_protocol.py`

**Interfaces:**
- Consumes: nada de `opencode.py` (reescrever; os helpers de lá são a referência de comportamento, não dependência).
- Produces:
  - `TOOL_CALL_OPEN = "<orin_tool_call"`, `TOOL_CALL_CLOSE = "</orin_tool_call>"`
  - `escape_tool_content(text: str) -> str` (troca `<orin_tool_call`→`&lt;orin_tool_call`, `</orin_tool_call`→`&lt;/orin_tool_call`, `[Tool Result`→`&#91;Tool Result`, `[/Tool Result]`→`&#91;/Tool Result]`)
  - `build_system_prompt(stable_system: str, declarations: Sequence[Mapping[str, Any]]) -> str`
  - `class TranscriptState:` por turno; `blocks_for(window: Sequence[Mapping[str, Any]], *, allowed_now: set[str] | None, closing: bool) -> list[dict[str, Any]]`; `last_rebuild_reason: str | None`
  - `class ToolCallStreamParser:` `__init__(allowed_names: set[str])`; `feed(chunk: str) -> list[TextProtocolEvent]`; `finish() -> list[TextProtocolEvent]`; `stopped: bool`
  - `TextProtocolEvent(kind: Literal["text", "tool_call_started", "tool_call", "stop"], text: str = "", call_id: str = "", name: str = "", arguments: str = "")`

Decisões:
- Prompt de sistema = 1ª mensagem `system` da janela + declarações da **união** de todas as ferramentas do turno (estável entre iterações). O conjunto disponível agora (fase, `tool_choice="none"`) vai como texto no último bloco; o parser recusa nomes fora de `allowed_now`. Regra 4 do prompt antigo (`opencode.py:431`) vira: "Emita só as chamadas independentes deste passo, todas juntas, e pare. Nunca escreva `[Tool Result`."
- Transcript = lista de blocos do conteúdo **de uma** mensagem stream-json: um bloco `text` por mensagem do histórico (`[User]`, `[Assistant]` com chamadas `<orin_tool_call id="…">{…}</orin_tool_call>`), **todos os resultados consecutivos de uma rodada num bloco só** (`[Tool Result id=… name=… status=…]\n<escapado>\n[/Tool Result]`), imagens como blocos `image` logo depois. Mensagens `system` além da primeira (volátil, nota de fase, `CLOSING_INSTRUCTION`) viram um bloco `[System]` no ponto em que apareceram pela primeira vez.
- Append-only: `TranscriptState` guarda os blocos enviados e o digest de cada mensagem; se o histórico novo começa com as mesmas mensagens, devolve `enviados + novos`; senão reconstrói e grava `last_rebuild_reason` (`"history_rewritten"`). Cada iteração acrescenta no máximo ~4 blocos de texto (lookback de 20 blocos do cache).
- Parser: emite `text` em streaming segurando só um prefixo possível de tag; `tool_call_started` ao abrir a tag (progresso R2.7); `tool_call` assim que a tag fecha (não mais no fim do processo); ao ver `[Tool Result` no texto do modelo, descarta o resto, emite `stop` e `stopped = True` (o chamador mata o processo). Ids: o do atributo `id` quando presente e único, senão `call_<12 hex>`.

- [ ] **Step 1: Testes que falham**

```python
def test_second_iteration_blocks_start_with_first_iteration_blocks():
    st = TranscriptState()
    b1 = st.blocks_for(window_iter1, allowed_now=None, closing=False)
    b2 = st.blocks_for(window_iter2, allowed_now=None, closing=False)   # iter1 + assistant(tool calls) + 3 resultados
    assert b2[:len(b1)] == b1 and len(b2) - len(b1) <= 4 and st.last_rebuild_reason is None
def test_rewritten_history_rebuilds_and_says_why(): ...  # mensagem antiga alterada → last_rebuild_reason == "history_rewritten"
def test_system_prompt_is_identical_across_phases_and_closing():
    assert build_system_prompt(s, all_tools) == build_system_prompt(s, all_tools)   # declarações = união
    assert "Ferramentas disponíveis agora: read_file" in st.blocks_for(w, allowed_now={"read_file"}, closing=False)[-1]["text"]
def test_history_calls_carry_ids_and_results_are_escaped():
    text = "".join(b["text"] for b in blocks if b["type"] == "text")
    assert '<orin_tool_call id="call_1">' in text
    assert "&lt;orin_tool_call" in text and "&#91;Tool Result" in text   # conteúdo malicioso dentro de um resultado
def test_parser_emits_call_as_soon_as_tag_closes():
    events = p.feed('vou ler <orin_tool_call>{"name":"read_file","arguments":{"path":"a"}}</orin_tool_call> e')
    assert [e.kind for e in events] == ["text", "tool_call_started", "tool_call", "text"]
def test_parser_stops_at_first_hallucinated_result_and_drops_later_calls():
    events = p.feed('<orin_tool_call>{"name":"read_file","arguments":{"path":"a"}}</orin_tool_call>\n[Tool Result id=x]ok[/Tool Result]<orin_tool_call>{"name":"edit_file","arguments":{}}</orin_tool_call>')
    assert [e.name for e in events if e.kind == "tool_call"] == ["read_file"] and events[-1].kind == "stop" and p.stopped
def test_parser_refuses_tools_not_allowed_now(): ...
def test_parser_split_tag_across_chunks(): ...  # "<orin_to" + "ol_call>{...}</orin_tool_call>"
def test_text_protocol_does_not_import_opencode():
    import agentos.providers.claude_cli.text_protocol as m
    assert "opencode" not in Path(m.__file__).read_text(encoding="utf-8")
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/providers/claude_cli/test_text_protocol.py -v` → FAIL.
- [ ] **Step 3: Implementar** conforme as decisões acima.
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `git commit -m "feat(claude_cli): protocolo de texto corrigido, em blocos append-only"`

---

### Task 5: `ClaudeCliProvider` (caminho de texto), fachada, despacho e observabilidade (R2.2, R2.7, R2.8)

**Files:**
- Create: `src/agentos/providers/claude_cli/provider.py`
- Modify: `src/agentos/provider_catalog/claude_code.py` (vira fachada), `src/agentos/agentic/provider_stream.py:260,881-897`, `src/agentos/provider_catalog/http.py:8-11,70-71`, `src/agentos/agentic/runtime.py:1005-1024` (thinking vazio)
- Test: `tests/unit/providers/claude_cli/test_provider_text.py`, `tests/unit/providers/claude_cli/test_no_opencode_import.py`, `tests/unit/agentic/test_runtime_thinking_progress.py`
- Rewrite: `tests/unit/provider_catalog/test_claude_code_bridge.py`, `test_claude_code_bridge_resilience.py`, `test_claude_code_context_and_subagents.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `class ClaudeCliProvider:` `provider = "anthropic"`; `__init__(self, *, base_url: str, model: str, binary: str | None = None, popen_factory: Callable[..., Any] | None = None, env: Mapping[str, str] | None = None, clock: Callable[[], float] = time.monotonic)`; atributos `base_url`, `model`; `stream(self, request: Mapping[str, object], *, cancel_check: Callable[[], bool] | None = None) -> Iterator[NormalizedStreamItem]`; `close(self) -> None`. (Os métodos nativos entram na Task 8/12.)
  - `CLI_SLOTS: CliSlots` (substitui o `_CLAUDE_CLI_SEMAPHORE` de 3 vagas). **Sem limite fixo** (decisão do Carlos): `CliSlots.from_env()` lê `ORIN_CLI_MAX_PROCESSES`; ausente, vazio ou `0` = sem limite (`acquire` nunca espera); inteiro ≥ 1 = limite. O paralelismo real fica limitado pelo teto de subagentes que já existe (`ORIN_MAX_CONCURRENT_SUBAGENTS`, padrão 16, `runtime.py:47`). Interface: `acquire(cancel_check) -> bool`, `release() -> None`, `limit: int | None`. A Fase 3 troca por fila justa com proteção de memória.
  - `kill_process_tree(proc: Any) -> None` (movido)
  - `log_cli_run(*, argv: Sequence[str], requested_model: str, init: CliInit | None, result: CliResult | None, exit_code: int | None, duration_s: float, stderr_tail: str, mode: Literal["text", "native"]) -> None` no logger `agentos.providers.claude_cli`
  - Fachada `claude_code.py`: reexporta `DEFAULT_CLAUDE_CLI_BRIDGE_URL`, `CLAUDE_CODE_LOCAL_BASE_URL`, `ANTHROPIC_CLOUD_BASE_URL`, `DEFAULT_ANTHROPIC_BASE_URL`, `ProviderTransportError`, `StreamUsage`, `normalize_anthropic_base_url`, `is_claude_cli_bridge_url`, `is_anthropic_cloud`, `build_isolated_claude_env`, `check_claude_cli_status`, `ClaudeCodeModelCatalogClient = ClaudeCliModelCatalogClient`, e `stream_claude_code_bridge(request=None, **kwargs)` = `ClaudeCliProvider(...).stream(...)`. As funções de URL ficam em `provider.py` (são do adapter).

Decisões:
- `stream` usa um `TranscriptState` **por instância** (o provider é criado por turno em `workers/chat.py`), escreve o prompt de sistema num arquivo temporário só quando o texto muda (mesmo caminho entre iterações), manda `blocks_for(...)` como `content` de uma mensagem stream-json e fecha o stdin.
- `max_output_tokens` do request vira `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (R2.6). `effort` passa como hoje.
- Thinking com texto vazio vira `NormalizedStreamItem(StreamKind.THINKING, seq, thinking="")`, no máximo um a cada 2 s. No runtime (`runtime.py:1005-1017`), um THINKING vazio chama `self._life(turn, "thinking_delta", delta="", iteration=iteration)` com o mesmo limite de 2 s (o session já grava isso como "Pensando", `session.py:1259-1277`).
- `tool_call_started` vira `StreamKind.TOOL_CALL` com `tool_name` e `arguments_delta=""` (o runtime já emite `generating_tool_call`, `runtime.py:1069-1080`); `tool_call` vira `TOOL_CALL` com os argumentos; `stop` mata o processo e termina com `FinishReason.TOOL_CALLS` se houve chamadas.
- `system/init.apiKeySource != "none"` continua abortando (`claude_code.py:1119-1127`).
- R2.8: `log_cli_run` uma vez por processo, nível INFO, formato `claude_cli run mode=%s argv=%s requested=%s actual=%s exit=%s duration=%.1fs in=%d out=%d cache_read=%d cache_creation=%d stderr_tail=%r`, argv por `redact_argv`, `stderr_tail` = últimas 20 linhas limitadas a 2.000 caracteres.

- [ ] **Step 1: Testes que falham** (fake `Popen` em `tests/unit/providers/claude_cli/fakes.py`: grava argv/env/stdin, devolve linhas de fixture)

```python
def test_text_stream_sends_multiblock_content_and_untranslated_model():
    items = list(ClaudeCliProvider(base_url=LOCAL, model="claude-opus-5-5", popen_factory=fake).stream(req))
    env = json.loads(fake.stdin[0]); assert isinstance(env["message"]["content"], list) and len(env["message"]["content"]) > 1
    assert fake.argv[fake.argv.index("--model") + 1] == "claude-opus-5-5"
def test_text_stream_emits_tool_call_before_process_ends(): ...  # o TOOL_CALL sai antes do item USAGE/FINISH e antes da última linha do fake
def test_text_stream_kills_process_on_hallucinated_tool_result(): ...  # fake.killed is True; só a 1ª chamada sai
def test_text_stream_second_call_reuses_prefix_blocks(): ...  # 2ª chamada: content[:n1] == content da 1ª
def test_max_output_tokens_becomes_env(): ...   # fake.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
def test_empty_thinking_yields_throttled_progress(): ...
def test_cli_run_is_logged_without_secrets(caplog):
    assert "claude_cli run mode=text" in caplog.text and "<redacted>" in caplog.text and "actual=claude-opus-5-5" in caplog.text
def test_http_transport_dispatches_bridge_to_claude_cli_provider(monkeypatch): ...  # substitui test em test_claude_code_context_and_subagents.py:174-190
def test_no_module_in_claude_cli_or_cli_tools_mcp_imports_opencode():
    for path in Path("src/agentos/providers").rglob("*.py"):
        if "claude_cli" in path.parts or "cli_tools_mcp" in path.parts:
            assert "provider_catalog.opencode" not in path.read_text(encoding="utf-8")
def test_runtime_records_thinking_progress_for_empty_deltas(): ...  # store fake: lifecycle("thinking_delta", delta="") chamado 1x em 2 s simulados
```

Reescrita dos testes antigos (mesmo arquivo, mesmo nome quando o comportamento continua; troca quando inverte):
- `test_claude_code_context_and_subagents.py:91-164` → `test_stream_sends_multimodal_blocks_without_model_translation` (asserta `"claude-opus-5-5" in argv` e ausência de `"claude-opus-4-7"`; imagem como bloco do content).
- `:167-171` → aliases 5.x (ou removido, coberto pela Task 2).
- `:27-88` → testes equivalentes contra `TranscriptState`, sem import de `opencode`.
- `:329-393` → vai para a Task 6 (janela).
- `test_claude_code_bridge*.py`: casos de `<orin_tool_call>` migram para `test_text_protocol.py`; casos de processo (timeout, cancel, `taskkill`, deadlock de stdin, semáforo, `apiKeySource`) passam a importar de `agentos.providers.claude_cli` e continuam valendo.

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/providers/claude_cli tests/unit/agentic/test_runtime_thinking_progress.py -v` → FAIL.
- [ ] **Step 3: Implementar `provider.py`, fachada, despacho** (`provider_stream.py:881-897` instancia `ClaudeCliProvider(base_url=self.base_url, model=self.model)` uma vez por transport e chama `.stream`; o hook de teste `stream_claude_code_bridge` em `provider_stream.py:260` continua) e o ajuste do runtime.
- [ ] **Step 4: Rodar a suíte do bridge inteira**

Run: `uv run pytest tests/unit/providers tests/unit/provider_catalog tests/unit/agentic -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(claude_cli): ClaudeCliProvider no caminho de texto; claude_code.py vira fachada"`

---

### Task 6: Janela, cache e compactação (R2.5, D7)

**Files:**
- Modify: `src/agentos/workers/chat.py:932-941` (`_context_window_for`), `src/agentos/agentic/runtime.py:1549-1564` (`_prefix_caching`), `src/agentos/agentic/quality.py:45,68-102`, `src/agentos/agentic/runtime.py:1094-1098`
- Modify: `src/agentos/providers/claude_cli/provider.py` (`text_context_budget()`)
- Test: `tests/unit/providers/claude_cli/test_window.py`; reescrever `tests/unit/provider_catalog/test_claude_code_context_and_subagents.py:193-227, 329-393`

**Interfaces:**
- Consumes: Task 1 (`TEXT_CACHE_OK`), Task 2 (`context_window_for`), Task 12 (`native_tools_enabled`) — esta task cria em `provider.py` o stub com a assinatura final da Task 12 (`native_tools_enabled(*, conversation_mode, code_mode, env=None, version=None) -> bool`) devolvendo `False`; a Task 12 preenche o corpo.
- Produces:
  - `TEXT_CACHE_OK: bool` (constante em `provider.py`, valor do spike)
  - `text_context_budget(env: Mapping[str, str] | None = None) -> int | None` — `None` (sem teto) quando `TEXT_CACHE_OK`; senão `int(env.get("ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET", "200000"))`, mínimo 50.000. Valor explícito na variável vale nos dois casos.
  - `TurnQualityCounters.note_usage(..., cache_creation_input_tokens: int | None = None)` e `cache_creation_input_tokens` no snapshot.

Decisões:
- `_context_window_for`: sai o corte `min(window, 200_000)` e a exceção `[1m]`. Para o bridge: janela = catálogo; se o turno **não** for nativo e `text_context_budget()` não for `None`, `min(janela, orçamento)`.
- `_prefix_caching`: para o bridge devolve `True` quando o turno é nativo ou `TEXT_CACHE_OK` (sem aging por iteração, `runtime.py:1310-1311`; a compactação do Orin só dispara perto do limite via `_autocompact_threshold`); devolve `False` só no fallback de texto com `TEXT_CACHE_OK = False` (comportamento antigo).
- Cada chamada de provider anota `cache_read`/`cache_creation` nos contadores (o `StreamUsage` já traz os dois, `claude_code.py:135-139`) e o `log_cli_run` registra por processo.

- [ ] **Step 1: Testes que falham**

```python
def test_window_is_catalog_window_for_cli_bridge(monkeypatch):     # substitui :383-391
    assert worker._context_window_for({"provider": "anthropic", "model_id": "claude-opus-5-5", ...}) == 1_000_000
def test_text_budget_applies_only_when_cache_not_ok(monkeypatch):
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", False)
    assert text_context_budget({}) == 200_000 and text_context_budget({"ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET": "300000"}) == 300_000
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", True)
    assert text_context_budget({}) is None
def test_prefix_caching_true_on_cli_bridge_when_cache_ok(monkeypatch): ...   # substitui :193-227 (antes asseria False)
def test_no_aging_between_iterations_when_prefix_caching(): ...  # runtime fake de 3 iterações: conteúdo do 1º tool result intacto na 3ª requisição
def test_counters_keep_cache_creation(): ...
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/providers/claude_cli/test_window.py tests/unit/provider_catalog/test_claude_code_context_and_subagents.py -v` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar e ver passar** + `uv run pytest tests/unit/agentic -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(claude_cli): janela do catálogo e fallback de texto sem aging enquanto cabe"`

---

### Task 7: `cli_tools_mcp` — servidor MCP HTTP loopback por turno (D2, D3) — *só se o portão passou*

**Files:**
- Create: `src/agentos/providers/cli_tools_mcp/__init__.py`, `types.py`, `server.py`
- Test: `tests/unit/providers/cli_tools_mcp/test_turn_tool_server.py`

**Interfaces:**
- Consumes: `agentos.mcp.protocol.PROTOCOL_VERSION` (`"2025-06-18"`). O formato de `tools/list` segue `cli_code/commands/mcp_serve.py:31-37` (nome público, descrição ≤ 2.000, `inputSchema`). Não reaproveitar `mcp_serve.handle`: ele chama `toolset.invoke` direto e pularia o D3.
- Produces (`types.py`):
  - `McpToolSpec(name: str, description: str, input_schema: Mapping[str, Any], read_only: bool = False)`
  - `ToolCallRequest(name: str, arguments: dict[str, Any], meta: Mapping[str, Any])`
  - `ToolCallResult(text: str, is_error: bool = False, images: tuple[Mapping[str, str], ...] = ())` — imagem = `{"data": <b64>, "mime_type": "image/png"}`
  - `class ToolExecutor(Protocol)`: `list_tools(self) -> Sequence[McpToolSpec]`; `call_tool(self, request: ToolCallRequest) -> ToolCallResult` (síncrono, bloqueante)
- Produces (`server.py`):
  - `class TurnToolServer:` `__init__(self, executor: ToolExecutor, *, server_name: str = "orin", host: str = "127.0.0.1")`; `async start(self, *, port: int = 0) -> TurnToolServerHandle`
  - `class TurnToolServerHandle:` `url: str` (`http://127.0.0.1:<porta>/mcp`), `token: str` (`secrets.token_urlsafe(32)`), `server_name: str`, `mcp_config_json(self) -> str` (`{"mcpServers":{"<name>":{"type":"http","url":…,"headers":{"Authorization":"Bearer …"}}}}`), `in_flight: int` (propriedade), `refuse_calls(self, reason: str) -> None`, `async close(self) -> None` (idempotente)
  - `class BackgroundTurnToolServer:` para chamadores síncronos — `start(server: TurnToolServer) -> BackgroundTurnToolServer` (classmethod; loop asyncio numa thread daemon), `.handle`, `.close(timeout: float = 5.0) -> None`

Decisões:
- Starlette + `uvicorn.Server(Config(app, log_level="warning", lifespan="off"))` servindo um socket já ligado em `(host, 0)` (`server.serve(sockets=[sock])`) para saber a porta antes do primeiro request.
- Segurança: `Authorization` comparado com `hmac.compare_digest` (401); `Host` tem de ser `127.0.0.1:<porta>` ou `localhost:<porta>` (421); `Origin` presente e não-loopback → 403; corpo > 4 MiB → 413.
- Protocolo: POST `/mcp` com objeto ou lote JSON-RPC; `initialize` devolve a versão pedida se for `2025-06-18` ou `2025-03-26`, senão `PROTOCOL_VERSION`, e um `Mcp-Session-Id`; notificações → 202; `tools/list` com `annotations.readOnlyHint`; `tools/call` roda `executor.call_tool` em `asyncio.to_thread` e devolve `{"content":[{"type":"text","text":…}, {"type":"image","data":…,"mimeType":…}], "isError": …}`; nome desconhecido → resultado `isError` (não erro JSON-RPC, para o modelo ler); exceção do executor → `isError` com `"{tipo}: {mensagem}"[:2000]`; depois de `refuse_calls`/`close` → `isError` com o motivo. GET → 405; DELETE → 200.

- [ ] **Step 1: Testes que falham** (cliente `httpx` real contra o servidor em background)

```python
def test_initialize_list_and_call_round_trip(): ...   # tools/list traz "Read" com inputSchema; tools/call devolve o texto do executor fake
def test_rejects_missing_or_wrong_token(): ...        # 401
def test_rejects_foreign_origin_and_host(): ...       # 403 / 421
def test_image_result_becomes_mcp_image_block(): ...  # content[1] == {"type":"image","data":"iVBOR","mimeType":"image/png"}
def test_executor_exception_is_tool_error_not_jsonrpc_error(): ...
def test_in_flight_counts_running_calls(): ...        # executor que espera um Event: in_flight == 1 durante, 0 depois
def test_close_frees_port_and_refuses_new_calls():
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake)); url = bg.handle.url; bg.close()
    with pytest.raises(httpx.ConnectError): httpx.post(url, json=INIT, headers=AUTH, timeout=1)
def test_mcp_config_json_is_valid_and_names_server(): ...
def test_server_package_does_not_import_runtime():
    assert "agentos.agentic" not in Path(server.__file__).read_text(encoding="utf-8")
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/providers/cli_tools_mcp -v` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `git commit -m "feat(cli_tools_mcp): servidor MCP HTTP loopback com token por turno"`

---

### Task 8: `native_loop.py` — processo, stream, follow-ups, idle timeout, cancelamento e vagas (R2.3, R2.6, R2.7) — *só se o portão passou*

**Files:**
- Create: `src/agentos/providers/claude_cli/native_loop.py`
- Modify: `src/agentos/providers/claude_cli/provider.py` (`start_native_turn`)
- Test: `tests/unit/providers/claude_cli/test_native_loop.py` (fake CLI = script Python em `tests/unit/providers/claude_cli/fake_claude.py` que fala stream-json, lê `--mcp-config`, chama o MCP real via `httpx` e imprime linhas como a CLI do spike)

**Interfaces:**
- Consumes: Tasks 2, 3, 5, 7; Task 1 (`IDLE_TIMEOUT_SECONDS`, `MCP_TOOL_TIMEOUT_ENV`, `PERSISTENT_STDIN_OK`).
- Produces:
  - `NativeTurnRequest(model: str, system_prompt: str, user_content: list[dict[str, Any]], executor: ToolExecutor, cancel_check: Callable[[], bool], deadline_monotonic: float, max_turns: int, cwd: Path, effort: str | None = None, fallback_model: str | None = None, max_output_tokens: int | None = None, autocompact: str | None = None, session: SessionPlan | None = None)`
  - `class NativeTurnSink(Protocol)`: `on_init(init: CliInit)`, `on_text(text: str)`, `on_thinking(text: str)` (`""` = ainda pensando), `on_tool_use_started(tool_use_id: str, tool_name: str)`, `on_tool_input_progress(tool_use_id: str, tool_name: str, bytes_received: int)`, `on_tool_use_complete(tool_use_id: str, tool_name: str, tool_input: Mapping[str, Any])`, `on_assistant_usage(message_id: str, usage: CliUsage)`, `on_compacted()`, `on_rate_limit(state: RateLimitState)` — todos `-> None`
  - `NativeTurnOutcome(result: CliResult, init: CliInit | None, final_text: str, exit_code: int | None)`
  - `class NativeTurnHandle:` `run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome`; `send_user(self, content: list[dict[str, Any]]) -> None`; `refuse_tools(self, reason: str) -> None`; `close(self) -> None` (idempotente: mata a árvore, fecha o MCP, apaga temporários, devolve a vaga, chama `log_cli_run`)
  - `ClaudeCliProvider.start_native_turn(self, request: NativeTurnRequest) -> NativeTurnHandle`
  - `NativeLoopUnavailable` (Task 3) levantada **antes de qualquer saída** quando: spawn falha, `init.mcp_servers[<name>] != "connected"`, `init.apiKeySource != "none"`, versão fora de `VALIDATED_CLI_RANGE`.

Decisões:
- Ordem de `start`: adquirir vaga (`CLI_SLOTS`, com `cancel_check` a cada 50 ms) → `BackgroundTurnToolServer.start` → gravar config e prompt em temporários (`tempfile.mkstemp`, permissões do dono) → `build_claude_command(mcp_config_file=…, max_turns=…, autocompact=…, session…)` → `Popen` com `cwd=request.cwd`, env isolado com `max_output_tokens` e `mcp_tool_timeout_ms = (deadline − agora) × 1000` → threads de stdout/stderr antes de escrever no stdin (`claude_code.py:900-905`) → escrever a 1ª mensagem. stdin fica **aberto** se `PERSISTENT_STDIN_OK`; senão fecha e `send_user` levanta `RuntimeError("follow-up requires a new process")` (o runner trata, Task 9).
- O executor passado ao servidor é embrulhado: antes de `call_tool` devolve a vaga, depois readquire (com `cancel_check`); assim um pai esperando subagentes não segura vaga (Review Focus 3).
- Idle: `idle_deadline` renova a cada linha do stdout; enquanto `handle.in_flight > 0` não corre. Estouro → `kill_process_tree` e `ProviderTransportError("Claude CLI sem saída por {IDLE_TIMEOUT_SECONDS}s", status_code=None)` (retentável). Deadline do turno → mesma coisa com código `TURN_DEADLINE_EXCEEDED` no texto.
- Cancelamento: `cancel_check` a cada 50 ms no laço de leitura; ao cancelar, `refuse_tools("turn cancelled")`, `kill_process_tree`, `close()` e `run_until_result` devolve `NativeTurnOutcome(result=CliResult(subtype="cancelled", …))`.
- Thinking: `THINKING_DELTA` vazio repassa `on_thinking("")` sem limitar (o runner limita).
- Resultado com `is_error` → `run_until_result` levanta `classify_result_error(...)`; `error_max_turns` **não** é erro (devolve o outcome; o runner decide).

- [ ] **Step 1: Testes que falham**

```python
def test_native_turn_runs_two_dependent_calls_through_executor():
    # fake_claude: chama echo("A"), espera resultado, chama echo(resultado+"B"), responde
    out = provider.start_native_turn(req(executor=rec)).run_until_result(sink)
    assert [c.arguments["text"] for c in rec.calls] == ["A", "AB"] and out.result.subtype == "success"
def test_unconnected_mcp_raises_native_unavailable_before_output(): ...   # fake init com status "failed"
def test_api_key_source_aborts(): ...
def test_cancel_during_slow_tool_kills_tree_and_closes_server():
    # executor bloqueia num Event; cancel_check vira True; em < 3 s: outcome.result.subtype == "cancelled", processo morto, porta fechada
def test_idle_timeout_paused_while_tool_in_flight(): ...  # IDLE=1s; ferramenta demora 3 s; turno termina success
def test_idle_timeout_kills_silent_process(): ...         # fake dorme 3 s sem linha, IDLE=1s → ProviderTransportError
def test_slot_released_while_tool_runs():
    # CLI_SLOTS com 1 vaga (ORIN_CLI_MAX_PROCESSES=1); ferramenta do turno 1 inicia o turno 2 e espera ele terminar → os dois terminam (sem deadlock)
def test_ten_concurrent_native_turns_without_limit():
    # sem ORIN_CLI_MAX_PROCESSES: 10 handles fake iniciados em threads → os 10 processos fake ficam vivos ao mesmo tempo
def test_follow_up_on_same_process_when_persistent_stdin(): ...  # send_user + 2º run_until_result
def test_mcp_tool_timeout_env_matches_remaining_deadline(): ...
def test_sink_gets_tool_progress_and_empty_thinking(): ...
def test_close_is_idempotent_and_logs_once(caplog): ...
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/providers/claude_cli/test_native_loop.py -v` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `git commit -m "feat(claude_cli): loop nativo com MCP por turno, idle timeout e cancelamento"`

---

### Task 9: Costura no runtime — `NativeTurnRunner` e `RuntimeToolExecutor` (R2.3, R2.5, R2.4 uso real, D3) — *só se o portão passou*

**Files:**
- Create: `src/agentos/agentic/native_turn.py`
- Modify: `src/agentos/agentic/runtime.py` (novo `_native_provider(turn)`; chamada no `_run` entre a linha 784 e o `for iteration` da 786)
- Test: `tests/unit/agentic/test_native_turn.py`

**Interfaces:**
- Consumes: Task 8 (`NativeTurnRequest`, `NativeTurnHandle`, `NativeTurnSink`, `NativeLoopUnavailable`), Task 7 (`ToolExecutor`, `McpToolSpec`, `ToolCallRequest`, `ToolCallResult`), Task 4 (`TranscriptState` para renderizar histórico), runtime existente: `_tool_schemas`, `_names.schemas_out/name_in`, `toolset.key_for`, `_run_toolset`, `_record_step`, `_effect_started/_effect_finished`, `_life`, `_emit`, `_context_usage`, `_recover_provider_failure`, `_switch_to_fallback`, `_settle_remaining_todos`, `_settle_quality`, `_cancel`, `_fail`, `counters`, `store.record_usage`.
- Produces:
  - `AgenticTurnRuntime._native_provider(self, turn: Mapping[str, object]) -> object | None` — devolve `self.provider` quando ele tem `native_turn_available`/`start_native_turn` chamáveis, `self.toolset is not None`, não é `/compact` nem `!shell`, e `self.provider.native_turn_available(turn)` é verdadeiro.
  - `class NativeTurnRunner:` `__init__(self, runtime: AgenticTurnRuntime, provider: object, turn: dict[str, object], *, turn_id: str, messages: list[dict[str, object]], deadline: datetime)`; `run(self) -> AgenticRunResult | None` — `None` = nativo indisponível antes de qualquer saída; o `_run` segue para o laço clássico (texto).
  - `class RuntimeToolExecutor:` implementa `ToolExecutor`; `__init__(self, runtime, turn, *, tool_schemas: list[dict[str, object]], correlate: Callable[[str, Mapping[str, Any], Mapping[str, Any]], str])`; atributo `abort_code: str | None`; `action_count: int`.

Costura no `_run` (pseudocódigo, o resto do `_run` não muda):

```python
native = self._native_provider(turn)
if native is not None:
    outcome = NativeTurnRunner(self, native, turn, turn_id=turn_id, messages=messages, deadline=deadline).run()
    if outcome is not None:
        return outcome
    # indisponível antes de qualquer saída: o laço clássico abaixo usa provider.stream (texto)
iterations = ...
```

Decisões (`RuntimeToolExecutor.call_tool`):
1. Nome público → interno: `runtime._names.name_in` (se houver) e `toolset.key_for`; desconhecido → `ToolCallResult(is_error=True)` com a lista de nomes válidos.
2. `call_id = correlate(public_name, arguments, meta)`: `meta[TOOL_USE_ID_META_KEY]` quando o spike achou a chave; senão o `tool_use_id` do `on_tool_use_complete` pendente com o mesmo nome e argumentos (JSON canônico, FIFO); senão `mcp_<12 hex>`.
3. **Agrupador por mensagem do assistente (decisão do Carlos: manter o paralelismo).** O estado de `_run_toolset` (`_writes`, `_succeeded_reads`, `_failed_signatures`) não é seguro para chamadas concorrentes, mas um **lote** dentro de um único `_run_toolset` já roda leituras e subagentes em paralelo e escritas em ordem (`runtime.py:2387-2426`, `MAX_PARALLEL_TOOLS`/`MAX_PARALLEL_SUBAGENT_TOOLS`). **Ajustado pelo spike** (`docs/agent_memory/2026-11-05-claude-cli-native-spike.md`, 3c): a CLI manda juntas as chamadas somente leitura de uma mensagem e **em série** as de escrita (a próxima só chega depois do resultado da anterior), então esperar as N chamadas da mensagem travaria. Regra: cada `call_tool` entra num `ToolBatchCollector` (classe nova em `native_turn.py`: `submit(call_id, internal_name, arguments, *, message_id: str, read_only: bool) -> ToolCallResult`, bloqueante). Uma chamada de escrita roda sozinha, na hora. Uma chamada somente leitura abre (ou entra n)uma janela de `BATCH_GATHER_SECONDS = 0.1` para a mesma `message_id`; ao fechar a janela, roda **um** `_run_toolset(turn, [calls...])` na ordem de chegada e devolve a cada chamada o seu resultado. Lotes nunca misturam mensagens e rodam um de cada vez (um `threading.Lock` em volta do `_run_toolset`). O `message_id` vem do `tool_use` correlacionado pelo `_meta["claudecode/toolUseId"]`. O `tools/list` anota `annotations.readOnlyHint = true` exatamente nas ferramentas que `toolset.is_read_only` aceita.
4. `results = runtime._run_toolset(turn, [...lote...])` — isso já emite `tool_started`/`tool_finished`, efeitos, `PostToolUse`, `POST_TOOL_BATCH`, e aplica as regras de lote da Fase 1 (R1.8: repetidas rodam uma vez; edit de arquivo lido no mesmo lote é recusado).
5. Grava `transcript.STEP_ASSISTANT_TOOL_CALL` e `STEP_TOOL_RESULT` como `runtime.py:1267-1279`, e acrescenta ao `messages` local `_assistant_tool_message(...)` + `_tool_result_messages(...)` (histórico do Orin completo para o próximo turno e para outros provedores).
6. Paradas (definem `abort_code` e devolvem `is_error` "turn stopped: …"): `toolset.stop_requested` → `HOOK_STOPPED`; `max(_consecutive_tool_failures) >= 3` → `REPEATED_TOOL_FAILURE`; `limits.max_actions` → `ACTION_LIMIT`; `reconciliation_required(turn)` → `EFFECT_RECONCILIATION_REQUIRED`; `limits.safety_max_tool_rounds` → pausa de segurança.
7. Imagens do resultado → `ToolCallResult.images` (`{"data", "mime_type"}` a partir de `{"type":"image","media_type","data"}`); se o spike reprovou o item 4, texto + aviso.

Decisões (`NativeTurnRunner.run`):
- Antes de subir: `tool_schemas = names.schemas_out(_tool_schemas(turn))`; emite `context_updated` com `_context_usage(messages, _request_messages(messages), tool_schemas)` e `limit_tokens = limits.context_window_tokens`.
- Conteúdo da 1ª mensagem: sem sessão retomada (Task 11 ramo B, ou sessão nova) = `TranscriptState().blocks_for(histórico anterior à mensagem atual)` + bloco `<system-reminder>` com `volatile_prompt` e `first_user_context` + a mensagem atual (texto e imagens/anexos como blocos). Prompt de sistema = `runtime.system_prompt` (estável). `max_turns = limits.max_iterations or 200`. `autocompact = "auto"` ou o inteiro do `autoCompact` do Orin quando 100k–1M; `False` no Orin → não passar a flag.
- Sink: `on_text` → `store.delta` com a mesma regra de `\n\n` entre mensagens do assistente (`runtime.py:1030-1043`); `on_thinking` → buffer de 60 caracteres/0,12 s como `runtime.py:963-970`, e `""` limitado a 1 a cada 2 s; `on_tool_use_started`/`on_tool_input_progress` → `_life("generating_tool_call", tool_name=<interno>, call_id, bytes_received, iteration)` a cada 1,5 s; `on_assistant_usage` → `counters.note_usage(...)` + `settle_provider_call()` + `store.record_usage` uma vez por `message_id`, e `context_updated` com `used_tokens = usage.context_tokens` (R2.5); `on_compacted` → `_compaction_count += 1`; `on_init` → se `init.model` difere de `turn["model_id"]`, `turn["model_id"] = init.model`, `_model_switched(init.model)` e `_life("model_switched", from_model, to_model, reason="cli_reported")` (R2.4).
- Efeito de provider: um `_effect_started(kind="provider", invocation_ref=f"provider:native:{n}")` por processo; `APPLIED` no resultado; `UNKNOWN` se o processo morreu depois de ter emitido algo.
- Depois de cada `result`:
  - `abort_code` → `_fail`/`HOOK_STOPPED` como o laço clássico;
  - `success`: roda, nesta ordem e com os mesmos limites do laço clássico, `code_completion_gate` (`runtime.py:1349-1386`), `_needs_verification` (`1387-1406`) e o hook `STOP` (`1407-1424`); se algum pede continuação, `handle.send_user([texto])` (ou, sem `PERSISTENT_STDIN_OK`, novo processo — ramo A com `--resume`, ramo B com o histórico atual em blocos) e volta a `run_until_result`; senão `_settle_remaining_todos`, `_life("completed")`, `store.finish`, `_settle_quality`, `AgenticRunResult("completed", n_processos, action_count)`;
  - `error_max_turns`: uma vez, `handle.refuse_tools("tool budget exhausted")` + `send_user([CLOSING_INSTRUCTION])`; de novo → `_fail(..., "ITERATION_LIMIT")`;
  - exceção classificada: se nada foi emitido ainda, `_recover_provider_failure` (retry → novo handle; fallback 404 → `_switch_to_fallback` e, se o novo provider também aceitar nativo, novo handle, senão `return None` para o laço clássico); se já emitiu, `reconciliation_required` como `runtime.py:1146-1149`;
  - `cancelled` → `runtime._cancel(...)`.
- `finally: handle.close()`.

- [ ] **Step 1: Testes que falham** (provider fake com `start_native_turn` que devolve um handle roteirizado: chama o executor e o sink numa ordem fixa)

```python
def test_runtime_hands_whole_turn_to_native_provider_once():
    result = runtime.run("t1", turn=turn)
    assert result.state == "completed" and fake_provider.starts == 1 and fake_provider.stream_calls == 0
def test_mcp_call_goes_through_run_toolset_with_permissions_hooks_and_effects():
    # toolset fake: PreToolUse nega edit_file → resultado is_error com "Um hook PreToolUse bloqueou"
    # store fake registra effect_started(kind="tool") e lifecycle("tool_started"/"tool_finished") com invocation_id == tool_use_id
def test_native_unavailable_falls_back_to_text_loop():
    fake_provider.raise_on_start = NativeLoopUnavailable("mcp failed")
    assert runtime.run("t1", turn=turn).state == "completed" and fake_provider.stream_calls >= 1
def test_cli_reported_model_replaces_turn_model(): ...      # init.model "claude-opus-5-5[1m]" → turn["model_id"] e lifecycle model_switched
def test_context_meter_uses_cli_usage_and_catalog_window():
    # usage input=1000 cache_read=50000 cache_creation=2000 → context_updated used_tokens == 53000, limit_tokens == 1_000_000
def test_usage_recorded_once_per_assistant_message(): ...
def test_stop_hook_block_sends_follow_up_on_same_handle(): ...
def test_max_turns_gets_one_closing_follow_up_then_iteration_limit(): ...
def test_repeated_tool_failure_aborts_native_turn(): ...
def test_cancel_returns_cancelled_and_closes_handle(): ...
def test_404_switches_to_fallback_model_without_retry(): ...  # retries == 0, model_switched, 2º handle com o modelo reserva
def test_history_written_for_next_turn(): ...                 # store.record_step recebeu assistant_tool_call e tool_result com o mesmo id
def test_parallel_calls_of_one_message_run_as_one_batch():
    # mensagem com 3 tool_use de read_file; 3 call_tool chegam em threads → _run_toolset chamado 1 vez com 3 chamadas,
    # cada submit recebe o seu resultado, duração total < 2 × a de uma leitura lenta fake
def test_a_write_call_runs_immediately_without_waiting_for_a_window(): ...  # edit_file sozinho: _run_toolset de 1, sem atraso
def test_reads_arriving_after_the_window_form_a_new_batch(): ...
def test_batches_of_different_messages_never_mix(): ...
def test_tools_list_marks_read_only_tools_with_read_only_hint(): ...      # readOnlyHint == toolset.is_read_only(nome)
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/agentic/test_native_turn.py -v` → FAIL.
- [ ] **Step 3: Implementar `native_turn.py` e a costura.**
- [ ] **Step 4: Rodar e ver passar** + `uv run pytest tests/unit/agentic -q` (o laço clássico não muda).
- [ ] **Step 5: Commit** — `git commit -m "feat(runtime): turno inteiro no loop nativo do claude -p com execução pelo _run_toolset"`

---

### Task 10: `ask_user` dentro do turno, fila e lembretes (R2.3) — *só se o portão passou*

**Files:**
- Modify: `src/agentos/agentic/native_turn.py`, `src/agentos/agentic/session.py:1484-1486`, `src/agentos/workers/chat.py:230-233`
- Modify: `frontend/src/features/conversations/ChatPage.tsx:892-930, 1283-1310`
- Test: `tests/unit/agentic/test_native_turn_ask_user.py`, `frontend/src/features/conversations/__tests__/ChatPageNativeAskUser.test.tsx`

**Interfaces:**
- Consumes: Task 9 (`RuntimeToolExecutor`), `store.claim_queued_inputs(turn)` (`workers/chat.py:258`, `conversations/chat.py:2028`), `ChatApplication.enqueue` (`conversations/chat.py:2932`, que já fila quando há turno vivo), `enqueueMessage` (`frontend/src/api/sessionTools.ts:22`).
- Produces:
  - Estado de lifecycle `"waiting_user_in_turn"`: o session grava `TURN_WAITING_USER` com `{"in_turn": True}`; o store do worker **não** transiciona a execução (o turno continua `RUNNING`, então `_has_live_turn` é verdadeiro e a resposta vai para a fila).
  - `RuntimeToolExecutor._wait_for_answer(self, poll_seconds: float = 0.25) -> str | None` — `None` em cancelamento/deadline.
  - `RuntimeToolExecutor._turn_notes(self) -> str` — lembretes drenados de `runtime.reminders` (com `_add_periodic_reminders`) + `_check_external_changes` + filas reclamadas, em `<system-reminder>`.

Decisões:
- Quando `_run_toolset` devolve `wait_for_user` **e** a ferramenta é `ask_user`: `_life("waiting_user_in_turn")`, depois `_wait_for_answer` reclama a fila a cada 0,25 s (passando pelo hook `USER_PROMPT_SUBMIT` como `_inject_queued_inputs`, `runtime.py:434-464`), emite `_life("queued_input", ...)` e `_life("running")`, e devolve ao modelo: `"Resposta da pessoa:\n" + texto`. Cancelado → `ToolCallResult(is_error=True, text=INTERRUPTED_TOOL_RESULT)`. Sem timeout próprio além do deadline do turno (o `MCP_TOOL_TIMEOUT` da Task 8 já cobre).
- Outros `wait_for_user` (aprovação de plano, MCP, plugin, `exit_plan_mode`) **continuam encerrando o turno** como hoje (`runtime.py:1315-1320`): o executor define `abort_code = "WAITING_USER"`, devolve o texto da ferramenta e o runner termina `waiting_user` depois do `result` (o fluxo dessas aprovações na UI não muda).
- Fila e lembretes: todo resultado de ferramenta recebe `_turn_notes()` no fim (append-only do ponto de vista da CLI); a fila é reclamada aqui e não mais entre iterações.
- Frontend: em `submitQuestionAnswers`, quando o turno da pergunta é o turno ativo e não terminal, enviar com `enqueueMessage(client, conversationId, response)` em vez de `sendChatMessage(response)`; em `openQuestionTurnIds`, um `turn.started` com `queue_id` posterior ao `turn.waiting_user` (`in_turn`) do mesmo turno fecha a pergunta. Sem mudança visual.

- [ ] **Step 1: Testes que falham**

```python
def test_ask_user_waits_for_queued_answer_inside_the_turn():
    store.queue_after(seconds=0.5, text="Respostas às perguntas do agente: ...")
    result = executor.call_tool(ToolCallRequest("AskUserQuestion", QUESTIONS, {}))
    assert result.text.startswith("Resposta da pessoa:") and "waiting_user_in_turn" in store.states
    assert store.transitions == []          # execução nunca foi para WAITING_USER
def test_ask_user_wait_ends_on_cancel(): ...      # cancel em 0,3 s → is_error, texto INTERRUPTED_TOOL_RESULT, < 1 s
def test_plan_approval_still_ends_turn_waiting_user(): ...
def test_queued_input_and_reminders_ride_on_next_tool_result(): ...
def test_worker_lifecycle_ignores_waiting_user_in_turn(): ...   # _RuntimeStore.lifecycle não chama _transition
```

```tsx
it('envia respostas de pergunta de turno vivo pela fila', async () => {
  // turno ativo running + evento turn.waiting_user {in_turn:true} → submit chama POST /queue, não POST /messages
})
it('fecha a pergunta quando chega turn.started com queue_id do mesmo turno', async () => { /* openQuestionTurnIds vazio, running volta a true */ })
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/agentic/test_native_turn_ask_user.py -v` e `npm --prefix frontend run test -- ChatPageNativeAskUser` → FAIL.
- [ ] **Step 3: Implementar backend e frontend.**
- [ ] **Step 4: Rodar e ver passar** + `npm --prefix frontend run test -- ChatPage`.
- [ ] **Step 5: Commit** — `git commit -m "feat(native): ask_user espera a resposta dentro do turno pela fila"`

---

### Task 11: Continuidade entre mensagens da conversa (R2.3, spike item 6) — *só se o portão passou*

**Files:**
- Create (ramo A): `src/agentos/providers/claude_cli/sessions.py`
- Modify: `src/agentos/agentic/native_turn.py`, `src/agentos/providers/claude_cli/native_loop.py`
- Test: `tests/unit/providers/claude_cli/test_sessions.py`, `tests/unit/agentic/test_native_turn_sessions.py`

**Interfaces:**
- Consumes: Task 1 (`SESSION_BRANCH`, `SESSION_FILES_DIR`), Task 8 (`NativeTurnRequest.session`), `orin_paths().data` (`installation/paths.py:153`).
- Produces:
  - `history_digest(messages: Sequence[Mapping[str, object]]) -> str` — sha256 de `(role, texto)` das mensagens de usuário e respostas finais do assistente (sem passos de ferramenta, sem lembretes `_reminder`).
  - `prompt_digest(system_prompt: str) -> str`
  - `SessionPlan(session_id: str, resume: bool)`
  - Ramo A: `SessionRecord(session_id: str, history_digest: str, prompt_digest: str, model: str, updated_at: float)`; `class ClaudeCliSessionStore:` `__init__(self, root: Path | None = None)` (padrão `orin_paths().data / "claude-cli" / "sessions"`, arquivo por `sha256(conversation_id)[:32]`, gravação atômica); `get(conversation_id) -> SessionRecord | None`; `put(conversation_id, record) -> None`; `drop(conversation_id) -> None`; `plan(self, conversation_id: str, *, history_digest: str, prompt_digest: str) -> SessionPlan`.
  - `NATIVE_CWD = orin_paths().data / "claude-cli" / "workdir"` (cwd fixo do processo nativo; as sessões da CLI ficam no slug dessa pasta, que o usuário não abre).

Decisões:
- **Ramo A** (item 6 aprovado): `plan` devolve `resume=True` só se há registro com o mesmo `history_digest` do histórico **antes** da mensagem atual e o mesmo `prompt_digest`; senão `session_id` novo (`uuid4`) e `resume=False`. Com `resume=True`, o conteúdo enviado é só o bloco `<system-reminder>` (volátil) + a mensagem atual; com `resume=False`, o histórico em blocos (Task 9). Ao fim de um turno nativo `completed`/`waiting_user`, `put` com o digest do histórico **incluindo** a mensagem atual e a resposta final. Falha ao retomar (`result` com erro de sessão inexistente antes de qualquer saída) → `drop` e uma nova tentativa com `resume=False`. Troca de modelo não invalida (a CLI aceita `--model` no `--resume`); mudança do prompt estável invalida.
- **Ramo B** (item 6 reprovado): sem `sessions.py`; todo turno é `--no-session-persistence` com o histórico em blocos; `NATIVE_CWD` continua (cwd vazio do Orin).
- Follow-ups dentro do turno (Task 9) usam o stdin persistente se `PERSISTENT_STDIN_OK`; senão: ramo A → novo processo com `--resume`; ramo B → novo processo com histórico atualizado.

- [ ] **Step 1: Testes que falham**

```python
def test_resume_only_when_history_unchanged(tmp_path):            # ramo A
    st = ClaudeCliSessionStore(tmp_path); st.put("c1", SessionRecord("u1", "h1", "p1", "m", 0))
    assert st.plan("c1", history_digest="h1", prompt_digest="p1") == SessionPlan("u1", True)
    assert st.plan("c1", history_digest="h2", prompt_digest="p1").resume is False   # rewind/fork/edição
    assert st.plan("c1", history_digest="h1", prompt_digest="p2").resume is False   # prompt estável mudou
def test_history_digest_ignores_tool_steps_and_reminders(): ...
def test_second_message_resumes_and_sends_only_new_content(): ...  # argv tem --resume u1; content não tem o histórico
def test_failed_resume_falls_back_to_fresh_session_once(): ...
def test_native_process_cwd_is_orin_workdir(): ...
def test_branch_b_never_persists(): ...                            # se SESSION_BRANCH == "B": "--no-session-persistence" sempre
```

- [ ] **Step 2: Rodar e ver falhar.** `uv run pytest tests/unit/providers/claude_cli/test_sessions.py tests/unit/agentic/test_native_turn_sessions.py -v` → FAIL.
- [ ] **Step 3: Implementar o ramo do spike** (o outro ramo não é escrito; os testes do ramo ausente são marcados `skipif` lendo `SESSION_BRANCH`).
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `git commit -m "feat(claude_cli): continuidade de sessão entre mensagens da conversa"`

---

### Task 12: Ativação — flag, portão de versão, ligação no worker, modo código (R2.9, D6 aliases)

**Files:**
- Modify: `src/agentos/providers/claude_cli/provider.py` (`native_tools_enabled`, `ClaudeCliProvider.native_turn_available`)
- Modify: `src/agentos/workers/chat.py:1104-1205` (`_transport_for`, `_provider_transport` devolvem `ClaudeCliProvider` para o bridge), `:1299-1320` (alias de subagente pelo catálogo), `:932-941` (usa `native_tools_enabled` da Task 6)
- Test: `tests/unit/providers/claude_cli/test_activation.py`; reescrever `tests/unit/provider_catalog/test_claude_code_context_and_subagents.py:265-327`

**Interfaces:**
- Consumes: Task 1 (`NATIVE_LOOP_VALIDATED`, `VALIDATED_CLI_RANGE`), Task 2 (`claude_cli_version`, `resolve_family_alias`, `discover_cached_models`).
- Produces:
  - `NATIVE_MODES: frozenset[str] = frozenset({"code"})` (a Task 13 amplia)
  - `native_tools_enabled(*, conversation_mode: str, code_mode: str, env: Mapping[str, str] | None = None, version: tuple[int, int, int] | None = None) -> bool` — verdadeiro só com `ORIN_CLAUDE_CLI_NATIVE_TOOLS` ≠ `off` (padrão `auto`; valor inválido = `auto` com aviso no log), `NATIVE_LOOP_VALIDATED`, versão dentro de `VALIDATED_CLI_RANGE` (`None` = sonda não respondeu → falso) e o modo do turno em `NATIVE_MODES` (`"code"` quando `code_mode == "code"`, senão `conversation_mode`).
  - `ClaudeCliProvider.native_turn_available(self, turn: Mapping[str, object]) -> bool` — `native_tools_enabled(...)` com a versão cacheada do binário e sem disjuntor aberto (um `NativeLoopUnavailable` abre o disjuntor por 10 min para esse binário, logado uma vez).

Decisões:
- Se `NATIVE_LOOP_VALIDATED` é falso (portão reprovado), esta task entrega só a flag e `native_turn_available` sempre falso.
- `_transport_for`/`_provider_transport`: para `provider == "anthropic"` com bridge, devolver `ClaudeCliProvider(base_url=base_url, model=model_id)` (hoje `HTTPProviderStreamTransport`, `workers/chat.py:1150-1151,1199-1200`). O tipo de retorno de `_child_provider_for_turn` passa a `object`.
- Subagente: em `_child_provider_for_turn`, quando `keep_cli_subscription` e o id resolvido é um valor de `FAMILY_DEFAULTS` ou um alias (`opus|sonnet|haiku|fable`), trocar por `resolve_family_alias(família, discover_cached_models())`.

- [ ] **Step 1: Testes que falham**

```python
@pytest.mark.parametrize("env,mode,expected", [({}, "code", True), ({"ORIN_CLAUDE_CLI_NATIVE_TOOLS": "off"}, "code", False), ({}, "chat", False), ({"ORIN_CLAUDE_CLI_NATIVE_TOOLS": "bogus"}, "code", True)])
def test_native_tools_enabled_flag_and_mode(env, mode, expected): ...   # version=(2,1,309)
def test_native_disabled_outside_validated_version(): ...   # (2,2,0) e None → False
def test_circuit_breaker_after_native_unavailable(): ...
def test_worker_returns_claude_cli_provider_for_bridge(): ...   # substitui :265-327
def test_child_alias_resolved_by_catalog(monkeypatch): ...      # catálogo com claude-opus-5-6 → filho usa claude-opus-5-6
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/providers/claude_cli/test_activation.py tests/unit/provider_catalog -v` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar a suíte inteira** — `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(claude_cli): ORIN_CLAUDE_CLI_NATIVE_TOOLS e loop nativo no modo código"`

---

### Task 13: Loop nativo em chat e friend (R2.9, segunda etapa) — *só se o portão passou e a Task 14 aprovou o modo código*

**Files:**
- Modify: `src/agentos/providers/claude_cli/provider.py` (`NATIVE_MODES = frozenset({"code", "chat", "friend"})`)
- Modify: `src/agentos/agentic/runtime.py:903-961` (extrair `emit_speak_parse_result` para método), `src/agentos/agentic/native_turn.py` (texto passa pelo `SpeakStreamParser` em friend/live)
- Test: `tests/unit/agentic/test_native_turn_friend.py`; suíte existente de friend/live

**Interfaces:**
- Consumes: Task 9 (sink `on_text`).
- Produces: `AgenticTurnRuntime._emit_speak_events(self, turn: dict[str, object], parser: SpeakStreamParser, result: SpeakParseResult, *, iteration: int, state: _SpeakState) -> str` e `_SpeakState(iteration_had_speak: bool, pending_retraction: bool)` (dataclass mutável), usados pelo laço clássico e pelo runner — mesma saída que o closure atual.

Decisões: o chat comum usa `PhaseController` (`session.py:4642-4649`); no nativo a fase não muda a lista de ferramentas no meio do turno (a CLI recebe a lista uma vez): o runner publica as ferramentas da fase inicial e o `PhaseController` só é avançado pelos resultados (`_advance_phase` no executor). No friend, `on_text` alimenta o parser de fala; `on_tool_use_started` chama `parser.on_non_text_boundary("tool_call")`; ao fim, `parser.finish(...)`.

- [ ] **Step 1: Testes que falham** — `test_friend_native_turn_emits_speak_events_like_classic_loop` (mesma sequência de `SPEAK_*` do laço clássico para o mesmo texto roteirizado); `test_chat_native_turn_advances_phase_from_results`.
- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/unit/agentic/test_native_turn_friend.py -v`.
- [ ] **Step 3: Implementar a extração (refactor sem mudança de comportamento) e o uso no runner.**
- [ ] **Step 4: Rodar** `uv run pytest tests/unit/agentic -q` → PASS (inclui os testes de friend/live existentes).
- [ ] **Step 5: Commit** — `git commit -m "feat(native): loop nativo também em chat e friend"`

---

### Task 14: Aceite da Fase 2 — replay real e registro

**Files:**
- Modify: `docs/agent_memory/2026-11-05-claude-cli-native-spike.md` (seção "Aceite")

- [ ] **Step 1:** Com o Orin rodando localmente (`uv run orin`), modo código, Opus 5.5 pela assinatura, refazer as duas tarefas reais da auditoria (análise de upsell no Educly; investigação de contexto no Orin).
- [ ] **Step 2: Conferir e anotar** cada critério do aceite da spec: log `claude_cli run mode=native … actual=claude-opus-5-5`; medidor com `limit_tokens = 1000000`; nenhuma resposta com mais de uma rodada de chamadas dependentes (cada `tools/call` depois do resultado anterior, pelos ids); nenhum `[Tool Result` no texto do modelo; progresso visível (eventos "Pensando"/"Preparando…" sem buraco maior que `IDLE_TIMEOUT_SECONDS`); tokens de entrada do turno < 216k para 10 iterações, com `cache_read` por processo. Fazer o mesmo replay com `ORIN_CLAUDE_CLI_NATIVE_TOOLS=off` e anotar `cache_read`/`cache_creation` por iteração do texto (confirma o item 7 em uso real).
- [ ] **Step 3:** Se o modo código passou, liberar a Task 13; se não, abrir a pendência no documento com a evidência.
- [ ] **Step 4: Commit** — `git commit -m "docs(agent_memory): aceite da fase 2 do adapter claude_cli"`

---

## Autorrevisão

- **Cobertura da spec:** R2.1 → Task 1 (itens 1–7, portão e ramos); R2.2 → Tasks 2–5, 7, 8 (pacote e módulos fixos, fachada, teste de não-import); R2.3 → Tasks 8–11 (um processo por mensagem, stdin persistente quando aprovado, eventos existentes, D3 por `_run_toolset`, `ask_user` no turno); R2.4 → Task 2 (mapa removido, aliases), Task 9 (modelo do `system/init`), Task 5 (testes reescritos); R2.5 → Task 6 (janela do catálogo nos dois caminhos, orçamento só se item 7 reprovar, sem aging enquanto cabe, cache_read/creation), Task 4 (blocos append-only, prompt e ferramentas estáveis), Task 9 (`--autocompact`, medidor pelo uso da CLI); R2.6 → Task 3 (404, `rateLimitType`, horário local), Task 5 (`CLAUDE_CODE_MAX_OUTPUT_TOKENS`), Task 8 (idle do item 5); R2.7 → Tasks 3, 5, 9; R2.8 → Task 5 (`log_cli_run`), Task 8 (modo nativo); R2.9 → Tasks 12, 13. Riscos da spec: versão da CLI (Task 12), sessão no `/resume` (Task 11, cwd do Orin).
- **Nomes:** `ProviderTransportError`, `NativeLoopUnavailable`, `CliUsage.context_tokens`, `TurnToolServerHandle.in_flight/refuse_calls`, `NativeTurnHandle.refuse_tools/send_user/run_until_result/close`, `native_tools_enabled`, `text_context_budget`, `TEXT_CACHE_OK` conferidos entre as tasks.
- **Proporção:** corpos só onde a regra não sai da assinatura (costura do `_run`); o resto são assinaturas, valores e testes.
