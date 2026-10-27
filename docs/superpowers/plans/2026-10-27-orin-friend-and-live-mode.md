# Plano de Implementação: Orin Friend & Modo LIVE (Full-Duplex + Orquestração Orin Code)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar o companheiro contínuo fixado **Orin Friend** com **Memória Dual-Layer & Personalidade Evolutiva ("Caixa de Vidro" + `~/.orin/friend/SOUL.md`)**, **Modo LIVE Full-Duplex** (streaming paralelo de `<speak>...</speak>`, chunking prosódico PT-BR em duas marchas, *Audio Sprites* de pensamento `<10ms`, barge-in `<80ms` com rastreamento da última palavra falada, multiproviders STT/TTS locais e cloud com sidecar supervisionado e preset para RTX 4060) e **Orquestração de Sessões `Orin Code` em Tempo Real com Desperdício Zero de Tokens**.

**Architecture:**
1. **Persistência, Memória Dual-Layer e Alma Evolutiva (`schema.py`, migration `0054`, `agent_memory.py`, `friend_persona.py`):** Tabela `user_friend_personas` sincronizada bidirecionalmente com `~/.orin/friend/SOUL.md`, evolução amortecida pós-sessão (`delta_max = 0.03`, `candor >= 0.50`), extensão de `agent_memories` com `scope_type='friend'` (`episodic`, `inside_joke`, `milestone`, `emotional`, `emotional_valence`, `salience`) e recuperação automática híbrida (`relevant_for_friend`) injetada a cada turno.
2. **Parser `<speak>`, Chunking Prosódico PT-BR e Vocalizações de Pensamento (`speak_stream.py`, `live_voice/sprites.py`):** FSM tolerante a falhas no nível de `StreamKind.TEXT` do `AgenticTurnRuntime` (compatível com os 7 providers), segmentador prosódico em duas marchas (`"Olá"` | `"meu nome é X"` | `"posso te auxiliar com várias coisas"`), normalizador PT-BR em tempo real (`R$`, `14h30`, `Sr.`, siglas), banco de *Audio Sprites* (`<10ms`) e `AudioArbitrator` (supressão preditiva em `SPEAK_OPENED`, *Grace Window* `<=200ms` e *Cosine Fade-Out* `90ms`).
3. **Ponte `Orin Friend <-> Orin Code` Zero-Token-Waste (`code_control.py`, `code_milestone_watcher.py`):** Tool `control_code_session` (`start`, `send_message`, `approve_or_answer`, `status`, `stop`) + `CodeSessionMilestoneWatcher` em 3 Tiers: **Tier 0** (eventos de leitura/busca atualizam apenas o card visual, `0 tokens`), **Tier 1** (marcos de `todo_write` / reta final / início de testes vão *Direct-to-TTS* com templates da persona, `0 tokens` de LLM + recibo de 15 tokens no `ReminderQueue`) e **Tier 2** (aprovações pendentes, erros ou conclusão acordam o Orin Friend com um `CompactCodeDigest` `<150 tokens`).
4. **Motor Full-Duplex Multi-Provider & Sidecar Local (`live_voice/`, `launcher/internal.py`, `gateway.py`):** WebSocket `/v1/conversations/{id}/live`, `VoiceHardwareGovernor` (adaptável por GPU/CPU, habilitando `Chatterbox-Multilingual-pt-br` 500M / `F5-TTS-pt-br` 335M + `faster-whisper large-v3-turbo` INT8 na RTX 4060 8GB, ou `Kokoro-82M ONNX` em CPU), `SileroVAD` + detector semântico de fim de turno PT-BR, `ProsodyContinuityEngine` (*Rolling Audio Tail* + crossfade Hann `20ms`), `SpokenTranscriptLedger` no barge-in e processo `orin-voice-sidecar`.
5. **Frontend & Desktop UX (`WorkspaceNavigation.tsx`, `Composer.tsx`, `LiveVoiceStage.tsx`, `FriendSoulDrawer.tsx`, `CodeSessionLiveCard.tsx`, `MediaModelsSetting.tsx`):** Item fixado `◈ Orin Friend` no topo da Sidebar, botão `((•)) LIVE` global (`Alt+L`), `AudioWorklet` com 4 barreiras anti-eco, visualização de canal duplo (`<speak>` na voz vs. artefatos/código silenciosos na timeline), painel "Caixa de Vidro" dos traços/memórias e seletores independentes de STT e TTS.

