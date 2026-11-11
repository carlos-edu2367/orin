"""Unit tests for subscription CLI image generation (`CliImageGenerationClient`, policy, catalog, stream)."""
from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time
from typing import Any

import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.cli_media_client import (
    ANTIGRAVITY_IMAGE_MODEL_ID,
    CODEX_IMAGE_MODEL_ID,
    CliImageGenerationClient,
    detect_image_extension,
)
from agentos.agentic.media_client import MAX_MEDIA_DOWNLOAD_BYTES, MediaGenerationError
from agentos.agentic.native_turn import builtin_tool_reminder
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.provider_configuration import (
    PostgresProviderConfigurationAdapter,
)
from agentos.persistence.postgres.provider_models import (
    PostgresProviderCatalogRepository,
)
from agentos.persistence.postgres.schema import (
    media_model_selections,
    metadata,
    provider_configurations,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.modalities import (
    SameProviderMediaModelPolicy,
    infer_output_modalities,
)
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import ProviderModelCatalogService
from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.antigravity_cli.command import build_antigravity_command
from agentos.providers.antigravity_cli.stream import (
    ANTIGRAVITY_BUILTIN_TOOL_ALTERNATIVES,
    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    RECOVERABLE_ANTIGRAVITY_BUILTIN_TOOLS,
    AntigravityStreamTranslator,
)
from agentos.providers.cli_adapter import CliTurnRequest
from agentos.providers.cli_registry import CLI_PROVIDERS
from agentos.providers.codex_cli.adapter import CodexCliAdapter
from agentos.providers.codex_cli.command import build_codex_command

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-fake-png-payload"
JPG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF-fake-jpg-payload"
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 -fake-webp-payload"


def _write_fake_codex_script(path: Path) -> Path:
    script = path / "fake_codex_img.py"
    script.write_text(
        """from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time

argv = sys.argv[1:]
stdin_data = sys.stdin.read()
mode = os.environ.get("FAKE_CODEX_MODE", "success")
codex_home = Path(os.environ["CODEX_HOME"])

# Record captured argv and stdin for assertions
capture_file = os.environ.get("FAKE_CODEX_CAPTURE")
if capture_file:
    Path(capture_file).write_text(
        json.dumps({"argv": argv, "stdin": stdin_data, "cwd": os.getcwd()}),
        encoding="utf-8",
    )

if mode == "timeout":
    time.sleep(10.0)
    sys.exit(0)

thread_id = "019cfe2a-aaaa-7bbb-8ccc-ddddeeeeffff"
print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
print(
    json.dumps(
        {
            "type": "item.completed",
            "item": {"id": "msg_1", "type": "agent_message", "text": "Generated image."},
        }
    ),
    flush=True,
)

if mode == "success":
    out_dir = codex_home / "generated_images" / thread_id
    out_dir.mkdir(parents=True, exist_ok=True)
    older = out_dir / "img_old.png"
    older.write_bytes(b"\\x89PNG\\r\\n\\x1a\\nOLD_PNG")
    os.utime(older, (1000, 1000))
    newer = out_dir / "img_new.png"
    newer.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR-fake-png-payload")
    os.utime(newer, (2000, 2000))
elif mode == "no_image":
    pass

print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}), flush=True)
""",
        encoding="utf-8",
    )
    return script


def _write_fake_agy_script(path: Path) -> Path:
    script = path / "fake_agy_img.py"
    script.write_text(
        """from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time

argv = sys.argv[1:]
mode = os.environ.get("FAKE_AGY_MODE", "success")
brain_dir = Path(os.environ["FAKE_AGY_BRAIN"])

capture_file = os.environ.get("FAKE_AGY_CAPTURE")
if capture_file:
    cwd = Path(os.getcwd())
    agent_md = (cwd / ".agents" / "agents" / "orin.md").read_text(encoding="utf-8") if (cwd / ".agents" / "agents" / "orin.md").is_file() else ""
    settings_json = (cwd / ".agents" / "orin_settings.json").read_text(encoding="utf-8") if (cwd / ".agents" / "orin_settings.json").is_file() else ""
    Path(capture_file).write_text(
        json.dumps(
            {
                "argv": argv,
                "cwd": str(cwd),
                "has_git": (cwd / ".git").is_dir(),
                "agent_md": agent_md,
                "settings": json.loads(settings_json) if settings_json else {},
            }
        ),
        encoding="utf-8",
    )

conv_id = "70b71d47-1111-4222-8333-444455556666"
print(json.dumps({"event": "init", "conversation_id": conv_id, "init": {"model": "gemini-3.8-flash-medium"}}), flush=True)

if mode == "forbidden_tool":
    print(
        json.dumps(
            {
                "event": "step_update",
                "conversation_id": conv_id,
                "step_update": {
                    "step_index": 1,
                    "step_type": "tool",
                    "tool_name": "run_command",
                    "state": "ACTIVE",
                },
            }
        ),
        flush=True,
    )
    # Wait until killed by parent
    time.sleep(10.0)
    sys.exit(0)

if mode == "timeout":
    time.sleep(10.0)
    sys.exit(0)

print(
    json.dumps(
        {
            "event": "step_update",
            "conversation_id": conv_id,
            "step_update": {
                "step_index": 1,
                "step_type": "tool",
                "tool_name": "generate_image",
                "state": "ACTIVE",
            },
        }
    ),
    flush=True,
)

if mode == "success":
    conv_dir = brain_dir / conv_id
    conv_dir.mkdir(parents=True, exist_ok=True)
    # Drop a non-image metadata file and a JPEG image file
    (conv_dir / "task.md.metadata.json").write_text("{}", encoding="utf-8")
    img_file = conv_dir / "generated_cube.jpg"
    img_file.write_bytes(b"\\xff\\xd8\\xff\\xe0\\x00\\x10JFIF-fake-jpg-payload")

print(
    json.dumps(
        {
            "event": "step_update",
            "conversation_id": conv_id,
            "step_update": {
                "step_index": 1,
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
    return script


def test_codex_image_generation_success_and_flags(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    script = _write_fake_codex_script(tmp_path)
    capture_file = tmp_path / "codex_capture.json"

    env = {
        **os.environ,
        "CODEX_HOME": str(codex_home),
        "FAKE_CODEX_MODE": "success",
        "FAKE_CODEX_CAPTURE": str(capture_file),
    }

    client = CliImageGenerationClient(
        "openai-codex",
        binary=str(script),
        codex_home=codex_home,
        env=env,
    )
    def _popen_with_test_env(cmd: list[str], **kwargs: Any) -> Any:
        import subprocess

        merged_env = {**kwargs.get("env", {}), "FAKE_CODEX_MODE": "success", "FAKE_CODEX_CAPTURE": str(capture_file)}
        kwargs["env"] = merged_env
        return subprocess.Popen(cmd, **kwargs)

    client._popen_factory = _popen_with_test_env

    data, ext = client.generate_image(
        model=CODEX_IMAGE_MODEL_ID,
        prompt="a red cube on a white background",
        size="1536x1024",
    )

    assert data == PNG_BYTES
    assert ext == "png"

    captured = json.loads(capture_file.read_text(encoding="utf-8"))
    argv = captured["argv"]
    assert "exec" in argv
    assert "--json" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert "--skip-git-repo-check" in argv
    assert "--ephemeral" in argv
    assert "-m" in argv
    # Virtual model id 'codex-image' must never be passed as '-m'; real chat model is used
    model_passed = argv[argv.index("-m") + 1]
    assert model_passed == "gpt-6-luna"
    assert "features.shell_tool=false" in argv
    assert 'web_search="disabled"' in argv
    assert "features.view_image=false" in argv
    assert "features.multi_agent=false" in argv
    assert 'sandbox_mode="read-only"' in argv
    assert 'approval_policy="never"' in argv
    assert argv[-1] == "-"
    assert "1536x1024" in captured["stdin"]
    assert "a red cube on a white background" in captured["stdin"]
    # Temp directory must be deleted after completion
    assert not Path(captured["cwd"]).exists()


def test_codex_image_generation_no_image_raises_error(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    script = _write_fake_codex_script(tmp_path)

    client = CliImageGenerationClient(
        "openai-codex",
        binary=str(script),
        codex_home=codex_home,
    )
    def _popen_no_image(cmd: list[str], **kwargs: Any) -> Any:
        import subprocess

        kwargs["env"] = {**kwargs.get("env", {}), "FAKE_CODEX_MODE": "no_image"}
        return subprocess.Popen(cmd, **kwargs)

    client._popen_factory = _popen_no_image

    with pytest.raises(MediaGenerationError, match="Nenhuma imagem foi gerada pelo Codex CLI"):
        client.generate_image(model=CODEX_IMAGE_MODEL_ID, prompt="missing image")


def test_antigravity_image_generation_success_and_isolation(tmp_path: Path) -> None:
    brain_dir = tmp_path / "agy_brain"
    brain_dir.mkdir()
    script = _write_fake_agy_script(tmp_path)
    capture_file = tmp_path / "agy_capture.json"

    client = CliImageGenerationClient(
        "google-antigravity",
        binary=str(script),
        antigravity_brain_dir=brain_dir,
    )
    def _popen_agy(cmd: list[str], **kwargs: Any) -> Any:
        import subprocess

        kwargs["env"] = {
            **kwargs.get("env", {}),
            "FAKE_AGY_MODE": "success",
            "FAKE_AGY_BRAIN": str(brain_dir),
            "FAKE_AGY_CAPTURE": str(capture_file),
        }
        return subprocess.Popen(cmd, **kwargs)

    client._popen_factory = _popen_agy

    data, ext = client.generate_image(
        model=ANTIGRAVITY_IMAGE_MODEL_ID,
        prompt="a red cube on a white background",
        size="1024x1024",
    )

    assert data == JPG_BYTES
    assert ext == "jpg"

    captured = json.loads(capture_file.read_text(encoding="utf-8"))
    argv = captured["argv"]
    assert "--dangerously-skip-permissions" not in argv
    assert "--agent" in argv and argv[argv.index("--agent") + 1] == "orin"
    assert "--settings" in argv
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gemini-3.8-flash-medium"
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "stream-json"
    assert "-p" in argv
    prompt_arg = argv[argv.index("-p") + 1]
    assert "1024x1024" in prompt_arg
    assert "a red cube on a white background" in prompt_arg
    assert captured["has_git"] is True
    assert "tools: [generate_image]" in captured["agent_md"]
    assert "excludeDefaultComponents: true" in captured["agent_md"]
    assert "inheritMcp: false" in captured["agent_md"]
    settings = captured["settings"]
    assert settings["allowNonWorkspaceAccess"] is False
    assert settings["enableTerminalSandbox"] is True
    assert settings["disableSlashCommands"] is True
    assert settings["permissions"]["allow"] == []
    assert "command(*)" in settings["permissions"]["deny"]
    assert not Path(captured["cwd"]).exists()


def test_antigravity_forbidden_tool_kills_process_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain_dir = tmp_path / "agy_brain"
    brain_dir.mkdir()
    script = _write_fake_agy_script(tmp_path)

    killed_pids: list[int] = []
    from agentos.providers.claude_cli import provider as claude_provider_mod

    real_kill = claude_provider_mod.kill_process_tree

    def _spy_kill(proc: Any) -> None:
        if getattr(proc, "pid", None):
            killed_pids.append(proc.pid)
        real_kill(proc)

    monkeypatch.setattr("agentos.agentic.cli_media_client.kill_process_tree", _spy_kill)

    client = CliImageGenerationClient(
        "google-antigravity",
        binary=str(script),
        antigravity_brain_dir=brain_dir,
        timeout_seconds=15.0,
    )
    def _popen_forbidden(cmd: list[str], **kwargs: Any) -> Any:
        import subprocess

        kwargs["env"] = {
            **kwargs.get("env", {}),
            "FAKE_AGY_MODE": "forbidden_tool",
            "FAKE_AGY_BRAIN": str(brain_dir),
        }
        return subprocess.Popen(cmd, **kwargs)

    client._popen_factory = _popen_forbidden

    t0 = time.monotonic()
    with pytest.raises(MediaGenerationError, match="ferramenta não permitida"):
        client.generate_image(model=ANTIGRAVITY_IMAGE_MODEL_ID, prompt="try forbidden tool")
    elapsed = time.monotonic() - t0

    assert len(killed_pids) >= 1
    assert elapsed < 8.0


def test_cli_image_generation_timeout_kills_process_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    script = _write_fake_codex_script(tmp_path)

    killed_pids: list[int] = []
    from agentos.providers.claude_cli import provider as claude_provider_mod

    real_kill = claude_provider_mod.kill_process_tree

    def _spy_kill(proc: Any) -> None:
        if getattr(proc, "pid", None):
            killed_pids.append(proc.pid)
        real_kill(proc)

    monkeypatch.setattr("agentos.agentic.cli_media_client.kill_process_tree", _spy_kill)

    client = CliImageGenerationClient(
        "openai-codex",
        binary=str(script),
        codex_home=codex_home,
        timeout_seconds=1.0,
    )
    def _popen_timeout(cmd: list[str], **kwargs: Any) -> Any:
        import subprocess

        kwargs["env"] = {**kwargs.get("env", {}), "FAKE_CODEX_MODE": "timeout"}
        return subprocess.Popen(cmd, **kwargs)

    client._popen_factory = _popen_timeout

    with pytest.raises(MediaGenerationError, match="Tempo limite excedido"):
        client.generate_image(model=CODEX_IMAGE_MODEL_ID, prompt="slow image")

    assert len(killed_pids) >= 1


def test_extension_detection_by_magic_bytes_and_unsupported_modalities(tmp_path: Path) -> None:
    # Magic bytes override misleading file extensions
    assert detect_image_extension(PNG_BYTES, fallback_suffix=".jpg") == "png"
    assert detect_image_extension(JPG_BYTES, fallback_suffix=".png") == "jpg"
    assert detect_image_extension(WEBP_BYTES, fallback_suffix=".png") == "webp"

    with pytest.raises(MediaGenerationError):
        detect_image_extension(b"not-an-image", fallback_suffix=".bin")

    # Audio and video are unsupported on CLI image client
    client = CliImageGenerationClient("openai-codex", binary="codex")
    with pytest.raises(MediaGenerationError, match="não suportado"):
        client.generate_audio(model="codex-image", text="hello")
    with pytest.raises(MediaGenerationError, match="não suportado"):
        client.generate_video(model="codex-image", prompt="hello")

    # Brain root resolution via env and default
    env_brain = tmp_path / "env_brain"
    c_env = CliImageGenerationClient(
        "google-antigravity",
        env={"ORIN_ANTIGRAVITY_BRAIN_DIR": str(env_brain)},
    )
    assert c_env._resolve_antigravity_brain_root() == env_brain

    c_default = CliImageGenerationClient(
        "google-antigravity",
        env={"USERPROFILE": str(tmp_path)},
    )
    assert c_default._resolve_antigravity_brain_root() == (
        tmp_path / ".gemini" / "antigravity-cli" / "brain"
    )


def test_policy_and_worker_expose_virtual_cli_image_models_and_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert infer_output_modalities(CODEX_IMAGE_MODEL_ID) == ("image",)
    assert infer_output_modalities(ANTIGRAVITY_IMAGE_MODEL_ID) == ("image",)

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata.create_all(engine)
    cipher = ProviderSecretCipher("test-secret-key-for-cli-media-32b!!")
    monkeypatch.setenv("PROVIDER_SECRET_KEY", "test-secret-key-for-cli-media-32b!!")

    config_adapter = PostgresProviderConfigurationAdapter(engine, cipher=cipher)
    config_adapter.configure(
        {
            "provider": "openai-codex",
            "user_id": "u1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
        }
    )
    config_adapter.configure(
        {
            "provider": "google-antigravity",
            "user_id": "u1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
            "risk_acknowledged": True,
        }
    )

    monkeypatch.setitem(CLI_PROVIDERS, "openai-codex", CodexCliAdapter)
    monkeypatch.setitem(CLI_PROVIDERS, "google-antigravity", AntigravityCliAdapter)
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda env=None: None,
    )

    repo = PostgresProviderCatalogRepository(engine, cipher=cipher)
    catalog_service = ProviderModelCatalogService(repo)
    ctx = ProviderCatalogContext(user_id="u1", purpose="provider.catalog")

    catalog_service.refresh(ctx, "openai-codex")
    catalog_service.refresh(ctx, "google-antigravity")

    codex_models = {m.model_id: m for m in catalog_service.list(ctx, "openai-codex")}
    agy_models = {m.model_id: m for m in catalog_service.list(ctx, "google-antigravity")}

    assert CODEX_IMAGE_MODEL_ID in codex_models
    assert codex_models[CODEX_IMAGE_MODEL_ID].output_modalities == ("image",)
    assert codex_models[CODEX_IMAGE_MODEL_ID].capabilities == ()

    assert ANTIGRAVITY_IMAGE_MODEL_ID in agy_models
    assert agy_models[ANTIGRAVITY_IMAGE_MODEL_ID].output_modalities == ("image",)
    assert agy_models[ANTIGRAVITY_IMAGE_MODEL_ID].capabilities == ()

    # Favorite both virtual models and set antigravity-image as default image model
    catalog_service.favorite(ctx, "openai-codex", CODEX_IMAGE_MODEL_ID, True)
    catalog_service.favorite(ctx, "google-antigravity", ANTIGRAVITY_IMAGE_MODEL_ID, True)
    with engine.begin() as conn:
        conn.execute(
            insert(media_model_selections).values(
                user_id="u1",
                modality="image",
                provider="google-antigravity",
                model_id=ANTIGRAVITY_IMAGE_MODEL_ID,
                updated_at=datetime.now(UTC),
            )
        )

    from agentos.workers.chat import ChatWorker

    worker = object.__new__(ChatWorker)
    worker.store = type("_FakeStore", (), {"_engine": engine})()

    # Any active turn provider (e.g. anthropic) can resolve and build the CLI media client
    turn = {"user_id": "u1", "provider": "anthropic"}
    policy = worker._media_policy_for_turn(turn)
    assert policy.has_modality("image") is True
    assert policy.resolve_target("image", None) == SameProviderMediaModelPolicy.from_catalog_models(
        turn_provider="anthropic",
        favorite_models=[
            ("openai-codex", CODEX_IMAGE_MODEL_ID, ("image",)),
            ("google-antigravity", ANTIGRAVITY_IMAGE_MODEL_ID, ("image",)),
        ],
        defaults_by_modality={"image": ("google-antigravity", ANTIGRAVITY_IMAGE_MODEL_ID)},
        enabled_providers={"anthropic", "openai-codex", "google-antigravity"},
    ).resolve_target("image", None)
    assert policy.resolve_target("image", "openai-codex:codex-image").provider == "openai-codex"
    assert policy.resolve_target("image", "google-antigravity:antigravity-image").provider == "google-antigravity"

    build_client = worker._media_client_for_turn(turn)
    codex_client = build_client("openai-codex")
    agy_client = build_client("google-antigravity")
    assert isinstance(codex_client, CliImageGenerationClient)
    assert codex_client.provider == "openai-codex"
    assert isinstance(agy_client, CliImageGenerationClient)
    assert agy_client.provider == "google-antigravity"

    # Without risk_acknowledged_at, google-antigravity returns None
    with engine.begin() as conn:
        conn.execute(
            provider_configurations.update()
            .where(provider_configurations.c.provider == "google-antigravity")
            .values(risk_acknowledged_at=None)
        )
    assert build_client("google-antigravity") is None

    # End-to-end toolset.generate_image using CliImageGenerationClient
    ws = ConversationWorkspace(tmp_path / "ws", "conv_1")
    brain_dir = tmp_path / "e2e_brain"
    brain_dir.mkdir()
    script = _write_fake_agy_script(tmp_path)
    fake_agy_client = CliImageGenerationClient(
        "google-antigravity",
        binary=str(script),
        antigravity_brain_dir=brain_dir,
    )
    fake_agy_client._popen_factory = lambda cmd, **kw: __import__("subprocess").Popen(
        cmd,
        **{**kw, "env": {**kw.get("env", {}), "FAKE_AGY_MODE": "success", "FAKE_AGY_BRAIN": str(brain_dir)}},
    )

    toolset = AgentToolset(
        ws,
        media_policy=policy,
        media_client=lambda prov=None: fake_agy_client if prov == "google-antigravity" else codex_client,
    )
    outcome = toolset.generate_image(prompt="a red cube", output_path="out/cube")
    assert outcome.payload["path"] == "out/cube.jpg"
    assert (ws.root / "out" / "cube.jpg").read_bytes() == JPG_BYTES


def test_codex_turn_argv_disables_native_image_generation_and_guards_virtual_model(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "sys.md"
    prompt_file.write_text("System instructions", encoding="utf-8")

    argv = build_codex_command(
        CliTurnRequest(
            binary="codex",
            system_prompt_file=prompt_file,
            model=CODEX_IMAGE_MODEL_ID,
        )
    )
    assert "features.image_generation=false" in argv
    # Virtual model id 'codex-image' must fall back to default chat model
    m_idx = argv.index("-m")
    assert argv[m_idx + 1] != CODEX_IMAGE_MODEL_ID

    agy_argv = build_antigravity_command(
        CliTurnRequest(
            binary="agy",
            system_prompt_file=prompt_file,
            model=ANTIGRAVITY_IMAGE_MODEL_ID,
        )
    )
    agy_m_idx = agy_argv.index("--model")
    assert agy_argv[agy_m_idx + 1] != ANTIGRAVITY_IMAGE_MODEL_ID


def test_antigravity_stream_recoverable_generate_image() -> None:
    assert "generate_image" in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS
    assert "generate_image" in RECOVERABLE_ANTIGRAVITY_BUILTIN_TOOLS
    alt_map = dict(ANTIGRAVITY_BUILTIN_TOOL_ALTERNATIVES)
    assert alt_map.get("generate_image") == ("generate_image",)
    assert AntigravityCliAdapter.capabilities.recoverable_builtin_tools == RECOVERABLE_ANTIGRAVITY_BUILTIN_TOOLS
    assert dict(AntigravityCliAdapter.capabilities.builtin_tool_alternatives).get("generate_image") == ("generate_image",)

    translator = AntigravityStreamTranslator(model="gemini-4-argon-high")
    translator.feed_line(
        json.dumps({"event": "init", "conversation_id": "c1", "init": {"model": "gemini-4-argon-high"}})
    )
    events = translator.feed_line(
        json.dumps(
            {
                "event": "step_update",
                "conversation_id": "c1",
                "step_update": {
                    "step_index": 1,
                    "step_type": "tool",
                    "tool_name": "generate_image",
                    "state": "ACTIVE",
                },
            }
        )
    )
    assert len(events) == 1
    assert events[0].tool_name == "generate_image"

    reminder = builtin_tool_reminder("generate_image", ["GenerateImage"], prefix="mcp_orin_")
    assert "`generate_image` does not exist here" in reminder
    assert "`mcp_orin_GenerateImage`" in reminder
