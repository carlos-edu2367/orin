import pytest

from agentos.browser.conversation_worker import _policy_for
from agentos.browser.security import NetworkPolicyError, validate_url


def test_local_mode_keeps_loopback_for_dev_servers(monkeypatch):
    monkeypatch.delenv("ORIN_MODE", raising=False)
    assert _policy_for("full").allow_loopback is True


def test_server_mode_blocks_loopback_at_every_level(monkeypatch):
    monkeypatch.setenv("ORIN_MODE", "server")
    for capability in ("interact", "full"):
        policy = _policy_for(capability)
        assert policy.allow_loopback is False
        with pytest.raises(NetworkPolicyError):
            validate_url("http://127.0.0.1:49200/v1/auth/me", policy)
