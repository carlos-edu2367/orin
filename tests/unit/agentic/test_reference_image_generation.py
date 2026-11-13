"""Unit tests for visual reference image support (`image-to-image` / style & character reference)."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.cli_media_client import (
    ANTIGRAVITY_IMAGE_MODEL_ID,
    CODEX_IMAGE_MODEL_ID,
    CliImageGenerationClient,
)
from agentos.agentic.media_client import (
    MediaGenerationClient,
    MediaGenerationError,
    ReferenceImageInput,
)
from agentos.agentic.workspace import ConversationWorkspace
from agentos.provider_catalog.modalities import SameProviderMediaModelPolicy

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
REF_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-reference-avatar-png"
OUT_JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF-generated-output-jpg"


def _make_policy(provider: str = "openai", model: str = "gpt-image-1") -> SameProviderMediaModelPolicy:
    return SameProviderMediaModelPolicy.from_catalog_models(
        turn_provider=provider,
        favorite_models=[(provider, model, ("image",))],
    )


def test_media_tools_generate_image_resolves_reference_path_and_uri_and_backward_compat(
    tmp_path: Path,
) -> None:
    workspace = ConversationWorkspace(tmp_path)
    uploads_dir = workspace.root / ".orin" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    (uploads_dir / "avatar.png").write_bytes(REF_PNG)
    (uploads_dir / "my avatar.png").write_bytes(REF_PNG)
    (uploads_dir / "notes.txt").write_text("not an image", encoding="utf-8")

    outside_file = tmp_path.parent / "outside_ref.png"
    outside_file.write_bytes(REF_PNG)

    captured_calls: list[dict[str, Any]] = []

    class _CapturingClient:
        def generate_image(
            self,
            *,
            model: str,
            prompt: str,
            size: str = "1024x1024",
            reference_image: ReferenceImageInput | None = None,
        ) -> tuple[bytes, str]:
            captured_calls.append(
                {
                    "model": model,
                    "prompt": prompt,
                    "size": size,
                    "reference_image": reference_image,
                }
            )
            return PNG_1X1, "png"

    toolset = AgentToolset(
        workspace,
        media_policy=_make_policy(),
        media_client=_CapturingClient(),
    )

    # Check schema advertises reference_image_path and reference_image_uri
    defs = {d.name: d for d in toolset.definitions()}
    img_props = defs["generate_image"].parameters["properties"]
    assert "reference_image_path" in img_props
    assert "reference_image_uri" in img_props
    assert "reference" in defs["generate_image"].description.lower()

    # 1. Resolve via reference_image_path (.orin/uploads/avatar.png)
    res_path = toolset.execute(
        "generate_image",
        {
            "prompt": "Keep character in cyberpunk style",
            "reference_image_path": ".orin/uploads/avatar.png",
            "output_path": "generated/out1.png",
        },
    )
    assert res_path["status"] == "succeeded"
    assert res_path["payload"]["reference_image"] == ".orin/uploads/avatar.png"
    assert len(captured_calls) == 1
    ref_arg = captured_calls[-1]["reference_image"]
    assert isinstance(ref_arg, ReferenceImageInput)
    assert ref_arg.data == REF_PNG
    assert ref_arg.mime_type == "image/png"
    assert ref_arg.filename == "avatar.png"

    # 2. Resolve via reference_image_uri with workspace:// and %20 URL-encoding
    res_uri = toolset.execute(
        "generate_image",
        {
            "prompt": "Same character in watercolor",
            "reference_image_uri": "workspace://.orin/uploads/my%20avatar.png",
            "output_path": "generated/out2.png",
        },
    )
    assert res_uri["status"] == "succeeded"
    assert res_uri["payload"]["reference_image"] == ".orin/uploads/my avatar.png"
    assert len(captured_calls) == 2
    ref_arg_uri = captured_calls[-1]["reference_image"]
    assert isinstance(ref_arg_uri, ReferenceImageInput)
    assert ref_arg_uri.data == REF_PNG
    assert ref_arg_uri.filename == "my avatar.png"

    # 3. Reject path traversal via reference_image_path and reference_image_uri
    bad_trav_path = toolset.execute(
        "generate_image",
        {
            "prompt": "Escape attempt",
            "reference_image_path": "../outside_ref.png",
        },
    )
    assert bad_trav_path["status"] == "failed"
    assert bad_trav_path["error_code"] == "INVALID_ARGUMENTS"

    bad_trav_uri = toolset.execute(
        "generate_image",
        {
            "prompt": "Escape attempt via URI",
            "reference_image_uri": "workspace://.orin/uploads/../../../outside_ref.png",
        },
    )
    assert bad_trav_uri["status"] == "failed"
    assert bad_trav_uri["error_code"] == "INVALID_ARGUMENTS"

    # 4. Reject non-image (.txt) reference file via magic-byte check
    bad_txt = toolset.execute(
        "generate_image",
        {
            "prompt": "Try text file as reference",
            "reference_image_path": ".orin/uploads/notes.txt",
        },
    )
    assert bad_txt["status"] == "failed"
    assert bad_txt["error_code"] == "INVALID_ARGUMENTS"
    assert "imagem válida" in bad_txt["content"]

    # 5. Backward compatibility with strict legacy client (no reference_image parameter)
    class _StrictLegacyClient:
        def generate_image(self, *, model: str, prompt: str, size: str = "1024x1024") -> tuple[bytes, str]:
            return PNG_1X1, "png"

    legacy_toolset = AgentToolset(
        workspace,
        media_policy=_make_policy(),
        media_client=_StrictLegacyClient(),
    )
    legacy_ok = legacy_toolset.execute(
        "generate_image",
        {"prompt": "Plain prompt without reference", "output_path": "generated/plain.png"},
    )
    assert legacy_ok["status"] == "succeeded"
    assert "reference_image" not in legacy_ok["payload"]

    legacy_ref_fail = legacy_toolset.execute(
        "generate_image",
        {
            "prompt": "With ref on legacy client",
            "reference_image_path": ".orin/uploads/avatar.png",
        },
    )
    assert legacy_ref_fail["status"] == "failed"
    assert legacy_ref_fail["error_code"] == "MEDIA_GENERATION_FAILED"


def test_openai_media_client_with_reference_image_uses_multipart_edits_and_chat_fallback() -> None:
    requests_seen: list[httpx.Request] = []

    def edits_handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path.endswith("/images/edits"):
            ctype = request.headers.get("content-type", "")
            assert ctype.startswith("multipart/form-data; boundary="), f"Unexpected content-type: {ctype}"
            assert request.headers.get("authorization") == "Bearer sk-openai-test"
            assert REF_PNG in request.content
            assert b'name="prompt"' in request.content
            assert b"Hero portrait in oil painting" in request.content
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(PNG_1X1).decode("ascii")}]},
            )
        return httpx.Response(404)

    client = MediaGenerationClient(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-openai-test",
        transport=httpx.MockTransport(edits_handler),
    )
    ref_input = ReferenceImageInput(data=REF_PNG, mime_type="image/png", filename="avatar.png")
    data, ext = client.generate_image(
        model="gpt-image-1",
        prompt="Hero portrait in oil painting",
        size="1024x1024",
        reference_image=ref_input,
    )
    assert data == PNG_1X1
    assert ext == "png"
    assert len(requests_seen) == 1
    assert requests_seen[0].url.path.endswith("/images/edits")

    # Fallback to multimodal /chat/completions when /images/edits returns 404
    fallback_paths: list[str] = []

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        fallback_paths.append(request.url.path)
        if request.url.path.endswith("/images/edits"):
            return httpx.Response(404, json={"error": {"message": "edits endpoint not supported"}})
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content.decode("utf-8"))
            assert body["modalities"] == ["image", "text"]
            content = body["messages"][0]["content"]
            assert isinstance(content, list)
            assert content[0] == {"type": "text", "text": "Fallback to chat multimodal"}
            expected_url = f"data:image/png;base64,{base64.b64encode(REF_PNG).decode('ascii')}"
            assert content[1] == {"type": "image_url", "image_url": {"url": expected_url}}
            out_url = f"data:image/png;base64,{base64.b64encode(PNG_1X1).decode('ascii')}"
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "images": [{"image_url": {"url": out_url}}]}}]},
            )
        return httpx.Response(404)

    client_fb = MediaGenerationClient(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-openai-test",
        transport=httpx.MockTransport(fallback_handler),
    )
    fb_data, fb_ext = client_fb.generate_image(
        model="gpt-4o-image",
        prompt="Fallback to chat multimodal",
        reference_image=ref_input,
    )
    assert fb_data == PNG_1X1
    assert fb_ext == "png"
    assert fallback_paths == ["/v1/images/edits", "/v1/chat/completions"]


def test_openrouter_media_client_with_reference_image_uses_multimodal_chat_and_images_fallback() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    ref_input = ReferenceImageInput(data=REF_PNG, mime_type="image/png", filename="ref.png")
    expected_data_url = f"data:image/png;base64,{base64.b64encode(REF_PNG).decode('ascii')}"

    def or_chat_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append((request.url.path, body))
        if request.url.path.endswith("/chat/completions"):
            out_url = f"data:image/png;base64,{base64.b64encode(PNG_1X1).decode('ascii')}"
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "images": [{"image_url": {"url": out_url}}]}}]},
            )
        return httpx.Response(404)

    client = MediaGenerationClient(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-test",
        transport=httpx.MockTransport(or_chat_handler),
    )
    data, ext = client.generate_image(
        model="google/gemini-2.5-flash-image-preview",
        prompt="Anime style version",
        reference_image=ref_input,
    )
    assert data == PNG_1X1
    assert ext == "png"
    assert len(calls) == 1
    path, payload = calls[0]
    assert path.endswith("/chat/completions")
    assert payload["modalities"] == ["image", "text"]
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Anime style version"},
                {"type": "image_url", "image_url": {"url": expected_data_url}},
            ],
        }
    ]

    # Fallback to /images with "image" field when /chat/completions returns 404
    fallback_calls: list[tuple[str, dict[str, Any]]] = []

    def or_fallback_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        fallback_calls.append((request.url.path, body))
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": {"message": "use /images endpoint"}})
        if request.url.path.endswith("/images"):
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(PNG_1X1).decode("ascii"), "media_type": "image/png"}]},
            )
        return httpx.Response(404)

    client_fb = MediaGenerationClient(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-test",
        transport=httpx.MockTransport(or_fallback_handler),
    )
    fb_data, fb_ext = client_fb.generate_image(
        model="black-forest-labs/flux-kontext-pro",
        prompt="Keep character on Mars",
        reference_image=ref_input,
    )
    assert fb_data == PNG_1X1
    assert fb_ext == "png"
    assert len(fallback_calls) == 2
    assert fallback_calls[0][0].endswith("/chat/completions")
    assert fallback_calls[1][0].endswith("/images")
    assert fallback_calls[1][1]["image"] == expected_data_url


def test_codex_cli_image_generation_with_reference_image_passes_i_flag(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    capture_file = tmp_path / "codex_ref_capture.json"
    script = tmp_path / "fake_codex_ref.py"
    script.write_text(
        """from __future__ import annotations
