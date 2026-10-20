# OpenCode Local Bridge & Question Answer Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the duplicate/pinned user answer bug in `ChatPage.tsx` / `UserQuestionCard.tsx`, implement the local `opencode serve` Bridge sharing Orin's full agentic harness (`AgentRuntime`, tools, permissions, checkpoints, timeline), and keep `.orin/` ignored in Git.

**Architecture:**
1. **Frontend Question Dedup:** Normalize user question responses with `.trim()` before setting `pendingUserMessage`, compare trimmed strings in `alreadyDurable`, clear `pendingUserMessage` via effect once the durable snapshot includes the message, and guard `UserQuestionCard` against double-submission with `localSubmitting`.
2. **Backend OpenCode Local Bridge (`src/agentos/provider_catalog/opencode.py` & `src/agentos/agentic/provider_stream.py`):** Support both Local Bridge mode (`http://127.0.0.1:18446`, keyless, driving `opencode serve` via `GET /config/providers`, `POST /session`, `POST /session/{id}/message` with `"read": true` to satisfy the FreeTier gate) and Cloud Zen mode (`https://opencode.ai/zen/v1`). Translate Orin's tool schemas and `<orin_tool_call>` blocks into `NormalizedStreamItem(StreamKind.TOOL_CALL / TEXT / USAGE / FINISH)` so Orin's `AgentRuntime` executes all tools natively.
3. **Frontend OpenCode Setup (`OpenCodeSetup.tsx`):** Provide a Local Bridge (keyless) vs. Cloud Zen mode selector, server URL input, "Testar conexão" button, and "Atualizar catálogo" button in `ProviderDetail.tsx`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, `httpx`, `pytest`, React 19, TypeScript, Vitest, Testing Library, Playwright.

---

## File Structure

- **Modify:** `.gitignore` — Add `.orin/` to prevent runtime tool outputs/worktree metadata from being committed.
- **Modify:** `frontend/src/features/conversations/UserQuestionCard.tsx` — Add `localSubmitting` state to prevent rapid double-submits and trim notes on submit.
- **Modify:** `frontend/src/features/conversations/ChatPage.tsx` — Trim `answer.note`, `response`, and `feedback` in `submitQuestionAnswers` and `handleRequestPlanChanges`; compare `.trim()` in `alreadyDurable`; clear `pendingUserMessage` when durable; guard `submitQuestionAnswers` with a ref while in flight.
- **Modify:** `frontend/src/features/conversations/__tests__/UserQuestionCard.test.tsx` — Add unit tests for double-click prevention and note trimming.
- **Modify:** `frontend/tests/unit/ChatPage.test.tsx` — Add unit test reproducing the trailing-newline note in `ask_user` answer and asserting no duplicate user bubble remains pinned at the bottom after snapshot reload.
- **Create:** `src/agentos/provider_catalog/opencode.py` — URL normalization (`DEFAULT_OPENCODE_LOCAL_BASE_URL`, `OPENCODE_CLOUD_BASE_URL`, `normalize_opencode_base_url`, `is_opencode_cloud`, `ensure_loopback_opencode_url`), `OpenCodeBridgeProcessManager` (spawns/checks `opencode serve --port <port> --hostname 127.0.0.1`), `OpenCodeModelCatalogClient` (supports both `/config/providers` and Cloud `/models`), and `stream_opencode_bridge` (drives `/session` + `/session/{id}/message`, injects Orin tool protocol, parses `<orin_tool_call>` and native tool fallbacks into `NormalizedStreamItem`s).
- **Modify:** `src/agentos/provider_catalog/models.py` — Add `"opencode"` to `PROVIDERS_WITH_BASE_URL` and `PROVIDERS_WITH_OPTIONAL_KEY`.
- **Modify:** `src/agentos/provider_catalog/http.py` — Re-export `OpenCodeModelCatalogClient` and `DEFAULT_OPENCODE_BASE_URL` from `agentos.provider_catalog.opencode`.
- **Modify:** `src/agentos/persistence/postgres/provider_configuration.py` — Wire `opencode` into `_base_url`, `_api_key`, and `test_connection`.
- **Modify:** `src/agentos/api/gateway.py` — Add `POST /v1/providers/opencode/test` endpoint.
- **Modify:** `src/agentos/workers/chat.py` — Update `_base_url_for` to resolve `opencode` base URLs.
- **Modify:** `src/agentos/agentic/provider_stream.py` — Delegate `HTTPProviderStreamTransport.stream` to `stream_opencode_bridge` when `self.provider == "opencode"` and `not is_opencode_cloud(self.base_url)`.
- **Create:** `tests/unit/provider_catalog/test_opencode_bridge.py` — Unit and adversarial tests for URL validation (SSRF rejection), catalog parsing (`/config/providers` vs `/models`), bridge message serialization, `<orin_tool_call>` extraction, unauthorized tool rejection, and 403/error handling.
- **Create:** `frontend/src/features/providers/OpenCodeSetup.tsx` — Setup component for OpenCode (Bridge Local vs Cloud Zen mode, URL input, optional/required API key, Testar conexão, Salvar).
- **Modify:** `frontend/src/api/providers.ts` — Add `testOpenCodeConnection`.
- **Modify:** `frontend/src/features/providers/useProviderState.ts` — Add `opencodeMode` (`'local' | 'cloud'`), default local URL, keyless save in local mode, and connection test wiring.
- **Modify:** `frontend/src/features/providers/ProviderDetail.tsx` — Render `OpenCodeSetup` for `provider === 'opencode'` and enable the "Atualizar catálogo" button in the catalog section.
- **Modify:** `frontend/tests/unit/ProviderDetail.test.tsx` — Add tests for OpenCode Local Bridge mode (keyless save + connection test) while preserving existing primary key save test.

