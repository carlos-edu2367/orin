"""Choosing a loopback port without producing an obscure error.

Three cases have to be told apart, because they need three different answers:
the port is free, the port is our own already-running Orin, or the port belongs
to something else entirely.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

DEFAULT_PORT = 8000
SCAN_LIMIT = 64
LOOPBACK = "127.0.0.1"


class PortUnavailable(RuntimeError):
    """A port was demanded explicitly and something else holds it."""


def port_is_free(port: int, host: str = LOOPBACK) -> bool:
    """Whether a server could bind this port right now.

    Binding is the only honest test. A connect probe cannot distinguish a free
    port from one held by a socket that is not accepting.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # No SO_REUSEADDR: it would report a port in TIME_WAIT as free on POSIX,
        # and the question here is whether the child will actually manage to bind.
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def port_is_listening(port: int, host: str = LOOPBACK, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        try:
            probe.connect((host, port))
        except OSError:
            return False
    return True


@dataclass(frozen=True, slots=True)
class PortChoice:
    port: int
    requested: int
    moved: bool

    @property
    def message(self) -> str | None:
        if not self.moved:
            return None
        return f"Port {self.requested} is in use by another program; using {self.port} instead."


def select_port(requested: int | None, *, explicit: bool = False, host: str = LOOPBACK) -> PortChoice:
    """Pick the port the backend will bind.

    An explicitly requested port is never silently moved: the user asked for a
    specific address and a different one would be a lie. A default port that is
    taken moves forward, because "something else already uses 8000" is not a
    reason to refuse to start.
    """
    preferred = requested or DEFAULT_PORT
    if port_is_free(preferred, host):
        return PortChoice(preferred, preferred, False)
    if explicit:
        raise PortUnavailable(
            f"Port {preferred} is already in use by another program.\n"
            f"Choose a different port with 'orin --port <number>', or stop whatever is listening on {host}:{preferred}."
        )
    for candidate in range(preferred + 1, preferred + SCAN_LIMIT):
        if port_is_free(candidate, host):
            return PortChoice(candidate, preferred, True)
    raise PortUnavailable(
        f"No free port found between {preferred} and {preferred + SCAN_LIMIT - 1} on {host}.\n"
        "Pick one explicitly with 'orin --port <number>'."
    )


__all__ = ["DEFAULT_PORT", "PortChoice", "PortUnavailable", "port_is_free", "port_is_listening", "select_port"]
