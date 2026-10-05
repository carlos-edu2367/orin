"""The console face of ``orin``; lives in ``agentos.installation.console`` so the
installer can use it without importing the whole launcher."""

from agentos.installation.console import *  # noqa: F401,F403
from agentos.installation.console import Console, default_console

__all__ = ["Console", "default_console"]
