"""PyInstaller entry point that preserves the package import context."""

from agentos.launcher.cli import main


raise SystemExit(main())
