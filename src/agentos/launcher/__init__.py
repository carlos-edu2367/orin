"""The Orin launcher.

``orin`` is the whole product surface: one command that starts the runtime,
waits for it to actually work, and opens the interface. Everything below it —
FastAPI, arq, Postgres, a bundler — is an implementation detail the person
running the command never has to know about.
"""

from .cli import main
from .supervisor import LaunchOptions, Supervisor

__all__ = ["LaunchOptions", "Supervisor", "main"]
