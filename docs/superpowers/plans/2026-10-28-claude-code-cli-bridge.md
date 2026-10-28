# Claude Code CLI (`claude -p`) Subscription Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Orin Code to execute turns and agentic tool calls using the user's local Claude Code CLI subscription (`claude -p` authenticated via `claude.ai` OAuth Pro/Max/Team session limits) without requiring an `ANTHROPIC_API_KEY`, while preserving 100% of Orin's agentic harness (`AgenticTurnRuntime`, `AgentToolset`, `ToolPermissionBroker`, `FileHistory` `/rewind`, hooks, and streaming UI).

**Architecture:**
1. **Dual-Mode `anthropic` Provider (`src/agentos/provider_catalog/claude_code.py`):** Mirror `opencode.py` by introducing `DEFAULT_CLAUDE_CLI_BRIDGE_URL = "http://127.0.0.1:18447"` and `is_claude_cli_bridge_url(base_url)`. When `anthropic` is configured with this local bridge URL, API keys are optional and model catalog discovery + streaming inference run through the local `claude.exe` binary.
2. **Strict OAuth & Context Isolation:** Strip `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_SIMPLE`, and cloud provider variables from the subprocess environment so `claude -p` strictly uses the local `claude.ai` OAuth subscription. Invoke `claude.exe -p` with `--safe-mode --strict-mcp-config --disable-slash-commands --no-chrome --tools ""` to cut initial prompt cache overhead from 325k tokens down to ~596 tokens.
3. **Streaming NDJSON + Tool Call Bridge (`HTTPProviderStreamTransport`):** Pass the system prompt and Orin tool schemas via `--system-prompt-file` and the conversation transcript via `stdin` (`--input-format stream-json`), parse `stream_event` (`thinking_delta`, `text_delta`, `<orin_tool_call>`, native `tool_use`), `rate_limit_event` (5-hour / 7-day subscription windows), and `result` into `NormalizedStreamItem`s.
4. **Frontend Setup (`AnthropicSetup.tsx`):** Add a mode toggle (`Claude Code CLI (Assinatura Local)` vs `Cloud (Chave de API)`) in the Anthropic provider configuration screen with connection testing and keyless save support.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, `pytest`, React 19, TypeScript, Vitest, Testing Library, Playwright.

---

## File Structure

