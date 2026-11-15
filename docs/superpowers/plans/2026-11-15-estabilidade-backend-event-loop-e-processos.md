# Estabilidade do Backend, Isolamento do Event Loop, Auto-Heal e Ciclo de Vida de Processos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar de forma definitiva os congelamentos recorrentes do `backend`/UI (ReDoS no GIL em Markdown/mídia/redaction, tempestade `429`/`403` no Discord, bloqueio síncrono do event loop e exaustão dos 6 sockets HTTP/1.1 do Electron), adicionar *auto-heal* isolado *in-place* por serviço no `Supervisor` sem interromper turnos ativos nem perder a posse do `OmniRoute`, conter árvores de subprocessos no Windows com *Job Objects* 64-bit e liberar CLIs não-persistentes antes de esperas em background, e implantar higiene determinística de locks `.turn`, turnos `paused` órfãos e eventos transitórios antigos no `orin.db` (preservando `tool.started`).

**Architecture:** Endurecimento *in-place* sobre a topologia atual (`launcher` → `backend`, `worker`, `scheduler`), substituindo regexes com backtracking superlinear por scanners $O(N)$ e quantificadores possessivos com fronteira esquerda, despachando I/O síncrono e locks de disco para `run_in_threadpool` com SQL indexado, protegendo os 6 sockets HTTP/1.1 do Electron com worker pool (`<= 2`), `AbortSignal`, throttle e cache restrito a `waiting_user`, monitorando `/healthz` continuamente a cada `5s` no `Supervisor` para reiniciar o `backend` na mesma porta/índice, contendo previews/shells com handles Win32 64-bit e identidade verificada via `agentos.process_identity`, e drenando eventos estritamente transitórios `> 7 dias` sem apagar `tool.started`.

**Tech Stack:** Python 3.12+ (possessive quantifiers `*+`/`++`), FastAPI / Starlette (`run_in_threadpool`), SQLAlchemy Core + Alembic + SQLite WAL, `httpx`, Win32 Job Objects / `GetProcessTimes` via `ctypes` + `agentos.process_identity`, React 19 + TypeScript + Vitest 4 (ESM).

**Spec:** `docs/superpowers/specs/2026-11-15-estabilidade-backend-event-loop-e-processos-design.md`

## Global Constraints

- **Runtime Orin ao vivo:** Nunca matar processos do Orin Desktop em execução (porta `49200`) nem escrever no `AppData\Local\Orin\data\orin.db` durante desenvolvimento/testes; todos os testes usam bancos e diretórios temporários (`tmp_path`).
- **Preservar árvore git local:** Não adicionar nem reverter `src/agentos/providers/antigravity_cli/catalog.py` nem `docs/superpowers/plans/2026-11-07-agentes-em-segundo-plano-e-previa.md` em commits (`git add` explícito apenas nos arquivos listados em cada task).
- **Execução em Git Worktrees (`workspace:"share"`):** `.venv/` e `frontend/node_modules/` estão no `.gitignore` e não existem dentro de worktrees compartilhados. Antes de rodar `pytest` ou `npm` em qualquer task, sempre resolva o `.venv` do repositório raiz, aponte `$env:PYTHONPATH = (Resolve-Path .\src).Path` (sem isso, o pacote editável `agentos` importa `<main_repo>\src` em vez do worktree!) e crie a *junction* `frontend\node_modules` se ausente:
  ```powershell
  $mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path
  $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }
  $env:PYTHONPATH = (Resolve-Path .\src).Path
  if (-not (Test-Path frontend\node_modules) -and (Test-Path (Join-Path $mainRoot "frontend\node_modules"))) {
      New-Item -ItemType Junction -Path frontend\node_modules -Target (Join-Path $mainRoot "frontend\node_modules") | Out-Null
  }
  ```
  *(Nota para Vitest: `frontend/package.json` já define `"test": "vitest run"`, portanto invoque `npm --prefix frontend test -- tests/unit/...` **sem** passar `--run` duplicado.)*
- **Layout canônico de testes:** Todos os novos testes unitários de backend devem residir em `tests/unit/...` e os de frontend em `frontend/tests/unit/...` para rodar junto às suítes existentes.
- **Garantia $O(N)$ anti-GIL e zero vazamento de segredos:** Nenhum parser de Markdown/mídia ou redaction pode usar quantificadores aninhados ou prefixos curinga sem fronteira esquerda (`< 80ms` para entradas patológicas de `20–50 KB` sem fechamento, zero regressão nos 6 vetores de vazamento de segredo).
- **Preservação de auto-update e servidores auto-background no Windows:** O Job Object raiz do `Supervisor` mantém `allow_breakaway=True` (`0x2000 | 0x800`) para `orin update --apply --detach`; `PreviewServerManager` usa `ProcessGroup(allow_breakaway=False)` (`0x2000 = KILL_ON_JOB_CLOSE`); `bash.py` **mantém `_WindowsJob(kill_on_close=False)`** (`LimitFlags = 0`, que já bloqueia `CREATE_BREAKAWAY_FROM_JOB` sem matar servidores auto-backgroundados quando `_run_shell` retorna) e registra o job para término limpo em `stop_process`/`SessionEnd`.
- **Regra do Watchdog sobre `worker`:** Jamais matar um `worker` com `child.exited() is None` por atraso em `runtime_heartbeats`; reiniciar `worker`/`scheduler` apenas em `child.exited() is not None` validando `heartbeat_probe(..., minimum_updated_at=restart_started_at)`, e reiniciar `backend` in-place no mesmo índice de `self.children` em `child.exited() is not None` ou após `3` falhas consecutivas de `/healthz` (intervalo real de `5,0s`).
- **Poda segura do SQLite (preservando `tool.started`):** Deletar em lotes curtos (`LIMIT 2000`) **exclusivamente** eventos estritamente transitórios (`assistant.delta`, `agent.thought_delta`, `tool.progressed`, `tool.requested`, `code_mode.validation_*`, `code_mode.stage_changed`, `speak.*`) de turnos terminais com `finished_at < now - 7 dias` — **jamais remover `tool.started`**, pois `_compact_snapshot_activities` depende dele para enriquecer `tool.finished` com `path`, `target_path`, `command`, `description` e `transcript_id`.
- **Verificação Visual Obrigatória:** Todas as alterações em `frontend/src/` devem ser inspecionadas visualmente em navegador real (desktop/mobile + aba Network) antes da conclusão.

---

## File Structure

- **Modify:** `src/agentos/redaction.py` — substituição de `_ASSIGNMENT`, `_FLAG` e `_URL_PASSWORD` por regexes possessivas $O(N)$ com fronteira esquerda e zero vazamento.
- **Modify:** `src/agentos/discord/formatter.py` — scanner linear `iter_markdown_target_spans` (suportando imagens clicáveis, `\]` escapado, CRLF e *watermarks* anti-$O(N^2)$) e reescrita de `strip_attached_workspace_links` sem `_MD_IMAGE_LINK_RE`/`_MD_FILE_LINK_RE`.
- **Modify:** `src/agentos/discord/rest_client.py` — atributo `retry_after` em `DiscordApiError` e fail-fast quando `retry_after > MAX_RATE_LIMIT_SLEEP_SECONDS`.
- **Modify:** `src/agentos/discord/bridge_service.py` — uso de `_iter_markdown_targets` e `_iter_plain_media_paths` lineares, bloqueio de extração mid-stream, *backoff* unificado em `send_message` e `edit_message` (`429`, `404` msg vs `403`/`10003` canal), fechamento de conversas deletadas (`ApplicationNotFoundError`) e canceladas, avanço seguro via `_advance_watch_to_new_turn` preservando `origin`, e 1 leitura SQLite por tick em `_await_turn_with_typing` (`0.45s`).
- **Modify:** `src/agentos/launcher/desktop.py` — fechamento de `log` (`desktop.log`) no processo pai em bloco `finally`.
- **Modify:** `src/agentos/launcher/probes.py` — parâmetro opcional `minimum_updated_at: datetime | None = None` em `heartbeat_probe`.
- **Modify:** `src/agentos/launcher/supervisor.py` — probe contínuo de `/healthz` espaçado em `5s`, `_restart_child` *in-place* preservando ordem de `self.children`, respeito a shutdown limpo (`exit_code = 0`) e *circuit-breaker* (`3` restarts em `120s`), mais `sweep_stale_workspace_locks` em `_prepare()`.
- **Modify:** `src/agentos/omniroute/process_manager.py` — persistência de `{pid, created}` em `omniroute-owner.json` para re-adoção pós-restart do backend, e liberação de `self._lock` durante probes HTTP de `5s` e esperas de `45s`.
- **Modify:** `src/agentos/bootstrap/production.py` & `src/agentos/api/asgi.py` — `DependencyProbe.from_engine(engine)` reutilizando o pool de produção em `asgi.py`, `threading.Lock` em `DependencyProbe.from_settings`, e `run_in_threadpool` em `/readyz`.
- **Modify:** `src/agentos/provider_catalog/service.py` & `src/agentos/provider_catalog/opencode.py` — guarda `not items` + `_auto_refresh_backoff` (`60s`) em `list()`, e término imediato de `opencode serve` órfão em timeout de `ensure_running`.
- **Modify:** `src/agentos/api/gateway.py` & `src/agentos/api/settings_routes.py` — despacho completo para `run_in_threadpool` de rotas síncronas, `principal_for`/`check_rate_limit`, `conversation_workspace`/`conversation_root`/`folder_of`, `with exclusive_folder(...)` em rewind e pré-checagens de SSE.
- **Modify:** `src/agentos/persistence/postgres/schema.py` & **Create:** `src/agentos/persistence/postgres/migrations/versions/0061_activity_events_overview_index.py` — índice idempotente `ix_conversation_activity_conv_type_id` (`down_revision = "0060_discord_bridge_and_session_watches"`).
- **Modify:** `src/agentos/conversations/chat.py` — `SELECT count(*)` + filtro SQL em `overview()`, `prune_transient_activity_events()` preservando `tool.started`, bump de `conversations.c.updated_at` em eventos interativos de aprovação, e `expire_abandoned_paused_turns()` canônico.
- **Modify:** `src/agentos/workers/scheduler.py` — execução periódica de `prune_transient_activity_events()` (`60s` quando lote cheio, `6h` ocioso).
- **Modify:** `src/agentos/retrieval/embeddings/ollama.py` — guarda `if not texts: return []`, timeout `connect=3.0` e *cooldown* (`300s` em `404`, `60s` em `5xx`/`HTTPError`).
- **Modify:** `frontend/src/components/OrinActivityCenter.tsx` — cache restrito a sessões estacionadas em `'waiting_user'` (invalidação em resolve), concorrência `<= 2` e `AbortSignal` em `proxyClient.request`.
- **Modify:** `frontend/src/features/projects/WorkspaceNavigation.tsx` — polling de `3s` preservando os 5 estados ativos (`queued`, `starting`, `running`, `streaming`, `cancelling`) e `inFlightRef` + `pendingReloadRef`.
- **Modify:** `frontend/src/features/overview/OverviewPanel.tsx` — remoção de `liveEvents.length` do `useEffect` do intervalo, guarda `inFlightRef` e *throttle* de `1500ms` para eventos estruturais reais.
- **Modify:** `src/agentos/launcher/processes.py` & `src/agentos/hooks/processes.py` — tipagem Win32 64-bit (`Affinity` `c_size_t`), `ProcessGroup(allow_breakaway: bool = True)` e snapshot de descendentes em `adopt()`/`assign()`.
- **Modify:** `src/agentos/code_mode/preview_server.py` — `ProcessGroup(allow_breakaway=False)` em memória e `process_created` + `kill_verified` (`agentos.process_identity`) no manifesto.
- **Modify:** `src/agentos/agentic/tools/_shared.py`, `src/agentos/agentic/tools/shell/bash.py`, `src/agentos/agentic/tools/shell/tasks.py`, `src/agentos/agentic/native_turn.py` — registro de `_WindowsJob(kill_on_close=False)` para `background=True` e auto-background, `_DEV_SERVER_CMD_RE` seguro vs `_EXPLICIT_BG_SERVER_RE` em `ShellTaskTools._is_long_lived`, e fechamento antecipado de `handle` CLI não-persistente antes de `wait_for_background_work`.
- **Modify:** `src/agentos/code_mode/workspace_lock.py`, `src/agentos/workers/chat.py`, `src/agentos/workers/publisher.py` — validação anti-reuso de PID via `process_created`/`same_process`, `sweep_stale_workspace_locks()` restrito a `*.turn.*`/`*.rewind`, e chamada de `expire_abandoned_paused_turns()` em `ChatWorker.recover_stale`.
- **Create Tests:**
  - `tests/unit/test_redaction_linear.py`
  - `tests/unit/discord/test_bridge_markdown_and_rate_limit.py`
  - `tests/unit/launcher/test_supervisor_watchdog_and_jobs.py`
  - `tests/unit/api/test_gateway_nonblocking_and_overview.py`
  - `tests/unit/code_mode/test_workspace_lock_and_preview_tree.py`
  - `frontend/tests/unit/OverviewPanelPolling.test.tsx`

---

### Task 1: Parsers Lineares $O(N)$ em `redaction.py`, `formatter.py` e `bridge_service.py`

**Files:**
- Modify: `src/agentos/redaction.py:17-52`
- Modify: `src/agentos/discord/formatter.py:56-142`
- Modify: `src/agentos/discord/bridge_service.py:83-98, 2008-2036`
- Create: `tests/unit/test_redaction_linear.py`

**Interfaces:**
- Produces:
  - `iter_markdown_target_spans(text: str, *, include_nested: bool = True) -> Iterator[tuple[int, int, bool, str, str]]` e `strip_attached_workspace_links(...)` em `src/agentos/discord/formatter.py` em tempo $O(N)$ estrito.
  - `_iter_markdown_targets(text: str) -> Iterator[tuple[bool, str, str]]` e `_iter_plain_media_paths(text: str) -> Iterator[str]` em `src/agentos/discord/bridge_service.py` em tempo $O(N)$ estrito.
  - `redact_text(value: str) -> str` em `src/agentos/redaction.py` com quantificadores possessivos $O(N)$ e zero regressão de vazamento de segredo.

- [ ] **Step 1: Write the failing adversarial tests**

