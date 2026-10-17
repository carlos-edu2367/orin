from __future__ import annotations

import base64
import json
from pathlib import Path
import httpx
import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.media_client import MediaGenerationClient, MediaGenerationError
from agentos.agentic.workspace import ConversationWorkspace
from agentos.provider_catalog.modalities import SameProviderMediaModelPolicy


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
FAKE_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x0fake-audio-stream"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isomfake-video"


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/images/generations"):
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(PNG_1X1).decode("ascii")}]},
            )
        if path.endswith("/audio/speech"):
            return httpx.Response(
                200,
                content=FAKE_MP3,
                headers={"content-type": "audio/mpeg"},
            )
        if path.endswith("/videos/generations"):
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(FAKE_MP4).decode("ascii")}]},
            )
        if path.endswith("/chat/completions"):
            body = json.loads(request.content.decode("utf-8"))
            modalities = body.get("modalities") or []
            if "image" in modalities:
                data_url = f"data:image/png;base64,{base64.b64encode(PNG_1X1).decode('ascii')}"
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "images": [{"image_url": {"url": data_url}}],
                                }
                            }
                        ]
                    },
                )
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.MockTransport(handler)


def test_toolset_only_publishes_media_tools_with_same_provider_favorites(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path)
    empty_policy = SameProviderMediaModelPolicy.from_catalog_models(
        turn_provider="openai",
        favorite_models=[],
    )
    toolset_none = AgentToolset(workspace, media_policy=empty_policy)
    names_none = {d.name for d in toolset_none.definitions()}
    assert "generate_image" not in names_none
    assert "generate_audio" not in names_none
    assert "generate_video" not in names_none

    image_only_policy = SameProviderMediaModelPolicy.from_catalog_models(
        turn_provider="openai",
        favorite_models=[
            ("openai", "gpt-image-1", ("image",)),
            ("openrouter", "tts-1", ("audio",)),  # Different provider -> excluded
        ],
    )
    client = MediaGenerationClient(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        transport=_mock_transport(),
    )
    toolset_img = AgentToolset(workspace, media_policy=image_only_policy, media_client=client)
    names_img = {d.name for d in toolset_img.definitions()}
    assert "generate_image" in names_img
    assert "generate_audio" not in names_img
    assert "generate_video" not in names_img

    from agentos.agentic.phases import Phase, kinds_for, tools_for

    orient_schemas = toolset_img.schemas(tools_for(Phase.ORIENT), kinds_for(None, phase=Phase.ORIENT))
    orient_names = {s["function"]["name"] for s in orient_schemas}
    assert "generate_image" in orient_names
    assert "generate_audio" not in orient_names

    verify_schemas = toolset_img.schemas(tools_for(Phase.VERIFY), kinds_for(None, phase=Phase.VERIFY))
    verify_names = {s["function"]["name"] for s in verify_schemas}
    assert "generate_image" not in verify_names


def test_generate_image_audio_video_write_to_workspace_and_reject_traversal(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path)
    policy = SameProviderMediaModelPolicy.from_catalog_models(
        turn_provider="openai",
        favorite_models=[
            ("openai", "gpt-image-1", ("image",)),
            ("openai", "tts-1", ("audio",)),
            ("openai", "sora-1.0", ("video",)),
        ],
    )
    client = MediaGenerationClient(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        transport=_mock_transport(),
    )
    toolset = AgentToolset(workspace, media_policy=policy, media_client=client)

    img_outcome = toolset.execute("generate_image", {"prompt": "Um gato astronauta", "output_path": "generated/cat.png"})
    assert img_outcome["status"] == "succeeded"
    assert (tmp_path / "generated" / "cat.png").read_bytes() == PNG_1X1
    assert "workspace://generated/cat.png" in img_outcome["content"]

    aud_outcome = toolset.execute("generate_audio", {"text": "Olá mundo", "output_path": "generated/ola.mp3"})
    assert aud_outcome["status"] == "succeeded"
    assert (tmp_path / "generated" / "ola.mp3").read_bytes() == FAKE_MP3
    assert "workspace://generated/ola.mp3" in aud_outcome["content"]

    vid_outcome = toolset.execute("generate_video", {"prompt": "Ondas no mar", "output_path": "generated/sea.mp4"})
    assert vid_outcome["status"] == "succeeded"
    assert (tmp_path / "generated" / "sea.mp4").read_bytes() == FAKE_MP4
    assert "workspace://generated/sea.mp4" in vid_outcome["content"]

    # Adversarial check: path traversal must fail
    bad_path = toolset.execute("generate_image", {"prompt": "hack", "output_path": "../outside.png"})
    assert bad_path["status"] == "failed"

    # Adversarial check: non-favorited model must fail
    bad_model = toolset.execute("generate_image", {"prompt": "hack", "model": "unfavorited-model"})
    assert bad_model["status"] == "failed"


def test_openrouter_image_generation_uses_chat_completions_multimodal() -> None:
    client = MediaGenerationClient(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-test",
        transport=_mock_transport(),
    )
    data, ext = client.generate_image(model="google/gemini-2.5-flash-image-preview", prompt="Sunset")
    assert data == PNG_1X1
    assert ext == "png"


def test_media_client_rejects_ssrf_private_download_urls() -> None:
    def ssrf_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"url": "http://127.0.0.1:8080/secret.png"}]})

    client = MediaGenerationClient(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        transport=httpx.MockTransport(ssrf_handler),
    )
    with pytest.raises(MediaGenerationError, match="private|unsafe|recusado"):
        client.generate_image(model="dall-e-3", prompt="test")


def test_media_client_rejects_redirect_and_unsafe_video_job_id(tmp_path: Path) -> None:
    def adversarial_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/redir.png"}]})
        if request.url.host == "cdn.example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        if request.url.path.endswith("/videos/generations"):
            return httpx.Response(200, json={"id": "../../admin/delete", "status": "processing"})
        return httpx.Response(404)

    client = MediaGenerationClient(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        transport=httpx.MockTransport(adversarial_handler),
    )
    with pytest.raises(MediaGenerationError, match="HTTP 302"):
        client.generate_image(model="dall-e-3", prompt="test")

    with pytest.raises(MediaGenerationError, match="unsafe job identifier"):
        client.generate_video(model="sora-1.0", prompt="test")

    # Read-only subagent must never see or execute media tools
    policy = SameProviderMediaModelPolicy.from_catalog_models(
        turn_provider="openai",
        favorite_models=[("openai", "gpt-image-1", ("image",))],
    )
    ro_toolset = AgentToolset(ConversationWorkspace(tmp_path), media_policy=policy, media_client=client, read_only_mode=True)
    assert "generate_image" not in {d.name for d in ro_toolset.definitions()}
    res = ro_toolset.execute("generate_image", {"prompt": "test"})
    assert res["status"] == "failed"
    assert res["error_code"] == "READ_ONLY_SUBAGENT"

