# Correções de Trabalhos Longos, Assinatura CLI do Claude Code, Orin Code, Subagentes e SSE — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir integralmente todos os problemas críticos, altos, médios e baixos (P0.1–P0.6, P1.1–P1.7, P2.1–P2.5, P3.1–P3.2) que afetam trabalhos longos, a `Assinatura CLI` do Claude Code, o Orin Code, subagentes e o streaming SSE em tempo real no Orin.

**Architecture:** As correções são divididas em 6 tarefas independentes e testáveis via TDD: (1) Saneamento pós-redaction em `_bounded()`, coerção defensiva em `_event_from_row()`, proteção do endpoint SSE, cursores assinados e retenção de `TOOL_FINISHED` em `chat.py`; (2) Timeouts renováveis por delta (stall timeout), prevenção de deadlock no `stdin`, parser balanceado de `<orin_tool_call>`, mapeamento bidirecional de nomes/argumentos de tools e propagação de `FinishReason.LENGTH` na ponte `claude_code.py`/`opencode.py`; (3) Suporte a imagens (Vision), envelhecimento de tool results (`_age_tool_results`), janela de autocompactação realista (`200_000`), `cancel_check`, herança de subagentes na `Assinatura CLI` e isolamento de `cwd`/semáforo; (4) Desacoplamento de `_embed_pending()` no `project_map`, circuit-breaker de embeddings, baseline pré-comando em repositórios `.git` (`workspace.py`) e exclusão de `uploads/` em `agent_tools.py`; (5) Destravamento de pipes/árvore de processos no Windows (`bash.py` e `_shared.py`), conversão de `timeout` em ms, ciclo de vida de subagentes com `try/finally`, `recover_stale` com `lease_renewed_at`, `MICROCOMPACT_KEEP_RECENT = 16` e limite em `FileCheckpointStore`; (6) Reconciliação de `loadSnapshot(false)` no Frontend (`ChatPage.tsx`), exibição garantida da resposta final (`item.content`), liquidação de tools em `turnTimelineFold.ts`, suporte completo a `powershell` em `CodeToolCallBlock.tsx`/`TurnTimeline.tsx`, filtragem real de arquivos alterados na `Bancada` e correção de `Equipe em atividade`/`TeamActivityView.tsx`.

**Tech Stack:** Python 3.12+ (FastAPI, SQLAlchemy, Pydantic, Pytest), TypeScript / React 18 (Vite, Vitest, Playwright).

**Spec:** `docs/superpowers/specs/2026-10-31-correcoes-trabalhos-longos-claude-cli-orin-code-sse-design.md`

## Global Constraints