- **Create:** `src/agentos/provider_catalog/claude_code.py` — Binary resolution (`_resolve_claude_binary` prioritizing native `claude.exe` over `.cmd`), environment sanitization (`build_isolated_claude_env`), OAuth status verification (`check_claude_cli_status`), model catalog (`ClaudeCodeModelCatalogClient`), and streaming subprocess bridge (`stream_claude_code_bridge`).
- **Modify:** `src/agentos/provider_catalog/models.py` — Add `"anthropic"` to `PROVIDERS_WITH_BASE_URL` and `PROVIDERS_WITH_OPTIONAL_KEY`.
- **Modify:** `src/agentos/provider_catalog/http.py` — Update `AnthropicModelCatalogClient` (or re-export) to delegate to `ClaudeCodeModelCatalogClient` when `is_claude_cli_bridge_url(base_url)` is `True`.
- **Modify:** `src/agentos/provider_catalog/service.py` — Pass `base_url` when refreshing/normalizing `anthropic` models in CLI bridge mode.
- **Modify:** `src/agentos/persistence/postgres/provider_configuration.py` — Allow optional `api_key` for `anthropic` only when `is_claude_cli_bridge_url(base_url)` is `True`; support `provider == "anthropic"` in `test_connection`.
- **Modify:** `src/agentos/persistence/postgres/provider_models.py` — Allow refreshing `anthropic` catalog without stored API key when `is_claude_cli_bridge_url(base_url)` is `True`.
- **Modify:** `src/agentos/api/gateway.py` — Add `POST /v1/providers/anthropic/test` endpoint.
- **Modify:** `src/agentos/workers/chat.py` — Resolve `base_url` for `anthropic` in `_base_url_for` and allow keyless transport when `is_claude_cli_bridge_url(base_url)` is `True`.
- **Modify:** `src/agentos/agentic/provider_stream.py` — Route `HTTPProviderStreamTransport.stream` to `stream_claude_code_bridge` when `self._provider == "anthropic"` and `is_claude_cli_bridge_url(self._base_url)`.
- **Create:** `tests/unit/provider_catalog/test_claude_code_bridge.py` — Unit and Red-Team tests covering env sanitization, `--bare` exclusion, `claude auth status` validation, NDJSON streaming (`thinking_delta`, `text_delta`, `<orin_tool_call>`, `rate_limit_event`, `result`), error handling on 5-hour session limit exhaustion, and process tree cleanup on cancellation.
- **Create:** `frontend/src/features/providers/AnthropicSetup.tsx` — Mode selector (`Assinatura Claude Code (CLI Local)` vs `Cloud (Chave de API)`), connection test button, and keyless save in CLI mode.
- **Modify:** `frontend/src/api/providers.ts` — Add `testAnthropicConnection`.
- **Modify:** `frontend/src/features/providers/providerBrand.ts` — Enable `supportsBaseUrl: true` and `keyOptional: true` for `anthropic`.
- **Modify:** `frontend/src/features/providers/useProviderState.ts` — Add `anthropicMode` (`'local' | 'cloud'`), default CLI bridge URL, keyless save in local mode, and connection test handler.
- **Modify:** `frontend/src/features/providers/ProviderDetail.tsx` — Render `AnthropicSetup` when `provider === 'anthropic'`.
- **Modify:** `frontend/tests/unit/ProviderDetail.test.tsx` — Add unit tests for Anthropic CLI local mode (connection test + keyless save) alongside API key mode.

---

### Task 1: Backend Core Module `src/agentos/provider_catalog/claude_code.py` (TDD)

**Files:**
- Create: `tests/unit/provider_catalog/test_claude_code_bridge.py`
- Create: `src/agentos/provider_catalog/claude_code.py`

- [ ] **Step 1: Write failing unit & Red-Team tests in `tests/unit/provider_catalog/test_claude_code_bridge.py` (RED)**
  - Test `is_claude_cli_bridge_url` and `normalize_anthropic_base_url` (rejecting non-loopback or credentialed URLs for the local bridge).
  - Test `build_isolated_claude_env` removes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_SIMPLE`, `CLAUDECODE`, while keeping `PATH`, `USERPROFILE`, `APPDATA`, `SystemRoot`.
  - Test `_build_claude_cli_command` includes `--safe-mode`, `--strict-mcp-config`, `--disable-slash-commands`, `--tools`, `""`, `--input-format`, `stream-json`, `--output-format`, `stream-json`, `--verbose`, `--include-partial-messages`, and **never** includes `--bare`.
  - Test `check_claude_cli_status` succeeds when `loggedIn: true, authMethod: "claude.ai"` and raises `ProviderTransportError` when `loggedIn: false` or when `authMethod: "api_key"`.
  - Test `stream_claude_code_bridge` parses NDJSON lines (`system/init`, `stream_event` with `thinking_delta` and `text_delta` containing `<orin_tool_call>`, `rate_limit_event`, and `result`) into `NormalizedStreamItem`s (`THINKING`, `TEXT`, `TOOL_CALL`, `FINISH` with `StreamUsage`) without duplicating text from the consolidated `assistant` frame.
  - Test `stream_claude_code_bridge` surfaces a clear error message with reset timestamp when `rate_limit_event` / `result` indicates the 5-hour session limit was reached (`You've hit your session limit`).
  - Test `stream_claude_code_bridge` kills the subprocess tree when `cancel_check` returns `True`.

- [ ] **Step 2: Run pytest to confirm failure (RED)**
  - Run: `.venv\Scripts\pytest.exe tests/unit/provider_catalog/test_claude_code_bridge.py -v`

