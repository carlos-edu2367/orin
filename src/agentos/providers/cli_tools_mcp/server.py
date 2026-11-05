from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import socket
import threading
from typing import Any, Mapping
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
import uvicorn

from agentos.mcp.protocol import PROTOCOL_VERSION
from agentos.providers.cli_tools_mcp.types import (
    ToolCallRequest,
    ToolExecutor,
)

MAX_BODY_BYTES = 4 * 1024 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SUPPORTED_MCP_VERSIONS = frozenset({PROTOCOL_VERSION, "2025-03-26", "2024-11-05"})


class TurnToolServerHandle:
    """Handle for a running per-turn loopback MCP HTTP server."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        server_name: str,
        session_id: str,
        uvicorn_server: uvicorn.Server,
        serve_task: asyncio.Task[None],
        sock: socket.socket,
    ) -> None:
        self.url = url
        self.token = token
        self.server_name = server_name
        self._session_id = session_id
        self._uvicorn_server = uvicorn_server
        self._serve_task = serve_task
        self._sock = sock
        self._lock = threading.Lock()
        self._in_flight = 0
        self._refuse_reason: str | None = None
        self._closed = False

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def _enter_call(self) -> str | None:
        with self._lock:
            if self._refuse_reason is not None:
                return self._refuse_reason
            self._in_flight += 1
            return None

    def _exit_call(self) -> None:
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1

    def refuse_calls(self, reason: str) -> None:
        with self._lock:
            self._refuse_reason = str(reason or "turn finished").strip() or "turn finished"

    def mcp_config_json(self) -> str:
        payload = {
            "mcpServers": {
                self.server_name: {
                    "type": "http",
                    "url": self.url,
                    "headers": {
                        "Authorization": f"Bearer {self.token}",
                    },
                }
            }
        }
        return json.dumps(payload, ensure_ascii=False)

    async def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._refuse_reason is None:
                self._refuse_reason = "server closed"

        self._uvicorn_server.should_exit = True
        try:
            await asyncio.wait_for(self._serve_task, timeout=3.0)
        except Exception:
            self._serve_task.cancel()
            try:
                await self._serve_task
            except Exception:
                pass
        try:
            self._sock.close()
        except Exception:
            pass


class TurnToolServer:
    """Loopback-only HTTP MCP server exposing Orin's turn toolset with a single-turn Bearer token."""

    def __init__(
        self,
        executor: ToolExecutor,
        *,
        server_name: str = "orin",
        host: str = "127.0.0.1",
    ) -> None:
        cleaned_host = str(host or "").strip().lower()
        if cleaned_host not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"TurnToolServer must bind to a loopback address only, got {host!r}"
            )
        if not isinstance(server_name, str) or not server_name.strip():
            raise ValueError("server_name must be a non-empty string")
        self._executor = executor
        self._server_name = server_name.strip()
        self._host = "127.0.0.1" if cleaned_host == "localhost" else cleaned_host

    async def start(self, *, port: int = 0) -> TurnToolServerHandle:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            excl_opt = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if excl_opt is not None:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, excl_opt, 1)
                except OSError:
                    pass
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        sock.bind((self._host, port))
        sock.listen(128)
        sock.setblocking(False)
        bound_port = int(sock.getsockname()[1])

        token = secrets.token_urlsafe(32)
        expected_auth = f"Bearer {token}".encode("utf-8")
        session_id = secrets.token_urlsafe(18)
        allowed_hosts = frozenset(
            {
                f"127.0.0.1:{bound_port}",
                f"localhost:{bound_port}",
            }
        )

        handle_ref: list[TurnToolServerHandle] = []

        async def _dispatch_single(
            item: Mapping[str, Any],
        ) -> tuple[dict[str, Any] | None, dict[str, str]]:
            headers_out: dict[str, str] = {}
            has_id = "id" in item
            req_id = item.get("id")
            method = str(item.get("method") or "")
            params = item.get("params") if isinstance(item.get("params"), Mapping) else {}

            if not has_id:
                return None, headers_out

            if method == "initialize":
                requested_version = str(params.get("protocolVersion") or "").strip()
                negotiated = (
                    requested_version
                    if requested_version in _SUPPORTED_MCP_VERSIONS
                    else PROTOCOL_VERSION
                )
                headers_out["Mcp-Session-Id"] = session_id
                return (
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": negotiated,
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {
                                "name": self._server_name,
                                "version": "1.0.0",
                            },
                        },
                    },
                    headers_out,
                )

            if method == "ping":
                return ({"jsonrpc": "2.0", "id": req_id, "result": {}}, headers_out)

            if method == "tools/list":
                specs = self._executor.list_tools()
                tools_payload = [
                    {
                        "name": spec.name,
                        "description": (spec.description or "")[:2000],
                        "inputSchema": dict(spec.input_schema),
                        "annotations": {"readOnlyHint": bool(spec.read_only)},
                    }
                    for spec in specs
                ]
                return (
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"tools": tools_payload},
                    },
                    headers_out,
                )

            if method == "tools/call":
                handle = handle_ref[0] if handle_ref else None
                refuse_reason = handle._enter_call() if handle is not None else None
                if refuse_reason is not None:
                    return (
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": f"Tool execution refused: {refuse_reason}",
                                    }
                                ],
                                "isError": True,
                            },
                        },
                        headers_out,
                    )

                try:
                    tool_name = str(params.get("name") or "").strip()
                    raw_args = params.get("arguments")
                    arguments = (
                        dict(raw_args) if isinstance(raw_args, Mapping) else {}
                    )
                    raw_meta = params.get("_meta")
                    meta = dict(raw_meta) if isinstance(raw_meta, Mapping) else {}

                    known_specs = self._executor.list_tools()
                    if known_specs and tool_name not in {s.name for s in known_specs}:
                        valid_list = ", ".join(sorted(s.name for s in known_specs))
                        return (
                            {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "result": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": (
                                                f"Unknown tool {tool_name!r}. "
                                                f"Available tools: {valid_list}"
                                            ),
                                        }
                                    ],
                                    "isError": True,
                                },
                            },
                            headers_out,
                        )

                    try:
                        call_res = await asyncio.to_thread(
                            self._executor.call_tool,
                            ToolCallRequest(
                                name=tool_name,
                                arguments=arguments,
                                meta=meta,
                            ),
                        )
                    except Exception as exc:
                        err_text = f"{type(exc).__name__}: {exc}"[:2000]
                        return (
                            {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "result": {
                                    "content": [{"type": "text", "text": err_text}],
                                    "isError": True,
                                },
                            },
                            headers_out,
                        )

                    content_blocks: list[dict[str, Any]] = [
                        {"type": "text", "text": str(call_res.text or "")}
                    ]
                    for img in call_res.images or ():
                        if isinstance(img, Mapping) and img.get("data"):
                            content_blocks.append(
                                {
                                    "type": "image",
                                    "data": str(img["data"]),
                                    "mimeType": str(
                                        img.get("mime_type")
                                        or img.get("mimeType")
                                        or "image/png"
                                    ),
                                }
                            )
                    return (
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": content_blocks,
                                "isError": bool(call_res.is_error),
                            },
                        },
                        headers_out,
                    )
                finally:
                    if handle is not None:
                        handle._exit_call()

            return (
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                },
                headers_out,
            )

        async def _mcp_endpoint(request: Request) -> Response:
            host_header = (request.headers.get("host") or "").strip().lower()
            if host_header not in allowed_hosts:
                return Response("Misdirected Request", status_code=421)

            origin_header = request.headers.get("origin")
            if origin_header is not None:
                parsed_origin = urlsplit(origin_header.strip())
                origin_host = (parsed_origin.hostname or "").lower()
                if (
                    parsed_origin.scheme.lower() not in {"http", "https"}
                    or origin_host not in _LOOPBACK_HOSTS
                ):
                    return Response("Forbidden Origin", status_code=403)

            auth_header = (request.headers.get("authorization") or "").encode(
                "utf-8", errors="surrogateescape"
            )
            if not hmac.compare_digest(auth_header, expected_auth):
                return Response("Unauthorized", status_code=401)

            method_upper = request.method.upper()
            if method_upper == "GET":
                return Response("Method Not Allowed", status_code=405)
            if method_upper == "DELETE":
                return Response("", status_code=200)
            if method_upper != "POST":
                return Response("Method Not Allowed", status_code=405)

            cl_header = request.headers.get("content-length")
            if cl_header is not None:
                try:
                    if int(cl_header) > MAX_BODY_BYTES:
                        return Response("Payload Too Large", status_code=413)
                except ValueError:
                    return Response("Bad Request", status_code=400)

            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    return Response("Payload Too Large", status_code=413)
                chunks.append(chunk)
            raw_body = b"".join(chunks)

            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    },
                    status_code=400,
                )

            if isinstance(payload, Mapping):
                resp_obj, extra_headers = await _dispatch_single(payload)
                if resp_obj is None:
                    return Response("", status_code=202, headers=extra_headers)
                return JSONResponse(resp_obj, status_code=200, headers=extra_headers)

            if isinstance(payload, list) and payload:
                responses: list[dict[str, Any]] = []
                merged_headers: dict[str, str] = {}
                for entry in payload:
                    if not isinstance(entry, Mapping):
                        responses.append(
                            {
                                "jsonrpc": "2.0",
                                "id": None,
                                "error": {
                                    "code": -32600,
                                    "message": "Invalid Request",
                                },
                            }
                        )
                        continue
                    single_resp, extra_headers = await _dispatch_single(entry)
                    merged_headers.update(extra_headers)
                    if single_resp is not None:
                        responses.append(single_resp)
                if not responses:
                    return Response("", status_code=202, headers=merged_headers)
                return JSONResponse(responses, status_code=200, headers=merged_headers)

            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request"},
                },
                status_code=400,
            )

        app = Starlette(
            routes=[
                Route(
                    "/mcp",
                    _mcp_endpoint,
                    methods=["POST", "GET", "DELETE"],
                )
            ]
        )
        config = uvicorn.Config(
            app,
            host=self._host,
            port=bound_port,
            log_level="warning",
            lifespan="off",
            access_log=False,
        )
        uv_server = uvicorn.Server(config)
        uv_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        serve_task = asyncio.create_task(uv_server.serve(sockets=[sock]))

        # Wait until uvicorn marks started or task exits
        for _ in range(200):
            if uv_server.started or serve_task.done():
                break
            await asyncio.sleep(0.005)

        handle = TurnToolServerHandle(
            url=f"http://127.0.0.1:{bound_port}/mcp",
            token=token,
            server_name=self._server_name,
            session_id=session_id,
            uvicorn_server=uv_server,
            serve_task=serve_task,
            sock=sock,
        )
        handle_ref.append(handle)
        return handle


