from __future__ import annotations

from dataclasses import dataclass

from .login_guard import LoginGuard
from .session_security import SessionSecurityService
from .setup import SetupTokens
from .store import UserStore


@dataclass(frozen=True, slots=True)
class AccountServices:
    """Everything the auth and admin routes need, composed once for server mode."""

    users: UserStore
    setup: SetupTokens
    guard: LoginGuard
    sessions: SessionSecurityService


__all__ = ["AccountServices"]