---

### Task 1: Repository `.gitignore` Hygiene

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add `.orin/` to `.gitignore`**

Edit `.gitignore` to ignore `.orin/` under the `# Local runtime output` section:

```gitignore
# Local runtime output: process logs, agent workspaces, visual captures.
.logs/
.orin/
data/workspaces/
```

- [ ] **Step 2: Verify `git status --short` no longer reports `.orin/` as untracked**

Run: `git status --short`
Expected: `.orin/` is no longer listed in untracked files.

---

### Task 2: Fix Duplicate & Pinned User Question Answer in Frontend (TDD)

**Files:**
- Modify: `frontend/src/features/conversations/__tests__/UserQuestionCard.test.tsx`
- Modify: `frontend/tests/unit/ChatPage.test.tsx`
- Modify: `frontend/src/features/conversations/UserQuestionCard.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`

- [ ] **Step 1: Write failing unit tests in `UserQuestionCard.test.tsx` and `ChatPage.test.tsx`**

In `frontend/src/features/conversations/__tests__/UserQuestionCard.test.tsx`, add a test verifying that rapid double-submit on the form only invokes `onSubmit` once while the promise is pending, and trims trailing whitespace on `note`:

```tsx
  it('prevents duplicate onSubmit calls while submission is in flight and trims notes', async () => {
    let resolveSubmit!: () => void
    const onSubmit = vi.fn().mockImplementation(
      () => new Promise<void>((resolve) => { resolveSubmit = resolve }),
    )
    render(<UserQuestionCard questions={[ singleQuestion ]} active={true} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('user-question-note-toggle'))
    fireEvent.change(screen.getByLabelText('Observação (opcional)'), {
      target: { value: '  usar bridge local \n\n' },
    })

    const submitButton = screen.getByRole('button', { name: 'Enviar respostas' })
    fireEvent.click(submitButton)
    fireEvent.click(submitButton)

    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith([
      { id: 'q1', selected: [], note: 'usar bridge local' },
    ])

    resolveSubmit()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enviar respostas' })).not.toBeDisabled())
  })
```

In `frontend/tests/unit/ChatPage.test.tsx`, add a test where an active `ask_user` question is answered with a note containing trailing newlines (`"observação com enter\n"`), the backend snapshot returns the stripped user message + a new running assistant message, and verify that the user answer appears **only once** in the DOM (and is not pinned after the new assistant message).

