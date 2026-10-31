# Correções de UX, Wake Word, Mascote, Central de Aprovações e Contexto do Orin Code — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolver os 7 problemas reportados no Orin v0.11.1: (1) bancada do Orin Code recolhida por padrão sob botão expansível, (2) eliminação de falsos positivos de Wake Word no silêncio, (3) correção do overflow horizontal no drawer da Anthropic e demais providers, (4) bolinha verde condicional no mascote do topo com Central de Novidades e Aprovações Inline, (5) atualização dos ícones desktop/web para o mascote com fundo arredondado estilo Discord, (6) correção do contraste e paleta do mascote no modo Light, e (7) correção da perda de contexto de `tool_use`/`tool_result` na bridge Claude Code CLI (`claude -p`) e deduplicação de histórico entre turnos.

**Architecture:** As tarefas são organizadas por domínio independente (7 módulos + verificação visual integrada no navegador real com Playwright), aplicando TDD estrito (`RED -> GREEN -> REFACTOR`) e testes adversariais em cada entrega.

**Tech Stack:** Python 3.12 (`pytest`, `FastAPI`, `faster-whisper`, `Pillow`), TypeScript + React 18 (`Vite`, `Vitest`, `@testing-library/react`, `Playwright`), Electron (`main.cjs`).

**Spec:** `docs/superpowers/specs/2026-10-31-correcoes-ux-voz-mascote-e-contexto-code-design.md`

## Global Constraints

- Seguir obrigatoriamente TDD (`RED -> GREEN -> REFACTOR`) em cada tarefa antes de alterar código de produção.
- Manter 100% de compatibilidade com os testes existentes de regressão (`pytest` no backend e `npx vitest run` no frontend).
- Criar testes adversariais (entradas vazias/silêncio, concorrência, estados parciais, blocos malformados).
- Realizar verificação visual real no navegador (Playwright Desktop e Mobile, Dark e Light mode) antes de concluir.

---

### Task 1: Histórico e Contexto do Orin Code com Assinatura Claude (`claude -p`) e Reidratação de Turnos (Caso 7)

**Files:**
- Modify: `src/agentos/provider_catalog/opencode.py:417-525`
- Modify: `src/agentos/conversations/chat.py:2221-2325`
- Test: `tests/unit/provider_catalog/test_claude_code_bridge.py`
- Test: `tests/unit/conversations/test_history_rehydration_dedup.py`

**Interfaces:**
- Consumes: `messages: Iterable[Mapping[str, Any]]` (OpenAI format and Anthropic block format from `AgenticTurnRuntime` and `transcript.py`)
- Produces: `_format_bridge_conversation_transcript(messages) -> tuple[list[str], str]`, `_parse_orin_tool_calls(text, allowed_names) -> tuple[str, list[dict[str, Any]]]`, and deduplicated `PostgresChatStore.history_for_turn`

- [ ] **Step 1: Write failing tests for Anthropic `tool_use`/`tool_result` serialization in `_format_bridge_conversation_transcript` and turn rehydration deduplication**

Add tests to `tests/unit/provider_catalog/test_claude_code_bridge.py` and `tests/unit/conversations/test_history_rehydration_dedup.py`:

```python
def test_format_bridge_transcript_preserves_anthropic_tool_use_and_tool_result_blocks() -> None:
    from agentos.provider_catalog.opencode import _format_bridge_conversation_transcript

    messages = [
        {"role": "system", "content": "System instructions"},
        {"role": "user", "content": "Cheque o git status da pasta."},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Checando o estado do git na pasta."},
                {
                    "type": "tool_use",
                    "id": "call_git_1",
                    "name": "Bash",
                    "input": {"command": "git status", "description": "Check git status"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_git_1",
                    "content": "On branch main\nnothing to commit, working tree clean",
                }
            ],
        },
    ]

    system_parts, prompt_text = _format_bridge_conversation_transcript(messages)
    assert system_parts == ["System instructions"]
    assert '<orin_tool_call>{"name": "Bash", "arguments": {"command": "git status", "description": "Check git status"}}</orin_tool_call>' in prompt_text
    assert "[Tool Result id=call_git_1]\nOn branch main\nnothing to commit, working tree clean" in prompt_text
    assert not prompt_text.endswith("[User]\n")
```

