# Media Generation Tools & UX Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add same-provider favorite-gated `generate_image`, `generate_audio`, and `generate_video` agent tools with inline workspace media previews, display turn work duration (`"Trabalhou por X..."`) in the chat timeline, and display estimated USD spend (`$`) in the execution Overview panel.

**Architecture:** We introduce `infer_output_modalities` and `SameProviderMediaModelPolicy` in `src/agentos/provider_catalog/modalities.py` so the active turn provider's favorited media models determine which of `generate_image`, `generate_audio`, and `generate_video` are registered in `AgentToolset`. A bounded HTTP client (`src/agentos/agentic/media_client.py`) executes generation requests against the turn provider's credentials and writes files into the conversation workspace (`generated/...`), which `WorkspaceFileCard.tsx` previews inline for images, audio, and video. In parallel, `src/agentos/provider_catalog/pricing.py` computes per-agent and total execution USD cost in `PostgresChatStore.overview()`, while `ChatPage.tsx` / `TurnTimeline.tsx` and `OverviewPanel.tsx` render turn work duration and USD spend minimalistically.

**Tech Stack:** Python 3.12+, SQLAlchemy, FastAPI, httpx, pytest, React 19, TypeScript, Vitest, Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-17-media-generation-and-ux-metrics-design.md`

## Global Constraints

- Media generation tools (`generate_image`, `generate_audio`, `generate_video`) MUST ONLY be published when the user has at least one favorited (`is_favorite = True`) model of that output modality in the **exact same provider** as the active turn (`turn["provider"]`).
- Credentials from another provider MUST NEVER be used for media generation.
- Every generated file path MUST be resolved through `ConversationWorkspace.resolve()` to prevent path traversal (`../`), and external media URL downloads MUST block private/loopback IPs (SSRF protection) and cap size at `100 MB`.
- USD cost calculation MUST prioritize catalog pricing (`input_per_million`, `output_per_million`), fall back to the curated reference table for known model families, return `0.0` for `ollama`, and return `None` when unknown.
- Turn work duration MUST format as `"Trabalhou por Xs"`, `"Trabalhou por Xm Ys"`, or `"Trabalhou por Xh Ym"`.

---

### Task 1: Hybrid Media Modality Detection, Custom Model Modality & Same-Provider Media Policy

**Files:**
- Create: `src/agentos/provider_catalog/modalities.py`
- Modify: `src/agentos/provider_catalog/service.py`
- Modify: `src/agentos/provider_catalog/ports.py`
- Modify: `src/agentos/persistence/postgres/provider_models.py`
- Modify: `src/agentos/api/contracts.py`
- Modify: `src/agentos/api/gateway.py`
- Test: `tests/unit/provider_catalog/test_media_modalities_and_policy.py`

**Interfaces:**
- Produces:
  - `infer_output_modalities(model_id: str, explicit_modalities: Sequence[str] = ()) -> tuple[str, ...]`
  - `SameProviderMediaModelPolicy(turn_provider: str, favorites_by_modality: Mapping[str, Sequence[str]])` with methods:
    - `available_modalities() -> frozenset[str]`
    - `models_for(modality: str) -> tuple[str, ...]`
    - `resolve_model(modality: str, requested_model: str | None = None) -> str`
  - `ProviderModelCatalogService.add_custom(context, provider, model_id, display_name=None, output_modalities=None) -> ProviderModelRecord`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/provider_catalog/test_media_modalities_and_policy.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
import pytest

from agentos.provider_catalog.modalities import (
    SameProviderMediaModelPolicy,
    infer_output_modalities,
)
from agentos.provider_catalog.models import ProviderCatalogContext, ProviderModelRecord
from agentos.provider_catalog.service import ProviderModelCatalogService


class _FakeRepo:
    def __init__(self) -> None:
        self.custom_records: list[ProviderModelRecord] = []

    def credential(self, context, provider):
        return {"enabled": True, "api_key": "sk-test", "base_url": ""}

    def replace(self, context, provider, records, refreshed_at):
        self.custom_records = list(records)

    def list(self, context, provider, favorites_only=False):
        return list(self.custom_records)

    def set_favorite(self, context, provider, model_id, favorite):
        raise NotImplementedError

    def add_custom(self, context, provider, record: ProviderModelRecord) -> ProviderModelRecord:
        self.custom_records.append(record)
        return record

    def remove_custom(self, context, provider, model_id):
        raise NotImplementedError


def test_infer_output_modalities_from_known_model_patterns() -> None:
    assert "image" in infer_output_modalities("gpt-image-1")
    assert "image" in infer_output_modalities("dall-e-3")
    assert "image" in infer_output_modalities("black-forest-labs/flux-1.1-pro")
    assert "audio" in infer_output_modalities("tts-1-hd")
    assert "audio" in infer_output_modalities("gpt-4o-mini-tts")
    assert "audio" in infer_output_modalities("openai/gpt-4o-audio-preview")
    assert "video" in infer_output_modalities("sora-1.0")
    assert "video" in infer_output_modalities("google/veo-2")
    assert "video" in infer_output_modalities("kling-v1-6")
    assert infer_output_modalities("gpt-4o") == ("text",)


def test_infer_output_modalities_preserves_explicit_catalog_modalities() -> None:
    assert infer_output_modalities("custom-model-x", ("image",)) == ("image",)
    assert infer_output_modalities("custom-model-y", ("text", "audio")) == ("text", "audio")


def test_same_provider_media_policy_enforces_favorites_and_resolves_defaults() -> None:
    policy = SameProviderMediaModelPolicy.from_catalog_models(
        turn_provider="openai",
        favorite_models=[
            ("openai", "gpt-image-1", ("image",)),
            ("openai", "dall-e-3", ("image",)),
            ("openai", "tts-1", ("audio",)),
            ("openrouter", "sora-1.0", ("video",)),  # Different provider -> must be ignored!
        ],
    )
    assert policy.available_modalities() == frozenset({"image", "audio"})
    assert policy.models_for("image") == ("gpt-image-1", "dall-e-3")
    assert policy.models_for("video") == ()
    assert policy.resolve_model("image") == "gpt-image-1"
    assert policy.resolve_model("image", "dall-e-3") == "dall-e-3"

    with pytest.raises(PermissionError, match="not a favorited image model"):
        policy.resolve_model("image", "flux-pro")

    with pytest.raises(PermissionError, match="no favorited video model"):
        policy.resolve_model("video")


def test_service_add_custom_accepts_output_modalities_and_infers_by_name() -> None:
    repo = _FakeRepo()
    service = ProviderModelCatalogService(repo, upstreams={}, now=lambda: datetime(2026, 10, 17, tzinfo=UTC))
    ctx = ProviderCatalogContext("u1", "test")

    explicit = service.add_custom(ctx, "openai", "my-custom-synth", "Synth", output_modalities=["audio"])
    assert explicit.output_modalities == ("audio",)

    inferred = service.add_custom(ctx, "openai", "dall-e-3")
    assert "image" in inferred.output_modalities
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/provider_catalog/test_media_modalities_and_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.provider_catalog.modalities'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agentos/provider_catalog/modalities.py`:

```python
"""Hybrid media modality detection and same-provider media model authorization."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

VALID_OUTPUT_MODALITIES = frozenset({"text", "image", "audio", "video"})
MEDIA_MODALITIES = ("image", "audio", "video")

_IMAGE_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"gpt-image[\w.-]*|dall-e[\w.-]*|flux[\w.-]*|sdxl[\w.-]*|stable-diffusion[\w.-]*|"
    r"imagen[\w.-]*|recraft[\w.-]*|ideogram[\w.-]*|midjourney[\w.-]*|playground-v[\w.-]*|"
    r"[\w.-]*-image-preview[\w.-]*|[\w.-]*-image-generation[\w.-]*"
    r")$"
)

_AUDIO_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"tts-[\w.-]+|[\w.-]+-tts(?:-[\w.-]+)?|gpt-4o(?:-[\w.-]+)?-audio[\w.-]*|"
    r"elevenlabs[\w.-]*|kokoro[\w.-]*|bark[\w.-]*|musicgen[\w.-]*|stable-audio[\w.-]*"
    r")$"
)

_VIDEO_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"sora[\w.-]*|veo[\w.-]*|runway[\w.-]*|gen-3[\w.-]*|kling[\w.-]*|"
    r"luma[\w.-]*|ray-[23][\w.-]*|minimax-video[\w.-]*|wan-[\w.-]+|"
    r"cogvideo[\w.-]*|hunyuan-video[\w.-]*|mochi[\w.-]*"
    r")$"
)


def infer_output_modalities(
    model_id: str,
    explicit_modalities: Sequence[str] | Iterable[str] = (),
) -> tuple[str, ...]:
    """Return normalized output modalities, enriching text-only defaults when model_id matches a known media family."""
    cleaned: list[str] = []
    if isinstance(explicit_modalities, str):
        raw_items: Iterable[str] = [item.strip() for item in explicit_modalities.split(",")]
    else:
        raw_items = explicit_modalities or ()
    for item in raw_items:
        norm = str(item or "").strip().lower()
        if norm in VALID_OUTPUT_MODALITIES and norm not in cleaned:
            cleaned.append(norm)

    if any(mod in cleaned for mod in MEDIA_MODALITIES):
        return tuple(cleaned)

    candidate = str(model_id or "").strip().lstrip("~")
    inferred: list[str] = []
    if candidate:
        if _VIDEO_MODEL_RE.search(candidate):
            inferred.append("video")
        elif _AUDIO_MODEL_RE.search(candidate):
            inferred.append("audio")
        elif _IMAGE_MODEL_RE.search(candidate):
            inferred.append("image")

    if inferred:
        return tuple(inferred)
    return tuple(cleaned) if cleaned else ("text",)


@dataclass(frozen=True, slots=True)
class SameProviderMediaModelPolicy:
    """Authorizes image, audio, and video generation models strictly within the turn's active provider."""

    turn_provider: str
    favorites_by_modality: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_catalog_models(
        cls,
        *,
        turn_provider: str,
        favorite_models: Iterable[tuple[str, str, Sequence[str]]],
    ) -> "SameProviderMediaModelPolicy":
        active = str(turn_provider or "").strip().lower()
        buckets: dict[str, list[str]] = {mod: [] for mod in MEDIA_MODALITIES}
        for provider, model_id, raw_modalities in favorite_models:
            if str(provider or "").strip().lower() != active:
                continue
            clean_id = str(model_id or "").strip()
            if not clean_id or clean_id.lower().endswith(":batch"):
                continue
            modalities = infer_output_modalities(clean_id, raw_modalities)
            for mod in MEDIA_MODALITIES:
                if mod in modalities and clean_id not in buckets[mod]:
                    buckets[mod].append(clean_id)
        return cls(
            turn_provider=active,
            favorites_by_modality={mod: tuple(items) for mod, items in buckets.items() if items},
        )

    def available_modalities(self) -> frozenset[str]:
        return frozenset(
            mod for mod, models in self.favorites_by_modality.items()
            if mod in MEDIA_MODALITIES and models
        )

    def models_for(self, modality: str) -> tuple[str, ...]:
        return tuple(self.favorites_by_modality.get(str(modality or "").strip().lower(), ()))

    def resolve_model(self, modality: str, requested_model: str | None = None) -> str:
        norm_mod = str(modality or "").strip().lower()
        allowed = self.models_for(norm_mod)
        if not allowed:
            raise PermissionError(
                f"no favorited {norm_mod} model is configured for provider '{self.turn_provider}'"
            )
        raw = str(requested_model or "").strip()
        if not raw:
            return allowed[0]
        if raw in allowed:
            return raw
        norm_raw = raw.lstrip("~").lower()
        for candidate in allowed:
            cand_norm = candidate.lstrip("~").lower()
            if cand_norm == norm_raw or cand_norm.split("/", 1)[-1] == norm_raw:
                return candidate
        raise PermissionError(
            f"model '{raw}' is not a favorited {norm_mod} model for provider '{self.turn_provider}'. "
            f"Available favorites: {', '.join(allowed)}"
        )


__all__ = [
    "MEDIA_MODALITIES",
    "SameProviderMediaModelPolicy",
    "VALID_OUTPUT_MODALITIES",
    "infer_output_modalities",
]
```