- Preservar todos os contratos existentes de validação em `AgentActivityEvent` (rejeitar strings > `text_limit` passadas diretamente antes de `redact_text`, mas truncar expansões geradas por `redact_text` para `text_limit` e sanitizar linhas já persistidas em `_event_from_row`).
- Na `Assinatura CLI` (`is_claude_cli_bridge_url(base_url)`), manter compatibilidade retroativa com conversas existentes que tenham `model_id = "claude-opus-5-5"` salvo no banco (normalizando para `claude-opus-4-7` no transporte).
- Garantir execução limpa em Windows 11 (`powershell.exe`, `taskkill /PID <pid> /T /F`, caminhos com `\`).
- Seguir estritamente TDD (RED -> GREEN -> REFACTOR) + testes adversariais em cada tarefa e verificação visual real no navegador (Playwright) ao final.

---

### Task 1: Saneamento Pós-Redaction (`models.py`), Recuperação de Linhas Persistidas (`agentic_activity.py`), Cursores e Eventos em `chat.py` / `gateway.py` (Bloco 1 Backend)

**Files:**
- Modify: `src/agentos/agentic/models.py:110-245`
- Modify: `src/agentos/persistence/postgres/agentic_activity.py:68-166`
- Modify: `src/agentos/conversations/chat.py:495-522, 1350-1365, 1450-1628, 1847-1863, 2694-2722`
- Modify: `src/agentos/api/gateway.py:1777-1790`
- Test: `tests/unit/agentic/test_activity_redaction_bounds.py`

**Interfaces:**
- Consumes: `redact_text(value: str) -> str` from `agentos.redaction`
- Produces:
  - `_bounded(...)` in `src/agentos/agentic/models.py` that clamps post-redaction string expansion to `text_limit`
  - `_coerce_stored_payload(payload: object) -> dict[str, object]` in `src/agentos/persistence/postgres/agentic_activity.py` used by `_event_from_row`
  - `_truncate_activity_payload(payload: Mapping[str, object]) -> dict[str, object]` in `src/agentos/conversations/chat.py` used by `PostgresChatStore._activity`
  - Signed item cursors in `_compact_snapshot_activities` and `lease_renewed_at`-aware `recover_stale` in `src/agentos/conversations/chat.py`

- [ ] **Step 1: Write the failing tests in `tests/unit/agentic/test_activity_redaction_bounds.py`**

```python
from datetime import UTC, datetime, timedelta
from sqlalchemy import create_engine, insert, update

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.persistence.metadata import conversation_activity_events, conversation_dispatches, metadata
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.conversations.chat import PostgresChatStore


def test_redaction_expansion_does_not_exceed_output_tail_limit() -> None:
    # 7 occurrences of '"requiresApiKey": false' expand by +5 chars each (+35 chars total) when redacted
    repeated = '"requiresApiKey": false, ' * 7
    padding = "x" * (2000 - len(repeated))
    raw_tail = repeated + padding
    assert len(raw_tail) == 2000

    event = AgentActivityEvent(
        event_id="evt_1",
        conversation_id="conv_1",
        turn_id="turn_1",
        execution_id="exec_1",
        user_id="user_1",
        workspace_id=None,
        agent_id="agent:conv_1:main",
        parent_agent_id=None,
        event_type=AgentActivityEventType.TOOL_PROGRESSED,
        sequence=1,
        summary="Executando: Find Ashy providers.json",
        payload={"tool_name": "powershell", "output_tail": raw_tail},
        created_at=datetime.now(UTC),
    )
    assert len(str(event.payload["output_tail"])) <= 2000


def test_replay_heals_legacy_poisoned_rows_over_2000_chars() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")

    # Simulate an already-poisoned row in SQLite with 2040-char output_tail
    with engine.begin() as conn:
        conn.execute(
            insert(conversation_activity_events).values(
                event_id="evt_poisoned",
                conversation_id="conv_1",
                user_id="user_1",
                workspace_id=None,
                turn_id="turn_1",
                execution_id="exec_1",
                agent_id="agent:conv_1:main",
                parent_agent_id=None,
                event_type="tool.progressed",
                sequence=445,
                summary="Executando: Find Ashy providers.json",
                payload={"tool_name": "powershell", "output_tail": "a" * 2040},
                visibility="user",
                created_at=datetime.now(UTC),
            )
        )

    page = store.replay("user_1", "conv_1")
    assert len(page.events) == 1
    assert len(str(page.events[0].payload["output_tail"])) == 2000


def test_activity_with_many_artifacts_is_truncated_not_dropped() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    chat_store = PostgresChatStore(engine, activity_store=activity_store)

    turn = {
        "turn_id": "turn_1",
        "conversation_id": "conv_1",
        "execution_id": "exec_1",
        "user_id": "user_1",
    }
    many_artifacts = [{"path": f"file_{i}.txt", "action": "created"} for i in range(40)]
    chat_store._activity(
        turn,
        AgentActivityEventType.TOOL_FINISHED,
        "Finalizou powershell",
        {"tool_name": "powershell", "invocation_id": "call_1", "artifacts": many_artifacts},
    )
    page = activity_store.replay("user_1", "conv_1")
    assert len(page.events) == 1
    assert page.events[0].event_type == AgentActivityEventType.TOOL_FINISHED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/agentic/test_activity_redaction_bounds.py -v`
Expected: FAIL with `ValueError: payload text exceeds limit` on `replay` and `len(page.events) == 0` on `test_activity_with_many_artifacts_is_truncated_not_dropped`.

- [ ] **Step 3: Implement post-redaction clamping in `models.py`, stored row coercion in `agentic_activity.py`, and safe activity persistence/cursors/leases in `chat.py` and `gateway.py`**

1. In `src/agentos/agentic/models.py:125-132`, update `_bounded`:
```python
    if isinstance(value, str):
        if len(value) > text_limit:
            raise ValueError("payload text exceeds limit")
        if redact:  # W0-R11: what the activity log keeps is what its SSE stream and the interface show
            from agentos.redaction import redact_text

            redacted = redact_text(value)
            if len(redacted) > text_limit:
                return redacted[:text_limit]
            return redacted
        return value
```

2. In `src/agentos/persistence/postgres/agentic_activity.py:68-85`, add `_coerce_stored_payload` before `_event_from_row`:
```python
def _coerce_stored_value(value: object, *, depth: int, count: list[int], text_limit: int, item_limit: int) -> object:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:text_limit] if len(value) > text_limit else value
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for k, v in value.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if len(out) >= item_limit or count[0] >= item_limit:
                break
            count[0] += 1
            sub_limit = 2000 if depth == 0 and k in {"output_tail", "plan_content"} else text_limit
            out[k] = _coerce_stored_value(
                v, depth=depth + 1, count=count, text_limit=sub_limit, item_limit=item_limit
            )
        return out
    if isinstance(value, (list, tuple)):
        remaining = max(0, item_limit - count[0])
        sliced = list(value)[: min(len(value), item_limit, remaining)]
        count[0] += len(sliced)
        return [
            _coerce_stored_value(item, depth=depth + 1, count=count, text_limit=text_limit, item_limit=item_limit)
            for item in sliced
        ]
    return str(value)[:text_limit]


def _event_from_row(row: Mapping[str, object]) -> AgentActivityEvent:
    raw_payload = row["payload"]
    coerced_payload = (
        _coerce_stored_value(raw_payload, depth=0, count=[0], text_limit=512, item_limit=64)
        if isinstance(raw_payload, Mapping)
        else {}
    )
    return AgentActivityEvent(
        event_id=str(row["event_id"]),
        conversation_id=str(row["conversation_id"]),
        turn_id=str(row["turn_id"]),
        execution_id=str(row["execution_id"]),
        user_id=str(row["user_id"]),
        workspace_id=row["workspace_id"],
        agent_id=str(row["agent_id"]),
        parent_agent_id=row["parent_agent_id"],
        event_type=str(row["event_type"]),
        sequence=int(row["sequence"]),
        summary=str(row["summary"])[:512] or "Atividade",
        payload=coerced_payload if isinstance(coerced_payload, Mapping) else {},
        visibility=str(row["visibility"]),
        created_at=_aware(row["created_at"]),
    )
```

3. In `src/agentos/conversations/chat.py`:
   - In `PostgresChatStore._activity` (lines `495-522`), if `AgentActivityEvent` raises `ValueError`, fall back to `_coerce_stored_value` on `payload` and retry constructing `AgentActivityEvent` before giving up, so `TOOL_FINISHED` with many `artifacts` is never silently dropped.
   - In `generating_tool_call` (lines `1847-1862`), include `"invocation_id": call_id` alongside `"call_id": call_id`.
   - In `_query_activity_window` (`chat.py:1474-1480`) and `_compact_snapshot_activities` (`chat.py:1550`), drop all `"tool.progressed"` events for `settled_turns` (removing the `and payload.get("phase") != "generating_arguments"` exception) and pass `encode_cursor` into `_query_activity_window` / `_compact_snapshot_activities` so `entry["cursor"]` uses `self.activity_store._encode_cursor(user_id, conversation_id, r_id)` when available.
   - In `recover_stale` (`chat.py:2701-2706`), replace `conversation_dispatches.c.acquired_at < cutoff` with `func.coalesce(conversation_dispatches.c.lease_renewed_at, conversation_dispatches.c.acquired_at) < cutoff`.

4. In `src/agentos/api/gateway.py:1780-1785`, add an `except Exception:` guard around `run_in_threadpool(app_service.events, ...)` that logs the error and pauses briefly rather than crashing the ASGI response stream.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/agentic/test_activity_redaction_bounds.py tests/unit/persistence/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/models.py src/agentos/persistence/postgres/agentic_activity.py src/agentos/conversations/chat.py src/agentos/api/gateway.py tests/unit/agentic/test_activity_redaction_bounds.py
git commit -m "fix(activity): clamp post-redaction text expansion and heal stored SSE activity payloads"
```