- [ ] **Step 2: Run tests to verify they fail (RED)**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/provider_catalog/test_claude_code_bridge.py -v`
Expected: FAIL (`<orin_tool_call>` and `[Tool Result id=call_git_1]` missing from `prompt_text`).

- [ ] **Step 3: Implement Anthropic block support in `_format_bridge_conversation_transcript`, harden `_parse_orin_tool_calls`, and deduplicate `history_for_turn`**

In `src/agentos/provider_catalog/opencode.py`:
- In `_format_bridge_conversation_transcript`:
  - When `role == "assistant"` and `isinstance(content, list)`, extract both `"text"` blocks and `"type": "tool_use"` blocks (`name`, `input`/`arguments`) and serialize each `tool_use` as `<orin_tool_call>{"name": tc_name, "arguments": tc_args}</orin_tool_call>`.
  - When `role == "user"` and `isinstance(content, list)`, extract `"type": "tool_result"` blocks (`tool_use_id`, `content` as string or list of text blocks) and emit `[Tool Result id={call_id}]\n{res_text}` without emitting an empty `[User]\n` turn.
- In `_parse_orin_tool_calls`:
  - Support both `"arguments"` and `"input"` keys inside `<orin_tool_call>`, and resolve case-insensitive / public-internal tool aliases when present in `allowed_names`.
- In `src/agentos/conversations/chat.py` (`history_for_turn`):
  - When `replay.get(message_id)` is non-empty for an assistant row, strip the cumulative pre-tool text segments already emitted in `replay[message_id]` from `row["content"]`, avoiding duplicate assistant narration across turns.

- [ ] **Step 4: Run tests to verify they pass (GREEN)**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/provider_catalog/test_claude_code_bridge.py tests/unit/conversations/ -v`
Expected: PASS

---

### Task 2: Eliminação de Falsos Positivos do Wake Word no Silêncio (Caso 2)

**Files:**
- Modify: `src/agentos/live_voice/workers/whisper_worker.py:62-84`
- Modify: `src/agentos/live_voice/engines.py:255-280`
- Modify: `src/agentos/live_voice/session.py:998-1047`
- Modify: `src/agentos/live_voice/wake_word.py:207-248, 380-395`
- Modify: `src/agentos/live_voice/providers.py:300-348`
- Modify: `frontend/src/features/conversations/live/GlobalWakeWordListener.tsx:28-35, 155-235`
- Test: `tests/unit/live_voice/test_wake_word_engine.py`
- Test: `frontend/src/features/conversations/__tests__/LiveVoiceStageRef04.test.tsx`

**Interfaces:**
- Consumes: 16kHz 16-bit mono PCM audio frames in `GlobalWakeWordListener.tsx` and `/v1/live-voice/wake-check`
- Produces: Acoustic RMS/voiced-frame gating on frontend and backend, `vad_filter=True` + `no_speech_prob` filtering in Whisper, strict `phonetic_skeleton_ptbr`

- [ ] **Step 1: Write failing backend & frontend tests for silence, ambient noise, Whisper `vad_filter=True`, and false-positive Portuguese words**

In `tests/unit/live_voice/test_wake_word_engine.py`:
- Test that `phonetic_skeleton_ptbr` / `match_wake_word` rejects `"além"`, `"alma"`, `"aluno"`, `"eram"`, `"arma"`, `"urna"`, `"elena"`, `"aline"`.
- Test that `check_wake_word_route` rejects silent or low-energy PCM audio without calling `_transcribe` (or returning `matched=False`), and does not inject `"Oi Orin."` as Whisper's `initial_prompt`.
- Test that `whisper_worker.transcribe` keeps `vad_filter=True` even when `prompt_hint` is passed and filters segments with `no_speech_prob > 0.6`.