**Tech Stack:** Python 3.13+, FastAPI (WebSockets + REST + SSE), SQLAlchemy 2 / Alembic (SQLite local WAL / PostgreSQL), ONNX Runtime, React 19, TypeScript 6, WebAudio `AudioWorklet`, Vitest, Playwright.

---

### Task 1: Persistência SQL (`0054`), Memória Dual-Layer (`scope_type='friend'`) e Motor de Personalidade Evolutiva (`SOUL.md`)

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py`
- Create: `src/agentos/persistence/postgres/migrations/versions/0054_orin_friend_and_live_mode.py`
- Modify: `src/agentos/persistence/postgres/agent_memory.py`
- Create: `src/agentos/memory/friend_persona.py`
- Modify: `src/agentos/memory/consolidation.py`
- Modify: `src/agentos/reading/store.py`
- Create: `tests/unit/memory/test_friend_persona_and_dual_memory.py`
- Modify: `tests/unit/persistence/test_postgres_schema.py`

- [x] **Step 1 (RED): Escrever testes unitários falhando em `tests/unit/memory/test_friend_persona_and_dual_memory.py` e `tests/unit/persistence/test_postgres_schema.py`:**
  - `PostgresAgentMemoryStore.relevant_for_friend(task, limit=18)` retorna simultaneamente memórias da **Camada 1** (`scope_type in {'user', 'project'}`) e da **Camada 2** (`scope_type == 'friend'`, `kind in {'episodic', 'inside_joke', 'milestone', 'emotional'}`), aplicando penalidade de *cooldown* para memórias `inside_joke` usadas nos últimos 2 turnos.
  - `FriendPersonaStore`: cria persona default (`warmth=0.65`, `humor=0.50`, `sarcasm=0.25`, `formality=0.35`, `verbosity=0.40`, `candor=0.70`, `energy=0.55`, `bond_stage="buddy"` quando já existem memórias globais do usuário), aplica evolução amortecida (`apply_reflection_deltas`) respeitando `abs(delta) <= 0.03` por sessão e os limites (`candor >= 0.50`, `sarcasm <= 0.75`).
  - Sincronização bidirecional com `~/.orin/friend/SOUL.md`: salvar no banco gera o arquivo Markdown com YAML frontmatter; editar manualmente o arquivo no disco e chamar `sync_from_disk_if_modified()` atualiza os traços e notas no banco.
  - Hardening de segurança (`W3-R27` + Taint Gate): outputs de tools nunca entram na consolidação da persona nem criam memórias afetivas.
- [x] **Step 2 (GREEN): Implementar schema, migration `0054`, `relevant_for_friend` e `FriendPersonaStore`:**
  - Em `src/agentos/persistence/postgres/schema.py` e `0054_orin_friend_and_live_mode.py`:
    - Adicionar colunas `emotional_valence` (`Float`, server_default `"0.0"`) e `salience` (`Float`, server_default `"0.5"`) em `agent_memories`.
    - Criar tabela `user_friend_personas` (`user_id` PK, `version`, `bond_stage`, `affinity_score`, `current_mood`, `traits_json`, `style_notes_json`, `compiled_rubric`, `interaction_count`, `soul_sha256`, `last_reflected_at`, `updated_at`).
    - Expandir `PostgresMediaModelSettingsStore` (`src/agentos/reading/store.py`) para aceitar a modalidade `"stt"` (além de `"image"`, `"audio"`, `"video"`) e configurações de voz (`voice_id`, `reference_audio_path`).
  - Em `src/agentos/persistence/postgres/agent_memory.py`: implementar `relevant_for_friend` e suporte a `scope_type="friend"`.
  - Em `src/agentos/memory/friend_persona.py`: implementar `FriendPersonaState`, `FriendPersonaStore`, compilador de `compiled_rubric` (cache-safe), espelhamento `~/.orin/friend/SOUL.md` e `FriendReflectionConsolidator`.
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/memory/test_friend_persona_and_dual_memory.py tests/unit/persistence/test_postgres_schema.py`.**

