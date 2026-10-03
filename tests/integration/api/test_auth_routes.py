import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agentos.accounts.login_guard import LoginGuard
from agentos.accounts.services import AccountServices
from agentos.accounts.session_security import SessionSecurityService, derive_csrf_secret
from agentos.accounts.setup import SetupTokens
from agentos.accounts.store import UserStore
from agentos.api import ApiServices, create_app
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.persistence.postgres.schema import metadata

ORIGIN = "https://orin.test"
SECURE = {"Origin": ORIGIN}


@pytest.fixture()
def instance(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    users = UserStore(engine)
    sessions = SessionSecurityService(engine, users=users, public_origin=ORIGIN, csrf_secret=derive_csrf_secret("k" * 44))
    accounts = AccountServices(users=users, setup=SetupTokens(engine), guard=LoginGuard(engine), sessions=sessions)
    services = ApiServices(security=sessions, accounts=accounts, capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER), public_origin=ORIGIN)
    app = create_app(services)

    def client() -> TestClient:
        return TestClient(app, base_url=ORIGIN)

    return accounts, client


def _setup_admin(accounts, api, *, username="carla", password="a long password"):
    token = accounts.setup.issue_if_needed(accounts.users)
    response = api.post("/v1/auth/setup", json={"token": token, "username": username, "password": password}, headers=SECURE)
    assert response.status_code == 201, response.text
    return response.json()["csrf_token"]


def _login(api, username, password):
    return api.post("/v1/auth/login", json={"username": username, "password": password}, headers=SECURE)


def test_a_fresh_instance_asks_for_setup_and_refuses_a_wrong_token(instance):
    accounts, client = instance
    api = client()
    assert api.get("/v1/auth/me").json()["error"]["code"] == "setup_required"
    accounts.setup.issue_if_needed(accounts.users)
    wrong = api.post("/v1/auth/setup", json={"token": "nope", "username": "carla", "password": "a long password"}, headers=SECURE)
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "invalid_setup_token")
    assert _login(api, "carla", "a long password").json()["error"]["code"] == "setup_required"