```python
# tests/unit/test_redaction_linear.py
from __future__ import annotations

import time

import pytest

from agentos.discord.bridge_service import (
    DiscordBridgeService,
    _iter_markdown_targets,
    _iter_plain_media_paths,
)
from agentos.discord.formatter import iter_markdown_target_spans, strip_attached_workspace_links
from agentos.redaction import REDACTED, redact_text, redact_value


def test_markdown_and_media_scanners_linear_on_adversarial_inputs() -> None:
    # 1. Exact unclosed mid-stream pattern that froze live backend PID 30912
    midstream = "[caption.txt](workspace://instagram/studio/renders/2026-11-15/reel-01" + ("a" * 50000)
    t0 = time.perf_counter()
    assert list(_iter_markdown_targets(midstream)) == []
    assert (time.perf_counter() - t0) < 0.05

    # 2. Repeated unclosed links, images, angles, quotes, and plain media tokens
    adversarial_cases = [
        ("[x](" + ("b" * 120) + " ") * 200,
        "![a](" * 10000,
        "[a](workspace://" * 5000,
        "[bad](<http://x " * 5000,
        '[bad](http://x "unclosed ' * 5000,
    ]
    for payload in adversarial_cases:
        t0 = time.perf_counter()
        assert list(iter_markdown_target_spans(payload)) == []
        stripped = strip_attached_workspace_links(payload, ["workspace://shot.png"])
        elapsed = time.perf_counter() - t0
        assert isinstance(stripped, str)
        assert elapsed < 0.08, f"Markdown scanner/stripper took {elapsed:.3f}s on len={len(payload)}"

    media_adversarial = "=/a" * 15000
    t0 = time.perf_counter()
    assert list(_iter_plain_media_paths(media_adversarial)) == []
    assert (time.perf_counter() - t0) < 0.05


def test_markdown_scanner_preserves_valid_links_and_all_four_edge_cases() -> None:
    sample = (
        'Veja ![preview](workspace://renders/frame_(1).png "Titulo") '
        "e [relatorio](<workspace://docs/meu arquivo.pdf>) "
        "e [site](https://example.com/a(b)c 'ok')."
    )
    assert list(_iter_markdown_targets(sample)) == [
        (True, "preview", "workspace://renders/frame_(1).png"),
        (False, "relatorio", "workspace://docs/meu arquivo.pdf"),
        (False, "site", "https://example.com/a(b)c"),
    ]

    # Edge case 1: clickable image [![alt](img)](doc) yields BOTH inner image and outer link
    clickable = "[![alt](workspace://img.png)](<workspace://doc.pdf>)"
    assert list(_iter_markdown_targets(clickable)) == [
        (True, "alt", "workspace://img.png"),
        (False, "![alt](workspace://img.png)", "workspace://doc.pdf"),
    ]

    # Edge case 2: escaped bracket inside label
    escaped = r"[a \] b](workspace://doc.pdf)"
    assert list(_iter_markdown_targets(escaped)) == [(False, r"a \] b", "workspace://doc.pdf")]

    # Edge case 3: CRLF / multiline-wrapped target
    multiline = "[doc](\r\n  <workspace://a.pdf>\r\n)"
    assert list(_iter_markdown_targets(multiline)) == [(False, "doc", "workspace://a.pdf")]

    # Edge case 4: valid link on the same line after unclosed `<`, quote, or `(`
    for prefix in ("[bad](<http://x ", '[bad](http://x "unclosed ', "[bad](http://x(y ", "[bad](http://x(y"):
        recovered = f"{prefix}![ok](workspace://shot.png)"
        assert list(_iter_markdown_targets(recovered)) == [(True, "ok", "workspace://shot.png")]

    cands = DiscordBridgeService._extract_outgoing_file_candidates({}, f"{clickable}\nscreenshots/final.png.")
    targets = [c for _, c in cands]
    assert "workspace://img.png" in targets
    assert "workspace://doc.pdf" in targets
    assert "screenshots/final.png" in targets

    cleaned = strip_attached_workspace_links(
        "Resultado: ![preview](workspace://img.png) e [Abrir](workspace://doc.pdf)",
        ["workspace://img.png", "workspace://doc.pdf"],
    )
    assert "workspace://img.png" not in cleaned
    assert "`Abrir`" in cleaned


def test_redact_text_linear_on_adversarial_inputs() -> None:
    payloads = [
        ("auth_" * 3000) + " end",
        "--token-" * 3000,
        "token-" * 8000,
        "A" * 50000,
        "-" * 50000,
    ]
    t0 = time.perf_counter()
    for p in payloads:
        assert redact_text(p) == p
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.08, f"Expected strict O(N) < 80ms across all adversarial inputs, took {elapsed:.3f}s"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("GITHUB_TOKEN=ghp_1234567890abcdefghij123456", "ghp_1234567890abcdefghij123456"),
        ('"client_secret": "my-super-secret"', "my-super-secret"),
        ('--api-key="secret123"', "secret123"),
        ("postgres://admin:s3cr3t@localhost:5432/db", "s3cr3t"),
        # 6 regression vectors where naive non-secret candidate consumption would leak secrets:
        ("curl https://api.example.com/v1?api_key=super_secret_q", "super_secret_q"),
        ('cmd="API_KEY=super_secret_env"', "super_secret_env"),
        ('--header="X-Api-Key: super_secret_hdr"', "super_secret_hdr"),
        ('sh -c "--api-key super_secret_flag"', "super_secret_flag"),
        ("env:API_KEY=super_secret_colon", "super_secret_colon"),
        ("SECRET_KEY_" + ("A" * 135) + "=super_secret_long", "super_secret_long"),
        ('{"api_key": "a\\"super_secret_esc\\"b"}', "super_secret_esc"),
        ('"api_key":\n  "super_secret_multiline"', "super_secret_multiline"),
    ],
)
def test_redact_text_zero_secret_leaks(raw: str, secret: str) -> None:
    cleaned = redact_text(raw)
    assert secret not in cleaned
    assert REDACTED in cleaned


def test_redact_text_keeps_non_secrets_and_redacts_containers() -> None:
    plain = "normal_var=visible --verbose=true https://example.com/path"
    assert redact_text(plain) == plain
    nested = redact_value({"headers": {"X-Custom": "raw"}, "safe": "ok"})
    assert nested == {"headers": {"X-Custom": REDACTED}, "safe": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/test_redaction_linear.py -v
```
Expected: FAIL (`ImportError: cannot import name '_iter_markdown_targets'`).

- [ ] **Step 3: Write minimal $O(N)$ implementation**

1. In `src/agentos/redaction.py:19-26`, replace `_ASSIGNMENT`, `_FLAG`, and `_URL_PASSWORD` with left-bounded Python 3.12+ possessive regexes (keeping `redact_text` `.sub(...)` lambdas intact):

```python
_ASSIGNMENT = re.compile(
    rf"(?i)(?P<name>(?:[\"'](?=[A-Za-z0-9_.-])|(?<![A-Za-z0-9_.-]))(?=[A-Za-z0-9_.-]*?{_SECRET_WORD})[A-Za-z0-9_.-]++[\"']?)"
    r"(?P<sep>\s*+[:=]\s*+)"
    r"(?P<value>\"(?:\\.|[^\"\\\r\n])*+\"|'(?:\\.|[^'\\\r\n])*+'|[^\s,;&|)\]}]+)"
)
_FLAG = re.compile(
    rf"(?i)(?<![A-Za-z0-9_-])(?P<flag>--?(?=[A-Za-z0-9-]*?{_SECRET_WORD})[A-Za-z0-9-]++)"
    r"(?P<sep>[= ]++)"
    r"(?P<value>\"(?:\\.|[^\"\\\r\n])*+\"|'(?:\\.|[^'\\\r\n])*+'|\S+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_PASSWORD = re.compile(r"(?i)(?<![a-z0-9+.-])([a-z][a-z0-9+.-]*+://[^/\s:@]++):[^@\s/]++@")
```

2. In `src/agentos/discord/formatter.py:56-142`, delete `_MD_IMAGE_LINK_RE` and `_MD_FILE_LINK_RE`, add `iter_markdown_target_spans` (import `Iterator` from `collections.abc` if not already imported), and rewrite `strip_attached_workspace_links` over `iter_markdown_target_spans`:

```python
def iter_markdown_target_spans(
    text: str,
    *,
    include_nested: bool = True,
    _allow_nested_bracket: bool = True,
) -> Iterator[tuple[int, int, bool, str, str]]:
    """Yield `(span_start, span_end, is_image, label, target)` in strict O(N) time without backtracking."""
    n = len(text)
    idx = 0
    unclosed_angle_until = -1
    unclosed_quote_until: dict[str, int] = {'"': -1, "'": -1}

    while idx < n:
        open_br = text.find("[", idx)
        if open_br == -1:
            break
        is_img = open_br > 0 and text[open_br - 1] == "!"
        span_start = open_br - 1 if is_img else open_br

        scan = open_br + 1
        limit = min(n, scan + 1024)
        close_br = -1
        label_depth = 0
        had_nested_bracket = False
        first_inner_bracket = -1

        while scan < limit:
            ch = text[scan]
            if ch == "\\":
                scan += 2
                continue
            if ch == "]":
                if label_depth > 0:
                    label_depth -= 1
                    scan += 1
                    continue
                close_br = scan
                break
            if ch == "[":
                if _allow_nested_bracket and not is_img and not had_nested_bracket and label_depth == 0:
                    had_nested_bracket = True
                    label_depth = 1
                    first_inner_bracket = scan
                    scan += 1
                    continue
                break
            if ch == "\n" and scan + 1 < n and text[scan + 1] == "\n":
                break
            if ch == "\r" and scan + 3 < n and text[scan : scan + 4] == "\r\n\r\n":
                break
            scan += 1

        if close_br == -1:
            if first_inner_bracket != -1:
                idx = first_inner_bracket
            elif scan < limit and scan < n and text[scan] == "[":
                idx = scan
            else:
                idx = max(open_br + 1, scan)
            continue

        if close_br + 1 >= n or text[close_br + 1] != "(":
            idx = first_inner_bracket if first_inner_bracket != -1 else close_br + 1
            continue

        label = text[open_br + 1 : close_br]
        p = close_br + 2
        while p < n and text[p] in " \t\r\n":
            p += 1
        if p >= n:
            if first_inner_bracket != -1:
                idx = first_inner_bracket
                continue
            break

        target = ""
        first_target_bracket = -1
        if text[p] == "<":
            if p < unclosed_angle_until:
                idx = first_inner_bracket if first_inner_bracket != -1 else p + 1
                continue
            end_angle = p + 1
            while end_angle < n and text[end_angle] not in ">\r\n":
                end_angle += 1
            if end_angle >= n or text[end_angle] != ">":
                unclosed_angle_until = end_angle
                idx = first_inner_bracket if first_inner_bracket != -1 else p + 1
                continue
            target = text[p + 1 : end_angle].strip()
            p = end_angle + 1
        else:
            start_plain = p
            plain_limit = min(n, start_plain + 2048)
            depth = 0
            aborted_plain = False
            while p < plain_limit:
                ch = text[p]
                if ch in " \t\r\n":
                    break
                if ch == "[" and first_target_bracket == -1:
                    first_target_bracket = p
                if ch == "(":
                    if depth >= 1:
                        aborted_plain = True
                        break
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        break
                    depth -= 1
                p += 1
            if aborted_plain or depth != 0 or (p == plain_limit and p < n and text[p] not in " \t\r\n)"):
                if first_inner_bracket != -1:
                    idx = first_inner_bracket
                elif first_target_bracket != -1:
                    idx = first_target_bracket
                else:
                    idx = p if p > close_br + 2 else close_br + 2
                continue
            target = text[start_plain:p].strip()

        while p < n and text[p] in " \t\r\n":
            p += 1

        if p < n and text[p] in "\"'":
            quote = text[p]
            quote_pos = p
            if quote_pos < unclosed_quote_until[quote]:
                idx = first_inner_bracket if first_inner_bracket != -1 else quote_pos + 1
                continue
            p += 1
            while p < n and text[p] not in (quote, "\r", "\n"):
                p += 1
            if p >= n or text[p] != quote:
                unclosed_quote_until[quote] = p
                idx = first_inner_bracket if first_inner_bracket != -1 else quote_pos + 1
                continue
            p += 1
            while p < n and text[p] in " \t\r\n":
                p += 1

        if p < n and text[p] == ")":
            span_end = p + 1
            if include_nested and had_nested_bracket:
                for inner_s, inner_e, inner_img, inner_lbl, inner_tgt in iter_markdown_target_spans(
                    label, include_nested=False, _allow_nested_bracket=False
                ):
                    yield (open_br + 1 + inner_s, open_br + 1 + inner_e, inner_img, inner_lbl, inner_tgt)
            if target:
                yield (span_start, span_end, is_img, label, target)
            idx = span_end
        else:
            if first_inner_bracket != -1:
                idx = first_inner_bracket
            elif first_target_bracket != -1:
                idx = first_target_bracket
            else:
                idx = max(close_br + 2, p)
```

In `strip_attached_workspace_links` (`src/agentos/discord/formatter.py`), replace the two `_MD_IMAGE_LINK_RE.sub` / `_MD_FILE_LINK_RE.sub` lines (`L127-128`) with a single linear span-replacement helper `_rewrite_md_segment(part)`:

```python
    def _rewrite_md_segment(segment: str, *, allow_nested: bool = True) -> str:
        out: list[str] = []
        cursor = 0
        for s_idx, e_idx, is_img, raw_label, raw_target in iter_markdown_target_spans(
            segment, include_nested=False, _allow_nested_bracket=allow_nested
        ):
            out.append(segment[cursor:s_idx])
            target = raw_target.strip()
            if is_img:
                if not _matches_attached(target):
                    out.append(segment[s_idx:e_idx])
            else:
                label_clean = (
                    _rewrite_md_segment(raw_label, allow_nested=False).strip()
                    if allow_nested and "![" in raw_label
                    else raw_label.strip()
                )
                if target.lower().startswith(("workspace://", "file://")) and _matches_attached(target):
                    raw_name = PurePosixPath(target.replace("\\", "/")).name
                    if not label_clean or label_clean == target or label_clean.startswith("workspace://"):
                        out.append(f"`{raw_name}`" if raw_name else "")
                    else:
                        out.append(f"`{label_clean}`" if not label_clean.startswith("`") else label_clean)
                elif allow_nested and label_clean != raw_label.strip():
                    out.append(f"[{label_clean}]{segment[s_idx + 1 + len(raw_label) : e_idx]}")
                else:
                    out.append(segment[s_idx:e_idx])
            cursor = e_idx
        out.append(segment[cursor:])
        return "".join(out)
```
And set `seg = _rewrite_md_segment(part)` inside the non-code loop!

3. In `src/agentos/discord/bridge_service.py`:
   - Import `iter_markdown_target_spans` from `.formatter`, delete `_MD_TARGET_RE` and `_PLAIN_ABS_OR_REL_MEDIA_PATH_RE`, and define:
```python
_PLAIN_MEDIA_TOKEN_SPLIT_RE = re.compile(r"[\s<>\"'`)\]]+")
_PLAIN_MEDIA_PREFIX_RE = re.compile(
    r"(?:^|(?<![A-Za-z0-9_:/\\.-]))"
    r"((?:[A-Za-z]:[\\/]|/(?!/)|(?:\.orin|\.playwright-mcp|generated|screenshots|uploads)[\\/]).+)$",
    re.IGNORECASE,
)


def _iter_markdown_targets(text: str) -> Iterator[tuple[bool, str, str]]:
    for _s, _e, is_img, label, target in iter_markdown_target_spans(text, include_nested=True):
        yield (is_img, label, target)


def _iter_plain_media_paths(text: str) -> Iterator[str]:
    for raw_tok in _PLAIN_MEDIA_TOKEN_SPLIT_RE.split(text):
        if not raw_tok:
            continue
        cand = raw_tok.rstrip(".,;:")
        if len(cand) > 1024 or not cand.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            continue
        m = _PLAIN_MEDIA_PREFIX_RE.search(cand)
        if m is not None:
            yield m.group(1).strip()
```
   - Update `_extract_outgoing_file_candidates` (`L2011-2035`) to iterate over `_iter_markdown_targets(non_code_body)` and `_iter_plain_media_paths(non_code_body)`.

- [ ] **Step 4: Run tests (plus existing redaction & discord formatter suites) to verify they pass**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/test_redaction*.py tests/unit/agentic/test_*redaction*.py tests/unit/discord/ -v
```
Expected: PASS in `< 2s` with all new and existing redaction/formatter tests green.

- [ ] **Step 5: Commit**

```powershell
git add src/agentos/redaction.py src/agentos/discord/formatter.py src/agentos/discord/bridge_service.py tests/unit/test_redaction_linear.py
git commit -m "fix(discord,redaction): elimina ReDoS segurando GIL em Markdown, midia e redaction"
```

---

### Task 2: Resiliência do `DiscordBridgeService` (`429`/`403` Backoff Unificado, Sem Scan Mid-Stream, Watches Zumbis, Avanço de Turno e Typing Poll)

**Files:**
- Modify: `src/agentos/discord/rest_client.py:29-45, 221-255`
- Modify: `src/agentos/discord/bridge_service.py:270-310, 462-655, 690-725, 1710-1750, 2880-2922, 3026-3075, 3314-3345`
- Create: `tests/unit/discord/test_bridge_markdown_and_rate_limit.py`

**Interfaces:**
- Consumes: `_iter_markdown_targets` and `_iter_plain_media_paths` from Task 1.
- Produces:
  - `DiscordApiError(..., retry_after: float | None = None)` in `src/agentos/discord/rest_client.py`.
  - Unified progress card backoff on `send_message` & `edit_message` (`429`, `404` msg vs `403`/`10003` channel), closure of deleted (`ApplicationNotFoundError`) & cancelled `waiting_user` watches, safe turn advancement via `_advance_watch_to_new_turn` preserving `origin`, and single SQLite read per tick in `_await_turn_with_typing`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/discord/test_bridge_markdown_and_rate_limit.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentos.api.contracts import ApplicationNotFoundError
from agentos.discord.bridge_service import DiscordBridgeService
from agentos.discord.rest_client import DiscordApiError, DiscordRestClient
from agentos.discord.store import FriendSessionWatch

CHAN_ID = "123456789012345678"
PROG_MSG_ID = "1571375025511071885"
PROMPT_MSG_ID = "1571375025511071886"


@pytest.mark.anyio
async def test_rest_client_fails_fast_when_retry_after_exceeds_sleep_cap() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "You are being rate limited.", "retry_after": 120.0})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DiscordRestClient(
        "bot-token",
        client=http_client,
        sleep_fn=fake_sleep,
    )
    try:
        with pytest.raises(DiscordApiError) as exc_info:
            await client.edit_message(CHAN_ID, PROG_MSG_ID, content="hi")
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 120.0
        # Must NOT waste 3 x 10s sleeping when retry_after > MAX_RATE_LIMIT_SLEEP_SECONDS
        assert slept == []
    finally:
        await client.aclose()
        await http_client.aclose()


