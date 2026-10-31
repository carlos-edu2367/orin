# Especificação de Design: Correções de UX, Wake Word, Mascote, Central de Aprovações e Contexto do Orin Code (7 Casos)

- **Data:** 2026-10-31
- **Status:** Aguardando aprovação do Carlos
- **Escopo:** Resolução integrada dos 7 problemas identificados no Orin Desktop / Web (`v0.11.1`).

---

## 1. Caso 1 — Painel `CodeWorkbenchPanel` (Prévia, Arquivos e Terminal) Recolhido por Padrão no Orin Code

### 1.1 Causa Raiz
- Em `frontend/src/features/conversations/ChatPage.tsx` (linhas 1986 e 2890–2901), sempre que `conversationMode === 'code'`, o elemento `<main>` recebe incondicionalmente a classe `.chat--code-split` e monta `<CodeWorkbenchPanel />`.
- Em `frontend/src/styles/agentos.css` (linhas 5887–5925), `.chat.chat--code-split` comprime a coluna de chat para `minmax(380px, 460px)` e entrega todo o restante da tela (`minmax(0, 1fr)`) para a bancada, além de `.code-workbench__stage` ter fundo branco fixo (`#FAF8FC`) mesmo quando não há URL de prévia ativa.

### 1.2 Design da Solução
1. **Estado `workbenchOpen` em `ChatPage.tsx`**:
   - Adicionar `const [workbenchOpen, setWorkbenchOpen] = useState(false)` (resetando para `false` ao trocar de `conversationId`).
   - Aplicar `.chat--code-split` no `<main>` e renderizar `<CodeWorkbenchPanel />` **somente** quando `conversationMode === 'code' && workbenchOpen`.
2. **Botão de Expansão/Recolhimento no Header do Orin Code**:
   - Em `.chat__bar-actions` (`ChatPage.tsx`, ao lado do botão `Histórico`), adicionar o botão `Bancada` (`data-testid="code-workbench-toggle"`, `aria-pressed={workbenchOpen}`, `aria-expanded={workbenchOpen}`), exibindo indicador/contador opcional quando houver arquivos alterados ou prévia detectada.
   - Em `CodeWorkbenchPanel.tsx`, aceitar prop `onClose?: () => void` e renderizar um botão de recolher (`data-testid="code-workbench-close"`) alinhado à direita de `.code-workbench__tabs`.
   - Ao clicar explicitamente em `"Ver alterações"` (`data-testid="code-view-changes-button"`) ou em um arquivo dentro de `code-changed-files-card`, abrir a bancada (`setWorkbenchOpen(true)`) na aba `files`.
3. **Refino Visual do Empty State da Prévia**:
   - Estilizar `.code-workbench__preview-empty` / `.code-workbench__stage` para que, quando não houver URL ativa (`!activeUrl`), o estado vazio respeite a superfície do tema em vez de exibir um bloco branco estourado.

---

## 2. Caso 2 — Orin Friend Live Abrindo Sozinho no Silêncio (Falsos Positivos de Wake Word)

### 2.1 Causa Raiz (Cadeia de 5 Fatores)
1. **Whisper alucinando o `initial_prompt` com `vad_filter=False` (Causa Principal)**:
   - Em `src/agentos/live_voice/session.py:1028`, `check_wake_word_route` sempre passa `prompt_hint = build_wake_prompt_hint(custom_words)` (`"Assistente Orin. Oi Orin."`).
   - Em `src/agentos/live_voice/workers/whisper_worker.py:79` e `src/agentos/live_voice/engines.py:270`, o código faz `vad_filter=not bool(prompt_hint)`, o que avalia para **`False`** justamente no wake-check!
   - Com `vad_filter=False` e `initial_prompt="Assistente Orin. Oi Orin."`, qualquer trecho curto de silêncio ou ruído branco faz o decoder do Whisper alucinar a continuação/repetição do próprio `initial_prompt` (`"Oi Orin."`).
2. **Standby VAD hipersensível + WebRTC `autoGainControl: true`**:
   - Em `frontend/src/features/conversations/live/GlobalWakeWordListener.tsx:31, 161, 225`, a sensibilidade mínima em standby é forçada para `0.78` (`threshold` de RMS cai para `~0.0106`), `startSpeechMs` é de apenas `64ms` (2 frames), e `MicCapture.open({ headphoneMode: true })` ativa `autoGainControl: true`, fazendo o navegador amplificar o ruído de fundo no silêncio até cruzar o limiar do VAD.