import json
import os
from pathlib import Path
import sys

argv = sys.argv[1:]
stdin_data = sys.stdin.read()
codex_home = Path(os.environ["CODEX_HOME"])
capture_file = Path(os.environ["FAKE_CODEX_CAPTURE"])

staged_exists = False
staged_bytes_hex = ""
if "-i" in argv:
    idx = argv.index("-i")
    ref_file = Path(argv[idx + 1])
    staged_exists = ref_file.is_file()
    if staged_exists:
        staged_bytes_hex = ref_file.read_bytes().hex()

capture_file.write_text(
    json.dumps(
        {
            "argv": argv,
            "stdin": stdin_data,
            "cwd": os.getcwd(),
            "staged_exists": staged_exists,
            "staged_bytes_hex": staged_bytes_hex,
        }
    ),
    encoding="utf-8",
)

thread_id = "019cfe2a-1111-2222-3333-444455556666"
print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
out_dir = codex_home / "generated_images" / thread_id
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "out.png").write_bytes(bytes.fromhex(os.environ["EXPECTED_PNG_HEX"]))
print(json.dumps({"type": "turn.completed"}), flush=True)
""",
        encoding="utf-8",
    )

    client = CliImageGenerationClient(
        "openai-codex",
        binary=str(script),
        codex_home=codex_home,
    )

    def _popen(cmd: list[str], **kwargs: Any) -> Any:
        import subprocess

        kwargs["env"] = {
            **kwargs.get("env", {}),
            "FAKE_CODEX_CAPTURE": str(capture_file),
            "EXPECTED_PNG_HEX": PNG_1X1.hex(),
        }
        return subprocess.Popen(cmd, **kwargs)

    client._popen_factory = _popen

    ref_input = ReferenceImageInput(data=REF_PNG, mime_type="image/png", filename="avatar.png")
    data, ext = client.generate_image(
        model=CODEX_IMAGE_MODEL_ID,
        prompt="Same character wearing a space helmet",
        size="1024x1024",
        reference_image=ref_input,
    )
    assert data == PNG_1X1
    assert ext == "png"

    captured = json.loads(capture_file.read_text(encoding="utf-8"))
    argv = captured["argv"]
    assert "-i" in argv
    i_idx = argv.index("-i")
    assert i_idx < argv.index("-")
    staged_arg = Path(argv[i_idx + 1])
    assert staged_arg.name == "reference.png"
    assert captured["staged_exists"] is True
    assert bytes.fromhex(captured["staged_bytes_hex"]) == REF_PNG
    assert "reference image" in captured["stdin"].lower()
    assert not Path(captured["cwd"]).exists()


def test_antigravity_cli_image_generation_with_reference_image_allows_read_file(
    tmp_path: Path,
) -> None:
    brain_dir = tmp_path / "agy_brain"
    brain_dir.mkdir()
    capture_file = tmp_path / "agy_ref_capture.json"
    script = tmp_path / "fake_agy_ref.py"
    script.write_text(
        """from __future__ import annotations
