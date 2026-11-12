# Spec: Antigravity enxergando/ouvindo mídia e Codex fora do "somente leitura"

Data: 12/11/2026 · Base: `main` em v0.12.21 (576c13fa) · agy 1.5.6 · Codex CLI 0.167.2

Evidências brutas: `C:/tmp/orin-agy-img156/` e `C:/tmp/orin-agy-media-diag/` (sondas do agy, a segunda do
subagente Lumen), `C:/tmp/orin-codex-ro-mine/` e `C:/tmp/orin-codex-ro-diag/` (sondas do Codex).
Relatório completo do Lumen: `.orin/tool-outputs/check_subagent-a74f37ab9818.txt`.

---

## Problema 1: o Antigravity não vê imagens nem ouve áudio

### Causa raiz (confirmada)

1. **O agy 1.5.6 descarta imagem grande vinda de MCP.** Quando uma ferramenta MCP devolve um bloco
   `image`, o agy grava o arquivo em `brain/<conv>/.system_generated/steps/N/media_0.*` ("Resource
   offloaded") e só anexa a imagem ao modelo se ela for pequena. Medido com a mesma imagem em vários
   tamanhos, tanto com `claude-opus-5-5-high` quanto com `gemini-3.8-flash-high`:

   | Imagem (bytes) | Resultado |
   |---|---|
   | 7 KB PNG, 9,5 KB JPG | o modelo vê |
   | 15,4 KB, 24 KB, 32 KB, 39 KB JPG; 73 KB PNG; screenshot real de 104 KB | `CANNOT_PERCEIVE` |

   Um screenshot ou anexo real nunca fica abaixo de ~10 KB, então na prática **o caminho MCP
   (`view_file`, `Read`, `call_mcp_tool` do Playwright, `read_mcp_resource`) nunca entrega imagem ao
   modelo do agy**.

   Caso real (print do usuário): conversa `chat_53cfb47c…`, turno `turn_3487825d…`, agy com
   `claude-opus-5-5-high`. O `view_file` do Orin devolveu "A imagem … está anexada logo abaixo". O agy
   gravou `steps/83/media_0.jpg`, mas o passo seguinte do modelo teve `input_tokens: 561`, sem nenhum
   token de imagem. No raciocínio, o modelo escreveu: "The image seems to have been saved to a file
   rather than shown to me". O Orin está correto até o MCP: `read.py:651-657` → `runtime.py:3015` →
   `native_turn.py:1280` → `cli_tools_mcp/server.py:381-393`.

2. **O `read_file` nativo do agy funciona, mas o Orin só o libera para modelos `gemini-*`.** Com
   `tools: [read_file]` e `read_file(<dir>)` liberado, Claude Opus e Gemini descreveram certo o
   screenshot real de 295 KB. Hoje o Orin só liga isso quando `is_antigravity_gemini_model` é
   verdadeiro (`native_loop.py:1251-1260`, `antigravity_cli/command.py:568-583`). Para Claude ou GPT via
   agy, o Orin gera `deny read_file(*)` (ex.: `workdir/run-19428-2/.agents/orin_settings.json`).

3. **A liberação cobre uma raiz só: a do projeto.** `read_file(<workspace_root>)` em
   `command.py:659-662`, `allowed_root` único no gate (`media_gate.py:116-160`, `command.py:718`), no
   tradutor (`stream.py:336-357`) e na checagem de proibidos (`native_loop.py:123-145`). O Playwright
   salva screenshots em `AppData/Local/Orin/data/.playwright-mcp/`, fora dessa raiz.

4. **O prompt não manda usar o `read_file` nativo para imagem.** A nota em `command.py:379-389` só
   cita áudio e vídeo. Para imagem, o modelo usa o `view_file` do Orin, que cai no item 1.

5. **Áudio com modelo não-Gemini não tem caminho.** Com Claude, o `read_file` nativo do agy responde
   `binary file type 'audio/wav' is not supported by the current model`. Com Gemini, áudio funciona
   (`neutro.wav` foi transcrito exatamente). O Orin não tem transcrição de áudio no `view_file`
   (`read.py` só faz leitura visual).

6. **O upload rejeita áudio e vídeo.** `uploads/media.py:67-92` só aceita PNG, JPEG, GIF, WEBP, PDF,
   Office e texto; o resto recebe `UploadRejected("file type is not accepted")`. Um anexo de áudio no
   chat não chega a nenhum modelo, em nenhum provedor.

7. **Um `read_file` nativo negado mata o processo agy.** Mesmo quando o hook `PreToolUse` nega a
   chamada (caminho relativo, arquivo fora da raiz, extensão de texto), o agy emite um `step_update`.
   `find_forbidden_item_kinds` (`native_loop.py:949-961`) trata isso como uso proibido, mata o processo e
   gasta uma das duas retomadas (`CliBuiltinToolDetour`). Isso aparece no `worker.log`, inclusive no
   turno do print: `10:21:57 … called its built-in tool 'read_file'; resuming from history (2/2)`. O
   modelo nunca recebe a mensagem do hook que o ajudaria a corrigir.

8. **O Playwright grava fora do projeto e recusa gravar dentro.** `McpToolProvider` inicia o servidor
   stdio com `cwd=None` (`mcp/toolset.py:108-110`), e o `@playwright/mcp` herda o cwd do worker
   (`AppData/Local/Orin/data`). Tentativas de salvar em `<projeto>/.orin/tmp/…` ou em `C:/tmp` falham com
   `File access denied … outside allowed roots` (turno do print, passos 89-90).

9. **O subagente herda a visão do agente principal.** `session.py:4925` repassa `self.model_sees_images`
   do principal ao toolset do subagente. Um principal só-texto deixa um subagente Gemini ou Claude sem
   imagem, e o contrário também.

### Correção

**A1. Imagem de resultado de ferramenta vira arquivo e o modelo a abre com o `read_file` nativo** (núcleo
da correção)
- Num ponto central do turno agy (`TurnToolServer`/`cli_tools_mcp/server.py`, ou `native_turn.py:1266-1288`
  com um flag do adapter `offload_tool_images`), grave cada imagem de `ToolCallResult.images` em
  `<pasta de mídia do turno>/<call_id>-<n>.<ext>`. A pasta fica dentro do workspace da conversa, em
  `.orin/tool-media/`, para cair na raiz já liberada.
- Acrescente ao texto do resultado: `Imagem salva em <caminho absoluto>. Para ver, chame sua ferramenta
  nativa read_file com esse caminho.`
- Mantenha o bloco inline (inofensivo, e funciona abaixo de ~10 KB).
- Isso cobre de uma vez `view_file`, `Read`, `call_mcp_tool` (Playwright e outros MCPs), `read_mcp_resource`
  e screenshots da Bancada.
- Limpeza: apague a pasta junto com a conversa e aplique um TTL, como já é feito com uploads.
- No `view_file` do Orin, quando o turno for agy, inclua no texto o caminho absoluto do arquivo
  original. Assim, um modelo que chame `ViewFile` por hábito se recupera na chamada seguinte.

**A2. `read_file` nativo para todos os modelos do agy, com extensões por modelo**
- Ligue `enable_media_gate` para qualquer modelo agy (`command.py:568-583`, `native_loop.py:1251-1260`).
- Extensões permitidas: Gemini = imagem + áudio + vídeo (como hoje). Claude e GPT = só imagem. Grave isso
  em `orin_media_gate.json` (`allowed_extensions`), para o hook negar áudio e vídeo com uma mensagem clara.

**A3. Várias raízes liberadas**
- Troque `allowed_root` por `allowed_roots: [...]`. Continue lendo `allowed_root` para manter
  compatibilidade com arquivos já gerados.
- Raízes: workspace da conversa (inclui `.orin/uploads` e `.orin/tool-media`), `workspace.additional_roots`
  (`/add-dir`) e a pasta de saída do Playwright (A8).
- Em `full_autonomy` (`workspace.unrestricted`), libere `read_file(*)` com o gate ainda filtrando por
  extensão de mídia, tamanho, UNC, device paths e symlinks. É o mesmo alcance que o `Read`/`ViewFile` do
  Orin já tem nesse modo.
- Não libere `~/.gemini/antigravity-cli/brain` inteiro, porque ele contém mídia de outras conversas.
- Atualize os quatro pontos juntos: `wire_antigravity_tool_server` (uma regra `read_file(<raiz>)` por
  raiz), `media_gate.is_allowed_media_path`, `AntigravityStreamTranslator._is_allowed_native_media_read`
  e `native_loop._is_allowed_antigravity_media_step` / `find_forbidden_item_kinds`.

**A4. Prompt**
- Reescreva `antigravity_media_prompt_note` para cobrir imagem também: "Para ver imagem (ou ouvir/assistir,
  no Gemini), use seu `read_file` nativo com o caminho ABSOLUTO; o `view_file` do Orin informa esse
  caminho".
