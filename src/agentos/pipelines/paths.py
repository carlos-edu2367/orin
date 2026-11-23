"""Where pipeline files live and the limits applied when reading them."""
from __future__ import annotations

from pathlib import Path

from agentos.settings.paths import orin_user_dir

MAX_FILE_BYTES = 256 * 1024
SUFFIXES = (".yaml", ".yml")
APPROVALS_FILENAME = "pipeline-approvals.json"


def project_dir(root: Path | str) -> Path:
    """``<workspace>/.orin/pipelines`` (read only from a trusted workspace)."""
    return Path(root) / ".orin" / "pipelines"


def user_dir() -> Path:
    """``<orin user dir>/pipelines`` (``~/.orin/pipelines`` unless ``ORIN_USER_DIR`` says otherwise)."""
    return orin_user_dir() / "pipelines"


def builtin_dir() -> Path:
    """Pipelines shipped with Orin: ``src/agentos/pipelines/builtin`` (collected by the packaging as data)."""
    return Path(__file__).with_name("builtin")


def approvals_path() -> Path:
    return orin_user_dir() / APPROVALS_FILENAME


__all__ = [
    "APPROVALS_FILENAME",
    "MAX_FILE_BYTES",
    "SUFFIXES",
    "approvals_path",
    "builtin_dir",
    "project_dir",
    "user_dir",
]