- [ ] **Step 2: Run tests to verify they fail (RED)**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/live_voice/test_wake_word_engine.py -v`
Expected: FAIL

- [ ] **Step 3: Implement backend & frontend wake-word hardening**

1. In `src/agentos/live_voice/workers/whisper_worker.py` and `src/agentos/live_voice/engines.py`:
   - Set `vad_filter=True` always in `model.transcribe(...)`.
   - Filter out segments with `getattr(seg, "no_speech_prob", 0.0) > 0.6` or `getattr(seg, "avg_logprob", 0.0) < -1.0`.
2. In `src/agentos/live_voice/session.py` (`check_wake_word_route`):
   - Add `_has_sufficient_wake_speech_energy(pcm_bytes)` checking frame-by-frame RMS (32ms frames, requiring peak RMS `>= 0.025` and at least `6` voiced frames with RMS `>= 0.020`). If not met, return `match_wake_word("")` immediately.
   - Pass `prompt_hint=None` to `_transcribe` in `check_wake_word_route` so Whisper never hallucinates `"Oi Orin."` from `initial_prompt`.
3. In `src/agentos/live_voice/wake_word.py`:
   - Tighten `phonetic_skeleton_ptbr` so only initial `o`/`u` (after optional `h`) maps to `O`, require a front/high vowel (`i`, `e`, `y`) between `r/l` and `n/m`, and reject trailing open vowels (`a`, `o`, `u`) after `n/m`.
4. In `frontend/src/features/conversations/live/GlobalWakeWordListener.tsx`:
   - Remove `MIN_STANDBY_SENSITIVITY = 0.78` override; use `Math.min(prefs.sensitivity, 0.65)`.
   - Increase `startSpeechMs` from `64` to `160`.
   - Pass `headphoneMode: false` to `MicCapture.open` in standby so WebRTC `autoGainControl` does not amplify silent room noise.
   - In `inspectSpeechFrames`, compute voiced frame count (`rms >= 0.020`) and peak RMS (`>= 0.025`); require at least `6` voiced frames (`>= 192ms`) before calling `checkWakeWord`.

- [ ] **Step 4: Run backend and frontend wake-word tests to verify they pass (GREEN)**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/live_voice/test_wake_word_engine.py -v`
Expected: PASS

---

### Task 3: Painel `CodeWorkbenchPanel` Recolhido por Padrão com Botão Expansível no Orin Code (Caso 1)

**Files:**
- Modify: `frontend/src/features/conversations/ChatPage.tsx:607-630, 1986, 2054-2075, 2553-2605, 2890-2905`
- Modify: `frontend/src/features/conversations/CodeWorkbenchPanel.tsx:22-35, 235-275`
- Modify: `frontend/src/styles/agentos.css:5887-6210`
- Test: `frontend/src/features/conversations/__tests__/ChatPageCodePhase5.test.tsx`
- Test: `frontend/src/features/conversations/__tests__/CodeWorkbenchPanelRef08.test.tsx`

**Interfaces:**
- Consumes: `conversationMode === 'code'`, `workbenchOpen: boolean`
- Produces: `data-testid="code-workbench-toggle"` in `.chat__bar-actions`, `data-testid="code-workbench-close"` in `CodeWorkbenchPanel`

- [ ] **Step 1: Update and add failing tests in `ChatPageCodePhase5.test.tsx` and `CodeWorkbenchPanelRef08.test.tsx`**

Test that:
1. When opening a conversation with `mode: 'code'`, `code-workbench-panel` is NOT rendered by default and `<main>` has class `chat` (not `chat--code-split`).
2. Clicking `data-testid="code-workbench-toggle"` opens `code-workbench-panel` and adds `chat--code-split` to `<main>`.
3. Clicking `data-testid="code-workbench-close"` inside the panel (or clicking `code-workbench-toggle` again) collapses the panel.
4. Clicking `data-testid="code-view-changes-button"` in `code-changed-files-card` also expands the workbench on the `files` tab.

