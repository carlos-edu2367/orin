"""How ``orin update`` looks in a terminal.

The engine (``agentos.installation.updater``) only emits events; this turns them
into numbered steps, one live progress bar and a short summary. On a terminal
the active step redraws in place; piped or logged output gets plain lines (the
download at 25% intervals) so a CI log is readable too.
"""

from __future__ import annotations

import json
import sys
from typing import TextIO

from .updater import STEPS, UpdateError, UpdateEvent, UpdateResult

from .console import Console

BAR_WIDTH = 24
_STEP_LABELS = dict(STEPS) | {"integrate": "Criando o comando orin e os atalhos"}


def _mb(value: float) -> str:
    return f"{value / 1_048_576:.1f}".replace(".", ",")


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min {seconds % 60:02d} s"


def bar(fraction: float, width: int = BAR_WIDTH) -> str:
    filled = max(0, min(width, int(fraction * width)))
    return "█" * filled + "░" * (width - filled)


class UpdateRenderer:
    """Consumes ``UpdateEvent`` values; one instance per ``orin update`` run."""

    def __init__(self, console: Console, *, current_version: str) -> None:
        self.console = console
        self.current_version = current_version
        self._plain_progress = -1

    def __call__(self, event: UpdateEvent) -> None:
        c = self.console
        tag = f"[{event.index + 1}/{event.total}]"
        if event.phase == "start":
            self._plain_progress = -1
            c.redraw(f"  {c.paint('◦', 'violet')} {c.paint(tag, 'dim')} {event.label}…")
        elif event.phase == "progress":
            self._progress(event, tag)
        elif event.phase == "done":
            detail = f" {c.paint('· ' + event.detail, 'dim')}" if event.detail else ""
            label = event.label if event.label != _STEP_LABELS.get(event.step) else _past(event.step)
            c.settle(f"  {c.paint('✓', 'green')} {c.paint(tag, 'dim')} {label}{detail}")
        elif event.phase == "rolled_back":
            c.settle(f"  {c.paint('↺', 'yellow')} {c.paint(tag, 'dim')} {event.label}" + (f" {c.paint('· ' + event.detail, 'dim')}" if event.detail else ""))

    def _progress(self, event: UpdateEvent, tag: str) -> None:
        c = self.console
        if event.progress is None:
            return
        percent = int(event.progress * 100)
        if c.interactive:
            parts = [f"{percent:>3}%"]
            if event.bytes_done is not None and event.bytes_total:
                parts.append(f"{_mb(event.bytes_done)}/{_mb(event.bytes_total)} MB")
            if event.speed:
                parts.append(f"{_mb(event.speed)} MB/s")
                if event.bytes_total and event.bytes_done is not None:
                    parts.append(f"~{_duration((event.bytes_total - event.bytes_done) / event.speed)}")
            c.redraw(f"  {c.paint('◦', 'violet')} {c.paint(tag, 'dim')} {event.label}  {c.paint(bar(event.progress), 'violet')} {c.paint('  '.join(parts), 'dim')}")
        elif percent // 25 > self._plain_progress and event.step == "download":
            self._plain_progress = percent // 25
            c.line(f"    {event.label}: {percent}%")

    def header(self) -> None:
        self.console.banner()
        self.console.line("  " + self.console.paint("Atualização", "bold") + self.console.paint(f"  (versão atual {self.current_version})", "dim"))
        self.console.line("")

    def available(self, result: UpdateResult) -> None:
        c = self.console
        c.line("")
        c.line(f"  {c.paint('Nova versão disponível:', 'bold')} {result.version} {c.paint(f'(você tem {result.previous_version})', 'dim')}")
        if result.notes:
            c.line("")
            for line in result.notes.strip().splitlines()[:8]:
                c.line("    " + line)
        c.line("")
        c.line("  Para instalar, rode: " + c.paint("orin update", "bold"))
        c.line("")

    def up_to_date(self, result: UpdateResult) -> None:
        c = self.console
        c.line("")
        c.line(f"  {c.paint('✓', 'green')} Você já está na versão mais recente ({result.previous_version}).")
        c.line("")

    def updated(self, result: UpdateResult) -> None:
        c = self.console
        rows = [("Versão", f"{result.previous_version} → {c.paint(result.version, 'bold')}")]
        if result.downloaded_bytes:
            rows.append(("Download", f"{_mb(result.downloaded_bytes)} MB em {_duration(result.seconds)}"))
        if result.release_url:
            rows.append(("Novidades", result.release_url))
        c.line("")
        c.line("  " + c.paint("Orin atualizado", "bold", "green"))
        c.line("")
        for name, value in rows:
            c.line(f"    {c.paint(name.ljust(11), 'dim')}{value}")
        c.line("")
        c.line("  Para usar a nova versão, abra o Orin: " + c.paint("orin", "bold"))
        c.line("  " + c.paint("Algo deu errado? Volte à anterior com: orin update --rollback", "dim"))
        c.line("")

    def rolled_back(self, result: UpdateResult) -> None:
        c = self.console
        c.line("")
        c.line("  " + c.paint("Versão restaurada", "bold", "green"))
        c.line(f"    {c.paint('Ativa'.ljust(11), 'dim')}{result.version}")
        c.line("")

    def failed(self, error: UpdateError) -> None:
        c = self.console
        c.line("")
        c.line("  " + c.paint("✗ A atualização não foi concluída.", "bold", "red"))
        c.line(f"    {error.message}")
        if error.hint:
            c.line("    " + c.paint(error.hint, "dim"))
        if not error.rolled_back:
            c.line("    " + c.paint("A versão instalada continua a mesma.", "dim"))
        c.line("")


def _past(step: str) -> str:
    return {
        "check": "Versão mais recente encontrada",
        "download": "Download concluído",
        "verify": "Integridade confirmada",
        "extract": "Arquivos extraídos",
        "validate": "Nova versão testada",
        "activate": "Versão ativada",
        "integrate": "Comando orin e atalhos criados",
    }.get(step, step)


class JsonRenderer:
    """One JSON object per line, for the desktop app and scripts."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout

    def __call__(self, event: UpdateEvent) -> None:
        self._write({"type": "event", **event.as_dict()})

    def result(self, result: UpdateResult) -> None:
        self._write({"type": "result", **result.as_dict()})

    def failed(self, error: UpdateError) -> None:
        self._write({"type": "error", "message": error.message, "step": error.step, "hint": error.hint, "rolled_back": error.rolled_back})

    def _write(self, payload: dict[str, object]) -> None:
        self.stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.stream.flush()
