from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from hmac import compare_digest
from time import monotonic


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: str
    credential_ref: str
    scopes: frozenset[str]
    credential_kind: str = "pat"
    revoked: bool = False
    session_id: str | None = field(default=None, repr=False)


@dataclass(slots=True)
class _Session:
    principal: AuthenticatedPrincipal
    csrf_digest: str
    revoked: bool = False


class AuthenticationError(PermissionError):
    ...


class AuthorizationError(PermissionError):
    ...


class RateLimitError(RuntimeError):
    ...


class InMemorySecurityService:
    """A test adapter for security ports; production composes server-side stores.

    PATs are stored as hashes, never as reusable raw tokens. Session state is
    server-side and cookie handling is intentionally left to the gateway.
    """

    def __init__(self, *, maximum_requests: int = 120) -> None:
        self._pats: dict[str, AuthenticatedPrincipal] = {}
        self._sessions: dict[str, _Session] = {}
        self._epochs: dict[str, int] = {}
        self._limits: dict[tuple[str, str], list[float]] = {}
        self._maximum_requests = maximum_requests

    def add_pat(self, token: str, principal: AuthenticatedPrincipal) -> None:
        self._pats[self._digest(token)] = principal

    def add_session(self, session_id: str, principal: AuthenticatedPrincipal, *, csrf_token: str) -> None:
        self._sessions[session_id] = _Session(principal, self._digest(csrf_token))

    def revoke(self, credential_ref: str) -> None:
        for digest, principal in tuple(self._pats.items()):
            if principal.credential_ref == credential_ref:
                self._pats[digest] = AuthenticatedPrincipal(principal.user_id, principal.credential_ref, principal.scopes, principal.credential_kind, True)
        for session in self._sessions.values():
            if session.principal.credential_ref == credential_ref:
                session.revoked = True
        self._epochs[credential_ref] = self._epochs.get(credential_ref, 0) + 1

    def authenticate(self, *, bearer_token: str | None, session_id: str | None) -> AuthenticatedPrincipal:
        if bearer_token:
            principal = self._pats.get(self._digest(bearer_token))
            if principal is None or principal.revoked:
                raise AuthenticationError("credential is invalid")
            return principal
        if session_id:
            session = self._sessions.get(session_id)
            if session is None or session.revoked or session.principal.revoked:
                raise AuthenticationError("session is invalid")
            return AuthenticatedPrincipal(
                session.principal.user_id,
                session.principal.credential_ref,
                session.principal.scopes,
                "session",
                session_id=session_id,
            )
        raise AuthenticationError("credential is required")

    def validate_csrf(self, principal: AuthenticatedPrincipal, token: str | None, origin: str | None) -> None:
        if principal.credential_kind != "session":
            return
        if not token or not origin:
            raise AuthorizationError("csrf validation failed")
        session = self._sessions.get(principal.session_id or "")
        if (
            session is None
            or session.revoked
            or session.principal.revoked
            or session.principal.user_id != principal.user_id
            or session.principal.credential_ref != principal.credential_ref
            or not compare_digest(session.csrf_digest, self._digest(token))
        ):
            raise AuthorizationError("csrf validation failed")

    def authorize(self, principal: AuthenticatedPrincipal, *, action: str, resource_id: str | None, purpose: str) -> None:
        if principal.revoked or "api" not in principal.scopes or not purpose.strip():
            raise AuthorizationError("action is not authorized")

    def check_rate_limit(self, principal: AuthenticatedPrincipal, *, action: str, origin: str | None) -> None:
        now = monotonic()
        key = (principal.credential_ref, action)
        entries = [instant for instant in self._limits.get(key, []) if now - instant < 60]
        if len(entries) >= self._maximum_requests:
            raise RateLimitError("rate limit exceeded")
        entries.append(now)
        self._limits[key] = entries

    def revocation_epoch(self, principal: AuthenticatedPrincipal) -> int:
        return self._epochs.get(principal.credential_ref, 0)

    @staticmethod
    def _digest(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()


class LoopbackSecurityService(InMemorySecurityService):
    """Personal-installation security adapter for requests gated by the gateway.

    This adapter deliberately has no knowledge of network addresses: the HTTP
    gateway is the only boundary that can verify the peer address.  It marks
    itself so the gateway can require a literal IPv4/IPv6 loopback client before
    this fixed local principal is returned.  It must never be composed for a
    VPS or another network-facing deployment.
    """

    requires_loopback_client = True

    def __init__(self, *, maximum_requests: int = 120) -> None:
        super().__init__(maximum_requests=maximum_requests)
        self._principal = AuthenticatedPrincipal(
            user_id="local-user",
            credential_ref="loopback-local",
            scopes=frozenset({"api"}),
            credential_kind="loopback",
        )

    def authenticate(self, *, bearer_token: str | None, session_id: str | None) -> AuthenticatedPrincipal:
        # Mixed credentials are an operational error in local mode.  Accepting
        # them would hide a mistaken host/IdP configuration and weaken the
        # eventual VPS migration path.
        if bearer_token or session_id:
            raise AuthenticationError("loopback mode does not accept credentials")
        return self._principal
