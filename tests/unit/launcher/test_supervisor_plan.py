from __future__ import annotations

import io
from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher import supervisor as supervisor_module
from agentos.launcher.environment import RuntimeEnvironment
from agentos.launcher.probes import ProbeResult
from agentos.launcher.supervisor import StartupFailed, Supervisor
from agentos.launcher.ui import Console
from agentos.omniroute import OmniRouteRuntimeSettingsStore


class FakeChild:
    """Stands in for a spawned service without spawning anything."""

    def __init__(self, name: str, log: Path) -> None:
        self.name = name
        self.log_path = log
        self.process = object()
        self.pid = 4242
        self.stopped = False
        self.exit_code: int | None = None

    def exited(self) -> int | None:
        return self.exit_code

    def stop(self, **_: object) -> None:
        self.stopped = True

    def tail(self, lines: int = 12) -> str:
        return ""


def _build(tmp_path: Path) -> tuple[Supervisor, io.StringIO]:
    paths = OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()
    profile = RuntimeProfile("installed", tmp_path / "install", "9.9.9", None)
    stream = io.StringIO()
    supervisor = Supervisor(paths=paths, profile=profile, console=Console(stream, colour=False))
    supervisor.environment = RuntimeEnvironment({"DATABASE_URL": "postgresql://x", "REDIS_URL": "redis://x"}, (), None)
    return supervisor, stream


# -- OmniRouter ---------------------------------------------------------


def test_omnirouter_is_absent_from_startup_when_the_preference_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, stream = _build(tmp_path)
    OmniRouteRuntimeSettingsStore(supervisor.paths.data / "omniroute-runtime.json").set_auto_start("user-a", False)
    probed: list[str] = []
    monkeypatch.setattr(supervisor_module, "http_probe", lambda url, **_: probed.append(url) or ProbeResult(True, ""))

    supervisor._step_omnirouter()

    assert probed == []
    assert "OmniRouter" not in stream.getvalue()
    # Disabled is a normal choice, so it is not reported as a problem either.
    assert "!" not in stream.getvalue()
    assert supervisor.omniroute_expected is False


def test_omnirouter_is_reported_when_the_existing_preference_is_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, stream = _build(tmp_path)
    OmniRouteRuntimeSettingsStore(supervisor.paths.data / "omniroute-runtime.json").set_auto_start("user-a", True)
    probed: list[str] = []
    monkeypatch.setattr(supervisor_module, "http_probe", lambda url, **_: probed.append(url) or ProbeResult(True, "ready"))

    supervisor._step_omnirouter()

    assert probed and probed[0].endswith("/models")
    assert "✓ OmniRouter" in stream.getvalue()
    assert supervisor.omniroute_expected is True


def test_a_slow_gateway_never_delays_the_rest_of_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A cold OmniRoute can take minutes. Orin does not depend on it, so the
    # step reports that it is coming and startup continues immediately.
    supervisor, stream = _build(tmp_path)
    OmniRouteRuntimeSettingsStore(supervisor.paths.data / "omniroute-runtime.json").set_auto_start("user-a", True)
    monkeypatch.setattr(supervisor_module, "http_probe", lambda *_, **__: ProbeResult(False, "refused"))
    monkeypatch.setattr(supervisor_module, "OMNIROUTE_GRACE", 0.05)

    supervisor._step_omnirouter()  # must not raise

    assert "· OmniRouter starting" in stream.getvalue()
    assert "✓ OmniRouter" not in stream.getvalue()
    assert supervisor.omniroute_ready is False


def test_a_gateway_that_arrives_late_is_reported_when_it_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, stream = _build(tmp_path)
    supervisor.omniroute_expected = True
    supervisor._omniroute_deadline = supervisor_module.monotonic() + 60
    answers = iter([ProbeResult(False, "refused"), ProbeResult(True, "ready")])
    monkeypatch.setattr(supervisor_module, "http_probe", lambda *_, **__: next(answers))
    monkeypatch.setattr(supervisor_module, "OMNIROUTE_WATCH_INTERVAL", 0.0)

    supervisor._watch_omniroute()
    assert "OmniRouter" not in stream.getvalue()

    supervisor._watch_omniroute()
    assert "✓ OmniRouter" in stream.getvalue()
    assert supervisor.omniroute_ready is True


def test_watching_for_a_gateway_gives_up_after_its_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, stream = _build(tmp_path)
    supervisor.omniroute_expected = True
    supervisor._omniroute_deadline = supervisor_module.monotonic() - 1
    monkeypatch.setattr(supervisor_module, "http_probe", lambda *_, **__: ProbeResult(True, "ready"))

    supervisor._watch_omniroute()

    assert supervisor.omniroute_expected is False
    assert "OmniRouter" not in stream.getvalue()