- Para modelo não-Gemini, diga explicitamente que áudio e vídeo vêm transcritos pelo Orin (A5).
- Ajuste o texto do anexo do usuário (o "Use view_file…" do lembrete) para incluir o caminho absoluto.

**A5. Áudio para Claude e GPT via agy (fase 2, depende de decisão)**
- Opção recomendada: `view_file` de áudio, num turno sem percepção de áudio, transcreve via um processo agy
  curto com `gemini-3.8-flash` e `read_file` nativo. Usa a assinatura, sem chave de API, e reaproveita o
  mesmo gate. Devolve o texto como resultado.
- Alternativa: recusar com uma mensagem clara sugerindo trocar para Gemini.

**A6. Subagentes**
- O subagente agy recebe a mesma fiação (cada processo já tem pasta própria, desde 5e35fb8b). Confira se
  `workspace_root` chega no wiring do subagente. A falta dele gera `deny read_file(*)`, como em
  `run-19428-2`.
- Calcule `model_sees_images` pelo modelo do subagente, não pelo do principal (`session.py:3635`, `:4925`).

**A7. Não matar o agy quando o hook já negou**
- Em `find_forbidden_item_kinds`/`_is_allowed_antigravity_media_step` (`native_loop.py:123-146`, `949-961`)
  e `stream.py:336-357`: com o media gate ativo, um `step_update` de `read_file` negado pelo hook (estado
  de erro) não dispara `_stop_for_forbidden`. O modelo recebe a mensagem do hook e corrige.
