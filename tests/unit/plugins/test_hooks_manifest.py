import json

from agentos.plugins.hooks_manifest import parse_hooks

REAL_HOOKS = {
    "hooks": {
        "SessionStart": [{"matcher": "", "hooks": [
            {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/load_vault_context.py"'}
        ]}],
        "PostToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|create_file", "hooks": [
            {"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/validate-ai-first.sh"', "timeout": 10}
        ]}],
        "PostCompact": [{"matcher": "", "hooks": [
            {"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/obsidian-bg-agent.sh"', "timeout": 10, "async": True}
        ]}],
    }
}


def _write(tmp_path, payload):
    (tmp_path / "hooks").mkdir(exist_ok=True)
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path / "hooks"


def test_the_reference_hooks_file_yields_three_hooks(tmp_path):
    hooks, warnings = parse_hooks(_write(tmp_path, REAL_HOOKS), plugin_id="obsidian")

    assert [item.event for item in hooks] == ["PostCompact", "PostToolUse", "SessionStart"]
    assert hooks[1].matcher == "Write|Edit|MultiEdit|NotebookEdit|create_file"
    assert hooks[2].hook_id == "obsidian:SessionStart:0"
    assert any("async" in warning for warning in warnings)


def test_an_unsupported_event_is_warned_not_dropped_silently(tmp_path):
    hooks, warnings = parse_hooks(
        _write(tmp_path, {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "x"}]}]}}),
        plugin_id="demo",
    )

    assert hooks == ()
    assert any("PreToolUse" in warning for warning in warnings)


def test_an_uncompilable_matcher_skips_the_hook(tmp_path):
    hooks, warnings = parse_hooks(
        _write(tmp_path, {"hooks": {"PostToolUse": [{"matcher": "[unclosed", "hooks": [{"type": "command", "command": "x"}]}]}}),
        plugin_id="demo",
    )

    assert hooks == ()
    assert any("matcher" in warning for warning in warnings)


def test_timeout_is_clamped(tmp_path):
    hooks, _ = parse_hooks(
        _write(tmp_path, {"hooks": {"SessionStart": [{"matcher": "", "hooks": [
            {"type": "command", "command": "x", "timeout": 900}
        ]}]}}),
        plugin_id="demo",
    )

    assert hooks[0].timeout_seconds == 30


def test_a_missing_or_malformed_file_contributes_nothing(tmp_path):
    assert parse_hooks(tmp_path / "hooks", plugin_id="demo") == ((), ())
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text("{not json", encoding="utf-8")
    hooks, warnings = parse_hooks(tmp_path / "hooks", plugin_id="demo")
    assert hooks == () and warnings != ()
