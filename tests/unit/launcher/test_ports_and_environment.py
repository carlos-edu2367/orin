from __future__ import annotations

import socket
from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher.environment import (
    ConfigurationError,
    load_environment,
    parse_env_file,
    redact,
    write_default_configuration,
)
from agentos.launcher.ports import PortUnavailable, port_is_free, select_port


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def _profile(tmp_path: Path) -> RuntimeProfile:
    root = tmp_path / "install"
    (root / "web").mkdir(parents=True)
    (root / "web" / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    return RuntimeProfile("installed", root, "9.9.9", None)


# -- ports --------------------------------------------------------------


def test_a_free_default_port_is_used_as_is() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]

    choice = select_port(free)

    assert choice.port == free
    assert choice.moved is False
    assert choice.message is None


def test_a_taken_default_port_moves_forward_instead_of_failing() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]

        choice = select_port(port)

    assert choice.port != port
    assert choice.moved is True
    assert "in use" in (choice.message or "")


def test_an_explicitly_requested_port_is_never_silently_moved() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]

        with pytest.raises(PortUnavailable) as error:
            select_port(port, explicit=True)

    assert "--port" in str(error.value)
    assert str(port) in str(error.value)


def test_a_listening_socket_makes_its_port_unavailable() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()

        assert port_is_free(taken.getsockname()[1]) is False


# -- configuration ------------------------------------------------------


def test_env_files_are_parsed_without_quotes_or_comments(tmp_path: Path) -> None:
    path = tmp_path / "orin.env"
    path.write_text('# comment\nA=1\nB="two"\nC=\nnot-a-pair\n', encoding="utf-8")

    assert parse_env_file(path) == {"A": "1", "B": "two", "C": ""}


def test_a_first_run_generates_a_usable_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", raising=False)
    paths, profile = _paths(tmp_path), _profile(tmp_path)

    environment = load_environment(paths, profile)

    assert environment.created_configuration == paths.config / "orin.env"
    assert environment.values["AGENTOS_PROVIDER_ENCRYPTION_KEY"]
    assert environment.database_url.startswith("sqlite+pysqlite://")


def test_the_web_bundle_is_handed_to_children_as_an_absolute_path(tmp_path: Path) -> None:
    paths, profile = _paths(tmp_path), _profile(tmp_path)

    environment = load_environment(paths, profile)

    assert Path(environment.values["WEB_DIST_DIR"]).is_absolute()
    assert Path(environment.values["WEB_DIST_DIR"]) == (profile.root / "web").resolve()


def test_children_are_told_the_layout_rather_than_left_to_guess(tmp_path: Path) -> None:
    paths, profile = _paths(tmp_path), _profile(tmp_path)

    values = load_environment(paths, profile).for_port(8123)

    assert values["ORIN_DATA_DIR"] == str(paths.data)
    assert values["ORIN_BACKEND_PORT"] == "8123"
    assert values["ORIN_BACKEND_HOST"] == "127.0.0.1"


def test_a_missing_web_build_is_reported_before_anything_starts(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    profile = RuntimeProfile("installed", tmp_path / "empty", "9.9.9", None)

    with pytest.raises(ConfigurationError) as error:
        load_environment(paths, profile)

    assert "web interface is missing" in str(error.value)


def test_loopback_trust_outside_a_local_environment_is_refused(tmp_path: Path) -> None:
    paths, profile = _paths(tmp_path), _profile(tmp_path)
    write_default_configuration(paths.config / "orin.env")
    (paths.config / "orin.env").write_text(
        "DATABASE_URL=postgresql+psycopg://a@127.0.0.1:5433/a\nREDIS_URL=redis://127.0.0.1:6380/0\n"
        "AGENTOS_ENV=production\nLOCALHOST_TRUST_ENABLED=true\nAGENTOS_PROVIDER_ENCRYPTION_KEY=x\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as error:
        load_environment(paths, profile)

    assert "LOCALHOST_TRUST_ENABLED" in str(error.value)


def test_a_missing_encryption_key_stops_startup_with_an_actionable_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", raising=False)
    paths, profile = _paths(tmp_path), _profile(tmp_path)
    (paths.config / "orin.env").write_text("AGENTOS_ENV=local\nAGENTOS_PROVIDER_ENCRYPTION_KEY=\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as error:
        load_environment(paths, profile)

    assert "Fernet.generate_key" in str(error.value)


def test_secrets_are_never_rendered_in_full(tmp_path: Path) -> None:
    paths, profile = _paths(tmp_path), _profile(tmp_path)
    environment = load_environment(paths, profile)
    key = environment.values["AGENTOS_PROVIDER_ENCRYPTION_KEY"]

    assert redact("AGENTOS_PROVIDER_ENCRYPTION_KEY", key) == "***"
    assert redact("OPENAI_API_KEY", "sk-secret") == "***"
    assert key not in environment.describe()
    assert "postgres-password" not in redact("DATABASE_URL", "postgresql://user:postgres-password@host/db")