3. **`MIN_WAKE_FRAMES` inoperante no Frontend**:
   - `EnergyVad` sempre concatena `preRollMs (280ms = 9 frames)` + `startSpeechMs (64ms = 2 frames)` + `endSilenceMs (420ms = 14 frames)` = no mínimo 25 frames (`~800ms`), tornando `frames.length < MIN_WAKE_FRAMES` (`5 frames`) código morto.
4. **Ausência de gate acústico no Backend (`check_wake_word_route`)**:
   - `session.py:998-1047` envia o PCM direto ao Whisper sem checar RMS de pico ou quantidade de frames efetivamente vocais, e `providers.py:341` (`MultiProviderSTTAdapter`) chegava a retornar o próprio `prompt_hint` como transcrição fallback.
5. **Colapso fonético excessivo em `wake_word.py`**:
   - `phonetic_skeleton_ptbr` (`src/agentos/live_voice/wake_word.py:236-248`) mapeia qualquer vogal inicial `{o, a, e, u}` para `O` e descarta todas as vogais internas/finais, fazendo palavras comuns (`"além"`, `"alma"`, `"aluno"`, `"eram"`, `"arma"`, `"urna"`) gerarem `"ORN"` com `confidence = 0.86`.

### 2.2 Design da Solução
1. **Backend Whisper & STT (`whisper_worker.py`, `engines.py`, `session.py`, `providers.py`)**:
   - Manter `vad_filter=True` sempre no `faster-whisper` e descartar segmentos com `no_speech_prob > 0.6` ou `avg_logprob < -1.0`.
   - Em `check_wake_word_route` (`session.py`), **não** passar `"Assistente Orin. Oi Orin."` como `initial_prompt` / `prompt` no STT de wake-word (`prompt_hint=None`), eliminando a fonte de alucinação de prompt do Whisper.
   - Em `providers.py` (`MultiProviderSTTAdapter`), nunca retornar `prompt_hint` como fallback de transcrição e rejeitar áudio com RMS baixo independentemente de `prompt_hint`.
2. **Gate Acústico no Backend (`session.py:check_wake_word_route`)**:
   - Antes de invocar `_transcribe`, calcular o RMS frame a frame (janelas de 32ms) do `pcm_bytes`. Se o pico de RMS for `< 0.025` ou houver menos de `6` frames vocais (`~192ms` com RMS `>= 0.020`), retornar imediatamente `matched=False` sem chamar o STT.
3. **Endurecimento do Standby VAD no Frontend (`GlobalWakeWordListener.tsx`)**:
   - Remover `MIN_STANDBY_SENSITIVITY = 0.78` e respeitar `prefs.sensitivity` (limitando o teto padrão de standby para não ficar hipersensível).
   - Aumentar `startSpeechMs` de `64ms` para `160ms` (5 frames consecutivos de voz).
   - Passar `headphoneMode: false` no `MicCapture.open` do standby para desativar `autoGainControl` durante a escuta passiva.
   - Em `inspectSpeechFrames`, exigir pelo menos `6` frames efetivamente vocais (`RMS >= 0.020`) e pico `>= 0.025` antes de disparar `checkWakeWord`.
4. **Refino Fonético em `src/agentos/live_voice/wake_word.py`**:
   - Restringir `phonetic_skeleton_ptbr` para alinhar com a validação estrita do frontend (`wakeWord.ts`): exigir vogal inicial labial/posterior (`o`/`u`, nunca `a`/`e`), vogal interna palatal/fechada (`i`/`e`/`y`) entre `r/l` e `n/m`, e ausência de vogal aberta final (`a`/`o`/`u`) após `n/m`, impedindo falsos positivos em `"além"`, `"alma"`, `"aluno"`, `"eram"`, `"arma"`, `"urna"`, etc.

---

## 3. Caso 3 — Configuração da Anthropic (e Demais Providers) Cortada na Direita

### 3.1 Causa Raiz
- `.slide-over-drawer` (`frontend/src/styles/agentos.css:3182`) tem largura `min(460px, 100vw)` (`~404px` úteis) sem `overflow-x: hidden`.
- `.settings-drawer`, `.settings-drawer__body`, `.ollama-flow`, `.ollama-flow__connection` e `.provider-key-list` (`agentos.css:125`, `index.css:254, 363, 374`) usam `display: grid` sem `grid-template-columns: minmax(0, 1fr)` e sem `min-width: 0`.
- `.ollama-flow__modes` (`index.css:261`) define `width: max-content` sem `flex-wrap: wrap`; com os rótulos `"Assinatura Claude Code (CLI Local)"` + `"Cloud (Chave de API)"`, a linha exige `~505px`, esticando todas as seções irmãs do grid para fora do drawer.
- As regras responsivas de coluna única (`index.css:409-423`) estão presas em `@media (max-width: 620px)` da viewport, nunca ativando em telas desktop quando o formulário está dentro do drawer lateral de `460px`.
- Adicionalmente, no Electron (`html[data-desktop="true"]`), o topo direito do `.slide-over-drawer` fica sob os botões nativos da janela (`─ ❐ ✕`).