---

### Task 2: Ponte `Assinatura CLI` — Stall Timeout Renovável, Prevenção de Deadlock `stdin`, Parser `<orin_tool_call>` Robusto e `FinishReason.LENGTH` (Bloco 2 Parte A)

**Files:**
- Modify: `src/agentos/provider_catalog/claude_code.py:650-1288`
- Modify: `src/agentos/provider_catalog/opencode.py:42-65, 395-415, 554-622`
- Modify: `src/agentos/agentic/runtime.py:74, 821, 953, 1109-1122`
- Test: `tests/unit/provider_catalog/test_claude_code_bridge_resilience.py`

**Interfaces:**
- Consumes: `StreamKind`, `FinishReason`, `NormalizedStreamItem` from `agentos.agentic.provider_stream`
- Produces:
  - Idle/stall timeout in `stream_claude_code_bridge` (`idle_timeout_seconds = 180.0`, `max_wall_clock_seconds = 1800.0`) and chunk-renewed deadline in `AgenticTurnRuntime`
  - Reader threads started *before* `proc.stdin.write()` in `stream_claude_code_bridge`
  - Balanced JSON extractor for `<orin_tool_call>...</orin_tool_call>` immune to inner `</orin_tool_call>` strings
  - Bidirectional tool name & argument resolution across Chat Mode (`read_file`, `edit_file`, `run_command`) and Code Mode (`Read`, `Edit`, `Bash`, `PowerShell`)
  - `FinishReason.LENGTH` emitted when `stop_reason == "max_tokens"` or `<orin_tool_call>` is truncated at EOF

- [ ] **Step 1: Write the failing tests in `tests/unit/provider_catalog/test_claude_code_bridge_resilience.py`**

```python
from agentos.provider_catalog.opencode import _parse_orin_tool_calls, _resolve_tool_name


def test_parse_orin_tool_calls_with_inner_closing_tag_in_json_string() -> None:
    text = (
        'Vou editar o arquivo.\n'
        '<orin_tool_call>{"name": "Edit", "arguments": {"file_path": "src/opencode.py", '
        '"old_string": "foo", "new_string": "tag = \'</orin_tool_call>\'\\nbar"}}</orin_tool_call>'
    )
    cleaned, calls = _parse_orin_tool_calls(text, allowed_names={"Edit", "Read", "Bash"})
    assert len(calls) == 1
    assert calls[0]["name"] == "Edit"
    assert "</orin_tool_call>" in calls[0]["arguments"]["new_string"]
    assert "Vou editar o arquivo." in cleaned


def test_resolve_tool_name_bidirectional_chat_and_code_modes() -> None:
    # Chat mode allowed_names (snake_case)
    chat_allowed = {"read_file", "write_file", "edit_file", "run_command", "search_files", "list_files"}
    assert _resolve_tool_name("Read", chat_allowed) == "read_file"
    assert _resolve_tool_name("Edit", chat_allowed) == "edit_file"
    assert _resolve_tool_name("Write", chat_allowed) == "write_file"
    assert _resolve_tool_name("Bash", chat_allowed) == "run_command"

    # Code mode allowed_names (PascalCase)
    code_allowed = {"Read", "Write", "Edit", "Bash", "Glob", "Grep"}
    assert _resolve_tool_name("read_file", code_allowed) == "Read"
    assert _resolve_tool_name("edit_file", code_allowed) == "Edit"
    assert _resolve_tool_name("write_file", code_allowed) == "Write"
    assert _resolve_tool_name("bash", code_allowed) == "Bash"
    assert _resolve_tool_name("run_command", code_allowed) == "Bash"
    assert _resolve_tool_name("search_files", code_allowed) == "Grep"
    assert _resolve_tool_name("list_files", code_allowed) == "Glob"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/provider_catalog/test_claude_code_bridge_resilience.py -v`
Expected: FAIL (`len(calls) == 0` due to inner `</orin_tool_call>` split, and `_resolve_tool_name` returning `None` for `Read` in chat mode and `read_file`/`bash` in code mode).

- [ ] **Step 3: Implement balanced `<orin_tool_call>` parser, bidirectional tool/arg mapping, reader-first `stdin` write, `FinishReason.LENGTH`, and stall timeouts**

1. In `src/agentos/provider_catalog/opencode.py`:
   - Expand `_TOOL_NAME_ALIASES` and `_resolve_tool_name` so every tool family (`read` / `read_file` / `Read`, `write` / `write_file` / `Write`, `edit` / `edit_file` / `Edit`, `bash` / `shell` / `run_command` / `powershell` / `Bash` / `PowerShell`, `grep` / `search_files` / `Grep`, `glob` / `list_files` / `Glob`, `web_fetch` / `WebFetch`, `web_search` / `WebSearch`, `todo_write` / `TodoWrite`, `agent` / `spawn_agent` / `Agent`) resolves against whichever candidate is in `allowed_names`.
   - Normalize tool call arguments in `_parse_orin_tool_calls`: if the resolved tool name is PascalCase (`Read`, `Write`, `Edit`), map `path -> file_path`, `old_text -> old_string`, `new_text -> new_string`; if snake_case (`read_file`, `write_file`, `edit_file`), map `file_path -> path`, `old_string -> old_text`, `new_string -> new_text`.
   - Replace the naive non-greedy regex extraction in `_parse_orin_tool_calls` (and `_feed_streaming_text` in `claude_code.py:935-961`) with a quote-aware JSON scanner `_find_orin_tool_call_close(buffer: str, open_idx: int) -> int` that only matches `</orin_tool_call>` outside JSON string literals after the top-level `{...}` object closes.
   - Update `_build_bridge_system_prompt` (`opencode.py:395-415`) so it identifies as Orin's tool bridge and permits emitting multiple `<orin_tool_call>` blocks in a single response when calls are independent.

