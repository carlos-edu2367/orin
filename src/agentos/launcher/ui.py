"""The console face of ``orin``.

Deliberately quiet. One line per step, a URL at the end. Everything else — the
reason a probe took four seconds, the exact command a child was spawned with —
goes to the log file, which is written at full detail whether or not the user
asked for ``--verbose``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TextIO

_ANSI = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "violet": "\033[38;5;99m",
}


def _supports_colour(stream: TextIO) -> bool:
    if os.getenv("NO_COLOR") is not None or os.getenv("ORIN_NO_COLOR") is not None:
        return False
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name == "nt":
        # Windows 10+ terminals understand VT sequences once the mode is set;
        # enabling it is cheap and failing to enable it only costs colour.
        try:
            import ctypes

            handle = ctypes.windll.kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return False
    return True


@dataclass
class Console:
    """Minimal renderer. ``verbose`` adds detail, never noise by default."""

    stream: TextIO
    verbose: bool = False
    quiet: bool = False
    colour: bool | None = None

    def __post_init__(self) -> None:
        if self.colour is None:
            self.colour = _supports_colour(self.stream)

    def _paint(self, text: str, *styles: str) -> str:
        if not self.colour:
            return text
        return "".join(_ANSI[style] for style in styles) + text + _ANSI["reset"]

    def _write(self, text: str = "") -> None:
        if self.quiet:
            return
        self.stream.write(text + "\n")
        self.stream.flush()

    def banner(self) -> None:
        self._write()
        self._write("  " + self._paint("ORIN", "bold", "violet"))
        self._write()

    def step(self, label: str) -> None:
        self._write("  " + self._paint("✓", "green") + " " + label)

    def failed(self, label: str, detail: str | None = None) -> None:
        self._write("  " + self._paint("✗", "red") + " " + label)
        if detail:
            self._write("    " + self._paint(detail, "dim"))

    def warning(self, message: str) -> None:
        self._write("  " + self._paint("!", "yellow") + " " + message)

    def ready(self, url: str) -> None:
        self._write()
        self._write("  " + self._paint("Orin is ready", "bold"))
        self._write("  " + url)
        self._write()

    def stopping(self) -> None:
        self._write()
        self._write("  " + self._paint("Stopping Orin...", "dim"))
        self._write()

    def stopped(self) -> None:
        self._write()
        self._write("  " + self._paint("Orin stopped.", "dim"))
        self._write()

    def already_running(self, url: str, *, opening: bool = True) -> None:
        self._write()
        self._write("  " + self._paint("Orin is already running.", "bold"))
        self._write()
        self._write(("  Opening " if opening else "  ") + url)
        self._write()

    def detail(self, message: str) -> None:
        """Shown only with ``--verbose``; the log file always has it."""
        if self.verbose:
            self._write("    " + self._paint(message, "dim"))

    def error(self, message: str) -> None:
        self.stream.write("\n  " + self._paint("Orin could not start.", "bold", "red") + "\n")
        for line in message.splitlines():
            self.stream.write("  " + line + "\n")
        self.stream.write("\n")
        self.stream.flush()

    def line(self, message: str) -> None:
        self._write(message)


def default_console(verbose: bool = False, quiet: bool = False) -> Console:
    return Console(sys.stdout, verbose=verbose, quiet=quiet)


__all__ = ["Console", "default_console"]