---

### Task 2: Parser Streaming `<speak>`, Chunking Prosódico PT-BR em Duas Marchas, Normalizador e `AudioSpriteBank` / `AudioArbitrator`

**Files:**
- Create: `src/agentos/agentic/speak_stream.py`
- Create: `src/agentos/live_voice/__init__.py`
- Create: `src/agentos/live_voice/sprites.py`
- Create: `tests/unit/agentic/test_speak_stream_and_ptbr_chunker.py`
- Create: `tests/unit/live_voice/test_sprites_and_arbitrator.py`

- [x] **Step 1 (RED): Escrever testes unitários exaustivos em `tests/unit/agentic/test_speak_stream_and_ptbr_chunker.py` e `tests/unit/live_voice/test_sprites_and_arbitrator.py`:**
  - **Teste do Exemplo Canônico do Usuário:** Alimentar token a token `"<speak>Olá, meu nome é X, posso te auxiliar com várias coisas!</speak>"` no `SpeakStreamParser` + `PTBRProsodicChunker` deve produzir exatamente 3 chunks com `clean_text`:
    1. `"Olá"` (`tts_text="Olá,"`, `boundary_kind=COMMA`, `is_first_in_block=True`)
    2. `"meu nome é X"` (`tts_text="meu nome é X,"`, `boundary_kind=COMMA`)
    3. `"posso te auxiliar com várias coisas"` (`tts_text="posso te auxiliar com várias coisas!"`, `boundary_kind=EXCLAMATION`, `is_last_in_block=True`)
  - **Testes de Casos Adversários e Armadilhas PT-BR:**
    - Tags divididas em múltiplos tokens (`"<"`, `"sp"`, `"eak>"`, `"</s"`, `"peak>"`).
    - Tag `<speak>` não fechada quando chega `TOOL_CALL` ou `FINISH` (auto-close + flush).
    - Abreviações e números não quebram chunk: `"<speak>Sr. Carlos, o lote custa R$ 1.250,50 às 14h30 na Av. Paulista, tudo certo?</speak>"` -> não quebra em `Sr.`, `1.250,50`, `14h30` nem `Av.`, e normaliza para `"senhor Carlos"`, `"1250 reais e 50 centavos"`, `"14 horas e 30 minutos"`, `"avenida Paulista"`.
    - Guarda de clíticos finais (`FORBIDDEN_CHUNK_ENDINGS`): nunca quebra deixando `"de"`, `"para"`, `"que"`, `"é"`, `"mas"` isolado no fim de uma chunk; `"Mas, pensando bem, vamos lá."` aglutina `"Mas, pensando bem"`.
    - `stripNonSpeakableContent`: se o LLM colocar um bloco ```` ```python ... ``` ```` ou URL `https://...` dentro de `<speak>`, remove antes do TTS e substitui por `"deixei o código na tela"` / `"o link na tela"`.
  - **Testes do `AudioArbitrator` e `ThinkingVocalizationController`:**
    - Dispara sprite `THINKING_SHORT` (`"Hmm..."`) quando `AGENT_THOUGHT_DELTA` ocorre sem `<speak>` aberto por `>320ms`, usando *Shuffle Bag* sem repetição consecutiva.
    - Bloqueia novos fillers no exato instante em que `SPEAK_OPENED` é emitido.
    - Se a Chunk 0 do TTS chega quando faltam `140ms` (`<=200ms`) para o filler `"Hmm..."` terminar, aplica *Grace Window* (espera os `140ms` + `40ms` de pausa). Se faltam `600ms` (`>200ms`), aplica *Cosine Fade-Out* de `90ms` + `45ms` de pausa.