2. In `src/agentos/provider_catalog/claude_code.py`:
   - Start `stdout_thread` and `stderr_thread` **before** writing `user_envelope` to `proc.stdin` (`lines 835-878`).
   - Treat `timeout_seconds` (`180.0`) as an **idle timeout** reset whenever a line is dequeued from `line_queue` (`deadline = min(hard_deadline, time.monotonic() + effective_timeout)` where `hard_deadline = started + max(effective_timeout * 10.0, 1800.0)`).
   - Capture `stop_reason` from `message_delta` (`delta.stop_reason`) and `result` events; if `stop_reason == "max_tokens"` (or if an unclosed `<orin_tool_call>` remains in `streaming_text_buffer` at EOF), yield `FinishReason.LENGTH` instead of `FinishReason.STOP`.

3. In `src/agentos/agentic/runtime.py`:
   - At line `953` (inside `for raw_event in events:`), refresh `deadline = time.monotonic() + self.provider_timeout_seconds` whenever a delta event arrives so active streaming is never cut off at 90s.
   - At lines `1109-1122`, treat `ProviderTransportError` when `not text_parts and not calls` as a retryable stall (`is_keepalive_stall = True`) rather than immediately halting the turn as `reconciliation_required`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/provider_catalog/test_claude_code_bridge_resilience.py tests/unit/providers/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/opencode.py src/agentos/provider_catalog/claude_code.py src/agentos/agentic/runtime.py tests/unit/provider_catalog/test_claude_code_bridge_resilience.py
git commit -m "fix(claude-cli): add stall timeout, balanced tool call parser, and bidirectional tool mapping"
```

---

### Task 3: Ponte `Assinatura CLI` — Visão (Imagens), Envelhecimento de Contexto, Janela Realista, `cancel_check`, Subagentes e Semáforo (Bloco 2 Parte B)

**Files:**
- Modify: `src/agentos/provider_catalog/opencode.py:456-552`
- Modify: `src/agentos/provider_catalog/claude_code.py:92-115, 240-262, 550-565, 737-830, 1185-1225`
- Modify: `src/agentos/agentic/provider_stream.py:880-896`
- Modify: `src/agentos/agentic/runtime.py:489-513, 1522-1531`
- Modify: `src/agentos/workers/chat.py:1133-1184, 1244-1312`
- Modify: `src/agentos/agentic/agent_definitions.py:258-270`
- Test: `tests/unit/provider_catalog/test_claude_code_context_and_subagents.py`

**Interfaces:**
- Consumes: `is_claude_cli_bridge_url` from `agentos.provider_catalog.claude_code`
- Produces:
  - Multimodal `content` blocks in `_format_bridge_conversation_transcript` / `stream_claude_code_bridge` when images are present in `messages` + no duplicate mid-turn `[System]` prompts
  - `_prefix_caching` returning `False` when `is_claude_cli_bridge_url(base_url)` is `True`
  - Realistic `200_000` default context window for CLI bridge autocompaction and clean `_SUBSCRIPTION_MODELS`
  - `cancel_check` forwarded from `HTTPProviderStreamTransport.stream` to `stream_claude_code_bridge`
  - Subagents keeping `target_provider = "anthropic"` on `Assinatura CLI` and resolving `haiku`/`sonnet`/`opus` aliases

- [ ] **Step 1: Write the failing tests in `tests/unit/provider_catalog/test_claude_code_context_and_subagents.py`**

```python
from agentos.provider_catalog.opencode import _format_bridge_conversation_transcript
from agentos.agentic.agent_definitions import resolve_agent_model


def test_format_bridge_transcript_does_not_duplicate_mid_turn_system_into_system_parts() -> None:
    messages = [
        {"role": "system", "content": "Initial system prompt."},
        {"role": "user", "content": "Fix the bug."},
        {"role": "system", "content": "Mid-turn phase note."},
    ]
    system_text, user_text = _format_bridge_conversation_transcript(messages)
    assert "Initial system prompt." in system_text
    assert "Mid-turn phase note." not in system_text
    assert "[System]\nMid-turn phase note." in user_text


def test_format_bridge_transcript_preserves_image_blocks_metadata() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Veja este print:"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="},
                },
            ],
        }
    ]
    _, user_text, image_blocks = _format_bridge_conversation_transcript(messages, return_images=True)
    assert "Veja este print:" in user_text
    assert len(image_blocks) == 1
    assert image_blocks[0]["type"] == "image"


def test_resolve_agent_model_supports_claude_aliases_without_manual_settings() -> None:
    assert resolve_agent_model("haiku", {}) == "claude-haiku-4-5-20251001"
    assert resolve_agent_model("sonnet", {}) == "claude-sonnet-4-6"
    assert resolve_agent_model("opus", {}) == "claude-opus-4-7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/provider_catalog/test_claude_code_context_and_subagents.py -v`
Expected: FAIL (`Mid-turn phase note.` duplicated into `system_text`, `return_images` not supported, and `resolve_agent_model("haiku", {})` returning `None`).

- [ ] **Step 3: Implement image block preservation, non-duplicated system messages, `_age_tool_results` activation, subagent provider retention, `cancel_check` forwarding, and CLI semaphore**

