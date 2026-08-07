from __future__ import annotations

import pytest

from agentos.filesystem.models import WorkspacePath
from agentos.filesystem.security import validate_path_text


@pytest.mark.parametrize(
    "value",
    [
        "C:\\temp\\file.txt",
        "/etc/passwd",
        "\\\\server\\share\\file",
        "https://host/file",
        "\\\\.\\PhysicalDrive0",
        "~/secret",
        "$env:USERPROFILE/file",
        "file.txt:secret",
        "a//b",
        "a/./b",
        "a/../b",
        "a\\b",
        "",
        "a\x00b",
    ],
)
def test_path_parser_rejects_physical_traversal_and_ambiguous_forms(value: str) -> None:
    with pytest.raises(ValueError):
        validate_path_text(value)


def test_path_parser_rejects_non_normalized_unicode_and_preserves_safe_segments() -> None:
    with pytest.raises(ValueError):
        WorkspacePath.from_string("caf\u0065\u0301.txt")
    assert validate_path_text("docs/report.txt") == WorkspacePath.from_segments("docs", "report.txt")