- [ ] **Step 2: Run Vitest to verify the new tests fail (RED)**

Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/UserQuestionCard.test.tsx tests/unit/ChatPage.test.tsx`
Expected: FAIL (double submit calls `onSubmit` twice, and `ChatPage` renders duplicate user message at the end of the chat).

- [ ] **Step 3: Implement the fix in `UserQuestionCard.tsx` and `ChatPage.tsx` (GREEN)**

In `frontend/src/features/conversations/UserQuestionCard.tsx`:
- Add `const [localSubmitting, setLocalSubmitting] = useState(false)` and `const isSubmitting = submitting || localSubmitting`.
- In `advance`:
  ```tsx
  async function advance(event: FormEvent) {
    event.preventDefault()
    if (!active || isSubmitting) return
    setError(null)

    if (!isLast) {
      setCurrentIndex((index) => index + 1)
      return
    }

    setLocalSubmitting(true)
    try {
      await onSubmit(
        questions.map((question) => {
          const ans = answers[question.id] ?? emptyAnswer(question.id)
          return { ...ans, note: ans.note.trim() }
        }),
      )
    } catch {
      setError('Não foi possível enviar as respostas. Tente novamente.')
    } finally {
      setLocalSubmitting(false)
    }
  }
  ```
- Replace `disabled={submitting}` with `disabled={isSubmitting}` and `{isSubmitting ? 'Enviando…' : ...}`.

In `frontend/src/features/conversations/ChatPage.tsx`:
- Add a ref `const questionSubmitInFlightRef = useRef(false)`.
- In `messages` `useMemo` (lines 660–662), compare trimmed strings:
  ```tsx
  const alreadyDurable = pendingUserMessage !== null
    && base.some((item) => item.role === 'user' && item.content.trim() === pendingUserMessage.content.trim())
  return pendingUserMessage && !alreadyDurable ? [...base, pendingUserMessage] : base
  ```
- Add an effect that clears `pendingUserMessage` as soon as the durable snapshot contains the message:
  ```tsx
  useEffect(() => {
    if (!pendingUserMessage) return
    const target = pendingUserMessage.content.trim()
    const persisted = (conversation?.messages ?? []).some(
      (item) => item.role === 'user' && item.content.trim() === target,
    )
    if (persisted) {
      setPendingUserMessage(null)
    }
  }, [conversation?.messages, pendingUserMessage])
  ```
- In `submitQuestionAnswers`:
  - Guard with `if (questionSubmitInFlightRef.current) return`.
  - Trim `answer.note` (`const note = answer.note.trim(); if (note) text.push(\`Observação: \${note}\`)`).
  - Trim `response` (`const response = text.join('\n').trim()`).
  - Reset `questionSubmitInFlightRef.current = false` in `finally`.
- In `handleRequestPlanChanges`:
  - Normalize `const normalizedFeedback = feedback.trim()` before setting `pendingUserMessage` and calling `sendChatMessage(normalizedFeedback)`.

- [ ] **Step 4: Re-run Vitest to verify all tests pass (GREEN)**

Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/UserQuestionCard.test.tsx tests/unit/ChatPage.test.tsx`
Expected: PASS.

---

### Task 3: Backend OpenCode Local Bridge & Catalog Integration (TDD)

**Files:**
- Create: `src/agentos/provider_catalog/opencode.py`
- Modify: `src/agentos/provider_catalog/models.py`
- Modify: `src/agentos/provider_catalog/http.py`
- Modify: `src/agentos/persistence/postgres/provider_configuration.py`
- Modify: `src/agentos/api/gateway.py`
- Modify: `src/agentos/workers/chat.py`
- Modify: `src/agentos/agentic/provider_stream.py`
- Create: `tests/unit/provider_catalog/test_opencode_bridge.py`

- [ ] **Step 1: Write failing unit & adversarial tests in `tests/unit/provider_catalog/test_opencode_bridge.py` (RED)**

Write tests covering:
1. `normalize_opencode_base_url` and `is_opencode_cloud`:
   - Accepts `http://127.0.0.1:18446`, `http://localhost:18446`, and `https://opencode.ai/zen/v1`.
   - Rejects non-loopback non-cloud URLs (e.g., `http://169.254.169.254`, `http://192.168.1.10:8080`, `https://evil.example.com`) with `ValueError` to prevent SSRF.
   - Rejects URLs containing credentials, query strings, or fragments.
