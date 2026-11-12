"""The native `read_file` of agy for every model: per-model extensions, several roots, unrestricted mode,
and a hook-denied call that no longer kills the process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

import pytest

from agentos.providers.antigravity_cli import (
    ALLOWED_AUDIO_EXTENSIONS,
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_MEDIA_EXTENSIONS,
    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    MEDIA_GATE_CONFIG_FILENAME,
    AntigravityCliAdapter,
    AntigravityStreamTranslator,
    antigravity_media_prompt_note,
    decide_media_gate,
    is_allowed_media_path,
    wire_antigravity_tool_server,
)
from agentos.providers.antigravity_cli.command import (
    antigravity_native_media_extensions,
    antigravity_native_media_policy,
)
from agentos.providers.antigravity_cli.media_gate import MediaReadPolicy
from agentos.providers.claude_cli.errors import CliBuiltinToolDetour
from agentos.providers.claude_cli.native_loop import (
    NativeTurnRequest,
    find_forbidden_item_kinds,
    start_native_turn_for_provider,
)
from agentos.providers.claude_cli.provider import ClaudeCliProvider
from tests.unit.providers.antigravity_cli.test_adapter_conformance import (
    FAKE_ANTIGRAVITY_SCRIPT,
    _EchoExecutor,
    _RecordingSink,
)
from tests.unit.providers.cli_adapter_conformance import FakeToolServerHandle


def _hook(path: object) -> dict[str, Any]:
    return {"toolCall": {"name": "read_file", "args": {"Path": path}}}


def _write(path: Path, data: bytes = b"\x89PNG\r\n\x1a\nxxxx") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _step(tool: str, path: str, state: str, *, with_error: bool = False) -> str:
    info: dict[str, Any] = {"name": tool, "parameters": {"Path": path}}
    if with_error:
        info["error"] = {"type": "TOOL_ERROR", "message": "denied by pre-tool hook"}
    return json.dumps(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-1",
                "step_index": 3,
                "state": state,
                "step_type": "tool",
                "tool_name": tool,
                "tool_info": info,
            },
        }
    )


# --- per-model extensions -------------------------------------------------------------------


def test_native_media_extensions_follow_what_the_model_perceives() -> None:
    assert antigravity_native_media_extensions("gemini-3.8-flash-high") == ALLOWED_MEDIA_EXTENSIONS
    assert antigravity_native_media_extensions("google-antigravity/gemini-4-argon-low") == (
        ALLOWED_MEDIA_EXTENSIONS
    )
    for claude in ("claude-opus-5-5-high", "claude-sonnet-5-5-low"):
        exts = antigravity_native_media_extensions(claude)
        assert exts == ALLOWED_IMAGE_EXTENSIONS
        assert not (exts & ALLOWED_AUDIO_EXTENSIONS)
    assert antigravity_native_media_extensions("gpt-oss-120b-medium") == frozenset()
    assert antigravity_native_media_extensions("antigravity-image") == frozenset()
    assert antigravity_native_media_extensions(None) == frozenset()


def test_wiring_claude_gets_native_read_file_for_images_with_one_allow_per_root(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    extra = tmp_path / "extra"
    ws.mkdir()
    extra.mkdir()
    wiring = wire_antigravity_tool_server(
        FakeToolServerHandle(),
        tmp_path / "run",
        model="claude-opus-5-5-high",
        workspace_root=ws,
        additional_roots=[extra],
    )
    agents = tmp_path / "run" / ".agents"
    md = (agents / "agents" / "orin.md").read_text(encoding="utf-8")
    assert "tools: [read_file]" in md
    assert "excludeDefaultComponents: true" in md
    # Claude does not hear: the prompt says audio/video come transcribed by Orin.
    assert "transcription" in md
    assert "see an image" in md and "hear an audio file" not in md
    settings = json.loads((agents / "orin_settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == [
        "mcp(orin/*)",
        f"read_file({ws.resolve().as_posix()})",
        f"read_file({extra.resolve().as_posix()})",
    ]
    assert "read_file(*)" not in settings["permissions"]["deny"]
    gate = json.loads((agents / MEDIA_GATE_CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert gate["allowed_roots"] == [str(ws.resolve()), str(extra.resolve())]
    assert gate["allowed_root"] == str(ws.resolve())
    assert gate["unrestricted"] is False
    assert set(gate["allowed_extensions"]) == set(ALLOWED_IMAGE_EXTENSIONS)
    assert len(wiring.files) == 5


def test_wiring_text_only_and_image_model_keep_native_read_file_denied(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    for index, model in enumerate(("gpt-oss-120b-medium", "antigravity-image")):
        run = tmp_path / f"run{index}"
        wiring = wire_antigravity_tool_server(
            FakeToolServerHandle(), run, model=model, workspace_root=ws
        )
        assert len(wiring.files) == 3
        settings = json.loads((run / ".agents" / "orin_settings.json").read_text(encoding="utf-8"))
        assert settings["permissions"]["allow"] == ["mcp(orin/*)"]
        assert "read_file(*)" in settings["permissions"]["deny"]
        assert not (run / ".agents" / "hooks.json").exists()
        assert "tools: [read_file]" not in (run / ".agents" / "agents" / "orin.md").read_text(encoding="utf-8")


def test_wiring_unrestricted_allows_read_file_everywhere_but_keeps_the_gate(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    wire_antigravity_tool_server(
        FakeToolServerHandle(),
        tmp_path / "run",
        model="gemini-3.8-flash-medium",
        workspace_root=ws,
        unrestricted=True,
    )
    agents = tmp_path / "run" / ".agents"
    settings = json.loads((agents / "orin_settings.json").read_text(encoding="utf-8"))
    assert "read_file(*)" in settings["permissions"]["allow"]
    assert "read_file(*)" not in settings["permissions"]["deny"]
    assert (agents / "hooks.json").is_file()
    gate = json.loads((agents / MEDIA_GATE_CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert gate["unrestricted"] is True
    assert set(gate["allowed_extensions"]) == set(ALLOWED_MEDIA_EXTENSIONS)
    assert "anywhere else on this machine" in (agents / "agents" / "orin.md").read_text(encoding="utf-8")


def test_prompt_note_names_extra_folders_and_tells_to_use_absolute_paths(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    extra = tmp_path / "extra"
    ws.mkdir()
    extra.mkdir()
    note = antigravity_media_prompt_note(ws, additional_roots=[extra])
    assert "ABSOLUTE path" in note
    assert "see an image" in note and "hear an audio file" in note
    assert extra.resolve().as_posix() in note
    assert "view_file" in note  # the Orin tool only tells the path


# --- the gate: several roots, extensions, unrestricted ----------------------------------------


def test_gate_accepts_any_configured_root_and_denies_the_rest(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    extra = tmp_path / "extra"
    other = tmp_path / "other"
    in_ws = _write(ws / ".orin" / "tool-media" / "a.png")
    in_extra = _write(extra / "b.jpg")
    outside = _write(other / "c.png")
    cfg = {
        "allowed_roots": [str(ws), str(extra)],
        "allowed_extensions": sorted(ALLOWED_IMAGE_EXTENSIONS),
        "max_bytes": 1024,
    }
    assert decide_media_gate(_hook(str(in_ws)), cfg)["decision"] == "allow"
    assert decide_media_gate(_hook(str(in_extra)), cfg)["decision"] == "allow"
    deny = decide_media_gate(_hook(str(outside)), cfg)
    assert deny["decision"] == "deny" and "outside" in deny["reason"]
    txt = _write(ws / "notes.txt", b"hello")
    env = _write(ws / ".env", b"SECRET=1")
    for text_file in (txt, env):
        assert decide_media_gate(_hook(str(text_file)), cfg)["decision"] == "deny"


def test_gate_denies_audio_and_video_when_the_model_only_sees_images(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    wav = _write(ws / "a.wav", b"RIFF")
    mp4 = _write(ws / "a.mp4", b"MP4")
    png = _write(ws / "a.png")
    cfg = {"allowed_roots": [str(ws)], "allowed_extensions": sorted(ALLOWED_IMAGE_EXTENSIONS)}
    assert decide_media_gate(_hook(str(png)), cfg)["decision"] == "allow"
    for blocked in (wav, mp4):
        result = decide_media_gate(_hook(str(blocked)), cfg)
        assert result["decision"] == "deny"
        assert "not allowed by native read_file" in result["reason"]


def test_gate_reads_the_legacy_single_allowed_root_config(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    png = _write(ws / "a.png")
    legacy = {"allowed_root": str(ws), "allowed_extensions": sorted(ALLOWED_MEDIA_EXTENSIONS)}
    assert decide_media_gate(_hook(str(png)), legacy)["decision"] == "allow"
    assert decide_media_gate(_hook(str(_write(tmp_path / "x" / "b.png"))), legacy)["decision"] == "deny"


def test_gate_rejects_malformed_multiroot_config(tmp_path: Path) -> None:
    png = _write(tmp_path / "ws" / "a.png")
    for bad in (
        {"allowed_roots": "not-a-list"},
        {"allowed_roots": [1, 2]},
        {"allowed_roots": [str(tmp_path / "ws")], "unrestricted": "yes"},
    ):
        assert decide_media_gate(_hook(str(png)), bad)["decision"] == "deny"


def test_unrestricted_gate_reads_media_anywhere_but_nothing_else(tmp_path: Path) -> None:
    elsewhere = _write(tmp_path / "far" / "away" / "shot.png")
    cfg = {"allowed_roots": [], "unrestricted": True, "allowed_extensions": sorted(ALLOWED_MEDIA_EXTENSIONS)}
    assert decide_media_gate(_hook(str(elsewhere)), cfg)["decision"] == "allow"

    for secret in (
        _write(tmp_path / "far" / "notes.txt", b"hi"),
        _write(tmp_path / "far" / ".env", b"K=1"),
        _write(tmp_path / "far" / "script.py", b"print(1)"),
        _write(tmp_path / "far" / "config.json", b"{}"),
    ):
        assert decide_media_gate(_hook(str(secret)), cfg)["decision"] == "deny"

    # relative path, traversal, UNC / device paths, missing file
    assert decide_media_gate(_hook("far/away/shot.png"), cfg)["decision"] == "deny"
    assert decide_media_gate(_hook(str(tmp_path / "far" / ".." / "far" / "away" / "shot.png")), cfg)["decision"] == "deny"
    for unc in (r"\\server\share\x.png", "//server/share/x.png", rf"\\?\{elsewhere}", rf"\\.\{elsewhere}"):
        assert decide_media_gate(_hook(unc), cfg)["decision"] == "deny"
    assert decide_media_gate(_hook(str(tmp_path / "far" / "away" / "missing.png")), cfg)["decision"] == "deny"


def test_unrestricted_gate_refuses_symlinked_files_and_agys_own_store(tmp_path: Path) -> None:
    real = _write(tmp_path / "real" / "shot.png")
    cfg = {"unrestricted": True, "allowed_extensions": sorted(ALLOWED_MEDIA_EXTENSIONS)}
    link = tmp_path / "linked.png"
    try:
        link.symlink_to(real)
    except OSError:
        link = None  # no privilege to create symlinks here
    if link is not None:
        assert decide_media_gate(_hook(str(link)), cfg)["decision"] == "deny"
        dir_link = tmp_path / "dirlink"
        try:
            dir_link.symlink_to(real.parent, target_is_directory=True)
        except OSError:
            dir_link = None
        if dir_link is not None:
            assert decide_media_gate(_hook(str(dir_link / "shot.png")), cfg)["decision"] == "deny"
    brain = _write(tmp_path / ".gemini" / "antigravity-cli" / "brain" / "c1" / "media_0.png")
    result = decide_media_gate(_hook(str(brain)), cfg)
    assert result["decision"] == "deny" and "conversation store" in result["reason"]


def test_is_allowed_media_path_accepts_roots_as_a_keyword_list(tmp_path: Path) -> None:
    a = _write(tmp_path / "a" / "x.png")
    b = _write(tmp_path / "b" / "y.png")
    roots = [tmp_path / "a", tmp_path / "b"]
    assert is_allowed_media_path(str(a), allowed_roots=roots)[0]
    assert is_allowed_media_path(str(b), allowed_roots=roots)[0]
    assert not is_allowed_media_path(str(_write(tmp_path / "c" / "z.png")), allowed_roots=roots)[0]
    assert not is_allowed_media_path(str(a), allowed_roots=[])[0]


# --- translator and forbidden filter: any root, kill only a completed read outside ------------


def _policy(*roots: Path, unrestricted: bool = False, extensions: frozenset[str] = ALLOWED_MEDIA_EXTENSIONS):
    return MediaReadPolicy(
        roots=tuple(str(root) for root in roots), unrestricted=unrestricted, extensions=extensions
    )


def test_translator_and_filter_accept_a_read_in_any_root_and_kill_a_completed_read_outside(tmp_path: Path) -> None:
    ws, extra, other = tmp_path / "ws", tmp_path / "extra", tmp_path / "other"
    in_ws = _write(ws / "a.png")
    in_extra = _write(extra / "b.png")
    outside = _write(other / "c.png")
    secret = _write(ws / "secret.txt", b"nope")
    policy = _policy(ws, extra)
    translator = AntigravityStreamTranslator(model="claude-opus-5-5-high", media_policy=policy)

    for ok in (in_ws, in_extra):
        for state in ("ACTIVE", "DONE"):
            line = _step("read_file", str(ok), state)
            assert translator.feed_line(line) == []
            assert find_forbidden_item_kinds(
                FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS, raw_line=line, media_policy=policy
            ) == []

    for bad in (outside, secret):
        done = _step("read_file", str(bad), "DONE")
        events = translator.feed_line(done)
        assert len(events) == 1 and events[0].tool_name == "read_file"
        assert find_forbidden_item_kinds(
            FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS, raw_line=done, media_policy=policy
        ) == ["read_file"]


def test_a_read_file_the_hook_denied_is_not_a_breach_but_a_done_one_outside_is(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    txt = _write(ws / "notes.txt", b"hi")
    policy = _policy(ws, extensions=ALLOWED_IMAGE_EXTENSIONS)
    translator = AntigravityStreamTranslator(model="claude-opus-5-5-high", media_policy=policy)
    for state, err in (("ACTIVE", False), ("ERROR", True)):
        line = _step("read_file", str(txt), state, with_error=err)
        assert translator.feed_line(line) == []
        assert find_forbidden_item_kinds(
            FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS, raw_line=line, media_policy=policy
        ) == []
    done = _step("read_file", str(txt), "DONE")
    assert find_forbidden_item_kinds(
        FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS, raw_line=done, media_policy=policy
    ) == ["read_file"]
    # audio on a model that only sees images: a completed read is a breach as well
    wav = _write(ws / "a.wav", b"RIFF")
    assert find_forbidden_item_kinds(
        FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
        raw_line=_step("read_file", str(wav), "DONE"),
        media_policy=policy,
    ) == ["read_file"]


def test_without_a_policy_every_native_read_file_stays_forbidden(tmp_path: Path) -> None:
    png = _write(tmp_path / "a.png")
    for state in ("ACTIVE", "ERROR", "DONE"):
        assert find_forbidden_item_kinds(
            FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
            raw_line=_step("read_file", str(png), state),
        ) == ["read_file"]
    translator = AntigravityStreamTranslator(model="gpt-oss-120b-medium")
    assert len(translator.feed_line(_step("read_file", str(png), "ACTIVE"))) == 1


def test_filter_unrestricted_policy_accepts_media_outside_but_not_text(tmp_path: Path) -> None:
    far = _write(tmp_path / "far" / "a.png")
    txt = _write(tmp_path / "far" / "a.txt", b"x")
    policy = _policy(unrestricted=True)
    assert find_forbidden_item_kinds(
        FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
        raw_line=_step("read_file", str(far), "DONE"),
        media_policy=policy,
    ) == []
    assert find_forbidden_item_kinds(
        FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
        raw_line=_step("read_file", str(txt), "DONE"),
        media_policy=policy,
    ) == ["read_file"]


def test_policy_from_the_turn_scope(tmp_path: Path) -> None:
    ws, extra = tmp_path / "ws", tmp_path / "extra"
    ws.mkdir()
    extra.mkdir()
    policy = antigravity_native_media_policy(
        "claude-opus-5-5-high", ws, additional_roots=[extra, tmp_path / "gone"]
    )
    assert policy is not None
    assert policy.roots == (str(ws.resolve()), str(extra.resolve()))
    assert policy.extensions == ALLOWED_IMAGE_EXTENSIONS
    assert antigravity_native_media_policy("gpt-oss-120b-medium", ws) is None
    assert antigravity_native_media_policy("claude-opus-5-5-high", None) is None


# --- through the real native loop with a fake agy ---------------------------------------------


class _Workspace:
    def __init__(self, root: Path, extra: tuple[Path, ...] = (), unrestricted: bool = False) -> None:
        self.root = root
        self.additional_roots = extra
        self.unrestricted = unrestricted


def _executor_with(workspace: _Workspace) -> _EchoExecutor:
    class _Toolset:
        pass

    toolset = _Toolset()
    toolset.workspace = workspace  # type: ignore[attr-defined]
    executor = _EchoExecutor()
    executor.toolset = toolset  # type: ignore[attr-defined]
    return executor


def _run_turn(tmp_path: Path, model: str, prompt: str, workspace: _Workspace) -> Any:
    cwd = tmp_path / "isolated"
    cwd.mkdir(exist_ok=True)
    provider = ClaudeCliProvider(binary=FAKE_ANTIGRAVITY_SCRIPT, adapter=AntigravityCliAdapter())
    request = NativeTurnRequest(
        model=model,
        system_prompt="You are Orin.",
        user_content=[{"type": "text", "text": prompt}],
        executor=_executor_with(workspace),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=2,
        cwd=cwd,
    )
    handle = start_native_turn_for_provider(provider, request)
    try:
        return handle, handle.run_until_result(_RecordingSink())
    finally:
        handle.close()


def test_native_turn_wires_every_root_and_does_not_die_on_a_hook_denied_read(tmp_path: Path) -> None:
    ws, extra = tmp_path / "ws", tmp_path / "extra"
    ws.mkdir()
    extra.mkdir()
    txt = _write(ws / "notes.txt", b"hi")
    handle, outcome = _run_turn(
        tmp_path,
        "claude-opus-5-5-high",
        f"read it NATIVE_READ_DENIED={txt.as_posix()}",
        _Workspace(ws, (extra,)),
    )
    assert outcome.result is not None and not outcome.result.is_error
    assert outcome.final_text.strip().endswith("carried on after read_file")


def test_native_turn_stops_a_completed_read_outside_the_policy(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    txt = _write(ws / "notes.txt", b"hi")
    with pytest.raises(CliBuiltinToolDetour):
        _run_turn(
            tmp_path,
            "claude-opus-5-5-high",
            f"read it NATIVE_READ_DONE={txt.as_posix()}",
            _Workspace(ws),
        )


def test_native_turn_of_a_text_only_model_still_stops_any_read_file(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    png = _write(ws / "a.png")
    with pytest.raises(CliBuiltinToolDetour):
        _run_turn(
            tmp_path,
            "gpt-oss-120b-medium",
            f"read it NATIVE_READ_DENIED={png.as_posix()}",
            _Workspace(ws),
        )


def test_native_turn_passes_additional_roots_and_unrestricted_to_the_wiring(tmp_path: Path) -> None:
    ws, extra = tmp_path / "ws", tmp_path / "extra"
    ws.mkdir()
    extra.mkdir()
    png = _write(extra / "x.png")
    # Done read in an extra root of the same turn is inside the policy: the turn completes.
    _, outcome = _run_turn(
        tmp_path,
        "claude-opus-5-5-high",
        f"look NATIVE_READ_DONE={png.as_posix()}",
        _Workspace(ws, (extra,)),
    )
    assert outcome.result is not None and not outcome.result.is_error
    far = _write(tmp_path / "far" / "y.png")
    _, outcome2 = _run_turn(
        tmp_path,
        "gemini-3.8-flash-high",
        f"look NATIVE_READ_DONE={far.as_posix()}",
        _Workspace(ws, unrestricted=True),
    )
    assert outcome2.result is not None and not outcome2.result.is_error
    # ... but outside a restricted turn it is a breach
    with pytest.raises(CliBuiltinToolDetour):
        _run_turn(
            tmp_path,
            "gemini-3.8-flash-high",
            f"look NATIVE_READ_DONE={far.as_posix()}",
            _Workspace(ws),
        )


def test_encode_user_turn_treats_an_image_with_a_path_like_audio_and_video(tmp_path: Path) -> None:
    from agentos.providers.antigravity_cli import encode_antigravity_user_turn
    from agentos.providers.antigravity_cli.command import (
        ANTIGRAVITY_OMITTED_MEDIA_NOTE,
        format_antigravity_workspace_media_note,
    )

    ws = tmp_path / "ws"
    shot = _write(ws / ".orin" / "uploads" / "tela.png")
    clip = _write(ws / "clip.mp4", b"MP4")
    raw = encode_antigravity_user_turn(
        [
            {"type": "image", "path": ".orin/uploads/tela.png"},
            {"type": "video", "source": {"path": str(clip)}},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
            {"type": "text", "text": "descreva"},
        ],
        workspace_root=ws,
    )
    texts = [block["text"] for block in json.loads(raw.decode("utf-8"))["message"]["content"]]
    assert texts[0] == format_antigravity_workspace_media_note(shot)
    assert shot.resolve().as_posix() in texts[0] and "Para ver a imagem" in texts[0]
    assert "read_file" in texts[0]
    assert clip.resolve().as_posix() in texts[1] and "view_file do Orin" in texts[1]
    assert texts[2] == ANTIGRAVITY_OMITTED_MEDIA_NOTE and "caminho ABSOLUTO" in texts[2]
    assert texts[3] == "descreva"