1. In `src/agentos/provider_catalog/opencode.py:456-552` (`_format_bridge_conversation_transcript`):
   - Only append `role == "system"` messages to `system_parts` when `not turns_text` (before the first user/assistant turn); once `turns_text` has started, append mid-turn `system` messages only to `turns_text` as `[System]\n...`.
   - Extract any `b_type in {"image", "input_image"}` blocks from `user` and `tool_result` messages; when `return_images=True`, return `(system_prompt, prompt_text, image_blocks)` and in `stream_claude_code_bridge` (`claude_code.py:831-839`), if `image_blocks` is non-empty, send `"content": [*image_blocks, {"type": "text", "text": prompt_text}]` in the `stream-json` user envelope!

2. In `src/agentos/agentic/provider_stream.py:886-894`:
   - Pass `cancel_check=cancel_check` into `stream_claude_code_bridge(...)`.

3. In `src/agentos/agentic/runtime.py`:
   - In `_prefix_caching` (`lines 1522-1531`), if `provider == "anthropic"` and `is_claude_cli_bridge_url(getattr(self.provider, "base_url", None))`, return `False` so `_age_tool_results()` runs on every iteration.
   - In `_retry_wait` (`lines 489-513`), if `retry_after` is provided and `> 0`, wait `retry_after` even when `self.retry_base_delay_seconds <= 0`.

4. In `src/agentos/workers/chat.py`:
   - In `_provider_transport` (`lines 1162-1184`), when `provider == "anthropic" and is_claude_cli_bridge_url(base_url)`, always set `first_key = ""` and return `HTTPProviderStreamTransport` directly without wrapping in `MultiKeyProviderStreamTransport`.
   - In `_child_provider_for_turn` (`lines 1277-1312`), when `target_provider == "anthropic"` and `is_claude_cli_bridge_url(self._provider_base_url("anthropic"))`, keep `target_provider = "anthropic"` whenever `normalized_model` starts with `"claude-"` or is in `{"haiku", "sonnet", "opus", "fable"}`.
   - In `_max_context_tokens_for` (`lines 1244-1275`), when `provider == "anthropic"` and `is_claude_cli_bridge_url(self._provider_base_url("anthropic"))` and `"[1m]"` is not in `model_id`, cap `context_window` at `200_000` so autocompaction triggers safely before 200k tokens.

5. In `src/agentos/agentic/agent_definitions.py:258-269` (`resolve_agent_model`):
   - Provide default fallback mappings for `MODEL_ALIASES`: `{"haiku": "claude-haiku-4-5-20251001", "sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-7", "fable": "claude-sonnet-4-6"}` when not overridden in `settings`.

