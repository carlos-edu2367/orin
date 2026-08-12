"""``orin`` — the command.

``orin`` with no arguments is the product: start everything, wait until it really
works, open the browser. The subcommands exist for the times that is not what you
want, and none of them are required to use Orin.
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep

from agentos.installation import OrinPaths, RuntimeProfile, orin_paths, runtime_profile

from .internal import SERVICES, run_service
from .ports import DEFAULT_PORT, port_is_listening
from .probes import http_probe
from .state import (
    InstanceLock,
    kill_process_tree,
    process_is_alive,
    read_state,
    request_stop,
    running_instance,
)
from .supervisor import LaunchOptions, Supervisor, attach_to_running, wait_for_exit
from .ui import Console, default_console

PROGRAM = "orin"
STOP_TIMEOUT = 30.0


def _start_options() -> argparse.ArgumentParser:
    """Options shared by the bare command and by ``start``/``restart``.

    Defaults are suppressed so a subcommand's copy never overwrites a value the
    top-level parser already accepted: ``orin --port 9000 start`` and
    ``orin start --port 9000`` have to mean the same thing.
    """
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--port", type=int, default=argparse.SUPPRESS, help=f"port for the Orin interface (default {DEFAULT_PORT})")
    shared.add_argument("--no-browser", action="store_true", default=argparse.SUPPRESS, help="do not open a browser window")
    shared.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="show startup detail on the console")
    return shared


def build_parser() -> argparse.ArgumentParser:
    shared = _start_options()
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Orin — a local-first agent workspace. Run 'orin' to start it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[shared],
        epilog=(
            "examples:\n"
            "  orin                    start Orin and open it in your browser\n"
            "  orin --port 9000        start on a specific port\n"
            "  orin status             show whether Orin is running\n"
            "  orin logs --follow      watch the launcher log\n"
            "  orin stop               stop a running Orin\n"
        ),
    )
    parser.add_argument("--version", action="store_true", help="print the Orin version and exit")
    parser.set_defaults(port=None, no_browser=False, verbose=False, command=None)

    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("start", parents=[shared], help="start the Orin runtime (the default)")
    commands.add_parser("stop", help="stop a running Orin")
    commands.add_parser("restart", parents=[shared], help="stop a running Orin and start it again")
    commands.add_parser("status", help="show whether Orin is running, and where")

    logs = commands.add_parser("logs", help="show Orin's logs")
    logs.add_argument("--service", choices=("launcher", *SERVICES), default="launcher", help="which log to show")
    logs.add_argument("-n", "--lines", type=int, default=50, help="how many lines to show (default 50)")
    logs.add_argument("-f", "--follow", action="store_true", help="keep printing new lines until interrupted")

    # No `help=`: that is what keeps this verb out of the public command list.
    # It is a contract between the launcher and the children it spawns.
    internal = commands.add_parser("internal-service")
    internal.add_argument("service", choices=SERVICES)

    return parser


def configure_logging(paths: OrinPaths, verbose: bool) -> logging.Logger:
    """Full detail to the file, always. The console stays minimal."""
    paths.logs.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("orin")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    handler = RotatingFileHandler(paths.logs / "launcher.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    if verbose:
        stderr = logging.StreamHandler(sys.stderr)
        stderr.setFormatter(logging.Formatter("    %(levelname)-7s %(message)s"))
        stderr.setLevel(logging.INFO)
        logger.addHandler(stderr)
    logger.propagate = False
    return logger.getChild("launcher")


# -- commands -----------------------------------------------------------


def command_start(arguments: argparse.Namespace, paths: OrinPaths, profile: RuntimeProfile, console: Console) -> int:
    existing = running_instance(paths)
    lock = InstanceLock(paths.instance_lock)
    if existing is not None or not lock.acquire():
        state = existing or read_state(paths)
        if state is not None:
            return attach_to_running(console, state, open_browser=not arguments.no_browser)
        console.error("Another Orin launcher is starting on this installation. Try again in a moment.")
        return 1

    log = configure_logging(paths, arguments.verbose)
    supervisor = Supervisor(
        paths=paths,
        profile=profile,
        console=console,
        options=LaunchOptions(
            port=arguments.port,
            explicit_port=arguments.port is not None,
            open_browser=not arguments.no_browser,
            verbose=arguments.verbose,
        ),
        log=log,
    )
    try:
        return supervisor.start(lock)
    finally:
        lock.release()


def command_stop(paths: OrinPaths, console: Console) -> int:
    state = running_instance(paths)
    if state is None:
        console.line("\n  Orin is not running.\n")
        return 0
    console.line(f"\n  Stopping Orin (pid {state.pid})...")
    request_stop(paths)
    if wait_for_exit(state.pid, STOP_TIMEOUT):
        console.line("  Orin stopped.\n")
        return 0
    # The supervisor did not act on the request. Its children are inside its job
    # object, so killing the tree cannot leave them behind.
    console.line("  Orin did not stop on request; terminating it.")
    if kill_process_tree(state.pid) and wait_for_exit(state.pid, 10.0):
        console.line("  Orin stopped.\n")
        return 0
    console.error(f"Could not stop the Orin process with pid {state.pid}.")
    return 1


def command_status(paths: OrinPaths, profile: RuntimeProfile, console: Console) -> int:
    state = running_instance(paths)
    console.line("")
    if state is None:
        console.line("  Orin is not running.")
        console.line(f"  Profile   {profile.kind} ({profile.root})")
        console.line(f"  Data      {paths.data}")
        console.line(f"  Logs      {paths.logs}")
        console.line("")
        return 3
    reachable = port_is_listening(state.port) and bool(http_probe(f"{state.url}/healthz", timeout=2.0))
    console.line("  Orin is running." if reachable else "  Orin is starting.")
    console.line(f"  URL       {state.url}")
    console.line(f"  Process   {state.pid}" + ("" if process_is_alive(state.pid) else " (not responding)"))
    console.line(f"  Version   {state.version}")
    console.line(f"  Profile   {state.profile} ({profile.root})")
    console.line(f"  Started   {state.started_at}")
    console.line(f"  Logs      {paths.logs}")
    console.line("")
    return 0


def command_logs(arguments: argparse.Namespace, paths: OrinPaths, console: Console) -> int:
    path = paths.logs / "launcher.log" if arguments.service == "launcher" else paths.service_log(arguments.service)
    if not path.is_file():
        console.line(f"\n  No log yet at {path}\n")
        return 1
    console.line(f"\n  {path}\n")
    for line in _tail(path, arguments.lines):
        console.line(line)
    if not arguments.follow:
        return 0
    try:
        _follow(path, console)
    except KeyboardInterrupt:
        console.line("")
    return 0


def _tail(path: Path, lines: int) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-max(lines, 1):]


def _follow(path: Path, console: Console) -> None:
    with open(path, encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if line:
                console.line(line.rstrip("\n"))
                continue
            sleep(0.3)


def command_restart(arguments: argparse.Namespace, paths: OrinPaths, profile: RuntimeProfile, console: Console) -> int:
    code = command_stop(paths, console)
    if code != 0:
        return code
    return command_start(arguments, paths, profile, console)


# -- entry point --------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if getattr(arguments, "command", None) == "internal-service":
        return run_service(arguments.service)

    profile = runtime_profile()
    if arguments.version:
        sys.stdout.write(f"orin {profile.version} ({profile.kind})\n")
        return 0

    paths = orin_paths()
    console = default_console(verbose=getattr(arguments, "verbose", False))
    command = getattr(arguments, "command", None)

    try:
        if command in (None, "start"):
            return command_start(arguments, paths, profile, console)
        if command == "stop":
            return command_stop(paths, console)
        if command == "restart":
            return command_restart(arguments, paths, profile, console)
        if command == "status":
            return command_status(paths, profile, console)
        if command == "logs":
            return command_logs(arguments, paths, console)
    except KeyboardInterrupt:
        console.line("")
        return 130
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