- [x] **Step 2 (GREEN): Implementar `src/agentos/agentic/speak_stream.py` e `src/agentos/live_voice/sprites.py`:**
  - Implementar `SpeakStreamParser`, `PTBRProsodicChunker`, `PTBRTextNormalizer` e `strip_non_speakable_content` em `src/agentos/agentic/speak_stream.py`.
  - Implementar `AudioSpriteBank`, `ThinkingVocalizationController` e `AudioArbitrator` em `src/agentos/live_voice/sprites.py`.
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/agentic/test_speak_stream_and_ptbr_chunker.py tests/unit/live_voice/test_sprites_and_arbitrator.py`.**

---

### Task 3: Acoplamento no `AgenticTurnRuntime`, `TurnSession`, Conversa Fixada `Orin Friend` (`mode="friend"`) e Rotas da API

**Files:**
- Modify: `src/agentos/agentic/events.py`
- Modify: `src/agentos/agentic/prompts.py`
- Modify: `src/agentos/agentic/runtime.py`
- Modify: `src/agentos/agentic/session.py`
- Modify: `src/agentos/conversations/chat.py`
- Modify: `src/agentos/api/gateway.py`
- Modify: `src/agentos/workers/chat.py`
- Create: `tests/unit/agentic/test_friend_runtime_integration.py`
- Create: `tests/unit/api/test_friend_routes.py`

- [x] **Step 1 (RED): Escrever testes em `tests/unit/agentic/test_friend_runtime_integration.py` e `tests/unit/api/test_friend_routes.py`:**
  - `POST /v1/conversations/friend/ensure` cria (ou retorna se já existir) a conversa única fixada `mode="friend"` do usuário, junto com o estado `FriendPersonaState`.
  - `GET /v1/friend/persona` e `PUT /v1/friend/persona` lêem e atualizam os traços da personalidade ("Caixa de Vidro") e refletem em `~/.orin/friend/SOUL.md`.
  - Durante um turno com `live_mode=True` (ou `mode="friend"`):
    - O modelo emite `<speak>Deixa eu ver sua agenda.</speak>` + `tool_call` + `<speak>Você tem reunião às 15h.</speak>`.
    - O `AgenticTurnRuntime` emite os eventos `SPEAK_BLOCK_STARTED` e `SPEAK_CHUNK_READY` da 1ª fala **antes** de executar a tool, limpa as tags `<speak>` do `conversation_messages.content` salvo no banco, e re-hidrata `<speak>` na projeção `_request_messages` para evitar *format drift*.
    - **Watchdog Anti-Mentira Otimista:** Se uma tool mutável falhar (`status="failed"`) após um `<speak>` otimista e o turno terminar sem novo `<speak>` explicando o erro, o runtime emite automaticamente um chunk de retratação sonora (`"Atenção: houve uma falha ao executar a ação. Veja o detalhe na tela."`).
- [x] **Step 2 (GREEN): Implementar integração no Runtime, Session, ChatStore e Gateway:**
  - Adicionar `SPEAK_BLOCK_STARTED`, `SPEAK_DELTA`, `SPEAK_CHUNK_READY`, `SPEAK_FILLER_TRIGGERED`, `SPEAK_INTERRUPTED`, `SPEAK_FINISHED` em `src/agentos/agentic/events.py`.
  - Adicionar `FRIEND_SYSTEM_PROMPT_SECTION` e `LIVE_MODE_SPEAK_GUIDANCE` em `src/agentos/agentic/prompts.py`.
  - Integrar `SpeakStreamParser` e `PTBRProsodicChunker` em `AgenticTurnRuntime._run()` (`src/agentos/agentic/runtime.py`).
  - Atualizar `TurnSession.build_system_prompt()` (`src/agentos/agentic/session.py`) para injetar a rubrica `compiled_rubric` no prompt estável e as memórias Dual-Layer (`relevant_for_friend`) + `current_mood` no `volatile_prompt`.
  - Permitir `mode="friend"` em `src/agentos/conversations/chat.py` e criar os endpoints `/v1/conversations/friend/ensure` e `/v1/friend/persona` em `src/agentos/api/gateway.py`.
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/agentic/test_friend_runtime_integration.py tests/unit/api/test_friend_routes.py tests/unit/agentic/test_phase_controller.py`.** (Garantindo que o teto de 16 tools em `test_phase_controller.py` continua 100% verde!).

