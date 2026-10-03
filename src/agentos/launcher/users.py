"""``orin user`` — manage server profiles from the terminal (rescue path)."""
from __future__ import annotations

import getpass
import os

from sqlalchemy.engine import Engine

from agentos.accounts.errors import AccountError, UserNotFound
from agentos.accounts.passwords import generate_temporary_password
from agentos.accounts.session_security import revoke_user_sessions
from agentos.accounts.store import UserRecord, UserStore
from agentos.installation import OrinPaths
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.sqlite import create_local_engine, sqlite_url

from .ui import Console


def create_user(engine: Engine, *, username: str, password: str, admin: bool, display_name: str | None = None) -> UserRecord:
    return UserStore(engine).create(username=username, password=password, role="admin" if admin else "member", display_name=display_name)


def reset_password(engine: Engine, *, username: str) -> str:
    users = UserStore(engine)
    found = users.find_for_login(username)
    if found is None:
        raise UserNotFound(username)
    temporary = generate_temporary_password()
    users.set_password(found[0].user_id, temporary, temporary=True)
    revoke_user_sessions(engine, found[0].user_id)
    return temporary


def command_user(arguments, paths: OrinPaths, console: Console) -> int:
    url = os.getenv("DATABASE_URL", "").strip() or sqlite_url(paths.data / "orin.db")
    paths.ensure()
    engine = create_local_engine(url)
    upgrade(engine)
    try:
        if arguments.user_command == "create":
            password = getpass.getpass("Senha: ")
            if password != getpass.getpass("Repita a senha: "):
                console.error("As senhas não conferem.")
                return 1
            user = create_user(engine, username=arguments.username, password=password, admin=arguments.admin, display_name=arguments.display_name)
            console.line(f"Perfil {user.username} criado ({user.role}).")
            return 0
        temporary = reset_password(engine, username=arguments.username)
        console.line(f"Senha provisória de {arguments.username}: {temporary}")
        console.line("Ela precisa ser trocada no primeiro login.")
        return 0
    except AccountError as error:
        console.error(f"{error.code}: {error}")
        return 1
    finally:
        engine.dispose()


__all__ = ["command_user", "create_user", "reset_password"]
