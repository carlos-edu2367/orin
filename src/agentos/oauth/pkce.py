"""PKCE (RFC 7636) and CSRF-state generation for the OAuth authorization-code flow."""
from __future__ import annotations

import base64
import hashlib
import secrets


def generate_code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def code_challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    return secrets.token_urlsafe(32)


__all__ = ["code_challenge_for", "generate_code_verifier", "generate_state"]
