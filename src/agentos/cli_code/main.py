"""Entry point of ``orin code`` (also the ``orin-code`` console script an SDK host points ``cli_path`` at)."""

from __future__ import annotations

import sys
from typing import Sequence, TextIO

from agentos.version import __version__

from .args import EXCLUDED_SUBCOMMANDS, EXIT_ERROR, EXIT_OK, SUBCOMMANDS, CliUsageError, CodeOptions, parse_code_arguments
from .errors import CliRuntimeError


def version_line() -> str:
    # An SDK host reads the leading x.y.z; Orin's own version is the honest answer (it warns below 2.0.0 and goes on).
    return f"{__version__} (Orin Code)"


def _utf8(*streams: TextIO) -> None:
    # A Windows pipe would be cp1252: the protocol is UTF-8 JSON lines, so the real streams are switched to it.
    for stream in streams:
        if stream in (sys.stdin, sys.stdout, sys.stderr) and hasattr(stream, "reconfigure"):
            if stream is sys.stdin:
                stream.reconfigure(encoding="utf-8")
            else:
                stream.reconfigure(encoding="utf-8", newline="\n")


def _configured_model() -> str | None:
    """The person's own model setting (user and managed layers only: a repository never picks the model)."""
    try:
        from agentos.settings import load_effective_settings

        value = load_effective_settings(None, sources=("user",)).get("model")
    except Exception:  # noqa: BLE001 - unreadable settings mean no configured model
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _exit_on_signal(signum: int, frame: object) -> None:
    # Leaves through the same path as Ctrl-C, so a running turn is cancelled first (W6-R2).
    raise SystemExit(EXIT_ERROR)


def _install_exit_signals() -> None:
    """SIGTERM (and SIGBREAK on Windows) end the run like Ctrl-C does. A console window closed on Windows cannot be
    caught here; the turn's lease ends it instead (W6-R2)."""
    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        return
    for name in ("SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, _exit_on_signal)
        except (OSError, ValueError):
            pass


def run_headless(options: CodeOptions, *, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    from pathlib import Path

    from agentos.installation import orin_paths

    from .headless import HeadlessRun, refuse_unsupported
    from .runtime import connect_local

    refuse_unsupported(options)  # before anything starts
    _utf8(stdin, stdout, stderr)
    _install_exit_signals()
    paths = orin_paths()
    client = connect_local(paths)
    try:
        return HeadlessRun(options, client=client, data_dir=paths.data, cwd=Path.cwd().resolve(), stdin=stdin,
                           stdout=stdout, stderr=stderr, configured_model=_configured_model()).run()
    finally:
        client.close()


def open_interface(options: CodeOptions, *, stdout: TextIO, stderr: TextIO) -> int:
    """``orin code [prompt]`` without ``-p``: a Code session in this folder, opened in the interface (spec §9.2)."""
    import webbrowser
    from pathlib import Path

    from agentos.installation import orin_paths

    from .headless import refuse_unsupported
    from .runtime import connect_local
    from .selection import ModelCatalog, choose_selection
    from .turn_options import permission_mode_for

    if options.desktop:
        raise CliRuntimeError("--desktop is not supported yet: open the Orin desktop app and pick this folder")
    refuse_unsupported(options)
    paths = orin_paths()
    client = connect_local(paths)
    try:
        if not (options.prompt or "").strip():
            url = client.base_url + "/"
            stdout.write(f"Orin is open at {url} (start a Code session there; this folder is {Path.cwd().resolve()})\n")
            webbrowser.open(url)
            return EXIT_OK
        selection = choose_selection(ModelCatalog(client), model=options.model, provider=options.provider, remembered=None,
                                     configured=_configured_model())
        created = client.post("/v1/conversations", {
            "message": options.prompt, "selection": selection.as_request(), "code_mode": "code",
            "workspace_path": str(Path.cwd().resolve()), "permission_mode": permission_mode_for(options),
        })
        url = f"{client.base_url}/chats/{created['conversation_id']}"
        stdout.write(f"{url}\n")
        webbrowser.open(url)
        return EXIT_OK
    finally:
        client.close()


def run_subcommand(name: str, argv: Sequence[str], *, stdout: TextIO, stderr: TextIO) -> int:
    from .commands import run_subcommand as dispatch

    _utf8(stdout, stderr)
    return dispatch(name, list(argv), stdout=stdout, stderr=stderr)


def main(argv: Sequence[str] | None = None, *, stdin: TextIO | None = None, stdout: TextIO | None = None,
         stderr: TextIO | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        if args and args[0] in EXCLUDED_SUBCOMMANDS:
            raise CliUsageError(f"`orin code {args[0]}` is a Claude Code command Orin does not have")
        if args and args[0] in SUBCOMMANDS:
            return run_subcommand(args[0], args[1:], stdout=stdout, stderr=stderr)
        options, show_version = parse_code_arguments(args)
        if show_version:
            stdout.write(version_line() + "\n")
            return EXIT_OK
        if options.headless and options.background:
            from pathlib import Path

            from .commands.background import start_background
            from .headless import refuse_unsupported

            refuse_unsupported(options)
            if not options.print_mode or not (options.prompt or "").strip():
                raise CliUsageError("--bg runs a -p prompt in the background")
            return start_background(args, cwd=Path.cwd().resolve(), stdout=stdout)
        if options.headless:
            return run_headless(options, stdin=stdin, stdout=stdout, stderr=stderr)
        return open_interface(options, stdout=stdout, stderr=stderr)
    except (CliUsageError, CliRuntimeError) as error:
        stderr.write(f"Error: {error}\n")
        return EXIT_ERROR
    except KeyboardInterrupt:
        # Ctrl-C while a turn runs stops that turn before the process goes (W6-R2).
        from .headless import cancel_active_turns

        cancel_active_turns()
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