### 3.2 Design da Solução
1. **Ajuste do `.slide-over-drawer` (`agentos.css`)**:
   - Ampliar a largura para `width: min(560px, 100vw)` com `overflow-x: hidden`.
   - Em `html[data-desktop="true"] .slide-over-drawer`, adicionar `padding-top: max(24px, calc(14px + var(--orin-titlebar-height, 0px)))` para que o cabeçalho e o botão `"Fechar"` não fiquem sob os controles da janela do Windows.
2. **Contenção de Grid e Flex (`agentos.css` e `index.css`)**:
   - Definir `grid-template-columns: minmax(0, 1fr); min-width: 0; max-width: 100%;` em `.settings-drawer`, `.settings-drawer__body`, `.ollama-flow`, `.ollama-flow__connection` e `.provider-key-list`.
   - Em `.ollama-flow__modes`, substituir `width: max-content` por `display: flex; flex-wrap: wrap; width: 100%; max-width: 100%;` com `.ollama-flow .chip { flex: 1 1 auto; min-width: 0; white-space: normal; text-align: center; }`.
3. **Regras Compactas Escopadas para `.settings-drawer` (`index.css`)**:
   - Aplicar as regras de empilhamento (atualmente restritas a `@media (max-width: 620px)`) também diretamente sob `.settings-drawer` (`.ollama-flow__overview`, `.ollama-flow__fields`, `.ollama-flow__mode-field`, `.ollama-flow__footer`, `.ollama-flow__actions`, `.provider-key-list__fields`, `.provider-key-list__cooldown`), garantindo encaixe perfeito em Anthropic, OpenCode, NVIDIA, Ollama e demais providers.

---

## 4. Caso 4 — Símbolo do Orin no Topo: Bolinha Verde Condicional + Central de Novidades e Aprovações Inline

### 4.1 Causa Raiz
- Os 5 headers principais (`WorkspaceShell.tsx:173`, `Home.tsx:343`, `ChatPage.tsx:2250`, `ProjectPage.tsx:280`, `ProjectsGalleryPage.tsx:160`) renderizam `<OrinMascot size={30} variant="flat" statusDot="online" />` de forma estática/hardcoded e sem nenhuma interação ao clicar.

### 4.2 Design da Solução
1. **Novo Componente Compartilhado `<OrinActivityCenter />` (`frontend/src/components/OrinActivityCenter.tsx`)**:
   - Substitui o `<OrinMascot statusDot="online" />` estático nos 5 headers do topo (e altera `FriendSoulDrawer.tsx:374` para `statusDot="none"`).
2. **Regra Condicional da Bolinha Verde (`statusDot`)**:
   - `statusDot="online"` (bolinha verde) **só é renderizada** quando `pendingApprovals.length > 0 || unreadUpdates.length > 0`.
   - Caso contrário, `statusDot="none"` (sem bolinha verde).
3. **Detecção de Aprovações Pendentes e Novidades entre Sessões**:
   - Monitora as sessões retornadas por `listConversations` e `listProjectSidebar` (atualizando via eventos `orin:conversations-updated`, `orin:projects-updated` e polling quando houver sessões ativas/aguardando).
   - Para sessões em `waiting_user` ou `running`/`streaming`, consulta os detalhes da conversa (`getConversation`) para extrair aprovações pendentes reais:
     - `toolPermission` (`code_mode.decision_required` com `status === 'pending'`, ex: comandos Bash/PowerShell, edições de arquivos)
     - `planApproval` (`code_mode.plan_approval`)
     - `workspaceTrust` (`workspace.trust_required`)
     - `pluginApproval` (`plugin.approval_required`)
     - `mcpApproval` (`mcp.approval_required`)
     - `questions` / `codeSessionProposal`
   - Rastreia em `localStorage` (`orin.activityCenter.lastSeen`) o `updated_at` visto por sessão: quando uma sessão em segundo plano conclui ou atualiza (`completed` / `failed` / `waiting_user`) enquanto o usuário não está nela, ela entra em `unreadUpdates` ("Novidades").
