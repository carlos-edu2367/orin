"""Unit tests for subagent attachments preparation, security validation, and multi-provider delivery (Frente B)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image
import pytest

from agentos.agentic.provider_content import project_messages
from agentos.agentic.session import TurnSession
from agentos.agentic.subagent_attachments import (
    MAX_SUBAGENT_ATTACHMENTS,
    MAX_SUBAGENT_TEXT_FILE_BYTES,
    SubagentAttachmentError,
    prepare_subagent_attachments,
)
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.providers.antigravity_cli.command import (
    encode_antigravity_user_turn,
    wire_antigravity_attached_media_only,
)
from agentos.providers.antigravity_cli.media_gate import (
    MEDIA_GATE_CONFIG_FILENAME,
    MediaReadPolicy,
    decide,
    is_allowed_media_path,
)
from agentos.providers.claude_cli.native_loop import isolated_cli_note
from agentos.providers.claude_cli.text_protocol import _extract_content_text_and_images
from agentos.providers.codex_cli.attachments import validated_codex_images
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _RecordingStore, _text_batch


def _make_png(path: Path, color: tuple[int, int, int] = (20, 120, 220)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (32, 32), color=color)
    img.save(path, format="PNG")
    return path


def _make_pdf(path: Path, text: str = "Orin PDF test content") -> Path:
    """Create a minimal valid single-page PDF containing ``text`` using reportlab/pypdf or raw PDF bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Minimal valid PDF 1.4 with one text stream readable by pypdf.
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET\n".encode("ascii")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        b"4 0 obj\n<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(bytes(out))
    return path


def test_prepare_images_text_and_pdf_attachments(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path)
    img_path = _make_png(ws.root / "shots" / "ref.png")
    txt_path = ws.root / "notes.md"
    txt_path.write_text("# Spec\nButton must be blue.\n", encoding="utf-8")
    pdf_path = _make_pdf(ws.root / "brief.pdf", "Visual brief heading")

    prepared = prepare_subagent_attachments(
        ["workspace://shots/ref.png", "notes.md", "brief.pdf"],
        ws,
        model_sees_images=True,
    )
    assert len(prepared.items) == 3
    assert prepared.has_images is True
    assert prepared.has_native_media is True
    assert prepared.summary_names == ("shots/ref.png", "notes.md", "brief.pdf")
    assert prepared.allowed_media_files == (img_path.resolve(),)

    # Check direct API projections (Anthropic, OpenAI, Ollama)
    msg = {"role": "user", "content": [{"type": "text", "text": "Compare"}, *prepared.content_blocks]}
    anth = project_messages([msg], "anthropic")[0]["content"]
    assert any(b.get("type") == "image" and b.get("source", {}).get("type") == "base64" for b in anth)
    oai = project_messages([msg], "openai")[0]["content"]
    assert any(b.get("type") == "image_url" and b["image_url"]["url"].startswith("data:image/jpeg;base64,") for b in oai)
    oll = project_messages([msg], "ollama")[0]
    assert len(oll["images"]) == 1

    # Check Claude CLI stream-json extraction produces base64 image blocks
    text_joined, claude_images = _extract_content_text_and_images(msg["content"])
    assert "Button must be blue." in text_joined
    assert "Visual brief heading" in text_joined
    assert len(claude_images) == 1
    assert claude_images[0]["type"] == "image"
    assert claude_images[0]["source"]["type"] == "base64"

    # Check Codex CLI image path validation accepts the workspace image
    codex_imgs = validated_codex_images(
        [item["path"] for item in prepared.turn_attachments if item["kind"] == "image"],
        ws.root,
    )
    assert codex_imgs == (img_path.resolve(),)


