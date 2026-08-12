"""``python -m agentos.launcher`` — how the supervisor re-executes itself today.

A frozen build calls the same verbs through ``orin.exe`` instead; see
``RuntimeProfile.service_command``.
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