import json
import os
from pathlib import Path
import sys

argv = sys.argv[1:]
brain_dir = Path(os.environ["FAKE_AGY_BRAIN"])
capture_file = Path(os.environ["FAKE_AGY_CAPTURE"])
cwd = Path(os.getcwd())
ref_file = cwd / "reference.png"
agent_md = (cwd / ".agents" / "agents" / "orin.md").read_text(encoding="utf-8")
settings = json.loads((cwd / ".agents" / "orin_settings.json").read_text(encoding="utf-8"))

capture_file.write_text(
    json.dumps(
        {
            "argv": argv,
            "cwd": str(cwd),
            "ref_exists": ref_file.is_file(),
            "ref_bytes_hex": ref_file.read_bytes().hex() if ref_file.is_file() else "",
            "agent_md": agent_md,
            "settings": settings,
        }
    ),
    encoding="utf-8",
)

conv_id = "80c82e58-1111-4222-8333-444455556666"
print(json.dumps({"event": "init", "conversation_id": conv_id, "init": {"model": "gemini-3.8-flash-medium"}}), flush=True)

# 1. Emit read_file step_update first (inspecting reference.png)
print(
    json.dumps(
        {
            "event": "step_update",
            "conversation_id": conv_id,
            "step_update": {
                "step_index": 1,
                "step_type": "tool",
                "tool_name": "read_file",
                "state": "ACTIVE",
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "event": "step_update",
            "conversation_id": conv_id,
            "step_update": {
                "step_index": 1,
                "step_type": "tool",
                "tool_name": "read_file",
                "state": "DONE",
            },
        }
    ),
    flush=True,
)

# 2. Emit generate_image step_update
print(
    json.dumps(
        {
            "event": "step_update",
            "conversation_id": conv_id,
            "step_update": {
                "step_index": 2,
                "step_type": "tool",
                "tool_name": "generate_image",
                "state": "ACTIVE",
            },
        }
    ),
    flush=True,
)

conv_dir = brain_dir / conv_id
conv_dir.mkdir(parents=True, exist_ok=True)
(conv_dir / "styled_output.jpg").write_bytes(bytes.fromhex(os.environ["EXPECTED_JPG_HEX"]))

print(
    json.dumps(
        {
            "event": "step_update",
            "conversation_id": conv_id,
            "step_update": {
                "step_index": 2,
                "step_type": "tool",
                "tool_name": "generate_image",
                "state": "DONE",
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "event": "result",
            "conversation_id": conv_id,
            "result": {"status": "SUCCESS", "response": "Done"},
        }
    ),
    flush=True,
)
""",
        encoding="utf-8",
    )

    client = CliImageGenerationClient(
        "google-antigravity",
        binary=str(script),
        antigravity_brain_dir=brain_dir,
    )

    def _popen(cmd: list[str], **kwargs: Any) -> Any:
        import subprocess

        kwargs["env"] = {
            **kwargs.get("env", {}),
            "FAKE_AGY_BRAIN": str(brain_dir),
            "FAKE_AGY_CAPTURE": str(capture_file),
            "EXPECTED_JPG_HEX": OUT_JPG.hex(),
        }
        return subprocess.Popen(cmd, **kwargs)

    client._popen_factory = _popen

    ref_input = ReferenceImageInput(data=REF_PNG, mime_type="image/png", filename="avatar.png")
    data, ext = client.generate_image(
        model=ANTIGRAVITY_IMAGE_MODEL_ID,
        prompt="Create a 3D render of this character",
        size="1024x1024",
        reference_image=ref_input,
    )
    assert data == OUT_JPG
    assert ext == "jpg"

    captured = json.loads(capture_file.read_text(encoding="utf-8"))
    assert captured["ref_exists"] is True
    assert bytes.fromhex(captured["ref_bytes_hex"]) == REF_PNG
    assert "tools: [read_file, generate_image]" in captured["agent_md"]
    settings = captured["settings"]
    assert settings["allowNonWorkspaceAccess"] is False
    assert settings["permissions"]["allow"] == ["read_file(*)"]
    assert "read_file(*)" not in settings["permissions"]["deny"]
    assert "command(*)" in settings["permissions"]["deny"]
    assert "write_file(*)" in settings["permissions"]["deny"]

    argv = captured["argv"]
    prompt_arg = argv[argv.index("-p") + 1]
    assert "reference.png" in prompt_arg
    assert "read_file" in prompt_arg
    assert not Path(captured["cwd"]).exists()