---

### Task 4: Orquestração `Orin Friend <-> Orin Code` em Tempo Real com Desperdício Zero de Tokens (`control_code_session` & `CodeSessionMilestoneWatcher`)

**Files:**
- Modify: `src/agentos/agentic/tools/orin/projects.py`
- Create: `src/agentos/conversations/code_milestone_watcher.py`
- Modify: `src/agentos/agentic/session.py`
- Create: `tests/unit/conversations/test_code_milestone_watcher.py`

- [x] **Step 1 (RED): Escrever testes unitários em `tests/unit/conversations/test_code_milestone_watcher.py`:**
  - No modo `friend`, a tool `control_code_session` (ou extensão de `propose_code_session` com `action="start" | "send_message" | "approve_or_answer" | "status" | "stop"`, mantendo o mesmo slot no orçamento de 16 tools!) cria uma sessão `mode="code"`, despacha a primeira mensagem imediatamente e registra o `CodeSessionMilestoneWatcher`.
  - **Teste de Desperdício Zero de Tokens (3 Tiers):**
    - Simular 20 eventos `filesystem.read`, `filesystem.write` e `assistant.delta` na sessão `Orin Code` -> **Tier 0**: `0` chamadas ao LLM do Orin Friend e `0` chunks de TTS; apenas emite `CODE_SESSION_PROGRESS` para o card visual da UI.
    - Simular `todo_write` avançando para `3/4` concluídas (`active_form="Rodando testes unitários"`) -> **Tier 1 (Reta Final)**: `0` chamadas ao LLM do Orin Friend! O watcher gera diretamente o evento `SPEAK_CHUNK_READY` (*"Já estamos na reta final do projeto, senhor! 3 de 4 etapas concluídas, agora rodando testes unitários."*) e insere um recibo de `<20 tokens` no `ReminderQueue` do Orin Friend. Respeita cooldown de `30s` entre falas de Tier 1.
    - Simular `PlanApprovalCard` (`waiting_user`) ou `turn.completed` -> **Tier 2**: dispara wakeup/reminder no Orin Friend com `CompactCodeDigest` JSON `<150 tokens` (contendo título, todos concluídos, arquivos alterados e pergunta/status, sem logs brutos).
    - Chamar `control_code_session(action="approve_or_answer", code_conversation_id=..., decision="approve")` aprova o plano na sessão `Orin Code` e retoma a execução.
