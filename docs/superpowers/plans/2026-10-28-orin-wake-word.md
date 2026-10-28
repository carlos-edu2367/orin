# Plano de Implementação: Wake Word "Orin" em Segundo Plano para o Orin Friend Live

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar a detecção de Wake Word **"Orin"** (com alta tolerância a sotaques regionais do PT-BR e pronúncias internacionais) rodando em segundo plano (bandeja do sistema, janela minimizada/oculta ou fora do modo Live), abrindo automaticamente a janela do app direto no **Modo Live do Orin Friend** e encaminhando qualquer comando falado na mesma frase.

**Architecture:**
1. **Motor Fonético Multi-Sotaque & Condicionamento de STT (`src/agentos/live_voice/wake_word.py`, `engines.py`, `whisper_worker.py`, `session.py`):**
   - Condicionamento de vocabulário (`prompt_hint` / `initial_prompt = "Orin. Oi Orin, ei Orin, fala Orin, olá Orin, hey Orin, ok Orin."`) em `LocalWhisperEngine` (in-process e `whisper_worker.py`), OpenAI e Groq.
   - `WakeWordMatcher` com normalização de vocativos (`oi`, `ei`, `fala`, `hey`, `olá`, `ô`, `ok`, `escuta`), tabela de variantes fonéticas multi-sotaque (`orin`, `órin`, `orim`, `órim`, `oryn`, `ourin`, `ourim`, `aurin`, `horin`, `orem`, `órem`, `oren`, `ori`, `orinho`, `orion`, `urin`, `urim`, `olin`, `olim`, `olhin`, `o rin`, `oh rin`, `all in`, etc.), esqueleto fonético PT-BR/EN (`phonetic_skeleton` $\rightarrow$ `ORN`), lista de bloqueio anti-colisão (`hora`, `agora`, `olha`, `olho`, `ouro`, `outro`, `onde`, `ontem`, `ordem`, `origem`, `orçamento`, `orelha`) e extração de `command_remainder`.
   - Endpoint `POST /v1/live-voice/wake-check` validado com CSRF, limite de payload de áudio (máx. 5s PCM16) e classificado em `SCOPED` no `test_profile_isolation.py`.
2. **Integração Desktop Electron em Segundo Plano (`desktop/electron/main.cjs`, `desktop/electron/preload.cjs`, `frontend/src/vite-env.d.ts`):**
   - `backgroundThrottling: false` no `mainWindow.webPreferences` para manter `AudioWorklet` / `AudioContext` e timers ativos quando o Orin estiver oculto na bandeja do sistema (`hideToBackground`) ou minimizado.
   - Canal IPC `desktop:wake-live-voice` (`window.orinDesktop.wakeToLiveFriend()`) que restaura a janela da bandeja/minimizada, aplica foco no topo no Windows (`setAlwaysOnTop(true)` $\rightarrow$ `focus()` $\rightarrow$ `setAlwaysOnTop(false)`) e exibe a interface.
   - Exposição de `window.orinDesktop.onToggleLiveVoice(callback)` no `preload.cjs` para conectar também o item da bandeja `"Conversa Live (Orin Friend)"` e o atalho global `CommandOrControl+Shift+L`.
3. **Listener Global de Wake Word & Abertura Direta no Orin Friend Live (`wakeWord.ts`, `GlobalWakeWordListener.tsx`, `liveVoicePrefs.ts`, `App.tsx`, `ChatPage.tsx`, `LiveVoiceQuickSettings.tsx`, `BackgroundSection.tsx`):**
   - Preferência `wakeWordEnabled: boolean` (default `true`) em `LiveVoicePrefs`.
   - `GlobalWakeWordListener` montado globalmente em `App.tsx` (dentro de `SessionGate`), ativo apenas quando `wakeWordEnabled === true` e nem `LiveVoiceStage` nem `LiveVoiceQuickSettings` estão abertos (com cooldown pós-fechamento para evitar auto-disparo).
   - Ao detectar `"Orin"`, toca chime de confirmação (`<120ms`), chama `wakeToLiveFriend()`, garante a conversa do Orin Friend (`ensureFriendConversation`) e abre `/chats/{friendId}?live=1` (ou dispara evento `orin:open-friend-live`), enviando `command_remainder` como primeiro turno se o usuário já falou o comando junto com a wake word.

