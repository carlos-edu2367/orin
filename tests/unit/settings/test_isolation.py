from __future__ import annotations

import os
from pathlib import Path


def test_settings_directories_are_isolated_per_test(tmp_path: Path) -> None:
    for name in ("ORIN_USER_DIR", "ORIN_CLAUDE_HOME", "ORIN_MANAGED_DIR", "ORIN_CLAUDE_MANAGED_DIR"):
        value = os.environ.get(name)
        assert value, f"{name} must be set by tests/conftest.py"
        assert Path(value).resolve() != (Path.home() / ".orin").resolve()
        assert Path(value).resolve() != (Path.home() / ".claude").resolve()
        assert Path(value).exists()