4. **Popover com Resumo e Aprovação Inline ao Clicar no Mascote**:
   - Clicar no mascote do topo abre um popover ancorado à direita do header contendo:
     - **Aprovações Pendentes**: mostra o título da sessão, o tipo de solicitação, o comando/caminho/resumo (ex: `$ git clone ...`) e botões de ação **inline**:
       - `Permitir` (`allow_once`), `Sempre permitir` (`allow_always`, quando houver `suggested_rule`) e `Negar` (`deny`) via `submitToolPermission`
       - `Aprovar plano` via `updateConversationPermissionMode` + `submitToolPermission` / `sendConversationMessage`
       - `Confiar na pasta` via `trustConversationWorkspace`
       - `Aprovar plugin` / `Aprovar MCP`
       - Link rápido para abrir a sessão correspondente (`/c/:id`).
     - **Novidades nas Sessões**: lista sessões com atualizações não lidas, resumo da última mensagem/estado e botão `"Marcar como lido"` ou clique para navegar à sessão.

---

## 5. Caso 5 — Novos Ícones Desktop e Web (Mascote do Orin com Fundo Arredondado Estilo Discord)

### 5.1 Causa Raiz
- Embora os componentes React tenham sido migrados para o novo `OrinMascot`, os arquivos de ícone estáticos usados pelo Electron, instalador Windows/Linux e favicon web (`desktop/electron/assets/orin-logo.png`, `desktop/electron/assets/orin-logo.ico`, `frontend/src/assets/orin-logo.png`, `frontend/src/assets/orin-favicon.svg`) continuam com a arte antiga (anel roxo com espinho central e estrela).

### 5.2 Design da Solução
1. **Arte do Novo Ícone (Estilo Discord)**:
   - Fundo em formato squircle / quadrado arredondado (`rx="36"` em canvas `160x160`, ou equivalente em `1024x1024`) com gradiente escuro profundo violeta-índigo (`#1E1A30` → `#12101B` e borda interna sutil) contrastando com o mascote oficial do Orin centralizado em lavanda (`#B4A4EF` com volume 3D suave `#E4DCFF` → `#B4A4EF` → `#8F7AE0` e olhos/sorriso em `#282333`).
2. **Atualização Completa dos Assets**:
   - Atualizar `frontend/src/assets/orin-favicon.svg`.
   - Gerar e substituir `desktop/electron/assets/orin-logo.png` (`1024x1024` RGBA), `frontend/src/assets/orin-logo.png` (`1024x1024` RGBA) e `frontend/public/orin-logo.png`.
   - Gerar e substituir `desktop/electron/assets/orin-logo.ico` contendo todas as camadas de resolução do Windows (`16x16`, `24x24`, `32x32`, `48x48`, `64x64`, `128x128`, `256x256`).
   - Atualizar o emblema de splash em `desktop/electron/splash.html` para exibir o mascote do Orin em vez da letra `"O"`.

---

## 6. Caso 6 — Contraste e Paleta do `OrinMascot` no Modo Light

### 6.1 Causa Raiz
- Em `frontend/src/styles/theme.css` (linhas 267–271 e 410–589), o modo Light redefiniu `--orin-accent: #6d28d9`, `--orin-accent-strong: #5b21b6` e `--orin-accent-soft: #4c1d95` (Violet 900 — **mais escuro** que a cor base!) para garantir contraste de textos/botões no fundo claro.
- Porém, `OrinMascot.tsx` (linhas 78–83 e 108–112) usa `--orin-accent-soft` na parada `0%` do `<radialGradient>` (o brilho no topo esquerdo da cabeça) e `--orin-accent` na parada `55%`, com olhos e boca fixos em `#282333`.
- Resultado no modo Light: o "brilho" no rosto do mascote vira uma mancha roxa escura (`#4c1d95`), o corpo fica roxo saturado escuro (`#6d28d9`), e os olhos/boca em `#282333` ficam com contraste de apenas **1.25:1 a 1.85:1** (e no tema `mono` claro, literal preto sobre preto `#18181b`). Além disso, a sombra no chão (`rgba(0, 0, 0, 0.34)`) fica pesada demais sobre o fundo creme `#FAF9F6`.