- [ ] **Step 2: Run Vitest to verify tests fail (RED)**

Run: `npm --prefix frontend test -- src/features/conversations/__tests__/ChatPageCodePhase5.test.tsx src/features/conversations/__tests__/CodeWorkbenchPanelRef08.test.tsx`
Expected: FAIL

- [ ] **Step 3: Implement `workbenchOpen` state, toggle button, close button, and empty preview surface styling**

1. In `ChatPage.tsx`:
   - Add `const [workbenchOpen, setWorkbenchOpen] = useState(false)` (reset to `false` on `conversationId` change).
   - Change `<main>` className to `conversationMode === 'code' && workbenchOpen ? 'chat chat--code-split' : 'chat'`.
   - Render `data-testid="code-workbench-toggle"` button in `.chat__bar-actions` when `conversationMode === 'code'`.
   - Call `setWorkbenchOpen(true)` in `code-view-changes-button` and `.code-changed-files-card__file-btn` click handlers.
   - Render `<CodeWorkbenchPanel ... onClose={() => setWorkbenchOpen(false)} />` only when `!loadFailure && conversationMode === 'code' && workbenchOpen`.
2. In `CodeWorkbenchPanel.tsx`:
   - Accept `onClose?: () => void` and render `data-testid="code-workbench-close"` button in `.code-workbench__tabs`.
3. In `agentos.css`:
   - Style `.chat__workbench-toggle-btn`, `.code-workbench__close-btn`, and fix `.code-workbench__stage` / `.code-workbench__preview-empty` so the empty state uses theme surface colors instead of a stark white rectangle.

- [ ] **Step 4: Run Vitest to verify tests pass (GREEN)**

Run: `npm --prefix frontend test -- src/features/conversations/__tests__/ChatPageCodePhase5.test.tsx src/features/conversations/__tests__/CodeWorkbenchPanelRef08.test.tsx`
Expected: PASS

---

### Task 4: Correção do Layout Cortado na Configuração da Anthropic e Demais Providers (Caso 3)

**Files:**
- Modify: `frontend/src/styles/agentos.css:125-135, 3182-3210`
- Modify: `frontend/src/styles/index.css:250-285, 360-425`
- Test: `frontend/tests/unit/ProvidersPhase7Ref14.test.tsx`

**Interfaces:**
- Consumes: `.slide-over-drawer`, `.settings-drawer`, `.ollama-flow`, `.provider-key-list`
- Produces: Zero horizontal overflow in `/integrations/providers/anthropic` and `/settings/providers/anthropic`

- [ ] **Step 1: Write test in `ProvidersPhase7Ref14.test.tsx` and verify layout classes/structure**

Add unit test verifying `AnthropicSetup` inside `SettingsDrawer` renders its mode selector, fields, and actions within constrained drawer layout classes.

- [ ] **Step 2: Update CSS in `agentos.css` and `index.css`**

1. In `frontend/src/styles/agentos.css`:
   - Update `.slide-over-drawer` width to `min(560px, 100vw)`, add `overflow-x: hidden;`, and add `html[data-desktop="true"] .slide-over-drawer { padding-top: max(24px, calc(14px + var(--orin-titlebar-height, 0px))); }`.
   - Update `.settings-drawer` and `.settings-drawer__body` with `grid-template-columns: minmax(0, 1fr); min-width: 0; max-width: 100%;`.
2. In `frontend/src/styles/index.css`:
   - Add `grid-template-columns: minmax(0, 1fr); min-width: 0; max-width: 100%;` to `.ollama-flow`, `.ollama-flow__connection`, and `.provider-key-list`.
   - Change `.ollama-flow__modes` from `width: max-content` to `display: flex; flex-wrap: wrap; width: 100%; max-width: 100%;` and allow `.ollama-flow .chip` to wrap (`flex: 1 1 auto; min-width: 0; white-space: normal; text-align: center;`).
   - Scope the stacked layout rules (`.ollama-flow__overview`, `.ollama-flow__fields`, `.ollama-flow__mode-field`, `.ollama-flow__footer`, `.ollama-flow__actions`, `.provider-key-list__fields`, `.provider-key-list__cooldown`) to `.settings-drawer` in addition to `@media (max-width: 620px)`.