@pytest.mark.anyio
async def test_watch_coordinator_backoff_zombies_and_turn_advance() -> None:
    now_ref = [datetime(2026, 11, 15, 15, 0, tzinfo=UTC)]
    discord_store = MagicMock()
    updated_calls: list[tuple[str, dict]] = []

    def record_update(watch_id: str, **kwargs):
        assert "target_turn_id" not in kwargs, "update_watch forbids target_turn_id"
        updated_calls.append((watch_id, kwargs))
        return None

    discord_store.update_watch.side_effect = record_update
    discord_store.get_config.return_value = MagicMock(
        default_notify_channel_id=CHAN_ID,
        allowed_channel_ids=(CHAN_ID,),
    )
    discord_store.list_open_watches.return_value = []

    chat_store = MagicMock()
    chat_store.get.return_value = {
        "conversation_id": "conv_run",
        "title": "Marketfy",
        "mode": "code",
        "state": "running",
        "turns": [{"turn_id": "turn_1", "state": "running"}],
        "messages": [{"role": "assistant", "content": "[partial](workspace://a/b/c"}],
        "activities": [],
    }
    rest = MagicMock()
    rest.edit_message = AsyncMock(
        side_effect=DiscordApiError("rate limit", status_code=429, retry_after=90.0)
    )
    rest.send_message = AsyncMock(
        side_effect=DiscordApiError("unknown channel", status_code=404, error_code=10003)
    )

    bridge = DiscordBridgeService(
        discord_store=discord_store,
        chat_application=chat_store,
        rest_client_factory=lambda _t: rest,
        clock=lambda: now_ref[0],
    )
    bridge._get_rest_client = lambda _u: rest  # type: ignore[method-assign]
    bridge._ensure_stop_binding = AsyncMock(return_value="bind_1")  # type: ignore[method-assign]

    watch = FriendSessionWatch(
        watch_id="fsw_rate",
        user_id="local-user",
        friend_conversation_id="friend_1",
        target_conversation_id="conv_run",
        target_turn_id="turn_1",
        origin="friend",
        discord_channel_id=CHAN_ID,
        discord_progress_message_id=PROG_MSG_ID,
        discord_prompt_message_id=None,
        status="watching",
        last_activity_seq=1,
        last_digest_hash="old_hash",
        last_prompt_fingerprint=None,
        last_patched_at=now_ref[0] - timedelta(seconds=60),
        notified_terminal=False,
        created_at=now_ref[0] - timedelta(hours=3),
        updated_at=now_ref[0] - timedelta(seconds=60),
    )

    # 1. During `running`, mid-stream artifacts must NOT be extracted
    insp = bridge._inspect_watched_session(watch)
    assert insp is not None
    assert insp["snapshot"]["artifacts"] == []

    # 2. 429 on PATCH sets >=120s backoff (card > 45min old) and updates last_patched_at
    await bridge.watch_coordinator._process_watch(watch)
    assert rest.edit_message.await_count == 1
    assert any("last_patched_at" in kw for _, kw in updated_calls)

    # Immediate next tick 2s later must respect backoff (0 new PATCH calls, 0 stop_binding calls!)
    bridge._ensure_stop_binding.reset_mock()
    now_ref[0] = now_ref[0] + timedelta(seconds=2)
    await bridge.watch_coordinator._process_watch(watch)
    assert rest.edit_message.await_count == 1
    assert bridge._ensure_stop_binding.await_count == 0

    # 3. Unknown Channel (404 code 10003) on send_message applies 300s backoff instead of 2s spam
    no_card_watch = FriendSessionWatch(**{**watch.__dict__, "watch_id": "fsw_no_chan", "discord_progress_message_id": None})
    await bridge.watch_coordinator._process_watch(no_card_watch)
    assert rest.send_message.await_count == 1
    now_ref[0] = now_ref[0] + timedelta(seconds=2)
    await bridge.watch_coordinator._process_watch(no_card_watch)
    assert rest.send_message.await_count == 1

    # 4. Cancelled turn that previously had an ask_user question MUST close as terminal
    chat_store.get.return_value = {
        "conversation_id": "conv_cancel",
        "title": "Cancelled Session",
        "mode": "code",
        "state": "cancelled",
        "turns": [{"turn_id": "turn_cancel", "state": "cancelled"}],
        "messages": [],
        "activities": [
            {
                "id": 10,
                "turn_id": "turn_cancel",
                "event_type": "turn.waiting_user",
                "summary": "Pergunta",
                "payload": {"in_turn": True, "questions": [{"question": "Continuar?"}]},
            }
        ],
    }
    cancelled_watch = FriendSessionWatch(
        watch_id="fsw_zombie",
        user_id="local-user",
        friend_conversation_id=None,
        target_conversation_id="conv_cancel",
        target_turn_id="turn_cancel",
        origin="code_auto",
        discord_channel_id=CHAN_ID,
        discord_progress_message_id=None,
        discord_prompt_message_id=PROMPT_MSG_ID,
        status="waiting_user",
        last_activity_seq=10,
        last_digest_hash=None,
        last_prompt_fingerprint="fp1",
        last_patched_at=None,
        notified_terminal=False,
        created_at=now_ref[0] - timedelta(hours=5),
        updated_at=now_ref[0] - timedelta(hours=5),
    )
    updated_calls.clear()
    await bridge.watch_coordinator._process_watch(cancelled_watch)
    assert any(
        wid == "fsw_zombie" and kw.get("status") == "cancelled" and kw.get("notified_terminal") is True
        for wid, kw in updated_calls
    )

    # 5. Deleted conversation (`ApplicationNotFoundError`) closes watch instead of looping forever
    chat_store.get.side_effect = ApplicationNotFoundError("conversation not found")
    deleted_watch = FriendSessionWatch(**{**cancelled_watch.__dict__, "watch_id": "fsw_deleted"})
    updated_calls.clear()
    await bridge.watch_coordinator._process_watch(deleted_watch)
    assert any(
        wid == "fsw_deleted" and kw.get("status") == "cancelled" and kw.get("notified_terminal") is True
        for wid, kw in updated_calls
    )

    # 6. Advancing to a newer active turn calls `_advance_watch_to_new_turn` preserving `origin="friend"`
    chat_store.get.side_effect = None
    chat_store.get.return_value = {
        "conversation_id": "conv_run",
        "title": "Marketfy",
        "mode": "code",
        "state": "running",
        "turns": [
            {"turn_id": "turn_1", "state": "cancelled"},
            {"turn_id": "turn_2", "state": "running"},
        ],
        "messages": [],
        "activities": [],
    }
    discord_store.list_open_watches.return_value = [watch]
    discord_store.upsert_watch.return_value = FriendSessionWatch(**{**watch.__dict__, "target_turn_id": "turn_2"})
    await bridge.watch_coordinator._process_watch(watch)
    discord_store.upsert_watch.assert_called_once()
    assert discord_store.upsert_watch.call_args.kwargs["origin"] == "friend"

    # 7. `_inspect_conversation_by_id(..., conv=...)` performs 0 extra `chat_store.get` calls
    chat_store.get.reset_mock()
    snap = bridge._inspect_conversation_by_id("local-user", "conv_run", conv=chat_store.get.return_value)
    assert snap["conversation_id"] == "conv_run"
    assert chat_store.get.call_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/discord/test_bridge_markdown_and_rate_limit.py -v
