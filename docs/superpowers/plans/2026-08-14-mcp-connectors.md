# MCP Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o Orin conecte servidores MCP (stdio local e HTTP remoto), exponha as tools remotas ao agente dentro de um turno, e permita que o próprio agente proponha uma configuração de servidor que o usuário aprova em um card no chat.

**Architecture:** Um novo pacote `src/agentos/mcp/` implementa cliente JSON-RPC, dois transportes, um registry durável e um adapter que traduz cada tool remota em uma `ToolDefinition` nativa com nome namespaced `mcp__<slug>__<tool>`. O worker monta as definições a partir do **cache de descoberta** (zero I/O na construção do toolset); a sessão só é aberta na primeira chamada e fechada no fim do turno. Segredos nunca passam por argumento de tool: `configure_mcp` cria o servidor em `pending_approval` e o usuário digita as credenciais em um card do chat que fala com um endpoint dedicado.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core + Alembic, `httpx` (já dependência), `subprocess` da stdlib para stdio, `cryptography.Fernet` via `ProviderSecretCipher`, React 18 + TypeScript no cliente.

---

## Decisões de design (leia antes da Task 1)

Esta é a implementação pragmática da [RFC 903](../../architecture/900-extensibility/903-mcp-future.md). A RFC é normativa e enterprise; este plano entrega o subset local-first mantendo os invariantes que importam. O que a RFC exige e este plano **implementa**: opt-in explícito por servidor, descoberta que não cria grant, descriptors remotos tratados como não confiáveis, credencial como referência cifrada, egress controlado, cancelamento cooperativo, eventos auditáveis, e fail-closed. O que este plano **adia** conscientemente (registrar como follow-up, não como esquecimento): `binding_version` por tool, quarentena automática, reconciliação de efeito `UNKNOWN`, e resource/prompt surfaces do MCP — v1 expõe **somente tools**.

| Decisão | Escolha | Porquê |
| --- | --- | --- |
| Transportes | `stdio` + `http` (streamable HTTP com fallback JSON) | stdio cobre os servidores locais do ecossistema; HTTP cobre os hospedados. |
| Nome da tool | `mcp__<slug>__<tool>` | Namespace impede colisão com tool nativa e deixa a origem legível no card de atividade. |
| Descoberta | Cacheada em `mcp_server_tools`, revalidada por `tools_digest` | O toolset de um turno é construído sem rede; um servidor lento não atrasa o primeiro token. |
| Segredos | Blob JSON único cifrado com `ProviderSecretCipher` | Reusa a chave e o padrão já auditados de provider credentials. Nomes das chaves ficam em claro para a UI; valores nunca. |
| Aprovação | Card no chat → endpoint dedicado | O modelo propõe a forma; o usuário fornece o valor. Prompt injection não consegue exfiltrar nem ativar nada. |
| Política de rede | Reusa `_public_url` de `agent_tools.py` | Uma implementação de SSRF, não duas. |
| Comando stdio | Allowlist de executáveis, `shell=False`, env explícito | Um servidor MCP é um processo local; o modelo não escolhe o binário livremente. |

### Estados do servidor

```
draft ──► pending_approval ──► active ⇄ disabled
                │                 │
                └──► error ◄──────┘
```

`pending_approval` é o estado criado pela tool do agente. `active` só é alcançado por um `POST /approve` autenticado do usuário que **conectou com sucesso** e cacheou as tools. Falha de conexão em `/approve` mantém `pending_approval` e devolve o erro.

## Estrutura de arquivos

**Backend — criar:**

| Arquivo | Responsabilidade |
| --- | --- |
| `src/agentos/mcp/__init__.py` | Exports públicos do pacote. |
| `src/agentos/mcp/models.py` | `McpTransport`, `McpServerState`, `McpServerConfig`, `McpToolDescriptor`, `slugify`, `tools_digest`. Sem I/O. |
| `src/agentos/mcp/catalog.py` | Catálogo curado de servidores conhecidos (`McpCatalogEntry`) que o agente consulta para explicar ao usuário o que é necessário. |
| `src/agentos/mcp/protocol.py` | Frames JSON-RPC 2.0: `request`, `notification`, parse de resposta, `McpProtocolError`. |
| `src/agentos/mcp/transport_stdio.py` | Subprocesso NDJSON, allowlist de comando, env explícito, encerramento de árvore de processo. |
| `src/agentos/mcp/transport_http.py` | POST JSON-RPC, `Mcp-Session-Id`, resposta SSE ou JSON, guarda SSRF. |
| `src/agentos/mcp/client.py` | `McpClient`: `initialize`, `list_tools`, `call_tool`, `close`. Agnóstico de transporte. |
| `src/agentos/mcp/sanitize.py` | Saneamento de descriptor remoto: schema, profundidade, tamanho, nome. |
| `src/agentos/mcp/toolset.py` | `McpToolProvider`: cache → `ToolDefinition[]`, sessão preguiçosa, tradução de resultado. |
| `src/agentos/mcp/service.py` | `McpServerService`: CRUD, aprovação, teste, descoberta. Dono das regras de estado. |
| `src/agentos/persistence/postgres/mcp.py` | Adapter SQL do service (mesmo padrão de `postgres/skills.py`). |
| `src/agentos/persistence/postgres/migrations/versions/0034_mcp_servers.py` | Tabelas `mcp_servers` e `mcp_server_tools`. |

**Backend — modificar:**

| Arquivo | Mudança |
| --- | --- |
| `src/agentos/persistence/postgres/schema.py` | Declarar as duas tabelas novas. |
| `src/agentos/agentic/agent_tools.py` | Aceitar `mcp_provider`; adicionar `list_mcp_catalog`, `list_mcp_servers`, `configure_mcp`, `test_mcp_server`; estender `close()`. |
| `src/agentos/agentic/session.py` | Repassar `mcp_provider` ao `_toolset`. |
| `src/agentos/workers/chat.py` | Construir `McpToolProvider` a partir do engine e injetar na `TurnSession`. |
| `src/agentos/api/gateway.py` | Rotas `/v1/mcp/*`. |

**Frontend — criar:** `frontend/src/api/mcp.ts`, `frontend/src/features/mcp/McpSection.tsx`, `McpServerCard.tsx`, `McpServerForm.tsx`, `frontend/src/features/conversations/McpApprovalCard.tsx`.

**Frontend — modificar:** `frontend/src/features/conversations/ActivityCard.tsx` (renderizar o card de aprovação), rotas e navegação (entregue pelo plano de Settings).

---

### Task 1: Modelos e identidade de servidor

**Files:**
- Create: `src/agentos/mcp/__init__.py`, `src/agentos/mcp/models.py`
- Test: `tests/unit/mcp/__init__.py`, `tests/unit/mcp/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_models.py
import pytest

from agentos.mcp.models import (
    McpServerConfig, McpServerState, McpToolDescriptor, McpTransport,
    qualified_tool_name, slugify, tools_digest,
)


def test_slugify_produces_a_bounded_lowercase_identifier():
    assert slugify("Notion Workspace") == "notion-workspace"
    assert slugify("  GitHub  ") == "github"
    assert len(slugify("x" * 200)) <= 32


def test_slugify_rejects_a_name_without_usable_characters():
    with pytest.raises(ValueError):
        slugify("***")


def test_qualified_tool_name_namespaces_the_remote_tool():
    assert qualified_tool_name("notion", "search") == "mcp__notion__search"


def test_tools_digest_is_stable_and_order_independent():
    first = McpToolDescriptor(name="a", description="d", input_schema={"type": "object"})
    second = McpToolDescriptor(name="b", description="e", input_schema={"type": "object"})
    assert tools_digest((first, second)) == tools_digest((second, first))
    changed = McpToolDescriptor(name="a", description="d2", input_schema={"type": "object"})
    assert tools_digest((first, second)) != tools_digest((changed, second))


def test_stdio_config_requires_a_command_and_http_requires_a_url():
    with pytest.raises(ValueError):
        McpServerConfig(server_id="s1", user_id="u1", slug="x", display_name="X",
                        transport=McpTransport.STDIO, command=None)
    with pytest.raises(ValueError):
        McpServerConfig(server_id="s1", user_id="u1", slug="x", display_name="X",
                        transport=McpTransport.HTTP, url=None)


def test_a_new_config_starts_in_pending_approval():
    config = McpServerConfig(server_id="s1", user_id="u1", slug="x", display_name="X",
                             transport=McpTransport.HTTP, url="https://mcp.example.com/v1")
    assert config.state is McpServerState.PENDING_APPROVAL
    assert config.is_usable is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_models.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.mcp'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/mcp/models.py
"""Value types for MCP server configuration and remote tool descriptors.

A descriptor arriving from a remote server is untrusted data (RFC 903): these
types only carry it, never grant anything because of it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

MAX_SLUG_LENGTH = 32
TOOL_NAME_PREFIX = "mcp"


class McpTransport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"


class McpServerState(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


def slugify(value: str) -> str:
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")
    if not slug:
        raise ValueError("mcp server name does not produce an identifier")
    return slug[:MAX_SLUG_LENGTH].rstrip("-")


def qualified_tool_name(slug: str, tool_name: str) -> str:
    return f"{TOOL_NAME_PREFIX}__{slug}__{tool_name}"


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    name: str
    description: str
    input_schema: Mapping[str, Any]


def tools_digest(tools: tuple[McpToolDescriptor, ...]) -> str:
    payload = sorted(
        (item.name, item.description, json.dumps(item.input_schema, sort_keys=True))
        for item in tools
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    server_id: str
    user_id: str
    slug: str
    display_name: str
    transport: McpTransport
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    # Names only. Values live encrypted and never enter this type.
    secret_names: tuple[str, ...] = ()
    catalog_id: str | None = None
    tool_allowlist: tuple[str, ...] | None = None
    state: McpServerState = McpServerState.PENDING_APPROVAL
    state_reason: str = ""
    protocol_version: str = ""
    tools_digest: str = ""

    def __post_init__(self) -> None:
        if self.transport is McpTransport.STDIO and not self.command:
            raise ValueError("a stdio mcp server requires a command")
        if self.transport is McpTransport.HTTP and not self.url:
            raise ValueError("an http mcp server requires a url")
        if self.slug != slugify(self.slug):
            raise ValueError("mcp server slug is not normalized")

    @property
    def is_usable(self) -> bool:
        return self.state is McpServerState.ACTIVE


__all__ = [
    "MAX_SLUG_LENGTH", "McpServerConfig", "McpServerState", "McpToolDescriptor",
    "McpTransport", "qualified_tool_name", "slugify", "tools_digest",
]
```

