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


def run_headless(options: CodeOptions, *, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    raise CliRuntimeError("headless runs are not available in this build yet")


def open_interface(options: CodeOptions, *, stdout: TextIO, stderr: TextIO) -> int:
    raise CliRuntimeError("opening a Code session in the interface is not available in this build yet")


def run_subcommand(name: str, argv: Sequence[str], *, stdout: TextIO, stderr: TextIO) -> int:
    raise CliRuntimeError(f"`orin code {name}` is not available in this build yet")


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
        if options.headless:
            return run_headless(options, stdin=stdin, stdout=stdout, stderr=stderr)
        return open_interface(options, stdout=stdout, stderr=stderr)
    except (CliUsageError, CliRuntimeError) as error:
        stderr.write(f"Error: {error}\n")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