def test_attachment_security_rejections(tmp_path: Path) -> None:
    ws_dir = tmp_path / "ws"
    outside_dir = tmp_path / "outside"
    ws_dir.mkdir()
    outside_dir.mkdir()
    ws = ConversationWorkspace.at_root(ws_dir)

    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("top-secret", encoding="utf-8")

    # Path traversal
    with pytest.raises(SubagentAttachmentError):
        prepare_subagent_attachments(["../outside/secret.txt"], ws)

    # Outside workspace when host_folders=False
    with pytest.raises(SubagentAttachmentError):
        prepare_subagent_attachments([str(outside_file)], ws, host_folders=False)

    # Missing file
    with pytest.raises(SubagentAttachmentError):
        prepare_subagent_attachments(["missing.png"], ws)

    # Directory
    (ws.root / "subdir").mkdir()
    with pytest.raises(SubagentAttachmentError):
        prepare_subagent_attachments(["subdir"], ws)

    # Exceed max attachments count
    with pytest.raises(SubagentAttachmentError):
        prepare_subagent_attachments([f"f{i}.txt" for i in range(MAX_SUBAGENT_ATTACHMENTS + 1)], ws)

    # Exceed per-file text size limit
    big_txt = ws.root / "big.txt"
    big_txt.write_bytes(b"a" * (MAX_SUBAGENT_TEXT_FILE_BYTES + 10))
    with pytest.raises(SubagentAttachmentError):
        prepare_subagent_attachments(["big.txt"], ws)

    # Image with non-vision model raises SUBAGENT_MODEL_NO_VISION
    _make_png(ws.root / "pic.png")
    with pytest.raises(SubagentAttachmentError) as exc_info:
        prepare_subagent_attachments(["pic.png"], ws, model_sees_images=False)
    assert exc_info.value.code == "SUBAGENT_MODEL_NO_VISION"


def test_attachment_rejects_symlinks_and_junctions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    ws = ConversationWorkspace.at_root(ws_dir)
    real_file = ws.root / "doc.txt"
    real_file.write_text("hello", encoding="utf-8")

    # Always verify symlink/junction rejection guard even on Windows without symlink privileges.
    monkeypatch.setattr(
        "agentos.agentic.subagent_attachments._is_reparse_or_symlink",
        lambda path: Path(path).name == "doc.txt",
    )
    with pytest.raises(SubagentAttachmentError, match="Symlinks and junctions"):
        prepare_subagent_attachments(["doc.txt"], ws)


def test_host_folders_staging_for_codex_and_cleanup_in_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_dir = tmp_path / "ws"
    ext_dir = tmp_path / "external"
    ws_dir.mkdir()
    ext_img = _make_png(ext_dir / "ref.png")

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'att.db').as_posix()}")
    metadata.create_all(engine)
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    store = _RecordingStore()
    seen_requests: list[object] = []

    class _CapProvider:
        def stream(self, req):
            seen_requests.append(req)
            return iter(_text_batch("Looks identical."))

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=_CapProvider,
        workspace_root=ws_dir,
        child_provider_factory=lambda _model: _CapProvider(),
        model_sees_images=True,
        host_folders=True,
    )
    try:
        outcome = session._run_subagent(
            subagent_type="general",
            task="Compare this screenshot",
            access_mode="prompt_only",
            attachments=[str(ext_img)],
        )
        assert outcome.status == "succeeded", outcome.content
        assert seen_requests
        # Staging directory under .orin/subagent-staging must be cleaned up after run.
        staging_root = session.workspace.root / ".orin" / "subagent-staging"
        remaining = list(staging_root.iterdir()) if staging_root.exists() else []
        assert remaining == []

        # AGENT_MESSAGE_SENT carries attachment_count and attachment_names
        sent_payload = [payload for et, _s, payload, _a in store.activity if str(getattr(et, "value", et)) == "agent.message_sent"][-1]
        assert sent_payload["access_mode"] == "prompt_only"
        assert sent_payload["attachment_count"] == 1
        assert sent_payload["attachment_names"] == ["ref.png"]
    finally:
        engine.dispose()


def test_session_rejects_image_attachment_when_model_lacks_vision(tmp_path: Path) -> None:
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'novision.db').as_posix()}")
    metadata.create_all(engine)
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    session = TurnSession(
        turn=dict(TURN),
        store=_RecordingStore(),
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=ws_dir,
        child_provider_factory=lambda _model: None,
        model_sees_images=False,
    )
    _make_png(session.workspace.root / "shot.png")
    try:
        res = session._run_subagent(
            subagent_type="general",
            task="Inspect shot",
            access_mode="prompt_only",
            attachments=["shot.png"],
        )
        assert res.status == "failed"
        assert res.error_code == "SUBAGENT_MODEL_NO_VISION"
    finally:
        engine.dispose()


