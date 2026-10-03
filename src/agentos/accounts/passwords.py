"""Password hashing with the standard library's scrypt.

No new dependency: the frozen PyInstaller build stays as it is. Parameters
travel inside each stored hash so the default can be raised later without
invalidating existing accounts.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from functools import lru_cache
from hmac import compare_digest

from .errors import WeakPassword

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256
_N, _R, _P = 2**15, 8, 1
_SALT_BYTES, _KEY_BYTES = 16, 32
# 128 * r * n bytes is what scrypt needs; the stdlib default (32 MiB) is just
# under that for n=2**15, r=8.
_MAXMEM = 64 * 1024 * 1024


def validate_password(password: str) -> None:
    if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise WeakPassword(f"password must have between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _derive(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=_KEY_BYTES)


def _hash_with(password: str, *, n: int, r: int, p: int) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(_derive(password, salt, n=n, r=r, p=p))}"


def hash_password(password: str) -> str:
    return _hash_with(password, n=_N, r=_R, p=_P)


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        derived = _derive(password, base64.b64decode(salt, validate=True), n=int(n), r=int(r), p=int(p))
        return compare_digest(derived, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(24))


def burn_password_check(password: str) -> None:
    """Spend one real verification so an unknown username costs the same time as a wrong password."""
    verify_password(password, _dummy_hash())


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(12)
