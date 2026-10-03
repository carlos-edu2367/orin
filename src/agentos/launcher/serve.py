"""``orin serve`` — run the server profile in the foreground.

Built for a container or a systemd unit: no browser, no desktop window, no
per-user instance lock, logs on stdout. If any service exits, everything
stops and the exit code is returned so the process manager restarts it.
"""
from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from time import sleep
from typing import Callable, Mapping, Sequence

from agentos.installation import OrinPaths, RuntimeProfile
from agentos.installation.layout import migrate_data_layout

from .environment import ConfigurationError, load_server_environment
from .internal import SERVICES
from .processes import child_environment
from .services import apply_migrations
from .ui import Console


class ServeSupervisor:
    def __init__(self, commands: Mapping[str, Sequence[str]], environment: Mapping[str, str], *, cwd: Path, popen: Callable[..., subprocess.Popen] = subprocess.Popen, poll_interval: float = 0.5) -> None:
        self.commands = {name: list(command) for name, command in commands.items()}
        self.environment = dict(environment)
        self.cwd = cwd
        self._popen = popen
        self._poll_interval = poll_interval
        self.children: dict[str, subprocess.Popen] = {}
        self._stopping = False

    def start(self) -> None:
        for name, command in self.commands.items():
            self.children[name] = self._popen(command, cwd=str(self.cwd), env=self.environment, stdin=subprocess.DEVNULL)

    def stop(self) -> None:
        self._stopping = True
        for child in self.children.values():
            if child.poll() is None:
                child.terminate()
        for child in self.children.values():
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)

    def run(self) -> int:
        self.start()
        try:
            while not self._stopping:
                for child in self.children.values():
                    code = child.poll()
                    if code is not None:
                        return code
                sleep(self._poll_interval)
            return 0
        finally:
            self.stop()


def command_serve(paths: OrinPaths, profile: RuntimeProfile, console: Console) -> int:
    import logging

    log = logging.getLogger("orin.serve")
    paths.ensure()
    try:
        environment = load_server_environment(paths, profile)
    except ConfigurationError as error:
        console.error(str(error))
        return 2
    apply_migrations(environment, profile, log=log)
    if migrate_data_layout(paths):
        log.info("moved managed workspaces into the per-profile layout")
    commands = {name: profile.service_command(name) for name in SERVICES}
    supervisor = ServeSupervisor(commands, child_environment(environment.values, {}), cwd=profile.repository or paths.data)

    def request_stop(_signum, _frame) -> None:  # noqa: ANN001 - signal handler signature
        supervisor._stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    console.line(f"Orin server em {environment.values['ORIN_PUBLIC_URL']} (escutando {environment.values['ORIN_BACKEND_HOST']}:{environment.values['ORIN_BACKEND_PORT']})")
    return supervisor.run()


__all__ = ["ServeSupervisor", "command_serve"]
