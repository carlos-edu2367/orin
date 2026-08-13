"""End-to-end coverage for a turn that reads a promoted attachment.

Follows the pattern in ``test_provider_tool_loop.py``: a real
``HTTPProviderStreamTransport`` talks to an ``httpx.MockTransport`` fake
provider, and a real ``AgenticTurnRuntime`` drives the loop. What is new here
is the tool side: instead of the policy-projected ``ActionLoop``, the runtime
is wired to a real ``AgentToolset`` over a real ``ConversationWorkspace``, and
the attachment reaches that workspace through the real staging/promotion path
(``UploadStaging`` + ``promote_uploads``) rather than being written directly —
so the test also proves promotion lands the file where ``view_file`` expects
it.
"""
from __future__ import annotations

import io
import json

import httpx

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.provider_stream import HTTPProviderStreamTransport
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.uploads.promotion import promote_uploads
from agentos.uploads.staging import UploadStaging


def _real_png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="blue").save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeVisionReader:
    """Stands in for a configured visual-reading model (see reading/vision.py)."""

    def __init__(self, text: str = "Nota fiscal nº 42, total R$ 100") -> None:
        self.text = text
        self.calls: list[tuple[list[tuple[str, str]], str]] = []

    def transcribe(self, images, *, instruction: str = "") -> str:
        self.calls.append((list(images), instruction))
        return self.text


class _Store:
    """Same shape as the ``Store`` fake in ``test_provider_tool_loop.py``."""

    def __init__(self, provider: str, model_id: str, attachment_path: str) -> None:
        self.turn = {
            "turn_id": "turn-attachment",
            "conversation_id": "chat_1",
            "user_id": "user-1",
            "workspace_id": None,
            "agent_id": "agent-1",
            "execution_id": "execution-1",
            "provider": provider,
            "model_id": model_id,
            "user_message_id": "user-message-1",
            "assistant_message_id": "assistant-message-1",
        }
        self._attachment_path = attachment_path
        self.states: list[str] = []
        self.content: list[str] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{
            "role": "user",
            "content": (
                "O que tem nessa foto?\n\n"
                f"[anexos enviados pela pessoa: {self._attachment_path} (PNG, 1 KB)]"
            ),
        }]

    def lifecycle(self, turn, state, **payload):
        self.states.append(state)

    def delta(self, turn, text):
        self.content.append(text)

    def finish(self, turn, *, failed=False, code=None):
        self.states.append("failed" if failed else "completed")


def _promoted_image_path(tmp_path) -> tuple[ConversationWorkspace, str]:
    """Stage and promote a real PNG the way a turn's creation would.

    Uses the real ``UploadStaging``/``promote_uploads`` path from
    ``src/agentos/uploads`` rather than writing straight into ``uploads/``, so
    the test also proves the file the runtime sees is the one promotion
    actually produced.
    """
    workspace = ConversationWorkspace(tmp_path / "workspaces", "chat_1")
    staging = UploadStaging(tmp_path / "staging")
    staged = staging.store("user-1", "foto.png", _real_png_bytes())
    records = promote_uploads(staging, workspace, "user-1", [staged.upload_id])
    return workspace, records[0]["path"]


def _tool_call_response(call_id: str, name: str, arguments: dict) -> str:
    delta = {
        "choices": [{
            "delta": {"tool_calls": [{
                "index": 0, "id": call_id,
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }]},
            "finish_reason": "tool_calls",
        }],
    }
    return f"data: {json.dumps(delta)}\n\ndata: [DONE]\n\n"


def _text_response(text: str) -> str:
    delta = {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(delta)}\n\ndata: [DONE]\n\n"


def _has_image_block(messages: list[dict]) -> bool:
    """True when any message in ``messages`` carries an OpenAI-shaped image block.

    ``project_messages`` (agentic/provider_content.py) rewrites the runtime's
    neutral image block into ``image_url`` for an OpenAI-compatible provider
    (which is what ``openrouter`` is here) before the request is sent.
    """
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                return True
    return False


