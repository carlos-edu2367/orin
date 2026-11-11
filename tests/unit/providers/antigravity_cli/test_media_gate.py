from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pytest
from sqlalchemy import update

from agentos.launcher import cli as launcher_cli
from agentos.persistence.postgres.schema import provider_model_catalog
from agentos.providers.antigravity_cli import (
    ALLOWED_MEDIA_EXTENSIONS,
    DEFAULT_MAX_MEDIA_BYTES,
    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    MEDIA_GATE_CONFIG_FILENAME,
    AntigravityCliAdapter,
    AntigravityStreamTranslator,
    build_antigravity_command,
    build_media_gate_hook_command,
    decide_media_gate,
    encode_antigravity_user_turn,
    wire_antigravity_tool_server,
)
from agentos.providers.antigravity_cli import media_gate
from agentos.providers.antigravity_cli.catalog import antigravity_input_modalities
from agentos.providers.antigravity_cli.command import (
    ANTIGRAVITY_OMITTED_MEDIA_NOTE,
    format_antigravity_workspace_media_note,
)
from agentos.providers.claude_cli.native_loop import (
    NativeTurnRequest,
    find_forbidden_item_kinds,
    start_native_turn_for_provider,
)
from agentos.providers.claude_cli.provider import ClaudeCliProvider
from agentos.providers.cli_adapter import CliEventKind, CliTurnRequest
from tests.unit.providers.antigravity_cli.test_adapter_conformance import (
    FAKE_ANTIGRAVITY_SCRIPT,
    _EchoExecutor,
    _RecordingSink,
)
from tests.unit.providers.antigravity_cli.test_catalog_vision import _service
from tests.unit.providers.cli_adapter_conformance import FakeToolServerHandle


def _gate_config(root: Path, *, max_bytes: int = DEFAULT_MAX_MEDIA_BYTES) -> dict[str, Any]:
    return {
        "allowed_root": str(root.resolve()),
        "allowed_extensions": sorted(ALLOWED_MEDIA_EXTENSIONS),
        "max_bytes": max_bytes,
    }


def _hook_payload(path: object, *, tool_name: str = "read_file") -> dict[str, Any]:
    return {
        "toolCall": {
            "name": tool_name,
            "args": {"Path": path},
        },
        "stepIdx": 1,
        "conversationId": "conv-test-1",
    }


@pytest.mark.parametrize(
    "ext",
    [
        ".mp3",
        ".wav",
        ".ogg",
        ".opus",
        ".m4a",
        ".aac",
        ".flac",
        ".webm",
        ".mp4",
        ".mov",
        ".mkv",
        ".m4v",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
    ],
)
def test_media_gate_allows_supported_media_inside_workspace(
    tmp_path: Path, ext: str
) -> None:
    ws = tmp_path / "workspace"
    sub = ws / "subdir"
    sub.mkdir(parents=True)
    media_file = sub / f"sample{ext}"
    media_file.write_bytes(b"FAKE_MEDIA_DATA")

    decision = decide_media_gate(_hook_payload(str(media_file)), _gate_config(ws))
    assert decision == {"decision": "allow", "reason": ""}