**Tech Stack:** Python 3.13+, FastAPI, faster-whisper, Electron (`main.cjs`/`preload.cjs`), React 19, TypeScript 6, WebAudio `AudioWorklet`, Vitest, Pytest, Playwright.

---

### Task 1: Motor Fonético Multi-Sotaque (`wake_word.py`), `prompt_hint` no Whisper Local e Endpoint `POST /v1/live-voice/wake-check`

**Files:**
- Create: `src/agentos/live_voice/wake_word.py`
- Modify: `src/agentos/live_voice/workers/whisper_worker.py`
- Modify: `src/agentos/live_voice/engines.py`
- Modify: `src/agentos/live_voice/session.py`
- Modify: `tests/integration/api/test_profile_isolation.py`
- Create: `tests/unit/live_voice/test_wake_word_engine.py`

- [x] **Step 1 (RED): Escrever testes unitários e de rota falhando em `tests/unit/live_voice/test_wake_word_engine.py`:**
  - Testar `match_wake_word(transcript, custom_names=...)` cobrindo:
    - Chamadas diretas e com vocativos: `"Orin"`, `"Oi Orin"`, `"Ei, Orin!"`, `"Fala Orin"`, `"Hey Orin"`, `"Olá Orin"`, `"Ô Orin"`, `"Ok Orin"`, `"Escuta Orin"`.
    - Variações de sotaques regionais e transcrições típicas de STT: `"Orim"`, `"Órin"`, `"Órim"`, `"Oryn"`, `"Ourin"`, `"Ourim"`, `"Aurin"`, `"Horin"`, `"Orem"`, `"Órem"`, `"Oren"`, `"Ori"`, `"Orinho"`, `"Orion"`, `"Urin"`, `"Urim"`, `"Olin"`, `"Olim"`, `"Olhin"`, `"O rin"`, `"Oh rin"`, `"Ô rin"`, `"All in"`, `"Ronin"`.
    - Extração de `command_remainder`: `"Oi Orin, que horas são agora?"` $\rightarrow$ `detected=True`, `wake_phrase="Oi Orin"`, `command_remainder="que horas são agora?"`; `"Orin"` $\rightarrow$ `detected=True`, `command_remainder=""`.
    - Suporte a `custom_names` (ex.: se o companheiro também tiver apelido customizado, mantendo `"Orin"` sempre ativo).
    - **Guarda Adversária Anti-Falso-Positivo:** Rejeitar frases comuns do português que começam com sons parecidos (`"Olha só isso"`, `"Hora de almoçar"`, `"Agora não posso"`, `"Ordem no tribunal"`, `"Onde você está?"`, `"Ontem foi legal"`, `"Ouro e prata"`, `"Outro dia a gente vê"`, `"Origem do problema"`, `"Orçamento aprovado"`, `"Orelha vermelha"`) e rejeitar menções perdidas no meio de frases longas.
  - Testar passagem de `prompt_hint` para `whisper_worker.transcribe` e `LocalWhisperEngine.transcribe`.
  - Testar endpoint `POST /v1/live-voice/wake-check`:
    - Com `transcript_hint` e com `pcm_base64` (usando STT configurado + `prompt_hint` de wake word).
    - Rejeição de `pcm_base64` acima do limite de segurança (DoS guard `> 5s`) com HTTP 422.