```
Expected: FAIL (`TypeError: DiscordApiError.__init__() got an unexpected keyword argument 'retry_after'`).

- [ ] **Step 3: Write minimal implementation**

1. In `src/agentos/discord/rest_client.py`:
   - Add `retry_after: float | None = None` to `DiscordApiError.__init__` and store `self.retry_after = float(retry_after) if retry_after is not None else None`.
   - In `_request()` (`L221-234`): extract `retry_after = self._extract_retry_after(response, parsed)` whenever `response.status_code == 429`. Only sleep and retry when `attempt < MAX_RATE_LIMIT_RETRIES and retry_after <= MAX_RATE_LIMIT_SLEEP_SECONDS`; otherwise raise `DiscordApiError(..., status_code=429, error_code=err_code, response_body=parsed, retry_after=retry_after)` immediately.
2. In `src/agentos/discord/bridge_service.py`:
   - Import `ApplicationNotFoundError` from `agentos.api.contracts` (if not already imported).
   - In `SessionWatchCoordinator.__init__`: add `self._patch_backoff_until: dict[str, float] = {}` and `self._patch_failures: dict[str, int] = {}`, plus helper `_record_progress_api_error(self, watch: FriendSessionWatch, err: Exception, now: datetime, progress_msg_id: str | None, watch_updates: dict[str, Any]) -> None`:
     - Compute `now_ts = now.timestamp()`.
     - If `isinstance(err, DiscordApiError)` and `err.status_code == 404` and `err.error_code != 10003`: set `watch_updates["discord_progress_message_id"] = None`, `watch_updates["last_patched_at"] = now`, and `self._patch_backoff_until[watch.watch_id] = now_ts + 15.0`.
     - Else if `isinstance(err, DiscordApiError)` and (`err.status_code == 403` or `err.error_code == 10003`): set `self._patch_backoff_until[watch.watch_id] = now_ts + 300.0` and `watch_updates["last_patched_at"] = now`.
     - Else: increment `failures = self._patch_failures.get(watch.watch_id, 0) + 1`; `self._patch_failures[watch.watch_id] = failures`; compute `retry_hint = getattr(err, "retry_after", None) or 15.0`; `cooldown = max(float(retry_hint), min(300.0, 15.0 * (2 ** (failures - 1))))`. Check card age (via Snowflake `((int(progress_msg_id) >> 22) + 1420070400000) / 1000.0` if `progress_msg_id and progress_msg_id.isdigit()` else `_as_utc(watch.created_at)`): if `isinstance(err, DiscordApiError) and err.status_code == 429` and card age `> 2700.0`, `cooldown = max(cooldown, 120.0)`. Store `self._patch_backoff_until[watch.watch_id] = now_ts + cooldown` and `watch_updates["last_patched_at"] = now`.
   - In `_inspect_watched_session(self, watch: FriendSessionWatch, *, conv: Mapping[str, Any] | None = None) -> dict[str, Any] | None` (`L3052`):
     - When `conv is None`, call `self.chat_store.get(watch.target_conversation_id, watch.user_id)`: catch `ApplicationNotFoundError` explicitly and return `{"missing_conversation": True}`; catch generic `Exception` and return `None`.
     - If `target_turn` is in `_TERMINAL_TURN_STATES` and `turns` has a newer `turns[-1]` whose state is in `_ACTIVE_TURN_STATES`, set `target_turn = turns[-1]`.
     - At `L3314`: if `turn_state in _TERMINAL_TURN_STATES`, force `pending_block = None`.
     - At `L3334`: only call `_extract_outgoing_file_candidates` when `effective_state in {"completed", "completed_with_caveats", "failed", "cancelled", "waiting_user"}` (otherwise `[]`).
   - In `_inspect_conversation_by_id(self, user_id: str, conversation_id: str, *, conv: Mapping[str, Any] | None = None)` (`L3026`): accept optional `conv` and forward `conv=conv` to `self._inspect_watched_session(watch_stub, conv=conv)`. In `_await_turn_with_typing` (`L1728`), pass `conv=conv` to `_inspect_conversation_by_id` and change `turn_poll_interval_seconds: float = 0.45` (`L693`), cutting SQLite reads from `3` per tick down to `1`.
   - In `SessionWatchCoordinator._process_watch(self, watch: FriendSessionWatch)` (`L462`):
     - If `inspection.get("missing_conversation")`: clean `_activity_seq_cursors`, `_patch_backoff_until`, `_patch_failures`, call `await asyncio.to_thread(self._bridge.discord_store.update_watch, watch.watch_id, status="cancelled", notified_terminal=True)` and `return`.
     - After resolving `channel_id` (`L481`), if `current_turn_id and current_turn_id != watch.target_turn_id`: call `await asyncio.to_thread(self._bridge._advance_watch_to_new_turn, user_id=watch.user_id, target_conversation_id=watch.target_conversation_id, new_turn_id=current_turn_id, channel_id=channel_id)` and `return` (and in `_advance_watch_to_new_turn` at `L2884, L2911`, preserve `preserved_origin = matching[0].origin if matching else "discord"` instead of hardcoding `"code_auto" if preserve_code_auto else "discord"`!).
     - In the non-terminal progress card branch (`L542-596`): check `in_backoff = now.timestamp() < self._patch_backoff_until.get(watch.watch_id, 0.0)` and determine whether `will_send` (`not progress_msg_id and not in_backoff`) or `will_edit` (`bool(progress_msg_id) and digest_hash != watch.last_digest_hash and ((entering_waiting and not in_backoff) or (not in_backoff and elapsed >= self._debounce_seconds))`). Only call `_ensure_stop_binding` when `will_send or will_edit`! On success of `send_message` or `edit_message`, pop `_patch_backoff_until` and `_patch_failures`; on `Exception as err`, call `self._record_progress_api_error(watch, err, now, progress_msg_id, watch_updates)`.
     - In `is_terminal and not watch.notified_terminal` (`L631`): also pop `self._patch_backoff_until.pop(watch.watch_id, None)` and `self._patch_failures.pop(watch.watch_id, None)`.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/discord/ tests/integration/discord/ -v
```
Expected: PASS (all new and existing unit + integration Discord tests green).

- [ ] **Step 5: Commit**

```powershell
git add src/agentos/discord/rest_client.py src/agentos/discord/bridge_service.py tests/unit/discord/test_bridge_markdown_and_rate_limit.py
git commit -m "fix(discord): aplica backoff unificado em 429/403, evita scan mid-stream e encerra watches zumbis"
```

---

### Task 3: Watchdog de `/healthz`, *Auto-Heal* In-Place no `Supervisor`, Re-Adoção do `OmniRoute` e Fix de `desktop.log`

**Files:**
- Modify: `src/agentos/launcher/desktop.py:44-62`
- Modify: `src/agentos/launcher/probes.py:107-149`
- Modify: `src/agentos/launcher/supervisor.py:45-100, 369-388, 468-495`
- Modify: `src/agentos/omniroute/process_manager.py:108-246`
- Create: `tests/unit/launcher/test_supervisor_watchdog_and_jobs.py`

**Interfaces:**
- Produces:
  - `heartbeat_probe(..., *, maximum_age=..., minimum_updated_at: datetime | None = None)` in `src/agentos/launcher/probes.py`.
  - `Supervisor._restart_child(service: str, reason: str) -> bool` replacing `self.children[idx]` *in-place*, advancing `_backend_next_health_check` every `5.0s`, and honoring clean stop (`exit_code = 0`).
  - Owned OmniRoute persistence via `omniroute-owner.json` (`process_created` / `same_process` / `kill_verified`) in `OmniRouteProcessManager`.
  - Leak-free `launch_desktop()` closing parent's `desktop.log` handle immediately after `Popen`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/launcher/test_supervisor_watchdog_and_jobs.py
from __future__ import annotations

import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentos.installation import OrinPaths, RuntimeProfile
from agentos.launcher.desktop import launch_desktop
from agentos.launcher.desktop_status import DesktopStatusWriter
from agentos.launcher.ports import PortChoice
from agentos.launcher.probes import ProbeResult, heartbeat_probe
from agentos.launcher.supervisor import LaunchOptions, Supervisor
from agentos.launcher.ui import Console
from agentos.omniroute.process_manager import OmniRouteProcessManager
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.schema import runtime_heartbeats
from agentos.persistence.sqlite import create_local_engine
from sqlalchemy import insert


def _make_paths_and_profile(tmp_path: Path) -> tuple[OrinPaths, RuntimeProfile]:
    paths = OrinPaths(
        tmp_path / "config",
        tmp_path / "data",
        tmp_path / "logs",
        tmp_path / "cache",
        tmp_path / "run",
    ).ensure()
    (tmp_path / "desktop").mkdir(exist_ok=True)
    profile = RuntimeProfile(kind="development", root=tmp_path, repository=tmp_path, version="0.12.28")
    return paths, profile


def test_launch_desktop_closes_parent_log_handle(tmp_path: Path) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    exe = tmp_path / "OrinFakeElectron.exe"
    exe.write_bytes(b"MZ")
    status = DesktopStatusWriter(paths, restart_command=("orin",))
    opened_files: list[object] = []
    real_open = open

    def tracking_open(*args, **kwargs):
        fh = real_open(*args, **kwargs)
        opened_files.append(fh)
        return fh

    fake_proc = MagicMock()
    with (
        patch.dict("os.environ", {"ORIN_ELECTRON_EXECUTABLE": str(exe)}),
        patch("builtins.open", side_effect=tracking_open),
        patch("subprocess.Popen", return_value=fake_proc),
    ):
        launch_desktop(paths, profile, status)

    assert len(opened_files) == 1
    assert opened_files[0].closed is True  # type: ignore[attr-defined]


def test_supervisor_restarts_hung_backend_in_place_preserving_shutdown_order(tmp_path: Path) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    console = Console(io.StringIO(), quiet=True, colour=False)
    sup = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    sup.choice = PortChoice(port=49200, message=None)
    sup.environment = MagicMock()
    sup.environment.for_port.return_value = {}
    sup.environment.database_url = f"sqlite:///{tmp_path / 'test.db'}"

    old_backend = MagicMock(name="old_backend")
    old_backend.name = "backend"
    old_backend.pid = 1111
    old_backend.exited.return_value = None

    worker = MagicMock(name="worker")
    worker.name = "worker"
    worker.pid = 2222
    worker.exited.return_value = None

    scheduler = MagicMock(name="scheduler")
    scheduler.name = "scheduler"
    scheduler.pid = 2223
    scheduler.exited.return_value = None

    new_backend = MagicMock(name="new_backend")
    new_backend.name = "backend"
    new_backend.pid = 3333
    new_backend.exited.return_value = None

    sup.children = [old_backend, worker, scheduler]

    def fake_spawn(svc: str, *, replace_at: int | None = None):
        assert svc == "backend"
        assert replace_at == 0
        sup.children[replace_at] = new_backend
        return new_backend

    with (
        patch.object(sup, "_spawn", side_effect=fake_spawn),
        patch("agentos.launcher.supervisor.http_probe", return_value=ProbeResult(True, "ok")),
    ):
        ok = sup._restart_child("backend", "healthz timed out 3 times")

    assert ok is True
    old_backend.stop.assert_called_once()
    worker.stop.assert_not_called()
    scheduler.stop.assert_not_called()
    # Order MUST remain [backend, worker, scheduler] so _stop_children stops backend LAST!
    assert [c.name for c in sup.children] == ["backend", "worker", "scheduler"]
    assert sup.children[0] is new_backend


def test_supervisor_circuit_breaker_trips_on_fourth_attempt_and_heartbeat_requires_fresh_timestamp(tmp_path: Path) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    console = Console(io.StringIO(), quiet=True, colour=False)
    sup = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    sup.choice = PortChoice(port=49200, message=None)
    dsn = f"sqlite:///{tmp_path / 'hb.db'}"
    eng = create_local_engine(dsn)
    upgrade(eng)
    stale_ts = datetime.now(UTC) - timedelta(seconds=2)
    with eng.begin() as c:
        c.execute(
            insert(runtime_heartbeats).values(
                component="chat-worker",
                instance_id="dead-worker",
                status="ok",
                updated_at=stale_ts,
            )
        )
    eng.dispose()

    # Stale heartbeat from 2s BEFORE restart must fail minimum_updated_at check!
    restart_ts = datetime.now(UTC)
    assert heartbeat_probe(dsn, ("chat-worker",), minimum_updated_at=restart_ts).ok is False

    sup.environment = MagicMock()
    sup.environment.for_port.return_value = {}
    sup.environment.database_url = dsn

    def make_child() -> MagicMock:
        c = MagicMock()
        c.name = "backend"
        c.exited.return_value = None
        return c

    sup.children = [make_child()]

    def replace_spawn(_s: str, *, replace_at: int | None = None):
        c = make_child()
        if replace_at is not None:
            sup.children[replace_at] = c
        else:
            sup.children.append(c)
        return c

    with (
        patch.object(sup, "_spawn", side_effect=replace_spawn),
        patch("agentos.launcher.supervisor.http_probe", return_value=ProbeResult(True, "ok")),
    ):
        assert sup._restart_child("backend", "hang 1") is True
        assert sup._restart_child("backend", "hang 2") is True
        assert sup._restart_child("backend", "hang 3") is True
        # 4th attempt inside 120s window trips circuit breaker
        assert sup._restart_child("backend", "hang 4") is False


def test_omniroute_readopts_owned_process_across_backend_restart(tmp_path: Path) -> None:
    owner_file = tmp_path / "omniroute-owner.json"
    settings = MagicMock()
    fake_proc = MagicMock()
    fake_proc.pid = os.getpid()
    fake_proc.poll.return_value = None

    mgr1 = OmniRouteProcessManager(
        settings,
        base_url="http://127.0.0.1:20128/v1",
        command=("omniroute",),
        health=MagicMock(side_effect=[False, True, True]),
        start_process=lambda _cmd: fake_proc,
        wait_ready=lambda: True,
        owner_file=owner_file,
    )
    assert mgr1.start() == {"state": "ready", "ownership": "agentos"}
    assert owner_file.is_file()

    # Simulate new backend instance after hung-backend kill (no in-memory _process handle)
    mgr2 = OmniRouteProcessManager(
        settings,
        base_url="http://127.0.0.1:20128/v1",
        command=("omniroute",),
        health=lambda: True,
        owner_file=owner_file,
    )
    assert mgr2.status() == {"state": "ready", "ownership": "agentos"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/launcher/test_supervisor_watchdog_and_jobs.py -v
```
Expected: FAIL (`opened_files[0].closed is False` / `TypeError: heartbeat_probe() got an unexpected keyword argument 'minimum_updated_at'` / `AttributeError: 'Supervisor' object has no attribute '_restart_child'`).

- [ ] **Step 3: Write minimal implementation**

1. In `src/agentos/launcher/desktop.py:46-61`:
```python
    log = open(log_path, "ab", buffering=0)  # noqa: SIM115 - inherited by the child on spawn
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(_desktop_working_directory(profile)),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
        )
    finally:
        log.close()
    return DesktopProcess(process, log_path)
