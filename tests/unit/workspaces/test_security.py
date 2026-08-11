from __future__ import annotations

import pytest

from agentos.workspaces.security import (
    reject_physical_root_input,
    sanitize_display_name,
    sanitize_public_reason,
    validate_actor_binding,
    validate_logical_path,
)


@pytest.mark.parametrize("value", ["", "..", "C:\\temp", r"\\server\share", "https://example", "/tmp/root", "\\\\?\\C:\\x"])
def test_rejects_physical_roots_and_path_like_identity(value: str) -> None:
    with pytest.raises(ValueError):
        reject_physical_root_input(value)


@pytest.mark.parametrize("value", ["", ".", "..", "a/b", "a\\b", "a\x00b", "C:"])
def test_logical_paths_are_relative_and_single_segmented(value: str) -> None:
    with pytest.raises(ValueError):
        validate_logical_path(value)


def test_security_helpers_bound_display_name_reason_and_actor() -> None:
    assert sanitize_display_name(" Project ") == "Project"
    assert sanitize_public_reason("secret-token=abc path=C:\\private") == "secret-token=<redacted> path=<redacted>"
    validate_actor_binding("user-1", "user:user-1", "agent-1")
    with pytest.raises(PermissionError):
        validate_actor_binding("user-1", "user:other", "agent-1")