def test_media_gate_denies_outside_root_traversal_relative_and_wrong_extensions(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    outside_audio = outside / "leak.ogg"
    outside_audio.write_bytes(b"OggS")

    inside_audio = ws / "ok.ogg"
    inside_audio.write_bytes(b"OggS")

    secret_txt = ws / "secret.txt"
    secret_txt.write_text("TOP_SECRET", encoding="utf-8")

    code_py = ws / "script.py"
    code_py.write_text("print(1)", encoding="utf-8")

    no_ext = ws / "README"
    no_ext.write_text("hi", encoding="utf-8")

    cfg = _gate_config(ws)

    # 1. Outside workspace root
    res_outside = decide_media_gate(_hook_payload(str(outside_audio)), cfg)
    assert res_outside["decision"] == "deny"

    # 2. Traversal `..` escaping root
    traversal_escape = f"{ws}{os.sep}..{os.sep}outside{os.sep}leak.ogg" if "os" in globals() else str(ws / ".." / "outside" / "leak.ogg")
    res_trav_out = decide_media_gate(_hook_payload(traversal_escape), cfg)
    assert res_trav_out["decision"] == "deny"

    # 3. Traversal `..` staying inside root is also rejected
    traversal_in = str(ws / "sub" / ".." / "ok.ogg")
    res_trav_in = decide_media_gate(_hook_payload(traversal_in), cfg)
    assert res_trav_in["decision"] == "deny"

    # 4. Relative path
    res_rel = decide_media_gate(_hook_payload("ok.ogg"), cfg)
    assert res_rel["decision"] == "deny"

    # 5. Text / code / no-extension files inside workspace
    for denied_file in (secret_txt, code_py, no_ext, ws):
        res = decide_media_gate(_hook_payload(str(denied_file)), cfg)
        assert res["decision"] == "deny", f"Expected deny for {denied_file}"


def test_media_gate_denies_ads_unc_device_paths_oversize_and_non_read_file(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    audio = ws / "clip.mp3"
    audio.write_bytes(b"x" * 1024)
    cfg = _gate_config(ws, max_bytes=512)

    # Oversize (> 512 bytes)
    res_oversize = decide_media_gate(_hook_payload(str(audio)), cfg)
    assert res_oversize["decision"] == "deny"
    assert "exceeds" in res_oversize["reason"]

    cfg_normal = _gate_config(ws)

    # Alternate Data Streams (ADS)
    for ads_path in (
        f"{audio}:secret.txt",
        f"{audio}:$DATA",
        f"{ws}\\clip.mp3:zone.identifier",
    ):
        res_ads = decide_media_gate(_hook_payload(ads_path), cfg_normal)
        assert res_ads["decision"] == "deny"
        assert "Alternate data streams" in res_ads["reason"]

    # UNC and Windows device paths
    for unc_path in (
        r"\\server\share\clip.mp3",
        "//server/share/clip.mp3",
        f"\\\\?\\{audio}",
        f"\\\\.\\{audio}",
    ):
        res_unc = decide_media_gate(_hook_payload(unc_path), cfg_normal)
        assert res_unc["decision"] == "deny"
        assert "UNC and device paths" in res_unc["reason"]

    # Wrong tool name
    res_tool = decide_media_gate(
        _hook_payload(str(audio), tool_name="run_command"), cfg_normal
    )
    assert res_tool["decision"] == "deny"


def test_media_gate_denies_symlinks_and_junctions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_mp3 = outside / "outside.mp3"
    outside_mp3.write_bytes(b"ID3")
    inside_mp3 = ws / "inside.mp3"
    inside_mp3.write_bytes(b"ID3")

    sym_file = ws / "linked.mp3"
    symlink_created = False
    try:
        sym_file.symlink_to(outside_mp3)
        symlink_created = True
    except OSError:
        symlink_created = False

    cfg = _gate_config(ws)
    if symlink_created:
        res_sym = decide_media_gate(_hook_payload(str(sym_file)), cfg)
        assert res_sym["decision"] == "deny"

    # Deterministic check via monkeypatch even when Windows privileges block symlink creation
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: True if self.name == "inside.mp3" else real_is_symlink(self),
    )
    res_patched = decide_media_gate(_hook_payload(str(inside_mp3)), cfg)
    assert res_patched["decision"] == "deny"
    assert "Symlinks and junctions" in res_patched["reason"]


def test_media_gate_main_handles_malformed_stdin_and_runs_isolated_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    audio = ws / "voice.ogg"
    audio.write_bytes(b"OggS")
    secret = ws / "secret.txt"
    secret.write_text("NOPE", encoding="utf-8")

    agents_dir = tmp_path / "turn_cwd" / ".agents"
    agents_dir.mkdir(parents=True)
    cfg_file = agents_dir / MEDIA_GATE_CONFIG_FILENAME
    cfg_file.write_text(json.dumps(_gate_config(ws)), encoding="utf-8")

    # Malformed stdin in-process never raises and always prints deny
    for bad_stdin in ("", "   ", "{not json", "[]", '{"toolCall": "bad"}'):
        monkeypatch.chdir(agents_dir)
        monkeypatch.setattr(sys, "stdin", io.StringIO(bad_stdin))
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        rc = media_gate.main([])
        assert rc == 0
        out = json.loads(buf.getvalue().strip())
        assert out["decision"] == "deny"

    # Subprocess execution with `-I` (isolated stdlib-only mode, as used in development)
    script_path = Path(media_gate.__file__).resolve()
    proc_allow = subprocess.run(
        [sys.executable, "-I", str(script_path)],
        input=json.dumps(_hook_payload(str(audio))),
        cwd=str(agents_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    assert json.loads(proc_allow.stdout.strip()) == {
        "decision": "allow",
        "reason": "",
    }

    proc_deny = subprocess.run(
        [sys.executable, "-I", str(script_path)],
        input=json.dumps(_hook_payload(str(secret))),
        cwd=str(agents_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    deny_obj = json.loads(proc_deny.stdout.strip())
    assert deny_obj["decision"] == "deny"


def test_frozen_vs_dev_hook_command_and_launcher_subcommand_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_exe = tmp_path / "Program Files" / "Orin" / "runtime" / "orin.exe"
    fake_script = tmp_path / "path with spaces" / "media_gate.py"

    cmd_dev = build_media_gate_hook_command(
        frozen=False, executable=fake_exe, script_path=fake_script
    )
    assert "media_gate.py" in cmd_dev
    assert "-I" in cmd_dev
    if sys.platform == "win32":
        # Must not contain double quotes on Windows because Go's exec.Command("cmd", "/c", cmd) escapes them as \"
        assert '"' not in cmd_dev

    cmd_frozen = build_media_gate_hook_command(
        frozen=True, executable=fake_exe, script_path=fake_script
    )
    assert "internal-agy-media-gate" in cmd_frozen
    if sys.platform == "win32":
        assert '"' not in cmd_frozen

    # Hidden from public `--help`
    help_text = launcher_cli.build_parser().format_help()
    assert "internal-agy-media-gate" not in help_text

    # Dispatch via launcher_cli.main(["internal-agy-media-gate"])
    ws = tmp_path / "ws"
    ws.mkdir()
    vid = ws / "clip.mp4"
    vid.write_bytes(b"MP4")
    agents_dir = tmp_path / "agents_cwd"
    agents_dir.mkdir()
    (agents_dir / MEDIA_GATE_CONFIG_FILENAME).write_text(
        json.dumps(_gate_config(ws)), encoding="utf-8"
    )

    monkeypatch.chdir(agents_dir)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(_hook_payload(str(vid))))
    )
    out_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_buf)
    rc = launcher_cli.main(["internal-agy-media-gate"])
    assert rc == 0
    assert json.loads(out_buf.getvalue().strip()) == {
        "decision": "allow",
        "reason": "",
    }

    # Also works when invoked as `orin-code.exe` (`argv=None`)
    monkeypatch.setattr(
        sys, "argv", ["C:/Orin/runtime/orin-code.exe", "internal-agy-media-gate"]
    )
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(_hook_payload(str(vid))))
    )
    out_buf2 = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_buf2)
    rc2 = launcher_cli.main()
    assert rc2 == 0
    assert json.loads(out_buf2.getvalue().strip()) == {
        "decision": "allow",
        "reason": "",
    }