```
2. In `src/agentos/launcher/probes.py:107-149`:
   - Add `minimum_updated_at: datetime | None = None` to `heartbeat_probe(dsn, components, *, maximum_age=timedelta(seconds=30), minimum_updated_at=None)`:
   - Normalize `min_ts = minimum_updated_at.replace(tzinfo=UTC) if minimum_updated_at and minimum_updated_at.tzinfo is None else minimum_updated_at`. If `min_ts is not None and updated < min_ts`, append `f"{component} has not reported since restart"` to `missing`.
3. In `src/agentos/launcher/supervisor.py`:
   - Add constants `BACKEND_WATCHDOG_INTERVAL = 5.0`, `BACKEND_WATCHDOG_TIMEOUT = 3.0`, `BACKEND_WATCHDOG_FAILURE_THRESHOLD = 3`, `SERVICE_RESTART_MAX_ATTEMPTS = 3`, `SERVICE_RESTART_WINDOW = 120.0`.
   - Add fields `_backend_next_health_check: float = field(default=0.0, init=False)`, `_backend_health_failures: int = field(default=0, init=False)`, `_restart_history: dict[str, list[float]] = field(default_factory=dict, init=False)`.
   - Update `_spawn(self, service: str, *, replace_at: int | None = None) -> SupervisedProcess` (`L369-384`): when `replace_at is not None`, set `self.children[replace_at] = child` instead of `self.children.append(child)`.
   - Implement `_restart_child(self, service: str, reason: str) -> bool`:
     - Return `False` immediately if `self._stop.is_set() or stop_requested(self.paths)`.
     - Prune `history = self._restart_history.setdefault(service, [])` to `[t for t in history if t >= now - SERVICE_RESTART_WINDOW]`; if `len(history) >= SERVICE_RESTART_MAX_ATTEMPTS`, log error and return `False`; append `now`.
     - Find `(idx, old)` in `enumerate(self.children)` matching `old.name == service`; call `old.stop()`.
     - Record `restart_started_at = datetime.now(UTC)` BEFORE calling `new_child = self._spawn(service, replace_at=idx)`.
     - For `service == "backend"`: wait via `wait_until(lambda: http_probe(f"{self.base_url}/healthz", timeout=2.0), timeout=BACKEND_READY_TIMEOUT, abort=lambda: self._startup_abort(new_child))` and `wait_until(lambda: http_probe(f"{self.base_url}/readyz", timeout=3.0), timeout=BACKEND_READY_TIMEOUT, abort=lambda: self._startup_abort(new_child))`, resetting `self._backend_health_failures = 0` and `self._backend_next_health_check = monotonic() + BACKEND_WATCHDOG_INTERVAL`.
     - For `service in {"worker", "scheduler"}`: `components = ("chat-worker",) if service == "worker" else ("scheduled-chat-worker",)` and wait via `wait_until(lambda: heartbeat_probe(self.environment.database_url, components, minimum_updated_at=restart_started_at), timeout=WORKERS_READY_TIMEOUT, abort=lambda: self._startup_abort(new_child))`.
     - Return `bool(ready)`.
   - In `_supervise()` (`L468-494`):
     - Initialize `self._backend_next_health_check = monotonic() + BACKEND_WATCHDOG_INTERVAL` and `self._backend_health_failures = 0` right before the `while not self._stop.is_set():` loop.
     - When `dead` is detected: if `not self._restart_child(...)`: check `if self._stop.is_set() or stop_requested(self.paths): break` (`exit_code = 0`) BEFORE calling `_desktop_failed(..., exit_code = 1)`!
     - When `now >= self._backend_next_health_check`: **immediately** set `self._backend_next_health_check = now + BACKEND_WATCHDOG_INTERVAL` (so probes always run every `5.0s`, never `0.5s`), then run `http_probe(f"{self.base_url}/healthz", timeout=BACKEND_WATCHDOG_TIMEOUT)`. Reset `_backend_health_failures = 0` on `ok`; otherwise increment and, when `>= BACKEND_WATCHDOG_FAILURE_THRESHOLD`, call `_restart_child("backend", ...)` (again checking `stop_requested` / `_stop` before `_desktop_failed` if it returns `False`).
4. In `src/agentos/omniroute/process_manager.py`:
   - Add optional `owner_file: Path | None = None` to `OmniRouteProcessManager.__init__` (defaulting lazily to `orin_paths().run / "omniroute-owner.json"`).
   - Write `{"pid": proc.pid, "created": process_created(proc.pid)}` to `owner_file` whenever `self._start_process` succeeds; unlink `owner_file` inside `_terminate_owned()`.
   - In `_owned_alive()`: return `True` if `self._process is not None and self._process.poll() is None`, OR if `owner_file` contains `{pid, created}` where `same_process(int(pid), created)` is `True` (and in `_terminate_owned()`, if `self._process is None` and `owner_file` had a live owned process, terminate it via `kill_verified(int(pid), created)`!).

- [ ] **Step 4: Run tests to verify they pass**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/launcher/ tests/unit/omniroute/ -v
```
*(Se `tests/unit/omniroute/` não existir, rode `tests/unit/launcher/ -v`.)*
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/agentos/launcher/desktop.py src/agentos/launcher/probes.py src/agentos/launcher/supervisor.py src/agentos/omniroute/process_manager.py tests/unit/launcher/test_supervisor_watchdog_and_jobs.py
git commit -m "feat(launcher,omniroute): adiciona watchdog /healthz in-place, re-adocao do OmniRoute e fecha desktop.log"
```

---

### Task 4: Desbloqueio Completo do Event Loop (`gateway.py`, `settings_routes.py`, `production.py`, `asgi.py`, `provider_catalog`, `omniroute`)

**Files:**
- Modify: `src/agentos/bootstrap/production.py:146-171, 410-415`
- Modify: `src/agentos/api/asgi.py:1-17`
- Modify: `src/agentos/provider_catalog/service.py:175-210, 339-383`
- Modify: `src/agentos/provider_catalog/opencode.py:190-216`
- Modify: `src/agentos/omniroute/process_manager.py:177-223`
- Modify: `src/agentos/api/gateway.py` & `src/agentos/api/settings_routes.py:480-520`
- Create: `tests/unit/api/test_gateway_nonblocking_and_overview.py`

**Interfaces:**
- Produces:
  - `DependencyProbe.from_engine(cls, engine: Engine)` (wired in `asgi.py`) + lock-protected `DependencyProbe.from_settings` + async `/readyz` via `run_in_threadpool`.
  - `60s` failure backoff `_auto_refresh_backoff` in `ProviderModelCatalogService.list` + skipped refresh when `opencode` already has `items` + orphan `opencode serve` cleanup on startup timeout.
  - Lock-free HTTP health checks/waits in `OmniRouteProcessManager.status` / `start`.
  - Complete threadpool offloading of sync DB/disk/lock/auth/SSE-precheck operations in `gateway.py` and `settings_routes.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/api/test_gateway_nonblocking_and_overview.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentos.bootstrap.production import DependencyProbe, ProductionSettings
from agentos.provider_catalog.models import ProviderCatalogContext, ProviderModelRecord
from agentos.provider_catalog.service import ProviderModelCatalogService


def test_dependency_probe_reuses_single_engine_thread_safely_and_supports_from_engine(tmp_path: Path) -> None:
    db_path = tmp_path / "ready.db"
    settings = ProductionSettings(
        AGENTOS_ENV="development",
        DATABASE_URL=f"sqlite:///{db_path}",
        LOCALHOST_TRUST_ENABLED=False,
    )
    created_engines: list[object] = []
    from agentos.persistence import sqlite as sqlite_mod

    real_create = sqlite_mod.create_local_engine

    def counting_create(*args, **kwargs):
        eng = real_create(*args, **kwargs)
        created_engines.append(eng)
        return eng

    with patch("agentos.bootstrap.production.create_local_engine", side_effect=counting_create):
        probe = DependencyProbe.from_settings(settings)
        assert probe.ready() is True
        assert probe.ready() is True
        assert probe.ready() is True

    assert len(created_engines) == 1
    direct_probe = DependencyProbe.from_engine(created_engines[0])  # type: ignore[arg-type]
    assert direct_probe.ready() is True
    created_engines[0].dispose()  # type: ignore[attr-defined]


def test_opencode_list_skips_refresh_when_populated_and_applies_backoff_when_offline() -> None:
    repo = MagicMock()
    existing = [
        ProviderModelRecord(
            provider="opencode",
            model_id="opencode/glm-5",
            display_name="GLM 5",
            context_window=128000,
            capabilities=("tools",),
            input_modalities=("text",),
            output_modalities=("text",),
            pricing=None,
            refreshed_at=datetime.now(UTC),
            is_favorite=False,
        )
    ]
    repo.list.return_value = existing
    repo.credential.return_value = {"enabled": True, "base_url": "", "api_key": ""}

    service = ProviderModelCatalogService(repository=repo, clients={})
    service.refresh = MagicMock()  # type: ignore[method-assign]

    ctx = ProviderCatalogContext(user_id="local-user", purpose="provider.catalog.list")
    result = service.list(ctx, "opencode")
    assert result == existing
    service.refresh.assert_not_called()

    # Empty catalog + failing daemon must apply 60s failure cooldown instead of retrying on every list()
    repo.list.return_value = []
    service.refresh.side_effect = RuntimeError("opencode offline")
    assert service.list(ctx, "opencode") == []
    assert service.refresh.call_count == 1
    assert service.list(ctx, "opencode") == []
    assert service.refresh.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/api/test_gateway_nonblocking_and_overview.py -v
```
Expected: FAIL (`AttributeError: type object 'DependencyProbe' has no attribute 'from_engine'` and `service.refresh` called unexpectedly).

- [ ] **Step 3: Write minimal implementation**

1. In `src/agentos/bootstrap/production.py` & `src/agentos/api/asgi.py`:
   - Add `@classmethod def from_engine(cls, engine: Engine) -> DependencyProbe:` executing `SELECT 1` on `engine.connect()`.
   - In `DependencyProbe.from_settings(cls, settings)`: protect lazy `shared_engine: Engine | None = None` with `lock = threading.Lock()` (creating once under `lock`, running `SELECT 1`, and on exception disposing & resetting `shared_engine = None` under `lock`).
   - In `readyz()` (`production.py:412`): `if await run_in_threadpool(dependency_probe.ready):`.
   - In `src/agentos/api/asgi.py:8`: pass `probe=DependencyProbe.from_engine(engine)` to `create_production_app(...)`.
2. In `src/agentos/provider_catalog/service.py` & `src/agentos/provider_catalog/opencode.py`:
   - Add `self._auto_refresh_backoff: dict[tuple[str, str], float] = {}` (`AUTO_REFRESH_BACKOFF_SECONDS = 60.0`) on `ProviderModelCatalogService.__init__`; clear `self._auto_refresh_backoff.pop((context.user_id, normalized_provider), None)` on successful `refresh()`.
   - Helper `_try_auto_refresh(context, normalized_provider)`: if `time.monotonic() < self._auto_refresh_backoff.get((context.user_id, normalized_provider), 0.0)`, return `False`; call `self.refresh(context, normalized_provider)`, and on `Exception` record `self._auto_refresh_backoff[(context.user_id, normalized_provider)] = time.monotonic() + 60.0` and return `False`.
   - In `list()` (`L346-382`): use `_try_auto_refresh` for `anthropic` CLI bridge, `openai-codex`/`google-antigravity`, and `opencode`, and trigger `opencode` auto-refresh **only when `not items and not favorites_only`** (`if normalized_provider == "opencode" and not items and not favorites_only:`).
   - In `src/agentos/provider_catalog/opencode.py:208-215` (`ensure_running`): if the readiness loop times out, pop `self._processes.pop(port, None)` and terminate/kill `proc` (`_terminate_process_tree(proc)`) before raising `RuntimeError`, preventing leaked `opencode serve` daemons.
3. In `src/agentos/omniroute/process_manager.py:177-223`:
   - Release `self._lock` during network health probes: in `status()`, compute `healthy = self._healthy()` outside `self._lock`; in `start()`, check `_healthy()` before locking to spawn, release `self._lock` during `self._wait_ready()`, then re-acquire `self._lock` to update `_last_failure` / `_terminate_owned`.
4. In `src/agentos/api/gateway.py` & `src/agentos/api/settings_routes.py`:
   - Offload `principal_for` + `services.security.check_rate_limit` via an async wrapper (`await run_in_threadpool(...)`) or run the entire sync route body inside a single sync closure passed to `await run_in_threadpool(...)`.
   - Offload sync workspace/DB helpers called inside `async def` routes: `conversation_record`, `conversation_workspace` (`gateway.py:1331`), `conversation_root` (`L2988`), and `folder_of` (`settings_routes.py:481`).
   - In `rewind_conversation_turn` (`L1828`) and `rewind_conversation_message` (`L1858`): move the **entire** `with exclusive_folder(workspace.root):` block (including `_turn_in_flight(conversation_record(...))`, `store.rewind_turn` / `store.rewind_message`, and `history.rewind`) inside a single sync function executed via `await run_in_threadpool(...)` — **never** hold `exclusive_folder` across `await`.
   - Offload SSE pre-stream sync DB reads via `run_in_threadpool` while keeping `StreamingResponse` on the event loop: `conversation_record` in `conversation_events` (`L1940`), and `services.events.read` + `delivery_permitted` in `sse` (`L2972-2979`).
   - Wrap all remaining sync handlers in `run_in_threadpool`: `create_upload` (`L729`), `delete_upload` (`L739`), `create_conversation` (`L790`), `list_projects` (`L1003`), `project_sidebar` (`L1009`), `get_project` (`L1015`), `list_conversations`, `get_conversation` (`L1255`), `get_conversation_older_activities` (`L1262`), `send_conversation_message` (`L1482`), `cancel_conversation` (`L1528`), `delete_conversation`, `conversation_overview` (`L1927`), `omniroute_installation_status` (`L2629`), `omniroute_runtime_status` (`L2642`), `update_omniroute_runtime` (`L2649`), `control_omniroute_runtime` (`L2656`), `get_agent_runtime_quality` (`L2721`), `refresh_provider_models` (`L2884`), `list_provider_models` (`L2894`), `add_provider_custom_model` (`L2905`), `remove_provider_custom_model` (`L2923`), `favorite_provider_model` (`L2935`), `unfavorite_provider_model` (`L2939`).

- [ ] **Step 4: Run tests to verify they pass**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/api/test_gateway_nonblocking_and_overview.py tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py tests/unit/provider_catalog/ -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/agentos/bootstrap/production.py src/agentos/api/asgi.py src/agentos/provider_catalog/service.py src/agentos/provider_catalog/opencode.py src/agentos/omniroute/process_manager.py src/agentos/api/gateway.py src/agentos/api/settings_routes.py tests/unit/api/test_gateway_nonblocking_and_overview.py
git commit -m "perf(api,catalog): move I/O e locks sincronos para threadpool, reusa Engine em readyz e aplica backoff em opencode"
```

---

