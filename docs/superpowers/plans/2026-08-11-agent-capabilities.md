# Agent Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar as três lacunas de capacidade do agente conversacional: ele não descobre informação na web, não enxerga páginas que dependem de JavaScript, e executa tools sem nenhuma camada de política.

**Architecture:** `web_search` entra como uma tool com cliente HTTP injetável, registrada só quando há chave configurada. O browser ganha uma fachada fina (`ConversationBrowser`) que constrói `BrowserJob` diretamente contra o `PlaywrightBrowserAdapter` já existente, com um coletor de artefato em memória para o DOM voltar ao modelo. A política vira um gate declarativo consultado por `AgentToolset.invoke`, reaproveitando o vocabulário de `tool_runtime` sem forçar os resultados pelo contrato "só resumo" daquele caminho.

**Tech Stack:** Python 3.12, httpx, Playwright (opcional), pytest.

## Global Constraints

- Nome público do produto é **Orin**; identificadores internos permanecem `agentos`.
- Uma capacidade indisponível (sem chave, sem Playwright) **não** registra a tool. O modelo nunca deve ver uma tool que vai falhar por configuração.
- `web_search` e o browser continuam sujeitos à mesma regra de rede das tools atuais: nada de endereços privados, loopback ou `.local`.
- Nenhum segredo (chave de busca, cookie) pode aparecer em `ToolOutcome.content`, em `payload` ou em evento de atividade.
- Todo módulo novo/alterado começa com `from __future__ import annotations`.
- Rodar testes com `uv run pytest <caminho> -v`.

**Depende de:** Plano 1 (superfície de tools) e Plano 2 (execução paralela — `web_search` e `browse_page` são `read_only=True`).

---

### Task 1: Busca na web

**Files:**
- Create: `src/agentos/agentic/web_search.py`
- Modify: `src/agentos/agentic/agent_tools.py` (`__init__`, `_build_definitions`, handler)
- Modify: `src/agentos/agentic/session.py:365-373` (`_toolset`), `:172-199` (`__init__`)
- Modify: `src/agentos/workers/chat.py:207-220` (construção da `TurnSession`)
- Test: `tests/unit/agentic/test_web_search.py`

**Interfaces:**
- Consumes: `httpx.Client`
- Produces:
  - `agentos.agentic.web_search.SearchResult` — dataclass `(title: str, url: str, snippet: str)`
  - `agentos.agentic.web_search.BraveSearchClient(api_key: str, client: httpx.Client | None = None)` com `search(query: str, *, limit: int = 5) -> list[SearchResult]`
  - `agentos.agentic.web_search.search_client_from_environment() -> BraveSearchClient | None` — lê `AGENTOS_SEARCH_API_KEY`; devolve `None` quando ausente
  - tool `web_search` com argumentos `{query, limit?}`, `read_only=True`

**Por quê:** o agente só tem `fetch_url`. Sem busca ele não descobre endereços — ou o usuário fornece a URL, ou ele adivinha e falha.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/agentic/test_web_search.py`:

```python
from __future__ import annotations

import httpx
import pytest

from agentos.agentic.web_search import BraveSearchClient, SearchResult, search_client_from_environment


