"""W7 (W0-R11): secrets never leave through a sink."""
from __future__ import annotations

import pytest

from agentos.redaction import REDACTED, redact_text, redact_value


@pytest.mark.parametrize("text, secret", [
    ("GITHUB_TOKEN=ghp_0123456789abcdefghijABCD curl https://api.github.com", "ghp_0123456789abcdefghijABCD"),
    ("export DB_PASSWORD='hunter2 two' && run", "hunter2 two"),
    ('{"api_key": "abc123xyz"}', "abc123xyz"),
    ("curl -H 'Authorization: Bearer abcdefghijklmnop' x", "abcdefghijklmnop"),
    ("mysql --password=s3cr3t -u root", "s3cr3t"),
    ("git clone https://carlos:tok3nvalue@github.com/x/y", "tok3nvalue"),
    ("key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV in the text", "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV"),
    ("AWS AKIAABCDEFGHIJKLMNOP here", "AKIAABCDEFGHIJKLMNOP"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "MIIabc"),
    ("jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4", "SflKxwRJSMeKKF2QT4"),
])
def test_secrets_are_redacted(text: str, secret: str) -> None:
    redacted = redact_text(text)
    assert secret not in redacted and REDACTED in redacted


@pytest.mark.parametrize("text", ["git status", "ls -la src/agentos", "pytest -q tests/unit", "echo hello world", "path=src/a.py"])
def test_ordinary_text_is_kept(text: str) -> None:
    assert redact_text(text) == text


def test_mappings_hide_secret_keys_env_and_headers() -> None:
    value = {
        "command": "TOKEN=abcdefgh123 make",
        "env": {"PATH": "/usr/bin", "ANYTHING": "visible?"},
        "headers": {"X-Custom": "v"},
        "nested": [{"client_secret": "s"}, {"name": "kept"}],
        "enabled": True,
    }
    result = redact_value(value)
    assert "abcdefgh123" not in result["command"]
    assert result["env"] == {"PATH": REDACTED, "ANYTHING": REDACTED}
    assert result["headers"] == {"X-Custom": REDACTED}
    assert result["nested"] == [{"client_secret": REDACTED}, {"name": "kept"}]
    assert result["enabled"] is True and value["env"]["PATH"] == "/usr/bin"  # the input is not changed


def test_too_deep_is_redacted() -> None:
    deep: dict = {}
    current = deep
    for _ in range(40):
        current["x"] = {}
        current = current["x"]
    current["y"] = "z"
    assert REDACTED in repr(redact_value(deep))