### Task 5: SQL Indexado em `overview()` (Preservando `activity_count`), Poda em Lotes (`> 7 dias`, Preservando `tool.started`) e Cooldown no `OllamaEmbedder`

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py:895-905`
- Create: `src/agentos/persistence/postgres/migrations/versions/0061_activity_events_overview_index.py`
- Modify: `src/agentos/conversations/chat.py:3383-3470`
- Modify: `src/agentos/workers/scheduler.py:14-35`
- Modify: `src/agentos/retrieval/embeddings/ollama.py:18-48`
- Modify: `tests/unit/api/test_gateway_nonblocking_and_overview.py`

**Interfaces:**
- Produces:
  - Idempotent Alembic migration `0061_activity_events_overview_index` (`down_revision = "0060_discord_bridge_and_session_watches"`).
  - `PostgresChatStore.overview()` returning exact `"activity_count"` via `SELECT count(*)` while reading full payload rows only for `tool.finished`, `agent.message_sent`, `agent.message_received`, `turn.failed` (capping `messages`/`errors` at `200`).
  - `PostgresChatStore.prune_transient_activity_events(*, older_than_days: int = 7, batch_size: int = 2000, max_batches: int = 10) -> int` preserving `tool.started` and `tool.finished`.
  - Adaptive backlog drain in `scheduler.py` (`60s` when full batch deleted, `6h` otherwise) + cooldown-protected `OllamaEmbedder.embed()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/api/test_gateway_nonblocking_and_overview.py`:

```python
from datetime import timedelta
import httpx
from sqlalchemy import insert, select, update

from agentos.agentic.events import AgentActivityEventType
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.schema import conversation_activity_events, conversation_turns
from agentos.persistence.sqlite import create_local_engine
from agentos.retrieval.embeddings.ollama import OllamaEmbedder
from agentos.retrieval.ports import EmbeddingUnavailable


def test_overview_preserves_activity_count_and_prunes_only_strictly_transient_events(tmp_path: Path) -> None:
    dsn = f"sqlite:///{tmp_path / 'orin_prune.db'}"
    engine = create_local_engine(dsn)
    upgrade(engine)
    store = PostgresChatStore(engine)

    now = datetime.now(UTC)
    old_time = now - timedelta(days=10)

    # Create & settle an old turn (> 7 days) and a recent turn (1 hour ago) through real store methods
    r_old = store.create(
        user_id="local-user",
        message="Old heavy session",
        provider="google-antigravity",
        model_id="gemini-4-carbon-high",
        idempotency_key="idem_old_1",
    )
    cid = r_old.conversation_id
    claimed_old = store.claim(r_old.turn_id)
    assert claimed_old is not None
    store.finish(claimed_old)

    r_recent = store.create(
        user_id="local-user",
        message="Recent follow-up",
        provider="",
        model_id="",
        idempotency_key="idem_recent_1",
        conversation_id=cid,
    )
    claimed_recent = store.claim(r_recent.turn_id)
    assert claimed_recent is not None
    store.finish(claimed_recent)

    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == r_old.turn_id)
            .values(created_at=old_time, started_at=old_time, finished_at=old_time + timedelta(minutes=5))
        )
        rows = [
            {
                "event_id": f"evt_old_prog_{i}",
                "sequence": 100 + i,
                "conversation_id": cid,
                "turn_id": r_old.turn_id,
                "execution_id": str(claimed_old["execution_id"]),
                "user_id": "local-user",
                "agent_id": f"agent:{cid}:main",
                "event_type": AgentActivityEventType.TOOL_PROGRESSED.value,
                "visibility": "public",
                "summary": "streaming...",
                "payload": {"output_tail": "x" * 200},
                "created_at": old_time,
            }
            for i in range(50)
        ] + [
            {
                "event_id": "evt_old_started",
                "sequence": 200,
                "conversation_id": cid,
                "turn_id": r_old.turn_id,
                "execution_id": str(claimed_old["execution_id"]),
                "user_id": "local-user",
                "agent_id": f"agent:{cid}:main",
                "event_type": AgentActivityEventType.TOOL_STARTED.value,
                "visibility": "public",
                "summary": "running pytest",
                "payload": {"tool_name": "run_command", "command": "pytest -q"},
                "created_at": old_time,
            },
            {
                "event_id": "evt_old_fin",
                "sequence": 201,
                "conversation_id": cid,
                "turn_id": r_old.turn_id,
                "execution_id": str(claimed_old["execution_id"]),
                "user_id": "local-user",
                "agent_id": f"agent:{cid}:main",
                "event_type": AgentActivityEventType.TOOL_FINISHED.value,
                "visibility": "public",
                "summary": "done",
                "payload": {"tool_name": "run_command", "tool_kind": "terminal", "status": "succeeded"},
                "created_at": old_time,
            },
            {
                "event_id": "evt_recent_prog",
                "sequence": 202,
                "conversation_id": cid,
                "turn_id": r_recent.turn_id,
                "execution_id": str(claimed_recent["execution_id"]),
                "user_id": "local-user",
                "agent_id": f"agent:{cid}:main",
                "event_type": AgentActivityEventType.TOOL_PROGRESSED.value,
                "visibility": "public",
                "summary": "recent progress",
                "payload": {},
                "created_at": now - timedelta(minutes=30),
            },
        ]
        c.execute(insert(conversation_activity_events), rows)

    ov = store.overview(cid, "local-user")
    assert ov["activity_count"] >= 53
    assert ov["tools"] == [
        {
            "tool_name": "run_command",
            "kind": "terminal",
            "count": 1,
            "failures": 0,
            "agent_id": f"agent:{cid}:main",
        }
    ]

    deleted = store.prune_transient_activity_events(older_than_days=7, batch_size=20, max_batches=5)
    assert deleted == 50

    with engine.connect() as c:
        remaining_ids = set(c.execute(select(conversation_activity_events.c.event_id)).scalars())
    # CRITICAL: `evt_old_started` (`tool.started`), `evt_old_fin` (`tool.finished`), and `evt_recent_prog` MUST survive!
    assert {"evt_old_started", "evt_old_fin", "evt_recent_prog"}.issubset(remaining_ids)
    assert not any(eid.startswith("evt_old_prog_") for eid in remaining_ids)
    engine.dispose()


def test_ollama_embedder_handles_empty_input_and_caches_404_and_500_with_cooldown() -> None:
    calls = 0
    status_to_return = 404

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_to_return, json={"error": "unavailable"})

    clock_val = 100.0
    embedder = OllamaEmbedder(
        base_url="http://localhost:11434",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: clock_val,
    )
    # Empty input must return [] immediately without hitting HTTP or tripping cooldown
    assert embedder.embed([]) == []
    assert calls == 0

    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["hello"])
    assert calls == 1

    # Within 300s 404 cooldown: zero HTTP requests, while embed([]) still returns []
    clock_val = 250.0
    assert embedder.embed([]) == []
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["world"])
    assert calls == 1

    # After 300s cooldown expires, 503 sets 60s cooldown
    clock_val = 405.0
    status_to_return = 503
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["again"])
    assert calls == 2
    clock_val = 430.0
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["during_5xx_cooldown"])
    assert calls == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/api/test_gateway_nonblocking_and_overview.py -k "prune or ollama" -v
```
Expected: FAIL (`AttributeError: 'PostgresChatStore' object has no attribute 'prune_transient_activity_events'`).

- [ ] **Step 3: Write minimal implementation**

1. In `src/agentos/persistence/postgres/schema.py` & `src/agentos/persistence/postgres/migrations/versions/0061_activity_events_overview_index.py`:
   - Add `Index("ix_conversation_activity_conv_type_id", conversation_activity_events.c.conversation_id, conversation_activity_events.c.event_type, conversation_activity_events.c.id)` in `schema.py`.
   - Create `0061_activity_events_overview_index.py`:
     ```python
     """Add composite index for conversation overview activity queries."""

     from __future__ import annotations

     import sqlalchemy as sa
     from alembic import op

     revision = "0061_activity_events_overview_index"
     down_revision = "0060_discord_bridge_and_session_watches"
     branch_labels = None
     depends_on = None

     INDEX_NAME = "ix_conversation_activity_conv_type_id"
     TABLE_NAME = "conversation_activity_events"


     def upgrade() -> None:
         bind = op.get_bind()
         existing = {ix["name"] for ix in sa.inspect(bind).get_indexes(TABLE_NAME)}
         if INDEX_NAME not in existing:
             op.create_index(INDEX_NAME, TABLE_NAME, ["conversation_id", "event_type", "id"])


     def downgrade() -> None:
         bind = op.get_bind()
         existing = {ix["name"] for ix in sa.inspect(bind).get_indexes(TABLE_NAME)}
         if INDEX_NAME in existing:
             op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
     ```
2. In `src/agentos/conversations/chat.py`:
   - In `overview()` (`L3402-3468`):
     - Compute `total_activity_count = int(c.execute(select(func.count()).select_from(conversation_activity_events).where(conversation_activity_events.c.conversation_id == conversation_id, conversation_activity_events.c.user_id == user_id)).scalar_one() or 0)` and return `"activity_count": total_activity_count`.
     - Select only `(event_id, event_type, agent_id, summary, payload, created_at)` where `event_type.in_(overview_event_types)` (`tool.finished`, `agent.message_sent`, `agent.message_received`, `turn.failed`) ordered by `id`, and cap returned `messages = messages[-200:]` and `errors = errors[-200:]`.
   - Define module-level `TRANSIENT_SETTLED_EVENT_TYPES` (**excluding `tool.started`**):
     ```python
     TRANSIENT_SETTLED_EVENT_TYPES: tuple[str, ...] = (
         "assistant.delta",
         "agent.thought_delta",
         "tool.progressed",
         "tool.requested",
         "code_mode.validation_started",
         "code_mode.validation_finished",
         "code_mode.stage_changed",
         "speak.block_started",
         "speak.delta",
         "speak.chunk_ready",
         "speak.filler_triggered",
         "speak.interrupted",
         "speak.finished",
     )
     ```
   - Implement `prune_transient_activity_events(self, *, older_than_days: int = 7, batch_size: int = 2000, max_batches: int = 10) -> int` deleting in short transactions up to `max_batches` of `batch_size` rows matching `TRANSIENT_SETTLED_EVENT_TYPES` joined to `conversation_turns` where `state.in_(("completed", "failed", "cancelled"))` and `finished_at < datetime.now(UTC) - timedelta(days=max(1, int(older_than_days)))`.
3. In `src/agentos/workers/scheduler.py`:
   - Instantiate `chat_store = PostgresChatStore(engine)` and track `next_prune_mono = 0.0`. When `now_mono >= next_prune_mono`: run `deleted = chat_store.prune_transient_activity_events(batch_size=2000, max_batches=10)` and set `next_prune_mono = now_mono + (60.0 if deleted >= 20000 else 6 * 3600.0)` so multi-100k-row legacy backlogs drain in 60s steps without locking SQLite.
4. In `src/agentos/retrieval/embeddings/ollama.py`:
   - Accept optional `clock: Callable[[], float] = time.monotonic` in `OllamaEmbedder.__init__`, use `httpx.Timeout(30.0, connect=3.0)` for the default client, guard `if not texts: return []` **first** in `embed()`, check `if self._clock() < self._unavailable_until: raise EmbeddingUnavailable(self._last_reason)`, and set cooldown `300.0s` on HTTP `404` and `60.0s` on HTTP `5xx` or `httpx.HTTPError`.

- [ ] **Step 4: Run tests (including Alembic schema sync & migrations) to verify they pass**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/api/test_gateway_nonblocking_and_overview.py tests/unit/persistence/test_migrations.py tests/unit/persistence/test_alembic_schema_sync.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/agentos/persistence/postgres/schema.py src/agentos/persistence/postgres/migrations/versions/0061_activity_events_overview_index.py src/agentos/conversations/chat.py src/agentos/workers/scheduler.py src/agentos/retrieval/embeddings/ollama.py tests/unit/api/test_gateway_nonblocking_and_overview.py
git commit -m "perf(persistence,ollama): indexa overview no SQL, poda eventos transitorios >7d preservando tool.started e adiciona cooldown no Ollama"
```

---

### Task 6: Disciplina dos 6 Sockets HTTP/1.1 no Frontend (`OrinActivityCenter`, `WorkspaceNavigation`, `OverviewPanel`)

**Files:**
- Modify: `frontend/src/features/overview/OverviewPanel.tsx:29-42`
- Modify: `frontend/src/features/projects/WorkspaceNavigation.tsx:250-327`
- Modify: `frontend/src/components/OrinActivityCenter.tsx:168-195, 419-565`
- Modify: `src/agentos/conversations/chat.py:607-676` (bump `conversations.c.updated_at` em eventos interativos de aprovação)
- Create: `frontend/tests/unit/OverviewPanelPolling.test.tsx`

**Interfaces:**
- Produces:
  - Stable 4s polling in `OverviewPanel` (decoupled from `liveEvents.length`, guarded by `inFlightRef` + `1500ms` throttle on real structural `AgentActivityEventType`s).
  - Bounded concurrency (`<= 2`) + `AbortSignal` injected into `proxyClient.request` + safe cache **only for settled `'waiting_user'` sessions** (with cache eviction on permission/plan/trust resolution) in `OrinActivityCenter`.
  - `WorkspaceNavigation` 3s poll preserving all 5 active states (`['queued', 'starting', 'running', 'streaming', 'cancelling']`) with `inFlightRef` + `pendingReloadRef`.

- [ ] **Step 1: Write the failing frontend test**

