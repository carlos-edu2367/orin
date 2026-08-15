"""Provider-agnostic OAuth 2.0 authorization-code + PKCE flow.

This module never talks to a specific provider by name: a caller supplies an
``OAuthProviderConfig`` (authorize/token URLs, scopes, client_id) and gets back
a URL to send the user's browser to, plus the machinery to turn the resulting
authorization code into tokens. Nothing here assumes how the redirect is
received — see ``callback_server`` for the loopback listener that plugs in on
the other end — or how tokens are stored — see ``token_store``.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .pkce import code_challenge_for, generate_code_verifier, generate_state

DEFAULT_TIMEOUT_SECONDS = 30.0


class OAuthFlowError(RuntimeError):
    """The flow could not proceed: misconfiguration, a denied consent, or a bad token response."""


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    provider_id: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id: str | None

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
        "scope": " ".join(config.scopes),
        "state": generate_state(),
        "code_challenge": code_challenge_for(verifier),
        "code_challenge_method": "S256",
    }
    return PendingAuthorization(
        provider_id=config.provider_id,
        state=params["state"],
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        authorization_url=f"{config.authorize_url}?{urlencode(params)}",
    )


def _tokens_from_response(response: httpx.Response, *, fallback_refresh_token: str | None = None) -> OAuthTokens:
    if response.status_code >= 400:
        raise OAuthFlowError(f"the token endpoint answered {response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        raise OAuthFlowError("the token endpoint did not answer with JSON") from error
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthFlowError("the token endpoint did not return an access_token")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token", fallback_refresh_token),
        expires_in=payload.get("expires_in"),
        scope=payload.get("scope"),
    )


def exchange_code_for_tokens(
    config: OAuthProviderConfig, pending: PendingAuthorization, *, code: str, client: httpx.Client | None = None,
) -> OAuthTokens:
    owned_client = client is None
    client = client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
    try:
        response = client.post(config.token_url, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": config.client_id,
            "code_verifier": pending.code_verifier,
        }, headers={"accept": "application/json"})
    except httpx.HTTPError as error:
        raise OAuthFlowError(f"the token endpoint did not answer: {error}") from error
    finally:
        if owned_client:
            client.close()
    return _tokens_from_response(response)


def refresh_tokens(config: OAuthProviderConfig, refresh_token: str, *, client: httpx.Client | None = None) -> OAuthTokens:
    owned_client = client is None
    client = client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
    try:
        response = client.post(config.token_url, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": config.client_id,
        }, headers={"accept": "application/json"})
    except httpx.HTTPError as error:
        raise OAuthFlowError(f"the token endpoint did not answer: {error}") from error
    finally:
        if owned_client:
            client.close()
    return _tokens_from_response(response, fallback_refresh_token=refresh_token)


__all__ = [
    "OAuthFlowError", "OAuthProviderConfig", "OAuthTokens", "PendingAuthorization",
    "begin_authorization", "exchange_code_for_tokens", "refresh_tokens",
]
