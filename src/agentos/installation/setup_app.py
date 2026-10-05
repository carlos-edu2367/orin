"""``OrinSetup``: the installer people double-click.

Three pieces, kept apart so the logic can be tested without a display:

* ``SetupModel`` turns the engine's ``UpdateEvent`` stream into what a window
  shows (seven steps, one overall percentage, one line of detail);
* ``SetupWindow`` is the thin tkinter view over it;
* ``run_silent`` is the same flow for terminals, scripts and machines with no
  display (``OrinSetup --silent``).

The heavy lifting is ``agentos.installation.installer.Installer``, i.e. the very
engine behind ``orin update``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import queue
import subprocess
import sys
from threading import Thread
from typing import Any, Callable

from .installer import (
    INTEGRATE_STEP, SETUP_STEPS, InstallOptions, InstallResult, Installer, default_bin_root, default_install_root,
)
from .updater import UpdateError, UpdateEvent

# How much of the progress bar each step owns. The download dominates the wait.
WEIGHTS = {"check": 3, "download": 62, "verify": 3, "extract": 15, "validate": 5, "activate": 4, "integrate": 8}


def _mb(value: float) -> str:
    return f"{value / 1_048_576:.1f}".replace(".", ",")


@dataclass(slots=True)
class StepView:
    key: str
    label: str
    state: str = "pending"  # pending | running | done | failed
    detail: str = ""


@dataclass(slots=True)
class SetupModel:
    steps: list[StepView] = field(default_factory=lambda: [StepView(key, label) for key, label in SETUP_STEPS])
    fraction: float = 0.0
    detail: str = ""
    _progress: dict[str, float] = field(default_factory=dict)

    def apply(self, event: UpdateEvent) -> None:
        step = next((item for item in self.steps if item.key == event.step), None)
        if step is None:
            return
        if event.phase == "start":
            step.state, step.detail = "running", ""
            if event.label and step.key == "download":
                step.label = event.label
        elif event.phase == "progress":
            step.state = "running"
            if event.progress is not None:
                self._progress[step.key] = event.progress
            self.detail = self._describe(event)
        elif event.phase == "done":
            step.state = "done"
            self._progress[step.key] = 1.0
            if event.step == "download":
                self.detail = ""
            if event.detail and event.step == "check":
                step.detail = event.detail
        elif event.phase == "rolled_back":
            step.state, step.detail = "failed", event.detail or ""
        self.fraction = self._overall()

    def fail(self) -> None:
        for step in self.steps:
            if step.state == "running":
                step.state = "failed"
        self.detail = ""

    def _overall(self) -> float:
        total = sum(WEIGHTS.values())
        earned = 0.0
        for step in self.steps:
            weight = WEIGHTS.get(step.key, 0)
            if step.state == "done":
                earned += weight
            elif step.state == "running":
                earned += weight * self._progress.get(step.key, 0.0)
        return min(1.0, earned / total)

    @staticmethod
    def _describe(event: UpdateEvent) -> str:
        if event.step != "download" or event.bytes_done is None:
            return ""
        parts = [f"{_mb(event.bytes_done)} de {_mb(event.bytes_total)} MB" if event.bytes_total else f"{_mb(event.bytes_done)} MB"]
        if event.speed:
            parts.append(f"{_mb(event.speed)} MB/s")
            if event.bytes_total:
                remaining = max(0, int((event.bytes_total - event.bytes_done) / event.speed))
                parts.append(f"~{remaining} s" if remaining < 60 else f"~{remaining // 60} min {remaining % 60:02d} s")
        return " · ".join(parts)


def options_from(arguments: argparse.Namespace) -> InstallOptions:
    return InstallOptions(
        root=Path(arguments.dir) if arguments.dir else default_install_root(),
        bin_root=default_bin_root(),
        version=arguments.to or "latest",
        desktop_shortcut=not arguments.no_shortcut,
        menu_entry=not arguments.no_shortcut,
        add_to_path=not arguments.no_path,
        background=arguments.background,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="OrinSetup", description="Installs (or updates) Orin for the current user.")
    parser.add_argument("--silent", action="store_true", help="no window: print progress in the terminal")
    parser.add_argument("--dir", default=None, help="install folder (default: your user programs folder)")
    parser.add_argument("--to", default=None, metavar="VERSION", help="install an exact version instead of the latest")
    parser.add_argument("--background", action="store_true", help="start Orin in the background when you sign in to the computer")
    parser.add_argument("--no-shortcut", action="store_true", help="do not create desktop/menu shortcuts")
    parser.add_argument("--no-path", action="store_true", help="do not add the orin command to PATH")
    return parser


# -- silent / terminal -----------------------------------------------------


def run_silent(options: InstallOptions, *, stream: Any = None, installer_factory: Callable[..., Installer] = Installer) -> int:
    from .console import Console

    console = Console(stream or sys.stdout)
    from .update_ui import UpdateRenderer

    renderer = UpdateRenderer(console, current_version="—")
    console.banner()
    console.line("  " + console.paint("Instalação", "bold"))
    console.line("")
    try:
        result = installer_factory(options, emit=renderer).run()
    except UpdateError as error:
        renderer.failed(error)
        return 1
    console.line("")
    console.line("  " + console.paint(_headline(result), "bold", "green"))
    console.line(f"    Versão     {result.version}")
    console.line(f"    Pasta      {result.root}")
    if result.path_hint:
        console.line("    " + console.paint(result.path_hint, "dim"))
    console.line("")
    return 0


def _headline(result: InstallResult) -> str:
    return {"installed": "Orin instalado", "updated": "Orin atualizado", "up_to_date": "O Orin já está na versão mais recente"}[result.status]


# -- the window --------------------------------------------------------------

BG, SURFACE, LINE, TEXT, MUTED, FAINT = "#0b0a14", "#14122a", "#2a2745", "#f5f1ff", "#a39db8", "#6e6889"
ACCENT, ACCENT_STRONG, GREEN, RED = "#a66cff", "#7c3aed", "#6ee7a8", "#ff9d91"


class SetupWindow:  # pragma: no cover - exercised by hand and by the Xvfb screenshot, not by unit tests
    def __init__(self, options: InstallOptions, *, installer_factory: Callable[..., Installer] = Installer) -> None:
        import tkinter as tk  # noqa: PLC0415 - absent on headless builds; only the window needs it
        from tkinter import filedialog  # noqa: PLC0415

        self.tk, self.filedialog = tk, filedialog
        self.options = options
        self.factory = installer_factory
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.model = SetupModel()
        self.release: str | None = None
        self.result: InstallResult | None = None
        root = self.root = tk.Tk()
        self.shortcut = tk.BooleanVar(master=root, value=options.desktop_shortcut)
        self.path = tk.BooleanVar(master=root, value=options.add_to_path)
        self.background = tk.BooleanVar(master=root, value=options.background)
        root.title("Instalador do Orin")
        root.configure(bg=BG)
        root.geometry("560x500")
        root.resizable(False, False)
        self._center()
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=34, pady=(26, 22))
        self.show_welcome()
        root.after(80, self._pump)
        Thread(target=self._look_up_release, daemon=True).start()

    # structure ---------------------------------------------------------

    def _center(self) -> None:
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 560) // 2
        y = (self.root.winfo_screenheight() - 500) // 3
        self.root.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def _label(self, parent: Any, text: str, *, size: int = 10, color: str = TEXT, weight: str = "normal", wrap: int = 490, **grid: Any) -> Any:
        label = self.tk.Label(parent, text=text, bg=BG, fg=color, font=("Segoe UI", size, weight), justify="left", anchor="w", wraplength=wrap)
        label.pack(anchor="w", **grid)
        return label

    def _button(self, parent: Any, text: str, command: Callable[[], None], *, primary: bool = False) -> Any:
        button = self.tk.Button(
            parent, text=text, command=command, relief="flat", bd=0, padx=22, pady=9, cursor="hand2",
            bg=ACCENT_STRONG if primary else SURFACE, fg=TEXT, activebackground=ACCENT if primary else LINE, activeforeground=TEXT,
            font=("Segoe UI", 10, "bold" if primary else "normal"), highlightthickness=1, highlightbackground=ACCENT_STRONG if primary else LINE,
        )
        button.pack(side="right", padx=(10, 0))
        return button

    def _brand(self) -> None:
        row = self.tk.Frame(self.body, bg=BG)
        row.pack(anchor="w", pady=(0, 18))
        dots = self.tk.Canvas(row, width=22, height=22, bg=BG, highlightthickness=0)
        for index, (x, y) in enumerate(((3, 3), (13, 3), (3, 13), (13, 13))):
            dots.create_oval(x, y, x + 6, y + 6, fill=ACCENT if index != 3 else ACCENT_STRONG, outline="")
        dots.pack(side="left")
        self.tk.Label(row, text="ORIN", bg=BG, fg=TEXT, font=("Segoe UI", 15, "bold")).pack(side="left", padx=(10, 0))

    # screens -----------------------------------------------------------

    def show_welcome(self) -> None:
        tk = self.tk
        self._clear()
        self._brand()
        self._label(self.body, "Instalar o Orin", size=19, weight="bold")
        self.release_label = self._label(self.body, "Procurando a versão mais recente…", color=MUTED, pady=(4, 20))
        self._label(self.body, "Onde será instalado", size=9, color=FAINT)
        place = tk.Frame(self.body, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        place.pack(fill="x", pady=(4, 16))
        self.place_label = tk.Label(place, text=str(self.options.root), bg=SURFACE, fg=TEXT, anchor="w", font=("Consolas", 9), padx=12, pady=9)
        self.place_label.pack(side="left", fill="x", expand=True)
        change = tk.Button(place, text="Alterar…", command=self._choose_folder, relief="flat", bd=0, bg=SURFACE, fg=ACCENT, activebackground=SURFACE, activeforeground=TEXT, cursor="hand2", font=("Segoe UI", 9), highlightthickness=0)
        change.pack(side="right", padx=8)
        for variable, text in ((self.shortcut, "Criar atalhos na Área de Trabalho e no menu Iniciar"), (self.path, "Adicionar o comando orin ao terminal"),
                                   (self.background, "Iniciar o Orin em segundo plano ao ligar o computador")):
            tk.Checkbutton(
                self.body, text=text, variable=variable, bg=BG, fg=TEXT, selectcolor=SURFACE, activebackground=BG, activeforeground=TEXT,
                font=("Segoe UI", 10), anchor="w", highlightthickness=0,
            ).pack(anchor="w", pady=2)
        self._label(self.body, "Instala só para o seu usuário. Não pede senha de administrador. O navegador do agente é opcional e pode ser instalado depois, dentro do Orin.", size=9, color=FAINT, pady=(16, 0))
        footer = tk.Frame(self.body, bg=BG)
        footer.pack(side="bottom", fill="x")
        self._button(footer, "Cancelar", self.root.destroy)
        self.install_button = self._button(footer, "Instalar", self.start_install, primary=True)
        self.install_button.configure(state="disabled")
        self._sync_release()

    def show_progress(self) -> None:
        tk = self.tk
        self._clear()
        self._brand()
        self._label(self.body, "Instalando o Orin", size=19, weight="bold", pady=(0, 14))
        self.step_rows: dict[str, tuple[Any, Any]] = {}
        for step in self.model.steps:
            row = tk.Frame(self.body, bg=BG)
            row.pack(fill="x", pady=2)
            mark = tk.Label(row, text="○", bg=BG, fg=FAINT, width=2, font=("Segoe UI", 10))
            mark.pack(side="left")
            text = tk.Label(row, text=step.label, bg=BG, fg=FAINT, anchor="w", font=("Segoe UI", 10))
            text.pack(side="left")
            self.step_rows[step.key] = (mark, text)
        self.bar = tk.Canvas(self.body, height=8, bg=SURFACE, highlightthickness=0)
        self.bar.pack(fill="x", pady=(18, 6))
        self.detail = self._label(self.body, " ", size=9, color=MUTED)
        self._render_progress()

    def show_done(self, result: InstallResult) -> None:
        tk = self.tk
        self._clear()
        self._brand()
        title = {"installed": "Orin instalado", "updated": "Orin atualizado", "up_to_date": "O Orin já está atualizado"}[result.status]
        self._label(self.body, "✓  " + title, size=19, weight="bold", color=GREEN, pady=(0, 6))
        self._label(self.body, f"Versão {result.version}", color=MUTED, pady=(0, 14))
        if result.notes:
            self._label(self.body, result.notes.strip()[:400], size=9, color=MUTED, pady=(0, 8))
        self._label(self.body, "Se o Orin estiver aberto, feche e abra de novo para usar a nova versão." if result.status != "installed" else "Abra o Orin pelo atalho ou digite  orin  em um novo terminal.", size=9, color=FAINT)
        if result.path_hint:
            self._label(self.body, result.path_hint, size=9, color=FAINT, pady=(6, 0))
        footer = tk.Frame(self.body, bg=BG)
        footer.pack(side="bottom", fill="x")
        self._button(footer, "Fechar", self.root.destroy)
        self._button(footer, "Abrir o Orin", lambda: self._open(result), primary=True)

    def show_error(self, error: UpdateError) -> None:
        tk = self.tk
        self._clear()
        self._brand()
        self._label(self.body, "A instalação não foi concluída", size=19, weight="bold", color=RED, pady=(0, 10))
        self._label(self.body, error.message, pady=(0, 6))
        if error.hint:
            self._label(self.body, error.hint, color=MUTED, size=9)
        self._label(self.body, "A versão que você já tinha (se havia uma) continua como estava.", size=9, color=FAINT, pady=(10, 0))
        footer = tk.Frame(self.body, bg=BG)
        footer.pack(side="bottom", fill="x")
        self._button(footer, "Fechar", self.root.destroy)
        self._button(footer, "Tentar de novo", self.start_install, primary=True)

    # behaviour ---------------------------------------------------------

    def _choose_folder(self) -> None:
        chosen = self.filedialog.askdirectory(initialdir=str(self.options.root.parent), title="Escolha a pasta de instalação")
        if chosen:
            folder = Path(chosen)
            self.options = InstallOptions(
                root=folder if folder.name.lower() == "orin" else folder / "Orin", bin_root=self.options.bin_root, version=self.options.version,
                desktop_shortcut=self.options.desktop_shortcut, menu_entry=self.options.menu_entry, add_to_path=self.options.add_to_path,
                background=self.options.background,
            )
            self.place_label.configure(text=str(self.options.root))

    def _sync_release(self) -> None:
        if self.release is None:
            return
        failed = self.release.startswith("!")
        self.release_label.configure(text=self.release[1:] if failed else f"Versão {self.release}", fg=RED if failed else MUTED)
        self.install_button.configure(state="disabled" if failed else "normal")

    def _look_up_release(self) -> None:
        try:
            self.events.put(("release", self.factory(self.options).check().version))
        except UpdateError as error:
            self.events.put(("release", "!" + error.message))
        except Exception:  # noqa: BLE001
            self.events.put(("release", "!Não consegui consultar a versão mais recente."))

    def start_install(self) -> None:
        self.options = InstallOptions(
            root=self.options.root, bin_root=self.options.bin_root, version=self.options.version,
            desktop_shortcut=bool(self.shortcut.get()), menu_entry=bool(self.shortcut.get()), add_to_path=bool(self.path.get()),
            background=bool(self.background.get()),
        )
        self.model = SetupModel()
        self.show_progress()
        Thread(target=self._install, daemon=True).start()

    def _install(self) -> None:
        try:
            result = self.factory(self.options, emit=lambda event: self.events.put(("event", event))).run()
            self.events.put(("done", result))
        except UpdateError as error:
            self.events.put(("error", error))
        except Exception as error:  # noqa: BLE001
            self.events.put(("error", UpdateError(f"Erro inesperado durante a instalação ({type(error).__name__}).", hint="Tente de novo.")))

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "release":
                    self.release = payload
                    if hasattr(self, "install_button") and self.install_button.winfo_exists():
                        self._sync_release()
                elif kind == "event":
                    self.model.apply(payload)
                    self._render_progress()
                elif kind == "done":
                    self.show_done(payload)
                elif kind == "error":
                    self.model.fail()
                    self.show_error(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _render_progress(self) -> None:
        if not hasattr(self, "bar") or not self.bar.winfo_exists():
            return
        marks = {"pending": ("○", FAINT, FAINT), "running": ("◉", ACCENT, TEXT), "done": ("✓", GREEN, MUTED), "failed": ("✗", RED, RED)}
        for step in self.model.steps:
            mark, text = self.step_rows[step.key]
            symbol, mark_color, text_color = marks[step.state]
            mark.configure(text=symbol, fg=mark_color)
            text.configure(text=step.label + (f"  ·  {step.detail}" if step.detail else ""), fg=text_color)
        self.bar.update_idletasks()
        width = max(self.bar.winfo_width(), 1)
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, width * self.model.fraction, 8, fill=ACCENT, outline="")
        self.detail.configure(text=self.model.detail or " ")

    def _open(self, result: InstallResult) -> None:
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        try:
            subprocess.Popen(list(result.launch_command), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=os.name != "nt", creationflags=flags)
        except OSError:
            pass
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def _attach_console() -> None:
    """A windowed Windows exe has no stdout; attach to the launching terminal for ``--silent``."""
    if os.name != "nt":
        return
    try:
        import ctypes  # noqa: PLC0415

        if ctypes.windll.kernel32.AttachConsole(-1):  # type: ignore[attr-defined]
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
            sys.stderr = sys.stdout
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    options = options_from(arguments)
    if arguments.silent:
        _attach_console()
        return run_silent(options)
    try:
        window = SetupWindow(options)
    except Exception as error:  # noqa: BLE001 - no display, or tkinter missing: fall back to the terminal flow
        if os.environ.get("ORIN_SETUP_DEBUG"):
            raise
        sys.stderr.write(f"Sem interface gráfica disponível ({type(error).__name__}); instalando pelo terminal.\n")
        return run_silent(options)
    return window.run()


__all__ = ["INTEGRATE_STEP", "SetupModel", "SetupWindow", "build_parser", "main", "options_from", "run_silent"]
