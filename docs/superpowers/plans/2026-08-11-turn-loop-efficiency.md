# Turn Loop Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cortar o custo de tokens e a latência de uma turn agêntica sem perder trabalho: cache de prompt, execução paralela de tools de leitura, fecho gracioso no limite de iterações, janela de contexto por orçamento e envelhecimento de resultados de tool.

**Architecture:** Todas as decisões de custo vivem em `AgenticTurnRuntime` (o único lugar que monta a requisição por iteração) e em `HTTPProviderStreamTransport` (o único lugar que fala HTTP com o provider). `AgentToolset` ganha apenas um dado declarativo — quais tools são de leitura — para o runtime poder paralelizá-las com segurança.

**Tech Stack:** Python 3.12, `concurrent.futures.ThreadPoolExecutor`, httpx, pytest.

## Global Constraints

- Nome público do produto é **Orin**; identificadores internos permanecem `agentos`.
- Nenhuma mudança pode alterar a ordem em que os resultados de tool aparecem no histórico enviado ao provider: a ordem é a das `tool_calls` retornadas pelo modelo.
- Nenhum segredo, header ou corpo bruto do provider pode cruzar a fronteira de `provider_stream.py` (contrato existente do módulo).
- Todo módulo novo/alterado começa com `from __future__ import annotations`.
- Rodar testes com `uv run pytest <caminho> -v`.

**Depende de:** Plano 1 Task 1 e Task 5 — a Task 2 daqui marca `search_files` como `read_only` e a Task 4 edita o `resolve` indexado. Se o Plano 1 ainda não estiver mesclado, omitir a linha de `search_files` na Task 2 e reconciliar depois.

---

### Task 1: Prompt caching e usage confiável no stream

**Files:**
- Modify: `src/agentos/agentic/provider_stream.py:202-237`
- Test: `tests/unit/agentic/test_provider_stream_payload.py`

**Interfaces:**
- Consumes: dict de requisição montado por `AgenticTurnRuntime.run` (`{"messages", "tools", "max_output_tokens", ...}`)
- Produces: payload Anthropic com `system` em blocos e `cache_control: {"type": "ephemeral"}` no último bloco de system e na última tool; payload OpenAI-compatível com `stream_options: {"include_usage": true}`.

**Por quê:** hoje o system prompt + os schemas das tools são reenviados por inteiro em cada uma das até 12 iterações da turn. O cache efêmero da Anthropic cobre todo o prefixo até o bloco marcado. No caminho OpenAI-compatível o cache é automático desde que o prefixo seja estável — a mudança ali é outra: sem `stream_options.include_usage` o provider não emite `usage` no stream, e `record_usage` nunca é chamado.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/agentic/test_provider_stream_payload.py`:

```python
from __future__ import annotations

import httpx
import pytest

from agentos.agentic.provider_stream import HTTPProviderStreamTransport