```tsx
// frontend/tests/unit/OverviewPanelPolling.test.tsx
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import type { ConversationActivityEvent } from '../../src/features/conversations/activityTypes'
import { OverviewPanel } from '../../src/features/overview/OverviewPanel'

vi.mock('../../src/components/three/OrbitalScene', () => ({
  OrbitalScene: () => <div data-testid="orbital-mock">3D</div>,
}))

function makeOverviewResponse() {
  return new Response(
    JSON.stringify({
      conversation_id: 'conv_1',
      title: 'Test',
      state: 'running',
      provider: 'google-antigravity',
      model_id: 'gemini-4-carbon-high',
      agents: [],
      tools: [],
      messages: [],
      errors: [],
      turns: [],
      activity_count: 0,
      duration_seconds: null,
      token_usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, usage_reported: false },
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
}

function makeEvents(count: number, lastType: ConversationActivityEvent['type'] = 'agent.thought_delta'): ConversationActivityEvent[] {
  return Array.from({ length: count }, (_, i) => ({
    eventId: `evt_${i + 1}`,
    type: i === count - 1 ? lastType : 'agent.thought_delta',
    summary: 'streaming',
    createdAt: '2026-11-15T15:00:00Z',
  }))
}

describe('OverviewPanel polling discipline', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('does not re-fetch /overview on every streaming thought_delta event, only on interval or throttled structural events', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => makeOverviewResponse())
    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })

    const { rerender } = render(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(1)} onClose={() => {}} />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // Simulate 10 rapid streaming thought_delta events within 500ms: MUST NOT trigger 10 /overview fetches!
    for (let i = 2; i <= 11; i++) {
      rerender(
        <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(i)} onClose={() => {}} />,
      )
    }
    await act(async () => {
      vi.advanceTimersByTime(500)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // Real structural event (`agent.created`) after >= 1500ms triggers exactly 1 refresh
    await act(async () => {
      vi.advanceTimersByTime(1600)
    })
    rerender(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(12, 'agent.created')} onClose={() => {}} />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; if (-not (Test-Path frontend\node_modules) -and (Test-Path (Join-Path $mainRoot "frontend\node_modules"))) { New-Item -ItemType Junction -Path frontend\node_modules -Target (Join-Path $mainRoot "frontend\node_modules") | Out-Null }; npm --prefix frontend test -- tests/unit/OverviewPanelPolling.test.tsx
```
Expected: FAIL (`expected "spy" to be called 1 times, but got 11 times`).

- [ ] **Step 3: Write minimal implementation**

1. In `frontend/src/features/overview/OverviewPanel.tsx`:
   - Define `const STRUCTURAL_OVERVIEW_EVENTS = new Set<string>(['agent.created', 'delegation.created', 'delegation.completed', 'delegation.failed', 'turn.completed', 'turn.failed', 'turn.cancelled', 'turn.waiting_user'])`.
   - Remove `liveEvents.length` from the main `setInterval(..., 4000)` `useEffect` dependency list (`[client, conversationId]`), guarding `load()` with `inFlightRef.current` and recording `lastLoadAtRef.current = Date.now()`.
   - Add a secondary `useEffect` watching `const lastEvent = liveEvents[liveEvents.length - 1]` (`lastEvent?.eventId`, `lastEvent?.type`): when `lastEvent && STRUCTURAL_OVERVIEW_EVENTS.has(lastEvent.type)` and `lastEvent.eventId !== lastStructuralIdRef.current`, update `lastStructuralIdRef.current` and either call `load()` immediately if `Date.now() - lastLoadAtRef.current >= 1500` or schedule a single trailing `setTimeout` for the remaining `1500ms` window (cleaned up on unmount/conversation change).
2. In `frontend/src/features/projects/WorkspaceNavigation.tsx`:
   - Keep all 5 active execution states (`['queued', 'starting', 'running', 'streaming', 'cancelling']`, which already excludes `'waiting_user'`) for the `3000ms` interval (`L284, L320-326`).
   - Protect `reload()` with `inFlightRef` + `pendingReloadRef`: if `inFlightRef.current` is `true` when `reload()` is called (e.g., from `orin:conversations-updated`), set `pendingReloadRef.current = true`; in `finally`, set `inFlightRef.current = false` and if `pendingReloadRef.current` is `true` (and component is still mounted), clear `pendingReloadRef.current = false` and trigger one trailing `void reload()`.
3. In `frontend/src/components/OrinActivityCenter.tsx` (and `src/agentos/conversations/chat.py:607-676`):
   - Pass `signal?: AbortSignal` into `fetchConversationWithRawActivities(apiClient, conversationId, signal)` (`L168-195`), injecting `signal` inside `proxyClient.request`: `apiClient.request<T>({ ...options, signal })`, and silently ignore `AbortError` (`err instanceof DOMException && err.name === 'AbortError'`).
   - Maintain `detailCacheRef = useRef<Map<string, { updatedAt: string; state: string; snapshot: ConversationSnapshot; rawActivities: RawActivityRecord[] }>>(new Map())`, pruning IDs no longer in `conversations`.
   - **CRITICAL:** Only read/populate `detailCacheRef` when `entry.state === 'waiting_user' && Boolean(entry.updatedAt)` — **never** cache `ACTIVE_SESSION_STATES` (`running`, `starting`, `queued`, `streaming`, `working`, `cancelling`) across polls so in-turn approvals (`tool.permission_required`, `code_mode.plan_ready`, `workspace.trust_required`, `turn.waiting_user`) are always detected immediately! Also call `detailCacheRef.current.delete(item.conversationId)` inside `handleResolveToolPermission`, `handleResolvePlan`, and `handleTrustWorkspace` (`L419-469`).
   - Run uncached `fetchConversationWithRawActivities` calls through a 2-worker pool (`maxConcurrency = 2`) sharing an `AbortController` cancelled when a newer refresh starts or on unmount.
   - As defense-in-depth in `src/agentos/conversations/chat.py` (`PostgresChatStore._activity`, `L607-676`): when `event.type` is in `{TOOL_PERMISSION_REQUIRED, TOOL_PERMISSION_RESOLVED, CODE_MODE_PLAN_READY, CODE_MODE_DECISION_REQUIRED, WORKSPACE_TRUST_REQUIRED, TURN_WAITING_USER}`, also update `conversations.c.updated_at = created_at` inside the same transaction.