def test_a_turn_with_an_image_reaches_a_model_that_sees(tmp_path) -> None:
    """A model whose ``input_modalities`` include ``image`` gets the real picture.

    The promoted file exists under ``uploads/`` in the conversation workspace;
    the model calls ``view_file``, the tool result plus the image the runtime
    appends both reach the provider request, and the turn completes.
    """
    workspace, attachment_path = _promoted_image_path(tmp_path)
    assert (workspace.root / attachment_path).is_file()
    assert attachment_path == "uploads/foto.png"

    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests_seen.append(payload)
        if len(requests_seen) == 1:
            body = _tool_call_response("call-view-1", "view_file", {"path": attachment_path})
        else:
            body = _text_response("A foto mostra um quadrado azul.")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = HTTPProviderStreamTransport(
        provider="openrouter", base_url="https://provider.test/v1", api_key="sk-test", model="vision-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    store = _Store("openrouter", "vision-model", attachment_path)
    # A model whose input_modalities include "image" is the caller's decision
    # to set model_sees_images=True on the toolset (agent_tools.AgentToolset);
    # the runtime itself is provider-agnostic about it.
    toolset = AgentToolset(workspace, enable_terminal=False, model_sees_images=True)
    runtime = AgenticTurnRuntime(
        store=store, provider=transport, toolset=toolset,
        limits=AgenticLimits(max_actions=3, max_iterations=4),
    )

    result = runtime.run("turn-attachment")

    assert result.state == "completed"
    assert store.states[-1] == "completed"
    assert len(requests_seen) == 2

    # The model's view_file call came back with the image: a tool-role message
    # announcing the image is attached, immediately followed by a user message
    # carrying the actual image block, both present in the follow-up request.
    follow_up_messages = requests_seen[1]["messages"]
    tool_messages = [m for m in follow_up_messages if m.get("role") == "tool"]
    assert tool_messages, "expected a tool-result message for the view_file call"
    assert "anexada" in str(tool_messages[-1]["content"]).lower() or attachment_path in str(tool_messages[-1]["content"])
    assert _has_image_block(follow_up_messages), "expected an image block reaching the provider request"


def test_a_turn_with_an_image_transcribes_for_a_text_only_model(tmp_path) -> None:
    """A text-only model (``input_modalities=("text",)``) never sees the image.

    ``view_file`` routes through the configured ``VisionReader`` instead; the
    tool result carries the transcription, and no provider message carries an
    image block.
    """
    workspace, attachment_path = _promoted_image_path(tmp_path)
    assert (workspace.root / attachment_path).is_file()

    reader = _FakeVisionReader("Nota fiscal nº 42, total R$ 100")
    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests_seen.append(payload)
        if len(requests_seen) == 1:
            body = _tool_call_response("call-view-2", "view_file", {"path": attachment_path})
        else:
            body = _text_response("O total da nota é R$ 100.")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = HTTPProviderStreamTransport(
        provider="openrouter", base_url="https://provider.test/v1", api_key="sk-test", model="text-only-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    store = _Store("openrouter", "text-only-model", attachment_path)
    # A model whose input_modalities is ("text",) cannot see images: the
    # caller does not set model_sees_images, and wires the configured
    # VisionReader instead (agentos.reading.vision / selection.py picks it).
    toolset = AgentToolset(workspace, enable_terminal=False, model_sees_images=False, visual_reader=reader)
    runtime = AgenticTurnRuntime(
        store=store, provider=transport, toolset=toolset,
        limits=AgenticLimits(max_actions=3, max_iterations=4),
    )

    result = runtime.run("turn-attachment")

    assert result.state == "completed"
    assert reader.calls, "the vision reader should have been invoked for the image"

    follow_up_messages = requests_seen[1]["messages"]
    tool_messages = [m for m in follow_up_messages if m.get("role") == "tool"]
    assert tool_messages, "expected a tool-result message for the view_file call"
    assert reader.text in str(tool_messages[-1]["content"])

    # No message in either provider request ever carries an image block.
    assert not _has_image_block(requests_seen[0]["messages"])
    assert not _has_image_block(follow_up_messages)