def test_wiring_gemini_enables_media_gate_while_claude_stays_byte_for_byte_unchanged(
    tmp_path: Path,
) -> None:
    handle = FakeToolServerHandle()
    ws_root = tmp_path / "real_workspace"
    ws_root.mkdir()
    sys_file = tmp_path / "sys.txt"
    sys_file.write_text("You are Orin.", encoding="utf-8")

    # 1. Baseline default wiring (no model/workspace_root)
    base_dir = tmp_path / "agy_base"
    base_wiring = wire_antigravity_tool_server(
        handle, base_dir, server_name="orin", system_prompt_file=sys_file
    )

    # 2. Claude model with workspace_root -> must be byte-for-byte identical to baseline!
    claude_dir = tmp_path / "agy_claude"
    claude_wiring = wire_antigravity_tool_server(
        handle,
        claude_dir,
        server_name="orin",
        system_prompt_file=sys_file,
        model="claude-sonnet-5-5-low",
        workspace_root=ws_root,
    )
    assert len(claude_wiring.files) == 3
    assert not (claude_dir / ".agents" / "hooks.json").exists()
    assert not (claude_dir / ".agents" / MEDIA_GATE_CONFIG_FILENAME).exists()
    assert (claude_dir / ".agents" / "agents" / "orin.md").read_bytes() == (
        base_dir / ".agents" / "agents" / "orin.md"
    ).read_bytes()
    assert (claude_dir / ".agents" / "mcp_config.json").read_bytes() == (
        base_dir / ".agents" / "mcp_config.json"
    ).read_bytes()
    claude_settings = json.loads(
        (claude_dir / ".agents" / "orin_settings.json").read_text(encoding="utf-8")
    )
    assert claude_settings["permissions"]["allow"] == ["mcp(orin/*)"]
    assert "read_file(*)" in claude_settings["permissions"]["deny"]

    # 3. Gemini model with workspace_root -> enables native read_file media gate
    gemini_dir = tmp_path / "agy_gemini"
    gemini_wiring = wire_antigravity_tool_server(
        handle,
        gemini_dir,
        server_name="orin",
        system_prompt_file=sys_file,
        model="google-antigravity/gemini-3.8-flash-medium",
        workspace_root=ws_root,
    )
    assert len(gemini_wiring.files) == 5
    agent_md = gemini_dir / ".agents" / "agents" / "orin.md"
    settings_json = gemini_dir / ".agents" / "orin_settings.json"
    hooks_json = gemini_dir / ".agents" / "hooks.json"
    gate_json = gemini_dir / ".agents" / MEDIA_GATE_CONFIG_FILENAME

    md_text = agent_md.read_text(encoding="utf-8")
    assert "excludeDefaultComponents: true" in md_text
    assert "tools: [read_file]" in md_text
    assert ws_root.resolve().as_posix() in md_text
    assert "ABSOLUTE path" in md_text

    settings_data = json.loads(settings_json.read_text(encoding="utf-8"))
    assert settings_data["permissions"]["allow"] == [
        "mcp(orin/*)",
        f"read_file({ws_root.resolve().as_posix()})",
    ]
    assert "read_file(*)" not in settings_data["permissions"]["deny"]
    for still_denied in (
        "command(*)",
        "write_file(*)",
        "read_url(*)",
        "execute_url(*)",
        "unsandboxed(*)",
    ):
        assert still_denied in settings_data["permissions"]["deny"]

    hooks_data = json.loads(hooks_json.read_text(encoding="utf-8"))
    pre_hooks = hooks_data["orin-media-gate"]["PreToolUse"]
    assert len(pre_hooks) == 1
    assert pre_hooks[0]["matcher"] == "read_file"
    assert pre_hooks[0]["hooks"][0]["command"] == build_media_gate_hook_command()
    assert pre_hooks[0]["hooks"][0]["timeout"] == 10

    gate_data = json.loads(gate_json.read_text(encoding="utf-8"))
    assert gate_data["allowed_root"] == str(ws_root.resolve())
    assert set(gate_data["allowed_extensions"]) == set(ALLOWED_MEDIA_EXTENSIONS)
    assert gate_data["max_bytes"] == DEFAULT_MAX_MEDIA_BYTES

    # Updating instructions via build_antigravity_command keeps the media prompt note
    build_antigravity_command(
        CliTurnRequest(
            binary="agy",
            system_prompt_file=sys_file,
            model="gemini-3.8-flash-medium",
            tool_server_wiring=gemini_wiring,
            workspace_root=ws_root,
        ),
        gemini_wiring,
    )
    updated_md = agent_md.read_text(encoding="utf-8")
    assert "tools: [read_file]" in updated_md
    assert ws_root.resolve().as_posix() in updated_md