- Mantenha o kill para um `read_file` concluído (`DONE`) fora da política, como defesa em profundidade.

**A8. Playwright gravando dentro do projeto**
- Inicie o `@playwright/mcp` do catálogo com `--output-dir <workspace>/.orin/playwright` (ou com cwd no
  workspace), chaveando a sessão stdio por `(servidor, agente, workspace_root)`. Assim duas conversas em
  projetos diferentes não dividem a mesma pasta (`mcp/toolset.py:108-110`, `218-221`, `340-360`).

**A9. Upload de áudio e vídeo**
- Aceite em `uploads/media.py` os formatos que o gate já conhece (`.mp3 .wav .ogg .opus .m4a .flac .aac
  .mp4 .webm .mov .mkv`), com classe `audio`/`video` e um limite de tamanho próprio.
- `_attachment_marker` (`conversations/chat.py:123-130`) passa a dar o caminho ABSOLUTO. No agy, ele manda
  usar o `read_file` nativo para imagem, áudio e vídeo, e o `view_file` para PDF e Office.
- `encode_antigravity_user_turn` (`command.py:1109-1160`) trata imagem igual a áudio e vídeo: nota com o
  caminho absoluto, em vez de `ANTIGRAVITY_OMITTED_MEDIA_NOTE`.
- Vale para os outros provedores também: o Claude CLI e o Codex recebem imagem; para áudio sem
  modelo que ouça, use A5.

### Testes
- Unit:
  - `wire_antigravity_tool_server` com `claude-opus-*` gera `tools: [read_file]` e um allow por raiz.
  - Gate com várias raízes: nega fora delas, nega extensão de texto e nega áudio para modelo não-Gemini.
  - Tradutor e `find_forbidden_item_kinds` aceitam `read_file` em qualquer raiz e matam fora delas.
  - O servidor MCP grava a imagem, acrescenta a nota e mantém o bloco inline.
  - Um `read_file` negado pelo hook não gera `CliBuiltinToolDetour`; um `DONE` fora da política gera.
  - O upload aceita `.mp3`/`.wav`/`.mp4`, e o `_attachment_marker` traz caminho absoluto.
  - Um subagente Gemini sob um principal só-texto recebe `model_sees_images=True`.
  - O Playwright inicia com `--output-dir` dentro do workspace.
