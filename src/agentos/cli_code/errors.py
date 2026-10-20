"""Errors of ``orin code``: both end the run with exit code 1 and the message on stderr."""

from __future__ import annotations

from .args import CliUsageError


class CliRuntimeError(Exception):
    """The command line was fine but the run could not happen (no runtime, refused by the API, ...)."""


__all__ = ["CliRuntimeError", "CliUsageError"]