2. `OpenCodeModelCatalogClient`:
   - In Cloud mode (`https://opencode.ai/zen/v1` or empty `base_url` with `api_key`), fetches `GET /zen/v1/models` with `Authorization: Bearer <key>`.
   - In Local Bridge mode (`http://127.0.0.1:18446`), fetches `GET /config/providers`, extracts models from provider `opencode`, and maps `id`, `name`, `limit.context`, `capabilities`, and `modalities` into the normalized dict format expected by `_normalize_opencode`.
3. `stream_opencode_bridge`:
   - Sends `POST /session` and `POST /session/{id}/message` with `"read": True` and all other built-in OpenCode tools set to `False` (critical FreeTier gate requirement).
   - Injects Orin's tool declarations into `system` and translates `<orin_tool_call>{"name": "read_file", "arguments": {"path": "README.md"}}</orin_tool_call>` into `NormalizedStreamItem(StreamKind.TOOL_CALL, ...)` with `FinishReason.TOOL_CALLS`.
   - Adversarial: ignores/strips `<orin_tool_call>` with malformed JSON or tool names not in the turn's declared `tools` whitelist (preventing prompt-injected tool invocation).
   - Adversarial: when `tool_choice == "none"`, does not emit `TOOL_CALL` items.
   - Emits `TEXT`, `USAGE`, and `FINISH` items for normal assistant replies.
   - Raises `ProviderKeyRejected` on HTTP 401/403/429 or transport error.
4. `PostgresProviderConfigurationAdapter` & `ChatWorker`:
   - Allows configuring `opencode` in Local Bridge mode (`base_url="http://127.0.0.1:18446"`, `api_key=""`) without requiring an API key.
   - Still requires an API key when configuring `opencode` in Cloud mode (`base_url="https://opencode.ai/zen/v1"`).

- [ ] **Step 2: Run pytest on `tests/unit/provider_catalog/test_opencode_bridge.py` to confirm failure (RED)**

Run: `.venv\Scripts\python -m pytest tests/unit/provider_catalog/test_opencode_bridge.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'agentos.provider_catalog.opencode'`).

- [ ] **Step 3: Implement `src/agentos/provider_catalog/opencode.py` and wire backend modules (GREEN)**

1. Create `src/agentos/provider_catalog/opencode.py` with:
   - `DEFAULT_OPENCODE_LOCAL_BASE_URL = "http://127.0.0.1:18446"`
   - `OPENCODE_CLOUD_BASE_URL = "https://opencode.ai/zen/v1"`
   - `DEFAULT_OPENCODE_BASE_URL = OPENCODE_CLOUD_BASE_URL`
   - `normalize_opencode_base_url(value: str) -> str`: validates scheme, rejects credentials/query/fragment, allows only loopback hosts (`127.0.0.1`, `localhost`, `::1`) or `opencode.ai` (`*.opencode.ai`).
   - `is_opencode_cloud(base_url: str) -> bool`: checks if hostname is `opencode.ai` or `*.opencode.ai`.
   - `OpenCodeBridgeProcessManager`: locates `opencode` binary (`shutil.which("opencode")` or `%APPDATA%/npm/node_modules/opencode-ai/bin/opencode.exe` / `%APPDATA%/npm/opencode.cmd`), probes `GET {base_url}/config/providers`, and if unreachable on `127.0.0.1`/`localhost`, spawns `opencode serve --port <port> --hostname 127.0.0.1` without `shell=True` (`CREATE_NO_WINDOW` on Windows) and waits up to 10s for readiness.
   - `OpenCodeModelCatalogClient`:
     - When `is_opencode_cloud(target)` (or `not base_url and api_key.strip()`): calls `GET {target}/models` with `Authorization: Bearer {api_key}`.
     - When Local Bridge: ensures bridge is running, calls `GET {target}/config/providers`, finds the `opencode` provider entry, and converts its `models` dict into a list of model dicts (`id`, `name`, `context_length`, `supported_parameters`, `input_modalities`, `output_modalities`, `pricing`).
   - `stream_opencode_bridge`:
     - Ensures local bridge is running if target is loopback.
     - Creates session via `POST {base_url}/session`.
     - Builds system prompt combining Orin's `system` messages + Orin tool call instructions (when `tools` are provided and `tool_choice != "none"`).
     - Formats conversation history (`user`, `assistant`, `tool` messages) into clear text transcript parts.
     - Calls `POST {base_url}/session/{session_id}/message` with:
       ```python
       "tools": {
           "read": True,
           "bash": False, "edit": False, "glob": False, "grep": False,
           "skill": False, "task": False, "todowrite": False, "webfetch": False,
           "websearch": False, "write": False, "question": False, "apply_patch": False,
       }
       ```
     - Parses response `parts` and `info`: extracts `<orin_tool_call>...</orin_tool_call>` blocks, validates tool name against allowed tool names in `tools`, emits `THINKING` (if reasoning parts present), `TEXT`, `TOOL_CALL`, `USAGE`, and `FINISH` items.
