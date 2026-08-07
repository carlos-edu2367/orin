from __future__ import annotations

import pytest

from agentos.browser.security import NetworkPolicy, NetworkPolicyError, sanitize_url, validate_redirect, validate_url


def test_network_is_denied_by_default_and_rejects_unsafe_schemes_and_hosts() -> None:
    policy = NetworkPolicy()
    for url in ("file:///etc/passwd", "data:text/html,secret", "http://127.0.0.1/", "https://169.254.169.254/latest"):
        with pytest.raises(NetworkPolicyError):
            validate_url(url, policy)


def test_allowlist_and_port_are_enforced() -> None:
    policy = NetworkPolicy(allowed_hosts=("example.com",), allowed_schemes=("https",), allowed_ports=(443,))
    validate_url("https://example.com/path", policy)
    with pytest.raises(NetworkPolicyError):
        validate_url("https://other.example/path", policy)
    with pytest.raises(NetworkPolicyError):
        validate_url("https://example.com:8443/path", policy)


def test_sanitization_removes_credentials_query_and_fragment() -> None:
    sanitized = sanitize_url("https://user:password@example.com/a?token=secret#fragment")
    assert sanitized == "https://example.com/a"
    assert "password" not in sanitized and "token" not in sanitized


def test_dns_rebinding_is_revalidated_for_each_hop() -> None:
    policy = NetworkPolicy(allowed_hosts=("example.com",), resolver=lambda host: ("93.184.216.34",))
    validate_url("https://example.com", policy)
    rebinding = NetworkPolicy(allowed_hosts=("example.com",), resolver=lambda host: ("127.0.0.1",))
    with pytest.raises(NetworkPolicyError):
        validate_url("https://example.com", rebinding)


def test_redirect_limit_and_origin_policy_are_enforced() -> None:
    policy = NetworkPolicy(allowed_hosts=("example.com",))
    with pytest.raises(NetworkPolicyError):
        validate_redirect("https://example.com/next", policy, "https://example.com/start", redirect_count=3, maximum_redirects=2)
    with pytest.raises(NetworkPolicyError):
        validate_redirect("https://other.example/next", policy, "https://example.com/start", redirect_count=1, maximum_redirects=2)