def test_stream_translator_and_forbidden_filter_allow_gated_read_file_and_block_everything_else(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    ok_audio = ws / "neutro.ogg"
    ok_audio.write_bytes(b"OggS")
    ok_video = ws / "vid.mp4"
    ok_video.write_bytes(b"MP4")
    secret_txt = ws / "secret.txt"
    secret_txt.write_text("secret", encoding="utf-8")
    outside_audio = outside / "neutro.ogg"
    outside_audio.write_bytes(b"OggS")

    tr = AntigravityStreamTranslator(
        model="gemini-3.8-flash-medium", allowed_media_root=ws
    )

    def _step_line(
        tool_name: str, path_val: str, *, key: str = "event"
    ) -> str:
        return json.dumps(
            {
                key: "step_update",
                "step_update": {
                    "conversation_id": "conv-1",
                    "step_idx": 4,
                    "step_type": "tool",
                    "state": "DONE",
                    "tool_name": tool_name,
                    "tool_info": {
                        "name": tool_name,
                        "parameters": {"Path": path_val},
                    },
                },
            }
        )

    # 1. Allowed audio/video inside workspace -> no forbidden events from translator or native_loop filter
    for valid_media in (ok_audio, ok_video):
        for packet_key in ("event", "type"):
            line = _step_line("read_file", str(valid_media), key=packet_key)
            assert tr.feed_line(line) == []
            assert (
                find_forbidden_item_kinds(
                    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
                    raw_line=line,
                    allowed_media_root=ws,
                )
                == []
            )

    # 2. read_file on text file inside workspace -> flagged as forbidden
    txt_line = _step_line("read_file", str(secret_txt), key="type")
    txt_events = tr.feed_line(txt_line)
    assert len(txt_events) == 1
    assert txt_events[0].kind == CliEventKind.TOOL_USE_START
    assert txt_events[0].tool_name == "read_file"
    assert find_forbidden_item_kinds(
        FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
        raw_line=txt_line,
        allowed_media_root=ws,
    ) == ["read_file"]

    # 3. read_file on audio file outside workspace -> flagged as forbidden
    out_line = _step_line("read_file", str(outside_audio), key="type")
    out_events = tr.feed_line(out_line)
    assert len(out_events) == 1
    assert out_events[0].tool_name == "read_file"
    assert find_forbidden_item_kinds(
        FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
        raw_line=out_line,
        allowed_media_root=ws,
    ) == ["read_file"]

    # 4. Without allowed_media_root (e.g. Claude turn), even valid media is flagged
    tr_claude = AntigravityStreamTranslator(model="claude-sonnet-5-5-low")
    media_line = _step_line("read_file", str(ok_audio), key="type")
    claude_events = tr_claude.feed_line(media_line)
    assert len(claude_events) == 1
    assert claude_events[0].tool_name == "read_file"
    assert find_forbidden_item_kinds(
        FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
        raw_line=media_line,
        allowed_media_root=None,
    ) == ["read_file"]

    # 5. Every other native tool (even targeting a valid media file) is flagged
    for other_tool in (
        "run_command",
        "write_to_file",
        "view_file",
        "list_dir",
        "grep_search",
        "unknown_native_tool",
    ):
        other_line = _step_line(other_tool, str(ok_audio), key="type")
        other_events = tr.feed_line(other_line)
        assert len(other_events) == 1
        assert other_events[0].kind == CliEventKind.TOOL_USE_START
        assert other_events[0].tool_name in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS
        assert find_forbidden_item_kinds(
            FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
            raw_line=other_line,
            allowed_media_root=ws,
        )


def test_start_native_turn_extracts_executor_workspace_root_and_cleans_up_files(
    tmp_path: Path,
) -> None:
    ws_root = tmp_path / "conv_workspace"
    ws_root.mkdir()
    cwd = tmp_path / "isolated_base"
    cwd.mkdir()

    class _WorkspaceRef:
        root = ws_root

    class _ToolsetRef:
        workspace = _WorkspaceRef()

    class _WorkspaceEchoExecutor(_EchoExecutor):
        toolset = _ToolsetRef()

    adapter = AntigravityCliAdapter()
    provider = ClaudeCliProvider(binary=FAKE_ANTIGRAVITY_SCRIPT, adapter=adapter)
    req = NativeTurnRequest(
        model="gemini-3.8-flash-medium",
        system_prompt="You are Orin.",
        user_content=[{"type": "text", "text": "Hello Turn 1"}],
        executor=_WorkspaceEchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=2,
        cwd=cwd,
    )
    handle = start_native_turn_for_provider(provider, req)
    proc_cwd = handle.process_cwd
    assert proc_cwd is not None
    try:
        assert (proc_cwd / ".agents" / "hooks.json").is_file()
        assert (proc_cwd / ".agents" / MEDIA_GATE_CONFIG_FILENAME).is_file()
        gate_cfg = json.loads(
            (proc_cwd / ".agents" / MEDIA_GATE_CONFIG_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        assert gate_cfg["allowed_root"] == str(ws_root.resolve())
        outcome = handle.run_until_result(_RecordingSink())
        assert outcome.result is not None and not outcome.result.is_error
    finally:
        handle.close()

    assert not proc_cwd.exists()


def test_encode_antigravity_user_turn_points_to_workspace_audio_video_or_falls_back(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    uploads = ws / ".orin" / "uploads"
    uploads.mkdir(parents=True)
    audio_file = uploads / "neutro.ogg"
    audio_file.write_bytes(b"OggS")
    video_file = ws / "vid.mp4"
    video_file.write_bytes(b"MP4")

    raw = encode_antigravity_user_turn(
        [
            {"type": "audio", "path": ".orin/uploads/neutro.ogg"},
            {"type": "video", "source": {"path": str(video_file)}},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
            },
            {"type": "audio", "path": "missing_not_on_disk.ogg"},
            {"type": "text", "text": "Transcreva e descreva."},
        ],
        workspace_root=ws,
    )
    decoded = json.loads(raw.decode("utf-8"))
    texts = [b["text"] for b in decoded["message"]["content"]]
    assert texts == [
        format_antigravity_workspace_media_note(audio_file),
        format_antigravity_workspace_media_note(video_file),
        ANTIGRAVITY_OMITTED_MEDIA_NOTE,
        "Transcreva e descreva.",
    ]
    assert audio_file.resolve().as_posix() in texts[0]
    assert "read_file" in texts[0]
    assert video_file.resolve().as_posix() in texts[1]


def test_catalog_refreshes_gemini_stored_with_only_text_and_image_modalities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert antigravity_input_modalities("gemini-3.8-flash-medium") == (
        "text",
        "image",
        "audio",
        "video",
    )
    assert antigravity_input_modalities("claude-sonnet-5-5-low") == (
        "text",
        "image",
    )

    engine, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "google-antigravity")
    # Simulate a catalog row stored before audio/video support was added.
    with engine.begin() as connection:
        connection.execute(
            update(provider_model_catalog)
            .where(provider_model_catalog.c.model_id == "gemini-4-argon-high")
            .values(input_modalities=["text", "image"])
        )

    models = {item.model_id: item for item in service.list(ctx, "google-antigravity")}
    assert models["gemini-4-argon-high"].input_modalities == (
        "text",
        "image",
        "audio",
        "video",
    )