- [ ] **Step 4: Run frontend unit tests, build check & backend chat tests**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; if (-not (Test-Path frontend\node_modules) -and (Test-Path (Join-Path $mainRoot "frontend\node_modules"))) { New-Item -ItemType Junction -Path frontend\node_modules -Target (Join-Path $mainRoot "frontend\node_modules") | Out-Null }; npm --prefix frontend test -- tests/unit/OverviewPanelPolling.test.tsx tests/unit/OverviewPanel.test.tsx; npm --prefix frontend run build
```
Expected: PASS with zero test or TypeScript/Vite build errors.

- [ ] **Step 5: Commit**

```powershell
git add src/agentos/conversations/chat.py frontend/src/features/overview/OverviewPanel.tsx frontend/src/features/projects/WorkspaceNavigation.tsx frontend/src/components/OrinActivityCenter.tsx frontend/tests/unit/OverviewPanelPolling.test.tsx
git commit -m "perf(frontend): protege pool de 6 sockets HTTP/1.1 preservando aprovacoes in-turn no ActivityCenter"
```

---

### Task 7: *Job Objects* Win32 64-bit (`processes.py`), `PreviewServerManager`, Shells Background/Auto-Bg e Fechamento de CLI Ociosa (`native_turn.py`)

**Files:**
- Modify: `src/agentos/launcher/processes.py:45-135`
- Modify: `src/agentos/hooks/processes.py:97-105`
- Modify: `src/agentos/code_mode/preview_server.py:390-495`
- Modify: `src/agentos/agentic/tools/_shared.py:199-206, 660-685, 815-845`
- Modify: `src/agentos/agentic/tools/shell/bash.py:445-465, 630-675`
- Modify: `src/agentos/agentic/tools/shell/tasks.py:69-82, 355-375`
- Modify: `src/agentos/agentic/native_turn.py:2638-2715`
- Create: `tests/unit/code_mode/test_workspace_lock_and_preview_tree.py`

**Interfaces:**
- Produces:
  - 64-bit-safe `ProcessGroup(allow_breakaway: bool = True)` in `src/agentos/launcher/processes.py` (`Affinity` `ctypes.c_size_t`, typed `WinDLL("kernel32", use_last_error=True)`, and descendant snapshot in `adopt(pid)` / `_WindowsJob.assign(pid)`).
  - `PreviewServerManager` tree containment via `ProcessGroup(allow_breakaway=False)` + `process_created` / `kill_verified` (`agentos.process_identity`).
  - Preserved `_WindowsJob(kill_on_close=False)` in `bash.py` registered via `register_background_job` for both `background=True` and auto-backgrounded servers (terminated cleanly in `stop_process` & `SessionEnd`), safe `_DEV_SERVER_CMD_RE` vs explicit-bg Python server detection in `ShellTaskTools._is_long_lived`, and early `handle.close()` for non-persistent CLIs before `wait_for_background_work` in `native_turn.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/code_mode/test_workspace_lock_and_preview_tree.py
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentos.agentic.tools._shared import _DEV_SERVER_CMD_RE
from agentos.agentic.tools.shell.tasks import ShellTaskTools
from agentos.launcher.processes import ProcessGroup
from agentos.launcher.state import process_is_alive


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object test")
def test_nested_process_group_disallows_breakaway_and_kills_grandchildren(tmp_path: Path) -> None:
    group = ProcessGroup(allow_breakaway=False)
    try:
        marker = tmp_path / "grandchild.pid"
        script = (
            "import subprocess, sys, pathlib, time\n"
            "CREATE_BREAKAWAY_FROM_JOB = 0x01000000\n"
            "try:\n"
            "    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], creationflags=CREATE_BREAKAWAY_FROM_JOB)\n"
            "except OSError:\n"
            "    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
            "time.sleep(60)\n"
        )
        parent = subprocess.Popen([sys.executable, "-c", script])
        assert group.adopt(parent.pid) is True

        deadline = time.monotonic() + 5.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        grandchild_pid = int(marker.read_text().strip())

        group.terminate_all()
        parent.wait(timeout=5)

        deadline = time.monotonic() + 3.0
        while process_is_alive(grandchild_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert process_is_alive(grandchild_pid) is False
    finally:
        group.close()


def test_python_server_detection_preserves_foreground_scripts_and_recognizes_explicit_bg() -> None:
    # Foreground script `python main.py` or `python run.py` MUST NOT match `_DEV_SERVER_CMD_RE`
    # (otherwise 2s foreground scripts get prematurely auto-backgrounded!)
    assert _DEV_SERVER_CMD_RE.search("python run.py") is None
    assert _DEV_SERVER_CMD_RE.search("python main.py") is None
    # Explicit server frameworks DO match `_DEV_SERVER_CMD_RE`
    assert _DEV_SERVER_CMD_RE.search("python -m uvicorn app.main:app --port 8000") is not None
    assert _DEV_SERVER_CMD_RE.search("python -m http.server 8080") is not None

    dummy = type("DummyTasks", (ShellTaskTools,), {"_background_log_text": lambda self, _p: ""})()
    # Explicit background (`auto_backgrounded=False`) treats `python run.py` as long-lived server
    assert dummy._is_long_lived({"command": "python run.py", "auto_backgrounded": False}) is True
    # Auto-backgrounded timeout (`auto_backgrounded=True`) does NOT treat `python run.py` as a server without port output
    assert dummy._is_long_lived({"command": "python run.py", "auto_backgrounded": True}) is False
    assert dummy._is_long_lived({"command": "python -m uvicorn app.main:app --port 8000"}) is True
    assert dummy._is_long_lived({"command": "pytest tests/"}) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/code_mode/test_workspace_lock_and_preview_tree.py -v
```
Expected: FAIL (`TypeError: ProcessGroup.__init__() got an unexpected keyword argument 'allow_breakaway'` and `_is_long_lived({"command": "python run.py", ...})` returning `False`).

- [ ] **Step 3: Write minimal implementation**

1. In `src/agentos/launcher/processes.py` (and `src/agentos/hooks/processes.py:97`):
   - Fix `Affinity` in `JOBOBJECT_BASIC_LIMIT_INFORMATION` from `ctypes.POINTER(ctypes.c_ulong)` to `ctypes.c_size_t`.
   - Configure explicit 64-bit `argtypes`/`restype` on `ctypes.WinDLL("kernel32", use_last_error=True)` for `CreateJobObjectW`, `SetInformationJobObject`, `OpenProcess`, `AssignProcessToJobObject`, `TerminateJobObject`, `CloseHandle`, and `GetConsoleProcessList`.
   - Update `ProcessGroup.__init__(self, *, allow_breakaway: bool = True)` (`LimitFlags = 0x2000 | (0x0800 if allow_breakaway else 0)`).
   - In `ProcessGroup.adopt(self, pid: int)` (and `_WindowsJob.assign(self, pid: int)` in `hooks/processes.py`): after assigning `pid` into the job, snapshot already-spawned descendants via `_windows_descendants` (`hooks/processes.py`) and assign each child handle (`SET_QUOTA | TERMINATE`) into the job as well, closing the `Popen -> adopt()` race window.
2. In `src/agentos/code_mode/preview_server.py`:
   - Maintain `_LIVE_PROCESS_GROUPS: dict[tuple[str, str], ProcessGroup] = {}` (`ProcessGroup(allow_breakaway=False)`), calling `group.adopt(proc.pid)` right after `Popen` and storing `"created": process_created(proc.pid)` (`from agentos.process_identity import kill_verified, process_created`) in the manifest record.
   - In `stop_server()`: call `group.terminate_all(); group.close()` when in `_LIVE_PROCESS_GROUPS`; for manifest-only records from prior runs, call `kill_verified(pid, record.get("created"))` when `"created"` is present (falling back to `_pid_alive` + `SIGTERM` only for legacy records lacking `"created"`).
3. In `src/agentos/agentic/tools/_shared.py`, `src/agentos/agentic/tools/shell/bash.py`, and `src/agentos/agentic/tools/shell/tasks.py`:
   - **Keep `_WindowsJob(kill_on_close=False)` in `bash.py`** (never use `KILL_ON_JOB_CLOSE` there, so auto-backgrounded dev servers survive `_run_shell` returning!).
   - Add `_BACKGROUND_JOBS: dict[int, Any] = {}` with `register_background_job(pid, job)` and `terminate_background_job(pid) -> bool` in `_shared.py` (calling `job.terminate(); job.close()`, and also cleaning all registered jobs inside `cleanup_session_background_processes` / `SessionEnd`).
   - In `bash.py:449` (`if background:`): create `bg_job = _job_for(process)` and call `register_background_job(process.pid, bg_job)`.
   - In `bash.py:637` (`if should_auto_bg:`): instead of `_release_job(process_job)`, call `register_background_job(process.pid, process_job)` so `stop_process` (`tasks.py:360`) can terminate the entire Windows Job tree via `terminate_background_job(pid)` + `kill_verified(pid, info.get("created"))` before falling back to `_terminate_pid(pid)`.
   - In `_shared.py:199`: extend `_DEV_SERVER_CMD_RE` **only** with unambiguous server frameworks (`\b(?:uvicorn|gunicorn|hypercorn)\b|\bfastapi\s+(?:dev|run)\b|\bflask\s+run\b|\bstreamlit\s+run\b|\bgradio\b|\bpython(?:\d+(?:\.\d+)?)?(?:\.exe)?\s+(?:-u\s+)?-m\s+(?:uvicorn|gunicorn|fastapi|flask|http\.server)\b|\b(?:django-admin|manage\.py)\s+runserver\b`).
   - In `tasks.py:69` (`ShellTaskTools._is_long_lived`): add `_EXPLICIT_BG_PYTHON_SERVER_RE = re.compile(r"\bpython(?:\d+(?:\.\d+)?)?(?:\.exe)?\s+(?:-u\s+)?[\w./\\-]*(?:run|server|app|main)\.py\b", re.IGNORECASE)` and return `True` when `not entry.get("auto_backgrounded") and bool(_EXPLICIT_BG_PYTHON_SERVER_RE.search(cmd))`.
4. In `src/agentos/agentic/native_turn.py:2638-2715`:
   - Right before calling `wait_for_background_work(...)` (`L2648`), when `not persistent_stdin and handle is not None`: close `handle` (`try: handle.close() except Exception: pass; handle_closed_early = True`) and guard post-wait `handle` cleanup when `handle_closed_early` is `True`, freeing ~370 MB of idle `agy.EXE` RAM while subagents/background jobs finish.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/code_mode/ tests/unit/launcher/ -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/agentos/launcher/processes.py src/agentos/hooks/processes.py src/agentos/code_mode/preview_server.py src/agentos/agentic/tools/_shared.py src/agentos/agentic/tools/shell/bash.py src/agentos/agentic/tools/shell/tasks.py src/agentos/agentic/native_turn.py tests/unit/code_mode/test_workspace_lock_and_preview_tree.py
git commit -m "fix(windows,shell): tipa Job Objects 64-bit, contem arvores de preview/shell e fecha CLI ociosa antes de background wait"
```

---

### Task 8: Anti-Reuso de PID em Locks `.turn`, Sweep Seguro no Boot, Expiração Canônica de Turnos `paused` Órfãos & Verificação Visual

**Files:**
- Modify: `src/agentos/code_mode/workspace_lock.py:60-175`
- Modify: `src/agentos/launcher/supervisor.py:148-175`
- Modify: `src/agentos/conversations/chat.py`
- Modify: `src/agentos/workers/chat.py:949-986`
- Modify: `src/agentos/workers/publisher.py:105-123`
- Modify: `tests/unit/code_mode/test_workspace_lock_and_preview_tree.py`

**Interfaces:**
- Produces:
  - PID-reuse-resistant `.turn` / `.rewind` markers in `src/agentos/code_mode/workspace_lock.py` reusing `process_created` & `same_process` from `agentos.process_identity`, plus `sweep_stale_workspace_locks(folder: Path | None = None) -> int` touching **only** `*.turn.*` and `*.rewind` markers.
  - Canonical `PostgresChatStore.expire_abandoned_paused_turns(*, older_than: timedelta = timedelta(hours=2)) -> tuple[str, ...]` transitioning `Execution`, `conversation_dispatches`, `conversation_turns`, `conversation_messages`, `code_mode_runs`, and `conversations`, called from `ChatWorker.recover_stale()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/code_mode/test_workspace_lock_and_preview_tree.py`:

```python
from datetime import UTC, datetime, timedelta
from sqlalchemy import update

from agentos.code_mode.workspace_lock import _live_turn_marker, sweep_stale_workspace_locks
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter, ExecutionQueryAdapter
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.schema import conversation_dispatches, conversation_turns
from agentos.persistence.sqlite import create_local_engine
from agentos.process_identity import process_created


def test_workspace_lock_detects_pid_reuse_and_sweeps_only_turn_and_rewind_markers(tmp_path: Path) -> None:
    my_pid = os.getpid()
    real_ct = process_created(my_pid)
    assert real_ct is not None

    digest = "abc123digest"
    stale_ct = (real_ct - 36_000_000_000) if isinstance(real_ct, int) else f"{real_ct}-stale"
    reused_marker = tmp_path / f"{digest}.turn.{my_pid}.deadbeef"
    reused_marker.write_text(f"{my_pid} {stale_ct}", encoding="utf-8")

    # Even though `my_pid == os.getpid()`, mismatched creation time proves PID reuse -> removed!
    assert _live_turn_marker(tmp_path, digest) is False
    assert not reused_marker.exists()

    # Sweep removes dead `.turn.*` and `.rewind`, but NEVER touches unrelated `.lock` files
    dead_turn = tmp_path / "otherdigest.turn.99999999.cafe"
    dead_turn.write_text("99999999 12345", encoding="utf-8")
    unrelated_flock = tmp_path / "orin.lock"
    unrelated_flock.write_text(str(my_pid), encoding="utf-8")

    removed = sweep_stale_workspace_locks(tmp_path)
    assert removed == 1
    assert not dead_turn.exists()
    assert unrelated_flock.exists()


def test_abandoned_paused_turn_is_canonically_expired_and_allows_follow_up_message(tmp_path: Path) -> None:
    dsn = f"sqlite:///{tmp_path / 'paused_recover.db'}"
    engine = create_local_engine(dsn)
    upgrade(engine)
    store = PostgresChatStore(engine)

    receipt = store.create(
        user_id="local-user",
        message="Paused Forever",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="idem_paused_1",
    )
    claimed = store.claim(receipt.turn_id)
    assert claimed is not None

    exec_query = ExecutionQueryAdapter(engine)
    exec_app = ExecutionApplicationAdapter(engine)
    now = datetime.now(UTC)
    for idx, target in enumerate(("STARTING", "RUNNING", "PAUSED"), start=1):
        view = exec_query.get(
            {"resource_id": str(claimed["execution_id"]), "user_id": "local-user", "purpose": "execution.read"}
        )
        exec_app.transition(
            {
                "execution_id": str(claimed["execution_id"]),
                "user_id": "local-user",
                "target_state": target,
                "reason_code": "test_setup",
                "result_ref": None,
                "expected_state_version": int(view["state_version"]),
                "idempotency_key": f"setup-{idx}",
                "requested_at": now,
            }
        )

    store.pause_for_reconciliation(claimed, code="EFFECT_RECONCILIATION_REQUIRED", message="Stream interrupted")

    seven_days_ago = now - timedelta(days=7)
    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == receipt.turn_id)
            .values(created_at=seven_days_ago, started_at=seven_days_ago, updated_at=seven_days_ago)
        )
        c.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == receipt.turn_id)
            .values(updated_at=seven_days_ago)
        )

    unblocked = store.expire_abandoned_paused_turns(older_than=timedelta(hours=2))
    assert receipt.turn_id in unblocked

    refreshed = store.get(receipt.conversation_id, "local-user")
    assert refreshed["state"] == "failed"
    exec_after = exec_query.get(
        {"resource_id": str(claimed["execution_id"]), "user_id": "local-user", "purpose": "execution.read"}
    )
    assert exec_after["state"] == "FAILED"

    # Follow-up `"continuar"` must now succeed without conflict!
    follow_up = ChatApplication(store).send("local-user", receipt.conversation_id, "continuar", "idem_paused_2")
    assert follow_up.state == "queued"
    engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path; $py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }; $env:PYTHONPATH = (Resolve-Path .\src).Path; & $py -m pytest tests/unit/code_mode/test_workspace_lock_and_preview_tree.py -k "pid_reuse or abandoned_paused" -v
```
Expected: FAIL (`ImportError: cannot import name 'sweep_stale_workspace_locks'`).

- [ ] **Step 3: Write minimal implementation**

1. In `src/agentos/code_mode/workspace_lock.py`:
   - Import `process_created, same_process` from `agentos.process_identity`.
   - In `_write_turn_marker` and `_claim_rewind`: record `created = process_created(os.getpid())` and write `f"{os.getpid()} {created}" if created is not None else str(os.getpid())`.
   - Implement `_marker_holder_alive(path: Path, *, fallback_pid: int | None = None) -> bool`:
     - Read `text = path.read_text(encoding="utf-8").strip()` (`FileNotFoundError -> False`, other `OSError -> True` fail-closed).
     - If `not text`: if `fallback_pid is not None and not _pid_alive(fallback_pid)`: return `False`; check `time.time() - path.stat().st_mtime < _UNWRITTEN_SECONDS` (`5.0s`) so a marker between `os.open` and `write` is never deleted prematurely.
     - Parse `parts = text.split(None, 1)`: if `not parts[0].isdigit()`, return `False`; `pid = int(parts[0])`.
     - If `len(parts) == 2`: parse `expected: int | str = int(parts[1]) if parts[1].lstrip("-").isdigit() else parts[1]` and return `same_process(pid, expected)` (which checks `process_created(pid) == expected` even when `pid == os.getpid()`!).
     - Legacy fallback (`len(parts) == 1`): return `_pid_alive(pid)`.
   - Use `_marker_holder_alive` in both `_rewind_holder_alive` and `_live_turn_marker`.
   - Implement `sweep_stale_workspace_locks(folder: Path | None = None) -> int`: scan **only** `target.glob("*.turn.*")` and `target.glob("*.rewind")` inside `target = folder or _lock_dir()` (never touching `.lock` files!), unlinking any marker where `_marker_holder_alive` returns `False`, and returning the count removed.
   - Call `sweep_stale_workspace_locks(self.paths.run / "workspace-locks")` in `Supervisor._prepare()` (`src/agentos/launcher/supervisor.py`) and inside `if not has_in_flight and not _ORPHANS_SWEPT:` in `recover_once` (`src/agentos/workers/publisher.py:108`).
2. In `src/agentos/conversations/chat.py` & `src/agentos/workers/chat.py`:
   - Implement `PostgresChatStore.expire_abandoned_paused_turns(self, *, older_than: timedelta = timedelta(hours=2)) -> tuple[str, ...]`:
     - Select rows from `conversation_turns` outer-joined to `conversation_dispatches` where `conversation_turns.c.state == "paused"` and `func.coalesce(conversation_dispatches.c.updated_at, conversation_turns.c.updated_at, conversation_turns.c.created_at) < datetime.now(UTC) - older_than`.
     - For each row:
       1) Transition canonical `Execution` (`PAUSED -> FAILED`) via `ExecutionQueryAdapter(self._engine)` + `ExecutionApplicationAdapter(self._engine).transition(...)` when `view["state"] == "PAUSED"` (ignoring `ApplicationConflictError` if concurrently transitioned).
       2) In `with self._engine.begin() as c:` (guarded by `conversation_turns.c.state == "paused"`):
          - Update `conversation_dispatches` (`state="failed"`, `last_error=existing_last_error or "ABANDONED_PAUSED_TURN_EXPIRED"`, `updated_at=now`).
          - Update `conversation_turns` (`state="failed"`, `finished_at=now`, `updated_at=now`, `cli_options=None`).
          - Update `conversation_messages` for `assistant_message_id` (`status="failed"`, `retryable=True`, `updated_at=now`, preserving partial `content`!).
          - Update `code_mode_runs` for `turn_id` (`stage=CodeStage.BLOCKED.value`, `completion_kind=None`, `caveats=existing_last_error or "ABANDONED_PAUSED_TURN_EXPIRED"`, `updated_at=now`).
          - Update `conversations` (`where conversation_id == cid and state == "paused"` and no newer turn on `cid` is in `("queued", "starting", "running", "waiting_user")`) to `state="failed", updated_at=now`.
          - Insert `conversation_events` (`event_type="turn.failed"`, `payload={"state": "failed", "retryable": True, "reason": "abandoned_paused_turn_expired"}`).
   - Call `expired_paused = self.store.expire_abandoned_paused_turns(older_than=timedelta(hours=2))` at the end of `ChatWorker.recover_stale()` (`src/agentos/workers/chat.py:985`) and include `expired_paused` in the returned tuple.

- [ ] **Step 4: Run full test suite + Real Browser Visual Verification**

1. Run backend & frontend automated tests:
```powershell
$mainRoot = (Resolve-Path (Join-Path (git rev-parse --git-common-dir) "..")).Path
$py = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { Join-Path $mainRoot ".venv\Scripts\python.exe" }
$env:PYTHONPATH = (Resolve-Path .\src).Path
if (-not (Test-Path frontend\node_modules) -and (Test-Path (Join-Path $mainRoot "frontend\node_modules"))) {
    New-Item -ItemType Junction -Path frontend\node_modules -Target (Join-Path $mainRoot "frontend\node_modules") | Out-Null
}
& $py -m pytest tests/unit/test_redaction*.py tests/unit/agentic/test_*redaction*.py tests/unit/discord/ tests/integration/discord/ tests/unit/launcher/ tests/unit/api/test_gateway_nonblocking_and_overview.py tests/unit/api/test_blocking_routes_do_not_stall_the_event_loop.py tests/unit/provider_catalog/ tests/unit/persistence/test_migrations.py tests/unit/persistence/test_alembic_schema_sync.py tests/unit/code_mode/ -v
npm --prefix frontend test -- tests/unit/OverviewPanelPolling.test.tsx tests/unit/OverviewPanel.test.tsx
npm --prefix frontend run build
```
2. **Visual Verification in Real Browser (Mandatory Rule #3):**
   - Start an isolated test/dev instance on an unused port + temporary SQLite DB (never touching the live Orin Desktop instance on `49200` / `AppData\Local\Orin\data\orin.db`!).
   - Open in real browser (Playwright / browser) in both **desktop (`1440x900`)** and **mobile (`390x844`)** viewports:
     - Verify main chat view, `WorkspaceNavigation`, `OrinActivityCenter` and `OverviewPanel` (active, empty and error states).
     - Inspect browser Network requests to confirm zero cascading `/overview` storms and `<= 3` concurrent background HTTP/1.1 connections.

- [ ] **Step 5: Commit**

```powershell
git add src/agentos/code_mode/workspace_lock.py src/agentos/launcher/supervisor.py src/agentos/conversations/chat.py src/agentos/workers/chat.py src/agentos/workers/publisher.py tests/unit/code_mode/test_workspace_lock_and_preview_tree.py
git commit -m "fix(locks,workers): detecta reuso de PID via process_identity, varre markers no boot e expira turnos paused abandonados"
```
