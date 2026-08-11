"""Field-level encryption for provider credentials.

The database stores only ``enc:v1:...`` values. Deployments must provide a
stable ``AGENTOS_PROVIDER_ENCRYPTION_KEY`` (or ``APP_MASTER_KEY``) so a worker
restart can decrypt the credential; callers never receive the plaintext.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

_EPHEMERAL_KEY = secrets.token_urlsafe(32)


class ProviderSecretCipher:
    def __init__(self, key: str | bytes | None = None, *, allow_ephemeral: bool = True) -> None:
        raw = key or os.getenv("AGENTOS_PROVIDER_ENCRYPTION_KEY") or os.getenv("APP_MASTER_KEY")
        if raw is None:
            if not allow_ephemeral:
                raise ValueError("AGENTOS_PROVIDER_ENCRYPTION_KEY is required")
            raw = _EPHEMERAL_KEY
        if isinstance(raw, str):
            raw = raw.encode()
        try:
            if len(raw) != 44:
                raw = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
            self._fernet = Fernet(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("provider encryption key is invalid") from exc

    @classmethod
    def from_environment(cls, *, required: bool = False) -> "ProviderSecretCipher":
        return cls(allow_ephemeral=not required)

    def encrypt(self, value: str, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str) or len(value) > 4096 or (not allow_empty and not value.strip()):
            raise ValueError("provider credential is invalid")
        return "enc:v1:" + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        if not isinstance(value, str) or not value.startswith("enc:v1:"):
            raise ValueError("legacy plaintext provider credential requires rotation")
        try:
            return self._fernet.decrypt(value[7:].encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ValueError("provider credential cannot be decrypted") from exc


__all__ = ["ProviderSecretCipher"]
