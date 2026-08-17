import pytest

from agentos.plugins.hook_executor import HookRejected, resolve_argv

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
