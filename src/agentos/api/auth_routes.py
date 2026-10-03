"""Sign-in, first-admin setup and profile administration for server mode."""
from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from agentos.accounts.errors import InvalidCredentials, SetupCompleted, SetupRequired
from agentos.accounts.passwords import burn_password_check, generate_temporary_password, validate_password, verify_password
from agentos.accounts.session_security import SESSION_COOKIE, SESSION_TTL
from agentos.accounts.store import UserRecord, normalize_username

from .security import AuthenticatedPrincipal, AuthenticationError, AuthorizationError

_NO_STORE = {"Cache-Control": "no-store"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class SetupRequest(_Model):
    token: str = Field(min_length=1, max_length=256)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    display_name: str | None = Field(default=None, max_length=128)


class LoginRequest(_Model):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChangeRequest(_Model):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class CreateUserRequest(_Model):
    username: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    role: str = Field(default="member", pattern="^(admin|member)$")


class UpdateUserRequest(_Model):
    display_name: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, pattern="^(admin|member)$")
    active: bool | None = None


def register_auth_routes(app: FastAPI, services, principal_for: Callable[..., AuthenticatedPrincipal]) -> None:
    def accounts():
        services.capabilities.require("user_admin")
        if services.accounts is None:
            raise RuntimeError("server mode was composed without account services")
        return services.accounts

    def require_public_origin(request: Request) -> None:
        if not services.public_origin or request.headers.get("origin") != services.public_origin:
            raise AuthorizationError("origin is not allowed")

    def client_ip(request: Request) -> str:
        return request.client.host if request.client is not None else "unknown"

    def session_body(user: UserRecord, csrf: str | None) -> dict[str, object]:
        return {"user": user.public(), "csrf_token": csrf, "capabilities": services.capabilities.as_dict()}

    def with_cookie(response: Response, session_id: str) -> Response:
        response.set_cookie(
            SESSION_COOKIE, session_id, max_age=int(SESSION_TTL.total_seconds()), path="/",
            httponly=True, samesite="lax", secure=(services.public_origin or "").startswith("https://"),
        )
        return response

    def current_user(principal: AuthenticatedPrincipal) -> UserRecord:
        user = accounts().users.get(principal.user_id)
        if user is None:
            raise AuthenticationError("account no longer exists")
        return user

    def csrf_of(principal: AuthenticatedPrincipal) -> str | None:
        return accounts().sessions.csrf_for(principal.session_id) if principal.session_id else None

    @app.get("/v1/auth/me")
    async def me(request: Request) -> JSONResponse:
        bundle = accounts()
        if bundle.users.count() == 0:
            raise SetupRequired("this instance has no account yet")
        principal = principal_for(request, allow_pending_password=True)
        return JSONResponse(session_body(current_user(principal), csrf_of(principal)), headers=_NO_STORE)

    @app.post("/v1/auth/setup", status_code=201)
    async def setup(payload: SetupRequest, request: Request) -> Response:
        bundle = accounts()
        require_public_origin(request)
        if bundle.users.count() > 0:
            raise SetupCompleted("this instance already has an account")
        # Validate before consuming so a typo never burns the one-time token.
        normalize_username(payload.username)
        validate_password(payload.password)
        bundle.setup.consume(payload.token, bundle.users)
        user = bundle.users.create(username=payload.username, password=payload.password, role="admin", display_name=payload.display_name)
        session_id, csrf = bundle.sessions.open_session(user)
        return with_cookie(JSONResponse(session_body(user, csrf), status_code=201, headers=_NO_STORE), session_id)

    @app.post("/v1/auth/login")
    async def login(payload: LoginRequest, request: Request) -> Response:
        bundle = accounts()
        require_public_origin(request)
        if bundle.users.count() == 0:
            raise SetupRequired("this instance has no account yet")
        ip = client_ip(request)
        bundle.guard.check(username=payload.username, ip=ip)
        found = bundle.users.find_for_login(payload.username)
        if found is None:
            burn_password_check(payload.password)
            bundle.guard.record(username=payload.username, ip=ip, succeeded=False)
            raise InvalidCredentials("username or password is incorrect")
        user, encoded = found
        if not verify_password(payload.password, encoded) or not user.active:
            bundle.guard.record(username=payload.username, ip=ip, succeeded=False)
            raise InvalidCredentials("username or password is incorrect")
        bundle.guard.record(username=payload.username, ip=ip, succeeded=True)
        session_id, csrf = bundle.sessions.open_session(user)
        return with_cookie(JSONResponse(session_body(user, csrf), headers=_NO_STORE), session_id)

    @app.post("/v1/auth/logout", status_code=204)
    async def logout(request: Request) -> Response:
        bundle = accounts()
        principal = principal_for(request, mutable=True, allow_pending_password=True)
        if principal.session_id:
            bundle.sessions.close_session(principal.session_id)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.post("/v1/auth/password")
    async def change_password(payload: PasswordChangeRequest, request: Request) -> JSONResponse:
        bundle = accounts()
        principal = principal_for(request, mutable=True, allow_pending_password=True)
        user = current_user(principal)
        ip = client_ip(request)
        bundle.guard.check(username=user.username, ip=ip)
        found = bundle.users.find_for_login(user.username)
        if found is None or not verify_password(payload.current_password, found[1]):
            bundle.guard.record(username=user.username, ip=ip, succeeded=False)
            raise InvalidCredentials("current password is incorrect")
        updated = bundle.users.set_password(user.user_id, payload.new_password, temporary=False)
        bundle.sessions.revoke_user(user.user_id, keep_session_id=principal.session_id)
        return JSONResponse(session_body(updated, csrf_of(principal)), headers=_NO_STORE)

    def admin(request: Request, *, mutable: bool, purpose: str) -> AuthenticatedPrincipal:
        accounts()
        principal = principal_for(request, mutable=mutable)
        services.security.authorize(principal, action="admin.users", resource_id=None, purpose=purpose)
        return principal

    @app.get("/v1/admin/users")
    async def list_users(request: Request) -> JSONResponse:
        admin(request, mutable=False, purpose="admin.users.list")
        return JSONResponse({"items": [item.public() for item in accounts().users.list()]})

    @app.post("/v1/admin/users", status_code=201)
    async def create_user(payload: CreateUserRequest, request: Request) -> JSONResponse:
        admin(request, mutable=True, purpose="admin.users.create")
        temporary = generate_temporary_password()
        user = accounts().users.create(username=payload.username, password=temporary, role=payload.role, must_change_password=True, display_name=payload.display_name)
        return JSONResponse({"user": user.public(), "temporary_password": temporary}, status_code=201, headers=_NO_STORE)

    @app.patch("/v1/admin/users/{user_id}")
    async def update_user(user_id: str, payload: UpdateUserRequest, request: Request) -> JSONResponse:
        admin(request, mutable=True, purpose="admin.users.update")
        bundle = accounts()
        user = bundle.users.update(user_id, display_name=payload.display_name, role=payload.role, active=payload.active)
        if payload.active is False:
            bundle.sessions.revoke_user(user_id)
        return JSONResponse({"user": user.public()})

    @app.post("/v1/admin/users/{user_id}/reset-password")
    async def reset_password(user_id: str, request: Request) -> JSONResponse:
        admin(request, mutable=True, purpose="admin.users.reset_password")
        bundle = accounts()
        temporary = generate_temporary_password()
        user = bundle.users.set_password(user_id, temporary, temporary=True)
        bundle.sessions.revoke_user(user_id)
        return JSONResponse({"user": user.public(), "temporary_password": temporary}, headers=_NO_STORE)


__all__ = ["register_auth_routes"]