### 6.2 Design da Solução
1. **Tokens Escopados para `.orin-mascot` no Modo Light (`theme.css` & `OrinMascot.tsx`)**:
   - Em `OrinMascot.tsx`, usar `var(--orin-mascot-shadow, rgba(0, 0, 0, 0.34))` na elipse de sombra e `var(--orin-mascot-feature, #282333)` nos olhos e sorriso.
   - Em `frontend/src/styles/theme.css`, para `[data-theme="light"] .orin-mascot` (e `@media (prefers-color-scheme: light) :root[data-theme="system"] .orin-mascot`) em cada um dos 6 acentos (`violet`, `sapphire`, `emerald`, `amber`, `rose`, `mono`), definir `--orin-accent-soft` (highlight `0%` claro), `--orin-accent` (corpo médio-pastel luminoso) e `--orin-accent-strong` (borda `100%` com definição suave sobre fundo claro), garantindo **contraste >= 7.5:1** entre o corpo do mascote e os olhos/boca `#282333`:
     - **Violet (Padrão)**: Highlight `#E4DCFF` → Corpo `#B4A4EF` → Borda `#8870DF`, sombra `rgba(28, 22, 48, 0.12)`.
     - **Sapphire**: Highlight `#DCE8FF` → Corpo `#82A8F8` → Borda `#547EEB`.
     - **Emerald**: Highlight `#D1FAE5` → Corpo `#5CE0B2` → Borda `#22B886`.
     - **Amber**: Highlight `#FEF3C7` → Corpo `#FCD34D` → Borda `#F59E0B`.
     - **Rose**: Highlight `#FCE0E8` → Corpo `#F4A2B8` → Borda `#E47293`.
     - **Mono**: Highlight `#F4F4F6` → Corpo `#D4D4D8` → Borda `#A1A1AA`.

---

## 7. Caso 7 — Loop Repetitivo de Ferramentas e Perda de Contexto no Orin Code com Assinatura Claude (`claude -p`)

### 7.1 Causa Raiz
1. **Descarte de `tool_use` e `tool_result` em `_format_bridge_conversation_transcript` (Causa Principal do Loop)**:
   - Quando a sessão usa o provider `"anthropic"` (incluindo `Assinatura Claude Code (CLI Local)` em `http://127.0.0.1:18447`), `AgenticTurnRuntime._assistant_tool_message` (`src/agentos/agentic/runtime.py:2908-2924`), `_tool_result_messages` (`runtime.py:2948-2950`) e `transcript.py` (`src/agentos/agentic/transcript.py:81-118`) estruturam o histórico no **formato nativo de blocos da Anthropic**:
     - Chamada da ferramenta no assistant: `{"role": "assistant", "content": [{"type": "text", "text": "..."}, {"type": "tool_use", "id": "...", "name": "Bash", "input": {...}}]}` (sem a chave OpenAI `"tool_calls"`).
     - Resultado da ferramenta: `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]}` (com `role: "user"` em vez de `"tool"`, e chave `"content"` em vez de `"text"`).
   - Porém, `stream_claude_code_bridge` (`src/agentos/provider_catalog/claude_code.py:775`) chama `_format_bridge_conversation_transcript(messages)` (`src/agentos/provider_catalog/opencode.py:417-485`), que **só suportava o formato OpenAI** (`msg.get("tool_calls")` e `role == "tool"`):
     - Nos turnos `assistant`, ele extraía apenas `block.get("text")` (`"Checando o estado do git na pasta."`) e ignorava completamente os blocos `{"type": "tool_use"}`.
     - Nos turnos `user` contendo `tool_result`, como o bloco usa `"content"` e não `"text"`, `text_content` ficava vazio (`""`) e ele emitia um `[User]\n` em branco, **descartando 100% da saída do comando executado**!
   - Consequentemente, a cada iteração do loop, o `claude -p` recebia um histórico onde parecia que ele tinha apenas dito `"Checando o estado do git na pasta."` sem invocar nenhuma ferramenta, seguido de uma mensagem vazia do usuário — levando-o a pedir desculpas (*"Parece que nas últimas tentativas eu anunciei a checagem sem chegar a executar nada. Rodando agora:"*) e repetir o comando 30+ vezes seguidas.
2. **Duplicação de Texto Pré-Tool entre Turnos em `PostgresChatStore.history_for_turn` (Causa Secundária)**:
   - Em `src/agentos/conversations/chat.py:2221-2236`, durante um turno com $k$ iterações de ferramentas, `self.store.delta` concatena o texto de todas as iterações ($T_1 + T_2 + \dots + T_{\text{final}}$) em `conversation_messages.content`.
   - Ao reidratar o histórico para o turno seguinte, `history_for_turn` insere cada passo `STEP_ASSISTANT_TOOL_CALL` (que já contém $T_i$) e no final ainda adiciona `row["content"]` completo ($T_1 + T_2 + \dots + T_{\text{final}}$), duplicando todas as falas intermediárias do assistente no contexto dos turnos subsequentes.