- E2E manual (agy 1.5.6, modos chat e code, agente principal e subagente):
  - anexo PNG grande com Claude e com Gemini;
  - screenshot do Playwright descrito corretamente;
  - anexo de áudio com Gemini (transcrição) e com Claude (A5 ou recusa clara);
  - tentativa de `read_file` em `C:/Windows/win.ini` e em `.env` do projeto negada.
- Script de regressão do limite (`C:/tmp/orin-agy-img156`), para detectar quando um agy futuro passar a
  aceitar imagem grande.

### Riscos
- `read_file` nativo lê qualquer arquivo com extensão de mídia nas raízes. O hook continua barrando texto
  e código.
- Se o hook falhar, a chamada é bloqueada (falha fechada, já testado).
- Disco: imagens de ferramenta duplicadas. Mitigação: TTL e limpeza com a conversa.

---

## Problema 2: o Codex diz que está em "somente leitura"

### Causa raiz (confirmada)

Relatório completo do subagente Ferro: `.orin/tool-outputs/check_subagent-934fb2cef810.txt`.

- O Orin roda o Codex com `sandbox_mode="read-only"` e `approval_policy="never"`
  (`codex_cli/command.py:491-492`), para desligar o shell nativo e forçar tudo pelo MCP.
- Com isso, o Codex 0.167.2 injeta no contexto do modelo uma **mensagem de developer**:
  `<permissions instructions> … sandbox_mode is read-only: The sandbox only permits reading files.
  Network access is restricted. Approval policy is currently never. … commands will be rejected.`
  Injeta também um `<environment_context>` com `permission_profile` só leitura.
- A nota "Where you work" do Orin (`native_loop.py:77-94`, presente desde a v0.12.6) fica no prompt de
  sistema. O aviso do Codex chega depois, como developer, e ganha.
- Controle com a configuração atual: perguntado, o modelo respondeu "O sandbox local é **read-only**, com
  rede restrita e aprovação `never`". Em tarefa curta ele ainda chamou `Write` e `Bash`. Em tarefa longa
  ou retomada, essa crença vira a recusa relatada.
- O sandbox **não** bloqueia as chamadas MCP: `Write` e `Bash` via MCP funcionaram nas duas
  configurações, inclusive com o `TurnToolServer` real e o prompt do modo code (sonda do subagente).
- **Reproduzido no runtime real do Orin** (`TurnSession` + `ClaudeCliProvider` + `CodexCliAdapter`, harness
  do subagente Ferro em `C:/tmp/orin-codex-ro-diag/repro_e2e.py`, `full_autonomy`). No caso `e2e_e1` (modo
  code, gpt-5.6-terra), o modelo criou o arquivo e rodou o comando. Na retomada seguinte, respondeu: "Neste
  contexto atual, o sandbox está em **somente leitura**; não posso criar arquivos nem executar comandos".
  No caso `e2e_e3` (modo chat, gpt-6.1-sol), respondeu de primeira "esta sessão está configurada como
  **somente leitura**".
- Nenhum turno do Codex ficou gravado nos bancos do Orin desta máquina, então a conversa original do
  usuário não pôde ser relida. A reprodução acima mostra o mesmo sintoma.
- **Agravante 1: o modo de permissão do Orin chega pelo canal mais fraco.** No modo code,
  `Permission mode: bypassPermissions` e o diretório de trabalho só vão no `<system-reminder>` dentro da
  mensagem de **usuário** (`native_turn.py:1871-1894`); o prompt de sistema (`render_code_system_prompt`)
  não traz nenhum dos dois. A nota "Where you work" diz "with the permission mode stated in these
  instructions", mas nenhum modo está declarado nas instruções. O GPT trata o lembrete de usuário como
  tentativa de escalada. Resposta literal: "O lembrete que menciona `bypassPermissions` não corresponde
  às permissões efetivas desta sessão".
