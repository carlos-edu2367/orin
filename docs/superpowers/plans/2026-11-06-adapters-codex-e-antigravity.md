# Adapters de assinatura: Codex (`codex exec`) e Antigravity (`agy -p`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Usar no Orin as assinaturas do ChatGPT (via Codex CLI) e do Google (via Antigravity CLI), da mesma forma que o Orin já usa a do Claude: a CLI roda o loop do agente e chama as ferramentas do Orin pelo servidor MCP do turno.

**Architecture:**
- Cada provedor é um `CliAgentAdapter` novo, em `src/agentos/providers/codex_cli/` e `src/agentos/providers/antigravity_cli/`, e passa na suíte de conformidade `tests/unit/providers/cli_adapter_conformance.py`.
- Antes dos adapters, o contrato e o loop nativo deixam de supor o Claude: injeção de MCP por argv, env ou arquivo; prompt por stdin de uso único ou persistente; e inventário de ferramentas opcional.
- O `ClaudeCliProvider` já recebe `adapter=` (`src/agentos/providers/claude_cli/provider.py:374`). Ele vira o provedor genérico de CLI sem mudar de comportamento para o Claude.

**Tech Stack:** Python 3.12, subprocess com JSONL, servidor MCP Streamable HTTP do Orin (`src/agentos/providers/cli_tools_mcp/`), React e Vitest para os cards de configuração.

**Spec:** não há spec separada. As decisões de design abaixo fazem esse papel, e a spec da base do Claude continua valendo como referência de comportamento: `docs/superpowers/specs/2026-11-05-claude-assinatura-nativa-e-orin-code-design.md`.

## Decisões de design (fazem papel de spec)

**D1. `codex -p` não é o modo headless.**
- No Codex CLI, `-p` é `--profile`. O equivalente ao `claude -p` é `codex exec --json`, com saída em JSONL (`thread.started`, `turn.started`, `item.started/updated/completed`, `turn.completed` com `usage`, `turn.failed` e `error`).
- O prompt vai pelo stdin com `-`.
- Fonte: https://learn.chatgpt.com/docs/non-interactive-mode

