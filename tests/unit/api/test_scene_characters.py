"""Unit and API tests for Scene characters and subagent character_name."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image

from agentos.agentic.tools._shared import _PARAM_ALIASES, coerce_tool_arguments
from agentos.agentic.tools.agents.agent import (
    CHARACTER_NAME_DESCRIPTION,
    CHARACTER_NAME_MAX_LEN,
    SPECS,
    AgentInput,
    remember_subagent_character_name,
    resolve_subagent_character_name,
    sanitize_character_name,
)
from agentos.api.scene_character_routes import (
    DEFAULT_SCENE_CHARACTER_NAMES,
    MAX_CHARACTER_NAMES,
    MAX_CUSTOM_CHARACTERS,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_DIMENSION,
    NORMALIZED_MAX_HEIGHT,
    normalize_character_sprite,
    register_scene_character_routes,
)
from agentos.installation import reset_cached_paths
from agentos.api.security import AuthenticatedPrincipal


class _StubSecurity:
    def check_rate_limit(self, principal: Any, *, action: str, origin: str | None = None) -> None:
        return None

    def authorize(self, principal: Any, *, action: str, resource_id: str | None = None, purpose: str = "") -> None:
        return None


class _StubServices:
    def __init__(self) -> None:
        self.security = _StubSecurity()


def _make_png_bytes(
    width: int = 16,
    height: int = 32,
    color: tuple[int, int, int, int] = (255, 64, 128, 200),
) -> bytes:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(height // 2):
        for x in range(width // 2):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_webp_bytes(width: int = 24, height: int = 48) -> bytes:
    img = Image.new("RGBA", (width, height), (10, 200, 100, 128))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _make_animated_gif_bytes() -> bytes:
    frame1 = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    frame2 = Image.new("RGBA", (16, 16), (0, 255, 0, 255))
    buf = io.BytesIO()
    frame1.save(buf, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)
    return buf.getvalue()


@pytest.fixture()
def scene_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENTOS_HOME", str(tmp_path / "home"))
    reset_cached_paths()
    app = FastAPI()
    services = _StubServices()

    def principal_for(request: Request, *, mutable: bool = False) -> AuthenticatedPrincipal:
        user_id = request.headers.get("x-test-user", "user-alpha")
        return AuthenticatedPrincipal(
            user_id=user_id,
            credential_ref="test",
            scopes=frozenset({"*"}),
            session_id="sess-1",
        )

    register_scene_character_routes(app, services, principal_for)
    client = TestClient(app)
    yield client
    reset_cached_paths()


def test_default_names_list_has_24_original_short_names() -> None:
    assert len(DEFAULT_SCENE_CHARACTER_NAMES) == 24
    seen: set[str] = set()
    for name in DEFAULT_SCENE_CHARACTER_NAMES:
        assert isinstance(name, str)
        assert 1 <= len(name) <= CHARACTER_NAME_MAX_LEN
        assert name.casefold() not in seen
        seen.add(name.casefold())


def test_sanitize_character_name_and_schema() -> None:
    assert sanitize_character_name(None) is None
    assert sanitize_character_name("   ") is None
    assert sanitize_character_name("  Kira\x00\x07\n\t  ") == "Kira"
    long_name = "A" * 50
    assert sanitize_character_name(long_name) == "A" * CHARACTER_NAME_MAX_LEN

    schema = AgentInput.model_json_schema()
    assert "character_name" in schema["properties"]
    assert schema["properties"]["character_name"]["description"] == CHARACTER_NAME_DESCRIPTION

    run_subagent_spec = next(spec for spec in SPECS if spec.key == "run_subagent")
    assert "character_name" in run_subagent_spec.legacy_schema["properties"]

    assert _PARAM_ALIASES["run_subagent"]["characterName"] == "character_name"
    normalized, _ = coerce_tool_arguments("run_subagent", {"prompt": "oi", "characterName": "Bento"})
    assert normalized["character_name"] == "Bento"


def test_subagent_character_name_persistence_on_resume_and_event_resolution() -> None:
    record: dict[str, Any] = {"name": "explore-1", "subagent_type": "explore"}
    chosen = remember_subagent_character_name("conv-1", "agent-1", record, "  Zico \x01 ")
    assert chosen == "Zico"
    assert record["character_name"] == "Zico"

    # Resuming without passing character_name keeps the stored name.
    resumed = remember_subagent_character_name("conv-1", "agent-1", record, None)
    assert resumed == "Zico"

    # Explicitly passing a new character_name on resume updates it.
    updated = remember_subagent_character_name("conv-1", "agent-1", record, "Mika")
    assert updated == "Mika"

    registry = {"agent-1": record}
    assert resolve_subagent_character_name("conv-1", "main", registry, agent_id="agent-1") == "Mika"
    assert (
        resolve_subagent_character_name(
            "conv-1",
            "main",
            registry,
            agent_id="main",
            payload={"to_agent_id": "agent-1", "agent_name": "explore-1"},
        )
        == "Mika"
    )
    assert (
        resolve_subagent_character_name(
            "conv-1",
            "main",
            registry,
            agent_id="main",
            payload={"agent_name": "explore-1"},
        )
        == "Mika"
    )
    assert resolve_subagent_character_name("conv-1", "main", registry, payload={"agent_name": "unknown-1"}) is None


def test_normalize_character_sprite_formats_and_scaling() -> None:
    # 1. Valid small PNG preserves transparency and exact dimensions
    raw_png = _make_png_bytes(width=16, height=32, color=(12, 34, 56, 180))
    out_bytes, errs = normalize_character_sprite(raw_png)
    assert not errs and out_bytes is not None
    with Image.open(io.BytesIO(out_bytes)) as img:
        assert img.format == "PNG"
        assert img.mode == "RGBA"
        assert img.size == (16, 32)
        assert img.getpixel((0, 0)) == (12, 34, 56, 180)
        assert img.getpixel((15, 31)) == (0, 0, 0, 0)

    # 2. WebP and animated GIF (1st frame only)
    out_webp, errs_webp = normalize_character_sprite(_make_webp_bytes(20, 40))
    assert not errs_webp and out_webp is not None
    with Image.open(io.BytesIO(out_webp)) as img:
        assert img.format == "PNG"
        assert img.size == (20, 40)

    out_gif, errs_gif = normalize_character_sprite(_make_animated_gif_bytes())
    assert not errs_gif and out_gif is not None
    with Image.open(io.BytesIO(out_gif)) as img:
        assert img.format == "PNG"
        assert getattr(img, "n_frames", 1) == 1
        assert img.getpixel((0, 0))[:3] == (255, 0, 0)

    # 3. Integer factor downscale uses NEAREST (sharp pixel art edges)
    tall_int = Image.new("RGBA", (256, 512), (0, 0, 0, 0))
    for y in range(256):
        for x in range(128):
            tall_int.putpixel((x, y), (200, 10, 20, 255))
    buf_int = io.BytesIO()
    tall_int.save(buf_int, format="PNG")
    out_int, errs_int = normalize_character_sprite(buf_int.getvalue())
    assert not errs_int and out_int is not None
    with Image.open(io.BytesIO(out_int)) as img:
        assert img.size == (128, NORMALIZED_MAX_HEIGHT)
        # Boundary pixel should remain crisp without blended alpha
        assert img.getpixel((63, 127)) == (200, 10, 20, 255)
        assert img.getpixel((64, 128)) == (0, 0, 0, 0)

    # 4. Non-integer factor downscale reduces height to 256px
    tall_non_int = Image.new("RGBA", (300, 400), (50, 100, 150, 255))
    buf_non_int = io.BytesIO()
    tall_non_int.save(buf_non_int, format="PNG")
    out_non_int, errs_non_int = normalize_character_sprite(buf_non_int.getvalue())
    assert not errs_non_int and out_non_int is not None
    with Image.open(io.BytesIO(out_non_int)) as img:
        assert img.height == NORMALIZED_MAX_HEIGHT
        assert img.width == round(300 * (256 / 400))


def test_normalize_character_sprite_rejects_invalid_uploads() -> None:
    # Fake extension / non-image bytes
    out, errs = normalize_character_sprite(b"not an image at all", field_name="idle")
    assert out is None
    assert any("not a valid PNG, WebP, or GIF image" in e for e in errs)

    # Disallowed format (JPEG)
    jpg_img = Image.new("RGB", (16, 16), (255, 255, 255))
    jpg_buf = io.BytesIO()
    jpg_img.save(jpg_buf, format="JPEG")
    out_jpg, errs_jpg = normalize_character_sprite(jpg_buf.getvalue(), field_name="idle")
    assert out_jpg is None
    assert any("PNG, WebP, or GIF" in e for e in errs_jpg)

    # Too large (> 2 MB)
    out_big, errs_big = normalize_character_sprite(b"x" * (MAX_IMAGE_BYTES + 10), field_name="idle")
    assert out_big is None
    assert any("2 MB" in e for e in errs_big)

    # Dimensions > 2048px
    huge_img = Image.new("RGBA", (MAX_IMAGE_DIMENSION + 1, 16), (0, 0, 0, 255))
    huge_buf = io.BytesIO()
    huge_img.save(huge_buf, format="PNG")
    out_huge, errs_huge = normalize_character_sprite(huge_buf.getvalue(), field_name="work")
    assert out_huge is None
    assert any("2048" in e for e in errs_huge)


def test_scene_characters_crud_bindings_and_isolation(scene_client: TestClient) -> None:
    # Initial GET returns default 24 names and empty characters/bindings
    res = scene_client.get("/v1/scene/characters")
    assert res.status_code == 200
    body = res.json()
    assert body["names"] == list(DEFAULT_SCENE_CHARACTER_NAMES)
    assert body["characters"] == []
    assert body["bindings"] == {}

    # PUT /names validates duplicates (case-insensitive) and length > 32
    bad_dup = scene_client.put("/v1/scene/characters/names", json={"names": ["Tupi", "tupi"]})
    assert bad_dup.status_code == 422
    bad_len = scene_client.put("/v1/scene/characters/names", json={"names": ["X" * 33]})
    assert bad_len.status_code == 422
    bad_count = scene_client.put(
        "/v1/scene/characters/names",
        json={"names": [f"N{i}" for i in range(MAX_CHARACTER_NAMES + 1)]},
    )
    assert bad_count.status_code == 422

    ok_names = scene_client.put("/v1/scene/characters/names", json={"names": ["Bento", "Luna", "Kira"]})
    assert ok_names.status_code == 200
    assert ok_names.json()["names"] == ["Bento", "Luna", "Kira"]

    # POST /characters requires idle + name
    missing_idle = scene_client.post("/v1/scene/characters", data={"name": "Bento"})
    assert missing_idle.status_code == 422

    fake_img = scene_client.post(
        "/v1/scene/characters",
        data={"name": "Bento"},
        files={"idle": ("fake.png", b"hello world", "image/png")},
    )
    assert fake_img.status_code == 422

    created = scene_client.post(
        "/v1/scene/characters",
        data={"name": "Bento"},
        files={
            "idle": ("idle.png", _make_png_bytes(16, 32), "image/png"),
            "work": ("work.webp", _make_webp_bytes(16, 32), "image/webp"),
        },
    )
    assert created.status_code == 201, created.text
    char = created.json()["character"]
    char_id = char["id"]
    assert char["name"] == "Bento"
    assert set(char["poses"].keys()) == {"idle", "work"}

    # Serve pose image with cache-control and image/png
    img_res = scene_client.get(char["poses"]["work"])
    assert img_res.status_code == 200
    assert img_res.headers["content-type"] == "image/png"
    assert "max-age" in img_res.headers.get("cache-control", "")

    # Path traversal / invalid character_id or pose returns 404
    for bad_path in (
        "/v1/scene/characters/..%2F..%2Fetc/idle.png",
        f"/v1/scene/characters/{char_id}/../../characters.json.png",
        f"/v1/scene/characters/{char_id}/hack.png",
        "/v1/scene/characters/invalid_id/idle.png",
    ):
        assert scene_client.get(bad_path).status_code == 404

    # PUT /bindings rejects unknown character id and accepts valid id
    bad_bind = scene_client.put("/v1/scene/characters/bindings", json={"bindings": {"explore": "char_deadbeefdeadbeef"}})
    assert bad_bind.status_code == 422

    ok_bind = scene_client.put("/v1/scene/characters/bindings", json={"bindings": {"Explore": char_id, "claude-1": char_id}})
    assert ok_bind.status_code == 200
    assert ok_bind.json()["bindings"] == {"explore": char_id, "claude-1": char_id}

    # PATCH character: rename, add 'think', remove 'work', reject removing 'idle'
    bad_patch = scene_client.patch(f"/v1/scene/characters/{char_id}", json={"remove_poses": ["idle"]})
    assert bad_patch.status_code == 422

    patched = scene_client.patch(
        f"/v1/scene/characters/{char_id}",
        data={"name": "Bento Novo", "remove_poses": "work"},
        files={"think": ("think.gif", _make_animated_gif_bytes(), "image/gif")},
    )
    assert patched.status_code == 200, patched.text
    patched_char = patched.json()["character"]
    assert patched_char["name"] == "Bento Novo"
    assert set(patched_char["poses"].keys()) == {"idle", "think"}
    assert scene_client.get(f"/v1/scene/characters/{char_id}/work.png").status_code == 404

    # User isolation: user-beta cannot see or mutate user-alpha's character
    beta_headers = {"x-test-user": "user-beta"}
    beta_state = scene_client.get("/v1/scene/characters", headers=beta_headers).json()
    assert beta_state["characters"] == []
    assert beta_state["names"] == list(DEFAULT_SCENE_CHARACTER_NAMES)
    assert scene_client.get(f"/v1/scene/characters/{char_id}/idle.png", headers=beta_headers).status_code == 404
    assert scene_client.delete(f"/v1/scene/characters/{char_id}", headers=beta_headers).status_code == 404

    # DELETE removes character and cleans bindings
    deleted = scene_client.delete(f"/v1/scene/characters/{char_id}")
    assert deleted.status_code == 200
    assert deleted.json()["bindings"] == {}
    assert scene_client.get(f"/v1/scene/characters/{char_id}/idle.png").status_code == 404


def test_scene_character_limit_50(scene_client: TestClient) -> None:
    png = _make_png_bytes(8, 8)
    for idx in range(MAX_CUSTOM_CHARACTERS):
        res = scene_client.post(
            "/v1/scene/characters",
            data={"name": f"Char {idx}"},
            files={"idle": ("idle.png", png, "image/png")},
        )
        assert res.status_code == 201
    over = scene_client.post(
        "/v1/scene/characters",
        data={"name": "Char Overflow"},
        files={"idle": ("idle.png", png, "image/png")},
    )
    assert over.status_code == 409