class BackgroundTurnToolServer:
    """Synchronous wrapper running `TurnToolServer` on a dedicated daemon event loop thread."""

    def __init__(
        self,
        *,
        handle: TurnToolServerHandle,
        loop: asyncio.AbstractEventLoop,
        thread: threading.Thread,
    ) -> None:
        self.handle = handle
        self._loop = loop
        self._thread = thread
        self._closed = False
        self._lock = threading.Lock()

    @classmethod
    def start(cls, server: TurnToolServer, *, port: int = 0) -> BackgroundTurnToolServer:
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        holder: dict[str, Any] = {}

        def _runner() -> None:
            asyncio.set_event_loop(loop)
            try:
                holder["handle"] = loop.run_until_complete(server.start(port=port))
            except Exception as exc:
                holder["error"] = exc
                ready.set()
                return
            ready.set()
            try:
                loop.run_forever()
            finally:
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                except Exception:
                    pass
                loop.close()

        thread = threading.Thread(
            target=_runner,
            name="orin-turn-mcp-server",
            daemon=True,
        )
        thread.start()
        ready.wait(timeout=10.0)
        if "error" in holder:
            raise holder["error"]
        if "handle" not in holder:
            raise RuntimeError("Timed out starting BackgroundTurnToolServer")
        return cls(handle=holder["handle"], loop=loop, thread=thread)

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

        if self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self.handle.close(), self._loop
                )
                fut.result(timeout=max(0.5, timeout))
            except Exception:
                pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        self._thread.join(timeout=max(0.5, timeout))
