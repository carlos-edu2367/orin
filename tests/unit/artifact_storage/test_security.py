import pytest

from agentos.artifact_storage.security import derive_namespace, sanitize_logical_name, sanitize_public_reason


def test_namespace_is_derived_from_ownership_and_category_not_name():
    first = derive_namespace("user:1", "workspace:1", "RESULT")
    second = derive_namespace("user:1", "workspace:1", "RESULT")

    assert first == second
    assert "report" not in str(first)
    assert "workspace:1" not in str(first)


@pytest.mark.parametrize("name", ["../secret", "..\\secret", "/etc/passwd", "token=abc", "password.txt", ""]) 
def test_logical_name_rejects_traversal_and_secret_like_values(name):
    with pytest.raises(ValueError):
        sanitize_logical_name(name)


def test_public_reason_is_bounded_and_redacted():
    reason = sanitize_public_reason("cleanup failed for password=secret and /private/path")

    assert len(reason) <= 128
    assert "secret" not in reason
    assert "/private/path" not in reason