- **Agravante 2: as ferramentas do Orin não aparecem.** No Codex 0.167.2, `mcp__orin` é um namespace
  diferido, escondido atrás de `functions.exec` (`Deferred tool namespaces: - mcp__orin`). O modelo não vê
  `Write`/`Bash` no topo e, com o aviso de read-only, nem procura.
- **Agravante 3: a mensagem do usuário é achatada numa linha só.** `encode_codex_user_turn`
  (`codex_cli/command.py:611-644`) troca todas as quebras de linha por espaço: histórico, lembretes e
  código colado viram uma linha gigante. O `codex exec -` lê o stdin até o EOF, então não há motivo para
  isso.
- **Agravante 4: anexo de imagem derruba o turno.** O mesmo encoder levanta `ValueError` para qualquer
  bloco de imagem (`command.py:620-626`). É o mesmo tipo de bug que o agy teve na 3ca9c79f.
- **Segurança (por que não trocar o sandbox).** Dentro de `functions.exec`, o Codex 0.167.2 expõe
  `apply_patch` nativo, mesmo com `shell_tool=false`. Com `danger-full-access`, o modelo poderia escrever
  em disco em `plan_mode`, antes de o Orin ver o evento. `workspace-write` no Windows é rebaixado para
  read-only, porque `--ignore-user-config` ignora o `[windows] sandbox = "elevated"`. Então o
  `sandbox_mode="read-only"` precisa ficar.

### Correção

**C1.** Em `build_codex_command` (`command.py:483-514`), acrescente às `base_c_values` e às
`locked_c_keys`:
```
include_permissions_instructions=false
include_environment_context=false
include_apps_instructions=false
include_collaboration_mode_instructions=false
features.unified_exec=false
features.unified_exec_tty=false
features.computer_use=false
features.browser_use=false
features.apps=false
features.plugins=false
features.sleep_tool=false
features.skill_mcp_dependency_install=false
features.skip_host_skill_discovery=true
```
As duas primeiras são o núcleo da correção. As outras tiram blocos de instrução e ferramentas nativas que
o Orin não usa. O Ferro verificou todas no npm 0.167.2 e no Codex da Microsoft Store
(0.167.0-alpha.4.1). Não use as chaves `multi_agent_v2.*`, que mudam entre as duas versões.
`features.code_mode_host=false` quebra todas as chamadas MCP: não use.
Testado de duas formas:
- Codex direto: o bloco some da sessão (0 ocorrências no rollout), o modelo responde "bypassPermissions
  (autonomia total)" e `Write`/`Bash` funcionam.
- Runtime real do Orin (`C:/tmp/orin-codex-ro-mine/e2e_fix.py`, modos code/terra e chat/sol): o modelo diz
  `bypassPermissions`/`full_autonomy`, cria o arquivo, roda o comando e não volta a dizer "somente
  leitura", nem depois das retomadas.

Validação do Ferro com o conjunto completo e C3 (`validate_fix.py`): `full_autonomy` nos modos code/terra e
chat/sol terminou com `state=completed` em 1 iteração. Em `plan_mode`, a escrita continuou bloqueada
(`proibido.txt` não foi criado).

Mantenha `sandbox_mode="read-only"` e `approval_policy="never"`: são eles que seguram o `apply_patch`
nativo.

**C1b. Modo de permissão e workspace no prompt de sistema.** Faça `with_isolated_cli_note`
(`native_loop.py:76-103`, chamado em `:1316-1320`) receber o `permission_mode` efetivo e o `workspace_root`
(via `NativeTurnRequest`) e escrevê-los no `model_instructions_file`, que vai no canal developer. A nota
passa a dizer, por exemplo: "Modo de permissão do Orin: bypassPermissions. Workspace: X. As ferramentas
ficam no namespace `mcp__orin` (no Codex, podem estar diferidas em `functions.exec`)". Em `plan_mode`, a
nota diz que o turno é só leitura, alinhando o prompt ao broker. Vale para Claude CLI e agy também.