2. Update `src/agentos/provider_catalog/models.py`:
   - `PROVIDERS_WITH_BASE_URL = frozenset({"omniroute", "ollama", "opencode"})`
   - `PROVIDERS_WITH_OPTIONAL_KEY = frozenset({"omniroute", "ollama", "opencode"})`
3. Update `src/agentos/provider_catalog/http.py` to import and re-export `DEFAULT_OPENCODE_BASE_URL` and `OpenCodeModelCatalogClient` from `.opencode`.
4. Update `src/agentos/persistence/postgres/provider_configuration.py`:
   - In `_base_url`: for `provider == "opencode"`, if `value` is given, normalize with `normalize_opencode_base_url(str(value))`; if `value` is None/empty, return `None` when called from legacy/keyed configure or normalize default. Specifically, in `configure()`, pass `command.get("api_key")` to `_base_url(provider, command.get("base_url"), command.get("api_key"))` so that if `base_url` is omitted and `api_key` is provided, it defaults to `OPENCODE_CLOUD_BASE_URL`, whereas if both `base_url` and `api_key` are omitted, it defaults to `DEFAULT_OPENCODE_LOCAL_BASE_URL`.
   - In `_api_key`: if `provider == "opencode" and base_url is not None and is_opencode_cloud(base_url): optional = False`.
   - In `test_connection`: support `provider == "opencode"` using `OpenCodeModelCatalogClient()`.
5. Update `src/agentos/api/gateway.py`:
   - Add `@app.post("/v1/providers/opencode/test")` calling `services.provider_configuration.test_connection` in threadpool.
6. Update `src/agentos/workers/chat.py`:
   - In `_base_url_for`: if `provider == "opencode"`, return `normalize_opencode_base_url(configured or OPENCODE_CLOUD_BASE_URL)`.
7. Update `src/agentos/agentic/provider_stream.py`:
   - In `HTTPProviderStreamTransport.stream`: when `self.provider == "opencode" and not is_opencode_cloud(self.base_url)`, yield from `stream_opencode_bridge(...)`.

- [ ] **Step 4: Run backend unit tests to verify GREEN and no regressions**