- [ ] **Step 3: Run provider unit tests (GREEN)**

Run: `npm --prefix frontend test -- tests/unit/ProvidersPhase7Ref14.test.tsx`
Expected: PASS

---

### Task 5: Contraste e Paleta do `OrinMascot` no Modo Light (Caso 6)

**Files:**
- Modify: `frontend/src/components/OrinMascot.tsx:116-125, 168-193`
- Modify: `frontend/src/styles/theme.css:265-280, 345-360, 410-598`
- Test: `frontend/src/components/__tests__/OrinMascotAndBrand.test.tsx`
- Test: `frontend/src/app/__tests__/ThemeContext.test.tsx`

**Interfaces:**
- Consumes: `[data-theme="light"]`, `[data-accent="*"]`, `.orin-mascot`
- Produces: High-contrast pastel mascot body (`--orin-accent-soft` lighter than `--orin-accent` lighter than `--orin-accent-strong`) against `#282333` eyes/smile and soft ground shadow `--orin-mascot-shadow` in Light Mode

- [ ] **Step 1: Write failing tests in `OrinMascotAndBrand.test.tsx` and `ThemeContext.test.tsx`**

Test that:
1. `OrinMascot` uses `var(--orin-mascot-shadow, rgba(0, 0, 0, 0.34))` for its shadow ellipse and `var(--orin-mascot-feature, #282333)` for eyes and smile.
2. `theme.css` defines `[data-theme="light"] .orin-mascot` with a light highlight (`#E4DCFF`), pastel lavender body (`#B4A4EF`), and medium lavender edge (`#8870DF`) so the 3D gradient is never inverted and eyes/smile maintain high contrast across all 6 accents (`violet`, `sapphire`, `emerald`, `amber`, `rose`, `mono`).

- [ ] **Step 2: Run tests to verify they fail (RED)**

Run: `npm --prefix frontend test -- src/components/__tests__/OrinMascotAndBrand.test.tsx src/app/__tests__/ThemeContext.test.tsx`
Expected: FAIL

- [ ] **Step 3: Implement mascot tokens in `OrinMascot.tsx` and `theme.css`**

1. In `frontend/src/components/OrinMascot.tsx`:
   - Use `fill="var(--orin-mascot-shadow, rgba(0, 0, 0, 0.34))"` on `[data-mascot-part="shadow"]`.
   - Use `fill="var(--orin-mascot-feature, #282333)"` on `[data-mascot-part="eye"]` and `stroke="var(--orin-mascot-feature, #282333)"` on `[data-mascot-part="smile"]`.
2. In `frontend/src/styles/theme.css`:
   - Add scoped `[data-theme="light"] .orin-mascot` and `@media (prefers-color-scheme: light) :root[data-theme="system"] .orin-mascot` rules for default and all 6 `[data-accent="*"]` themes with luminous pastel mascot palettes (`highlight > body > shade`) and soft shadow `rgba(28, 22, 48, 0.12)`.

- [ ] **Step 4: Run tests to verify they pass (GREEN)**

Run: `npm --prefix frontend test -- src/components/__tests__/OrinMascotAndBrand.test.tsx src/app/__tests__/ThemeContext.test.tsx`
Expected: PASS

---

### Task 6: Símbolo do Orin no Topo com Bolinha Verde Condicional + Central de Novidades e Aprovações Inline (Caso 4)

