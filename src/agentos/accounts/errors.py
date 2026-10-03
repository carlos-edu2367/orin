"""Account failures, each already knowing its public HTTP shape.

The gateway maps every ``AccountError`` with one handler, so adding a failure
never means remembering to register another exception handler.
"""
from __future__ import annotations


class AccountError(Exception):
    status: int = 400
    code: str = "invalid_request"
    category: str = "VALIDATION"
    retryable: bool = False

    def __init__(self, message: str = "", *, retry_after: int | None = None) -> None:
        super().__init__(message or self.code)
        self.retry_after = retry_after


class InvalidCredentials(AccountError):
    status, code, category = 401, "invalid_credentials", "AUTHENTICATION"


class LoginLocked(AccountError):
    status, code, category, retryable = 429, "login_locked", "RATE_LIMITED", True

    def __init__(self, *, retry_after: int) -> None:
        super().__init__("too many failed sign-in attempts", retry_after=retry_after)


class SetupRequired(AccountError):
    status, code, category = 409, "setup_required", "CONFLICT"


class SetupCompleted(AccountError):
    status, code, category = 409, "setup_completed", "CONFLICT"


class InvalidSetupToken(AccountError):
    status, code, category = 401, "invalid_setup_token", "AUTHENTICATION"


class UsernameTaken(AccountError):
    status, code, category = 409, "username_taken", "CONFLICT"


class InvalidUsername(AccountError):
    status, code, category = 422, "invalid_username", "VALIDATION"


class WeakPassword(AccountError):
    status, code, category = 422, "weak_password", "VALIDATION"


class LastAdmin(AccountError):
    status, code, category = 409, "last_admin", "CONFLICT"


class UserNotFound(AccountError):
    status, code, category = 404, "resource_not_found", "NOT_FOUND"


__all__ = [
    "AccountError", "InvalidCredentials", "InvalidSetupToken", "InvalidUsername", "LastAdmin",
    "LoginLocked", "SetupCompleted", "SetupRequired", "UserNotFound", "UsernameTaken", "WeakPassword",
]