def test_the_launcher_never_writes_the_omnirouter_preference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, _ = _build(tmp_path)
    preference = supervisor.paths.data / "omniroute-runtime.json"
    OmniRouteRuntimeSettingsStore(preference).set_auto_start("user-a", True)
    before = preference.read_bytes()
    monkeypatch.setattr(supervisor_module, "http_probe", lambda *_, **__: ProbeResult(True, "ready"))

    supervisor._step_omnirouter()

    assert preference.read_bytes() == before


def test_an_unreadable_preference_is_treated_as_disabled(tmp_path: Path) -> None:
    supervisor, stream = _build(tmp_path)
    (supervisor.paths.data / "omniroute-runtime.json").write_text("{ broken", encoding="utf-8")

    supervisor._step_omnirouter()

    assert supervisor.omniroute_expected is False
    assert "OmniRouter" not in stream.getvalue()


# -- partial failure ----------------------------------------------------


def test_a_failed_step_stops_everything_already_started(tmp_path: Path) -> None:
    supervisor, stream = _build(tmp_path)
    backend = FakeChild("backend", tmp_path / "backend.log")
    publisher = FakeChild("publisher", tmp_path / "publisher.log")
    supervisor.children.extend([backend, publisher])  # type: ignore[arg-type]

    supervisor._rollback()

    assert backend.stopped and publisher.stopped
    assert supervisor.children == []
    assert "Stopping Orin" in stream.getvalue()


def test_rollback_stops_children_newest_first(tmp_path: Path) -> None:
    supervisor, _ = _build(tmp_path)
    order: list[str] = []
    for name in ("backend", "publisher", "worker"):
        child = FakeChild(name, tmp_path / f"{name}.log")
        child.stop = lambda _child=child, **_: order.append(_child.name)  # type: ignore[method-assign]
        supervisor.children.append(child)  # type: ignore[arg-type]

    supervisor._rollback()

    assert order == ["worker", "publisher", "backend"]


def test_the_workers_are_reported_as_one_thing_when_stopping(tmp_path: Path) -> None:
    supervisor, stream = _build(tmp_path)
    for name in ("backend", "publisher", "worker"):
        supervisor.children.append(FakeChild(name, tmp_path / f"{name}.log"))  # type: ignore[arg-type]

    supervisor._rollback()

    assert stream.getvalue().count("Workers stopped") == 1
    assert stream.getvalue().count("Backend stopped") == 1


def test_an_external_gateway_is_not_claimed_as_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Orin never stops a gateway it did not start, so it must not report one.
    supervisor, stream = _build(tmp_path)
    supervisor.omniroute_ready = True
    monkeypatch.setattr(supervisor_module, "http_probe", lambda *_, **__: ProbeResult(True, "still answering"))

    supervisor.shutdown()

    assert "OmniRouter stopped" not in stream.getvalue()


def test_a_gateway_that_went_down_with_the_backend_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, stream = _build(tmp_path)
    supervisor.omniroute_ready = True
    monkeypatch.setattr(supervisor_module, "http_probe", lambda *_, **__: ProbeResult(False, "refused"))

    supervisor.shutdown()

    assert "✓ OmniRouter stopped" in stream.getvalue()


def test_a_child_that_died_short_circuits_a_long_readiness_wait(tmp_path: Path) -> None:
    supervisor, _ = _build(tmp_path)
    child = FakeChild("backend", tmp_path / "backend.log")
    child.exit_code = 3

    assert supervisor._child_died(child) == "backend exited with code 3 before becoming ready"  # type: ignore[arg-type]


def test_a_failure_message_points_at_the_log(tmp_path: Path) -> None:
    supervisor, _ = _build(tmp_path)
    child = FakeChild("backend", tmp_path / "backend.log")

    message = supervisor._child_failure("Backend", child, ProbeResult(False, "timed out after 60s"))  # type: ignore[arg-type]

    assert "Backend did not become ready." in message
    assert str(tmp_path / "backend.log") in message


def test_startup_failure_is_raised_as_a_readable_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, _ = _build(tmp_path)
    monkeypatch.setattr(supervisor_module, "ensure_datastores", lambda *_, **__: (_ for _ in ()).throw(RuntimeError("Docker is not running")))

    with pytest.raises(StartupFailed) as error:
        supervisor._step_services()

    assert "Docker is not running" in str(error.value)