6. In `src/agentos/provider_catalog/claude_code.py`:
   - Clean `_SUBSCRIPTION_MODELS` (`lines 92-111`) to list real Claude Code models (`claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-haiku-4-5-20251001`) with `context_window = 200_000` (while keeping `_normalize_claude_model_id` mapping legacy `claude-opus-5-5` -> `claude-opus-4-7`).
   - Add `_CLAUDE_CLI_SEMAPHORE = threading.BoundedSemaphore(3)` around the `subprocess.Popen` execution in `stream_claude_code_bridge` (checking `cancel_check` while waiting to acquire) and pass an isolated temp directory `cwd=tempfile.gettempdir()` to `subprocess.Popen`.
   - Parse `resets_at` into `retry_after_seconds` when raising `ProviderTransportError` on `rate_limit_event` (`lines 1185-1223`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/provider_catalog/test_claude_code_context_and_subagents.py tests/unit/workers/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/opencode.py src/agentos/provider_catalog/claude_code.py src/agentos/agentic/provider_stream.py src/agentos/agentic/runtime.py src/agentos/workers/chat.py src/agentos/agentic/agent_definitions.py tests/unit/provider_catalog/test_claude_code_context_and_subagents.py
git commit -m "fix(claude-cli): preserve vision blocks, enable tool result aging, and keep subagents on CLI subscription"
```

---

### Task 4: Orin Code — Retrieval (`project_map`) Sem Bloqueio de Embedding e Git Change Tracking com Baseline Pré-Comando (`workspace.py` & `agent_tools.py`) (Bloco 3 Parte A)

**Files:**
- Modify: `src/agentos/retrieval/service.py:89-125`
- Modify: `src/agentos/retrieval/indexer.py:36-60, 138-155`
- Modify: `src/agentos/agentic/workspace.py:444-490`
- Modify: `src/agentos/agentic/agent_tools.py:1205-1225`
- Test: `tests/unit/retrieval/test_project_map_no_embed_block.py`
- Test: `tests/unit/agentic/test_git_change_tracking_baseline.py`

**Interfaces:**
- Consumes: `WorkspaceIndexer.scan(workspace_id, root, *, embed: bool = True)`
- Produces:
  - `RetrievalService.project_map` scanning AST/imports with `embed=False`
  - Fast-fail circuit-breaker in `WorkspaceIndexer._embed_pending()` when the embedding provider fails/returns empty on the first chunk
  - Baseline-diffed `begin_change_tracking()` / `end_change_tracking()` in git repositories ignoring pre-existing dirty/untracked files, `.orin/`, and `uploads/`

- [ ] **Step 1: Write the failing tests in `tests/unit/retrieval/test_project_map_no_embed_block.py` and `tests/unit/agentic/test_git_change_tracking_baseline.py`**

```python
# tests/unit/retrieval/test_project_map_no_embed_block.py
from pathlib import Path
from agentos.retrieval.service import RetrievalService


class _FailingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        raise RuntimeError("Ollama /api/embed 404 Not Found")


def test_project_map_does_not_call_embedder(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def hello():\n    return 42\n", encoding="utf-8")
    embedder = _FailingEmbedder()
    service = RetrievalService(db_dir=tmp_path / ".db", embedding=embedder)

    result = service.project_map("ws_1", tmp_path)
    assert "app.py" in result
    assert embedder.calls == 0
```

```python
# tests/unit/agentic/test_git_change_tracking_baseline.py
import subprocess
from pathlib import Path
from agentos.agentic.workspace import LocalWorkspace


def test_git_repo_change_tracking_ignores_preexisting_untracked_and_uploads(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    # Pre-existing untracked files (like uploads/image.png and .orin/state/cwd.json)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "image.png").write_bytes(b"png")
    (tmp_path / "preexisting.txt").write_text("dirty", encoding="utf-8")

    ws = LocalWorkspace(tmp_path)
    baseline = ws.begin_change_tracking()
    # Read-only operation: nothing modified during the command
    artifacts = ws.end_change_tracking(baseline)
    assert artifacts == []

    # Now actually modify a new file during a command
    baseline2 = ws.begin_change_tracking()
    (tmp_path / "new_feature.py").write_text("x = 1\n", encoding="utf-8")
    artifacts2 = ws.end_change_tracking(baseline2)
    paths = [a["path"] for a in artifacts2]
    assert paths == ["new_feature.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/retrieval/test_project_map_no_embed_block.py tests/unit/agentic/test_git_change_tracking_baseline.py -v`
Expected: FAIL (`embedder.calls > 0` in `test_project_map_does_not_call_embedder`, and `artifacts` containing `preexisting.txt` and `uploads/image.png` in `test_git_repo_change_tracking_ignores_preexisting_untracked_and_uploads`).

- [ ] **Step 3: Implement `embed=False` in `project_map`, circuit-breaker in `_embed_pending`, and git baseline diffing in `workspace.py` & `agent_tools.py`**

1. In `src/agentos/retrieval/indexer.py`:
   - Change `def scan(self, workspace_id: str, root: Path, *, embed: bool = True) -> dict[str, int]:` and only call `self._embed_pending()` when `embed` is `True`.
   - In `_embed_pending(self)` (`lines 138-152`), wrap `self._embedding.embed(...)` in a `try/except Exception:` and if the first attempt fails or returns `[]`, break out of the loop immediately so unreachable Ollama `/api/embed` endpoints fail in `<50ms` instead of looping for 5 minutes.
2. In `src/agentos/retrieval/service.py`:
   - Add `embed: bool = True` to `_refresh_if_stale(self, workspace_id: str, root: Path, *, embed: bool = True)` and pass `embed=False` from `project_map()`.
3. In `src/agentos/agentic/workspace.py:444-490`:
   - Add `_IGNORED_CHANGE_PREFIXES = (".orin/", ".git/", "uploads/", "node_modules/", ".venv/", "__pycache__/")`.
   - In `begin_change_tracking(self)`: when `(self.root / ".git").exists()`, call `self._git_changed_files()` and build a baseline dict `{rel_path: self._file_signature(self.root / rel_path)}` (where `_file_signature` returns `(stat.st_mtime_ns, stat.st_size)` or `None` if deleted).
   - In `end_change_tracking(self, baseline)`: when `(self.root / ".git").exists()`, compare current `self._git_changed_files()` against `baseline`, emitting only paths not in `ignored_prefixes` whose signature differs from `baseline.get(rel_path)`.
4. In `src/agentos/agentic/agent_tools.py:1205-1220` (`_record_changed_paths`):
   - Add `"uploads/"` to `ignored_prefixes`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/retrieval/test_project_map_no_embed_block.py tests/unit/agentic/test_git_change_tracking_baseline.py tests/unit/retrieval/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/retrieval/indexer.py src/agentos/retrieval/service.py src/agentos/agentic/workspace.py src/agentos/agentic/agent_tools.py tests/unit/retrieval/test_project_map_no_embed_block.py tests/unit/agentic/test_git_change_tracking_baseline.py
git commit -m "fix(code-mode): skip embeddings in project_map and diff git change tracking against baseline"
```

---

### Task 5: Windows Shell Pipes & Process Tree (`bash.py`, `_shared.py`), Subagent Lifecycle (`session.py`), Microcompaction & Checkpoints (Bloco 3 Parte B & Bloco 4 Backend)

**Files:**
- Modify: `src/agentos/agentic/tools/shell/bash.py:300-375`
- Modify: `src/agentos/agentic/tools/_shared.py:314-325, 374-386, 658-666`
- Modify: `src/agentos/agentic/session.py:1150-1220, 1319-1337, 1505-1520, 2240-2420, 2812-2835`
- Modify: `src/agentos/agentic/runtime.py:67`
- Modify: `src/agentos/code_mode/checkpoints.py:115-130`
- Test: `tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py`

**Interfaces:**
- Produces:
  - Non-hanging pipe drain in `_run_shell` when child processes inherit `stdout`/`stderr` after parent shell exits
  - Unconditional `_terminate_pid(process.pid)` in `_terminate_process_tree` on Windows
  - Automatic millisecond-to-second conversion when `timeout >= 1000` in `coerce_tool_arguments`
  - `description` preserved in `tool_progressed` payload and `powershell` handled in `_started_summary`
  - `try / finally` around subagent execution updating `conversation_agents.state` and emitting subagent completion activity event
  - `MICROCOMPACT_KEEP_RECENT = 16` and `MAX_CHECKPOINT_FILE_BYTES = 10 * 1024 * 1024`

- [ ] **Step 1: Write the failing tests in `tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py`**

```python
from agentos.agentic.tools._shared import coerce_tool_arguments
from agentos.agentic.runtime import MICROCOMPACT_KEEP_RECENT


def test_coerce_tool_arguments_converts_millisecond_timeout_alias() -> None:
    coerced = coerce_tool_arguments("powershell", {"command": "Write-Output ok", "timeout": 120000})
    assert coerced["timeout_seconds"] == 120


def test_microcompact_keep_recent_allows_parallel_file_reads() -> None:
    assert MICROCOMPACT_KEEP_RECENT >= 16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py -v`
Expected: FAIL (`coerced["timeout_seconds"] == 600` instead of `120`, and `MICROCOMPACT_KEEP_RECENT == 6`).

- [ ] **Step 3: Implement shell pipe drain, process tree kill, timeout ms conversion, `session.py` subagent `try/finally` + `powershell` progress description, `MICROCOMPACT_KEEP_RECENT = 16`, and checkpoint size guard**

1. In `src/agentos/agentic/tools/_shared.py`:
   - In `coerce_tool_arguments` (`lines 374-385`), if `"timeout"` was in `arguments` and `"timeout_seconds"` was not (or if `raw_timeout >= 1000`), convert milliseconds to seconds (`int(round(raw_timeout / 1000.0))`) before clamping to `[1, MAX_COMMAND_TIMEOUT_SECONDS]`.
   - In `_terminate_process_tree` (`lines 658-665`), on Windows (`os.name == "nt"`), always call `_terminate_pid(process.pid)` even if `process.poll() is not None` so child processes in the tree are terminated, and safely close `process.stdout` and `process.stderr`.
2. In `src/agentos/agentic/tools/shell/bash.py:335-372`:
   - Track `exited_at: float | None = None` when `process.poll() is not None`. Once `process.poll() is not None` and `time.monotonic() - exited_at >= 0.25`, break out of the `while` loop even if `t_out.is_alive()` or `t_err.is_alive()` (closing `process.stdout`/`process.stderr` so background grandchildren holding inherited handles cannot hang the tool for 120s).
   - Remove the concurrent `process.communicate(timeout=0.5)` calls at lines `356` and `368`, using `t_out.join(timeout=0.3)` / `t_err.join(timeout=0.3)` instead.
3. In `src/agentos/agentic/session.py`:
   - Line `1159` (`_started_summary`): change `if name == "run_command" and command:` to `if name in {"run_command", "bash", "powershell"} and command:`.
   - Lines `1325-1333` (`on_tool_progress`): include `"description": str(payload.get("description") or "")[:200]` in `progress_payload`.
   - Lines `1215-1217` and `1511-1513`: include `parent_agent_id=self.main_agent_id` in `_SubagentStore` events and emit a `turn.completed` / `turn.failed` lifecycle event for the subagent so the frontend knows the subagent finished.
   - Lines `2243-2415` (`_execute_subagent_run_unlocked`): wrap execution in `try / finally` so `self.agents_store.set_state(agent_id, final_state)` is always updated (`"completed"`, `"failed"`, or `"cancelled"`).
   - Lines `2820-2830` (`close_background_subagents`): when the parent turn is cancelled or failed, iterate `self._background_subagents.values()` and call `entry["cancel"].set()`.
4. In `src/agentos/agentic/runtime.py:67`: set `MICROCOMPACT_KEEP_RECENT = 16`.
5. In `src/agentos/code_mode/checkpoints.py:121`: check `target.stat().st_size <= 10 * 1024 * 1024` before reading bytes into a checkpoint snapshot.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py tests/unit/test_orin_code_phase_a.py tests/unit/test_orin_code_phase_b.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/tools/_shared.py src/agentos/agentic/tools/shell/bash.py src/agentos/agentic/session.py src/agentos/agentic/runtime.py src/agentos/code_mode/checkpoints.py tests/unit/agentic/test_windows_shell_and_subagent_lifecycle.py
git commit -m "fix(runtime): harden windows shell pipes, subagent lifecycle, and microcompaction window"
```

---

### Task 6: Frontend — Reconciliação de Snapshot/SSE, Exibição Garantida de `item.content`, Suporte a `powershell`, `Bancada` e `Equipe em atividade` (Bloco 1 & Bloco 4 Frontend)

**Files:**
- Modify: `frontend/src/features/conversations/ChatPage.tsx:251-268, 355-415, 1871-1961, 2075-2105, 2420-2445, 2570-2626`
- Modify: `frontend/src/features/conversations/CodeToolCallBlock.tsx:29-42, 145, 171, 278, 320, 408, 494-505`
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx:67-90, 367, 1145-1285`
- Modify: `frontend/src/features/conversations/turnTimelineFold.ts:275-285, 500-515`
- Modify: `frontend/src/features/conversations/activitySummary.ts:202-225, 265-323, 501`
- Modify: `frontend/src/features/conversations/activityReducer.ts:94-120`
- Modify: `frontend/src/features/conversations/TeamActivityView.tsx:140-155, 201-215, 313-370`
- Modify: `frontend/src/features/conversations/CodeWorkbenchPanel.tsx:226-230`
- Modify: `frontend/src/api/conversations.ts:775`
- Test: `frontend/src/features/conversations/__tests__/longTaskAndPowershellFixes.test.tsx`

**Interfaces:**
- Produces:
  - Complete `powershell` parity in `CodeToolCallBlock.tsx`, `TurnTimeline.tsx`, `activitySummary.ts`, `conversations.ts`
  - `computeSettledEventIds(events, turnCompleted)` settling all open tool/thought/agent items when `turnCompleted` is `true`
  - `ChatPage.tsx` reconciling `activity.events` & `cursorRef.current` on `loadSnapshot(false)` when turn is settled or SSE is degraded, and always rendering `item.content` when the turn is completed and the timeline lacks the final assistant answer
  - `codeWorkbenchFiles` ignoring `search_files`/`search_code` regex labels and separating `changedWorkbenchFiles` (`status === 'modified' | 'added'`) from `inspected` files
  - `TeamActivityView.tsx` excluding main-agent tool calls and respecting `running`

- [ ] **Step 1: Write the failing frontend tests in `frontend/src/features/conversations/__tests__/longTaskAndPowershellFixes.test.tsx`**

```tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CodeToolCallBlock } from '../CodeToolCallBlock';
import { buildTurnTimeline } from '../turnTimelineFold';
import type { ActivityEventItem } from '../../../types/conversation';

describe('PowerShell tool rendering & completed turn settlement', () => {
  it('formats powershell tool signature cleanly like Bash and settles when turnCompleted is true', () => {
    const event: ActivityEventItem = {
      id: 'evt_3989',
      type: 'tool.progressed',
      turnId: 'turn_1',
      sequence: 444,
      timestamp: '2026-10-31T20:00:16Z',
      summary: 'Executando: Find Ashy providers.json, mask API keys',
      state: 'waiting_tool',
      status: 'running',
      kind: 'tool',
      toolName: 'powershell',
      toolKind: 'terminal',
      command: 'Get-ChildItem providers.json',
      outputTail: 'C:\\Users\\carlos\\AppData\\Roaming\\Ashy\\providers.json',
    };

    const timeline = buildTurnTimeline([event], true);
    expect(timeline.length).toBeGreaterThan(0);
    const first = timeline[0];
    expect(first.kind).toBe('tool');
    if (first.kind === 'tool') {
      expect(first.event.state).toBe('completed');
      render(<CodeToolCallBlock event={first.event} />);
      expect(screen.queryByText(/powershell\(Executando:/i)).toBeNull();
      expect(screen.getByText(/Find Ashy providers\.json, mask API keys/i)).toBeTruthy();
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/longTaskAndPowershellFixes.test.tsx`
Expected: FAIL (`first.event.state` is still `'waiting_tool'` and text renders `powershell(Executando: Find Ashy providers.json, mask API keys)`).

- [ ] **Step 3: Implement Frontend fixes across `CodeToolCallBlock.tsx`, `turnTimelineFold.ts`, `activitySummary.ts`, `TurnTimeline.tsx`, `ChatPage.tsx`, `TeamActivityView.tsx`, `CodeWorkbenchPanel.tsx`, and `conversations.ts`**

1. **`CodeToolCallBlock.tsx`, `TurnTimeline.tsx`, `activitySummary.ts`, `conversations.ts`**:
   - Add `'powershell'` to `case 'run_command': case 'bash': case 'powershell':` in `CodeToolCallBlock.tsx:494-505` (and lines `145`, `171`, `278`, `320`, `408`).
   - Add `'powershell'` to `CODE_TOOL_NAMES` (`TurnTimeline.tsx:67-89`), `summarizeCodeToolBatch` (`TurnTimeline.tsx:367`), `COMMAND_TOOLS` (`activitySummary.ts:501`), and `parseBashOutput` (`conversations.ts:775`).
2. **`activitySummary.ts`, `turnTimelineFold.ts`, `activityReducer.ts`**:
   - In `computeSettledEventIds(events: ActivityEventItem[], turnCompleted = false)` (`activitySummary.ts:265-323`), when `turnCompleted` is `true`, add all remaining IDs in `openByTurn`, `openByCall`, and `openByTool` to `settled`.
   - In `buildTurnTimeline` (`turnTimelineFold.ts:277`), pass `explicitTurnCompleted` into `computeSettledEventIds(turnEvents, explicitTurnCompleted)`, and if `explicitTurnCompleted` is `true`, ensure any tool event whose `state` is `'waiting_tool'` or `'working'` is cloned with `state: 'completed', status: 'succeeded'` and stripped of leading `"Executando: "` in `summary`.
3. **`ChatPage.tsx` & `CodeWorkbenchPanel.tsx` & `TeamActivityView.tsx`**:
   - In `ChatPage.tsx` (`loadSnapshot`, lines `251-267`): when `resetActivity` is `false` and (`next.state !== 'working'` or `activity.connection === 'degraded'`), dispatch `{ type: 'snapshot', events: next.activities, cursor: next.activity_cursor, hasOlder: Boolean(next.has_older_activities), oldestId: next.oldest_activity_id ?? null }` and update `cursorRef.current = next.activity_cursor`.
   - In `ChatPage.tsx` (`onCursor` / `onEvent` in `streamConversationEvents`, lines `360-385`): transition `activity.connection` to `'live'` when currently `'connecting'` or `'degraded'`.
   - In `ChatPage.tsx` (`lines 2422-2437`): when rendering an assistant message with `timeline.length > 0`, if `item.status === 'completed'` and `item.content.trim()` is non-empty and not already duplicated as the final text item in `timeline`, render the markdown `item.content` below `<TurnTimeline />` so the final answer is always visible!
   - In `ChatPage.tsx` (`codeWorkbenchFiles`, lines `1913-1951`): exclude search tools (`search_files`, `search_code`, `grep`, `glob`, `list_files`, `project_map`), never fall back to `event.label` as `rawPath`, normalize path slashes, and compute `changedWorkbenchFiles = codeWorkbenchFiles.filter((f) => f.status === 'modified' || f.status === 'added')`. Use `changedWorkbenchFiles` for the `"N arquivos alterados · Ver alterações"` card (`lines 2572-2626`) and in `CodeWorkbenchPanel.tsx:226-229` return `modifiedOrAdded.length` directly.
   - In `ChatPage.tsx` (`subagentRoster`, lines `1871-1899`) and `TeamActivityView.tsx`: mark subagents as completed when `!running`, filter out main-agent events (`cleanId.endsWith(':main')`), and respect the `running` prop in `TeamActivityView.tsx`.

- [ ] **Step 4: Run frontend tests to verify they pass**

Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/longTaskAndPowershellFixes.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/conversations/ChatPage.tsx frontend/src/features/conversations/CodeToolCallBlock.tsx frontend/src/features/conversations/TurnTimeline.tsx frontend/src/features/conversations/turnTimelineFold.ts frontend/src/features/conversations/activitySummary.ts frontend/src/features/conversations/activityReducer.ts frontend/src/features/conversations/TeamActivityView.tsx frontend/src/features/conversations/CodeWorkbenchPanel.tsx frontend/src/api/conversations.ts frontend/src/features/conversations/__tests__/longTaskAndPowershellFixes.test.tsx
git commit -m "fix(ui): reconcile completed turn snapshots, support powershell blocks, and fix workbench/team counts"
```