def test_antigravity_media_gate_allowed_files_blocks_other_workspace_files(tmp_path: Path) -> None:
    allowed_img = _make_png(tmp_path / "ref.png", (10, 20, 30))
    other_img = _make_png(tmp_path / "other.png", (200, 10, 10))
    other_txt = tmp_path / "secret.txt"
    other_txt.write_text("do not read", encoding="utf-8")

    # Even with unrestricted=True and allowed_roots=[tmp_path], allowed_files strictly wins.
    ok_allowed, _ = is_allowed_media_path(
        str(allowed_img),
        allowed_roots=[tmp_path],
        allowed_files=[allowed_img],
        unrestricted=True,
    )
    assert ok_allowed is True

    ok_other, reason_other = is_allowed_media_path(
        str(other_img),
        allowed_roots=[tmp_path],
        allowed_files=[allowed_img],
        unrestricted=True,
    )
    assert ok_other is False
    assert "not in the allowed" in reason_other.lower()

    ok_txt, _ = is_allowed_media_path(
        str(other_txt),
        allowed_files=[other_txt],
    )
    assert ok_txt is False  # .txt extension is never allowed by media gate

    # Policy check & PreToolUse decide()
    policy = MediaReadPolicy(allowed_files=(str(allowed_img),), unrestricted=True)
    assert policy.check(str(allowed_img))[0] is True
    assert policy.check(str(other_img))[0] is False

    hook_cfg = {"allowed_files": [str(allowed_img)], "unrestricted": True}
    assert decide({"toolCall": {"name": "read_file", "args": {"Path": str(allowed_img)}}}, hook_cfg)["decision"] == "allow"
    assert decide({"toolCall": {"name": "read_file", "args": {"Path": str(other_img)}}}, hook_cfg)["decision"] == "deny"


def test_wire_antigravity_attached_media_only_and_prompt_only_cli_note(tmp_path: Path) -> None:
    img_a = _make_png(tmp_path / "a.png")
    img_b = _make_png(tmp_path / "b.png")
    workdir = tmp_path / "agy_cwd"

    wiring = wire_antigravity_attached_media_only(
        workdir,
        allowed_files=[img_a, img_b],
        model="gemini-4-neon-high",
        instructions="Compare both screenshots carefully.",
    )
    assert "--agent" in wiring.argv and "orin" in wiring.argv
    assert not (workdir / ".agents" / "mcp_config.json").exists()

    agent_md = (workdir / ".agents" / "agents" / "orin.md").read_text(encoding="utf-8")
    assert "inheritMcp: false" in agent_md
    assert "tools: [read_file]" in agent_md

    gate_cfg = json.loads((workdir / ".agents" / MEDIA_GATE_CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert gate_cfg["allowed_files"] == [str(img_a.resolve()), str(img_b.resolve())]
    assert gate_cfg["unrestricted"] is False

    # isolated_cli_note for prompt_only has zero MCP/shell tool mentions
    agy_note = isolated_cli_note(
        "orin",
        adapter_name="antigravity",
        access_mode="prompt_only",
        allowed_media_files=[img_a, img_b],
    )
    assert "mcp__orin" not in agy_note
    assert img_a.resolve().as_posix() in agy_note
    assert img_b.resolve().as_posix() in agy_note

    claude_note = isolated_cli_note("orin", adapter_name="claude", access_mode="prompt_only")
    assert "mcp__orin" not in claude_note
    assert "zero tools" in claude_note.lower()

    # encode_antigravity_user_turn with allowed_files never emits view_file fallback
    encoded = encode_antigravity_user_turn(
        [
            {"type": "text", "text": "Compare these two images."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}},
        ],
        allowed_files=[img_a, img_b],
    )
    payload = json.loads(encoded.decode("utf-8"))
    joined = "\n".join(b["text"] for b in payload["message"]["content"])
    assert "view_file" not in joined
    assert img_a.resolve().as_posix() in joined
    assert img_b.resolve().as_posix() in joined