**Files:**
- Create: `frontend/src/components/OrinActivityCenter.tsx`
- Create: `frontend/src/components/__tests__/OrinActivityCenter.test.tsx`
- Modify: `frontend/src/app/WorkspaceShell.tsx:172-180`
- Modify: `frontend/src/app/Home.tsx:343-349`
- Modify: `frontend/src/features/conversations/ChatPage.tsx:2248-2254`
- Modify: `frontend/src/features/projects/ProjectPage.tsx:278-283`
- Modify: `frontend/src/features/projects/ProjectsGalleryPage.tsx:158-163`
- Modify: `frontend/src/features/conversations/FriendSoulDrawer.tsx:374`
- Modify: `frontend/src/styles/agentos.css`
- Modify: `frontend/src/features/conversations/__tests__/ChatPageRef03AndTeam.test.tsx:173-180`

**Interfaces:**
- Consumes: `listConversations`, `listProjectSidebar`, `getConversation`, `submitToolPermission`, `updateConversationPermissionMode`, `trustConversationWorkspace`, `approvePlugin`, `approveMcpServer`, `sendConversationMessage`
- Produces: `<OrinActivityCenter client={client} activeConversationId={...} celebrating={...} />`

- [ ] **Step 1: Write failing tests in `OrinActivityCenter.test.tsx` and update `ChatPageRef03AndTeam.test.tsx`**

Test that:
1. When no session has pending approvals or unread updates, `orin-mascot-status-dot` is NOT rendered (`statusDot="none"`).
2. When a session has a pending `toolPermission` (e.g. `Bash(git clone ...)`), `planApproval`, or `workspaceTrust`, `orin-mascot-status-dot` IS rendered (`orin-mascot__status-dot--online`).
3. Clicking the topbar mascot button (`data-testid="orin-activity-center-trigger"`) opens the summary popover (`data-testid="orin-activity-center-popover"`) listing the pending approval and unread session updates.
4. Clicking `"Permitir"` (`data-testid="activity-center-approve-once-..."`) calls `submitToolPermission(..., 'allow_once')` and removes the resolved approval from the list.

- [ ] **Step 2: Run Vitest to verify tests fail (RED)**

Run: `npm --prefix frontend test -- src/components/__tests__/OrinActivityCenter.test.tsx`
Expected: FAIL

- [ ] **Step 3: Implement `OrinActivityCenter.tsx`, integrate into all 5 topbars, and style the popover**

1. Create `frontend/src/components/OrinActivityCenter.tsx`:
   - Polls/observes sessions via `listConversations` and `listProjectSidebar` + listens to `orin:conversations-updated`, `orin:projects-updated`, and `orin:activity-center-refresh`.
   - Fetches `getConversation` for sessions in `waiting_user` or `running`/`streaming` to detect pending `toolPermission`, `planApproval`, `workspaceTrust`, `pluginApproval`, `mcpApproval`, and `questions`.
   - Tracks `lastSeenUpdatedAt` per conversation in `localStorage` (`orin.activityCenter.lastSeen`) to detect unread background updates ("Novidades").
   - Renders `statusDot={hasAttention ? 'online' : 'none'}` on `OrinMascot`.
   - Opens popover on click with inline actions (`Permitir`, `Sempre permitir`, `Negar`, `Aprovar plano`, `Confiar na pasta`, `Aprovar plugin/MCP`, `Ir para sessão`, `Marcar como lido`).
2. Replace hardcoded `<OrinMascot size={30} variant="flat" statusDot="online" ... />` in `WorkspaceShell.tsx`, `Home.tsx`, `ChatPage.tsx`, `ProjectPage.tsx`, and `ProjectsGalleryPage.tsx` with `<OrinActivityCenter />`.

- [ ] **Step 4: Run Vitest to verify tests pass (GREEN)**

Run: `npm --prefix frontend test -- src/components/__tests__/OrinActivityCenter.test.tsx src/features/conversations/__tests__/ChatPageRef03AndTeam.test.tsx`
Expected: PASS

---

### Task 7: Novos Ícones Desktop e Web com Mascote do Orin e Fundo Arredondado Estilo Discord (Caso 5)

