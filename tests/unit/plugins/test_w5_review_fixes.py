"""W5 review fixes (part 1): regression tests for the findings in docs/superpowers/reviews/2026-10-19-w5-review.md."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, update

from agentos.persistence.postgres.schema import metadata, plugin_contributions
from agentos.plugins.hook_engine import HookEngine
from agentos.plugins.hook_executor import HookOutcome
from agentos.plugins.hook_specs import plugin_hook_specs
from agentos.plugins.runtime import active_plugins
from agentos.plugins.service import PluginService, PluginServiceError
from tests.unit.plugins.fakes import FakeMcpService, FakeSkillLibrary


class _Executor:
    """Records the hooks the engine runs; never starts a process."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, *, command, install_path, payload, timeout_seconds, hook_id=""):
        self.calls.append(hook_id)
        return HookOutcome(hook_id, "ok", "", "", 0)


def _service(tmp_path: Path, *, hook_engine=None) -> PluginService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return PluginService(
        engine, plugin_root=tmp_path / "plugins", skill_library=FakeSkillLibrary(), mcp_service=FakeMcpService(),
        hook_engine=hook_engine,
    )


def _write(src: Path, hooks: dict, version: str = "1.0.0") -> Path:
    (src / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (src / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "kit", "version": version}), encoding="utf-8")
    (src / "hooks").mkdir(exist_ok=True)
    (src / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    for name in ("a.py", "b.py", "c.py"):
        (src / "hooks" / name).write_text("print('x')", encoding="utf-8")
    return src


def _hook(script: str) -> list:
    return [{"hooks": [{"type": "command", "command": f"python ${{CLAUDE_PLUGIN_ROOT}}/hooks/{script}"}]}]


def _spec_scripts(service: PluginService) -> list[str]:
    plugins, _ = active_plugins(service, "u1", {})
    specs, _ = plugin_hook_specs(plugins, user_id="u1")
    return sorted(spec.command.rsplit("/", 1)[-1] for spec in specs)


def _approved_with_consent(tmp_path: Path, *, hook_engine=None) -> tuple[PluginService, Path]:
    service = _service(tmp_path, hook_engine=hook_engine)
    src = _write(tmp_path / "src", {"SessionStart": _hook("a.py")})
    service.inspect(user_id="u1", reference=str(src))
    service.approve(user_id="u1", plugin_id="kit")
    service.set_hooks_enabled(user_id="u1", plugin_id="kit", enabled=True)
    return service, src


# W5-R3: a plugin update is approved before it runs, and hook consent belongs to the exact hook that was shown.


def test_w5_r3_a_pending_update_cannot_be_switched_on(tmp_path: Path) -> None:
    service, src = _approved_with_consent(tmp_path)
    _write(src, {"SessionStart": _hook("b.py"), "PreToolUse": _hook("c.py")})
    assert service.inspect(user_id="u1", reference=str(src))["state"] == "pending_approval"
    with pytest.raises(PluginServiceError):
        service.set_enabled(user_id="u1", plugin_id="kit", enabled=True)
    assert service.get("u1", "kit")["state"] == "pending_approval"
    assert _spec_scripts(service) == []


def test_w5_r3_a_pending_update_gets_no_hook_consent_and_its_old_hooks_stop(tmp_path: Path) -> None:
    hook_engine = HookEngine(executor=_Executor())
    service, src = _approved_with_consent(tmp_path, hook_engine=hook_engine)
    assert [item.hook_id for item in hook_engine.dispatch(user_id="u1", event="SessionStart", payload={})] == ["kit:SessionStart:0"]
    _write(src, {"SessionStart": _hook("b.py")})
    service.inspect(user_id="u1", reference=str(src))
    with pytest.raises(PluginServiceError):
        service.set_hooks_enabled(user_id="u1", plugin_id="kit", enabled=True)
    assert hook_engine.dispatch(user_id="u1", event="SessionStart", payload={}) == ()


def test_w5_r3_an_approved_update_runs_no_hook_until_the_person_allows_it(tmp_path: Path) -> None:
    hook_engine = HookEngine(executor=_Executor())
    service, src = _approved_with_consent(tmp_path, hook_engine=hook_engine)
    _write(src, {"SessionStart": _hook("b.py"), "PreToolUse": _hook("c.py")})
    service.inspect(user_id="u1", reference=str(src))
    service.approve(user_id="u1", plugin_id="kit")
    assert _spec_scripts(service) == []
    assert hook_engine.dispatch(user_id="u1", event="SessionStart", payload={}) == ()
    service.set_hooks_enabled(user_id="u1", plugin_id="kit", enabled=True)
    assert _spec_scripts(service) == ["b.py", "c.py"]


def test_w5_r3_an_edited_installed_hooks_file_waits_for_consent(tmp_path: Path) -> None:
    service, _src = _approved_with_consent(tmp_path)
    record = service.get("u1", "kit")
    installed = Path(record["install_path"]) / "hooks" / "hooks.json"
    installed.write_text(json.dumps({"hooks": {"SessionStart": _hook("b.py"), "Stop": _hook("c.py")}}), encoding="utf-8")
    assert _spec_scripts(service) == []


def test_w5_r3_an_unchanged_hook_keeps_consent_across_a_disable_and_enable(tmp_path: Path) -> None:
    service, _src = _approved_with_consent(tmp_path)
    service.set_enabled(user_id="u1", plugin_id="kit", enabled=False)
    service.set_enabled(user_id="u1", plugin_id="kit", enabled=True)
    assert _spec_scripts(service) == ["a.py"]


def test_w5_r3_a_hook_edited_while_disabled_waits_for_consent_on_enable(tmp_path: Path) -> None:
    hook_engine = HookEngine(executor=_Executor())
    service, _src = _approved_with_consent(tmp_path, hook_engine=hook_engine)
    service.set_enabled(user_id="u1", plugin_id="kit", enabled=False)
    installed = Path(service.get("u1", "kit")["install_path"]) / "hooks" / "hooks.json"
    installed.write_text(json.dumps({"hooks": {"SessionStart": _hook("b.py")}}), encoding="utf-8")
    service.set_enabled(user_id="u1", plugin_id="kit", enabled=True)
    assert _spec_scripts(service) == []
    assert hook_engine.dispatch(user_id="u1", event="SessionStart", payload={}) == ()


def test_w5_r3_a_row_written_with_a_plain_hook_id_does_not_consent(tmp_path: Path) -> None:
    hook_engine = HookEngine(executor=_Executor())
    service, _src = _approved_with_consent(tmp_path, hook_engine=hook_engine)
    with service.engine.begin() as connection:  # a row from before the digest was part of the reference
        connection.execute(update(plugin_contributions).where(plugin_contributions.c.kind == "hook").values(reference="kit:SessionStart:0"))
    assert _spec_scripts(service) == []
    service.set_enabled(user_id="u1", plugin_id="kit", enabled=False)
    service.set_enabled(user_id="u1", plugin_id="kit", enabled=True)
    assert _spec_scripts(service) == []
    assert hook_engine.dispatch(user_id="u1", event="SessionStart", payload={}) == ()


def test_w5_r3_consent_is_recorded_per_hook_reference_with_a_digest(tmp_path: Path) -> None:
    service, _src = _approved_with_consent(tmp_path)
    references = [row["reference"] for row in service._contributions("u1", "kit") if row["kind"] == "hook"]
    assert len(references) == 1 and references[0].startswith("kit:SessionStart:0#")
    assert len(references[0].split("#", 1)[1]) == 16