**C2.** Sem o `environment_context`, o modelo perde o cwd e a data do Codex. O Orin já fornece os dois:
modo chat em `session.py:4547` (Working directory) e `session.py:753` (data); modo code em
`code_mode/system_prompt.py:219` e `:255`. Falta só confirmar no E2E que isso chega ao prompt do Codex.

**C3. Preservar quebras de linha** em `encode_codex_user_turn`: normalize `\r\n`/`\r` para `\n` e garanta um
`\n` no fim, em vez de trocar tudo por espaço. Atualize os testes que esperam uma linha só.

**C3b. Anexo de imagem no Codex.** O Codex aceita imagem (`codex exec -i <arquivo>`), mas o Orin desliga
`features.view_image`. Troque o `ValueError` por: (a) passar a imagem com `-i <caminho absoluto>` quando o
modelo aceitar imagem; ou (b) virar uma nota de texto com o caminho, como o agy fez na 3ca9c79f. Nunca
derrube o turno.

**C3c. Threads antigas.** Threads criadas antes da correção guardam o bloco `read-only` no histórico
(`~/.codex/sessions`). Na primeira retomada após o update, comece uma thread nova (descarte o
`provider_session_id` antigo).

**C4.** O Codex ignora em silêncio chaves `-c` desconhecidas (testado). Por isso o teste precisa conferir
o efeito, não só a flag.

**C5. Investigar: turnos do Codex terminando em `PROVIDER_STREAM_INTERRUPTED`.** No harness do runtime real,
os casos com a configuração atual, e também os que tinham só as duas chaves `include_*`, acabaram em
`reconciliation_required` / `PROVIDER_STREAM_INTERRUPTED` com 3 iterações. O Orin retoma o turno duas
vezes, e o modelo repete a resposta e relê o contexto (foi assim que o "somente leitura" apareceu no
`e2e_e1`). O stderr mostra `ConnectionResetError [WinError 10054]`. Com o conjunto completo de C1 e com C3,
os turnos do Ferro terminaram limpos em 1 iteração. Confirme qual mudança resolve, e confira num turno
real do app (`worker.log`, `end_reason`, mapeamento do `turn.completed`).

**C6. Fallback da Microsoft Store.** Forçando o binário da Store (0.167.0-alpha.4.1), o turno falhou
na hora com `PROVIDER_STREAM_FAILED` (0,03 s). Investigue antes de depender desse fallback.

**C7. Poluição residual (baixa prioridade).** Mesmo com `--ignore-user-config`, o Codex carrega
`~/.codex/AGENTS.md` e as skills globais do usuário, porque precisa do `CODEX_HOME` para o `auth.json`. Não
causa o bug, mas regras conflitantes nesse arquivo entram no contexto.

### Testes
- Unit:
  - `build_codex_command` contém as chaves de C1, e o wiring não consegue sobrescrevê-las (locked).
  - `sandbox_mode="read-only"` continua presente.
  - `encode_codex_user_turn` preserva `\n`, e um bloco de imagem não levanta erro.
  - `with_isolated_cli_note` escreve o modo de permissão e o workspace (`bypassPermissions` vs `plan`).
- E2E (script baseado em `C:/tmp/orin-codex-ro-diag/repro.py`):
  - o rollout da sessão não contém `permissions instructions` nem `permission_profile`;
  - perguntado sobre o sandbox, o modelo não diz "read-only";
  - uma tarefa de várias etapas (ler, editar, rodar teste) termina sem recusa, nos modos chat e code, com
    `persist_session` e com `resume`.
- Regressão de segurança: em `plan_mode`, nem o `Write` do Orin nem o `apply_patch` nativo dentro de
  `functions.exec` conseguem criar arquivo.

### Riscos
- O Codex pode renomear essas chaves numa versão futura (e ignora as desconhecidas em silêncio). O teste
  E2E acima pega isso. Condicione chaves novas a `codex_version()` quando necessário.
- Nunca afrouxar o sandbox do Codex para resolver isso: o `apply_patch` nativo continua exposto.
- Sem `environment_context`, ficam sem esses dados os modelos que dependiam dele (mitigado por C2).