**Files:**
- Modify: `frontend/src/assets/orin-favicon.svg`
- Modify: `desktop/electron/assets/orin-logo.png`
- Modify: `desktop/electron/assets/orin-logo.ico`
- Modify: `frontend/src/assets/orin-logo.png`
- Create: `frontend/public/orin-logo.png`
- Modify: `desktop/electron/splash.html`
- Test: `tests/unit/installation/test_desktop_icons.py`
- Test: `frontend/tests/unit/Brand.test.tsx`
- Test: `frontend/tests/unit/brandIdentity.test.ts`

**Interfaces:**
- Produces: Discord-style rounded-square (`rx`) icon with the official Orin mascot in `orin-favicon.svg`, `1024x1024` RGBA `orin-logo.png`, and multi-size Windows `orin-logo.ico` (`16x16` through `256x256`).

- [ ] **Step 1: Write failing test in `tests/unit/installation/test_desktop_icons.py` verifying the new icon assets**

Test that:
1. `frontend/src/assets/orin-favicon.svg` contains the Orin mascot geometry and rounded rect background (and no longer the legacy spike path).
2. `desktop/electron/assets/orin-logo.png` and `frontend/src/assets/orin-logo.png` are valid `1024x1024` RGBA PNGs whose center pixel is the lilac mascot body color (`#B4A4EF` range) rather than the legacy dark ring hole.
3. `desktop/electron/assets/orin-logo.ico` is a valid multi-size ICO file containing `16x16` to `256x256` frames.

- [ ] **Step 2: Run test to verify it fails (RED)**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/installation/test_desktop_icons.py -v`
Expected: FAIL

- [ ] **Step 3: Generate the new SVG, 1024x1024 PNGs, and multi-size ICO, and update `splash.html`**

1. Update `frontend/src/assets/orin-favicon.svg` with the Discord-style rounded-square background (`#181426` -> `#110E1B` gradient with subtle `#B4A4EF` border glow) and the centered Orin mascot (`#B4A4EF` with soft 3D gradient and `#282333` eyes/smile).
2. Render the master SVG at `1024x1024` RGBA PNG and save to `desktop/electron/assets/orin-logo.png`, `frontend/src/assets/orin-logo.png`, and `frontend/public/orin-logo.png`.
3. Generate `desktop/electron/assets/orin-logo.ico` with sizes `[(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)]`.
4. Update `desktop/electron/splash.html` `.orb-mark` to embed the Orin mascot SVG.

- [ ] **Step 4: Run icon tests (GREEN)**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/installation/test_desktop_icons.py -v`
Expected: PASS

---

### Task 8: Verificação Completa de Testes e Inspeção Visual no Navegador Real (Playwright)

**Files:**
- Run full test suites (`pytest` + `vitest`)
- Inspect live browser rendering via Playwright MCP across Desktop & Mobile, Dark & Light Mode

- [ ] **Step 1: Run affected and full unit test suites**

Run:
- `.venv\Scripts\python.exe -m pytest tests/unit/provider_catalog/test_claude_code_bridge.py tests/unit/live_voice/test_wake_word_engine.py tests/unit/installation/test_desktop_icons.py -v`
- `npm --prefix frontend test`

- [ ] **Step 2: Start local backend + frontend and verify all visual states in real browser (Playwright)**

Verify in real Chromium browser (Desktop `1440x900` and Mobile `390x844`, Dark Mode and Light Mode):
1. Orin Code: workbench collapsed by default, expands on clicking `Bancada` button, collapses on clicking `✕`.
2. Anthropic setup drawer (`/integrations/providers/anthropic`): zero horizontal clipping, no horizontal scrollbar, all buttons and inputs visible.
3. Topbar Orin mascot: no green dot when idle; green dot appears when a session has pending approval/update; clicking opens the Activity Center popover and inline approval works.
4. Light Mode mascot (`Home` and topbar): soft pastel lavender body with high-contrast dark eyes and smile.
5. Favicon & generated desktop icon preview: Discord-style rounded-square with Orin mascot.