### 7.2 Design da Solução
1. **Suporte Completo a Blocos Anthropic (`tool_use` e `tool_result`) em `_format_bridge_conversation_transcript` (`src/agentos/provider_catalog/opencode.py`)**:
   - Em mensagens `role == "assistant"`: além de `msg.get("tool_calls")`, percorrer `content` (quando lista) buscando blocos `block.get("type") == "tool_use"` e serializá-los como `<orin_tool_call>{"name": ..., "arguments": ...}</orin_tool_call>`.
   - Em mensagens `role == "user"`: quando `content` for lista, identificar blocos `block.get("type") == "tool_result"`, extrair `tool_use_id` e o texto de `block.get("content")` (string ou lista de blocos) e emitir `[Tool Result id=...]\n<conteúdo>`, sem gerar blocos `[User]\n` vazios.
   - Em `_parse_orin_tool_calls`: aceitar tanto `"arguments"` quanto `"input"` no JSON de `<orin_tool_call>` e resolver nomes de ferramentas case-insensitive / aliases públicos (`Bash` <-> `run_command`) quando presentes em `allowed_names`.
2. **Deduplicação do Texto Acumulado na Reidratação de Turnos (`src/agentos/conversations/chat.py`)**:
   - Em `PostgresChatStore.history_for_turn` / `_rehydrated_steps`, quando uma mensagem do assistente possuir passos de tool call reidratados (`replay[message_id]`), remover de `row["content"]` os prefixos de texto $T_1, \dots, T_k$ que já foram emitidos nos passos `STEP_ASSISTANT_TOOL_CALL`, mantendo na mensagem final apenas o texto pós-ferramenta $T_{\text{final}}$ (ou omitindo a mensagem final vazia caso o turno tenha encerrado logo após a ferramenta).

---

## 8. Estratégia de Testes e Verificação Visual
1. **Testes Unitários e Adversariais (TDD — Backend `pytest` & Frontend `vitest`)**:
   - Caso 1: `ChatPageCodePhase5.test.tsx` e `CodeWorkbenchPanelRef08.test.tsx` validando bancada recolhida por padrão, botão de toggle no header, abertura via `"Ver alterações"` e botão de fechar.
   - Caso 2: `test_wake_word_engine.py` e testes de `GlobalWakeWordListener` validando que silêncio/ruído branco/cliques curtos e palavras como `"além"`/`"alma"`/`"eram"` nunca disparam wake word, que `vad_filter=True` é preservado no Whisper e que `"Oi Orin"` real continua funcionando.
   - Caso 3: Teste em `ProvidersPhase7Ref14.test.tsx` e inspeção real de bounding boxes no navegador garantindo zero overflow horizontal em `/integrations/providers/anthropic`.
   - Caso 4: Testes unitários para `<OrinActivityCenter />` e atualização de `ChatPageRef03AndTeam.test.tsx` cobrindo bolinha verde desligada quando ocioso, acesa quando há aprovação pendente/novidade, e aprovação inline funcionando.
   - Caso 5: Verificação de integridade dos assets `.svg`, `.png` (`1024x1024`) e `.ico` multi-size + testes de `Brand.test.tsx` / `brandIdentity.test.ts`.
   - Caso 6: Testes em `OrinMascotAndBrand.test.tsx` e `ThemeContext.test.tsx` validando os tokens do mascote no modo Light e contraste.
   - Caso 7: Novos testes em `tests/unit/provider_catalog/test_claude_code_bridge.py` e `tests/unit/conversations/` simulando múltiplos passos de `tool_use` + `tool_result` no formato Anthropic na bridge `claude -p` e reidratação de `history_for_turn` sem duplicação.
2. **Verificação Visual Obrigatória no Navegador Real (Playwright)**:
   - Subir backend + frontend e validar visualmente em Desktop e Mobile, Dark e Light mode:
     - Orin Code com bancada recolhida e expandida;
     - Drawer da Anthropic sem qualquer corte à direita;
     - Mascote no topo sem bolinha verde quando ocioso e abrindo o popover de aprovações/novidades ao clicar;
     - Mascote na Home em modo Light com cores suaves e alto contraste nos olhos/boca;
     - Novo ícone renderizado com fundo arredondado estilo Discord.
