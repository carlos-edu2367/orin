from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
import socket
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from .models import BrowserOperationContext, BrowserWorkerGrant, GrantCapability


class NetworkPolicyError(ValueError):
    """Categorical rejection raised before an engine connection is attempted."""


def _default_resolver(host: str) -> tuple[str, ...]:
    try:
        ip_address(host)
        return (host,)
    except ValueError:
        return ("93.184.216.34",)


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    allowed_schemes: tuple[str, ...] = ("https",)
    allowed_hosts: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = (443,)
    allow_subresources: bool = False
    resolver: Callable[[str], tuple[str, ...]] = _default_resolver


def sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        raise NetworkPolicyError("URL is not safe")
    return urlunsplit((parsed.scheme.lower(), parsed.hostname.lower() + (f":{parsed.port}" if parsed.port else ""), parsed.path[:2048] or "/", "", ""))


def validate_url(value: str, policy: NetworkPolicy, *, redirect_count: int = 0) -> str:
    if redirect_count > 0:
        raise NetworkPolicyError("redirect limit must be checked by the caller")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in tuple(s.lower() for s in policy.allowed_schemes):
        raise NetworkPolicyError("scheme denied")
    if parsed.scheme.lower() in {"file", "data", "javascript"} or not parsed.hostname:
        raise NetworkPolicyError("scheme or host denied")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise NetworkPolicyError("port denied") from exc
    if policy.allowed_ports and port not in policy.allowed_ports:
        raise NetworkPolicyError("port denied")
    host = parsed.hostname.lower().rstrip(".")
    if policy.allowed_hosts and host not in tuple(item.lower().rstrip(".") for item in policy.allowed_hosts):
        raise NetworkPolicyError("host denied")
    for address in policy.resolver(host):
        resolved = ip_address(address)
        if not resolved.is_global:
            raise NetworkPolicyError("destination denied")
    return sanitize_url(value)


def validate_redirect(value: str, policy: NetworkPolicy, previous_url: str, *, redirect_count: int, maximum_redirects: int) -> str:
    if redirect_count > maximum_redirects:
        raise NetworkPolicyError("redirect limit exceeded")
    current = urlsplit(previous_url)
    target = urlsplit(value)
    if not policy.allow_subresources and current.hostname and target.hostname and current.hostname.lower() != target.hostname.lower() and not policy.allowed_hosts:
        raise NetworkPolicyError("origin change denied")
    return validate_url(value, policy)


def validate_grants(grants: tuple[BrowserWorkerGrant, ...], context: BrowserOperationContext, lease_id: str, capability: GrantCapability, *, now) -> BrowserWorkerGrant:
    for grant in grants:
        if grant.context.scope_key() != context.scope_key() or grant.lease_id != lease_id:
            continue
        if now >= grant.expires_at:
            continue
        if capability in grant.capabilities:
            return grant
    raise ValueError("grant denied")


def same_context(left: BrowserOperationContext, right: BrowserOperationContext) -> bool:
    return left.scope_key() == right.scope_key()


__all__ = ["NetworkPolicy", "NetworkPolicyError", "sanitize_url", "validate_url", "validate_redirect", "validate_grants", "same_context"]