Run: `.venv\Scripts\python -m pytest tests/unit/provider_catalog/ tests/unit/workers/test_chat_provider_transport.py tests/unit/api/test_provider_keys_gateway.py tests/unit/persistence/test_provider_api_keys.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Verify live integration against local `opencode serve` with free models**

Run a local verification test executing `OpenCodeModelCatalogClient().fetch("", base_url="http://127.0.0.1:18446")` and `HTTPProviderStreamTransport(provider="opencode", base_url="http://127.0.0.1:18446", api_key="", model="big-pickle").stream(...)` (including a turn with Orin tools declared) to confirm real end-to-end execution with `opencode serve`.

---

### Task 4: Frontend OpenCode Setup UI (TDD + Playwright Visual Verification)

**Files:**
- Create: `frontend/src/features/providers/OpenCodeSetup.tsx`
- Modify: `frontend/src/api/providers.ts`
- Modify: `frontend/src/features/providers/useProviderState.ts`
- Modify: `frontend/src/features/providers/ProviderDetail.tsx`
- Modify: `frontend/tests/unit/ProviderDetail.test.tsx`

- [ ] **Step 1: Write failing unit tests in `frontend/tests/unit/ProviderDetail.test.tsx` (RED)**

Add tests in `frontend/tests/unit/ProviderDetail.test.tsx` verifying:
1. OpenCode renders mode selector (`Bridge Local (Grátis)` vs `Cloud (Zen)`), allows testing connection (`Testar conexão`) and saving in Local Bridge mode without an API key.
2. Existing test `'supports OpenCode and refreshes the fallback key list after saving the primary key'` continues to pass.

- [ ] **Step 2: Run Vitest to confirm new test fails (RED)**

Run: `npm --prefix frontend test -- --run tests/unit/ProviderDetail.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement `OpenCodeSetup.tsx` and wire into `providers.ts`, `useProviderState.ts`, and `ProviderDetail.tsx` (GREEN)**

1. In `frontend/src/api/providers.ts`:
   - Add `testOpenCodeConnection(client, input: { apiKey: string; baseUrl: string }, intent)` calling `POST /v1/providers/opencode/test`.
2. In `frontend/src/features/providers/useProviderState.ts`:
   - Add `opencodeMode: 'local' | 'cloud'` and `setOpencodeMode`.
   - Default `baseUrl` for `opencode` to `'http://127.0.0.1:18446'`.
   - On `inspectProvider` load for `opencode`, if `state.extra.base_url` is a string, set `baseUrl` and set `opencodeMode` based on whether hostname ends with `opencode.ai`.
   - Update `requiresApiKey`: `provider !== 'omniroute' && !(provider === 'ollama' && ollamaMode === 'local') && !(provider === 'opencode' && opencodeMode === 'local')`.
   - In `configure`: include `baseUrl` when `provider === 'omniroute' || provider === 'ollama' || provider === 'opencode'` (and if `provider === 'opencode'` and `apiKey` is provided while `baseUrl` is still the default local URL and user didn't explicitly switch, wait: if `opencodeMode === 'local'`, send `baseUrl`).
   - In `testConnection`: call `testOpenCodeConnection` when `provider === 'opencode'`.
3. Create `frontend/src/features/providers/OpenCodeSetup.tsx`:
   - Mode radio group: `Bridge Local (Grátis)` and `Cloud (Zen)`.
   - Input `URL do servidor` (`id="opencode-base-url"`).
   - Input `Chave de API` (`id="opencode-api-key"`, label `Chave de API`, optional in Local Bridge mode, required in Cloud mode).
   - Buttons: `Testar conexão`, `Salvar`, `Revogar acesso`, `Atualizar catálogo`.
4. Update `frontend/src/features/providers/ProviderDetail.tsx`:
   - Render `<OpenCodeSetup ... />` when `provider === 'opencode'`.
   - Include `provider === 'opencode'` in the condition for the `Atualizar catálogo` button in the catalog section.

- [ ] **Step 4: Run frontend unit test suite (GREEN)**

Run: `npm --prefix frontend test -- --run tests/unit/ProviderDetail.test.tsx`
Expected: PASS.

- [ ] **Step 5: Visual verification in browser with Playwright (Mandatory per `RULE[user_global]` #4)**

Start the local app/dev server, open the UI with Playwright MCP (`browser_navigate`, `browser_snapshot`, `browser_take_screenshot`), verify:
1. Settings → Providers → OpenCode Drawer renders the Local Bridge / Cloud Zen selector, tests connection against local `opencode serve`, and loads free models (`big-pickle`, `mimo-v2.6-flash-free`).
2. Chat conversation view renders without duplicate user question answers at the bottom.