def _transport(provider: str, captured: list[dict]) -> HTTPProviderStreamTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.append(json.loads(request.content))
        return httpx.Response(200, text="data: [DONE]\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HTTPProviderStreamTransport(provider=provider, base_url="https://example.test", api_key="k", model="m", client=client)


def _request() -> dict[str, object]:
    return {
        "messages": [{"role": "system", "content": "you are orin"}, {"role": "user", "content": "hi"}],
        "tools": [
            {"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "write_file", "description": "write", "parameters": {"type": "object", "properties": {}}}},
        ],
        "max_output_tokens": 512,
    }


def test_anthropic_payload_marks_the_cacheable_prefix() -> None:
    captured: list[dict] = []
    list(_transport("anthropic", captured).stream(_request()))

    payload = captured[0]
    assert payload["system"] == [{"type": "text", "text": "you are orin", "cache_control": {"type": "ephemeral"}}]
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in payload["tools"][0]


def test_openai_payload_asks_for_usage_in_the_stream() -> None:
    captured: list[dict] = []
    list(_transport("openrouter", captured).stream(_request()))

    assert captured[0]["stream_options"] == {"include_usage": True}


def test_tool_choice_is_forwarded_when_present() -> None:
    captured: list[dict] = []
    request = {**_request(), "tool_choice": "none"}
    list(_transport("openrouter", captured).stream(request))

    assert captured[0]["tool_choice"] == "none"


def test_anthropic_tool_choice_uses_its_own_shape() -> None:
    captured: list[dict] = []
    request = {**_request(), "tool_choice": "none"}
    list(_transport("anthropic", captured).stream(request))

    assert captured[0]["tool_choice"] == {"type": "none"}
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_provider_stream_payload.py -v`
Expected: FAIL — `payload["system"]` é uma string e `stream_options` não existe

- [ ] **Step 3: Implementar o payload**

Em `src/agentos/agentic/provider_stream.py`, substituir o corpo de `stream` da linha `messages = list(...)` até `headers = {...}` (inclusive) por:

```python
    def stream(self, request: Mapping[str, object]) -> Iterator[NormalizedStreamItem]:
        messages = list(request.get("messages") or [])
        tools = list(request.get("tools") or [])
        tool_choice = request.get("tool_choice")
        if self.provider == "anthropic":
            system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
            messages = [item for item in messages if item.get("role") != "system"]
            payload: dict[str, object] = {"model": self.model, "max_tokens": int(request.get("max_output_tokens") or 1024), "messages": messages, "stream": True}
            if system:
                # A cached prefix must be a block, not a bare string. Marking the
                # last system block and the last tool covers system + every tool
                # definition, which is the part that repeats on every iteration.
                payload["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if tools:
                projected = [
                    {
                        "name": item.get("name") or item.get("function", {}).get("name"),
                        "description": item.get("description") or item.get("function", {}).get("description", ""),
                        "input_schema": item.get("input_schema") or item.get("function", {}).get("parameters", {}),
                    }
                    for item in tools
                ]
                projected[-1] = {**projected[-1], "cache_control": {"type": "ephemeral"}}
                payload["tools"] = projected
                if tool_choice is not None:
                    payload["tool_choice"] = {"type": str(tool_choice)}
            endpoint = f"{self.base_url}/messages"
            headers = {"x-api-key": self._api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        else:
            payload = {
                "model": self.model, "messages": messages, "stream": True,
                "max_tokens": int(request.get("max_output_tokens") or 1024),
                # Without this an OpenAI-compatible stream omits usage entirely
                # and the turn records no tokens at all.
                "stream_options": {"include_usage": True},
            }
            if tools:
                payload["tools"] = tools
                if tool_choice is not None:
                    payload["tool_choice"] = str(tool_choice)
            endpoint = f"{self.base_url}/chat/completions"
            headers = {"content-type": "application/json"}
            if self._api_key:
                headers["authorization"] = f"Bearer {self._api_key}"
```

O restante do método (o bloco `with self._client.stream(...)`) fica inalterado.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_provider_stream_payload.py tests/unit/agentic/test_agentic_runtime_loop.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/provider_stream.py tests/unit/agentic/test_provider_stream_payload.py
git commit -m "perf(agentic): cache the prompt prefix and request stream usage"
```

---

### Task 2: Execução paralela das tools de leitura

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py` (`ToolDefinition`, `AgentToolset`)
- Modify: `src/agentos/agentic/runtime.py:211-233` (`_run_toolset`)
- Test: `tests/unit/agentic/test_agent_tools.py`, `tests/unit/agentic/test_agentic_runtime_loop.py`

**Interfaces:**
- Consumes: `AgentToolset.invoke`
- Produces:
  - `ToolDefinition.read_only: bool` (default `False`)
  - `AgentToolset.is_read_only(name: str) -> bool`
  - `AgenticTurnRuntime._run_toolset` executa em paralelo as chamadas cujo `is_read_only` é `True`, preservando a ordem dos resultados

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_agent_tools.py`:

```python
def test_read_tools_are_declared_read_only_and_write_tools_are_not(toolset: AgentToolset) -> None:
    assert toolset.is_read_only("read_file")
    assert toolset.is_read_only("list_files")
    assert not toolset.is_read_only("write_file")
    assert not toolset.is_read_only("run_command")
    assert not toolset.is_read_only("unknown_tool")
```

E em `tests/unit/agentic/test_agentic_runtime_loop.py`:

```python
def test_read_only_calls_run_concurrently_and_keep_their_order() -> None:
    import threading
    import time

    barrier = threading.Barrier(2, timeout=5)

    class SlowToolset:
        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            # Both calls must be in flight at once or this times out.
            barrier.wait()
            time.sleep(0.01)
            return ToolOutcome("succeeded", f"{name} ok", f"content-{arguments['n']}")

    class TwoCallProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                return normalize_sse(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":['
                        '{"index":0,"id":"call-1","function":{"name":"read_file","arguments":"{\\"n\\":1}"}},'
                        '{"index":1,"id":"call-2","function":{"name":"read_file","arguments":"{\\"n\\":2}"}}'
                        ']},"finish_reason":"tool_calls"}]}',
                        "data: [DONE]",
                    ],
                    provider="openrouter",
                )
            return normalize_sse(['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")

    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=TwoCallProvider(), toolset=SlowToolset())

    result = runtime.run("turn-1")

    assert result.state == "completed"
    finished = [payload for state, payload in store.events if state == "tool_finished"]
    assert [item["invocation_id"] for item in finished] == ["call-1", "call-2"]
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py -k read_only tests/unit/agentic/test_agentic_runtime_loop.py -k concurrently -v`
Expected: FAIL — `AttributeError: 'AgentToolset' object has no attribute 'is_read_only'` e `threading.BrokenBarrierError` (timeout) no teste do runtime, porque a execução é serial

- [ ] **Step 3: Declarar `read_only` nas tools**

Em `src/agentos/agentic/agent_tools.py`, acrescentar o campo à dataclass:

```python
@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Callable[..., dict[str, Any]]
    # Drives the icon/label the UI shows for a grouped activity card.
    kind: str
    # A read-only tool has no workspace or network side effect, so several of
    # them may run at once without changing what any of them observes.
    read_only: bool = False

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": dict(self.parameters)}}
```

Marcar `read_only=True` nas definições de `read_file`, `list_files`, `search_files`, `fetch_url`, `recall`, `search_skills`, `list_skills` e `read_skill_resource` — acrescentando `read_only=True` como último argumento de cada `ToolDefinition` correspondente. Exemplo para `read_file`:

```python
                self.read_file, "filesystem", read_only=True,
```

`write_file`, `edit_file`, `run_command`, `remember`, `use_skill`, `create_agent` e `ask_agent` permanecem sem o argumento (default `False`). `use_skill` fica de fora de propósito: ele muta `self._loaded_skills`.

Adicionar o método público, logo após `resolve`:

```python
    def is_read_only(self, name: str) -> bool:
        try:
            return self.resolve(name).read_only
        except AgentToolError:
            return False
```

- [ ] **Step 4: Paralelizar no runtime**

Em `src/agentos/agentic/runtime.py`, acrescentar aos imports do topo:

```python
from concurrent.futures import ThreadPoolExecutor
```

E acrescentar a constante logo abaixo dos imports:

```python
MAX_PARALLEL_TOOLS = 4
```

Substituir `_run_toolset` inteiro por:

```python
    def _run_toolset(self, turn: dict[str, object], calls: list[dict[str, str]]) -> list[dict[str, object]]:
        """Execute the model's calls and return results it can actually read.

        Read-only calls in the same batch run concurrently; anything that can
        write to the workspace stays sequential so two calls never race on the
        same file. Results are emitted in the order the model requested them.
        """
        from .agent_tools import AgentToolError, parse_arguments

        prepared: list[tuple[str, str, dict[str, object] | None, str | None]] = []
        for call in calls:
            name = str(call.get("name") or "")
            call_id = str(call.get("id") or "")
            self._life(turn, "tool_started", tool_name=name, invocation_id=call_id)
            try:
                prepared.append((call_id, name, parse_arguments(call.get("arguments") or "{}"), None))
            except AgentToolError as error:
                prepared.append((call_id, name, None, str(error)))

        is_read_only = getattr(self.toolset, "is_read_only", None)
        parallel_indexes = [
            index for index, (_, name, arguments, error) in enumerate(prepared)
            if error is None and callable(is_read_only) and is_read_only(name)
        ]
        outcomes: dict[int, object] = {}
        if len(parallel_indexes) > 1:
            with ThreadPoolExecutor(max_workers=min(len(parallel_indexes), MAX_PARALLEL_TOOLS)) as pool:
                futures = {index: pool.submit(self.toolset.invoke, prepared[index][1], prepared[index][2]) for index in parallel_indexes}
                for index, future in futures.items():
                    outcomes[index] = future.result()

        results: list[dict[str, object]] = []
        for index, (call_id, name, arguments, error) in enumerate(prepared):
            if error is not None:
                self._life(turn, "tool_finished", tool_name=name, invocation_id=call_id, status="failed", summary=error, error_code="INVALID_ARGUMENTS")
                results.append({"id": call_id, "name": name, "status": "failed", "content": error})
                continue
            outcome = outcomes.get(index) or self.toolset.invoke(name, arguments)
            self._life(
                turn, "tool_finished", tool_name=name, invocation_id=call_id, status=outcome.status,
                summary=outcome.summary, error_code=outcome.error_code, tool_payload=dict(outcome.payload),
            )
            results.append({"id": call_id, "name": name, "status": outcome.status, "content": outcome.content})
        return results
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/agent_tools.py src/agentos/agentic/runtime.py tests/unit/agentic/test_agent_tools.py tests/unit/agentic/test_agentic_runtime_loop.py
git commit -m "perf(agentic): run read-only tool calls concurrently"
```

---

### Task 3: Fecho gracioso no limite de iterações

**Files:**
- Modify: `src/agentos/agentic/runtime.py:64-186` (`run`)
- Test: `tests/unit/agentic/test_agentic_runtime_loop.py`

**Interfaces:**
- Consumes: `AgenticLimits.max_iterations`, `tool_choice` no dict de requisição (Task 1)
- Produces: na última iteração permitida, a requisição carrega `tool_choice="none"` e uma mensagem final de sistema; o turn termina como `completed` com o texto produzido em vez de `failed/ITERATION_LIMIT`.

**Por quê:** hoje estourar o orçamento de iterações marca a turn como falha e o usuário não recebe nada, mesmo com 11 iterações de trabalho feito.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_agentic_runtime_loop.py`:

```python
def test_the_last_iteration_forbids_tools_and_returns_an_answer() -> None:
    class AlwaysToolsProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, request):
            self.calls.append(request)
            if request.get("tool_choice") == "none":
                return normalize_sse(['data: {"choices":[{"delta":{"content":"partial answer"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")
            return normalize_sse(
                [
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-%d","function":{"name":"read_file","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}' % len(self.calls),
                    "data: [DONE]",
                ],
                provider="openrouter",
            )

    class EchoToolset:
        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            return ToolOutcome("succeeded", "ok", "content")

    store = Store()
    provider = AlwaysToolsProvider()
    runtime = AgenticTurnRuntime(store=store, provider=provider, toolset=EchoToolset(), limits=AgenticLimits(max_iterations=3, max_actions=8))

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert store.deltas == ["partial answer"]
    assert provider.calls[-1]["tool_choice"] == "none"
    assert provider.calls[0].get("tool_choice") is None
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_agentic_runtime_loop.py -k last_iteration -v`
Expected: FAIL — `result.state == "failed"` com `ITERATION_LIMIT`

- [ ] **Step 3: Implementar o fecho**

Em `src/agentos/agentic/runtime.py`, acrescentar a constante junto de `MAX_PARALLEL_TOOLS`:

```python
CLOSING_INSTRUCTION = (
    "You have reached this turn's action budget. Do not request any more tools. "
    "Answer now with what you already accomplished, state plainly what is still missing, "
    "and say what the next step would be."
)
```

Dentro de `run`, substituir o bloco que monta `request` por:

```python
            final_iteration = self.limits.max_iterations is not None and iteration == self.limits.max_iterations
            window = self._request_messages(messages)
            if final_iteration:
                window = [*window, {"role": "system", "content": CLOSING_INSTRUCTION}]
            request = {
                "turn_id": turn_id, "provider": str(turn.get("provider", "")), "model": str(turn.get("model_id", "")),
                "messages": window, "tools": self._tool_schemas(turn),
                "max_output_tokens": min(self.limits.max_output_tokens, remaining_tokens),
            }
            if final_iteration:
                request["tool_choice"] = "none"
```

E, no bloco que trata as tool calls, ignorar chamadas emitidas apesar do `tool_choice`:

```python
            if (calls or (finish is not None and finish.value == "TOOL_CALLS")) and not final_iteration:
```

Por fim, substituir a linha final do laço:

```python
        return self._fail(turn, "ITERATION_LIMIT", self.limits.max_iterations or 0, action_count)
```

por:

```python
        # Reaching this point means the loop ended without any provider answer at
        # all; a turn that produced text has already returned "completed" above.
        return self._fail(turn, "ITERATION_LIMIT", self.limits.max_iterations or 0, action_count)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py tests/unit/agentic/test_agentic_runtime_loop.py
git commit -m "feat(agentic): answer with partial work instead of failing on the iteration limit"
```

---

### Task 4: Bloquear repetição de chamada que já falhou

**Files:**
- Modify: `src/agentos/agentic/runtime.py` (`run`, `_run_toolset`)
- Test: `tests/unit/agentic/test_agentic_runtime_loop.py`

**Interfaces:**
- Consumes: nada novo
- Produces: `AgenticTurnRuntime` mantém `self._failed_signatures: dict[str, str]` durante um `run`; uma chamada idêntica (mesmo nome + mesmos argumentos) a uma que já falhou não é reexecutada — o modelo recebe o erro anterior mais a instrução de mudar de abordagem.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_agentic_runtime_loop.py`:

```python
def test_an_identical_failing_call_is_not_executed_twice() -> None:
    class RepeatingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls <= 2:
                return normalize_sse(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-%d","function":{"name":"read_file","arguments":"{\\"path\\":\\"nope\\"}"}}]},"finish_reason":"tool_calls"}]}' % self.calls,
                        "data: [DONE]",
                    ],
                    provider="openrouter",
                )
            return normalize_sse(['data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")

    class CountingToolset:
        def __init__(self) -> None:
            self.invocations = 0

        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            self.invocations += 1
            return ToolOutcome("failed", "não encontrado", "file not found", {}, "TOOL_REFUSED")

    toolset = CountingToolset()
    runtime = AgenticTurnRuntime(store=Store(), provider=RepeatingProvider(), toolset=toolset)

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert toolset.invocations == 1
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_agentic_runtime_loop.py -k identical_failing -v`
Expected: FAIL — `toolset.invocations == 2`

- [ ] **Step 3: Implementar a detecção**

Em `src/agentos/agentic/runtime.py`, dentro de `run`, logo depois de `action_count = 0`, acrescentar:

```python
        self._failed_signatures: dict[str, str] = {}
```

Acrescentar o helper estático na classe:

```python
    @staticmethod
    def _signature(name: str, arguments: Mapping[str, object]) -> str:
        try:
            return f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)}"
        except (TypeError, ValueError):
            return f"{name}:{arguments!r}"
```

E, em `_run_toolset`, dentro do laço final que produz os resultados, substituir a linha `outcome = outcomes.get(index) or self.toolset.invoke(name, arguments)` por:

```python
            signature = self._signature(name, arguments)
            previous = getattr(self, "_failed_signatures", {}).get(signature)
            if previous is not None:
                content = (
                    f"{previous}\n\n[this exact call already failed in this turn; "
                    "it was not run again — change the arguments or use a different tool]"
                )
                self._life(turn, "tool_finished", tool_name=name, invocation_id=call_id, status="failed", summary="Chamada repetida ignorada", error_code="DUPLICATE_TOOL_CALL")
                results.append({"id": call_id, "name": name, "status": "failed", "content": content})
                continue
            outcome = outcomes.get(index) or self.toolset.invoke(name, arguments)
            if outcome.status == "failed":
                self._failed_signatures[signature] = outcome.content
```

Como as chamadas paralelas são submetidas antes desse laço, acrescentar o mesmo filtro na construção de `parallel_indexes`:

```python
        parallel_indexes = [
            index for index, (_, name, arguments, error) in enumerate(prepared)
            if error is None and callable(is_read_only) and is_read_only(name)
            and self._signature(name, arguments) not in getattr(self, "_failed_signatures", {})
        ]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py tests/unit/agentic/test_agentic_runtime_loop.py
git commit -m "fix(agentic): stop re-running a tool call that already failed"
```

---

### Task 5: Janela de contexto por orçamento de tokens

**Files:**
- Modify: `src/agentos/agentic/runtime.py:15-30` (`AgenticLimits`), `:64-68` e `:193-197` (`_request_messages`)
- Modify: `src/agentos/workers/chat.py:219`
- Test: `tests/unit/agentic/test_context_window.py`

**Interfaces:**
- Consumes: `AgenticLimits`
- Produces:
  - `AgenticLimits.max_context_tokens: int = 60_000`
  - `AgenticTurnRuntime._request_messages(messages: list[dict]) -> list[dict]` passa a cortar por orçamento estimado, sempre preservando a última mensagem `user` anterior ao laço (o pedido original) e marcando o que foi omitido.

**Por quê:** `messages[-32:]` é um corte cego. Quando o laço enche a janela com pares tool_call/tool_result, o pedido original do usuário é o primeiro a cair e o agente perde o objetivo no meio da execução.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/agentic/test_context_window.py`:

```python
from __future__ import annotations

from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


def _runtime(**limits) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(store=object(), provider=object(), system_prompt="prompt", limits=AgenticLimits(**limits))


def test_the_original_user_request_survives_a_full_window() -> None:
    messages = [{"role": "user", "content": "build the report"}]
    messages += [{"role": "assistant", "content": "x" * 4_000} for _ in range(60)]

    window = _runtime(max_context_tokens=2_000)._request_messages(messages)

    assert window[0]["role"] == "system"
    assert any(item.get("content") == "build the report" for item in window)
    assert len(window) < len(messages)


def test_omitted_messages_are_announced_not_silently_dropped() -> None:
    messages = [{"role": "user", "content": "build the report"}]
    messages += [{"role": "assistant", "content": "x" * 4_000} for _ in range(60)]

    window = _runtime(max_context_tokens=2_000)._request_messages(messages)

    assert any("earlier messages omitted" in str(item.get("content", "")) for item in window)


def test_a_short_conversation_is_returned_untouched() -> None:
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    window = _runtime(max_context_tokens=60_000)._request_messages(messages)

    assert window == [{"role": "system", "content": "prompt"}, *messages]
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_context_window.py -v`
Expected: FAIL — `TypeError: AgenticLimits.__init__() got an unexpected keyword argument 'max_context_tokens'`

- [ ] **Step 3: Acrescentar o limite**

Em `src/agentos/agentic/runtime.py`, na dataclass `AgenticLimits`, acrescentar o campo depois de `max_output_tokens`:

```python
    max_context_tokens: int = 60_000
```

E acrescentar a validação em `__post_init__`, dentro do bloco existente:

```python
        if self.max_output_tokens < 1 or self.max_context_tokens < 1_000:
            raise ValueError("agentic limits are invalid")
```

- [ ] **Step 4: Implementar a janela por orçamento**

Em `src/agentos/agentic/runtime.py`, substituir `_request_messages` por:

```python
    @staticmethod
    def _estimated_tokens(message: Mapping[str, object]) -> int:
        """Cheap upper-bound estimate; four characters per token is the usual rule."""
        try:
            payload = json.dumps(message, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload = str(message)
        return max(1, len(payload) // 4)

    def _request_messages(self, messages: list[dict[str, object]]) -> list[dict[str, object]]:
        """Keep the most recent exchange within budget without losing the ask.

        Dropping by message count loses the user's original request first, which
        is exactly the message the agent needs to still be working on. The first
        user message is pinned and the rest of the budget goes to the newest
        messages.
        """
        budget = self.limits.max_context_tokens
        pinned_index = next((index for index, item in enumerate(messages) if item.get("role") == "user"), None)
        pinned = messages[pinned_index] if pinned_index is not None else None
        available = budget - (self._estimated_tokens(pinned) if pinned is not None else 0)
        tail: list[dict[str, object]] = []
        for index in range(len(messages) - 1, -1, -1):
            if index == pinned_index:
                continue
            cost = self._estimated_tokens(messages[index])
            if cost > available:
                break
            available -= cost
            tail.append(messages[index])
        tail.reverse()
        omitted = len(messages) - len(tail) - (1 if pinned is not None else 0)
        window: list[dict[str, object]] = []
        if pinned is not None:
            window.append(pinned)
        if omitted > 0:
            window.append({"role": "system", "content": f"[{omitted} earlier messages omitted to stay within the context budget; re-read files or re-run searches if you need their content]"})
        window.extend(tail)
        if not self.system_prompt:
            return window
        return [{"role": "system", "content": self.system_prompt}, *window]
```

Em `run`, substituir a linha que carrega o histórico:

```python
        messages = list(self.store.history_for_turn(turn))[-32:]
```

por:

```python
        messages = list(self.store.history_for_turn(turn))
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic/test_context_window.py tests/unit/agentic/test_agentic_runtime_loop.py -v`
Expected: PASS

- [ ] **Step 6: Configurar o orçamento no worker**

Em `src/agentos/workers/chat.py:219`, substituir a construção de limites por:

```python
            limits=AgenticLimits(deadline=TURN_DEADLINE, max_iterations=configured_limits["max_iterations"], max_actions=24, max_output_tokens=4096, max_context_tokens=60_000),
```

- [ ] **Step 7: Rodar a suíte de workers**

Run: `uv run pytest tests/unit/workers tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/agentos/agentic/runtime.py src/agentos/workers/chat.py tests/unit/agentic/test_context_window.py
git commit -m "perf(agentic): budget the context window and pin the user request"
```

---

### Task 6: Envelhecer resultados de tool dentro da turn

**Files:**
- Modify: `src/agentos/agentic/runtime.py` (`run`, novo helper `_age_tool_results`)
- Test: `tests/unit/agentic/test_context_window.py`

**Interfaces:**
- Consumes: `_tool_result_message` (formatos OpenAI e Anthropic já existentes)
- Produces: `AgenticTurnRuntime._age_tool_results(messages: list[dict], keep_recent: int) -> None` — comprime *in place* o conteúdo de mensagens de resultado de tool que não estejam entre as `keep_recent` mais recentes.

**Por quê:** o `stdout` de 12.000 caracteres de um build continua sendo reenviado em todas as iterações seguintes da mesma turn, mesmo depois de já ter sido lido.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/unit/agentic/test_context_window.py`:

```python
from agentos.agentic.runtime import AgenticTurnRuntime


def test_old_tool_results_are_compressed_but_recent_ones_are_kept() -> None:
    messages = [
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_call_id": "a", "content": "old " * 500},
        {"role": "tool", "tool_call_id": "b", "content": "new " * 500},
    ]

    AgenticTurnRuntime._age_tool_results(messages, keep_recent=1)

    assert "compressed" in messages[1]["content"]
    assert len(messages[1]["content"]) < 800
    assert messages[2]["content"] == "new " * 500


def test_anthropic_tool_results_are_compressed_in_their_block_shape() -> None:
    messages = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "old " * 500}]},
        {"role": "assistant", "content": "thinking"},
    ]

    AgenticTurnRuntime._age_tool_results(messages, keep_recent=0)

    assert "compressed" in messages[0]["content"][0]["content"]
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `uv run pytest tests/unit/agentic/test_context_window.py -k tool_results -v`
Expected: FAIL — `AttributeError: type object 'AgenticTurnRuntime' has no attribute '_age_tool_results'`

- [ ] **Step 3: Implementar o envelhecimento**

Em `src/agentos/agentic/runtime.py`, acrescentar a constante junto das outras:

```python
AGED_TOOL_RESULT_CHARS = 400
```

E o método estático na classe:

```python
    @staticmethod
    def _is_tool_result(message: Mapping[str, object]) -> bool:
        if message.get("role") == "tool":
            return True
        content = message.get("content")
        return isinstance(content, list) and any(isinstance(block, Mapping) and block.get("type") == "tool_result" for block in content)

    @classmethod
    def _compress(cls, text: str) -> str:
        if len(text) <= AGED_TOOL_RESULT_CHARS:
            return text
        return f"{text[:AGED_TOOL_RESULT_CHARS]}\n[compressed: {len(text)} characters total; re-run the tool if you need the rest]"

    @classmethod
    def _age_tool_results(cls, messages: list[dict[str, object]], keep_recent: int) -> None:
        """Shrink tool output the model has already had a chance to read.

        The full result is what the model needed on the iteration right after
        the call. Re-sending it on every later iteration is pure cost, so older
        results keep only their head plus a pointer back to the tool.
        """
        indexes = [index for index, message in enumerate(messages) if cls._is_tool_result(message)]
        for index in indexes[: max(0, len(indexes) - max(0, int(keep_recent)))]:
            message = messages[index]
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = cls._compress(content)
            elif isinstance(content, list):
                message["content"] = [
                    {**block, "content": cls._compress(str(block.get("content", "")))}
                    if isinstance(block, Mapping) and block.get("type") == "tool_result" else block
                    for block in content
                ]
```

Chamar o helper em `run`, logo antes de `self._life(turn, "running")` no bloco que fecha o tratamento das tool calls:

```python
                messages.append(self._assistant_tool_message(turn, text_parts, calls))
                messages.extend(self._tool_result_message(turn, result) for result in results)
                self._age_tool_results(messages, keep_recent=len(results))
                self._life(turn, "running")
                continue
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/unit/agentic -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/runtime.py tests/unit/agentic/test_context_window.py
git commit -m "perf(agentic): compress tool results the model already read"
```

---

## Verificação final do plano

- [ ] `uv run pytest tests/unit/agentic tests/unit/workers -v` — PASS
- [ ] `uv run pytest tests/unit -q` — sem regressão nova
- [ ] Revisar o diff de `provider_stream.py`: nenhum header, credencial ou corpo bruto do provider vaza para fora do módulo
- [ ] Revisar `runtime.py`: a ordem dos resultados de tool continua igual à ordem das chamadas do modelo