Now update `src/agentos/provider_catalog/service.py`, `src/agentos/provider_catalog/ports.py`, `src/agentos/persistence/postgres/provider_models.py`, `src/agentos/api/contracts.py`, and `src/agentos/api/gateway.py` so `infer_output_modalities` is used during normalization and `list()`, and `add_custom` accepts optional `output_modalities`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/provider_catalog/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/modalities.py src/agentos/provider_catalog/service.py src/agentos/provider_catalog/ports.py src/agentos/persistence/postgres/provider_models.py src/agentos/api/contracts.py src/agentos/api/gateway.py tests/unit/provider_catalog/test_media_modalities_and_policy.py
git commit -m "feat(catalog): add hybrid media modality inference and same-provider media policy"
```

---

### Task 2: Media Generation Client & Dynamic Agent Tools (`generate_image`, `generate_audio`, `generate_video`)

**Files:**
- Create: `src/agentos/agentic/media_client.py`
- Modify: `src/agentos/agentic/agent_tools.py`
- Modify: `src/agentos/agentic/phases.py`
- Modify: `src/agentos/agentic/contract.py`
- Modify: `src/agentos/agentic/runtime.py`
- Modify: `src/agentos/agentic/session.py`
- Modify: `src/agentos/workers/chat.py`
- Test: `tests/unit/agentic/test_media_generation_tools.py`

**Interfaces:**
- Consumes: `SameProviderMediaModelPolicy`, `infer_output_modalities` from `agentos.provider_catalog.modalities`
- Produces:
  - `MediaGenerationClient(provider: str, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None)`
    - `generate_image(*, model: str, prompt: str, size: str = "1024x1024") -> tuple[bytes, str]` (returns `(bytes, extension)`)
    - `generate_audio(*, model: str, text: str, voice: str = "alloy", response_format: str = "mp3") -> tuple[bytes, str]`
    - `generate_video(*, model: str, prompt: str, duration_seconds: int = 5) -> tuple[bytes, str]`
  - `AgentToolset` conditional tools: `generate_image`, `generate_audio`, `generate_video`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agentic/test_media_generation_tools.py`:

```python
from __future__ import annotations

import base64
import json
from pathlib import Path
import httpx
import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
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
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
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
    assert img_outcome.status == "succeeded"
    assert (tmp_path / "generated" / "cat.png").read_bytes() == PNG_1X1
    assert "workspace://generated/cat.png" in img_outcome.content

    aud_outcome = toolset.execute("generate_audio", {"text": "Olá mundo", "output_path": "generated/ola.mp3"})
    assert aud_outcome.status == "succeeded"
    assert (tmp_path / "generated" / "ola.mp3").read_bytes() == FAKE_MP3
    assert "workspace://generated/ola.mp3" in aud_outcome.content

    vid_outcome = toolset.execute("generate_video", {"prompt": "Ondas no mar", "output_path": "generated/sea.mp4"})
    assert vid_outcome.status == "succeeded"
    assert (tmp_path / "generated" / "sea.mp4").read_bytes() == FAKE_MP4
    assert "workspace://generated/sea.mp4" in vid_outcome.content

    # Adversarial check: path traversal must fail
    bad_path = toolset.execute("generate_image", {"prompt": "hack", "output_path": "../outside.png"})
    assert bad_path.status == "failed"

    # Adversarial check: non-favorited model must fail
    bad_model = toolset.execute("generate_image", {"prompt": "hack", "model": "unfavorited-model"})
    assert bad_model.status == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/agentic/test_media_generation_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.agentic.media_client'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agentos/agentic/media_client.py` with `MediaGenerationClient` supporting `/images/generations`, `/audio/speech`, `/videos/generations`, and OpenRouter/chat multimodal fallback, plus SSRF-safe external URL downloading and size caps.
