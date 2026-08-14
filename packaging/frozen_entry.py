"""PyInstaller entry point that preserves the package import context."""

import multiprocessing

if __name__ == "__main__":
    # Required for a PyInstaller-frozen app that spawns multiprocessing child
    # processes on Windows: the default "spawn" start method re-executes this
    # very executable with special --multiprocessing-fork arguments. Without
    # this call (made before anything else runs), that re-invocation falls
    # through to the CLI's own argument parser instead of becoming the
    # intended child process, and fails immediately. That silently breaks
    # every isolated child process this app starts under multiprocessing —
    # including the sandboxed conversational browser host — because the
    # parent process then waits for a response that will never arrive.
    multiprocessing.freeze_support()

    from agentos.launcher.cli import main

    raise SystemExit(main())
