# Relatório Técnico: Diagnóstico e Arquitetura de Correção para Leitura de Arquivos e Imagens no OpenAI Codex CLI (`openai-codex`)

- **Data:** 15 de novembro de 2026
- **Escopo:** Agente Principal e Subagentes (`subagent_type="codex"`) no provedor `openai-codex` (Chat Mode e Code Mode)
- **Ambiente Analisado:** Windows 10 (`10.0.19045`), `codex-cli 0.167.0` (`@openai/codex-win32-x64`), Orin (`main`)
- **Artefatos Forenses Auditados:**
  - Rollouts reais da conversa do usuário em `C:\Users\carlos\.codex\sessions\2026\11\15\`:
    - `rollout-2026-11-15T19-05-23-01a1e13e-5bf7-7100-9753-1eacf86a1f33.jsonl` (Turno 1 — `turn_c2af89d0da2744a592f799b051c5dc36`)
    - `rollout-2026-11-15T19-06-17-01a1e13f-2fa7-7963-ada4-da940dbd1ac3.jsonl` (Turno 2 — `turn_4e094c52bf0f47338f4d027d2b3f549f`)
    - `rollout-2026-11-15T19-06-57-01a1e13f-cf61-7b31-8548-83f3041be48f.jsonl` (Turno 3 — `turn_47fd9cdfeca94adeac0cb31775566c7b`, correspondente à `image (5).png`)
  - Rollout comparativo de subagente Codex em `C:\Users\carlos\.codex\sessions\2026\11\09\rollout-2026-11-09T21-25-28-01a1c2d8-7531-7612-9a03-522986834ffc.jsonl`
  - Banco SQLite local `C:\Users\carlos\AppData\Local\Orin\data\orin.db` (tabelas `provider_model_catalog`, `vision_model_selections`, `conversation_turns`, `conversation_messages`, `user_agents`)
  - Cache local do Codex CLI `C:\Users\carlos\.codex\models_cache.json`
  - Código-fonte Rust oficial do `openai/codex` (`codex-rs/core`, `codex-rs/code-mode-runtime`, `codex-rs/codex-mcp`, `codex-rs/exec`)

---

## 1. Resumo Executivo

A investigação forense nos rollouts reais do Codex CLI (`~/.codex/sessions`), no banco `orin.db` e no código do Orin e do `openai/codex` revelou que o problema relatado pelo usuário (*"os agentes codex não estão conseguindo ver arquivos"* e a mensagem *"tanto `ViewFile` quanto `Read`, mesmo com o caminho completo, retornaram 'imagem omitida'"*) **não é causado por uma limitação intrínseca do modelo GPT-6.1 no Codex CLI**, mas sim por uma **cadeia de 6 falhas de integração entre o catálogo de modelos do Orin, o leitor visual de fallback (`VisionReader`), o sandbox JavaScript V8 (`functions.exec` / `code_mode_only`) do Codex CLI 0.167.0 e o mecanismo de descoberta de ferramentas (`ToolSearch`)**:

1. **Causa-Raiz Primária #1 — Catálogo do `openai-codex` no Orin omite `"image"` em `input_modalities` (`src/agentos/providers/codex_cli/catalog.py:25-74, 174-180` e `src/agentos/provider_catalog/service.py:190`):**
   Embora `~/.codex/models_cache.json` declare explicitamente `"input_modalities": ["text", "image"]` para todos os modelos Codex (`gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`, `gpt-6-astra`, `gpt-6-sol`, `gpt-5.6-*`), `discover_codex_models()` e `FALLBACK_CODEX_MODELS` instanciam `CliModelInfo` com `input_modalities=None`. Ao sincronizar o catálogo para o SQLite (`provider_model_catalog`), `ProviderCatalogService._fetch_provider_records` (`service.py:190`) aplica o fallback `or ("text",)`, gravando `input_modalities = '["text"]'` no banco para todos os modelos `openai-codex`. Consequentemente, `ChatWorker._model_sees_images` (`src/agentos/workers/chat.py:1062-1072`) retorna **`False`** tanto para o agente principal Codex quanto para qualquer subagente `codex`.

2. **Causa-Raiz Primária #2 — Efeito Cascata em `ViewFile`/`Read` → `VisionReader` → `AntigravityCliAdapter` gerando o texto `"imagem omitida"` (`src/agentos/agentic/tools/files/read.py:416-417, 657-679` e `src/agentos/reading/vision.py:33-65`):**
   Com `self.model_sees_images == False`, quando o Codex chama `ViewFile` (ou `Read`, que delega arquivos `image/*` para `self.view_file(path)` em `read.py:416-417`), o Orin **não envia o bloco MCP `ImageContent` ao Codex**. Em vez disso, desvia para `self._visual_read(...)` (`VisionReader.transcribe`). O `VisionReader` seleciona o modelo de visão configurado pelo usuário (`google-antigravity / gemini-4-argon-high`) e chama `ClaudeCliProvider.stream` passando a imagem como base64 inline **sem caminho de arquivo em disco**. O `encode_antigravity_user_turn` remove a imagem inline e injeta `[Imagem/mídia omitida: o Antigravity CLI não recebe imagens na mensagem...]`. O modelo Gemini (rodando dentro do `VisionReader` com `tools=[]`) responde em português que não pôde ler a imagem porque ela foi `"omitida pelo wrapper ([Imagem/mídia omitida...])"`. O `VisionReader` trata essa resposta textual como se fosse a transcrição da imagem e a devolve com `isError: false` para o Codex tanto em `ViewFile` quanto em `Read`!

3. **Causa-Raiz #3 — Armadilha do `text(...)` vs `image(...)` / `content(...)` no `functions.exec` (`ToolMode::CodeModeOnly`) do Codex CLI 0.167.0:**
   No `codex-cli 0.167.0`, todos os modelos têm `"tool_mode": "code_mode_only"` no `models_cache.json`. O Codex não expõe ferramentas MCP diretamente como tool calls normais: ele as encapsula dentro do ambiente JavaScript V8 `functions.exec`. Dentro do `functions.exec`, chamadas como `text(await tools.mcp__orin__Read({file_path: "foto.png"}))` ou `tools.mcp__orin__ViewFile(...).then(text)` (exatamente como o modelo fez no Turno 3 em `rollout-2026-11-15T19-06-57` itens `[18]` e `[28]`) passam o `CallToolResult` para `text_callback` (`codex-rs/code-mode-runtime/src/runtime/callbacks.rs:76-92`), que faz `JSON.stringify` do base64 como **texto puro (`InputText`)**, truncado em 10.000 tokens, **nunca** entregando um `InputImage` ao modelo! Para o modelo enxergar uma imagem vinda de MCP no `functions.exec`, o script JS precisa chamar `image(block)` ou `content(result.content)`.

4. **Causa-Raiz #4 — `build_codex_command` não passa `-i` / `--image <FILE>` para anexos de imagem do turno (`src/agentos/providers/codex_cli/command.py:455-586, 684-725`):**
   O `codex exec` suporta nativamente `-i <FILE>` / `--image <FILE>` tanto no comando inicial (`codex exec -i <path>`) quanto na retomada (`codex exec resume <id> -i <path>`), injetando `UserInput::LocalImage` diretamente no turno da Responses API sem depender de nenhuma tool call. Porém, o Orin nunca passa `-i` em `build_codex_command`.

5. **Causa-Raiz #5 — Dessincronia entre `allow_deferral=False`, `AgentToolset.deferred_tool_names` e `ToolSearch`, somada ao `features.mcp_tool_list_changed=false` no Codex CLI:**
   - Embora `NativeTurnRunner` defina `allow_deferral=False` para o Codex (`tool_list_changed=False` em `CodexCliAdapter`), `AgentToolset.deferred_tool_names` (`src/agentos/agentic/agent_tools.py:431-435`) ignora `allow_deferral` e continua reportando ~40 ferramentas como deferidas.
   - No Codex 0.167.0, o próprio cliente Codex coloca `mcp__orin` em `<tools>\nDeferred tool namespaces:\n- mcp__orin\n</tools>`. Quando o modelo segue o prompt do Orin e chama `mcp__orin__ToolSearch({query: "select:Skill,Glob,Agent"})` (item `[21]` do Turno 3), `search_mcp_tools` (`src/agentos/agentic/tools/mcp/admin.py:218-236`) **omite do JSON `tools` todas as ferramentas de `ALWAYS_LOADED_CODE_TOOLS` (`Glob`, `Agent`, `Read`, `Grep`)**, fazendo parecer que não existem!
   - Além disso, para servidores MCP externos (`playwright`, `clickup`, `rea`), a ativação via `ToolSearch` emite `notifications/tools/list_changed`, mas o Codex 0.167.0 tem `features.mcp_tool_list_changed = false` por padrão (`connection_manager.rs:291, 754`), ignorando a notificação até o fim do processo.

6. **Causa-Raiz #6 — Problemas de Visibilidade de Arquivos do Workspace (Índice vazio do `ProjectMap` em repositórios recém-clonados/atualizados, nomes internos em `_index_building_note` e isolamento de `cwd` em Subagentes Codex):**
   - No Turno 1 (`19:05:23`), antes do `git pull`, o lembrete inicial fixou `## Workspace contents\n- The working directory is currently empty.`
   - No Turno 3 (`19:06:57`), após o `git pull`, o modelo chamou `ProjectMap` e recebeu `[the project index is still being built in the background (0 files so far); results may be partial — use glob_files/search_files for anything missing]\n\n[the project index is empty]` (`src/agentos/agentic/tools/orin/search_code.py:107, 168-170`), que cita os nomes internos `glob_files/search_files` em vez dos nomes públicos `Glob`/`Grep`/`ListFiles` do Code Mode.

---

## 2. Diagnóstico Completo das Causas-Raiz (Passo a Passo no Código)

### 2.1. Reconstrução Forense Exata do Caso `"imagem omitida"` (`image (5).png`)

Abaixo está a cronologia exata reconstruída a partir do banco `orin.db` (conversa `chat_a8588eb702544fe283b05e15f7c95e10`, turnos `168`, `169` e `170`) e dos arquivos de rollout do Codex CLI em `C:\Users\carlos\.codex\sessions\2026\11\15\`:

#### Elo 1: `discover_codex_models` e `FALLBACK_CODEX_MODELS` descartam `input_modalities`
Em `src/agentos/providers/codex_cli/catalog.py`:
- Nas linhas `25–74`, `FALLBACK_CODEX_MODELS` define os 8 modelos `CliModelInfo` sem passar o 5º argumento `input_modalities`, que assume o default `None` (`src/agentos/providers/cli_adapter.py:202`).
- Nas linhas `112–185`, `discover_codex_models()` lê `~/.codex/models_cache.json`. No arquivo local `C:\Users\carlos\.codex\models_cache.json`, **todos** os modelos possuem `"input_modalities": ["text", "image"]`. Porém, `discover_codex_models()` (`catalog.py:173-180`) extrai apenas `id`, `display_name`, `context_window` e `supported_efforts`, deixando `input_modalities=None`:
  ```python
  # src/agentos/providers/codex_cli/catalog.py:173-180
  discovered.append(
      CliModelInfo(
          id=slug,
          display_name=display_name,
          context_window=context_window,
          supported_efforts=supported_efforts,
          # BUG: input_modalities não é preenchido! Fica None.
      )
  )
  ```

#### Elo 2: `ProviderCatalogService` grava `input_modalities = '["text"]'` no SQLite e nunca invalida linhas obsoletas do Codex
Em `src/agentos/provider_catalog/service.py:190`:
```python
# src/agentos/provider_catalog/service.py:190
in_mods = getattr(item, "input_modalities", None) or ("text",)
```
Quando o catálogo do provedor `openai-codex` foi atualizado em `2026-11-14 00:20:45` (registros `id=711..718` na tabela `provider_model_catalog` de `orin.db`), todos os modelos (`gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`, etc.) foram gravados no banco com:
```json
{"provider": "openai-codex", "model_id": "gpt-6.1-sol", "input_modalities": ["text"], "output_modalities": ["text"]}
```
Além disso, em `src/agentos/provider_catalog/service.py:368-370, 393-398, 462-473`, existe a função `_antigravity_modalities_are_stale()` que detecta quando linhas antigas do `google-antigravity` no banco estão só com `["text"]` e força um auto-refresh, mas **não existe `_codex_modalities_are_stale()` para `openai-codex`**.

#### Elo 3: `ChatWorker._model_sees_images` conclui que nenhum modelo Codex enxerga imagens
Em `src/agentos/workers/chat.py:1062-1072`:
```python
def _model_sees_images(self, turn: dict[str, object]) -> bool:
    row = self._catalog_row_for(turn) or {}
    modalities = row.get("input_modalities") or ()
    if isinstance(modalities, str):
        modalities = [item.strip() for item in modalities.split(",")]
    names = {str(item).lower() for item in modalities}
    return not names or "image" in names
```
- Nota histórica crucial: em `2026-11-09` (no rollout `rollout-2026-11-09T21-25-28`), ainda não havia linhas de `openai-codex` na tabela `provider_model_catalog` (e subagentes herdavam `model_sees_images` do agente principal). Como `names` era vazio (`set()`), `not names` avaliava para `True` e o subagente Codex conseguiu ver as 4 imagens!
- Após o refresh do catálogo em `2026-11-14` (que populou `input_modalities = '["text"]'`) e a mudança em `src/agentos/agentic/session.py:3655` (que passou a consultar `self._sees_images_for(active_model_id, provider_name)` também para subagentes), `names` passou a ser `{"text"}`.
- Resultado: `not names or "image" in names` passou a retornar **`False`** para 100% dos turnos do `openai-codex` (agente principal em `chat.py:1848` e subagentes `codex` em `session.py:3655`).

#### Elo 4: `ReadTools._view_image` recusa enviar a imagem ao Codex e aciona o `VisionReader`
Quando o usuário anexou `Imagem do ChatGPT 15 de nov. de 2026 19_00_03 (2).png`, o Orin salvou o arquivo em `.orin/uploads/` e anexou à mensagem do usuário (`src/agentos/conversations/chat.py:155-168`):
```text
[anexos enviados pela pessoa: C:/Users/carlos/Documents/projetos/carreira/portfólio/.orin/uploads/Imagem do ChatGPT 15 de nov. de 2026 19_00_03 (2).png (imagem, 1519 KB)]
Use view_file(path="…") para ler o conteúdo de um anexo visual, ou read_file para texto.
```
O agente Codex (`gpt-6.1-sol`) chamou primeiro `mcp__orin__ViewFile` (item `[20]` de `rollout-2026-11-15T19-06-57`) e depois `mcp__orin__Read` (item `[30]`).
Em `src/agentos/agentic/tools/files/read.py`:
- `read_file` (`linhas 415-417`) detecta `media_type.startswith("image/")` e delega para `self.view_file(path)`.
- `view_file` (`linhas 571-572`) chama `self._view_image(path, target, media_type, question)`.
- `_view_image` (`linhas 652-679`) verifica `if self.model_sees_images:`. Como `self.model_sees_images` é **`False`**, ele **não retorna `"images": [image_block(normalized_type, data)]`**; em vez disso, executa:
  ```python
  # src/agentos/agentic/tools/files/read.py:671
  text = self._visual_read(path, [(data, normalized_type)], question)
  ```

#### Elo 5: `VisionReader` invoca um modelo CLI (`google-antigravity`) via transporte sem arquivo, que omite a imagem e gera a resposta de erro
Em `src/agentos/workers/chat.py:1146-1190` e `src/agentos/reading/vision.py:33-65`:
1. Na tabela `vision_model_selections` do `orin.db`, o usuário tinha selecionado `provider='google-antigravity'`, `model_id='gemini-4-argon-high'` (e mesmo sem override, `_vision_candidates` em `chat.py:1127-1144` inclui modelos de provedores CLI como `google-antigravity`).
2. `VisionReader.transcribe` (`src/agentos/reading/vision.py:42-46`) monta uma mensagem contendo apenas o base64 em memória (`image_block(media_type, data)` — **sem `path` em disco**) e chama `transport.stream({"messages": [{"role": "user", "content": content}], "tools": []})`.
3. O `transport` construído para `google-antigravity` é o modo one-shot clássico do `ClaudeCliProvider`, que serializa o prompt chamando `AntigravityCliAdapter.encode_user_turn` (`src/agentos/providers/antigravity_cli/command.py:725-774`).
4. Como o bloco de imagem enviado pelo `VisionReader` não possui caminho de arquivo local (`_media_block_path(block)` retorna `None`), `encode_antigravity_user_turn` descarta o base64 e escreve no `stdin` do `agy`:
   ```text
   [Imagem/mídia omitida: o Antigravity CLI não recebe imagens na mensagem. Para ver ou ouvir um arquivo do workspace, chame sua ferramenta nativa read_file com o caminho absoluto dele.]
   ```
5. O modelo `gemini-4-argon-high` (executando dentro do `VisionReader`, sem ferramentas MCP do Orin e sem o caminho do arquivo) responde educadamente:
   > *"Não consigo acessar a imagem por dois motivos: 1. Caminho do arquivo não informado: A imagem foi omitida pelo wrapper (`[Imagem/mídia omitida...]`) e nenhum caminho absoluto de arquivo foi incluído na mensagem. 2. Ferramenta `read_file` indisponível nesta sessão..."*
6. O `VisionReader.transcribe` (`vision.py:62-65`) recebe essa resposta de texto (sem erro HTTP), considera a transcrição bem-sucedida e retorna para `ReadTools._view_image`, que a formata como:
   ```text
   Leitura visual de '.orin/uploads/Imagem do ChatGPT 15 de nov. de 2026 19_00_03 (2).png':

   Não consigo acessar a imagem por dois motivos:
   1. **Caminho do arquivo não informado:** A imagem foi omitida pelo wrapper (`[Imagem/mídia omitida...]`)...
   ```
7. Tanto `ViewFile` quanto `Read` retornam exatamente esse texto (com `isError: false`) ao agente Codex, que então reporta ao usuário na `image (5).png`:
   > *"A leitura do anexo continua bloqueada: tanto `ViewFile` quanto `Read`, mesmo com o caminho completo, retornaram 'imagem omitida'."*

---

### 2.2. Segundo Entrave Crítico: Como `functions.exec` (`ToolMode::CodeModeOnly`) no Codex CLI 0.167.0 Processa Imagens de Ferramentas MCP

Mesmo após corrigir `model_sees_images = True` no catálogo do Codex, existe um **segundo bloqueio crítico** no próprio funcionamento do `codex-cli 0.167.0` que foi observado no Turno 3 (`rollout-2026-11-15T19-06-57`):

1. **Todos os modelos Codex operam em `ToolMode::CodeModeOnly` (`functions.exec`):**
   Em `C:\Users\carlos\.codex\models_cache.json`, todos os modelos (`gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`, etc.) trazem `"tool_mode": "code_mode_only"`.
   Conforme verificado em `codex-rs/core/src/tools/mod.rs:69-78`:
   ```rust
   pub(crate) fn requested_tool_mode(turn_context: &TurnContext, model_info: &ModelInfo) -> ToolMode {
       model_info.tool_mode.unwrap_or_else(|| { ... })
   }
   ```
   Isso significa que o Codex CLI **sempre** coloca as ferramentas MCP (`mcp__orin__*`) dentro do isolate V8 da ferramenta `functions.exec`.

2. **Diferença crítica entre `text(...)` e `image(...)` / `content(...)` no V8 do `functions.exec`:**
   No runtime V8 do Codex (`codex-rs/code-mode-runtime/src/runtime/callbacks.rs:76-143` e `value.rs:21-125`):
   - Toda ferramenta MCP chamada em JS (`await tools.mcp__orin__ViewFile(...)` ou `await tools.mcp__orin__Read(...)`) resolve para um objeto JS `CallToolResult`:
     ```json
     {
       "content": [
         {"type": "text", "text": "A imagem '...' está anexada logo abaixo."},
         {"type": "image", "data": "<base64>", "mimeType": "image/jpeg"}
       ],
       "isError": false
     }
     ```
   - Se o modelo escreve no script JS `text(await tools.mcp__orin__Read({file_path: "..."}))` ou `tools.mcp__orin__ViewFile({...}).then(text)` — **exatamente como o modelo escreveu nos itens `[18]` e `[28]` de `rollout-2026-11-15T19-06-57`** — a função `text_callback` (`callbacks.rs:76-92`) executa `serialize_output_text` → `JSON.stringify(result)` e emite um `OutputItem::InputText` contendo a string JSON bruta do base64!
   - Esse `InputText`:
     1. **Não é um `InputImage`** (o codificador visual do modelo nunca é acionado);
     2. Estoura o orçamento de `DEFAULT_MAX_OUTPUT_TOKENS_PER_EXEC_CALL` (`10.000` tokens ≈ 40 KB em `output_render.rs:65`), sendo truncado no meio da string base64!
   - Para que o `functions.exec` emita um `FunctionCallOutputContentItem::InputImage` real para o modelo, o código JS executado pelo modelo **precisa obrigatoriamente** chamar:
     - `content(r.content)` (definido em `codex-rs/code-mode-runtime/src/runtime/content.js:1-19`, que itera sobre `r.content` e converte blocos `type: "text"` em `InputText` e blocos `type: "image"` em `InputImage`), **OU**
     - `for (const c of r.content ?? []) { if (c.type === "image") image(c); else if (c.type === "text") text(c.text); }` (como o subagente fez com sucesso em `rollout-2026-11-09T21-25-28` item `[17]`).

3. **Por que o modelo chamou `.then(text)` em `ViewFile` e `text(await ...Read(...))`?**
   - As descrições públicas de `Read` (`src/agentos/agentic/tools/files/read.py:843`) e `ViewFile` (`read.py:850`) e a nota `isolated_cli_note` (`src/agentos/providers/claude_cli/native_loop.py:115-139`) **não informam** ao modelo Codex que, dentro de `functions.exec`, chamadas a `ViewFile`/`Read` para imagens retornam blocos MCP `ImageContent` em `result.content` que devem ser passados para `image(block)` ou `content(result.content)` (e nunca para `text(result)`).
   - Além disso, em `read.py:842`, `read_file` declara `output_model=ReadOutput` (schema estruturado do Claude Code), induzindo o modelo a achar que `Read` retorna um JSON estruturado de texto em vez de `ImageContent` em `result.content`.

---

### 2.3. Terceiro Entrave: Falta de Envio Direto de Anexos de Imagem via `-i` / `--image <FILE>` no `codex exec`

Em `src/agentos/providers/codex_cli/command.py`:
- Na linha `510`, `build_codex_command` fixa `"features.view_image=false"`.
- Nas linhas `684-725`, `encode_codex_user_turn` substitui blocos de mídia por texto (`[Imagem/mídia no arquivo: {path}...]`), e no fluxo normal de chat/code (`src/agentos/conversations/chat.py:148-168`), anexos enviados pelo usuário são gravados em `.orin/uploads/` e anunciados apenas como texto no final da mensagem.
- No entanto, o binário `codex exec` possui suporte nativo de primeira classe à flag `-i` / `--image <FILE>` **tanto na criação da sessão quanto no `codex exec resume <SESSION_ID> -i <FILE>`** (confirmado em `codex-rs/exec/src/cli.rs:153-155, 245-253` e `codex-rs/exec/src/lib.rs:1001-1070`).
- Quando o arquivo de imagem é passado via `-i <caminho_absoluto>`, o próprio `codex` em Rust lê o arquivo local, redimensiona/sanitiza em `image_preparation.rs` e o inclui diretamente como `UserInput::LocalImage` na mensagem do usuário enviada à API da OpenAI — **sem precisar de nenhuma chamada de ferramenta MCP**!

---

### 2.4. Quarto Entrave: Deferral de Ferramentas (`ToolSearch`), `allow_deferral=False` e `features.mcp_tool_list_changed=false`

Quando um agente ou subagente Codex tenta descobrir e chamar ferramentas no modo Code (ou servidores MCP externos como `playwright`), ocorrem três conflitos simultâneos:

1. **O próprio Codex CLI 0.167.0 oculta o namespace `mcp__orin` no prompt inicial:**
   Como `model_info.supports_search_tool == true` e `tool_mode == "code_mode_only"` (`codex-rs/core/src/tools/spec_plan.rs:1070-1135`), o Codex CLI não lista as declarações TypeScript das ferramentas `mcp__orin__*` no bloco `<tools>` inicial; ele injeta apenas:
   ```xml
   <tools>
   Deferred tool namespaces:
   - mcp__orin
   </tools>
   ```
   Todas as ferramentas que o `TurnToolServer` retornou no `tools/list` inicial ficam disponíveis no objeto global JS `tools` e no array `ALL_TOOLS` dentro de `functions.exec`.

2. **Conflito entre `allow_deferral=False` no `NativeTurnRunner` e `AgentToolset.deferred_tool_names` no `ToolSearch`:**
   - Em `src/agentos/providers/codex_cli/adapter.py:44`, `CodexCliAdapter.capabilities.tool_list_changed` é `False`.
   - Por isso, `NativeTurnRunner._run` (`src/agentos/agentic/native_turn.py:2169-2191`) define `allow_deferral = False`, e `AgenticTurnRuntime._tool_schemas` (`src/agentos/agentic/runtime.py:1631-1637`) envia **todas as 60+ ferramentas de `CODE_MODE_CORE_TOOLS`** já no `tools/list` inicial do `TurnToolServer`.
   - **Porém**, `AgentToolset.deferred_tool_names` (`src/agentos/agentic/agent_tools.py:431-435`) **não recebe nem verifica `allow_deferral`**:
     ```python
     # src/agentos/agentic/agent_tools.py:431-435
     @property
     def deferred_tool_names(self) -> frozenset[str]:
         if not self._code_mode_active:
             return frozenset()
         defined = {item.name for item in self.definitions()}
         return frozenset((defined & CODE_MODE_CORE_TOOLS) - ALWAYS_LOADED_CODE_TOOLS - self._discovered_tool_names)
     ```
   - Como o prompt do Orin instrui o modelo a usar `ToolSearch(query="select:...")` e o Codex vê `Deferred tool namespaces: - mcp__orin`, o modelo Codex chama `tools.mcp__orin__ToolSearch({query: "select:Skill,Glob,Agent"})` (como ocorreu no item `[21]` de `rollout-2026-11-15T19-06-57`).
   - Em `McpTools.search_mcp_tools` (`src/agentos/agentic/tools/mcp/admin.py:206-237`), quando o modelo pede `select:Skill,Glob,Agent`:
     - `Glob` (`glob_files`) e `Agent` (`run_subagent`) pertencem a `ALWAYS_LOADED_CODE_TOOLS`, portanto **não** estão em `native_by_internal` (que só indexa `self.deferred_tool_names`).
     - `search_mcp_tools` coloca `Glob` e `Agent` na lista `already_available`, mas **inclui `already_available` apenas em `tool_output` (metadado de UI), e NÃO no `payload` / `content` JSON retornado ao modelo (`admin.py:291-303`)**!
     - Para o modelo, o JSON retornado por `ToolSearch` tem `"count": 1, "activated": ["Skill"], "tools": [{"name": "Skill", ...}]` — **omitindo completamente os schemas de `Glob` e `Agent`** (e na segunda chamada para `select:Skill`, no item `[31]`, retorna `"count": 0, "tools": []` porque `Skill` já entrou em `_discovered_tool_names`!).

3. **Ferramentas de servidores MCP externos (`playwright`, `clickup`, `rea`) ativadas via `ToolSearch` nunca entram em `ALL_TOOLS` / `tools` do Codex se `features.mcp_tool_list_changed=false`:**
   - Diferente de `CODE_MODE_CORE_TOOLS`, as ferramentas de servidores MCP externos (`mcp__playwright__*`, etc.) **não** são incluídas no `tools/list` inicial mesmo quando `allow_deferral=False` (`src/agentos/agentic/runtime.py:1641-1643` só inclui `self.toolset.active_mcp_tool_names`, que começa vazio).
   - Quando o agente Codex chama `ToolSearch` ou `InspectMcpServer(activate_all=True)`, o Orin adiciona as ferramentas em `active_mcp_tool_names` e o `TurnToolServer` (`src/agentos/providers/cli_tools_mcp/server.py:489-500`) envia a notificação SSE `notifications/tools/list_changed`.
   - Contudo, no binário `codex-cli 0.167.0`, a feature `mcp_tool_list_changed` está **`false` por padrão** (`mcp_tool_list_changed  under development  false` em `codex features list`). Em `codex-rs/codex-mcp/src/connection_manager.rs:290-293`:
     ```rust
     async fn refresh_notified_catalog(&self, server_name: &str, client: &ManagedClient) {
         if !self.refresh_tool_list || self.connection.client.is_codex_apps_mcp_server {
             return;
         }
     ```
   - Sem `-c features.mcp_tool_list_changed=true`, o Codex CLI **jamais recarrega `tools/list`** durante o turno!

---

### 2.5. Quinto Entrave: Localização de Arquivos do Workspace e Subagentes `codex` (`subagent_type="codex"`)

Além da leitura de imagens, a investigação identificou quatro pontos que prejudicam agentes e subagentes Codex ao localizar e ler arquivos gerais do projeto:

1. **`ProjectMap` e `SearchCode` em repositórios recém-clonados ou ainda indexando (`src/agentos/agentic/tools/orin/search_code.py:107, 168-170`):**
   - No Turno 1 (`19:05:23`), o repositório local estava vazio antes do `git pull`, então o lembrete inicial (`first_user_context`) registrou `## Workspace contents\n- The working directory is currently empty.`
   - No Turno 2 (`19:06:17`), o agente executou `git pull origin main` via `Bash`, trazendo todos os arquivos do projeto.
   - No Turno 3 (`19:06:57`), o agente chamou `mcp__orin__ProjectMap({max_tokens: 2200})` para conhecer a estrutura do projeto e recebeu:
     ```text
     [the project index is still being built in the background (0 files so far); results may be partial — use glob_files/search_files for anything missing]

     [the project index is empty]
     ```
   - Repare em dois problemas:
     - A mensagem em `_index_building_note` (`search_code.py:168-170`) recomenda `use glob_files/search_files` (nomes internos) sem passar por `public_text(...)`, embora no Code Mode as ferramentas se chamem `Glob` e `Grep`.
     - Quando o índice vetorial ainda está vazio (`0 files so far`), `project_map` retorna apenas `[the project index is empty]` em vez de fornecer um fallback imediato com a listagem de arquivos de topo do workspace (`self.workspace.list_entries(depth=2)`).

2. **Descoberta de ferramentas em `functions.exec` por subagentes `codex` (Chat Mode vs Code Mode):**
   - Em `src/agentos/agentic/session.py:4962`, `public_tool_names` só é `True` por padrão quando `self.turn.get("code_mode") == "code"`.
   - Quando um subagente `codex` é disparado a partir do **Chat Mode**, as ferramentas no servidor MCP `orin` usam os nomes internos (`mcp__orin__read_file`, `mcp__orin__view_file`, `mcp__orin__list_files`, `mcp__orin__glob_files`, `mcp__orin__search_files`), enquanto no **Code Mode** usam os nomes públicos (`mcp__orin__Read`, `mcp__orin__ViewFile`, `mcp__orin__ListFiles`, `mcp__orin__Glob`, `mcp__orin__Grep`).
   - Como o Codex 0.167.0 oculta o namespace `mcp__orin` atrás de `Deferred tool namespaces: - mcp__orin`, o subagente precisa primeiro rodar um turno de `functions.exec` inspecionando `ALL_TOOLS` (como visto em todos os rollouts de subagentes: `const ts=ALL_TOOLS.filter(...); text(ts);`), gastando uma rodada inteira apenas para descobrir os nomes e assinaturas das ferramentas de leitura do Orin.

3. **Diretório de trabalho (`cwd`) compartilhado entre processos concorrentes do Codex (`src/agentos/providers/claude_cli/native_loop.py:1387-1393`):**
   - Em `native_loop.py:1387-1393`, `uses_cwd_wiring` só cria um diretório por processo (`create_process_cwd`) para `antigravity`. Para `codex`, todos os processos principais e subagentes `codex` compartilham o mesmo diretório `C:\Users\carlos\AppData\Local\Orin\data\codex-cli\workdir`.

4. **Mensagens `<multi_agent_role>` e `<skills_instructions>` ainda injetadas pelo Codex 0.167.0:**
   - Nos rollouts de `2026-11-15` (linhas `[4]`, `[5]` e `[6]` de `rollout-2026-11-15T19-05-23.jsonl`), mesmo com `CODEX_NEUTRAL_CONFIG`, o Codex 0.167.0 ainda injeta mensagens `developer` com `<skills_instructions>` e `<multi_agent_role>You are /root, the primary agent in a team of agents...</multi_agent_role>`.

---

## 3. Evidências do Binário Local `codex` e Pesquisa Web (`openai/codex` & MCP)

### 3.1. Inspeção do Binário Local e Cache de Modelos
- **Binário resolvido (`resolve_codex_binary`):**
  `C:\Users\carlos\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`
- **Versão reportada (`codex --version`):** `codex-cli 0.167.0`
- **Flags relevantes de `codex exec --help` e `codex exec resume --help`:**
  - `-i, --image <FILE>...`: *"Optional image(s) to attach to the initial prompt"* (também aceito em `codex exec resume <SESSION_ID> --image <FILE>...`: *"Optional image(s) to attach to the prompt sent after resuming"*).
  - `-C, --cd <DIR>`: *"Tell the agent to use the specified directory as its working root"*.
  - `--add-dir <DIR>`: *"Additional directories that should be writable alongside the primary workspace"*.
- **Flags relevantes em `codex features list` (`0.167.0`):**
  - `view_image`: `stable | true` (desabilitado pelo Orin via `-c features.view_image=false`).
  - `image_generation`: `stable | true` (desabilitado pelo Orin via `-c features.image_generation=false`).
  - `mcp_tool_list_changed`: `under development | false` (**desabilitado por padrão no Codex 0.167.0**).
  - `code_mode_host`: `stable | true`.
- **Inspeção de `C:\Users\carlos\.codex\models_cache.json`:**
  Todos os modelos visíveis (`gpt-6.1-sol`, `gpt-6-astra`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`) possuem:
  ```json
  {
    "input_modalities": ["text", "image"],
    "supports_image_detail_original": true,
    "supports_search_tool": true,
    "tool_mode": "code_mode_only"
  }
  ```

### 3.2. Como o Código Rust do `openai/codex` Processa Imagens e MCP (Fontes Oficiais)

1. **Sanitização de `CallToolResult` MCP por modalidade do modelo (`codex-rs/core/src/mcp_tool_call.rs:1118-1155`):**
   - URL: `https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/mcp_tool_call.rs`
   - Função `sanitize_mcp_tool_result_for_model(input_modalities: &[InputModality], result: Result<CallToolResult, String>)`:
     Verifica `input_modalities.contains(&InputModality::Image)` (lido do `models_cache.json` do próprio Codex). Como todos os modelos no `models_cache.json` do Codex têm `["text", "image"]`, o Codex CLI **não** substitui blocos `{"type": "image"}` de ferramentas MCP por `<image content omitted because you do not support image input>`.

2. **Preparação e Redimensionamento de Imagens de Mensagens e Tool Outputs (`codex-rs/core/src/image_preparation.rs:1-420`):**
   - URL: `https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/image_preparation.rs`
   - `prepare_response_items` processa tanto `ResponseItem::Message` (imagens do usuário via `-i`) quanto `ResponseItem::FunctionCallOutput` e `ResponseItem::CustomToolCallOutput` (imagens retornadas por ferramentas ou `functions.exec`).
   - Só substitui uma imagem por `"image content omitted..."` em 4 casos de erro de validação da própria imagem:
     1. URL remota `http://` ou `https://` (`"image content omitted because remote image URLs are not supported"`);
     2. `detail: "low"` sem `unified_budget` (`"image content omitted because detail 'low' is not supported; use 'high', 'original', or 'auto'"`);
     3. Imagem acima de `MAX_SANITIZED_IMAGE_BYTES` / limite de pixels (`"image content omitted because it exceeded the supported size limit; use a smaller image"`);
     4. Bytes corrompidos (`"image content omitted because it could not be processed"`).

3. **Como `functions.exec` (`code-mode-runtime`) lida com `CallToolResult` e `ImageContent` (`codex-rs/code-mode-runtime/src/runtime/`):**
   - URLs:
     - `https://raw.githubusercontent.com/openai/codex/main/codex-rs/code-mode-runtime/src/runtime/globals.rs`
     - `https://raw.githubusercontent.com/openai/codex/main/codex-rs/code-mode-runtime/src/runtime/callbacks.rs`
     - `https://raw.githubusercontent.com/openai/codex/main/codex-rs/code-mode-runtime/src/runtime/value.rs`
     - `https://raw.githubusercontent.com/openai/codex/main/codex-rs/code-mode-runtime/src/runtime/content.js`
     - `https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tools/code_mode/response_adapter.rs`
   - Em `callbacks.rs:76-92` (`text_callback`) e `value.rs:21-38` (`serialize_output_text`), qualquer objeto passado para `text(obj)` é convertido em string via `JSON.stringify` e emitido como `OutputItem::InputText`.
   - Em `callbacks.rs:124-138` (`image_callback`) e `value.rs:138-181` (`parse_mcp_media_block`), passar um bloco MCP `{"type": "image", "data": "<base64>", "mimeType": "image/jpeg"}` para `image(block)` (ou passar `result.content` para `content(result.content)` em `content.js`) emite `OutputItem::InputImage { image_url: "data:image/jpeg;base64,...", detail }`, que `response_adapter.rs:24-28` transforma em `FunctionCallOutputContentItem::InputImage` para o modelo!
   - Isso foi **comprovado empiricamente** no rollout local `rollout-2026-11-09T21-25-28-01a1c2d8-7531-7612-9a03-522986834ffc.jsonl` (itens `[17]`, `[23]` e `[28]`), onde `image(c)` emitiu `{"type": "input_image", "image_url": "data:image/jpeg;base64,..."}` mesmo com `features.view_image=false`, e o modelo Codex analisou visualmente 4 imagens com perfeição!

4. **Como `view_image` nativo do Codex funciona (`codex-rs/core/src/tools/handlers/view_image.rs:134-285`):**
   - URL: `https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tools/handlers/view_image.rs`
   - A ferramenta nativa `view_image` do Codex é 100% somente-leitura e valida os bytes da imagem (`image::load_from_memory` / `resize_image`), rejeitando qualquer arquivo que não seja imagem válida (`"unable to process image: invalid or unsupported image data"`).
   - Porém, na linha `148`, `view_image` resolve caminhos relativos contra `turn_environment.cwd()` (que no Orin é a pasta isolada vazia `codex-cli/workdir`), e na linha `214` emite `TurnItem::ImageView` (`item_type = "image_view"` ou `"view_image_tool_call"`). Portanto, manter a leitura via ferramentas MCP do Orin (`ViewFile` / `Read`) + flag `-i <FILE>` é muito mais consistente com o controle de workspace do Orin.

5. **Como `notifications/tools/list_changed` é tratado no Codex (`codex-rs/codex-mcp/src/connection_manager.rs:290-334, 754, 821`):**
   - URL: `https://raw.githubusercontent.com/openai/codex/main/codex-rs/codex-mcp/src/connection_manager.rs`
   - O cliente MCP do Codex (`rmcp_client`) rastreia `tool_list_revision()` ao receber `notifications/tools/list_changed`, mas `refresh_notified_catalog` (`linha 291`) verifica `if !self.refresh_tool_list { return; }`, que depende de `config.mcp_tool_list_changed` (`features.mcp_tool_list_changed=true`).

---

## 4. Análise de Segurança e Isolamento

Para permitir que agentes e subagentes Codex vejam arquivos do projeto e imagens/anexos **sem abrir brechas de segurança**:

1. **Manter `sandbox_mode="read-only"` e `approval_policy="never"` (`src/agentos/providers/codex_cli/command.py:517-518`):**
   - Como documentado no plano de 12/11/2026, dentro de `functions.exec` o Codex expõe a ferramenta nativa `apply_patch` mesmo quando `features.shell_tool=false`.
   - Manter `sandbox_mode="read-only"` e `approval_policy="never"`, junto com a execução a partir do diretório isolado vazio (`codex-cli/workdir`), garante que o `apply_patch` nativo do Codex seja bloqueado pelo sandbox do Codex caso o modelo tente invocá-lo, forçando todas as escritas (`Write`, `Edit`) e execuções (`Bash`) a passarem pelo `TurnToolServer` e pelo `PermissionBroker` do Orin.
   - **Não devemos usar `--cd <workspace>` nem `--add-dir <workspace>`**, pois em modos restritos (`plan_mode`, `ask_permissions`, subagentes `read_only`) isso exporia o diretório ao contexto nativo do Codex.

2. **Segurança ao passar `-i` / `--image <FILE>` no `codex exec`:**
   - Passar `-i <FILE>` na linha de comando do `codex exec` (e `codex exec resume`) envia apenas os arquivos de imagem que **já fazem parte da mensagem atual do usuário** (validados dentro do workspace/uploads da conversa ou autorizados pela política do workspace).
   - O caminho deve ser validado por `_validate_path_arg` (rejeitando paths que comecem com `-`, caracteres decontrole `\x00`, `\r`, `\n`, symlinks ou caminhos fora do workspace quando `workspace.unrestricted` for `False`).

3. **Segurança no `VisionReader`:**
   - O `VisionReader` é um leitor auxiliar para quando o modelo principal do turno **realmente** não possui visão (ex.: um modelo puramente textual no Ollama/OpenRouter ou `gpt-oss-120b` no Antigravity).
   - ` _vision_candidates` (`src/agentos/workers/chat.py:1127-1144`) **não deve selecionar provedores CLI baseados em subprocesso (`google-antigravity`, `openai-codex`)** para o transporte `HTTPProviderStreamTransport` do `VisionReader` a menos que o `VisionReader` use um cliente dedicado que passe o arquivo em disco (ou devemos filtrar `google-antigravity` e `openai-codex` de `_vision_candidates` / `_vision_override` no `VisionReader` clássico, ou suportar passagem de arquivo local).

---

## 5. Formas de Resolver (Comparativo de Opções Arquiteturais)

### Opção A: Correção do Catálogo de Modalidades do Codex + Instruções de `image()`/`content()` para `functions.exec` + Correção do `ToolSearch`
- **Como funciona:**
  1. Atualizar `src/agentos/providers/codex_cli/catalog.py` (`discover_codex_models` e `FALLBACK_CODEX_MODELS`) para incluir `input_modalities=("text", "image")` (lendo `entry.get("input_modalities")` do `models_cache.json` com fallback `("text", "image")`).
  2. Adicionar `_codex_modalities_are_stale()` em `src/agentos/provider_catalog/service.py` (análogo a `_antigravity_modalities_are_stale`) para atualizar automaticamente no banco `orin.db` as linhas existentes de `openai-codex` que estão com `input_modalities = '["text"]'`.
  3. Instruir explicitamente no prompt (`isolated_cli_note` / `Read` / `ViewFile` / `_attachment_context_note`) que, no Codex (`functions.exec`), para ver imagens retornadas por `ViewFile` ou `Read`, o script JS deve usar `for (const b of res.content ?? []) { if (b.type === "image") image(b); else if (b.type === "text") text(b.text); }` (ou `content(res.content)`), e nunca `text(res)`.
  4. Corrigir `AgentToolset.deferred_tool_names` e `McpTools.search_mcp_tools` (`ToolSearch`) para incluir ferramentas `already_available` no payload JSON quando chamadas com `select:...` e sincronizar `allow_deferral`.
- **Prós:** Resolve tanto anexos do usuário quanto imagens lidas dinamicamente no meio do turno (ex.: screenshots tirados pelo Playwright, imagens geradas em `.orin/`, assets do repositório), funciona em turnos novos, `resume` e subagentes `codex`.
- **Contras:** Para anexos enviados junto com a mensagem do usuário, o modelo ainda precisa fazer 1 chamada de ferramenta (`ViewFile`/`Read`) se `-i` não for passado.

### Opção B: Passar Anexos de Imagem da Mensagem do Usuário via `-i <FILE>` (`--image`) no `codex exec` / `codex exec resume`
- **Como funciona:**
  - Extrair os caminhos de imagens anexadas na mensagem atual do usuário e passá-los como argumentos `-i <caminho_absoluto>` em `build_codex_command` (`codex exec ... -i <file> -` e `codex exec resume <id> ... -i <file> -`).
- **Prós:** O modelo Codex recebe a imagem anexada pelo usuário já no primeiro turno da API (zero tool calls necessárias, zero risco de o modelo escrever `.then(text)` errado no `functions.exec`), compatível com `exec` inicial e `exec resume`.
- **Contras:** Só cobre imagens anexadas na mensagem do usuário; não cobre imagens que o agente descobre sozinho no workspace durante o turno (ex.: `portfolio/screenshots/final-hero-1440.png` ou um subagente `codex` que recebe apenas o caminho da imagem no prompt de delegação `Agent(prompt="Analise .orin/uploads/ref.png")`).

### Opção C: Habilitar `features.view_image=true` Nativo do Codex CLI
- **Como funciona:**
  - Mudar `features.view_image=false` para `features.view_image=true` em `build_codex_command` e permitir `image_view` / `view_image` no stream translator e `native_loop.py`.
- **Prós:** Dá ao Codex uma função dedicada `tools.view_image({path: "..."})` no `functions.exec`.
- **Contras:**
  1. `view_image` do Codex resolve caminhos relativos contra o `cwd` do processo (`codex-cli/workdir`, que é uma pasta vazia isolada), falhando em qualquer caminho relativo do workspace (`.orin/uploads/img.png` ou `src/assets/logo.png`)!
  2. Não passa pelo controle de workspace (`ConversationWorkspace.resolve`) do Orin, permitindo que o modelo leia imagens fora do workspace mesmo quando `workspace.unrestricted` é `False`.
  3. Dentro de `functions.exec`, `ViewImageOutput::code_mode_result` (`view_image.rs:276-285`) retorna `{ image_url, detail }`, que **também** exige que o modelo chame `image(await tools.view_image(...))`!

### Conclusão Comparativa: **Combinação de Opção A + Opção B**
A **combinação da Opção A com a Opção B** (mantendo `features.view_image=false` e `sandbox_mode="read-only"`) é a arquitetura ideal:
- A **Opção A** garante que `model_sees_images` seja `True` para todos os modelos Codex (principais e subagentes), que `ViewFile` e `Read` devolvam o bloco `ImageContent` real via MCP, que o modelo saiba usar `image(block)`/`content(res.content)` no `functions.exec`, que o `VisionReader` nunca produza falsas transcrições `"imagem omitida"`, e que `ToolSearch` e `ProjectMap` funcionem sem atrito.
- A **Opção B** garante que imagens anexadas diretamente na mensagem do usuário também já entrem via `-i <FILE>` no `codex exec` (novo ou `resume`), permitindo que o modelo as enxergue imediatamente mesmo antes de chamar qualquer ferramenta.

---

## 6. Arquitetura Recomendada Passo a Passo (Plano de Implementação)

### Passo 1: Corrigir `input_modalities` no Catálogo do Codex (`src/agentos/providers/codex_cli/catalog.py`)
1. Criar a função `codex_input_modalities(slug: str, raw_modalities: Any = None) -> tuple[str, ...]`:
   - Para `codex-image` (`CODEX_IMAGE_MODEL_ID`), retornar `("text",)`.
   - Se `raw_modalities` for uma sequência no `models_cache.json` (ex.: `["text", "image"]`), extrair e normalizar as modalidades válidas garantindo `("text", "image")` quando `"image"` estiver presente.
   - Por padrão, para qualquer modelo de chat/código do Codex (`gpt-*`, `codex-*`), retornar `("text", "image")`.
2. Em `FALLBACK_CODEX_MODELS` (`src/agentos/providers/codex_cli/catalog.py:25-74`), definir `input_modalities=("text", "image")` em todos os `CliModelInfo`.
3. Em `discover_codex_models()` (`src/agentos/providers/codex_cli/catalog.py:170-180`), passar `input_modalities=codex_input_modalities(slug, entry.get("input_modalities"))` ao instanciar `CliModelInfo`.

### Passo 2: Auto-Refresh de Modalidades Defasadas do `openai-codex` no Banco (`src/agentos/provider_catalog/service.py`)
1. Em `src/agentos/provider_catalog/service.py`:
   - Criar `_codex_modalities_are_stale(items: Sequence[ProviderModelRecord]) -> bool` (espelhando `_antigravity_modalities_are_stale` nas linhas `462-473`):
     ```python
     def _codex_modalities_are_stale(items: Sequence[ProviderModelRecord]) -> bool:
         """True when a stored Codex row's input modalities lag the current catalog."""
         from agentos.providers.codex_cli.catalog import codex_input_modalities
         return any(
             set(codex_input_modalities(item.model_id)) - set(item.input_modalities)
             for item in items
             if not item.is_custom and "text" in item.output_modalities
         )
     ```
   - Em `_try_auto_refresh` (`linhas 368-372`) e `list` (`linhas 393-398`), verificar `_codex_modalities_are_stale(items)` para `normalized_provider == "openai-codex"`, forçando o refresh imediato das linhas de `provider_model_catalog` que foram gravadas anteriormente com `input_modalities = '["text"]'`.
   - Em `add_custom` (`linhas 441-445`), se `normalized_provider == "openai-codex"`, usar `resolved_input = codex_input_modalities(normalized_id)`.
2. Em `src/agentos/workers/chat.py:1062-1072` (`_model_sees_images`):
   - Como defesa em profundidade, se `turn.get("provider") == "openai-codex"` e a linha do banco ainda estiver com `["text"]` antes de um refresh de UI, consultar `codex_input_modalities(model_id)` (ou atualizar a leitura em `_model_sees_images` para considerar `codex_input_modalities` e `antigravity_input_modalities`), garantindo que **mesmo sem abrir a tela de modelos antes do turno**, `ChatWorker._model_sees_images` já retorne `True` imediatamente!

### Passo 3: Proteger `VisionReader` contra Provedores CLI que Não Aceitam Base64 Inline Sem Arquivo (`src/agentos/workers/chat.py` e `src/agentos/reading/vision.py`)
1. Em `ChatWorker._vision_candidates` (`src/agentos/workers/chat.py:1127-1144`) e `_vision_override` (`linhas 1146-1156`):
   - Filtrar provedores CLI (`google-antigravity`, `openai-codex`) que usam `ClaudeCliProvider.stream` no modo texto one-shot (pois `encode_antigravity_user_turn` e `encode_codex_user_turn` descartam blocos de imagem base64 sem caminho de arquivo em disco), **OU** fazer `VisionReader.transcribe` (`src/agentos/reading/vision.py:33-65`) aceitar o `path: Path | None` (ou gravar um arquivo temporário quando o transporte for CLI) e detectar respostas que contenham `[Imagem/mídia omitida` lançando `VisionUnavailable` em vez de retorná-las como se fossem transcrições válidas.

### Passo 4: Ensinar ao Codex o Uso de `image()` / `content()` no `functions.exec` e Ajustar `Read` / `ViewFile` (`src/agentos/providers/claude_cli/native_loop.py` e `src/agentos/agentic/tools/files/read.py`)
1. Em `src/agentos/providers/claude_cli/native_loop.py:114-139` (`isolated_cli_note`):
   - Adicionar orientação concisa para o Codex (`functions.exec`):
     - Todas as ferramentas do Orin já estão registradas em `ALL_TOOLS` e `tools.mcp__orin__*` dentro de `functions.exec` (para inspecionar schemas, filtre `ALL_TOOLS` diretamente em JS em vez de chamar `ToolSearch` para ferramentas nativas como `Read`, `Glob`, `Grep`, `ListFiles`, `ViewFile`, `Bash`, `Edit`, `Write`, `Agent`, `Skill`).
     - Para visualizar imagens com `ViewFile` ou `Read` dentro de `functions.exec`, **nunca passe o resultado para `text(...)`** (pois `text()` serializa o objeto como JSON textual truncado); use sempre `for (const b of res.content ?? []) { if (b.type === "image") image(b); else if (b.type === "text") text(b.text); }` (ou `content(res.content)`).
2. Em `src/agentos/agentic/tools/files/read.py:665-670` (`_view_image`):
   - O texto retornado junto com o bloco `ImageContent` (`"A imagem '{path}' está anexada logo abaixo."`) funciona perfeitamente quando o script JS repassa `image(b)`.
   - Em `ReadTools.SPECS` (`read.py:842`), revisar `output_model=ReadOutput` ou a descrição pública de `Read` e `ViewFile` para deixar claro que para imagens o retorno MCP traz um bloco `ImageContent` (`type: "image"`) em `content`.

### Passo 5: Passar Anexos de Imagem da Mensagem via `-i` / `--image` em `build_codex_command` (`src/agentos/providers/codex_cli/command.py` e `src/agentos/providers/claude_cli/native_loop.py`)
1. Em `CliTurnRequest` (`src/agentos/providers/cli_adapter.py`) e `start_native_turn_for_provider` (`src/agentos/providers/claude_cli/native_loop.py:1483-1507`):
   - Extrair os caminhos locais de imagens presentes em `request.user_content` (tanto de blocos de imagem com `path`/`file_path` quanto de anexos do turno atual dentro do workspace).
2. Em `build_codex_command` (`src/agentos/providers/codex_cli/command.py:455-586`):
   - Para cada caminho de imagem válido e existente no disco, adicionar `["-i", validated_image_path]` antes do `"-"` final (funciona tanto em `codex exec` quanto em `codex exec resume <id>`).

### Passo 6: Corrigir `ToolSearch`, `deferred_tool_names`, `mcp_tool_list_changed` e `ProjectMap`
1. **`AgentToolset.deferred_tool_names` (`src/agentos/agentic/agent_tools.py:431-435`) e `McpTools.search_mcp_tools` (`src/agentos/agentic/tools/mcp/admin.py:206-304`):**
   - Quando `search_mcp_tools` é chamado com `select:Name1,Name2` e uma ferramenta solicitada já está carregada (`is_already_available` — ex.: `Glob`, `Agent`, `Read`, `Grep`, ou qualquer ferramenta já descoberta em `_discovered_tool_names`), **incluir seu descritor e `input_schema` na lista `tools` do payload JSON** (e incluir `"already_available": already_available` no `payload`), em vez de retornar `tools: []`!
2. **Ativar `features.mcp_tool_list_changed=true` no Codex CLI (`src/agentos/providers/codex_cli/command.py:393-409` e `adapter.py:44`):**
   - Adicionar `"features.mcp_tool_list_changed=true"` em `CODEX_NEUTRAL_CONFIG` (ou `base_c_values`) para que o Codex CLI 0.167.0 processe notificações `notifications/tools/list_changed` quando servidores MCP externos (`playwright`, `clickup`, etc.) forem ativados durante o turno.
3. **Melhorar `_index_building_note` e fallback de `ProjectMap` (`src/agentos/agentic/tools/orin/search_code.py:106-124, 158-170`):**
   - Passar a mensagem de `_index_building_note` por `public_text(...)` quando `self.name_translator is not None` (para que no Code Mode ela diga `use Glob/Grep` em vez de `use glob_files/search_files`).
   - Quando `entries` for vazio (`[the project index is empty]`), incluir um resumo dos arquivos/pastas de topo do workspace (`self.workspace.list_entries(depth=2)[:40]`) para que o modelo veja imediatamente os arquivos do projeto mesmo enquanto o indexador em background ainda está escaneando.

### Passo 7: Plano de Testes Unitários e de Integração
1. `tests/unit/providers/codex_cli/test_catalog.py`:
   - Verificar que `discover_codex_models()` extrai `input_modalities=("text", "image")` do `models_cache.json` e que `FALLBACK_CODEX_MODELS` tem `input_modalities=("text", "image")` para todos os modelos de texto/código e `("text",)` para `codex-image`.
2. `tests/unit/provider_catalog/test_service.py`:
   - Verificar que `_codex_modalities_are_stale` detecta registros `openai-codex` no banco com `input_modalities=("text",)` e dispara o auto-refresh atualizando-os para `("text", "image")`.
3. `tests/unit/workers/test_model_capabilities.py`:
   - Verificar que `ChatWorker._model_sees_images` retorna `True` para `provider="openai-codex"`, `model_id="gpt-6.1-sol"` / `"gpt-6.1-terra"` / `"gpt-6-luna"` (mesmo se a linha do banco estiver obsoleta), e que `session._sees_images_for("gpt-6.1-terra", "openai-codex")` em subagentes também retorna `True`.
4. `tests/unit/agentic/test_view_file_vision.py`:
   - Verificar que `ViewFile` e `Read` chamados num turno `openai-codex` retornam `images: [{"type": "image", ...}]` inline sem cair no `VisionReader`.
   - Verificar que `VisionReader` não seleciona provedores CLI incompatíveis com imagem base64 inline ou levanta `VisionUnavailable` se receber `[Imagem/mídia omitida...]`.
5. `tests/unit/providers/codex_cli/test_command.py`:
   - Verificar que `build_codex_command` inclui `-i <path>` para imagens anexadas tanto em turnos novos quanto em `resume`, e inclui `-c features.mcp_tool_list_changed=true`.
6. `tests/unit/agentic/tools/test_mcp_tool_search.py`:
   - Verificar que `ToolSearch(query="select:Skill,Glob,Agent")` retorna os schemas de `Glob` e `Agent` mesmo quando já estão ativos (`ALWAYS_LOADED_CODE_TOOLS` ou já descobertos).