- [x] **Step 2 (GREEN): Implementar `wake_word.py`, atualizar `whisper_worker.py`, `engines.py`, `session.py` e classificar a rota em `test_profile_isolation.py`:**
  - Criar `src/agentos/live_voice/wake_word.py` com `WakeWordMatch`, `DEFAULT_WAKE_PROMPT_HINT`, `phonetic_skeleton_ptbr`, `match_wake_word` e `build_wake_prompt_hint`.
  - Em `src/agentos/live_voice/workers/whisper_worker.py` e `src/agentos/live_voice/engines.py`: repassar `prompt_hint` para `model.transcribe(..., initial_prompt=...)`.
  - Em `src/agentos/live_voice/session.py`: registrar `POST /v1/live-voice/wake-check` com validação Pydantic (`WakeCheckRequest`) e autorização `SCOPED`.
  - Em `tests/integration/api/test_profile_isolation.py`: adicionar `("POST", "/v1/live-voice/wake-check")` em `SCOPED`.
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/live_voice/test_wake_word_engine.py tests/integration/api/test_profile_isolation.py`.**

---

### Task 2: Suporte Electron em Segundo Plano (`backgroundThrottling: false`, IPC `desktop:wake-live-voice` e `onToggleLiveVoice`)

**Files:**
- Modify: `desktop/electron/main.cjs`
- Modify: `desktop/electron/preload.cjs`
- Modify: `frontend/src/vite-env.d.ts`
- Modify: `tests/unit/desktop/test_electron_shell.py` (ou teste equivalente da camada desktop)

- [x] **Step 1 (RED): Escrever testes verificando o contrato Electron (`main.cjs` e `preload.cjs`):**
  - Verificar que `webPreferences` em `desktop/electron/main.cjs` define `backgroundThrottling: false`.
  - Verificar que `desktop/electron/main.cjs` registra `ipcMain.handle('desktop:wake-live-voice', ...)` que chama `wakeWindowToForeground()` (restaurando da bandeja/minimizado e focando a janela).
  - Verificar que `desktop/electron/preload.cjs` expõe `wakeToLiveFriend` e `onToggleLiveVoice`.
- [x] **Step 2 (GREEN): Implementar em `desktop/electron/main.cjs`, `desktop/electron/preload.cjs` e `frontend/src/vite-env.d.ts`:**
  - Adicionar `backgroundThrottling: false` em `mainWindow` (`main.cjs`).
  - Implementar `wakeWindowToForeground()` em `main.cjs` (restaura se minimizado, destrói tray e mostra janela, faz `setAlwaysOnTop(true)` + `focus()` + `setAlwaysOnTop(false)` para garantir foco ao sair do segundo plano no Windows) e registrar `ipcMain.handle('desktop:wake-live-voice', ...)`.
  - Expor `wakeToLiveFriend: () => ipcRenderer.invoke('desktop:wake-live-voice')` e `onToggleLiveVoice: (callback) => ...` em `preload.cjs` e tipar em `frontend/src/vite-env.d.ts`.
- [x] **Step 3 (VERIFY): Rodar os testes de desktop e validar sintaxe de `main.cjs` / `preload.cjs`.**

---

### Task 3: Matcher Frontend, `GlobalWakeWordListener`, Abertura Direta no Orin Friend Live e Controles de UI

**Files:**
- Create: `frontend/src/features/conversations/live/wakeWord.ts`
- Create: `frontend/src/features/conversations/live/GlobalWakeWordListener.tsx`
- Modify: `frontend/src/api/liveVoice.ts`
- Modify: `frontend/src/features/conversations/live/liveVoicePrefs.ts`
- Modify: `frontend/src/features/conversations/LiveVoiceStage.tsx`
- Modify: `frontend/src/features/conversations/LiveVoiceQuickSettings.tsx`
- Modify: `frontend/src/features/settings/BackgroundSection.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/tests/unit/OrinFriendAndLiveMode.test.tsx`

- [x] **Step 1 (RED): Escrever testes unitários/integração em `frontend/tests/unit/OrinFriendAndLiveMode.test.tsx`:**
  - Testar `matchWakeWord` em TypeScript (`wakeWord.ts`) com sotaques variados (`"Orin"`, `"Oi Orin"`, `"Ei Orim"`, `"Fala Órem"`, `"Ourin"`, `"Olin"`, `"All in"`, `"Oi Orin, me ajuda com o código"`), garantindo extração de `commandRemainder` e rejeição de falsos positivos (`"Olha isso"`, `"Agora vou sair"`, `"Hora do almoço"`).
  - Testar `normalizeLiveVoicePrefs` com `wakeWordEnabled` (default `true`) e toggle em `LiveVoiceQuickSettings` e `BackgroundSection`.
  - Testar `GlobalWakeWordListener`:
    - Quando `wakeWordEnabled === true` e `LiveVoiceStage` está fechado, captura um segmento curto de voz ou evento de wake, chama `checkWakeWord` (`POST /v1/live-voice/wake-check`), toca o chime de confirmação, aciona `window.orinDesktop?.wakeToLiveFriend?.()`, chama `ensureFriendConversation` e navega para `/chats/{friendId}?live=1` abrindo automaticamente o `LiveVoiceStage` no `ChatPage`.
    - Se o usuário falou `"Orin, qual a agenda de hoje?"`, além de abrir o `LiveVoiceStage` no Orin Friend, envia automaticamente `"qual a agenda de hoje?"` como primeiro turno em modo Live.
    - Quando `LiveVoiceStage` está aberto, o `GlobalWakeWordListener` pausa a escuta e libera o microfone imediatamente.
- [x] **Step 2 (GREEN): Implementar `wakeWord.ts`, `GlobalWakeWordListener.tsx`, API client, `ChatPage.tsx` e toggles de UI:**
  - Adicionar `checkWakeWord(client, payload, signal)` em `frontend/src/api/liveVoice.ts`.
  - Adicionar `wakeWordEnabled: boolean` (default `true`) em `frontend/src/features/conversations/live/liveVoicePrefs.ts`.
  - Criar `frontend/src/features/conversations/live/wakeWord.ts` com `matchWakeWord`, `playWakeChime`, e controle de presença (`setLiveVoiceStageActive` / `onLiveVoiceStageActiveChange`) para suspender a escuta quando o `LiveVoiceStage` ou `LiveVoiceQuickSettings` estiverem abertos.
  - Criar `frontend/src/features/conversations/live/GlobalWakeWordListener.tsx` e montá-lo dentro de `SessionGate` em `frontend/src/app/App.tsx` (incluindo listener para `window.orinDesktop?.onToggleLiveVoice`).
  - Atualizar `ChatPage.tsx` para reagir a `?live=1` / `location.state.openLiveVoice` e ao evento `orin:open-friend-live`, abrindo `LiveVoiceStage` e despachando `initialLiveUtterance` quando presente.
  - Adicionar o toggle de Wake Word em `LiveVoiceQuickSettings.tsx` e `BackgroundSection.tsx`.
- [x] **Step 3 (VERIFY): Rodar `npm test` em `frontend` e `npm run build` (typecheck + bundle).**

---

### Task 4: Verificação Completa End-to-End (Playwright no Navegador + Suíte Pytest + Vitest)

**Files:**
- Modify: `frontend/tests/e2e/orin-friend-live.spec.ts`

- [x] **Step 1: Adicionar cenário E2E no Playwright (`frontend/tests/e2e/orin-friend-live.spec.ts`) e testar visualmente no navegador:**
  - Validar que falar `"Orin"` (ou disparar a detecção da wake word enquanto está em outra página como `/` ou `/settings/background`) navega automaticamente para a conversa do **Orin Friend** e abre o **`LiveVoiceStage`** em tela cheia no estado ativo.
  - Validar que o toggle de Wake Word aparece e funciona tanto em `Configurar voz` (`LiveVoiceQuickSettings`) quanto em `Configurações → Segundo plano` (`BackgroundSection`).
- [x] **Step 2: Rodar todas as suítes de verificação (`verification-before-completion`):**
  - `pytest` (unit + integration)
  - `npm test` (Vitest)
  - `npm run build` (TypeScript + Vite)
  - Playwright E2E / inspeção visual no navegador.