Wire `media_policy` and `media_client` (or `media_client_factory`) into `AgentToolset` (`src/agentos/agentic/agent_tools.py`), `PhaseController` (`src/agentos/agentic/phases.py`, `src/agentos/agentic/contract.py`, `src/agentos/agentic/runtime.py`), `TurnSession` (`src/agentos/agentic/session.py`), and `ChatWorker` (`src/agentos/workers/chat.py`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/agentic/test_media_generation_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/media_client.py src/agentos/agentic/agent_tools.py src/agentos/agentic/phases.py src/agentos/agentic/contract.py src/agentos/agentic/runtime.py src/agentos/agentic/session.py src/agentos/workers/chat.py tests/unit/agentic/test_media_generation_tools.py
git commit -m "feat(agentic): add same-provider generate_image, generate_audio, and generate_video tools"
```

---

### Task 3: USD Cost Estimation Engine & Execution Overview Integration

**Files:**
- Create: `src/agentos/provider_catalog/pricing.py`
- Modify: `src/agentos/conversations/chat.py`
- Test: `tests/unit/conversations/test_overview_usd_cost.py`

**Interfaces:**
- Produces:
  - `estimate_cost_usd(*, provider: str, model_id: str, input_tokens: int | None, output_tokens: int | None, input_per_million: str | float | None = None, output_per_million: str | float | None = None) -> float | None`
  - `token_usage.estimated_cost_usd: float | None` on both top-level `overview()` and each item in `overview()["agents"]`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/conversations/test_overview_usd_cost.py`:

```python
from __future__ import annotations

import pytest
from agentos.provider_catalog.pricing import estimate_cost_usd
from agentos.conversations.chat import _token_usage, _total_token_usage


def test_estimate_cost_usd_prefers_catalog_pricing() -> None:
    cost = estimate_cost_usd(
        provider="openrouter",
        model_id="custom/model",
        input_tokens=100_000,
        output_tokens=50_000,
        input_per_million="2.50",
        output_per_million="10.00",
    )
    assert cost == pytest.approx(0.25 + 0.50)


def test_estimate_cost_usd_uses_fallback_table_for_known_models() -> None:
    cost = estimate_cost_usd(
        provider="openai",
        model_id="gpt-4o-mini-2024-07-18",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost is not None and cost > 0


def test_estimate_cost_usd_handles_ollama_and_unknown_adversarial_inputs() -> None:
    assert estimate_cost_usd(
        provider="ollama",
        model_id="qwen2.5:14b",
        input_tokens=50_000,
        output_tokens=10_000,
    ) == 0.0

    assert estimate_cost_usd(
        provider="omniroute",
        model_id="totally-unknown-custom-model",
        input_tokens=10_000,
        output_tokens=5_000,
        input_per_million="not-a-number",
        output_per_million="-5",
    ) is None


def test_total_token_usage_aggregates_estimated_cost_usd() -> None:
    u1 = _token_usage(
        {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500, "usage_reported": True},
        estimated_cost_usd=0.015,
    )
    u2 = _token_usage(
        {"input_tokens": 2000, "output_tokens": 1000, "total_tokens": 3000, "usage_reported": True},
        estimated_cost_usd=0.030,
    )
    total = _total_token_usage([u1, u2])
    assert total["estimated_cost_usd"] == pytest.approx(0.045)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/conversations/test_overview_usd_cost.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.provider_catalog.pricing'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agentos/provider_catalog/pricing.py` with `estimate_cost_usd` and `KNOWN_MODEL_PRICING_PER_MILLION`.
Update `_token_usage`, `_total_token_usage`, `_unavailable_token_usage`, and `PostgresChatStore.overview` in `src/agentos/conversations/chat.py` so each agent's `(provider, model_id)` is priced using `provider_model_catalog` pricing (when available) + `estimate_cost_usd` fallback.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/conversations/test_overview_usd_cost.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/provider_catalog/pricing.py src/agentos/conversations/chat.py tests/unit/conversations/test_overview_usd_cost.py
git commit -m "feat(overview): calculate per-agent and total estimated USD cost"
```

---

### Task 4: Frontend — Turn Work Duration (`"Trabalhou por X..."`), USD Cost in Overview, Inline Audio/Video Preview & Modality Selector

**Files:**
- Modify: `frontend/src/api/conversations.ts`
- Modify: `frontend/src/features/conversations/activityTypes.ts`
- Modify: `frontend/src/features/overview/OverviewPanel.tsx`
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/features/conversations/WorkspaceFileCard.tsx`
- Modify: `frontend/src/api/providers.ts`
- Modify: `frontend/src/features/providers/useProviderState.ts`
- Modify: `frontend/src/features/providers/ProviderDetail.tsx`
- Test: `frontend/src/features/conversations/mediaAndMetricsUx.test.tsx`

**Interfaces:**
- Produces:
  - `formatTurnWorkDuration(durationMs: number | null | undefined): string | null`
  - `formatUsdCost(cost: number | null | undefined): string`
  - Audio (`mp3`, `wav`, `ogg`, `m4a`, `aac`, `flac`) and Video (`mp4`, `webm`, `mov`) inline players in `WorkspaceFileCard.tsx`
  - Output modality selector (`Texto`, `Imagem`, `Áudio`, `Vídeo`) and modality badges in `ProviderDetail.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/conversations/mediaAndMetricsUx.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { formatTurnWorkDuration } from './TurnTimeline'
import { formatUsdCost, OverviewPanel } from '../overview/OverviewPanel'
import { previewKindFor, WorkspaceFileCard } from './WorkspaceFileCard'
import type { ApiClient } from '../../api/client'

describe('formatTurnWorkDuration', () => {
  it('formats seconds, minutes and hours cleanly in Portuguese', () => {
    expect(formatTurnWorkDuration(null)).toBeNull()
    expect(formatTurnWorkDuration(400)).toBe('Trabalhou por 1s')
    expect(formatTurnWorkDuration(14_200)).toBe('Trabalhou por 14s')
    expect(formatTurnWorkDuration(135_000)).toBe('Trabalhou por 2m 15s')
    expect(formatTurnWorkDuration(180_000)).toBe('Trabalhou por 3m')
    expect(formatTurnWorkDuration(3_900_000)).toBe('Trabalhou por 1h 5m')
  })
})

describe('formatUsdCost & OverviewPanel USD display', () => {
  it('formats small, normal, zero and unknown USD costs', () => {
    expect(formatUsdCost(null)).toBe('—')
    expect(formatUsdCost(undefined)).toBe('—')
    expect(formatUsdCost(0)).toBe('$0.00')
    expect(formatUsdCost(0.0042)).toBe('$0.0042')
    expect(formatUsdCost(0.25)).toBe('$0.25')
  })

  it('renders estimated USD cost in OverviewPanel stats', async () => {
    const fakeClient = {
      request: vi.fn().mockResolvedValue({
        conversation_id: 'conv_1',
        title: 'Teste',
        state: 'completed',
        provider: 'openai',
        model_id: 'gpt-4o',
        activity_count: 4,
        duration_seconds: 12.4,
        token_usage: {
          input_tokens: 1000,
          output_tokens: 500,
          total_tokens: 1500,
          usage_reported: true,
          estimated_cost_usd: 0.0075,
        },
        agents: [
          {
            agent_id: 'agent:conv_1:main',
            name: 'Main',
            role: 'Agente principal',
            parent_agent_id: null,
            provider: 'openai',
            model_id: 'gpt-4o',
            state: 'completed',
            token_usage: {
              input_tokens: 1000,
              output_tokens: 500,
              total_tokens: 1500,
              usage_reported: true,
              estimated_cost_usd: 0.0075,
            },
          },
        ],
        tools: [],
        messages: [],
        errors: [],
        turns: [],
      }),
    } as unknown as ApiClient

    render(
      <OverviewPanel
        conversationId="conv_1"
        client={fakeClient}
        liveEvents={[]}
        onClose={() => {}}
      />,
    )

    expect(await screen.findByText('Gasto estimado')).toBeInTheDocument()
    expect(await screen.findByText('$0.0075')).toBeInTheDocument()
  })
})

describe('WorkspaceFileCard audio and video previews', () => {
  it('classifies audio and video extensions', () => {
    expect(previewKindFor('generated/speech.mp3')).toBe('audio')
    expect(previewKindFor('generated/sound.wav')).toBe('audio')
    expect(previewKindFor('generated/clip.mp4')).toBe('video')
    expect(previewKindFor('generated/clip.webm')).toBe('video')
  })

  it('renders native audio and video players inline', () => {
    const { container: audioContainer } = render(
      <WorkspaceFileCard reference={{ conversationId: 'c1', path: 'generated/voice.mp3' }} />,
    )
    expect(audioContainer.querySelector('audio')).not.toBeNull()

    const { container: videoContainer } = render(
      <WorkspaceFileCard reference={{ conversationId: 'c1', path: 'generated/movie.mp4' }} />,
    )
    expect(videoContainer.querySelector('video')).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- src/features/conversations/mediaAndMetricsUx.test.tsx --run`
Expected: FAIL with missing exports `formatTurnWorkDuration` and `formatUsdCost`.

- [ ] **Step 3: Write minimal implementation**

1. Update `frontend/src/api/conversations.ts` and `frontend/src/features/conversations/activityTypes.ts` to include `estimated_cost_usd: number | null` on `TokenUsage` and `AgentNode['tokenUsage']`.
2. Export `formatUsdCost` and render `<dt>Gasto estimado</dt><dd>{formatUsdCost(overview.token_usage.estimated_cost_usd)}</dd>` in `frontend/src/features/overview/OverviewPanel.tsx` (both in `.overview__stats` and in `.overview__agent-detail`).
3. Export `formatTurnWorkDuration` in `frontend/src/features/conversations/TurnTimeline.tsx` and render the subtle `"Trabalhou por X..."` badge in `ChatPage.tsx` / `TurnTimeline.tsx` for completed turns.
4. Update `frontend/src/features/conversations/WorkspaceFileCard.tsx` so `previewKindFor` returns `'audio'` for `['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac']` and `'video'` for `['mp4', 'webm', 'mov']`, rendering inline `<audio controls preload="metadata" />` and `<video controls preload="metadata" />`.
5. Update `frontend/src/api/providers.ts`, `frontend/src/features/providers/useProviderState.ts`, and `frontend/src/features/providers/ProviderDetail.tsx` to allow selecting modality (`Texto`, `Imagem`, `Áudio`, `Vídeo`) when adding a custom model and showing a subtle badge for media models.

- [ ] **Step 4: Run tests and build to verify they pass**

Run:
- `npm --prefix frontend test -- src/features/conversations/mediaAndMetricsUx.test.tsx --run`
- `npm --prefix frontend run build`
Expected: PASS

- [ ] **Step 5: Visual Verification in Real Browser (Playwright) & Commit**

Verify the rendered UI in the real browser via Playwright (Overview USD cost, Turn work duration `"Trabalhou por..."`, Provider custom model modality selector, and WorkspaceFileCard media player) per Rule #3 (`Verificação Visual Obrigatória`), then commit:

```bash
git add frontend/src/
git commit -m "feat(ui): add turn work duration, overview USD cost, inline audio/video players and modality selector"
```
