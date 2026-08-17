import json
import sys

import pytest

from agentos.plugins.hook_executor import HookExecutor, HookRejected, resolve_argv

DENIED = [
    'python3 -c "import os"',
    'cat "${CLAUDE_PLUGIN_ROOT}/x.py" | grep secret',
    'python3 "${CLAUDE_PLUGIN_ROOT}/x.py" && rm -rf /',
    'echo hi > /tmp/out',
    'python3 $(whoami).py',
    'curl https://example.com',
    'python3 /etc/passwd',
    'python3 "${CLAUDE_PLUGIN_ROOT}/../outside.py"',
]


@pytest.mark.parametrize("command", DENIED)
def test_the_executor_refuses_anything_it_cannot_confine(tmp_path, command):
    (tmp_path / "x.py").write_text("print(1)", encoding="utf-8")

    with pytest.raises(HookRejected):
        resolve_argv(command, install_path=tmp_path)


def test_an_interpreter_pointed_inside_the_package_is_allowed(tmp_path):
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "load_vault_context.py").write_text("print(1)", encoding="utf-8")

    argv = resolve_argv('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/load_vault_context.py"', install_path=tmp_path)

    assert argv[0] == "python3"
    assert argv[1] == str((tmp_path / "hooks" / "load_vault_context.py").resolve())


def test_a_script_inside_the_package_is_allowed_without_an_interpreter(tmp_path):
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "validate.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    argv = resolve_argv('"${CLAUDE_PLUGIN_ROOT}/hooks/validate.sh"', install_path=tmp_path)

    assert argv == [str((tmp_path / "hooks" / "validate.sh").resolve())]


def test_a_symlink_escaping_the_package_is_refused(tmp_path):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("print(1)", encoding="utf-8")
    package = tmp_path / "pkg"
    (package / "hooks").mkdir(parents=True)
    link = package / "hooks" / "link.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this host")

    with pytest.raises(HookRejected):
        resolve_argv('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/link.py"', install_path=package)


def _script(tmp_path, source):
    (tmp_path / "hooks").mkdir(exist_ok=True)
    target = tmp_path / "hooks" / "hook.py"
    target.write_text(source, encoding="utf-8")
    return target


# `sys.executable` is quoted on purpose: it is an absolute path, and on Windows
# it contains backslashes that `shlex.split(posix=True)` would otherwise eat as
# escapes. Inside double quotes they survive, exactly as in a POSIX shell.


def test_the_event_payload_arrives_on_stdin_and_stdout_comes_back(tmp_path):
    _script(tmp_path, "import json,sys\npayload=json.load(sys.stdin)\nprint('saw', payload['event'])\n")

    outcome = HookExecutor(interpreter=sys.executable).run(
        command=f'"{sys.executable}" "${{CLAUDE_PLUGIN_ROOT}}/hooks/hook.py"',
        install_path=tmp_path, payload={"event": "SessionStart"}, timeout_seconds=10,
    )

    assert outcome.status == "ok"
    assert outcome.stdout.strip() == "saw SessionStart"
    assert outcome.exit_code == 0


def test_a_non_zero_exit_is_reported_and_blocks_nothing(tmp_path):
    _script(tmp_path, "import sys\nsys.stderr.write('denied')\nsys.exit(2)\n")

    outcome = HookExecutor(interpreter=sys.executable).run(
        command=f'"{sys.executable}" "${{CLAUDE_PLUGIN_ROOT}}/hooks/hook.py"',
        install_path=tmp_path, payload={}, timeout_seconds=10,
    )

    assert outcome.exit_code == 2
    assert outcome.status == "failed"
    assert "denied" in outcome.stderr
    assert not hasattr(outcome, "deny")
    assert not hasattr(outcome, "blocked")


def test_a_hook_that_overruns_is_killed(tmp_path):
    _script(tmp_path, "import time\ntime.sleep(30)\n")

    outcome = HookExecutor(interpreter=sys.executable).run(
        command=f'"{sys.executable}" "${{CLAUDE_PLUGIN_ROOT}}/hooks/hook.py"',
        install_path=tmp_path, payload={}, timeout_seconds=1,
    )

    assert outcome.status == "timeout"


def test_the_orin_environment_is_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_SECRET_TOKEN", "do-not-leak")
    _script(tmp_path, "import json,os\nprint(json.dumps(sorted(os.environ)))\n")

    outcome = HookExecutor(interpreter=sys.executable).run(
        command=f'"{sys.executable}" "${{CLAUDE_PLUGIN_ROOT}}/hooks/hook.py"',
        install_path=tmp_path, payload={}, timeout_seconds=10,
    )

    names = json.loads(outcome.stdout)
    assert "ORIN_SECRET_TOKEN" not in names
    assert "CLAUDE_PLUGIN_ROOT" in names


def test_a_rejected_command_never_launches(tmp_path):
    outcome = HookExecutor().run(
        command='python3 -c "print(1)"', install_path=tmp_path, payload={}, timeout_seconds=10
    )

    assert outcome.status == "rejected"
    assert outcome.exit_code is None
