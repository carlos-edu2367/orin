from __future__ import annotations

from pathlib import Path

from agentos.omniroute.process_manager import OmniRouteProcessManager, OmniRouteRuntimeSettingsStore


class Process:
    def __init__(self, pid: int = 71) -> None:
        self.pid = pid
        self.terminated = False

    def poll(self) -> int | None:
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True


def test_autostart_persists_and_starts_only_when_gateway_is_absent(tmp_path: Path) -> None:
    settings = OmniRouteRuntimeSettingsStore(tmp_path / "runtime.json")
    settings.set_auto_start("user-a", True)
    process = Process()
    starts: list[tuple[str, ...]] = []
    manager = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: False,
        start_process=lambda command: starts.append(command) or process,
        wait_ready=lambda: True,
    )

    assert manager.auto_start("user-a") is True
    assert manager.start_if_enabled("user-a")["state"] == "ready"
    assert starts == [("omniroute",)]
    assert manager.status()["ownership"] == "agentos"


def test_external_gateway_is_never_started_or_stopped(tmp_path: Path) -> None:
    settings = OmniRouteRuntimeSettingsStore(tmp_path / "runtime.json")
    settings.set_auto_start("user-a", True)
    manager = OmniRouteProcessManager(settings, command=("omniroute",), health=lambda: True)

    assert manager.start_if_enabled("user-a") == {"state": "external", "ownership": "external"}
    assert manager.stop() == {"state": "external", "ownership": "external"}


def test_a_gateway_that_exited_is_reported_as_failed_and_left_unowned(tmp_path: Path) -> None:
    settings = OmniRouteRuntimeSettingsStore(tmp_path / "runtime.json")
    process = Process()
    process.terminated = True  # already gone by the time readiness gave up
    manager = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: False,
        start_process=lambda _command: process,
        wait_ready=lambda: False,
    )

    assert manager.start()["state"] == "failed"
    assert manager.status()["state"] == "stopped"


def test_a_slow_gateway_is_left_starting_rather_than_killed(tmp_path: Path) -> None:
    # A cold OmniRoute can take minutes to answer its first request. Terminating
    # it at the readiness timeout is what would guarantee it never succeeds.
    settings = OmniRouteRuntimeSettingsStore(tmp_path / "runtime.json")
    process = Process()
    manager = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: False,
        start_process=lambda _command: process,
        wait_ready=lambda: False,
    )

    assert manager.start() == {"state": "starting", "ownership": "agentos"}
    assert process.terminated is False
    assert manager.status() == {"state": "starting", "ownership": "agentos"}
    assert manager.stop() == {"state": "stopped", "ownership": None}
    assert process.terminated is True


def test_background_autostart_does_not_wait_for_gateway_health(tmp_path: Path) -> None:
    settings = OmniRouteRuntimeSettingsStore(tmp_path / "runtime.json")
    settings.set_auto_start("user-a", True)
    process = Process()
    manager = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: False,
        start_process=lambda _command: process,
        wait_ready=lambda: (_ for _ in ()).throw(AssertionError("background start must not wait")),
    )

    assert manager.start_if_any_enabled_in_background() == {"state": "starting", "ownership": "agentos"}
    assert manager.status() == {"state": "starting", "ownership": "agentos"}