**D2. Antigravity: há um bloqueio de termos de uso, e o Carlos decide antes da Fase C.**
- O FAQ oficial (https://antigravity.google/docs/faq/) diz: *"Wrapping `agy` inside third-party coding agents or applications, or automating account rotation, is not supported under consumer plans."* Diz também: *"Using third-party software, tools, or services to access Antigravity is a violation of our Terms of Service"*.
- O Orin chamando o `agy -p` com o login pessoal do Carlos se encaixa nisso. O risco é a conta Google ser suspensa.
- Caminhos possíveis (decisão do Carlos, registrada na Task C0):
  - **(a)** não fazer;
  - **(b)** fazer pela rota de API key, com `modelProvider: "gemini"` e `GEMINI_API_KEY`, que é suportada para headless e CI, mas é cobrada à parte e não usa a assinatura;
  - **(c)** fazer com o login da assinatura, assumindo o risco.
- O fórum (https://discuss.ai.google.dev/t/183051) tem uma resposta "fully supported", mas ela é de um usuário, não da Google.
- **Decisão em 2026-11-06: opção (c).** O Carlos assumiu o risco: usar o `agy -p` com o login da assinatura. Como o Orin tem releases públicas, o provedor `google-antigravity` vem **desligado por padrão**. Para ligar, a pessoa marca no card um aviso que cita o FAQ e diz que o risco é dela. A rota de API key (opção b) não faz parte desta entrega.

**D3. Ordem: Codex primeiro.**
- A memória do projeto dizia "Antigravity e depois Codex". A ordem foi invertida por causa da D2 e porque o contrato JSONL do Codex é mais estável.
- Pelo que a doc mostra, o Codex não proíbe o uso do `codex exec` com o login do ChatGPT em scripts. Ela chama o uso em CI de "advanced" e manda tratar o `auth.json` como senha. Mesmo assim, confira os termos do plano ChatGPT do Carlos na Task A0.

**D4. Isolamento do Codex.**
- Use `--ignore-user-config` (descarta o `config.toml` do Carlos, que tem 17 projetos, `model_reasoning_effort = "xhigh"` etc.) e `--ignore-rules`.
- Toda a configuração vai por `-c key=value`. O `CODEX_HOME` não muda, porque a autenticação mora nele.
- Ferramentas próprias do Codex desligadas: `features.shell_tool=false`, `web_search="disabled"` e `features.view_image=false`. Use também `sandbox_mode="read-only"` e `approval_policy="never"`.
- O Orin executa as ferramentas pelo MCP: `mcp_servers.orin.url=<url do turno>`, `mcp_servers.orin.bearer_token_env_var="ORIN_MCP_TOKEN"` e `mcp_servers.orin.required=true`.
- O token nunca vai no argv, só no env.
- O system prompt vai por `-c model_instructions_file=<arquivo owner-only>`.

**D5. Uso de tokens no Codex.**
- No `turn.completed`, `input_tokens` já inclui `cached_input_tokens`, como é padrão na OpenAI.
- O tradutor mapeia `CliUsage(input_tokens = input - cached, cache_read_input_tokens = cached, output_tokens = output)`, para o `context_tokens` não contar o cache duas vezes.
- `reasoning_output_tokens` vai para `StreamUsage.reasoning_tokens`.

**D6. Turnos no Codex.**
- O `codex exec` não aceita stdin persistente. Cada mensagem do Orin é um processo novo.
- Com sessão, use `codex exec resume <thread_id> -`. Sem sessão, use `--ephemeral`.
- `capabilities.persistent_stdin=False`.

**D7. Modelos.**
- Codex: leia `~/.codex/models_cache.json` (campo `models[]`, com `slug`, `display_name` e `supported_reasoning_levels[].effort`, filtrando `visibility == "list"`). Se não houver cache, use uma lista fixa.
- Os nomes exibidos seguem o padrão `"<display_name> (Assinatura Codex)"`.
- Antigravity: saída de `agy models`, no padrão `"<id> (Assinatura Antigravity)"`.
- O esforço do Orin vira `-c model_reasoning_effort=<effort>` no Codex (só se o modelo listar o nível) e `--effort` no `agy`.

**D8. UI.**
- Dois cards novos em Configurações → Provedores: "Assinatura ChatGPT (Codex CLI)" e "Assinatura Google (Antigravity CLI)".
- Cada card mostra se o binário foi encontrado, a versão, se está autenticado e o comando de login (`codex login` / `agy`).
- O seletor mostra `openai-codex / GPT-6.1-Sol (Assinatura Codex)` no mesmo formato de `anthropic / Claude Opus 5.5 (Assinatura CLI)`.

## Global Constraints

- O comportamento do Claude CLI não muda. Toda a suíte existente de `tests/unit/providers/claude_cli/` e `tests/unit/redteam/` continua passando sem edição, exceto renomeações de import feitas na Task 1.
- Nada de chamada real às CLIs nos testes automáticos: tudo roda com fixtures JSONL gravadas na Fase A/C.
- Chamada real só com autorização do Carlos, mínima e com o modelo mais barato.
- Token do MCP nunca no argv nem nos logs. `loggable_command` precisa redigir qualquer `-c` que contenha `token`, `key` ou `Authorization`.
- Nada de escrever em `~/.codex/config.toml`, `~/.gemini/**` ou outro arquivo global do usuário.
- A suíte precisa passar no Windows e no Linux (lição do CI da 0.12.1: nada de supor ferramenta de um só sistema).
- Textos visíveis em pt-BR.

## Review Focus

1. **Codex sem login ou com login expirado:** o card mostra "Faça login com `codex login`" e o turno falha com `CliErrorKind.AUTH`, sem retry.
2. **Limite da assinatura estourado no meio do turno:** um `error` ou `turn.failed` com mensagem de limite vira `RATE_LIMITED`, com `retry_after_seconds` quando a mensagem traz o horário, e o texto parcial já emitido não se perde.
3. **Ferramenta própria da CLI escapando:** se aparecer um item `command_execution`, `file_change` ou `web_search` (o Codex executou algo fora do MCP), o turno aborta com `CliSecurityAbort`, como o Claude faz com ferramenta fora da allowlist.
4. **Cache contado duas vezes:** a janela de contexto não pode estourar à toa em conversas longas (D5).
5. **Binário presente mas antigo:** quando a versão está abaixo da validada, o card mostra "Atualize o Codex CLI" e o turno não roda.

---

## Fase 0 — Contrato e loop nativo genéricos

### Task 1: Contrato v2 do `CliAgentAdapter`

**Files:**
- Modify: `src/agentos/providers/cli_adapter.py`
- Modify: `src/agentos/providers/claude_cli/adapter.py`
- Modify: `tests/unit/providers/cli_adapter_conformance.py`
- Test: `tests/unit/providers/test_claude_cli_adapter_conformance.py`

**Interfaces:**
- Produces, em `cli_adapter.py`:
  - `@dataclass(frozen=True) class ToolServerWiring: argv: tuple[str, ...]; env: Mapping[str, str]; files: tuple[Path, ...]`
  - `CliAgentAdapter.wire_tool_server(handle, directory: Path) -> ToolServerWiring`. Substitui `write_tool_server_config`. No Claude, `files=(json,)` e `argv=("--mcp-config", str(json))`. Os arquivos são apagados pelo provedor no fim do turno.
  - `CliAgentAdapter.encode_user_turn(content: list[dict[str, Any]]) -> bytes`. No Claude, é o `user_event_line` atual de `native_loop.py`.
  - Os campos `CliCapabilities.reports_tool_inventory: bool = True` e `CliCapabilities.forbidden_item_kinds: frozenset[str] = frozenset()`.
  - `CliTurnRequest.prompt_via_stdin_once: bool`. É derivado de `capabilities.persistent_stdin`, não configurado à mão.

- [x] **Step 1: Estenda a suíte de conformidade.**
  - `test_wiring_never_puts_secret_in_argv`: o token de `FakeToolServerHandle` não aparece em `wiring.argv` nem em `loggable_command(build_command(...))`.
  - `test_encode_user_turn_is_single_line_utf8`: a saída termina em `\n` e não contém outro `\n`.
- [x] **Step 2: Rode e veja falhar.** `uv run pytest tests/unit/providers -q`.
- [x] **Step 3: Implemente o contrato v2 e migre o `ClaudeCliAdapter`.** O `build_command` do Claude continua gerando o argv atual, byte a byte. Para provar, crie `test_claude_argv_unchanged_by_contract_v2`, que compara com um snapshot gerado antes da mudança.
- [x] **Step 4: Rode `tests/unit/providers` e `tests/unit/redteam`.** Esperado: tudo passa.
- [x] **Step 5: Commit.** `refactor(providers): CliAgentAdapter v2 with tool-server wiring and user-turn encoding`

### Task 2: Loop nativo sem pressupostos do Claude

**Files:**
- Modify: `src/agentos/providers/claude_cli/native_loop.py`, em `start_native_turn_for_provider` (perto da linha 628), na escrita do stdin (linha 810) e em `send_user` (linha 283)
- Modify: `src/agentos/agentic/native_turn.py`, no diretório de trabalho fixo (linhas 68 e 74) e nos imports (linhas 15 a 37)
- Modify: `src/agentos/providers/claude_cli/provider.py`, onde estão `self.adapter.build_command` (linha 635) e `environment` (linha 644)
- Test: `tests/unit/providers/claude_cli/test_native_loop.py`
- Test: `tests/unit/providers/test_native_loop_one_shot.py`

**Interfaces:**
- Consumes: `ToolServerWiring`, `encode_user_turn` e `capabilities.persistent_stdin` da Task 1.
- Produces:
  - `native_workdir(adapter_name: str) -> Path`, que devolve `orin_paths().data / f"{adapter_name}-cli" / "workdir"`. Para o Claude continua `claude-cli`.
  - Modo one-shot: com `persistent_stdin=False`, o loop escreve `encode_user_turn`, fecha o stdin e não aceita `send_user` depois (levanta `NativeLoopUnavailable("one-shot")`). O próximo turno abre outro processo com `resume_session_id`.
  - Com `reports_tool_inventory=False`, a checagem de segurança "init antes de result" passa a aceitar o primeiro evento `INIT` sem `tools`. Um evento de item cujo tipo esteja em `forbidden_item_kinds` dispara `CliSecurityAbort`.

- [x] **Step 1: Testes com um adapter falso one-shot** (em `tests/unit/providers/test_native_loop_one_shot.py`):
  - o stdin é fechado depois do primeiro write;
  - `send_user` levanta erro;
  - um item proibido aborta o turno;
  - uma chamada MCP entre o init e o result é executada pelo `ToolExecutor`.
- [x] **Step 2: Rode e veja falhar.**
- [x] **Step 3: Implemente.** O que for genérico pode ir para `src/agentos/providers/cli_runtime/`, se ajudar a ler. Se mover algo, deixe reexports em `claude_cli/` para os imports atuais continuarem funcionando.
- [x] **Step 4: Rode a suíte inteira.** `uv run pytest -q`. Esperado: 0 falhas novas.
- [x] **Step 5: Commit.** `refactor(native-loop): support one-shot CLIs and adapter-specific workdirs`

### Task 3: Roteamento de provedor e configuração sem API key

**Files:**
- Modify: `src/agentos/agentic/provider_stream.py`, no bloco do bridge do Claude (linhas 877 a 935)
- Modify: `src/agentos/persistence/postgres/provider_configuration.py:346`
- Modify: `src/agentos/api/gateway.py:2159`
- Create: `src/agentos/providers/cli_registry.py`
- Test: `tests/unit/providers/test_cli_registry.py`

**Interfaces:**
- Produces: `CLI_PROVIDERS: Mapping[str, Callable[[], CliAgentAdapter]]`. As chaves são `"anthropic-cli"` (o adapter atual, mantendo o reconhecimento por bridge URL para configurações antigas), `"openai-codex"` e `"google-antigravity"`.
- Produces: `cli_adapter_for(provider: str, base_url: str | None) -> CliAgentAdapter | None`.
- Os ids `openai-codex` e `google-antigravity` são válidos sem API key e não exigem `base_url`.

- [x] **Step 1: Testes.**
  - `cli_adapter_for("anthropic", "http://127.0.0.1:18447")` devolve um `ClaudeCliAdapter`;
  - `cli_adapter_for("openai-codex", None)` devolve um `CodexCliAdapter`. Até a Task A3, use um stub registrado no teste;
  - salvar a configuração `openai-codex` sem chave não dá 400.
- [x] **Step 2: Rode e veja falhar.** Depois implemente e faça `provider_stream` usar `cli_adapter_for` em vez do `if is_claude_cli_bridge_url`.
- [x] **Step 3: Rode.** `uv run pytest tests/unit/providers tests/unit/agentic -q`. Esperado: passa.
- [x] **Step 4: Commit.** `feat(providers): registry for subscription CLI providers`

## Fase A — Codex

### Task A0: Termos e spike com gravação de fixtures (manual, com autorização)

**Files:**
- Create: `docs/agent_memory/2026-11-xx-codex-cli-spike.md`
- Create: `tests/unit/providers/codex_cli/fixtures/*.jsonl`

- [x] **Step 1: Termos.** Leia os termos do plano ChatGPT do Carlos sobre o uso do `codex exec` por outro programa local e registre a conclusão, com link, no doc do spike. Se proibirem, pare e leve ao Carlos.
- [x] **Step 2: Binário.** Hoje há só o app desktop (`WindowsApps\OpenAI.Codex_*`) e o `codex` não está no PATH. Registre:
  - de onde vem o binário: `npm i -g @openai/codex` ou o executável que o app traz;
  - o caminho no Windows;
  - a saída de `codex --version`;
  - se `codex exec` usa o login do app (o `~/.codex/auth.json` já existe).
- [x] **Step 3: Autorização.** Peça ao Carlos autorização para no máximo 6 chamadas reais, com o modelo mais barato do `models_cache.json` e `model_reasoning_effort=low`.
- [x] **Step 4: Grave os fixtures**, rodando com o servidor MCP de eco (`tests/fixtures/mcp_echo_server.py`) e com o isolamento da D4:
  - `simple_text.jsonl`;
  - `tool_call.jsonl` (uma chamada ao MCP `orin`);
  - `resume.jsonl` (`exec resume <thread_id>`);
  - `auth_error.jsonl`, com `CODEX_HOME` apontando para uma pasta vazia;
  - `forbidden_shell.jsonl`, sem `features.shell_tool=false`, para ver o item `command_execution`;
  - `bad_model.jsonl`.

  Registre o código de saída de cada um e a forma de `item.updated`, `turn.failed` e `error`, que a doc não descreve.
- [x] **Step 5: Confirme as chaves `-c` no argv real.** Confira cada uma: `mcp_servers.orin.*`, `model_instructions_file`, `features.shell_tool`, `web_search` e `model_reasoning_effort`. Também confira se o `resume` aceita os mesmos `-c` e o `--json`. Toda divergência da D4 a D6 vira uma nota "D4 corrigida: …" no topo deste plano antes da A1.
- [x] **Step 6: Commit** dos fixtures e do doc (sem tokens; confira com `grep -ri bearer`).

### Task A1: Comando, ambiente e catálogo do Codex

**Files:**
- Create: `src/agentos/providers/codex_cli/__init__.py`
- Create: `src/agentos/providers/codex_cli/command.py`
- Create: `src/agentos/providers/codex_cli/catalog.py`
- Test: `tests/unit/providers/codex_cli/test_command.py`
- Test: `tests/unit/providers/codex_cli/test_catalog.py`

**Interfaces:**
- Produces:
  - `build_codex_command(request: CliTurnRequest, wiring: ToolServerWiring) -> list[str]`. A ordem é `[binary, "exec", ("resume", id)?, "--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", ("--ephemeral" se not persist_session), "-m", model, *("-c", kv) …, *wiring.argv, "-"]`.
  - `build_isolated_codex_env(base, *, mcp_token: str) -> dict[str, str]`. Só repassa o necessário (`PATH`, `SYSTEMROOT`, `USERPROFILE`, `HOME`, `CODEX_HOME`, `APPDATA`, `LOCALAPPDATA`, `TEMP` e `TMP`) e define `ORIN_MCP_TOKEN`. Remove `OPENAI_API_KEY` e `CODEX_API_KEY`, para garantir a cobrança na assinatura.
  - `codex_version(binary) -> tuple[int, int, int] | None` e `MIN_CODEX_VERSION`, que é a versão gravada na A0.
  - `discover_codex_models(codex_home: Path) -> tuple[CliModelInfo, ...]` (D7).

- [x] **Step 1: Testes.**
  - argv exato para um turno novo e para um `resume`;
  - o token só aparece no env, nunca no argv;
  - `OPENAI_API_KEY` é removida;
  - `models_cache.json` com 10 modelos (fixture copiada da forma real, só com `slug`, `display_name`, `visibility` e `supported_reasoning_levels`) gera os `CliModelInfo` filtrados;
  - um JSON corrompido devolve a lista fixa.
- [x] **Step 2: Rode, veja falhar, implemente e rode de novo.** Esperado: passa.
- [x] **Step 3: Commit.** `feat(codex-cli): isolated command, env and model catalog`

### Task A2: Tradutor JSONL do Codex

**Files:**
- Create: `src/agentos/providers/codex_cli/stream.py`
- Test: `tests/unit/providers/codex_cli/test_stream.py`

**Interfaces:**
- Produces: `CodexJsonlTranslator`, que implementa `CliStreamTranslator`. O mapeamento é:

| Evento do Codex | Eventos do Orin |
|---|---|
| `thread.started` | `INIT` (`session_id = thread_id`, `model` do request, `tools=()`) |
| `item.updated`/`item.completed` com `agent_message` | `TEXT_DELTA` (só o sufixo novo) e, no completed, `ASSISTANT_MESSAGE` |
| `reasoning` | `THINKING_DELTA` |
| `item.started` com `mcp_tool_call` | `TOOL_USE_START` |
| `item.completed` com `mcp_tool_call` | `TOOL_USE_END` e `TOOL_RESULT` |
| `turn.completed` | `USAGE` (D5) e `RESULT` (`subtype="success"`) |
| `turn.failed` ou `error` | `RESULT` com `is_error=True` e o texto do erro em `result_text` |

  Os tipos de item proibidos (`command_execution`, `file_change` e `web_search`) passam para o loop com `raw["item"]["type"]` preservado.

- [x] **Step 1: Testes com os fixtures da A0.**
  - o texto final do `simple_text` é igual ao gravado;
  - `tool_call` produz exatamente uma chamada com o nome e os argumentos gravados;
  - `turn.completed` com `input_tokens=24763` e `cached_input_tokens=24448` dá `CliUsage(input_tokens=315, cache_read_input_tokens=24448)`;
  - uma linha não-JSON vira `ERROR` sem exceção;
  - um `item.updated` repetido com o mesmo texto não duplica `TEXT_DELTA`.
- [x] **Step 2: Rode, veja falhar, implemente e rode de novo.** Esperado: passa.
- [x] **Step 3: Commit.** `feat(codex-cli): JSONL stream translator`

### Task A3: `CodexCliAdapter`, classificação de erro e conformidade

**Files:**
- Create: `src/agentos/providers/codex_cli/adapter.py`
- Create: `src/agentos/providers/codex_cli/errors.py`
- Create: `tests/unit/providers/codex_cli/test_adapter_conformance.py`
- Modify: `src/agentos/providers/cli_registry.py`

**Interfaces:**
- Produces: `CodexCliAdapter`, com `name="codex"` e `capabilities=CliCapabilities(native_tool_loop=True, persistent_stdin=False, reports_tool_inventory=False, forbidden_item_kinds=frozenset({"command_execution", "file_change", "web_search"}), tool_list_changed=<o que a A0 mostrar>, images_in_tool_results=<o que a A0 mostrar>)`.
- Produces: `classify_codex_error(result, rate_limit) -> CliErrorInfo`. Ela olha o texto e o código de saída gravados na A0:

| Erro | Classificação |
|---|---|
| login | `AUTH` |
| limite de uso | `RATE_LIMITED` (com o horário extraído quando houver) |
| modelo desconhecido | `MODEL_NOT_FOUND` |
| contexto | `CONTEXT_OVERFLOW` |
| rede ou 5xx | `TRANSIENT` (retryable) |
| o resto | `FATAL` |

- [x] **Step 1: Teste de conformidade.** Ele instancia `AdapterFixtures(adapter=CodexCliAdapter(), simple_text=…, tool_call=…, error_scenarios=[auth, rate_limit, bad_model])` e roda a suíte compartilhada.
- [x] **Step 2: Rode, veja falhar, implemente e registre** `"openai-codex"` no `cli_registry`.
- [x] **Step 3: Teste de ponta a ponta com uma CLI falsa.** Siga o modelo de `tests/unit/providers/claude_cli/fake_claude.py` e crie `fake_codex.py`, que reproduz os fixtures e chama o MCP real do turno. São dois turnos: o segundo usa `resume` com o `thread_id` do primeiro, e a ferramenta é executada pelo `ToolExecutor` do Orin.
- [x] **Step 4: Rode.** `uv run pytest tests/unit/providers -q`. Esperado: passa no Windows e no Linux.
- [x] **Step 5: Commit.** `feat(codex-cli): adapter, error classification and conformance`

### Task A4: Card de configuração e seletor de modelo do Codex

**Files:**
- Create: `frontend/src/features/providers/CodexSetup.tsx`
- Modify: `frontend/src/features/providers/ProviderDetail.tsx`
- Modify: `frontend/src/features/providers/providerBrand.ts`
- Modify: o endpoint de status das CLIs no gateway (siga o padrão de `check_claude_cli_status` em `src/agentos/provider_catalog/claude_code.py`)
- Test: `frontend/src/features/providers/__tests__/CodexSetup.test.tsx`
- Test: `tests/unit/api/test_codex_cli_status.py`

**Interfaces:**
- Produces: `GET` do status do provedor `openai-codex` com `{installed: bool, version: str | null, supported: bool, authenticated: bool, login_command: "codex login"}`. `authenticated` é a existência de `CODEX_HOME/auth.json`; o conteúdo nunca é lido.

- [x] **Step 1: Testes.**
  - Vitest:
    - sem binário, o card mostra "Instale o Codex CLI";
    - sem `auth.json`, mostra `codex login`;
    - com tudo certo, mostra a versão e "Pronto".
  - pytest: o status nunca lê o conteúdo do `auth.json` (um mock de `open` sobre ele falha o teste).
- [x] **Step 2: Rode, veja falhar, implemente e rode de novo.** Rode `npm run lint`, `npx vitest run` e `npm run build`.
- [x] **Step 3: Commit.** `feat(ui): Codex subscription provider card`

### Task A5: Replay manual (com o Carlos)

- [x] **Step 1:** com autorização, rode uma conversa de 3 turnos com uma ferramenta de leitura, um turno no Orin Code e uma troca de modelo no meio. Registre o resultado no doc do spike.

## Fase C — Antigravity (só depois da decisão D2)

### Task C0: Decisão do Carlos

- [x] **Step 1:** decidido em 2026-11-06: opção (c), assinatura com `agy -p`, desligada por padrão (veja D2).
- [x] **Step 2: Teste do opt-in.** `test_antigravity_disabled_until_risk_acknowledged`: sem o aceite, `cli_adapter_for("google-antigravity", None)` devolve `None`, salvar a configuração é recusado com uma mensagem que pede o aceite, e o modelo não aparece no seletor. O aceite fica salvo na configuração do provedor (`risk_acknowledged_at`), nunca em `localStorage`.

### Task C1: Spike do `agy` e gravação de fixtures

**Files:**
- Create: `docs/agent_memory/2026-11-xx-antigravity-cli-spike.md`
- Create: `tests/unit/providers/antigravity_cli/fixtures/*.jsonl`

A doc deixa estas perguntas em aberto, e o spike precisa respondê-las antes da C2:

- [x] **Step 1: Binário.** Hoje só existe o `Antigravity IDE\bin\antigravity-ide`. Descubra como instalar o `agy` no Windows e exija versão 1.1.1 ou maior: o bug de stdout vazio fora de TTY, issue #76, foi corrigido na 1.0.15 para Windows, e erros silenciosos na 1.1.1.
- [x] **Step 2: System prompt.** Não há flag documentada. Teste se o `AGENTS.md` ou o `GEMINI.md` na pasta de trabalho isolada (`native_workdir("antigravity")`) é lido no modo `-p`.
- [x] **Step 3: MCP.** Não há flag. Confirme que `<workdir>/.agents/mcp_config.json` (`serverUrl` e `headers.Authorization`) é lido no headless e se `${ENV}` é expandido nos headers. Se não for, o token vai no arquivo, que é owner-only e apagado no fim do turno, como o JSON do Claude.
- [x] **Step 4: Permissões sem tocar no `settings.json` global.** Descubra se há settings por workspace que permitam `mcp(orin/*)` e neguem as ferramentas embutidas. Se a única saída for `--dangerously-skip-permissions`, as ferramentas embutidas ficariam liberadas. Nesse caso, a fase para e volta ao Carlos.
- [x] **Step 5: Grave os fixtures** de `stream-json`, com stdin persistente (`--input-format stream-json`):
  - `simple_text`;
  - `tool_call` (um `step_update` com `step_type: "tool"` e `tool_info` contendo chamada e resultado juntos);
  - segundo turno no mesmo processo;
  - erro de modelo;
  - erro sem login.

### Task C2: `AntigravityCliAdapter`

**Files:**
- Create: `src/agentos/providers/antigravity_cli/` (`adapter.py`, `command.py`, `stream.py`, `errors.py`, `catalog.py`), no mesmo formato da Fase A
- Create: `tests/unit/providers/antigravity_cli/test_adapter_conformance.py`
- Modify: `src/agentos/providers/cli_registry.py`

**Interfaces:**
- Produces: `AntigravityCliAdapter`, com `name="antigravity"` e `capabilities=CliCapabilities(native_tool_loop=True, persistent_stdin=True, reports_tool_inventory=True)`. O `init.tools` vem do evento `init`, e o loop exige que ele contenha só ferramentas `orin/*` mais as permitidas pela C1.
- Produces: `AntigravityStreamTranslator`. O mapeamento é:

| Evento do Antigravity | Eventos do Orin |
|---|---|
| `init` | `INIT` |
| `step_update` com `agent_response` e `text_delta` | `TEXT_DELTA` |
| `step_update` com `tool` e `state: DONE` | `TOOL_USE_START`, `TOOL_USE_END` e `TOOL_RESULT` em sequência, com `tool_use_id = f"{conversation_id}:{step_index}"` |
| `result` | `USAGE` e `RESULT` |

  O mapeamento de `usage` é: `input_tokens - cache_read_tokens`, `cache_read_tokens` e `output_tokens`; `thinking_tokens` vai para o raciocínio.
- Produces: `encode_user_turn` no formato `{"event":"user","message":{"content":[{"type":"text","text":…}]}}`. Imagens são recusadas antes do envio com uma mensagem clara, porque o `agy` só aceita blocos de texto e um bloco de outro tipo encerra o processo com código 1.

- [x] **Step 1: Escreva a conformidade e os testes do tradutor com os fixtures da C1.** Inclua:
  - `test_status_waiting_or_canceled_is_not_success`;
  - `test_image_content_rejected_before_spawn`.
- [x] **Step 2: Rode, veja falhar, implemente, registre `"google-antigravity"` e rode de novo.** Esperado: passa.
- [x] **Step 3: Commit.** `feat(antigravity-cli): adapter, translator and conformance`

### Task C3: Card do Antigravity e replay manual

- [x] **Step 1:** crie o `AntigravitySetup.tsx`, com testes iguais aos da A4 e mais um: o botão "Ativar" fica desabilitado até a caixa do aviso ser marcada. O login é "abra `agy` uma vez e entre com sua conta Google". O aviso cita o trecho do FAQ (D2), traz o link e diz que a conta Google pode ser restringida.
- [x] **Step 2:** faça o replay manual com o Carlos, como na A5.
- [x] **Step 3: Commit.** `feat(ui): Antigravity subscription provider card`

---

## Revisão

Ao fim de cada fase (0, A e C), faça uma revisão red team por agentes diferentes dos que implementaram, atacando:
- vazamento do token MCP (argv, logs, arquivos esquecidos);
- ferramenta da CLI que roda fora do MCP;
- o Claude CLI mudando de comportamento;
- testes que só passam num sistema operacional.

Depois, faça a conferência independente: suíte no Windows, Vitest, `tsc -b` e o aceite estático.