def _client(payload: dict, captured: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_maps_the_response_into_bounded_results() -> None:
    payload = {"web": {"results": [
        {"title": "Orin docs", "url": "https://example.test/a", "description": "how it works"},
        {"title": "Other", "url": "https://example.test/b", "description": "more"},
    ]}}

    results = BraveSearchClient("key", _client(payload)).search("orin", limit=1)

    assert results == [SearchResult("Orin docs", "https://example.test/a", "how it works")]


def test_the_api_key_travels_in_the_header_and_never_in_the_query() -> None:
    captured: list[httpx.Request] = []
    BraveSearchClient("secret-key", _client({"web": {"results": []}}, captured)).search("orin")

    assert captured[0].headers["x-subscription-token"] == "secret-key"
    assert "secret-key" not in str(captured[0].url)


def test_a_malformed_response_yields_no_results_instead_of_raising() -> None:
    assert BraveSearchClient("key", _client({"unexpected": True})).search("orin") == []


def test_no_client_is_built_without_a_configured_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENTOS_SEARCH_API_KEY", raising=False)

    assert search_client_from_environment() is None


def test_a_client_is_built_when_the_key_is_present(monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_SEARCH_API_KEY", "abc")

    assert isinstance(search_client_from_environment(), BraveSearchClient)
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_web_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentos.agentic.web_search'`

- [ ] **Step 3: Implementar o cliente**

Criar `src/agentos/agentic/web_search.py`:

```python
"""Web search for the conversational agent.

``fetch_url`` can only read an address the agent already knows. This module is
the discovery half: a small, injectable client whose response is projected into
bounded values before anything reaches the model. The API key travels in a
header and is never part of a URL, a result or an activity payload.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping

import httpx


SEARCH_TIMEOUT_SECONDS = 15
MAX_SEARCH_RESULTS = 10
MAX_TITLE_CHARS = 200
MAX_SNIPPET_CHARS = 400
API_KEY_VARIABLE = "AGENTOS_SEARCH_API_KEY"
ENDPOINT_VARIABLE = "AGENTOS_SEARCH_ENDPOINT"
DEFAULT_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class BraveSearchClient:
    """Brave Search API adapter; any provider with the same shape can replace it."""

    def __init__(self, api_key: str, client: httpx.Client | None = None, *, endpoint: str = DEFAULT_ENDPOINT) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-blank string")
        self._api_key = api_key
        self._endpoint = endpoint
        self._client = client or httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS)
        self._owns_client = client is None

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        bounded = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        response = self._client.get(
            self._endpoint,
            params={"q": str(query)[:400], "count": bounded},
            headers={"x-subscription-token": self._api_key, "accept": "application/json"},
        )
        response.raise_for_status()
        return self._project(response.json(), bounded)

    @staticmethod
    def _project(body: Any, limit: int) -> list[SearchResult]:
        web = body.get("web") if isinstance(body, Mapping) else None
        entries = web.get("results") if isinstance(web, Mapping) else None
        if not isinstance(entries, list):
            return []
        results: list[SearchResult] = []
        for item in entries[:limit]:
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            results.append(SearchResult(
                str(item.get("title") or url)[:MAX_TITLE_CHARS],
                url,
                str(item.get("description") or "")[:MAX_SNIPPET_CHARS],
            ))
        return results

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def search_client_from_environment() -> BraveSearchClient | None:
    """Build a client only when a key is configured; otherwise the tool is not registered."""
    key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if not key:
        return None
    return BraveSearchClient(key, endpoint=os.environ.get(ENDPOINT_VARIABLE, "").strip() or DEFAULT_ENDPOINT)


__all__ = [
    "API_KEY_VARIABLE",
    "BraveSearchClient",
    "DEFAULT_ENDPOINT",
    "MAX_SEARCH_RESULTS",
    "SearchResult",
    "search_client_from_environment",
]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_web_search.py -v`
Expected: PASS

- [ ] **Step 5: Escrever o teste da tool**

Acrescentar em `tests/unit/agentic/test_agent_tools.py`:

```python
def test_web_search_is_absent_without_a_configured_client(toolset: AgentToolset) -> None:
    assert "web_search" not in [item.name for item in toolset.definitions()]


def test_web_search_returns_titles_and_urls_to_the_model(tmp_path) -> None:
    from agentos.agentic.web_search import SearchResult
    from agentos.agentic.workspace import ConversationWorkspace

    class Searcher:
        def search(self, query, *, limit=5):
            return [SearchResult("Orin docs", "https://example.test/a", "how it works")]

    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_search"), search_client=Searcher())

    outcome = tools.invoke("web_search", {"query": "orin"})

    assert outcome.status == "succeeded"
    assert "https://example.test/a" in outcome.content
    assert outcome.payload["count"] == 1
    assert tools.is_read_only("web_search")
```

- [ ] **Step 6: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py -k web_search -v`
Expected: FAIL — `TypeError: AgentToolset.__init__() got an unexpected keyword argument 'search_client'`

- [ ] **Step 7: Registrar a tool**

Em `src/agentos/agentic/agent_tools.py`, acrescentar o parâmetro ao `__init__` (junto de `http_client`):

```python
        search_client: object | None = None,
```

e no corpo:

```python
        self._search_client = search_client
```

Em `_build_definitions`, logo depois do bloco de `fetch_url` (ainda dentro da lista `items` inicial não — este é um bloco condicional, colocar após a lista, antes de `if self._enable_terminal:`):

```python
        if self._search_client is not None:
            items.append(ToolDefinition(
                "web_search",
                "Search the public web and return titles, URLs and snippets. Use this to find an address, then fetch_url to read it.",
                _schema({"query": _TEXT, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ("query",)),
                self.web_search, "web", read_only=True,
            ))
```

E o handler, logo depois de `fetch_url`:

```python
    def web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        if self._search_client is None:
            raise AgentToolError("Web search is not available.")
        if not isinstance(query, str) or not query.strip():
            raise AgentToolError("query must be a non-blank string")
        try:
            results = self._search_client.search(query.strip(), limit=int(limit))
        except httpx.HTTPError as error:
            raise AgentToolError(f"The search provider could not be reached: {type(error).__name__}") from error
        if not results:
            return {"summary": f"Nenhum resultado para '{query.strip()[:40]}'", "content": "[no results]", "payload": {"count": 0, "label": query.strip()[:80]}}
        body = "\n\n".join(f"{item.title}\n{item.url}\n{item.snippet}" for item in results)
        return {
            "summary": f"Buscou na web: {len(results)} {'resultado' if len(results) == 1 else 'resultados'}",
            "content": body,
            "payload": {"count": len(results), "label": query.strip()[:80]},
        }
```

- [ ] **Step 8: Ligar na sessão e no worker**

Em `src/agentos/agentic/session.py`, acrescentar o parâmetro ao `__init__` de `TurnSession` (depois de `skill_load_recorder`):

```python
        search_client=None,
```

no corpo:

```python
        self.search_client = search_client
```

e em `_toolset`:

```python
            search_client=self.search_client,
```

Em `src/agentos/workers/chat.py`, acrescentar ao topo:

```python
from agentos.agentic.web_search import search_client_from_environment
```

e à construção da `TurnSession` (linha ~207):

```python
            search_client=search_client_from_environment(),
```

- [ ] **Step 9: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic tests/unit/workers -v`
Expected: PASS

- [ ] **Step 10: Documentar a variável**

Acrescentar ao `README.md`, na seção de variáveis de ambiente:

```markdown
| `AGENTOS_SEARCH_API_KEY` | Chave da API de busca (Brave Search por padrão). Sem ela a tool `web_search` não é registrada. |
| `AGENTOS_SEARCH_ENDPOINT` | Endpoint alternativo compatível com o formato do Brave Search. Opcional. |
```

- [ ] **Step 11: Commit**

```bash
git add src/agentos/agentic/web_search.py src/agentos/agentic/agent_tools.py src/agentos/agentic/session.py src/agentos/workers/chat.py tests/unit/agentic/test_web_search.py tests/unit/agentic/test_agent_tools.py README.md
git commit -m "feat(tools): add web search alongside fetch_url"
```

---

### Task 2: Página renderizada via browser

**Files:**
- Create: `src/agentos/agentic/browser_tools.py`
- Modify: `src/agentos/agentic/agent_tools.py` (`__init__`, `_build_definitions`, handler)
- Modify: `src/agentos/agentic/session.py` (`__init__`, `_toolset`)
- Modify: `src/agentos/workers/chat.py`
- Test: `tests/unit/agentic/test_browser_tools.py`

**Interfaces:**
- Consumes: `agentos.browser.models` (`BrowserJob`, `BrowserOperationContext`, `BrowserOperationKind`, `BrowserLimits`, `BrowserWorkerGrant`, `GrantCapability`, `BrowserArtifactRef`, `BrowserResult`, `BrowserJobFailed`), `agentos.browser.playwright_adapter.PlaywrightBrowserAdapter`
- Produces:
  - `agentos.agentic.browser_tools.MemoryArtifactOutput` — implementa o protocolo `BrowserArtifactOutput` guardando os bytes em memória
  - `agentos.agentic.browser_tools.ConversationBrowser(adapter, *, user_id, workspace_id, agent_id, execution_id)` com `render(url: str) -> str` (HTML da página) e `close() -> None`
  - `agentos.agentic.browser_tools.conversation_browser_for(turn: Mapping[str, object]) -> ConversationBrowser | None` — devolve `None` quando Playwright não está instalado
  - tool `browse_page` com argumento `{url}`, `read_only=True`

**Por quê:** existe um stack de browser completo em `src/agentos/browser/` (adapter Playwright, política de rede, limites, artefatos) que o agente conversacional simplesmente não alcança. `fetch_url` devolve o HTML bruto do servidor, então qualquer página que monta conteúdo por JavaScript volta vazia.

**Fora de escopo deliberado:** cliques, formulários, cookies e sessões persistentes. Esta tarefa entrega leitura de página renderizada; interação é um plano próprio.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/agentic/test_browser_tools.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentos.agentic.browser_tools import ConversationBrowser, MemoryArtifactOutput
from agentos.browser.models import (
    BrowserArtifactRef,
    BrowserErrorCode,
    BrowserJobFailed,
    BrowserOperationKind,
    BrowserPageSnapshot,
    BrowserPageStatus,
    BrowserResult,
    EffectState,
    Retryability,
)


class FakeAdapter:
    """Stands in for PlaywrightBrowserAdapter without launching a browser."""

    def __init__(self, *, fail: bool = False) -> None:
        self.artifact_output = None
        self.jobs: list[BrowserOperationKind] = []
        self._fail = fail

    def execute(self, job):
        self.jobs.append(job.operation)
        if self._fail:
            return BrowserJobFailed(job.job_id, BrowserErrorCode.POLICY_DENIED, EffectState.NOT_APPLIED, Retryability.NEVER)
        if job.operation is BrowserOperationKind.NAVIGATE:
            snapshot = BrowserPageSnapshot(job.page_id, job.session_id, str(job.arguments["url"]), "Title", BrowserPageStatus.READY, 1, job.submitted_at, datetime.now(UTC))
            return BrowserResult("PAGE", page=snapshot, page_version=1)
        data = b"<html><body>rendered content</body></html>"
        grant = job.grants[0]
        sink = self.artifact_output.begin(job.operation.value, job.context, grant, len(data))
        sink.write(data)
        return BrowserResult("DOM", bytes_count=len(data), artifact_ref=self.artifact_output.commit(sink, "text/html"))

    def cleanup(self, session_id: str) -> bool:
        return True


def _browser(**kwargs) -> ConversationBrowser:
    return ConversationBrowser(FakeAdapter(**kwargs), user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1")


def test_render_navigates_then_captures_the_dom() -> None:
    browser = _browser()

    html = browser.render("https://example.test/page")

    assert "rendered content" in html
    assert browser.adapter.jobs == [BrowserOperationKind.NAVIGATE, BrowserOperationKind.CAPTURE_DOM]


def test_render_reports_a_refused_navigation_instead_of_returning_empty_html() -> None:
    with pytest.raises(RuntimeError):
        _browser(fail=True).render("https://example.test/page")


def test_the_artifact_output_returns_the_bytes_it_was_given() -> None:
    output = MemoryArtifactOutput()
    sink = output.begin("CAPTURE_DOM", object(), object(), 32)
    sink.write(b"abc")
    reference = output.commit(sink, "text/html")

    assert isinstance(reference, BrowserArtifactRef)
    assert output.data == b"abc"
    assert reference.size_bytes == 3
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_browser_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentos.agentic.browser_tools'`

- [ ] **Step 3: Implementar a fachada**

Criar `src/agentos/agentic/browser_tools.py`:

```python
"""Rendered-page reading for the conversational agent.

The browser domain in ``agentos.browser`` is built around durable sessions,
leases and artifact references, which is the right shape for a long-running
automation job and the wrong shape for one chat turn. This module is the thin
adaptation: it builds the ``BrowserJob`` values the adapter expects, collects
the captured DOM in memory instead of in artifact storage, and hands plain text
back to the model.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Mapping
from uuid import uuid4

from agentos.browser.models import (
    BrowserArtifactRef,
    BrowserJob,
    BrowserJobFailed,
    BrowserLimits,
    BrowserOperationContext,
    BrowserOperationKind,
    BrowserWorkerGrant,
    GrantCapability,
)


BROWSER_TIMEOUT = timedelta(seconds=30)
MAX_DOM_BYTES = 2_000_000
MAX_SCREENSHOT_BYTES = 4_000_000


class _MemorySink:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> int:
        self.buffer.extend(data)
        return len(data)


class MemoryArtifactOutput:
    """Collects a capture in memory so the turn can read it without storage."""

    def __init__(self) -> None:
        self.data = b""

    def begin(self, kind: str, context: object, grant: object, maximum_bytes: int) -> _MemorySink:
        return _MemorySink()

    def commit(self, sink: _MemorySink, media_type: str) -> BrowserArtifactRef:
        self.data = bytes(sink.buffer)
        return BrowserArtifactRef(f"memory:{uuid4().hex}", 1, len(self.data), media_type, "INTERNAL")

    def abort(self, sink: _MemorySink) -> None:
        self.data = b""


class ConversationBrowser:
    """One headless page, alive for the duration of a turn."""

    def __init__(self, adapter, *, user_id: str, workspace_id: str, agent_id: str, execution_id: str) -> None:
        self.adapter = adapter
        self.output = MemoryArtifactOutput()
        self.adapter.artifact_output = self.output
        self._session_id = f"session-{uuid4().hex}"
        self._page_id = f"page-{uuid4().hex}"
        self._context = BrowserOperationContext(user_id, workspace_id, agent_id, execution_id, f"correlation-{uuid4().hex}", "browser.page", f"agent:{agent_id}")
        self._limits = BrowserLimits(BROWSER_TIMEOUT, 1, 5, MAX_DOM_BYTES, MAX_SCREENSHOT_BYTES, 0, 0, 0, "network:strict")
        self._grant = BrowserWorkerGrant(
            f"grant-{uuid4().hex}", self._context, f"lease-{uuid4().hex}", "profile-conversation", self._session_id,
            (GrantCapability.NAVIGATE, GrantCapability.READ_DOM), datetime.now(UTC) + BROWSER_TIMEOUT, 1,
        )

    def _job(self, operation: BrowserOperationKind, arguments: Mapping[str, object]) -> BrowserJob:
        now = datetime.now(UTC)
        return BrowserJob(
            f"job-{uuid4().hex}", self._context, self._grant.lease_id, "profile-conversation", 1,
            self._session_id, self._page_id, operation, dict(arguments), self._limits, (self._grant,),
            f"idempotency-{uuid4().hex}", now + BROWSER_TIMEOUT, now,
        )

    def _run(self, operation: BrowserOperationKind, arguments: Mapping[str, object]):
        outcome = self.adapter.execute(self._job(operation, arguments))
        if isinstance(outcome, BrowserJobFailed):
            raise RuntimeError(f"browser refused the operation: {outcome.error_code.value}")
        return outcome

    def render(self, url: str) -> str:
        """Navigate and return the rendered HTML of the page."""
        self._run(BrowserOperationKind.NAVIGATE, {"url": url})
        self._run(BrowserOperationKind.CAPTURE_DOM, {})
        return self.output.data.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.adapter.cleanup(self._session_id)
        except Exception:
            # Cleanup is best effort; a stuck engine must not fail the turn.
            pass


def conversation_browser_for(turn: Mapping[str, object]) -> ConversationBrowser | None:
    """Build a browser only when the optional engine is actually installed."""
    from agentos.browser.playwright_adapter import PlaywrightBrowserAdapter

    if not PlaywrightBrowserAdapter.is_available():
        return None
    return ConversationBrowser(
        PlaywrightBrowserAdapter(),
        user_id=str(turn.get("user_id") or "user"),
        workspace_id=str(turn.get("workspace_id") or turn.get("conversation_id") or "workspace"),
        agent_id=str(turn.get("agent_id") or "agent"),
        execution_id=str(turn.get("execution_id") or "execution"),
    )


__all__ = ["BROWSER_TIMEOUT", "ConversationBrowser", "MemoryArtifactOutput", "conversation_browser_for"]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_browser_tools.py -v`
Expected: PASS

- [ ] **Step 5: Escrever o teste da tool `browse_page`**

Acrescentar em `tests/unit/agentic/test_agent_tools.py`:

```python
def test_browse_page_is_absent_without_a_browser(toolset: AgentToolset) -> None:
    assert "browse_page" not in [item.name for item in toolset.definitions()]


def test_browse_page_returns_the_rendered_text(tmp_path) -> None:
    from agentos.agentic.workspace import ConversationWorkspace

    class Browser:
        def render(self, url):
            return "<html><head><title>Rendered</title></head><body><p>hello</p><script>x()</script></body></html>"

    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_browser"), browser=Browser())

    outcome = tools.invoke("browse_page", {"url": "https://example.test/page"})

    assert outcome.status == "succeeded"
    assert "hello" in outcome.content
    assert "x()" not in outcome.content
    assert outcome.payload["label"] == "Rendered"


def test_browse_page_refuses_a_private_address(tmp_path) -> None:
    from agentos.agentic.workspace import ConversationWorkspace

    class Browser:
        def render(self, url):
            raise AssertionError("must not be reached")

    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_browser"), browser=Browser())

    outcome = tools.invoke("browse_page", {"url": "http://127.0.0.1/admin"})

    assert outcome.status == "failed"
```

- [ ] **Step 6: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py -k browse_page -v`
Expected: FAIL — `TypeError: AgentToolset.__init__() got an unexpected keyword argument 'browser'`

- [ ] **Step 7: Registrar a tool**

Em `src/agentos/agentic/agent_tools.py`, acrescentar ao `__init__`:

```python
        browser: object | None = None,
```

no corpo:

```python
        self._browser = browser
```

Em `_build_definitions`, logo depois do bloco de `web_search`:

```python
        if self._browser is not None:
            items.append(ToolDefinition(
                "browse_page",
                "Open a public page in a real browser and return its rendered text. Use this only when fetch_url comes back empty or incomplete because the page builds its content with JavaScript.",
                _schema({"url": _TEXT}, ("url",)),
                self.browse_page, "web", read_only=True,
            ))
```

E o handler, logo depois de `web_search`:

```python
    def browse_page(self, url: str) -> dict[str, Any]:
        if self._browser is None:
            raise AgentToolError("The browser is not available.")
        target = _public_url(url)
        try:
            html = self._browser.render(target)
        except RuntimeError as error:
            raise AgentToolError(f"The page could not be rendered: {error}") from error
        parser = _TextExtractor()
        parser.feed(html)
        title = parser.title or target
        return {
            "summary": f"Abriu {urlparse(target).netloc}",
            "content": f"{target}\n\n{parser.text()}",
            "payload": {"url": target, "label": title[:120] or target, "rendered": True},
        }
```

- [ ] **Step 8: Ligar na sessão e no worker**

Em `src/agentos/agentic/session.py`, acrescentar ao `__init__` de `TurnSession`:

```python
        browser=None,
```

no corpo:

```python
        self.browser = browser
```

em `_toolset`:

```python
            browser=self.browser,
```

Em `src/agentos/workers/chat.py`, acrescentar ao topo:

```python
from agentos.agentic.browser_tools import conversation_browser_for
```

e à construção da `TurnSession`:

```python
            browser=conversation_browser_for(turn),
```

- [ ] **Step 9: Rodar a suíte**

Run: `uv run pytest tests/unit/agentic tests/unit/browser tests/unit/workers -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/agentos/agentic/browser_tools.py src/agentos/agentic/agent_tools.py src/agentos/agentic/session.py src/agentos/workers/chat.py tests/unit/agentic/test_browser_tools.py tests/unit/agentic/test_agent_tools.py
git commit -m "feat(tools): read JavaScript-rendered pages through the browser adapter"
```

---

### Task 3: Gate de política sobre as tools do agente

**Files:**
- Create: `src/agentos/agentic/tool_policy.py`
- Modify: `src/agentos/agentic/agent_tools.py` (`ToolDefinition`, `__init__`, `_build_definitions`, `invoke`)
- Modify: `src/agentos/agentic/session.py` (`__init__`, `_toolset`)
- Test: `tests/unit/agentic/test_tool_policy.py`

**Interfaces:**
- Consumes: `ToolDefinition.kind`
- Produces:
  - `ToolDefinition.policy_tags: tuple[str, ...]` (default `()`)
  - `agentos.agentic.tool_policy.ToolPolicy` — Protocol com `allows(name: str, tags: tuple[str, ...]) -> bool`
  - `agentos.agentic.tool_policy.AllowList(allowed: Collection[str] | None = None, denied: Collection[str] = ())` — `None` significa "tudo que não está negado"
  - `AgentToolset(policy=...)`: uma tool negada **não é publicada** em `definitions()` e, se ainda assim for chamada, `invoke` devolve `TOOL_NOT_AUTHORIZED`

**Por quê:** hoje existem dois catálogos. `tool_runtime` tem política, capabilities, idempotência e classificação de dados — e não é o caminho usado no chat. `agent_tools` é o caminho usado e não tem política nenhuma. Esta tarefa traz o conceito que faltava para o caminho real.

**Fora de escopo deliberado, com motivo:** rotear `AgentToolset.invoke` por `ToolRuntime.invoke` não é feito aqui. Aquele contrato devolve ao modelo apenas `summary` + `result_ref` (é o ponto do módulo: nenhuma saída de adapter cruza a fronteira do provider), enquanto o agente conversacional precisa **ler** o que a tool retornou. Unificar os dois exigiria uma segunda projeção "conteúdo autorizado" em `tool_runtime` e é um plano próprio.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/agentic/test_tool_policy.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tool_policy import AllowList
from agentos.agentic.workspace import ConversationWorkspace


def _tools(tmp_path: Path, policy) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_policy"), policy=policy)


def test_a_denied_tool_is_not_published_to_the_model(tmp_path: Path) -> None:
    tools = _tools(tmp_path, AllowList(denied=("run_command",)))

    assert "run_command" not in [item.name for item in tools.definitions()]
    assert "read_file" in [item.name for item in tools.definitions()]


def test_a_denied_tool_is_refused_even_if_the_model_calls_it_anyway(tmp_path: Path) -> None:
    tools = _tools(tmp_path, AllowList(denied=("run_command",)))

    outcome = tools.invoke("run_command", {"command": "echo hi"})

    assert outcome.status == "failed"
    assert outcome.error_code == "UNKNOWN_TOOL"


def test_an_allow_list_publishes_only_what_it_names(tmp_path: Path) -> None:
    tools = _tools(tmp_path, AllowList(allowed=("read_file", "list_files")))

    assert sorted(item.name for item in tools.definitions()) == ["list_files", "read_file"]


def test_a_policy_can_deny_a_whole_family_by_tag(tmp_path: Path) -> None:
    tools = _tools(tmp_path, AllowList(denied=("tag:mutates",)))

    published = [item.name for item in tools.definitions()]
    assert "write_file" not in published
    assert "edit_file" not in published
    assert "read_file" in published


def test_no_policy_publishes_everything(tmp_path: Path) -> None:
    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_policy"))

    assert "run_command" in [item.name for item in tools.definitions()]
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_tool_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentos.agentic.tool_policy'`

- [ ] **Step 3: Implementar a política**

Criar `src/agentos/agentic/tool_policy.py`:

```python
"""Declarative authorization for the agent-facing tool set.

``agentos.tool_runtime`` already models policy for the projected action path.
This is the same idea placed on the path the chat agent actually uses, kept
deliberately small: a decision function over a tool's name and its tags. A
denied tool is never published, because a tool the model can see and cannot use
is a wasted round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Protocol


class ToolPolicy(Protocol):
    def allows(self, name: str, tags: tuple[str, ...]) -> bool: ...


@dataclass(frozen=True, slots=True)
class AllowList:
    """``allowed=None`` means everything that is not explicitly denied.

    An entry of the form ``tag:<value>`` matches a tool carrying that policy
    tag, which is how a whole family is authorized or refused at once.
    """

    allowed: Collection[str] | None = None
    denied: Collection[str] = ()

    def allows(self, name: str, tags: tuple[str, ...]) -> bool:
        tagged = {f"tag:{tag}" for tag in tags}
        if name in set(self.denied) or tagged & set(self.denied):
            return False
        if self.allowed is None:
            return True
        return name in set(self.allowed) or bool(tagged & set(self.allowed))


__all__ = ["AllowList", "ToolPolicy"]
```

- [ ] **Step 4: Aplicar o gate no toolset**

Em `src/agentos/agentic/agent_tools.py`, acrescentar o campo à dataclass `ToolDefinition`, depois de `read_only`:

```python
    # Coarse labels a policy can authorize or refuse as a family.
    policy_tags: tuple[str, ...] = ()
```

Acrescentar `policy_tags=("mutates",)` às definições de `write_file`, `edit_file`, `run_command`, `remember`, `create_agent` e `ask_agent`; e `policy_tags=("network",)` às de `fetch_url`, `web_search` e `browse_page`. Exemplo para `write_file`:

```python
                self.write_file, "filesystem", policy_tags=("mutates",),
```

Acrescentar o parâmetro ao `__init__`:

```python
        policy: object | None = None,
```

no corpo:

```python
        self._policy = policy
```

Em `definitions()`, filtrar depois de construir:

```python
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """The tool set is fixed for the lifetime of a turn, so build it once."""
        if self._definitions is None:
            built = self._build_definitions()
            if self._policy is not None:
                built = tuple(item for item in built if self._policy.allows(item.name, item.policy_tags))
            self._definitions = built
            self._by_name = {item.name: item for item in self._definitions}
        return self._definitions
```

`resolve` já levanta `AgentToolError` para nome ausente, e `invoke` já converte isso em `UNKNOWN_TOOL` — então uma tool negada é automaticamente recusada mesmo se o modelo insistir.

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_tool_policy.py tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 6: Expor na sessão**

Em `src/agentos/agentic/session.py`, acrescentar ao `__init__` de `TurnSession`:

```python
        tool_policy=None,
```

no corpo:

```python
        self.tool_policy = tool_policy
```

e em `_toolset`:

```python
            policy=self.tool_policy,
```

- [ ] **Step 7: Rodar a suíte completa e commit**

Run: `uv run pytest tests/unit/agentic tests/unit/workers tests/unit/tool_runtime -v`
Expected: PASS

```bash
git add src/agentos/agentic/tool_policy.py src/agentos/agentic/agent_tools.py src/agentos/agentic/session.py tests/unit/agentic/test_tool_policy.py
git commit -m "feat(agentic): authorize the agent tool set with a declarative policy"
```

---

## Verificação final do plano

- [ ] `uv run pytest tests/unit -q` — sem regressão nova
- [ ] Sem `AGENTOS_SEARCH_API_KEY` no ambiente, `web_search` não aparece em `definitions()`
- [ ] Sem Playwright instalado, `conversation_browser_for` devolve `None` e `browse_page` não aparece em `definitions()`
- [ ] `git grep -n "AGENTOS_SEARCH_API_KEY" src/` — a chave só é lida em `web_search.py`, nunca registrada em log, payload ou evento