- [x] **Step 2 (GREEN): Implementar `CodeSessionMilestoneWatcher` e ações de controle em `projects.py`:**
  - Criar `src/agentos/conversations/code_milestone_watcher.py` com o classificador de 3 Tiers, gerador determinístico de frases de marco calibrado pela persona, controle de cooldown de `30s` e construtor de `CompactCodeDigest` (`<150 tokens`).
  - Estender `ProjectTools` em `src/agentos/agentic/tools/orin/projects.py` para suportar controle direto de sessões `Orin Code` (`start`, `send_message`, `approve_or_answer`, `status`, `stop`).
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/conversations/test_code_milestone_watcher.py tests/unit/agentic/test_phase_controller.py`.**

---

### Task 5: Motor de Áudio Full-Duplex (`WS /v1/conversations/{id}/live`), Multiproviders STT/TTS, Barge-in (`SpokenTranscriptLedger`) e Sidecar Local (`orin-voice-sidecar`)

**Files:**
- Create: `src/agentos/live_voice/hardware_governor.py`
- Create: `src/agentos/live_voice/vad_eou.py`
- Create: `src/agentos/live_voice/prosody_continuity.py`
- Create: `src/agentos/live_voice/providers.py`
- Create: `src/agentos/live_voice/session.py`
- Create: `src/agentos/live_voice/sidecar.py`
- Modify: `src/agentos/launcher/internal.py`
- Modify: `src/agentos/api/gateway.py`
- Create: `tests/unit/live_voice/test_live_voice_engine.py`

- [x] **Step 1 (RED): Escrever testes unitários e de integração em `tests/unit/live_voice/test_live_voice_engine.py`:**
  - `VoiceHardwareGovernor`: ao detectar GPU com `8 GB` de VRAM (perfil RTX 4060), recomenda o preset `local_high_naturalness` (`Chatterbox-Multilingual-pt-br` 500M / `F5-TTS-pt-br` 335M + `faster-whisper large-v3-turbo` INT8); ao detectar `<4 GB` de VRAM livre ou CPU-only, recomenda `Kokoro-82M ONNX` + `Silero VAD` em CPU.
  - `PTBRSemanticEOUDetector`: frase terminando em `"Quero criar uma rota..."` com pontuação/queda de pitch fecha turno em `~380ms`; frase terminando em *Hold Marker* (`"Eu estava pensando se a gente pode usar..."` ou `"mas"`) estende o timeout para `~1400ms`.
  - `ProsodyContinuityEngine`: aplica *Rolling Audio Tail* (`600ms`) para modelos zero-shot locais, remove silêncios mortos nas bordas, injeta pausa prosódica calibrada (`110ms` para vírgula, `220ms` para ponto) e aplica crossfade *Raised-Cosine* (Hann) de `20ms` entre chunks consecutivas sem cliques de fase.
  - `SpokenTranscriptLedger` & Barge-in: dada uma chunk com 8 palavras e duração de `2000ms`, se o cliente enviar `{"type": "barge_in", "epoch": 1, "chunk_seq": 0, "played_ms": 750}`, o ledger trunca a mensagem exatamente na 3ª palavra (`"Olá, meu nome— [interrompido pelo usuário]"`) e injeta `[SYSTEM_BARGE_IN_RECEIPT]` para o próximo turno.
  - WebSocket `/v1/conversations/{id}/live`: aceita conexão, processa comandos JSON (`config`, `barge_in`, `playback_progress`, `audio_speech_end`), roteia para os adapters de STT e TTS selecionados pelo usuário e transmite pacotes PCM/áudio + word timings.
- [x] **Step 2 (GREEN): Implementar `live_voice/*`, Sidecar e Endpoint WebSocket:**
  - Implementar `VoiceHardwareGovernor`, `PTBRSemanticEOUDetector`, `ProsodyContinuityEngine`, `SpokenTranscriptLedger`, `MultiProviderSTTAdapter` (Local Sidecar `faster-whisper`, Groq, Deepgram, OpenAI) e `MultiProviderTTSAdapter` (Local Sidecar `Chatterbox-pt-br`, `F5-TTS-pt-br`, `Kokoro-82M ONNX`, ElevenLabs, Cartesia, OpenAI).
  - Criar `src/agentos/live_voice/sidecar.py` e registrar o processo opcional sob demanda em `src/agentos/launcher/internal.py`.
  - Expor `WEBSOCKET /v1/conversations/{conversation_id}/live` e `GET /v1/live-voice/hardware-status` em `src/agentos/api/gateway.py`.
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/live_voice/test_live_voice_engine.py`.**

---

### Task 6: Frontend & Desktop UX (`Orin Friend` Fixado, `LiveVoiceStage`, Anti-Eco, `FriendSoulDrawer`, `CodeSessionLiveCard` e Configurações STT/TTS)

**Files:**
- Modify: `frontend/src/api/conversations.ts`
- Modify: `frontend/src/api/providers.ts`
- Modify: `frontend/src/features/conversations/WorkspaceNavigation.tsx`
- Modify: `frontend/src/features/conversations/Composer.tsx`
- Modify: `frontend/src/features/conversations/AgentPulse.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Create: `frontend/src/features/conversations/LiveVoiceStage.tsx`
- Create: `frontend/src/features/conversations/FriendSoulDrawer.tsx`
- Create: `frontend/src/features/conversations/CodeSessionLiveCard.tsx`
- Modify: `frontend/src/features/providers/MediaModelsSetting.tsx`
- Modify: `desktop/electron/main.cjs`
- Create: `frontend/tests/unit/OrinFriendAndLiveMode.test.tsx`
- Create: `frontend/tests/e2e/orin-friend-live.spec.ts`

- [x] **Step 1 (RED): Escrever testes unitários (`frontend/tests/unit/OrinFriendAndLiveMode.test.tsx`) e E2E (`frontend/tests/e2e/orin-friend-live.spec.ts`):**
  - Sidebar exibe o item fixado **`◈ Orin Friend`** no topo; clicar nele abre a sessão contínua do companheiro e exibe o botão para abrir a **"Caixa de Vidro" (`FriendSoulDrawer`)** com nível de vínculo, humor, sliders dos 7 traços e lista de memórias afetivas/piadas internas.
  - O botão **`((•)) LIVE`** aparece no `Composer` tanto no `Orin Friend` quanto em sessões `Chat` e `Code`, ativando o `LiveVoiceStage` com seus 5 estados visuais (*Ouvindo*, *Pensando/Vocalizando*, *Trabalhando em Paralelo*, *Falando com Legenda Viva*, *Interrompido*) e respeitando `prefers-reduced-motion`.
  - O `CodeSessionLiveCard` renderiza em tempo real o progresso `TodoWrite` (`3/4 concluídas`) quando o `Orin Friend` supervisiona uma sessão `Orin Code`, com link direto para abrir a sessão Code.
  - Em `/settings/media` (`MediaModelsSetting.tsx`), o usuário visualiza o diagnóstico de hardware (`VoiceHardwareGovernor`) e seleciona independentemente o modelo de **Voz (TTS)** e **Ouvido (STT)**.
- [x] **Step 2 (GREEN): Implementar os componentes Frontend, AudioWorklet Anti-Eco e Integração Electron:**
  - Adicionar `ensureFriendConversation`, `getFriendPersona`, `updateFriendPersona` e cliente WebSocket `connectLiveVoiceSession` em `frontend/src/api/conversations.ts`.
  - Fixar `◈ Orin Friend` no topo de `WorkspaceNavigation.tsx`.
  - Criar `LiveVoiceStage.tsx` com as 4 camadas Anti-Eco (`echoCancellation: true`, *Audio Ducking* de 250ms, *Text-Similarity Echo Gate* >=65% e fallback Push-to-Talk), `FriendSoulDrawer.tsx` e `CodeSessionLiveCard.tsx`.
  - Expandir `MediaModelsSetting.tsx` com o seletor de **STT (Transcrição de Voz)** e card de **Preset de Hardware Local (Chatterbox PT-BR / F5-TTS / Kokoro / Faster-Whisper)**.
  - Adicionar permissão de mídia local e atalho no Tray do Electron (`desktop/electron/main.cjs`).
- [x] **Step 3 (VERIFY): Rodar `npm test` em `frontend`, suíte completa `pytest` no backend e validação visual/interativa via Playwright no navegador.**
