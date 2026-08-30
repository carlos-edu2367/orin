from __future__ import annotations

import pytest
import socket

from agentos.browser.security import NetworkPolicy, NetworkPolicyError, navigable_url, sanitize_url, validate_redirect, validate_url


def test_network_is_denied_by_default_and_rejects_unsafe_schemes_and_hosts() -> None:
    policy = NetworkPolicy()
    for url in ("file:///etc/passwd", "data:text/html,secret", "http://127.0.0.1/", "https://169.254.169.254/latest"):
        with pytest.raises(NetworkPolicyError):
            validate_url(url, policy)


def test_loopback_requires_an_explicit_policy_opt_in() -> None:
    policy = NetworkPolicy(
        allow_loopback=True,
        resolver=lambda host: ("127.0.0.1",) if host == "localhost" else ("192.168.1.7",),
    )

    assert validate_url("http://localhost:5173/", policy) == "http://localhost:5173/"
    with pytest.raises(NetworkPolicyError):
        validate_url("http://lan.example/", policy)


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


def test_navigable_url_keeps_the_query_and_fragment_but_drops_credentials() -> None:
    navigated = navigable_url("https://user:password@example.com/search?q=neectify+food#top")
    assert navigated == "https://example.com/search?q=neectify+food#top"
    assert "password" not in navigated


def test_validate_url_returns_a_navigable_url_not_a_display_sanitized_one() -> None:
    policy = NetworkPolicy(allowed_hosts=("example.com",))
    result = validate_url("https://example.com/search?q=neectify+food", policy)
    assert result == "https://example.com/search?q=neectify+food"


def test_dns_rebinding_is_revalidated_for_each_hop() -> None:
    policy = NetworkPolicy(allowed_hosts=("example.com",), resolver=lambda host: ("93.184.216.34",))
    validate_url("https://example.com", policy)
    rebinding = NetworkPolicy(allowed_hosts=("example.com",), resolver=lambda host: ("127.0.0.1",))
    with pytest.raises(NetworkPolicyError):
        validate_url("https://example.com", rebinding)


def test_default_dns_policy_rejects_a_hostname_resolving_to_private_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )

    with pytest.raises(NetworkPolicyError):
        validate_url("https://private.example", NetworkPolicy(allowed_hosts=("private.example",)))


def test_default_dns_policy_fails_closed_when_hostname_cannot_be_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_resolution(*_args, **_kwargs):
        raise socket.gaierror("no answer")

    monkeypatch.setattr(socket, "getaddrinfo", fail_resolution)

    with pytest.raises(NetworkPolicyError):
        validate_url("https://unresolved.example", NetworkPolicy(allowed_hosts=("unresolved.example",)))


def test_redirect_limit_and_origin_policy_are_enforced() -> None:
    policy = NetworkPolicy(allowed_hosts=("example.com",))
    with pytest.raises(NetworkPolicyError):
        validate_redirect("https://example.com/next", policy, "https://example.com/start", redirect_count=3, maximum_redirects=2)
    with pytest.raises(NetworkPolicyError):
        validate_redirect("https://other.example/next", policy, "https://example.com/start", redirect_count=1, maximum_redirects=2)