- [ ] **Step 3: Implement `src/agentos/provider_catalog/claude_code.py` (GREEN)**
  - Implement URL helpers, binary locator, environment sanitizer, auth status check, `ClaudeCodeModelCatalogClient`, and `stream_claude_code_bridge`.

- [ ] **Step 4: Run pytest to verify all tests in `test_claude_code_bridge.py` pass (GREEN)**
  - Run: `.venv\Scripts\pytest.exe tests/unit/provider_catalog/test_claude_code_bridge.py -v`

---

### Task 2: Wire `claude_code` into Provider Catalog, Persistence, Gateway, Worker, and Stream Transport (TDD)

**Files:**
- Modify: `src/agentos/provider_catalog/models.py`
- Modify: `src/agentos/provider_catalog/http.py`
- Modify: `src/agentos/provider_catalog/service.py`
- Modify: `src/agentos/persistence/postgres/provider_configuration.py`
- Modify: `src/agentos/persistence/postgres/provider_models.py`
- Modify: `src/agentos/api/gateway.py`
- Modify: `src/agentos/workers/chat.py`
- Modify: `src/agentos/agentic/provider_stream.py`
- Modify: `tests/unit/provider_catalog/test_claude_code_bridge.py`

- [ ] **Step 1: Add integration tests for `HTTPProviderStreamTransport` and `PostgresProviderConfigurationAdapter` with `anthropic` in CLI mode vs Cloud mode (RED)**
  - Verify `HTTPProviderStreamTransport(provider="anthropic", base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL).stream(request)` delegates to `stream_claude_code_bridge`.
  - Verify `PostgresProviderConfigurationAdapter` requires an `api_key` when `provider == "anthropic"` and `base_url is None`, but allows saving without `api_key` when `base_url == DEFAULT_CLAUDE_CLI_BRIDGE_URL`.
  - Verify `POST /v1/providers/anthropic/test` works in CLI mode.

- [ ] **Step 2: Implement wiring across catalog, persistence, gateway, worker, and `provider_stream.py` (GREEN)**

- [ ] **Step 3: Run backend test suite to verify zero regressions (GREEN)**
  - Run: `.venv\Scripts\pytest.exe tests/unit/provider_catalog/test_claude_code_bridge.py tests/unit/provider_catalog/test_opencode_bridge.py -v`

---

### Task 3: Frontend `AnthropicSetup.tsx` & Provider Settings Wiring (TDD + Playwright)

**Files:**
- Create: `frontend/src/features/providers/AnthropicSetup.tsx`
- Modify: `frontend/src/api/providers.ts`
- Modify: `frontend/src/features/providers/providerBrand.ts`
- Modify: `frontend/src/features/providers/useProviderState.ts`
- Modify: `frontend/src/features/providers/ProviderDetail.tsx`
- Modify: `frontend/tests/unit/ProviderDetail.test.tsx`

- [ ] **Step 1: Write failing frontend unit tests in `frontend/tests/unit/ProviderDetail.test.tsx` (RED)**
  - Test switching Anthropic between `Assinatura Claude Code (CLI Local)` and `Cloud (Chave de API)`, testing connection, and saving keyless in CLI mode while preserving the existing API key save test.

- [ ] **Step 2: Implement `AnthropicSetup.tsx`, `useProviderState.ts`, `ProviderDetail.tsx`, `providerBrand.ts`, and `providers.ts` (GREEN)**

- [ ] **Step 3: Run Vitest unit tests and TypeScript build check (GREEN)**
  - Run: `npm --prefix frontend test -- --run tests/unit/ProviderDetail.test.tsx`
  - Run: `npm --prefix frontend run build`

- [ ] **Step 4: Visual and End-to-End Verification in Browser with Playwright**
  - Start local backend + frontend, open Provider Settings -> Anthropic, verify `Assinatura Claude Code (CLI Local)` mode, click **"Testar conexão"** against the live `claude.exe` v2.1.309 on this machine, refresh catalog, and execute a real end-to-end chat/code turn in Orin using `claude -p`.
