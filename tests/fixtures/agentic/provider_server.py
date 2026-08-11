"""Local deterministic provider server used by the agentic harness.

This server is intentionally test-only. It does not stand in for a production
provider or create production integration evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentos.execution.events import DataClassification
from agentos.providers.models import (
    ApprovedModelRequirementsRef,
    ApprovedModelRequirementsSnapshot,
    AvailabilitySnapshotRef,
    CancellationRequirement,
    CatalogVersion,
    ContentRole,
    IntegrityRef,
    ModelRef,
    ModelRole,
    ModelRevision,
    ModelSelection,
    ModelSelectionId,
    ModelSelectionRef,
    ProviderInvocationId,
    ProviderInvocationLimits,
    ProviderInvocationRequest,
    ProviderMessage,
    ProviderModelBindingRef,
    ProviderOperationContext,
    ProviderRef,
    ProviderStatus,
    ResponseFormat,
    ResolvedCapabilities,
    SelectedModel,
    SelectionExplanation,
    TextPart,
)


def make_provider_request(*, message: str = "hello", invocation_id: str = "invocation:agentic") -> ProviderInvocationRequest:
    """Build one approved request for the deterministic provider boundary."""
    now = datetime(2026, 8, 10, tzinfo=UTC)
    context = ProviderOperationContext(
        "user:agentic",
        "workspace:agentic",
        "agent:agentic",
        "execution:agentic",
        "correlation:agentic",
        "agentic-harness",
        "actor:agentic",
    )
    selected = SelectedModel(
        ModelRef("model:deterministic"),
        ProviderRef("provider:deterministic"),
        ProviderModelBindingRef("binding:deterministic"),
        ModelRevision("model-revision:deterministic"),
        ResolvedCapabilities(),
        Decimal("1"),
        1,
        ModelRole.PRIMARY,
    )
    selection = ModelSelection(
        ModelSelectionId("selection:deterministic"),
        ModelSelectionRef("selection:deterministic"),
        context,
        selected,
        (),
        CatalogVersion("1"),
        "policy:deterministic",
        None,
        (),
        ApprovedModelRequirementsRef("approved:deterministic"),
        AvailabilitySnapshotRef("availability:deterministic"),
        SelectionExplanation(None),
        now,
        now + timedelta(minutes=5),
    )
    snapshot = ApprovedModelRequirementsSnapshot(
        ApprovedModelRequirementsRef("approved:deterministic"),
        ModelSelectionId("selection:deterministic"),
        context,
        DataClassification.INTERNAL,
        None,
        (),
        ResponseFormat.TEXT,
        (),
        CancellationRequirement.ANY,
        1,
        100,
        20,
        120,
        Decimal("1"),
        (ProviderRef("provider:deterministic"),),
        (ModelRef("model:deterministic"),),
        None,
        CatalogVersion("1"),
        "policy:deterministic",
        now,
        now + timedelta(minutes=5),
        IntegrityRef("integrity:deterministic"),
    )
    return ProviderInvocationRequest(
        ProviderInvocationId(invocation_id),
        context,
        selection,
        ApprovedModelRequirementsRef("approved:deterministic"),
        snapshot,
        messages=(ProviderMessage(ContentRole.USER, (TextPart(message),)),),
        limits=ProviderInvocationLimits(100, 20, 120),
    )


class DeterministicProviderServer:
    """Threaded localhost server with explicit, repeatable provider cases."""

    def __init__(self, case: str = "success", *, secret: str = "") -> None:
        if case not in {"success", "stream", "rate_limited", "retry_then_success", "invalid_response"}:
            raise ValueError(f"unknown deterministic provider case: {case}")
        self.case = case
        self.secret = secret
        self.requests: list[dict[str, Any]] = []
        self._calls = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.fixture = self  # type: ignore[attr-defined]
        self._thread: Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def call_count(self) -> int:
        return self._calls

    def __enter__(self) -> "DeterministicProviderServer":
        self._thread = Thread(target=self._server.serve_forever, name="agentic-provider-fixture", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def record(self, path: str, headers: dict[str, str], body: bytes) -> None:
        self._calls += 1
        self.requests.append({"path": path, "headers": headers, "body": body})

    def response(self) -> tuple[int, str, bytes]:
        if self.case == "rate_limited":
            return 429, "application/json", b'{"error":"rate limited"}'
        if self.case == "retry_then_success" and self._calls == 1:
            return 503, "application/json", b'{"error":"transient provider failure"}'
        if self.case == "invalid_response":
            return 200, "application/json", b"not-json"
        if self.case == "stream":
            body = (
                'data: {"choices":[{"delta":{"content":"deterministic "}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"stream"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode()
            return 200, "text/event-stream", body
        body = {
            "choices": [{"message": {"content": "deterministic answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        return 200, "application/json", json.dumps(body).encode()


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        fixture: DeterministicProviderServer = self.server.fixture  # type: ignore[attr-defined]
        fixture.record(self.path, {key.lower(): value for key, value in self.headers.items()}, body)
        status, content_type, response_body = fixture.response()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args: object) -> None:
        return


__all__ = ["DeterministicProviderServer", "make_provider_request"]

