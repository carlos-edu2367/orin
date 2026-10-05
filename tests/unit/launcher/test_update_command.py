from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path

import pytest

from agentos.installation import versions
from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher import cli
from agentos.launcher.ui import Console

from tests.unit.installation.test_updater import BASE, Server, _archive, _installed

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake runtimes are POSIX shell scripts")


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def _profile(root: Path, version: str = "0.4.0") -> RuntimeProfile:
    return RuntimeProfile("installed", root / version / "resources" / "runtime", version, None)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    monkeypatch.setenv("ORIN_RELEASE_BASE_URL", BASE)


def _run(tmp_path: Path, root: Path, server: Server, **options) -> tuple[int, str]:
    stream = io.StringIO()
    code = cli.command_update(_paths(tmp_path), _profile(root), Console(stream, colour=False), argparse.Namespace(**options), opener=server)
    return code, stream.getvalue()


def test_update_prints_numbered_steps_and_a_summary(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    code, out = _run(tmp_path, root, Server("0.5.0", _archive("0.5.0")))

    assert code == 0
    for number in range(1, 7):
        assert f"[{number}/6]" in out
    assert "0.4.0 → 0.5.0" in out and "Orin atualizado" in out and "orin update --rollback" in out
    assert (root / "current").resolve() == (root / "0.5.0").resolve()


def test_update_reports_failure_with_what_to_do_and_leaves_the_install_alone(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    code, out = _run(tmp_path, root, Server("0.5.0", _archive("0.5.0"), sha="0" * 64))

    assert code == 1
    assert "não foi concluída" in out and "descartado" in out and "continua a mesma" in out
    assert (root / "current").resolve() == (root / "0.4.0").resolve()


def test_check_says_when_a_release_is_available_and_changes_nothing(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    code, out = _run(tmp_path, root, Server("0.5.0", _archive("0.5.0"), notes="- mais rápido"), check=True)

    assert code == 0 and "Nova versão disponível: 0.5.0" in out and "- mais rápido" in out and "orin update" in out
    assert not (root / "0.5.0").exists()


def test_json_mode_emits_one_object_per_line_ending_in_a_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _installed(tmp_path)
    code = cli.command_update(_paths(tmp_path), _profile(root), Console(io.StringIO(), colour=False, quiet=True), argparse.Namespace(json=True), opener=Server("0.5.0", _archive("0.5.0")))

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == 0
    assert lines[-1]["type"] == "result" and lines[-1]["version"] == "0.5.0"
    assert {"check", "download", "activate"} <= {line["step"] for line in lines if line["type"] == "event"}


def test_rollback_flag_goes_back_to_the_previous_version(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    _run(tmp_path, root, Server("0.5.0", _archive("0.5.0")))
    stream = io.StringIO()
    code = cli.command_update(_paths(tmp_path), _profile(root, "0.5.0"), Console(stream, colour=False), argparse.Namespace(rollback=True))

    assert code == 0 and "Versão restaurada" in stream.getvalue()
    assert (root / "current").resolve() == (root / "0.4.0").resolve()


def test_update_refuses_a_source_checkout_and_an_unofficial_install(tmp_path: Path) -> None:
    stream = io.StringIO()
    dev = RuntimeProfile("development", tmp_path, "0.4.0", tmp_path)
    assert cli.command_update(_paths(tmp_path), dev, Console(stream, colour=False)) == 2
    assert "git pull" in stream.getvalue()

    stream = io.StringIO()
    loose = RuntimeProfile("installed", tmp_path / "runtime", "0.4.0", None)
    assert cli.command_update(_paths(tmp_path), loose, Console(stream, colour=False)) == 2
    assert "instalador oficial" in stream.getvalue()


def test_update_subcommand_accepts_its_options_without_clobbering_the_version_flag() -> None:
    arguments = cli.build_parser().parse_args(["update", "--to", "0.5.0", "--check", "--json"])
    assert (arguments.target, arguments.check, arguments.json, arguments.version) == ("0.5.0", True, True, False)
