"""Network policy for every URL the OAuth client reaches on its own.

Discovery documents name further URLs (authorization server, registration,
token and revocation endpoints). Any of them may point anywhere, so each one is
held to the rule the MCP HTTP transport already enforces: public HTTPS only,
with the host resolved and checked right before the call.
"""
from __future__ import annotations

from agentos.agentic.agent_tools import _public_url


class OAuthUrlRefused(RuntimeError):
    """A URL taken from OAuth metadata is not a public https endpoint."""


def public_https(url: str) -> str:
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise OAuthUrlRefused(f"'{url}' is not an https URL")
    try:
        return _public_url(url, resolve_dns=True)
    except Exception as error:  # the policy raises its own refusal type
        raise OAuthUrlRefused(str(error)) from error


__all__ = ["OAuthUrlRefused", "public_https"]
