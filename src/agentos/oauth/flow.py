"""Provider-agnostic OAuth 2.0 authorization-code + PKCE flow.

This module never talks to a specific provider by name: a caller supplies an
``OAuthProviderConfig`` (endpoints, scopes, client credentials, the protected
resource) and gets back a URL to send the user's browser to, plus the machinery
to turn the resulting authorization code into tokens, refresh them and revoke
them. Every token-endpoint call passes the OAuth network policy first.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .netpolicy import OAuthUrlRefused, public_https
from .pkce import code_challenge_for, generate_code_verifier, generate_state

DEFAULT_TIMEOUT_SECONDS = 30.0
_REJECTION_CODES = frozenset({"invalid_grant", "invalid_client", "unauthorized_client"})


class OAuthFlowError(RuntimeError):
    """The flow could not proceed: misconfiguration, a denied consent, or a bad token response."""


class OAuthGrantRejected(OAuthFlowError):
    """The server refused the grant itself (revoked, expired or unknown); only a new sign-in helps."""


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    provider_id: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id: str | None
    resource: str | None = None
    client_secret: str | None = None
    token_endpoint_auth_method: str = "none"

    def __post_init__(self) -> None:
        if not self.authorize_url.lower().startswith("https://"):
            raise ValueError("authorize_url must be https")
        if not self.token_url.lower().startswith("https://"):
            raise ValueError("token_url must be https")

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id)


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    provider_id: str
    state: str
    code_verifier: str
    redirect_uri: str
    authorization_url: str


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scope: str | None


def begin_authorization(config: OAuthProviderConfig, *, redirect_uri: str) -> PendingAuthorization:
    if not config.is_configured:
        raise OAuthFlowError(f"OAuth provider '{config.provider_id}' has no client_id configured")
    if not (redirect_uri.startswith("http://127.0.0.1:") or redirect_uri.startswith("http://localhost:")):
        raise OAuthFlowError("the redirect_uri must be a loopback address")
    verifier = generate_code_verifier()
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "state": generate_state(),
        "code_challenge": code_challenge_for(verifier),
        "code_challenge_method": "S256",
    }
    if config.scopes:
        params["scope"] = " ".join(config.scopes)
    if config.resource:
        params["resource"] = config.resource
    return PendingAuthorization(
        provider_id=config.provider_id,
        state=params["state"],
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        authorization_url=f"{config.authorize_url}?{urlencode(params)}",
    )


def _tokens_from_response(response: httpx.Response, *, fallback_refresh_token: str | None = None) -> OAuthTokens:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code >= 400:
        code = payload.get("error") if isinstance(payload, dict) else None
        if code in _REJECTION_CODES:
            raise OAuthGrantRejected(f"the token endpoint rejected the grant ({code})")
        raise OAuthFlowError(f"the token endpoint answered {response.status_code}")
    if not isinstance(payload, dict):
        raise OAuthFlowError("the token endpoint did not answer with a JSON object")
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthFlowError("the token endpoint did not return an access_token")
    expires_in = payload.get("expires_in")
    refresh_token = payload.get("refresh_token")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else fallback_refresh_token,
        expires_in=int(expires_in) if isinstance(expires_in, (int, float)) and expires_in > 0 else None,
        scope=payload.get("scope") if isinstance(payload.get("scope"), str) else None,
    )


def _post_form(config: OAuthProviderConfig, url: str, data: dict[str, str], client: httpx.Client | None) -> httpx.Response:
    try:
        checked = public_https(url)
    except OAuthUrlRefused as error:
        raise OAuthFlowError(str(error)) from error
    body = dict(data)
    auth: httpx.Auth | None = None
    if config.token_endpoint_auth_method == "client_secret_basic" and config.client_secret:
        auth = httpx.BasicAuth(config.client_id or "", config.client_secret)
    else:
        body["client_id"] = config.client_id or ""
        if config.token_endpoint_auth_method == "client_secret_post" and config.client_secret:
            body["client_secret"] = config.client_secret
    owned_client = client is None
    client = client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
    try:
        return client.post(checked, data=body, headers={"accept": "application/json"}, auth=auth)
    except httpx.HTTPError as error:
        raise OAuthFlowError(f"the endpoint did not answer: {error}") from error
    finally:
        if owned_client:
            client.close()


def _with_resource(config: OAuthProviderConfig, data: dict[str, str]) -> dict[str, str]:
    return {**data, "resource": config.resource} if config.resource else data


def exchange_code_for_tokens(
    config: OAuthProviderConfig, pending: PendingAuthorization, *, code: str, client: httpx.Client | None = None,
) -> OAuthTokens:
    response = _post_form(config, config.token_url, _with_resource(config, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending.redirect_uri,
        "code_verifier": pending.code_verifier,
    }), client)
    return _tokens_from_response(response)


def refresh_tokens(config: OAuthProviderConfig, refresh_token: str, *, client: httpx.Client | None = None) -> OAuthTokens:
    response = _post_form(config, config.token_url, _with_resource(config, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }), client)
    return _tokens_from_response(response, fallback_refresh_token=refresh_token)


def revoke_token(
    config: OAuthProviderConfig, *, revocation_url: str, token: str, token_type_hint: str,
    client: httpx.Client | None = None,
) -> None:
    response = _post_form(config, revocation_url, {"token": token, "token_type_hint": token_type_hint}, client)
    if response.status_code >= 400:
        raise OAuthFlowError(f"the revocation endpoint answered {response.status_code}")


__all__ = [
    "OAuthFlowError", "OAuthGrantRejected", "OAuthProviderConfig", "OAuthTokens", "PendingAuthorization",
    "begin_authorization", "exchange_code_for_tokens", "refresh_tokens", "revoke_token",
]