def test_setup_creates_the_admin_signs_in_and_cannot_run_twice(instance):
    accounts, client = instance
    api = client()
    _setup_admin(accounts, api)
    me = api.get("/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["role"] == "admin" and body["user"]["user_id"] == "local-user"
    assert body["capabilities"]["user_admin"] is True and body["capabilities"]["shell"] is False
    again = api.post("/v1/auth/setup", json={"token": "x", "username": "eve", "password": "a long password"}, headers=SECURE)
    assert again.json()["error"]["code"] == "setup_completed"


def test_a_weak_password_does_not_burn_the_setup_token(instance):
    accounts, client = instance
    api = client()
    token = accounts.setup.issue_if_needed(accounts.users)
    weak = api.post("/v1/auth/setup", json={"token": token, "username": "carla", "password": "short"}, headers=SECURE)
    assert weak.json()["error"]["code"] == "weak_password"
    ok = api.post("/v1/auth/setup", json={"token": token, "username": "carla", "password": "a long password"}, headers=SECURE)
    assert ok.status_code == 201


def test_login_sets_a_hardened_cookie_and_failures_look_identical(instance):
    accounts, client = instance
    _setup_admin(accounts, client())
    api = client()
    unknown = _login(api, "nobody", "a long password")
    wrong = _login(api, "carla", "wrong password!")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"] == "invalid_credentials"
    ok = _login(api, "Carla", "a long password")
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"].lower()
    assert "agentos_session=" in cookie and "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert ok.headers["cache-control"] == "no-store"


def test_anonymous_routes_require_the_public_origin(instance):
    accounts, client = instance
    _setup_admin(accounts, client())
    api = client()
    assert api.post("/v1/auth/login", json={"username": "carla", "password": "a long password"}).status_code == 403
    assert api.post("/v1/auth/login", json={"username": "carla", "password": "a long password"}, headers={"Origin": "https://evil.test"}).status_code == 403


def test_me_returns_the_same_csrf_for_the_same_session(instance):
    accounts, client = instance
    api = client()
    csrf = _setup_admin(accounts, api)
    assert api.get("/v1/auth/me").json()["csrf_token"] == csrf == api.get("/v1/auth/me").json()["csrf_token"]


def test_mutations_need_the_csrf_header(instance):
    accounts, client = instance
    api = client()
    csrf = _setup_admin(accounts, api)
    blocked = api.post("/v1/admin/users", json={"username": "bruno"}, headers=SECURE)
    assert blocked.status_code == 403
    created = api.post("/v1/admin/users", json={"username": "bruno"}, headers={**SECURE, "X-CSRF-Token": csrf})
    assert created.status_code == 201, created.text


def test_repeated_failures_lock_the_login(instance):
    accounts, client = instance
    _setup_admin(accounts, client())
    api = client()
    for _ in range(5):
        _login(api, "carla", "wrong password!")
    locked = _login(api, "carla", "a long password")
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "login_locked"
    assert int(locked.headers["retry-after"]) > 0


def test_a_new_member_must_change_the_temporary_password_and_is_not_an_admin(instance):
    accounts, client = instance
    admin = client()
    csrf = _setup_admin(accounts, admin)
    created = admin.post("/v1/admin/users", json={"username": "bruno", "display_name": "Bruno"}, headers={**SECURE, "X-CSRF-Token": csrf}).json()
    temporary = created["temporary_password"]
    assert created["user"]["must_change_password"] is True

    member = client()
    signed_in = _login(member, "bruno", temporary).json()
    assert signed_in["user"]["must_change_password"] is True
    pending = member.get("/v1/admin/users")
    assert (pending.status_code, pending.json()["error"]["code"]) == (403, "password_change_required")

    changed = member.post("/v1/auth/password", json={"current_password": temporary, "new_password": "bruno's own password"}, headers={**SECURE, "X-CSRF-Token": signed_in["csrf_token"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["user"]["must_change_password"] is False
    denied = member.get("/v1/admin/users")
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "admin_required")


def test_changing_the_password_needs_the_current_one(instance):
    accounts, client = instance
    api = client()
    csrf = _setup_admin(accounts, api)
    wrong = api.post("/v1/auth/password", json={"current_password": "not it at all", "new_password": "another long one"}, headers={**SECURE, "X-CSRF-Token": csrf})
    assert wrong.json()["error"]["code"] == "invalid_credentials"


def test_deactivating_a_member_ends_their_session(instance):
    accounts, client = instance
    admin = client()
    csrf = _setup_admin(accounts, admin)
    created = admin.post("/v1/admin/users", json={"username": "bruno"}, headers={**SECURE, "X-CSRF-Token": csrf}).json()
    member = client()
    _login(member, "bruno", created["temporary_password"])
    assert member.get("/v1/auth/me").status_code == 200
    patched = admin.patch(f"/v1/admin/users/{created['user']['user_id']}", json={"active": False}, headers={**SECURE, "X-CSRF-Token": csrf})
    assert patched.json()["user"]["active"] is False
    assert member.get("/v1/auth/me").status_code == 401
    assert _login(client(), "bruno", created["temporary_password"]).json()["error"]["code"] == "invalid_credentials"


def test_reset_password_returns_a_new_temporary_password(instance):
    accounts, client = instance
    admin = client()
    csrf = _setup_admin(accounts, admin)
    created = admin.post("/v1/admin/users", json={"username": "bruno"}, headers={**SECURE, "X-CSRF-Token": csrf}).json()
    reset = admin.post(f"/v1/admin/users/{created['user']['user_id']}/reset-password", headers={**SECURE, "X-CSRF-Token": csrf}).json()
    assert reset["temporary_password"] != created["temporary_password"]
    assert _login(client(), "bruno", reset["temporary_password"]).status_code == 200


def test_the_last_admin_cannot_be_demoted_through_the_api(instance):
    accounts, client = instance
    admin = client()
    csrf = _setup_admin(accounts, admin)
    response = admin.patch("/v1/admin/users/local-user", json={"role": "member"}, headers={**SECURE, "X-CSRF-Token": csrf})
    assert (response.status_code, response.json()["error"]["code"]) == (409, "last_admin")


def test_logout_ends_the_session(instance):
    accounts, client = instance
    api = client()
    csrf = _setup_admin(accounts, api)
    assert api.post("/v1/auth/logout", headers={**SECURE, "X-CSRF-Token": csrf}).status_code == 204
    assert api.get("/v1/auth/me").status_code == 401


def test_auth_routes_do_not_exist_in_local_mode():
    app = create_app(ApiServices())
    response = TestClient(app).get("/v1/auth/me")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "capability_unavailable")