```python
# src/agentos/mcp/__init__.py
from .models import (
    McpServerConfig, McpServerState, McpToolDescriptor, McpTransport,
    qualified_tool_name, slugify, tools_digest,
)

__all__ = [
    "McpServerConfig", "McpServerState", "McpToolDescriptor", "McpTransport",
    "qualified_tool_name", "slugify", "tools_digest",
]
```

Crie `tests/unit/mcp/__init__.py` vazio.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_models.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp tests/unit/mcp
git commit -m "feat(mcp): add server configuration and tool descriptor value types"
```

---

### Task 2: Saneamento de descriptor remoto

Um servidor MCP pode devolver um schema recursivo, gigante ou com um nome que colide com uma tool nativa. A RFC 903 exige rejeitar isso antes de qualquer uso.

**Files:**
- Create: `src/agentos/mcp/sanitize.py`
- Test: `tests/unit/mcp/test_sanitize.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_sanitize.py
import pytest

from agentos.mcp.sanitize import UntrustedDescriptorRejected, sanitize_tool_descriptors


def test_a_well_formed_descriptor_survives():
    tools = sanitize_tool_descriptors([
        {"name": "search", "description": "Search pages", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
    ])
    assert [item.name for item in tools] == ["search"]
    assert tools[0].input_schema["type"] == "object"


def test_a_descriptor_without_an_object_schema_is_dropped():
    assert sanitize_tool_descriptors([{"name": "x", "description": "", "inputSchema": {"type": "string"}}]) == ()


def test_a_name_that_is_not_a_safe_identifier_is_dropped():
    assert sanitize_tool_descriptors([{"name": "rm -rf", "description": "", "inputSchema": {"type": "object"}}]) == ()


def test_a_deeply_nested_schema_is_dropped():
    schema: dict = {"type": "object", "properties": {}}
    cursor = schema
    for _ in range(12):
        cursor["properties"]["next"] = {"type": "object", "properties": {}}
        cursor = cursor["properties"]["next"]
    assert sanitize_tool_descriptors([{"name": "deep", "description": "", "inputSchema": schema}]) == ()


def test_the_descriptor_batch_is_bounded():
    payload = [{"name": f"t{index}", "description": "", "inputSchema": {"type": "object"}} for index in range(200)]
    with pytest.raises(UntrustedDescriptorRejected):
        sanitize_tool_descriptors(payload)


def test_the_description_is_truncated_rather_than_dropped():
    tools = sanitize_tool_descriptors([{"name": "t", "description": "x" * 5000, "inputSchema": {"type": "object"}}])
    assert len(tools[0].description) == 1024
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_sanitize.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.mcp.sanitize'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/mcp/sanitize.py
"""Normalize untrusted tool descriptors before they reach the model.

A remote server controls this payload. Anything the runtime cannot bound is
dropped: a missing tool is a visible absence, an unbounded schema is a bug the
provider call pays for.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from .models import McpToolDescriptor

MAX_TOOLS_PER_SERVER = 128
MAX_DESCRIPTION_CHARS = 1024
MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_BYTES = 24_000
_TOOL_NAME = re.compile(r"[a-zA-Z0-9_.-]{1,64}")


class UntrustedDescriptorRejected(ValueError):
    """The whole batch is unusable, not just one item."""


def _depth(value: Any, level: int = 0) -> int:
    if level > MAX_SCHEMA_DEPTH:
        return level
    if isinstance(value, Mapping):
        return max((_depth(item, level + 1) for item in value.values()), default=level)
    if isinstance(value, list):
        return max((_depth(item, level + 1) for item in value), default=level)
    return level


def sanitize_tool_descriptors(payload: Iterable[Mapping[str, Any]]) -> tuple[McpToolDescriptor, ...]:
    items = list(payload)
    if len(items) > MAX_TOOLS_PER_SERVER:
        raise UntrustedDescriptorRejected(
            f"the server published {len(items)} tools; the limit is {MAX_TOOLS_PER_SERVER}"
        )
    accepted: list[McpToolDescriptor] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "")
        if not _TOOL_NAME.fullmatch(name) or name in seen:
            continue
        schema = raw.get("inputSchema") or raw.get("input_schema")
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            continue
        if _depth(schema) > MAX_SCHEMA_DEPTH:
            continue
        try:
            encoded = json.dumps(schema)
        except (TypeError, ValueError):
            continue
        if len(encoded) > MAX_SCHEMA_BYTES:
            continue
        seen.add(name)
        accepted.append(McpToolDescriptor(
            name=name,
            description=str(raw.get("description") or "")[:MAX_DESCRIPTION_CHARS],
            input_schema=json.loads(encoded),
        ))
    return tuple(accepted)


__all__ = [
    "MAX_DESCRIPTION_CHARS", "MAX_SCHEMA_DEPTH", "MAX_TOOLS_PER_SERVER",
    "UntrustedDescriptorRejected", "sanitize_tool_descriptors",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_sanitize.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp/sanitize.py tests/unit/mcp/test_sanitize.py
git commit -m "feat(mcp): reject unbounded or unsafe remote tool descriptors"
```

---

### Task 3: Frames JSON-RPC

**Files:**
- Create: `src/agentos/mcp/protocol.py`
- Test: `tests/unit/mcp/test_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_protocol.py
import pytest

from agentos.mcp.protocol import McpProtocolError, notification, parse_response, request


def test_request_carries_an_explicit_id_and_version():
    assert request(1, "tools/list", {"cursor": None}) == {
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"cursor": None},
    }


def test_notification_has_no_id():
    assert "id" not in notification("notifications/initialized")


def test_parse_response_returns_the_result_for_a_matching_id():
    assert parse_response({"jsonrpc": "2.0", "id": 7, "result": {"tools": []}}, expected_id=7) == {"tools": []}


def test_parse_response_raises_on_an_error_frame():
    with pytest.raises(McpProtocolError) as error:
        parse_response({"jsonrpc": "2.0", "id": 7, "error": {"code": -32601, "message": "no such method"}}, expected_id=7)
    assert "no such method" in str(error.value)


def test_parse_response_raises_when_the_id_does_not_match():
    with pytest.raises(McpProtocolError):
        parse_response({"jsonrpc": "2.0", "id": 9, "result": {}}, expected_id=7)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_protocol.py -q`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/mcp/protocol.py
"""Minimal JSON-RPC 2.0 framing for MCP. No transport knowledge here."""
from __future__ import annotations

from typing import Any, Mapping

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "orin", "version": "1"}


class McpProtocolError(RuntimeError):
    """The peer answered with an error frame or an unusable envelope."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def request(request_id: int, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        frame["params"] = dict(params)
    return frame


def notification(method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        frame["params"] = dict(params)
    return frame


def parse_response(frame: Mapping[str, Any], *, expected_id: int) -> dict[str, Any]:
    if not isinstance(frame, Mapping) or frame.get("jsonrpc") != "2.0":
        raise McpProtocolError("the peer did not answer with a JSON-RPC 2.0 envelope")
    if frame.get("id") != expected_id:
        raise McpProtocolError(f"expected a response for request {expected_id}, got {frame.get('id')!r}")
    if "error" in frame:
        error = frame["error"] if isinstance(frame["error"], Mapping) else {}
        raise McpProtocolError(str(error.get("message") or "the server refused the request")[:512],
                               code=error.get("code") if isinstance(error.get("code"), int) else None)
    result = frame.get("result")
    if not isinstance(result, Mapping):
        raise McpProtocolError("the response carried no result object")
    return dict(result)


__all__ = ["CLIENT_INFO", "PROTOCOL_VERSION", "McpProtocolError", "notification", "parse_response", "request"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_protocol.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp/protocol.py tests/unit/mcp/test_protocol.py
git commit -m "feat(mcp): add JSON-RPC framing for the MCP client"
```

---

### Task 4: Transporte stdio com allowlist de comando

**Files:**
- Create: `src/agentos/mcp/transport_stdio.py`
- Test: `tests/unit/mcp/test_transport_stdio.py`

- [ ] **Step 1: Write the failing test**

O teste usa um servidor de eco escrito em Python puro, então não depende de node nem de rede.

```python
# tests/unit/mcp/test_transport_stdio.py
import sys

import pytest

from agentos.mcp.transport_stdio import StdioTransport, StdioTransportRefused

ECHO_SERVER = """
import json, sys
for line in sys.stdin:
    frame = json.loads(line)
    if "id" not in frame:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": {"echo": frame["method"]}}) + "\\n")
    sys.stdout.flush()
"""


def test_a_command_outside_the_allowlist_is_refused():
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="curl", args=("https://example.com",), env={})


def test_a_command_carrying_shell_metacharacters_is_refused():
    with pytest.raises(StdioTransportRefused):
        StdioTransport(command="npx", args=("thing; rm -rf /",), env={})


def test_the_transport_round_trips_a_frame(tmp_path):
    script = tmp_path / "echo_server.py"
    script.write_text(ECHO_SERVER, encoding="utf-8")
    transport = StdioTransport(command=sys.executable, args=(str(script),), env={}, allow_any_command=True)
    transport.open()
    try:
        assert transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"}) == {
            "jsonrpc": "2.0", "id": 1, "result": {"echo": "ping"},
        }
    finally:
        transport.close()


def test_close_is_idempotent(tmp_path):
    script = tmp_path / "echo_server.py"
    script.write_text(ECHO_SERVER, encoding="utf-8")
    transport = StdioTransport(command=sys.executable, args=(str(script),), env={}, allow_any_command=True)
    transport.open()
    transport.close()
    transport.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_transport_stdio.py -q`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`_terminate_process_tree` já existe em `agent_tools.py:174` e trata o caso Windows; reutilize-o em vez de escrever outro.

```python
# src/agentos/mcp/transport_stdio.py
"""Run a local MCP server as a child process speaking NDJSON on stdio.

The model never chooses the binary: only launchers from ALLOWED_COMMANDS may
start, the argument vector is passed without a shell, and the child gets an
explicit environment instead of the worker's own.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from typing import Any, Mapping

from agentos.agentic.agent_tools import _terminate_process_tree

ALLOWED_COMMANDS = frozenset({"npx", "uvx", "node", "python", "python3", "uv", "deno", "bun"})
FORBIDDEN_CHARACTERS = frozenset(";&|`$><\n\r")
DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_LINE_BYTES = 4_000_000


class StdioTransportRefused(RuntimeError):
    """The requested process is not something this host will start."""


class StdioTransportError(RuntimeError):
    """The child process died or answered with an unusable frame."""


class StdioTransport:
    kind = "stdio"

    def __init__(self, *, command: str, args: tuple[str, ...], env: Mapping[str, str],
                 cwd: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 allow_any_command: bool = False) -> None:
        base = os.path.basename(command).lower().removesuffix(".exe").removesuffix(".cmd")
        if not allow_any_command and base not in ALLOWED_COMMANDS:
            raise StdioTransportRefused(
                f"'{command}' is not an allowed MCP launcher. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}."
            )
        for value in (command, *args):
            if FORBIDDEN_CHARACTERS & set(value):
                raise StdioTransportRefused("the command line carries shell metacharacters")
        self._command = command
        self._args = tuple(args)
        self._env = dict(env)
        self._cwd = cwd
        self._timeout = timeout
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def open(self) -> None:
        if self._process is not None:
            return
        executable = shutil.which(self._command) or self._command
        # A deliberately small environment: PATH so the launcher resolves its
        # own runtime, plus the secrets this server was approved with.
        environment = {"PATH": os.environ.get("PATH", ""), "SystemRoot": os.environ.get("SystemRoot", ""), **self._env}
        try:
            self._process = subprocess.Popen(
                [executable, *self._args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self._cwd, env=environment, shell=False, bufsize=0,
            )
        except OSError as error:
            raise StdioTransportError(f"the MCP server process could not start: {error}") from error

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise StdioTransportError("the MCP server process is not running")
        payload = (json.dumps(frame) + "\n").encode()
        with self._lock:
            try:
                process.stdin.write(payload)
                process.stdin.flush()
            except OSError as error:
                raise StdioTransportError(f"the MCP server closed its input: {error}") from error
            if "id" not in frame:
                return None
            line = process.stdout.readline(MAX_LINE_BYTES)
        if not line:
            raise StdioTransportError("the MCP server closed before answering")
        try:
            return json.loads(line)
        except json.JSONDecodeError as error:
            raise StdioTransportError("the MCP server answered with invalid JSON") from error

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        _terminate_process_tree(process)


__all__ = ["ALLOWED_COMMANDS", "StdioTransport", "StdioTransportError", "StdioTransportRefused"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_transport_stdio.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp/transport_stdio.py tests/unit/mcp/test_transport_stdio.py
git commit -m "feat(mcp): add a stdio transport with a launcher allowlist"
```

---

### Task 5: Transporte HTTP com guarda de SSRF

**Files:**
- Create: `src/agentos/mcp/transport_http.py`
- Test: `tests/unit/mcp/test_transport_http.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_transport_http.py
import httpx
import pytest

from agentos.mcp.transport_http import HttpTransport, HttpTransportRefused


def test_a_loopback_url_is_refused():
    with pytest.raises(HttpTransportRefused):
        HttpTransport(url="http://127.0.0.1:9000/mcp", headers={})


def test_a_plain_http_public_url_is_refused():
    with pytest.raises(HttpTransportRefused):
        HttpTransport(url="http://mcp.example.com/v1", headers={})


def test_the_transport_posts_a_frame_and_returns_the_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json, text/event-stream"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
                              headers={"Mcp-Session-Id": "abc"})

    transport = HttpTransport(url="https://mcp.example.com/v1", headers={},
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {"ok": True}
    assert transport.session_id == "abc"


def test_an_sse_response_body_is_decoded_to_the_first_data_frame():
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    transport = HttpTransport(url="https://mcp.example.com/v1", headers={},
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {"ok": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_transport_http.py -q`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/mcp/transport_http.py
"""Streamable-HTTP transport for a remote MCP server.

Endpoint policy is the one the agent's own fetch_url already enforces: public
HTTPS only. A private, loopback or link-local endpoint is refused before the
first byte leaves the machine.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import httpx

from agentos.agentic.agent_tools import _public_url

DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_RESPONSE_BYTES = 4_000_000


class HttpTransportRefused(RuntimeError):
    """The endpoint is not allowed by the network policy."""


class HttpTransportError(RuntimeError):
    """The server was reachable but the exchange failed."""


class HttpTransport:
    kind = "http"

    def __init__(self, *, url: str, headers: Mapping[str, str],
                 client: httpx.Client | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        try:
            normalized = _public_url(url, resolve_dns=True)
        except Exception as error:  # the policy raises its own refusal type
            raise HttpTransportRefused(str(error)) from error
        if not normalized.lower().startswith("https://"):
            raise HttpTransportRefused("an MCP endpoint must use https")
        self._url = normalized
        self._headers = dict(headers)
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self.session_id: str | None = None

    def open(self) -> None:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, follow_redirects=False)

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        self.open()
        assert self._client is not None
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            **self._headers,
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        try:
            response = self._client.post(self._url, json=dict(frame), headers=headers, timeout=self._timeout)
        except httpx.HTTPError as error:
            raise HttpTransportError(f"the MCP endpoint did not answer: {error}") from error
        self.session_id = response.headers.get("mcp-session-id") or self.session_id
        if response.status_code >= 400:
            raise HttpTransportError(f"the MCP endpoint answered {response.status_code}")
        if "id" not in frame:
            return None
        body = response.content[:MAX_RESPONSE_BYTES].decode("utf-8", "replace")
        if "text/event-stream" in response.headers.get("content-type", ""):
            body = _first_sse_payload(body)
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise HttpTransportError("the MCP endpoint answered with invalid JSON") from error

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
        self._client = None


def _first_sse_payload(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("data:"):
            return line[5:].strip()
    raise HttpTransportError("the event stream carried no data frame")


__all__ = ["HttpTransport", "HttpTransportError", "HttpTransportRefused"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_transport_http.py -q`
Expected: `4 passed`

> Se `_public_url` não aceitar `resolve_dns` para hosts inexistentes no ambiente de teste, adicione ao teste `monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)` **somente** nos dois testes de round-trip, mantendo os dois testes de recusa contra a função real.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp/transport_http.py tests/unit/mcp/test_transport_http.py
git commit -m "feat(mcp): add an https-only streamable transport for remote servers"
```

---

### Task 6: Cliente MCP

**Files:**
- Create: `src/agentos/mcp/client.py`
- Test: `tests/unit/mcp/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_client.py
from typing import Any, Mapping

import pytest

from agentos.mcp.client import McpClient
from agentos.mcp.protocol import McpProtocolError


class FakeTransport:
    kind = "fake"

    def __init__(self, responses: dict[str, Mapping[str, Any]]) -> None:
        self.responses = responses
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def open(self) -> None:
        pass

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        self.sent.append(dict(frame))
        if "id" not in frame:
            return None
        return {"jsonrpc": "2.0", "id": frame["id"], "result": self.responses[str(frame["method"])]}

    def close(self) -> None:
        self.closed = True


def _transport(**overrides: Mapping[str, Any]) -> FakeTransport:
    return FakeTransport({
        "initialize": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "demo"}, "capabilities": {"tools": {}}},
        "tools/list": {"tools": [{"name": "search", "description": "d", "inputSchema": {"type": "object"}}]},
        "tools/call": {"content": [{"type": "text", "text": "hello"}]},
        **overrides,
    })


def test_initialize_negotiates_and_sends_the_initialized_notification():
    transport = _transport()
    client = McpClient(transport)
    assert client.initialize().protocol_version == "2025-06-18"
    assert [item["method"] for item in transport.sent] == ["initialize", "notifications/initialized"]


def test_list_tools_returns_sanitized_descriptors():
    client = McpClient(_transport())
    client.initialize()
    assert [item.name for item in client.list_tools()] == ["search"]


def test_call_tool_returns_content_blocks_and_the_error_flag():
    client = McpClient(_transport())
    client.initialize()
    result = client.call_tool("search", {"q": "x"})
    assert result.is_error is False
    assert result.content == ({"type": "text", "text": "hello"},)


def test_call_tool_marks_a_server_side_tool_error():
    client = McpClient(_transport(**{"tools/call": {"content": [{"type": "text", "text": "boom"}], "isError": True}}))
    client.initialize()
    assert client.call_tool("search", {}).is_error is True


def test_calling_a_tool_before_initialize_is_a_protocol_error():
    with pytest.raises(McpProtocolError):
        McpClient(_transport()).call_tool("search", {})


def test_close_closes_the_transport():
    transport = _transport()
    client = McpClient(transport)
    client.initialize()
    client.close()
    assert transport.closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_client.py -q`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/mcp/client.py
"""The MCP client: negotiate once, discover tools, call one tool at a time."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .models import McpToolDescriptor
from .protocol import CLIENT_INFO, PROTOCOL_VERSION, McpProtocolError, notification, parse_response, request
from .sanitize import sanitize_tool_descriptors

MAX_TOOL_PAGES = 8


class McpTransportPort(Protocol):
    kind: str

    def open(self) -> None: ...
    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class McpNegotiation:
    protocol_version: str
    server_name: str
    capabilities: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class McpCallResult:
    content: tuple[Mapping[str, Any], ...]
    is_error: bool


class McpClient:
    def __init__(self, transport: McpTransportPort) -> None:
        self._transport = transport
        self._ids = itertools.count(1)
        self._negotiation: McpNegotiation | None = None

    @property
    def negotiation(self) -> McpNegotiation | None:
        return self._negotiation

    def _call(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = next(self._ids)
        frame = self._transport.send(request(request_id, method, params))
        if frame is None:
            raise McpProtocolError(f"the transport returned no response for {method}")
        return parse_response(frame, expected_id=request_id)

    def initialize(self) -> McpNegotiation:
        self._transport.open()
        result = self._call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": CLIENT_INFO,
            "capabilities": {},
        })
        server_info = result.get("serverInfo") if isinstance(result.get("serverInfo"), Mapping) else {}
        self._negotiation = McpNegotiation(
            protocol_version=str(result.get("protocolVersion") or PROTOCOL_VERSION),
            server_name=str(server_info.get("name") or "")[:120],
            capabilities=dict(result.get("capabilities") or {}),
        )
        self._transport.send(notification("notifications/initialized"))
        return self._negotiation

    def _require_session(self) -> None:
        if self._negotiation is None:
            raise McpProtocolError("the MCP session was not initialized")

    def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        self._require_session()
        collected: list[Mapping[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_TOOL_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self._call("tools/list", params)
            raw = result.get("tools")
            collected.extend(item for item in (raw or []) if isinstance(item, Mapping))
            cursor = result.get("nextCursor") if isinstance(result.get("nextCursor"), str) else None
            if not cursor:
                break
        return sanitize_tool_descriptors(collected)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> McpCallResult:
        self._require_session()
        result = self._call("tools/call", {"name": name, "arguments": dict(arguments)})
        content = tuple(item for item in (result.get("content") or []) if isinstance(item, Mapping))
        return McpCallResult(content=content, is_error=bool(result.get("isError")))

    def close(self) -> None:
        self._negotiation = None
        self._transport.close()


__all__ = ["McpCallResult", "McpClient", "McpNegotiation", "McpTransportPort"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_client.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp/client.py tests/unit/mcp/test_client.py
git commit -m "feat(mcp): add the MCP client with negotiation, discovery and tool calls"
```

---

### Task 7: Catálogo curado de servidores

O catálogo é o que permite ao agente **explicar** o que o usuário precisa fornecer, em vez de adivinhar.

**Files:**
- Create: `src/agentos/mcp/catalog.py`
- Test: `tests/unit/mcp/test_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_catalog.py
from agentos.mcp.catalog import CATALOG, find_catalog_entry, search_catalog
from agentos.mcp.models import McpTransport


def test_every_entry_declares_what_the_user_must_provide():
    for entry in CATALOG:
        assert entry.catalog_id and entry.display_name and entry.summary
        assert entry.setup_instructions
        for secret in entry.secrets:
            assert secret.name and secret.label and secret.how_to_obtain


def test_every_stdio_entry_has_a_command_and_every_http_entry_a_url():
    for entry in CATALOG:
        if entry.transport is McpTransport.STDIO:
            assert entry.command and entry.url is None
        else:
            assert entry.url and entry.command is None


def test_search_matches_name_and_keywords():
    assert any(entry.catalog_id == "filesystem" for entry in search_catalog("arquivos"))
    assert any(entry.catalog_id == "github" for entry in search_catalog("GitHub"))


def test_find_catalog_entry_returns_none_for_an_unknown_id():
    assert find_catalog_entry("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_catalog.py -q`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/mcp/catalog.py
"""A curated set of known MCP servers.

This exists so the agent can explain a connection instead of guessing one: each
entry says what the server does, how it is launched, and exactly which secret
the user has to fetch and where from.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import McpTransport


@dataclass(frozen=True, slots=True)
class McpSecretRequirement:
    name: str
    label: str
    how_to_obtain: str


@dataclass(frozen=True, slots=True)
class McpCatalogEntry:
    catalog_id: str
    display_name: str
    summary: str
    transport: McpTransport
    setup_instructions: str
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    secrets: tuple[McpSecretRequirement, ...] = ()
    keywords: tuple[str, ...] = ()
    # Placeholder tokens inside args that the user fills in (e.g. a folder path).
    arguments: tuple[str, ...] = ()


CATALOG: tuple[McpCatalogEntry, ...] = (
    McpCatalogEntry(
        catalog_id="filesystem",
        display_name="Filesystem",
        summary="Lê e escreve arquivos em uma pasta que você autoriza.",
        transport=McpTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "{root}"),
        arguments=("root",),
        setup_instructions="Escolha uma pasta. O servidor só enxerga o que estiver dentro dela.",
        keywords=("arquivos", "files", "pasta", "diretorio"),
    ),
    McpCatalogEntry(
        catalog_id="github",
        display_name="GitHub",
        summary="Issues, pull requests e código dos seus repositórios.",
        transport=McpTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-github"),
        secrets=(McpSecretRequirement(
            name="GITHUB_PERSONAL_ACCESS_TOKEN",
            label="Personal access token",
            how_to_obtain="github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens. Marque só os repositórios que o agente pode ver.",
        ),),
        setup_instructions="Crie um token de acesso pessoal com escopo de leitura nos repositórios desejados.",
        keywords=("github", "git", "repositorio", "pull request"),
    ),
    McpCatalogEntry(
        catalog_id="postgres",
        display_name="PostgreSQL",
        summary="Consulta somente-leitura em um banco PostgreSQL.",
        transport=McpTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-postgres", "{connection_url}"),
        arguments=("connection_url",),
        setup_instructions="Use uma connection string de um usuário com permissão apenas de SELECT.",
        keywords=("postgres", "sql", "banco", "database"),
    ),
    McpCatalogEntry(
        catalog_id="notion",
        display_name="Notion",
        summary="Páginas e bancos de dados do seu workspace Notion.",
        transport=McpTransport.HTTP,
        url="https://mcp.notion.com/mcp",
        setup_instructions="O servidor pede autorização na primeira conexão. Nenhuma chave é digitada aqui.",
        keywords=("notion", "notas", "wiki"),
    ),
    McpCatalogEntry(
        catalog_id="sentry",
        display_name="Sentry",
        summary="Erros e releases dos seus projetos no Sentry.",
        transport=McpTransport.HTTP,
        url="https://mcp.sentry.dev/mcp",
        setup_instructions="Requer uma conta Sentry com acesso à organização.",
        keywords=("sentry", "erros", "observabilidade"),
    ),
)


def find_catalog_entry(catalog_id: str) -> McpCatalogEntry | None:
    return next((entry for entry in CATALOG if entry.catalog_id == catalog_id), None)


def search_catalog(text: str) -> tuple[McpCatalogEntry, ...]:
    needle = text.strip().lower()
    if not needle:
        return CATALOG
    return tuple(
        entry for entry in CATALOG
        if needle in entry.display_name.lower()
        or needle in entry.summary.lower()
        or any(needle in keyword for keyword in entry.keywords)
    )


__all__ = ["CATALOG", "McpCatalogEntry", "McpSecretRequirement", "find_catalog_entry", "search_catalog"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_catalog.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp/catalog.py tests/unit/mcp/test_catalog.py
git commit -m "feat(mcp): add a curated catalog of known MCP servers"
```

---

### Task 8: Migração e schema

**Files:**
- Create: `src/agentos/persistence/postgres/migrations/versions/0034_mcp_servers.py`
- Modify: `src/agentos/persistence/postgres/schema.py`
- Test: `tests/unit/persistence/test_mcp_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/persistence/test_mcp_schema.py
from agentos.persistence.postgres.schema import mcp_server_tools, mcp_servers


def test_mcp_servers_declares_the_columns_the_service_needs():
    columns = set(mcp_servers.c.keys())
    assert {"server_id", "user_id", "slug", "display_name", "transport", "command", "args",
            "url", "secret_names", "secrets_ciphertext", "catalog_id", "tool_allowlist",
            "state", "state_reason", "protocol_version", "tools_digest",
            "created_at", "updated_at"} <= columns


def test_a_slug_is_unique_per_user():
    names = {constraint.name for constraint in mcp_servers.constraints if constraint.name}
    assert "uq_mcp_servers_slug" in names


def test_mcp_server_tools_keeps_the_discovered_schema():
    columns = set(mcp_server_tools.c.keys())
    assert {"id", "server_id", "name", "description", "input_schema", "enabled", "discovered_at"} <= columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/persistence/test_mcp_schema.py -q`
Expected: FAIL com `ImportError: cannot import name 'mcp_servers'`

- [ ] **Step 3: Write minimal implementation**

Adicione ao final de `src/agentos/persistence/postgres/schema.py`, seguindo o estilo das tabelas existentes (`metadata` é o objeto `MetaData` já declarado no arquivo):

```python
mcp_servers = Table(
    "mcp_servers", metadata,
    Column("server_id", String(255), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("slug", String(32), nullable=False),
    Column("display_name", String(255), nullable=False),
    Column("catalog_id", String(64)),
    Column("transport", String(16), nullable=False),
    Column("command", String(512)),
    Column("args", JSON(), nullable=False),
    Column("url", String(2048)),
    Column("secret_names", JSON(), nullable=False),
    Column("secrets_ciphertext", Text()),
    Column("tool_allowlist", JSON()),
    Column("state", String(32), nullable=False),
    Column("state_reason", String(512), nullable=False, server_default=""),
    Column("protocol_version", String(32), nullable=False, server_default=""),
    Column("tools_digest", String(64), nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "slug", name="uq_mcp_servers_slug"),
)

mcp_server_tools = Table(
    "mcp_server_tools", metadata,
    Column("id", Integer(), primary_key=True),
    Column("server_id", String(255), nullable=False),
    Column("name", String(64), nullable=False),
    Column("description", Text(), nullable=False),
    Column("input_schema", JSON(), nullable=False),
    Column("enabled", Boolean(), nullable=False, server_default="true"),
    Column("discovered_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("server_id", "name", name="uq_mcp_server_tools_ref"),
)
```

```python
# src/agentos/persistence/postgres/migrations/versions/0034_mcp_servers.py
"""persist MCP server configurations and their discovered tool cache

Revision ID: 0034_mcp_servers
Revises: 0033_scheduled_chats
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_mcp_servers"
down_revision = "0033_scheduled_chats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("server_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("catalog_id", sa.String(64), nullable=True),
        sa.Column("transport", sa.String(16), nullable=False),
        sa.Column("command", sa.String(512), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("secret_names", sa.JSON(), nullable=False),
        sa.Column("secrets_ciphertext", sa.Text(), nullable=True),
        sa.Column("tool_allowlist", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("state_reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("protocol_version", sa.String(32), nullable=False, server_default=""),
        sa.Column("tools_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "slug", name="uq_mcp_servers_slug"),
    )
    op.create_index("ix_mcp_servers_user", "mcp_servers", ["user_id", "state"])
    op.create_table(
        "mcp_server_tools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("server_id", "name", name="uq_mcp_server_tools_ref"),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.server_id"], name="fk_mcp_tools_server", ondelete="CASCADE"),
    )
    op.create_index("ix_mcp_server_tools_server", "mcp_server_tools", ["server_id", "enabled"])


def downgrade() -> None:
    op.drop_index("ix_mcp_server_tools_server", table_name="mcp_server_tools")
    op.drop_table("mcp_server_tools")
    op.drop_index("ix_mcp_servers_user", table_name="mcp_servers")
    op.drop_table("mcp_servers")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/persistence/test_mcp_schema.py -q`
Expected: `3 passed`

Aplique a migração num banco descartável e confirme o downgrade:

```bash
python -m alembic upgrade head
```

- [ ] **Step 5: Commit**

```bash
git add src/agentos/persistence/postgres/schema.py src/agentos/persistence/postgres/migrations/versions/0034_mcp_servers.py tests/unit/persistence/test_mcp_schema.py
git commit -m "feat(mcp): persist MCP server configuration and discovered tools"
```

---

### Task 9: Service de servidores MCP

**Files:**
- Create: `src/agentos/mcp/service.py`, `src/agentos/persistence/postgres/mcp.py`
- Test: `tests/unit/mcp/test_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_service.py
import pytest
from sqlalchemy import create_engine

from agentos.mcp.models import McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.service import McpServerService, McpServiceError
from agentos.persistence.postgres.schema import metadata


@pytest.fixture()
def service(monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return McpServerService(engine)


def _proposal(**overrides):
    return {"user_id": "u1", "display_name": "GitHub", "transport": "stdio", "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "secret_names": ["GITHUB_PERSONAL_ACCESS_TOKEN"], **overrides}


def test_proposing_a_server_creates_it_pending_approval(service):
    record = service.propose(_proposal())
    assert record["state"] == McpServerState.PENDING_APPROVAL.value
    assert record["slug"] == "github"
    assert record["secret_names"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]


def test_a_proposal_never_carries_secret_values(service):
    with pytest.raises(McpServiceError):
        service.propose(_proposal(secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_real"}))


def test_a_duplicate_slug_is_refused(service):
    service.propose(_proposal())
    with pytest.raises(McpServiceError):
        service.propose(_proposal())


def test_approving_stores_the_secrets_encrypted_and_activates(service):
    record = service.propose(_proposal())
    discovered = (McpToolDescriptor(name="search", description="d", input_schema={"type": "object"}),)
    activated = service.approve(user_id="u1", server_id=record["server_id"],
                                secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_real"},
                                connect=lambda config, secrets: ("2025-06-18", discovered))
    assert activated["state"] == McpServerState.ACTIVE.value
    assert activated["tool_count"] == 1
    assert "ghp_real" not in str(activated)


def test_a_failed_connection_keeps_the_server_pending(service):
    record = service.propose(_proposal())

    def failing(config, secrets):
        raise RuntimeError("token rejected")

    with pytest.raises(McpServiceError):
        service.approve(user_id="u1", server_id=record["server_id"],
                        secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "bad"}, connect=failing)
    assert service.get("u1", record["server_id"])["state"] == McpServerState.PENDING_APPROVAL.value


def test_active_servers_expose_their_cached_tools(service):
    record = service.propose(_proposal())
    service.approve(user_id="u1", server_id=record["server_id"], secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "t"},
                    connect=lambda config, secrets: ("2025-06-18", (McpToolDescriptor("search", "d", {"type": "object"}),)))
    active = service.active_servers("u1")
    assert len(active) == 1
    config, tools, secrets = active[0]
    assert config.transport is McpTransport.STDIO
    assert [item.name for item in tools] == ["search"]
    assert secrets["GITHUB_PERSONAL_ACCESS_TOKEN"] == "t"


def test_disabling_removes_the_server_from_the_active_set(service):
    record = service.propose(_proposal())
    service.approve(user_id="u1", server_id=record["server_id"], secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "t"},
                    connect=lambda config, secrets: ("2025-06-18", ()))
    service.set_enabled("u1", record["server_id"], enabled=False)
    assert service.active_servers("u1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_service.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.mcp.service'`

- [ ] **Step 3: Write minimal implementation**

Escreva `src/agentos/mcp/service.py` com a classe `McpServerService(engine)` expondo:

- `propose(command: Mapping) -> dict` — valida com `McpServerConfig`, recusa qualquer chave `secrets`/`env` com valores, deriva `slug` de `display_name` (ou usa `slug` explícito), grava `state=pending_approval`, `secrets_ciphertext=None`. Levanta `McpServiceError` em slug duplicado.
- `list(user_id) -> list[dict]` — resumo público: nunca inclui `secrets_ciphertext`.
- `get(user_id, server_id) -> dict`.
- `approve(*, user_id, server_id, secrets, connect) -> dict` — `connect` é injetado (`(config, secrets) -> (protocol_version, tools)`), o que mantém o service testável sem rede. Em sucesso: cifra `json.dumps(secrets)` com `ProviderSecretCipher.from_environment(required=True).encrypt(...)`, substitui as linhas de `mcp_server_tools`, grava `tools_digest`, `protocol_version`, `state=active`. Em falha: mantém `pending_approval`, grava `state_reason` truncado em 512 e levanta `McpServiceError`.
- `set_enabled(user_id, server_id, *, enabled)` — alterna `active` ⇄ `disabled`.
- `set_tool_enabled(user_id, server_id, tool_name, *, enabled)` — liga/desliga uma tool individual.
- `remove(user_id, server_id)` — apaga servidor e tools.
- `active_servers(user_id) -> list[tuple[McpServerConfig, tuple[McpToolDescriptor, ...], dict[str, str]]]` — uma consulta, usada pelo worker; decifra os segredos.
- `test(user_id, slug, connect) -> dict` — reexecuta a descoberta em um servidor já aprovado, atualiza o cache e o `tools_digest`, e devolve `{"connected": bool, "protocol_version": str, "tools": [nome, ...], "error": str | None}`. É o método que a tool `test_mcp_server` e a rota `POST /v1/mcp/servers/{id}/test` chamam.

Regras não negociáveis, cobertas pelos testes acima:
1. `propose` **rejeita** qualquer valor de segredo. Só nomes.
2. `approve` é a única transição para `active`, e só depois de `connect` retornar sem exceção.
3. Nenhum retorno público contém ciphertext ou valor de segredo.

`src/agentos/persistence/postgres/mcp.py` hospeda apenas os helpers de linha↔dataclass (`_row_to_config`, `_config_to_values`), no mesmo espírito de `postgres/skills.py`, para que `service.py` fique legível.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_service.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp/service.py src/agentos/persistence/postgres/mcp.py tests/unit/mcp/test_service.py
git commit -m "feat(mcp): add the MCP server service with approval-gated activation"
```

---

### Task 10: Provider de tools para o turno

**Files:**
- Create: `src/agentos/mcp/toolset.py`
- Test: `tests/unit/mcp/test_toolset.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_toolset.py
from agentos.mcp.client import McpCallResult
from agentos.mcp.models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.toolset import McpToolProvider


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self.result = McpCallResult(content=({"type": "text", "text": "ok"},), is_error=False)

    def initialize(self):
        return None

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return self.result

    def close(self):
        self.closed = True


def _config() -> McpServerConfig:
    return McpServerConfig(server_id="s1", user_id="u1", slug="notion", display_name="Notion",
                           transport=McpTransport.HTTP, url="https://mcp.example.com/v1",
                           state=McpServerState.ACTIVE)


def _provider(client: FakeClient) -> McpToolProvider:
    tools = (McpToolDescriptor(name="search", description="Search pages", input_schema={"type": "object", "properties": {"q": {"type": "string"}}}),)
    return McpToolProvider([(_config(), tools, {})], client_factory=lambda config, secrets: client)


def test_definitions_are_namespaced_and_tagged():
    definition = _provider(FakeClient()).definitions()[0]
    assert definition.name == "mcp__notion__search"
    assert definition.kind == "mcp"
    assert "mcp" in definition.policy_tags
    assert "Notion" in definition.description


def test_no_session_is_opened_until_a_tool_is_called():
    client = FakeClient()
    provider = _provider(client)
    provider.definitions()
    assert provider.open_session_count == 0


def test_invoking_a_definition_calls_the_remote_tool_with_its_bare_name():
    client = FakeClient()
    provider = _provider(client)
    outcome = provider.definitions()[0].handler(q="roadmap")
    assert client.calls == [("search", {"q": "roadmap"})]
    assert outcome.status == "succeeded"
    assert outcome.content == "ok"
    assert outcome.payload["mcp_server"] == "notion"


def test_a_server_side_tool_error_becomes_a_failed_outcome():
    client = FakeClient()
    client.result = McpCallResult(content=({"type": "text", "text": "no access"},), is_error=True)
    outcome = _provider(client).definitions()[0].handler(q="x")
    assert outcome.status == "failed"
    assert outcome.error_code == "MCP_TOOL_ERROR"


def test_an_image_block_becomes_an_image_on_the_outcome():
    client = FakeClient()
    client.result = McpCallResult(content=({"type": "image", "data": "AAAA", "mimeType": "image/png"},), is_error=False)
    outcome = _provider(client).definitions()[0].handler(q="x")
    assert outcome.images == [{"media_type": "image/png", "data": "AAAA"}]


def test_close_closes_every_open_session():
    client = FakeClient()
    provider = _provider(client)
    provider.definitions()[0].handler(q="x")
    provider.close()
    assert client.closed is True
    assert provider.open_session_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/mcp/test_toolset.py -q`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/mcp/toolset.py
"""Turn cached MCP descriptors into native ToolDefinitions.

Building the tool set must not touch the network: the definitions come from the
discovery cache. A session opens on the first call to that server and closes
with the turn, so a configured-but-unused server costs nothing.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

from agentos.agentic.agent_tools import ToolOutcome, _bounded
from .models import McpServerConfig, McpToolDescriptor, McpTransport, qualified_tool_name

MAX_MCP_RESULT_CHARS = 12_000
MAX_IMAGES_PER_CALL = 4

ServerBundle = tuple[McpServerConfig, tuple[McpToolDescriptor, ...], Mapping[str, str]]


def build_client(config: McpServerConfig, secrets: Mapping[str, str]) -> "McpClient":
    # Imported here so building a tool set never imports a transport it will not
    # use, and so the client module stays free of a cycle back into this one.
    from .client import McpClient
    from .transport_http import HttpTransport
    from .transport_stdio import StdioTransport

    if config.transport is McpTransport.STDIO:
        transport = StdioTransport(command=str(config.command), args=config.args, env=dict(secrets))
    else:
        headers = {"authorization": f"Bearer {secrets['token']}"} if "token" in secrets else {}
        transport = HttpTransport(url=str(config.url), headers=headers)
    return McpClient(transport)


class McpToolProvider:
    def __init__(self, bundles: Iterable[ServerBundle],
                 client_factory: Callable[[McpServerConfig, Mapping[str, str]], Any] = build_client) -> None:
        self._bundles = list(bundles)
        self._client_factory = client_factory
        self._sessions: dict[str, Any] = {}

    @property
    def open_session_count(self) -> int:
        return len(self._sessions)

    def _session(self, config: McpServerConfig, secrets: Mapping[str, str]) -> Any:
        client = self._sessions.get(config.server_id)
        if client is None:
            client = self._client_factory(config, secrets)
            client.initialize()
            self._sessions[config.server_id] = client
        return client

    def _handler(self, config: McpServerConfig, secrets: Mapping[str, str], tool: McpToolDescriptor):
        def call(**arguments: Any) -> ToolOutcome:
            from .protocol import McpProtocolError

            try:
                result = self._session(config, secrets).call_tool(tool.name, arguments)
            except (McpProtocolError, RuntimeError) as error:
                message = f"{config.display_name}: {error}"
                return ToolOutcome("failed", message[:240], message[:MAX_MCP_RESULT_CHARS],
                                   {"tool_kind": "mcp", "mcp_server": config.slug, "mcp_tool": tool.name},
                                   "MCP_UNAVAILABLE")
            text, images = _render(result.content)
            payload = {"tool_kind": "mcp", "mcp_server": config.slug, "mcp_tool": tool.name}
            if result.is_error:
                return ToolOutcome("failed", f"{config.display_name} recusou {tool.name}"[:240],
                                   text or "the server reported a tool error", payload, "MCP_TOOL_ERROR")
            return ToolOutcome("succeeded", f"{config.display_name} · {tool.name}"[:240], text, payload, None, images)

        return call

    def definitions(self) -> tuple[Any, ...]:
        from agentos.agentic.agent_tools import ToolDefinition

        built: list[ToolDefinition] = []
        for config, tools, secrets in self._bundles:
            for tool in tools:
                description = f"[{config.display_name}] {tool.description}".strip()[:1200]
                built.append(ToolDefinition(
                    qualified_tool_name(config.slug, tool.name),
                    description,
                    dict(tool.input_schema),
                    self._handler(config, secrets, tool),
                    "mcp",
                    read_only=False,
                    policy_tags=("mcp", "mutates", f"mcp:{config.slug}"),
                ))
        return tuple(built)

    def close(self) -> None:
        for client in list(self._sessions.values()):
            try:
                client.close()
            except Exception:  # closing must never fail a finished turn
                pass
        self._sessions.clear()


def _render(content: Sequence[Mapping[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    parts: list[str] = []
    images: list[dict[str, str]] = []
    for block in content:
        kind = str(block.get("type") or "")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "image" and len(images) < MAX_IMAGES_PER_CALL:
            images.append({"media_type": str(block.get("mimeType") or "image/png"), "data": str(block.get("data") or "")})
        elif kind == "resource":
            resource = block.get("resource") if isinstance(block.get("resource"), Mapping) else {}
            parts.append(str(resource.get("text") or resource.get("uri") or ""))
    text, _ = _bounded("\n".join(part for part in parts if part), MAX_MCP_RESULT_CHARS)
    return text, images


__all__ = ["McpToolProvider", "ServerBundle", "build_client"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/mcp/test_toolset.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/mcp/toolset.py tests/unit/mcp/test_toolset.py
git commit -m "feat(mcp): expose cached remote tools as native tool definitions"
```

---

### Task 11: Ligar o provider ao toolset do turno

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py:205-253` (construtor), `:487` (`_build_definitions` retorno), `:1142` (`close`)
- Modify: `src/agentos/agentic/session.py:769-795` (`_toolset`)
- Modify: `src/agentos/workers/chat.py:462-503`
- Test: `tests/unit/agentic/test_agent_tools_mcp.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/agentic/test_agent_tools_mcp.py
from agentos.agentic.agent_tools import AgentToolset, ToolDefinition, ToolOutcome
from agentos.agentic.tool_policy import AllowList
from agentos.agentic.workspace import ConversationWorkspace


class FakeProvider:
    def __init__(self) -> None:
        self.closed = False

    def definitions(self):
        return (ToolDefinition("mcp__demo__ping", "[Demo] ping", {"type": "object", "properties": {}},
                               lambda **_: ToolOutcome("succeeded", "pong", "pong"), "mcp",
                               policy_tags=("mcp", "mutates", "mcp:demo")),)

    def close(self) -> None:
        self.closed = True


def _workspace(tmp_path) -> ConversationWorkspace:
    return ConversationWorkspace(root=tmp_path, conversation_id="c1")


def test_mcp_definitions_join_the_native_tool_set(tmp_path):
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=FakeProvider())
    assert "mcp__demo__ping" in {item.name for item in toolset.definitions()}


def test_an_mcp_tool_is_invocable_through_the_toolset(tmp_path):
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=FakeProvider())
    assert toolset.invoke("mcp__demo__ping", {}).content == "pong"


def test_the_policy_can_deny_the_whole_mcp_family(tmp_path):
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=FakeProvider(), policy=AllowList(denied=("tag:mcp",)))
    assert "mcp__demo__ping" not in {item.name for item in toolset.definitions()}


def test_closing_the_toolset_closes_the_mcp_sessions(tmp_path):
    provider = FakeProvider()
    AgentToolset(_workspace(tmp_path), mcp_provider=provider).close()
    assert provider.closed is True
```

> Confira a assinatura real de `ConversationWorkspace` em `src/agentos/agentic/workspace.py` e ajuste o helper `_workspace` ao construtor existente antes de rodar.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/agentic/test_agent_tools_mcp.py -q`
Expected: FAIL com `TypeError: AgentToolset.__init__() got an unexpected keyword argument 'mcp_provider'`

- [ ] **Step 3: Write minimal implementation**

Em `agent_tools.py`:

```python
# no __init__, junto dos outros parâmetros nomeados
        mcp_provider: object | None = None,
...
        self._mcp_provider = mcp_provider
```

No fim de `_build_definitions`, imediatamente antes de `return tuple(items)`:

```python
        if self._mcp_provider is not None:
            # Remote tools come last so a server can never shadow a native tool
            # name, and the namespace prefix already makes collision impossible.
            native = {item.name for item in items}
            items.extend(item for item in self._mcp_provider.definitions() if item.name not in native)
```

Em `close()`, antes do fechamento do browser:

```python
        if self._mcp_provider is not None:
            self._mcp_provider.close()
```

Em `session.py`, adicione `mcp_provider=None` ao `__init__` da `TurnSession`, guarde em `self.mcp_provider`, e repasse dentro de `_toolset`:

```python
            mcp_provider=self.mcp_provider,
```

Em `workers/chat.py:_runtime_for`, antes de construir a `TurnSession`:

```python
        from agentos.mcp.service import McpServerService
        from agentos.mcp.toolset import McpToolProvider

        mcp_service = McpServerService(engine)
        # A broken MCP configuration must never stop a turn from running.
        try:
            bundles = mcp_service.active_servers(str(turn["user_id"]))
        except Exception:
            _LOGGER.exception("could not load the MCP servers for %s", turn["user_id"])
            bundles = []
        mcp_provider = McpToolProvider(bundles) if bundles else None
```

e passe `mcp_provider=mcp_provider` na construção da `TurnSession`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/agentic tests/unit/mcp -q`
Expected: todos passando, incluindo os testes existentes de `agentic`.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/agent_tools.py src/agentos/agentic/session.py src/agentos/workers/chat.py tests/unit/agentic/test_agent_tools_mcp.py
git commit -m "feat(mcp): publish approved MCP tools inside the turn tool set"
```

---

### Task 12: Tools de configuração para o agente

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py` (`_build_definitions` + quatro handlers novos)
- Test: `tests/unit/agentic/test_mcp_agent_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/agentic/test_mcp_agent_tools.py
import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


class FakeMcpService:
    def __init__(self) -> None:
        self.proposals: list[dict] = []

    def list(self, user_id):
        return [{"server_id": "s1", "slug": "github", "display_name": "GitHub", "state": "active", "tool_count": 3}]

    def propose(self, command):
        self.proposals.append(dict(command))
        return {"server_id": "s2", "slug": command.get("slug") or "notion", "display_name": command["display_name"],
                "state": "pending_approval", "secret_names": list(command.get("secret_names") or [])}


def _toolset(tmp_path, service):
    return AgentToolset(ConversationWorkspace(root=tmp_path, conversation_id="c1"),
                        mcp_service=service, mcp_user_id="u1")


def test_the_tools_are_absent_without_a_service(tmp_path):
    names = {item.name for item in AgentToolset(ConversationWorkspace(root=tmp_path, conversation_id="c1")).definitions()}
    assert "configure_mcp" not in names


def test_the_tools_are_published_with_a_service(tmp_path):
    names = {item.name for item in _toolset(tmp_path, FakeMcpService()).definitions()}
    assert {"list_mcp_catalog", "list_mcp_servers", "configure_mcp"} <= names


def test_list_mcp_catalog_explains_the_required_secrets(tmp_path):
    result = _toolset(tmp_path, FakeMcpService()).list_mcp_catalog(query="github")
    entry = result["payload"]["entries"][0]
    assert entry["catalog_id"] == "github"
    assert entry["secrets"][0]["how_to_obtain"]


def test_configure_mcp_creates_a_pending_server_and_asks_for_approval(tmp_path):
    service = FakeMcpService()
    outcome = _toolset(tmp_path, service).configure_mcp(catalog_id="github", display_name="GitHub")
    assert outcome.payload["mcp_approval"] is True
    assert outcome.payload["wait_for_user"] is True
    assert outcome.payload["server"]["state"] == "pending_approval"
    assert service.proposals[0]["secret_names"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]


def test_configure_mcp_refuses_a_secret_value_in_its_arguments(tmp_path):
    with pytest.raises(AgentToolError):
        _toolset(tmp_path, FakeMcpService()).configure_mcp(
            catalog_id="github", display_name="GitHub", secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"})


def test_configure_mcp_refuses_an_unknown_catalog_id_without_an_explicit_transport(tmp_path):
    with pytest.raises(AgentToolError):
        _toolset(tmp_path, FakeMcpService()).configure_mcp(catalog_id="nope", display_name="X")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/agentic/test_mcp_agent_tools.py -q`
Expected: FAIL com `TypeError: ... unexpected keyword argument 'mcp_service'`

- [ ] **Step 3: Write minimal implementation**

Adicione ao `__init__` os parâmetros `mcp_service=None` e `mcp_user_id: str | None = None`, guarde-os, e publique o bloco de definições quando ambos existirem:

```python
        if self._mcp_service is not None and self._mcp_user_id:
            items.extend((
                ToolDefinition(
                    "list_mcp_catalog",
                    "List the MCP servers Orin knows how to connect. Use this first when the user asks to connect a tool: each entry explains what the server does and exactly which credential the user has to fetch.",
                    _schema({"query": {**_TEXT, "description": "Filter by name or subject, e.g. 'github'. Omit for the whole catalog."}}),
                    self.list_mcp_catalog, "mcp", read_only=True,
                ),
                ToolDefinition(
                    "list_mcp_servers",
                    "List the MCP servers this user already configured, with their state and tool count.",
                    _schema({}), self.list_mcp_servers, "mcp", read_only=True,
                ),
                ToolDefinition(
                    "configure_mcp",
                    "Propose an MCP server connection. This never activates the server and never accepts a credential value: it creates a pending configuration and shows the user an approval card where they type any secret themselves. Explain to the user what the server does and which credential they will need before calling this.",
                    _schema({
                        "display_name": {**_TEXT, "description": "How the connection appears in Settings, e.g. 'GitHub'."},
                        "catalog_id": {**_TEXT, "description": "Id from list_mcp_catalog. Fills transport, command and required secrets."},
                        "transport": {"type": "string", "enum": ["stdio", "http"], "description": "Only for a server outside the catalog."},
                        "command": {**_TEXT, "description": "stdio launcher, e.g. 'npx'. Only for a server outside the catalog."},
                        "args": {"type": "array", "items": _TEXT},
                        "url": {**_TEXT, "description": "https endpoint. Only for a server outside the catalog."},
                        "secret_names": {"type": "array", "items": _TEXT, "description": "Names of the credentials the server needs. Names only — never values."},
                    }, ("display_name",)),
                    self.configure_mcp, "mcp", policy_tags=("mutates",),
                ),
                ToolDefinition(
                    "test_mcp_server",
                    "Re-run discovery against an already approved MCP server and report whether it answers and which tools it publishes.",
                    _schema({"slug": _TEXT}, ("slug",)), self.test_mcp_server, "mcp",
                ),
            ))
```

Handlers (mesmo arquivo, junto dos handlers de skill):

```python
    def list_mcp_catalog(self, query: str = "") -> dict[str, Any]:
        from agentos.mcp.catalog import search_catalog

        entries = [{
            "catalog_id": entry.catalog_id, "display_name": entry.display_name, "summary": entry.summary,
            "transport": entry.transport.value, "setup_instructions": entry.setup_instructions,
            "arguments": list(entry.arguments),
            "secrets": [{"name": item.name, "label": item.label, "how_to_obtain": item.how_to_obtain} for item in entry.secrets],
        } for entry in search_catalog(str(query or ""))]
        return {"summary": f"{len(entries)} servidor(es) no catálogo",
                "content": json.dumps(entries, ensure_ascii=False),
                "payload": {"entries": entries, "tool_kind": "mcp", "mcp_action": "catalog"}}

    def list_mcp_servers(self) -> dict[str, Any]:
        servers = self._mcp_service.list(self._mcp_user_id)
        return {"summary": f"{len(servers)} servidor(es) MCP configurado(s)",
                "content": json.dumps(servers, ensure_ascii=False),
                "payload": {"servers": servers, "tool_kind": "mcp", "mcp_action": "list"}}

    def configure_mcp(self, display_name: str, catalog_id: str | None = None, transport: str | None = None,
                      command: str | None = None, args: list[str] | None = None, url: str | None = None,
                      secret_names: list[str] | None = None, **rejected: Any) -> ToolOutcome:
        from agentos.mcp.catalog import find_catalog_entry

        if rejected:
            raise AgentToolError(
                "configure_mcp does not accept credential values. Pass secret_names only; the user types the values in the approval card."
            )
        command_values: dict[str, object] = {"user_id": self._mcp_user_id, "display_name": str(display_name)}
        if catalog_id:
            entry = find_catalog_entry(str(catalog_id))
            if entry is None:
                raise AgentToolError(f"'{catalog_id}' is not in the MCP catalog. Call list_mcp_catalog first, or pass transport plus command/url explicitly.")
            command_values.update({
                "catalog_id": entry.catalog_id, "transport": entry.transport.value,
                "command": entry.command, "args": list(entry.args), "url": entry.url,
                "secret_names": [item.name for item in entry.secrets],
            })
        else:
            if transport not in {"stdio", "http"}:
                raise AgentToolError("transport must be 'stdio' or 'http' when no catalog_id is given.")
            command_values.update({"transport": transport, "command": command, "args": list(args or []),
                                   "url": url, "secret_names": [str(item) for item in (secret_names or [])]})
        try:
            server = self._mcp_service.propose(command_values)
        except Exception as error:
            raise AgentToolError(str(error)) from error
        return ToolOutcome(
            "succeeded",
            f"Aguardando aprovação da conexão {server['display_name']}",
            "The connection is proposed and waiting for the user. They will fill in any credential and approve it in the card. Stop here and wait for their next message.",
            {"server": server, "mcp_approval": True, "wait_for_user": True, "tool_kind": "mcp", "mcp_action": "approval_requested"},
        )

    def test_mcp_server(self, slug: str) -> dict[str, Any]:
        result = self._mcp_service.test(self._mcp_user_id, str(slug))
        return {"summary": f"Testou {slug}", "content": json.dumps(result, ensure_ascii=False),
                "payload": {**result, "tool_kind": "mcp", "mcp_action": "test"}}
```

`**rejected` é intencional: qualquer argumento não declarado (inclusive `secrets`, `env`, `token`) vira uma recusa explícita em vez de ser silenciosamente ignorado.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/agentic/test_mcp_agent_tools.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/agent_tools.py tests/unit/agentic/test_mcp_agent_tools.py
git commit -m "feat(mcp): let the agent propose an MCP connection for user approval"
```

---

### Task 13: Rotas HTTP

**Files:**
- Modify: `src/agentos/api/gateway.py` (junto do bloco de `/v1/providers`, por volta da linha 1037)
- Test: `tests/unit/api/test_mcp_routes.py`

- [ ] **Step 1: Write the failing test**

Siga o padrão de `tests/unit/api/` já existente para montar o app com serviços falsos. Cobertura obrigatória:

```python
def test_get_catalog_returns_the_curated_entries(client): ...
def test_get_servers_never_returns_ciphertext_or_secret_values(client): ...
def test_post_servers_creates_a_pending_server(client): ...
def test_post_approve_activates_and_returns_the_tool_count(client): ...
def test_post_approve_with_a_bad_credential_returns_502_and_keeps_it_pending(client): ...
def test_delete_removes_the_server(client): ...
def test_every_route_is_rate_limited_and_requires_the_loopback_principal(client): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/api/test_mcp_routes.py -q`
Expected: FAIL com 404 em todas as rotas.

- [ ] **Step 3: Write minimal implementation**

Adicione as rotas, cada uma chamando `services.security.check_rate_limit(principal, action=..., origin=request.headers.get("origin"))` exatamente como as rotas de skills fazem em `gateway.py:797-870`:

| Método | Rota | Ação |
| --- | --- | --- |
| GET | `/v1/mcp/catalog` | Catálogo curado (`?query=`). |
| GET | `/v1/mcp/servers` | Lista pública dos servidores do usuário. |
| POST | `/v1/mcp/servers` | Cria um servidor `pending_approval` pela UI. |
| GET | `/v1/mcp/servers/{server_id}` | Detalhe + tools cacheadas. |
| POST | `/v1/mcp/servers/{server_id}/approve` | Recebe `{secrets: {...}}`, conecta, ativa. `502` se a conexão falhar. |
| POST | `/v1/mcp/servers/{server_id}/test` | Redescobre e atualiza o cache. |
| PUT | `/v1/mcp/servers/{server_id}/enabled` | `{enabled: bool}`. |
| PUT | `/v1/mcp/servers/{server_id}/tools/{tool_name}` | `{enabled: bool}`. |
| DELETE | `/v1/mcp/servers/{server_id}` | Remove (204). |

`approve` e `test` recebem `connect=` construído a partir de `McpToolProvider.build_client`, então a rota é o único lugar que abre rede.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/api -q`
Expected: todos passando.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api/gateway.py tests/unit/api/test_mcp_routes.py
git commit -m "feat(mcp): expose MCP server management over the local gateway"
```

---

### Task 14: Cliente HTTP no frontend

**Files:**
- Create: `frontend/src/api/mcp.ts`
- Test: `frontend/tests/unit/mcpApi.test.ts`

- [ ] **Step 1: Write the failing test**

Espelhe `frontend/tests/unit/skillsApi.test.ts`. Cobertura: `listMcpCatalog`, `listMcpServers`, `createMcpServer`, `approveMcpServer` (confirma que o corpo leva `secrets` e que a função **não** loga nem retorna os valores), `setMcpServerEnabled`, `deleteMcpServer`, e que um `ApiError` de 502 é propagado.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- mcpApi`
Expected: FAIL — módulo inexistente.

- [ ] **Step 3: Write minimal implementation**

Escreva `frontend/src/api/mcp.ts` com os tipos `McpCatalogEntry`, `McpServerSummary`, `McpServerDetail`, `McpToolSummary` e as funções acima, todas usando `ApiClient` e `MutationIntent` como `providers.ts` faz.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- mcpApi`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/mcp.ts frontend/tests/unit/mcpApi.test.ts
git commit -m "feat(mcp): add the frontend client for MCP server management"
```

---

### Task 15: Card de aprovação no chat

**Files:**
- Create: `frontend/src/features/conversations/McpApprovalCard.tsx`
- Modify: `frontend/src/features/conversations/ActivityCard.tsx`
- Test: `frontend/tests/unit/McpApprovalCard.test.tsx`

- [ ] **Step 1: Write the failing test**

Espelhe `UserQuestionCard.test.tsx`. Cobertura:

```
- renderiza display_name, transporte e o que o servidor vai poder fazer
- renderiza um campo por secret_name, todos type="password" e autoComplete="off"
- o botão Conectar fica desabilitado enquanto houver campo obrigatório vazio
- Conectar chama approveMcpServer com os valores e depois envia uma mensagem de continuação ao chat
- uma falha de conexão mostra a mensagem do servidor e mantém o formulário preenchido
- Recusar não chama a API e envia uma mensagem informando a recusa
- o valor digitado nunca aparece no DOM fora do input (sem eco em texto)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- McpApprovalCard`
Expected: FAIL — componente inexistente.

- [ ] **Step 3: Write minimal implementation**

O card é acionado pelo evento de atividade cujo payload tem `mcp_approval === true`. Ele lê `payload.server`, monta o formulário e, em sucesso, envia uma mensagem normal na conversa (`Conectei o servidor <nome>.`) para o agente retomar — mesmo mecanismo de continuação que o `UserQuestionCard` usa hoje.

Estilo: reutilize as classes `user-question-card*` renomeando para `approval-card*` no `agentos.css`, mantendo o violeta apenas no botão primário.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- McpApprovalCard`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/conversations/McpApprovalCard.tsx frontend/src/features/conversations/ActivityCard.tsx frontend/src/styles/agentos.css frontend/tests/unit/McpApprovalCard.test.tsx
git commit -m "feat(mcp): approve a proposed MCP connection from the chat"
```

---

### Task 16: Seção MCP em Settings

**Files:**
- Create: `frontend/src/features/mcp/McpSection.tsx`, `McpServerCard.tsx`, `McpServerForm.tsx`
- Test: `frontend/tests/unit/McpSection.test.tsx`

> **Dependência:** esta task consome o `SettingsShell` entregue pelo plano `2026-08-14-settings-shell-refactor.md`. Execute aquele plano antes desta task, ou renderize temporariamente dentro do `SettingsPage` atual e migre depois.

- [ ] **Step 1: Write the failing test**

Cobertura:

```
- lista os servidores configurados com estado e contagem de tools
- um servidor pending_approval mostra o aviso e o botão "Concluir conexão"
- "Adicionar servidor" abre o catálogo curado com busca
- escolher uma entrada do catálogo pré-preenche transporte, comando e campos de credencial
- é possível adicionar um servidor manual (transporte, url/comando, nomes de credencial)
- desligar uma tool individual chama a API e reflete o estado
- remover pede confirmação antes de chamar a API
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- McpSection`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Três arquivos, um assunto cada: `McpSection` (dados e layout), `McpServerCard` (um servidor + suas tools), `McpServerForm` (catálogo + formulário manual). Nenhum deles ultrapassa ~200 linhas.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- McpSection && npm --prefix frontend run lint && npm --prefix frontend run build`
Expected: PASS, sem warnings de lint, build ok.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/mcp frontend/tests/unit/McpSection.test.tsx
git commit -m "feat(mcp): add the MCP section to settings"
```

---

### Task 17: Teste de integração fim a fim

**Files:**
- Create: `tests/integration/test_mcp_end_to_end.py`

- [ ] **Step 1: Write the failing test**

Use um servidor MCP mínimo em Python puro (stdio) escrito em `tests/fixtures/mcp_echo_server.py`, que responde `initialize`, `tools/list` (uma tool `echo`) e `tools/call`. O teste:

```
1. propõe o servidor pelo service (transporte stdio, sys.executable, allow_any_command)
2. aprova, e confirma que ficou active com uma tool cacheada
3. constrói um McpToolProvider a partir de active_servers
4. monta um AgentToolset com esse provider e invoca mcp__echo__echo
5. confirma o conteúdo retornado e que provider.close() encerra o processo filho
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_mcp_end_to_end.py -q`
Expected: FAIL até o fixture existir.

- [ ] **Step 3: Write minimal implementation**

Escreva o fixture e ajuste o que faltar. Este teste é o portão: se ele passa, o caminho completo funciona sem rede externa.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_mcp_end_to_end.py -q`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_mcp_end_to_end.py tests/fixtures/mcp_echo_server.py
git commit -m "test(mcp): cover the approval-to-invocation path end to end"
```

---

### Task 18: Documentação

**Files:**
- Create: `docs/MCP.md`
- Modify: `README.md`, `docs/architecture/900-extensibility/903-mcp-future.md`

- [ ] **Step 1: Escrever `docs/MCP.md`**

Conteúdo obrigatório: o que é suportado (tools; não resources/prompts), os dois transportes, a allowlist de launchers stdio, onde os segredos ficam e sob qual chave, o fluxo de aprovação, como adicionar uma entrada ao catálogo, e o que fazer quando um servidor falha.

- [ ] **Step 2: Atualizar o README**

Adicione MCP à tabela de tools e uma seção curta "Conectores MCP" apontando para `docs/MCP.md`.

- [ ] **Step 3: Atualizar a RFC 903**

Troque o cabeçalho **Estado** para: `Parcialmente adotada — v1 expõe somente tools; ver docs/MCP.md`. Adicione uma seção "Estado da implementação" listando os critérios de adoção atendidos e os adiados (binding_version por tool, quarentena automática, reconciliação de efeito UNKNOWN, resources e prompts).

- [ ] **Step 4: Commit**

```bash
git add docs/MCP.md README.md docs/architecture/900-extensibility/903-mcp-future.md
git commit -m "docs(mcp): document the connector surface and its limits"
```

---

## Verificação final

```bash
python -m pytest -q tests/unit
```

```bash
npm --prefix frontend test && npm --prefix frontend run lint && npm --prefix frontend run build
```

## Follow-ups deliberadamente fora deste plano

1. **OAuth para servidores HTTP** — Notion e Sentry usam authorization code flow. v1 aceita apenas um bearer token colado pelo usuário; o fluxo OAuth completo é seu próprio plano.
2. **Resources e prompts do MCP** — exigem porta local proprietária (RFC 903 §Mapeamento). Nenhum binding genérico foi criado, então adicioná-los depois não quebra nada.
3. **Quarentena automática** por violação de schema repetida.
4. **Aprovação por chamada** para tools MCP marcadas como destrutivas — hoje a aprovação é por servidor.
5. **IP pinning na sessão HTTP** — a revisão de segurança da Task 12 encontrou um TOCTOU de DNS rebinding no guard de SSRF (`transport_http.py`): a validação hoje roda de novo a cada `send()`, o que fecha o buraco de "valida uma vez, reusa para sempre", mas ainda deixa uma corrida estreita entre essa checagem e a resolução DNS interna do `httpx` alguns milissegundos depois. Fechar isso por completo exige conectar a um IP já resolvido e validado (via `httpx.Client(transport=...)` com um `NetworkBackend`/resolver customizado do `httpcore`), mantendo o Host header e a verificação TLS pelo hostname original — mudança maior, adiada deliberadamente.
