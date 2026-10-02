# Modo servidor com perfis (etapa 1) — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** o Orin sobe em modo `server` numa VPS, com login por usuário e senha, um admin que cria perfis, e conversas, memórias, projetos e chaves isolados por perfil; tudo que executa processo no host fica travado até a etapa 2.

**Architecture:** um modo explícito (`ORIN_MODE=server`) define um objeto `InstanceCapabilities` que é a única fonte de verdade sobre o que a instância oferece. Contas vivem num pacote novo `agentos/accounts` (senhas scrypt, tabela `users`, token de setup, proteção de login e um `SessionSecurityService` que estende o `PostgresSecurityService` existente). Rotas novas ficam em módulos próprios (`api/auth_routes.py`, `api/files_routes.py`) registrados por `create_app`. Os workspaces gerenciados passam a morar em `<data>/users/<user_id>/workspaces`, e cada perfil tem uma área de arquivos em `<data>/users/<user_id>/files`. O frontend ganha um portão de sessão que chama `/v1/auth/me` e renderiza só o que `capabilities` permitir.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy Core sobre SQLite, Alembic, `hashlib.scrypt` (stdlib), pytest + `fastapi.testclient`; React + TypeScript + react-router, Vitest + Testing Library, Playwright com API simulada.

**Spec:** `docs/superpowers/specs/2026-10-02-server-mode-profiles-design.md`

## Global Constraints

- Branch: `feat/server-mode`. Nada na `main`.
- Commits: Conventional Commits, `type(scope): lowercase imperative`, corpo explicando o porquê, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Prosa de docs e textos de UI em PT-BR; código, nomes, comentários e docstrings em inglês.
- O modo `local` não muda de comportamento: a suíte atual tem que continuar passando sem editar asserções existentes. Se um teste antigo quebrar, o código novo está errado.
- Erros da API sempre no envelope de `_error` (`api/gateway.py:1882`): `{"error": {"code", "category", "message_key", "correlation_id", "retryable", "retry_after"}}`.
- Recurso de outro perfil responde `404 resource_not_found`, nunca `403`.
- Senha: mínimo 10, máximo 256 caracteres. scrypt `n=2**15, r=8, p=1`, sal 16 bytes, saída 32 bytes, `maxmem=64 MiB`. Formato `scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>`.
- Username: normalizado em minúsculas, `^[a-z0-9][a-z0-9._-]{2,63}$`.
- Sessão: cookie `agentos_session`, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age=2592000`, `Secure` sempre que `ORIN_PUBLIC_URL` for `https://`. Expiração deslizante de 30 dias, `last_seen_at` gravado no máximo a cada 5 minutos.
- CSRF da sessão = `HMAC-SHA256(csrf_secret, session_id)` em hex; `csrf_secret = sha256(b"orin-session-csrf:" + AGENTOS_PROVIDER_ENCRYPTION_KEY)`. Assim `/v1/auth/me` devolve o mesmo token em todas as abas.
- Bloqueio de login: 5 falhas em 15 min por username (bloqueia 15 min), 10 falhas em 1 h por username (bloqueia 1 h), 20 falhas em 15 min por IP (bloqueia 15 min). Falhas contam a partir do último sucesso daquele username.
- O primeiro usuário criado numa instância recebe `user_id = "local-user"`; os demais `usr_<uuid4 hex>`.
- Próxima migração: `0046_user_accounts` (down_revision `0045_mcp_oauth`).
- Chave de teste do cifrador: `wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=`.
- SQLite devolve `DateTime` sem fuso: todo `datetime` lido do banco passa por `aware()` de `agentos/accounts/clock.py` (assume UTC).
- Suíte backend: `python -m pytest tests/unit tests/integration -q`. Frontend: `cd frontend && npx tsc -b --noEmit && npx eslint . --max-warnings=0 && npx vitest run && npm run build`.

## Review Focus

1. **Duas abas abertas no celular e no desktop**: a segunda aba chamando `/v1/auth/me` não pode invalidar o CSRF da primeira. Testes na Task 5 (`test_the_csrf_token_is_stable_and_bound_to_the_session`) e na Task 6 (`test_me_returns_the_same_csrf_for_the_same_session`).
2. **Admin rebaixa ou desativa a si mesmo sendo o único admin**: a instância nunca pode ficar sem admin ativo. Teste na Task 3 (`test_cannot_remove_the_last_active_admin`) e na Task 6 pela API.
3. **Perfil desativado com a aba aberta**: o próximo request precisa receber 401 mesmo com cookie válido, e o turno que ele deixou na fila não pode rodar. Testes nas Tasks 5 e 12.
4. **Projeto vinculado a uma pasta do host numa instalação local migrada para servidor**: não pode virar acesso ao disco da VPS; a UI mostra a pasta indisponível e o worker recusa o turno. Testes nas Tasks 11 e 12.
5. **Zip com `../`, symlink ou bomba de compressão enviado para a área de arquivos**: nada pode ser escrito fora da pasta de destino e a extração para antes de encher o disco. Testes na Task 11.

## Mapa de arquivos

Backend, novos:
- `src/agentos/configuration/mode.py`: `RuntimeMode` e `current_mode()`.
- `src/agentos/configuration/capabilities.py`: `InstanceCapabilities`, `CapabilityUnavailable`.
- `src/agentos/accounts/__init__.py`: exports do pacote.
- `src/agentos/accounts/clock.py`: `utcnow()`, `aware()`.
- `src/agentos/accounts/errors.py`: `AccountError` e subclasses com status/código HTTP.
- `src/agentos/accounts/passwords.py`: hash, verificação, validação e senha provisória.
- `src/agentos/accounts/store.py`: `UserRecord`, `UserStore`, `normalize_username`.
- `src/agentos/accounts/setup.py`: `SetupTokens`.
- `src/agentos/accounts/login_guard.py`: `LoginGuard`.
- `src/agentos/accounts/session_security.py`: `SessionSecurityService`, `derive_csrf_secret`.
- `src/agentos/accounts/services.py`: `AccountServices` (agrupa store, setup e guard).
- `src/agentos/api/auth_routes.py`: rotas `/v1/auth/*` e `/v1/admin/*`.
- `src/agentos/api/files_routes.py`: rotas `/v1/files*`.
- `src/agentos/profile_files/__init__.py`, `binding.py`, `archive.py`, `listing.py`: área de arquivos do perfil.
- `src/agentos/installation/layout.py`: migração `<data>/workspaces` → `<data>/users/local-user/workspaces`.
- `src/agentos/launcher/serve.py`: `orin serve`.
- `src/agentos/launcher/users.py`: `orin user create|reset-password`.
- `src/agentos/persistence/postgres/migrations/versions/0046_user_accounts.py`.

Backend, modificados:
- `src/agentos/persistence/postgres/schema.py`: tabelas `users`, `instance_setup`, `auth_login_attempts`; colunas `expires_at`, `last_seen_at` em `security_sessions`.
- `src/agentos/api/security.py`: `AdminRequiredError`, `PasswordChangeRequiredError`.
- `src/agentos/api/gateway.py`: `ApiServices` (novos campos e `managed_root_for`), handlers, registro das rotas novas, travas por capacidade, vínculo de pasta no modo server.
- `src/agentos/bootstrap/production.py`: `ProductionSettings`, composição do modo server, frontend `session`, token de setup no boot.
- `src/agentos/api/asgi.py`: passa modo e origem pública.
- `src/agentos/installation/paths.py`: `user_root`, `user_workspaces`, `user_files`.
- `src/agentos/agentic/agent_tools.py`: diagnóstico pós-escrita só com terminal.
- `src/agentos/agentic/session.py`: `TurnSession(enable_terminal=…)`, raiz gerenciada por usuário.
- `src/agentos/workers/chat.py`: capacidades, raiz por usuário, dono inativo, pasta fora da área.
- `src/agentos/scheduler/scheduled_chats.py`: ignora agendamentos de dono inativo.
- `src/agentos/mcp/service.py`: `allow_stdio`.
- `src/agentos/plugins/hook_engine.py`: `enabled`.
- `src/agentos/plugins/fetcher.py`: `remote_only`.
- `src/agentos/browser/conversation_worker.py`: sem loopback no modo server.
- `src/agentos/launcher/cli.py`, `src/agentos/launcher/supervisor.py`: `serve`, `user`, migração de layout no boot local.

Frontend, novos:
- `frontend/src/api/session.ts`: estado da sessão em módulo (CSRF, capacidades, aviso de 401).
- `frontend/src/api/auth.ts`: chamadas e parsers de `/v1/auth/*` e `/v1/admin/*`.
- `frontend/src/api/files.ts`: chamadas de `/v1/files*`.
- `frontend/src/app/SessionGate.tsx`: bootstrap da sessão e redirecionamentos.
- `frontend/src/app/useSession.ts`: contexto React da sessão.
- `frontend/src/features/auth/LoginPage.tsx`, `SetupPage.tsx`, `ChangePasswordPage.tsx`, `AuthLayout.tsx`.
- `frontend/src/features/auth/ProfileMenu.tsx`.
- `frontend/src/features/users/UsersSection.tsx`.
- `frontend/src/features/files/ProfileFolderBrowser.tsx`.

Frontend, modificados:
- `frontend/src/api/browserSession.ts`, `frontend/src/api/client.ts`, `frontend/src/app/App.tsx`, `frontend/src/app/routes.tsx`, `frontend/src/features/settings/sections.ts`, `frontend/src/features/settings/SettingsNav.tsx`, `frontend/src/components/CommandPalette.tsx`, `frontend/src/features/projects/WorkspaceNavigation.tsx`, `frontend/src/features/settings/AboutSection.tsx`, `frontend/src/features/mcp/McpServerForm.tsx`, `frontend/src/features/conversations/WorkspaceFileCard.tsx`, `frontend/src/features/conversations/WorkspaceFolderButton.tsx`, `frontend/src/features/providers/ProvidersSection.tsx`, `frontend/src/styles/agentos.css`.

---

### Task 1: Modo de execução, capacidades e configuração do servidor

**Files:**
- Create: `src/agentos/configuration/mode.py`
- Create: `src/agentos/configuration/capabilities.py`
- Modify: `src/agentos/bootstrap/production.py:67-99` (`ProductionSettings`)
- Test: `tests/unit/configuration/test_runtime_mode.py`
- Test: `tests/unit/bootstrap/test_server_settings.py`

**Interfaces:**
- Produces:
  - `class RuntimeMode(StrEnum)` com `LOCAL = "local"` e `SERVER = "server"`.
  - `current_mode(environ: Mapping[str, str] | None = None) -> RuntimeMode`: lê `ORIN_MODE` (padrão `local`, sem diferenciar maiúsculas); valor desconhecido levanta `ValueError`.
  - `@dataclass(frozen=True, slots=True) class InstanceCapabilities` com os campos booleanos `shell`, `mcp_stdio`, `plugin_hooks`, `omniroute`, `host_folders`, `profile_files`, `open_in_desktop_app`, `ui_updater`, `user_admin`.
  - `InstanceCapabilities.for_mode(mode: RuntimeMode) -> InstanceCapabilities`.
  - `InstanceCapabilities.as_dict() -> dict[str, bool]`.
  - `InstanceCapabilities.require(name: str) -> None`: levanta `CapabilityUnavailable(name)` quando o campo é `False`; nome desconhecido levanta `KeyError`.
  - `class CapabilityUnavailable(RuntimeError)` com atributo `capability: str`.
  - `ProductionSettings` ganha `ORIN_MODE: RuntimeMode = RuntimeMode.LOCAL`, `ORIN_PUBLIC_URL: str | None = None`, `ORIN_TRUSTED_PROXIES: str = ""` e a propriedade `public_origin -> str | None` (`scheme://host[:porta]`, sem barra final).

- [ ] **Step 1: Escrever os testes que falham**

`tests/unit/configuration/test_runtime_mode.py`:

```python
import pytest

from agentos.configuration.capabilities import CapabilityUnavailable, InstanceCapabilities
from agentos.configuration.mode import RuntimeMode, current_mode


def test_mode_defaults_to_local_and_reads_orin_mode():
    assert current_mode({}) is RuntimeMode.LOCAL
    assert current_mode({"ORIN_MODE": "Server"}) is RuntimeMode.SERVER
    assert current_mode({"ORIN_MODE": " local "}) is RuntimeMode.LOCAL


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        current_mode({"ORIN_MODE": "cluster"})


def test_local_capabilities_keep_every_desktop_feature():
    caps = InstanceCapabilities.for_mode(RuntimeMode.LOCAL)
    assert caps.as_dict() == {
        "shell": True, "mcp_stdio": True, "plugin_hooks": True, "omniroute": True,
        "host_folders": True, "profile_files": False, "open_in_desktop_app": True,
        "ui_updater": True, "user_admin": False,
    }


def test_server_capabilities_close_everything_that_runs_a_process():
    caps = InstanceCapabilities.for_mode(RuntimeMode.SERVER)
    assert caps.as_dict() == {
        "shell": False, "mcp_stdio": False, "plugin_hooks": False, "omniroute": False,
        "host_folders": False, "profile_files": True, "open_in_desktop_app": False,
        "ui_updater": False, "user_admin": True,
    }


def test_require_raises_for_a_closed_capability():
    caps = InstanceCapabilities.for_mode(RuntimeMode.SERVER)
    caps.require("profile_files")
    with pytest.raises(CapabilityUnavailable) as raised:
        caps.require("shell")
    assert raised.value.capability == "shell"
    with pytest.raises(KeyError):
        caps.require("teleport")
```

`tests/unit/bootstrap/test_server_settings.py`:

```python
import pytest
from pydantic import ValidationError

from agentos.bootstrap.production import ProductionSettings
from agentos.configuration.mode import RuntimeMode

KEY = "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA="


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("ORIN_MODE", "ORIN_PUBLIC_URL", "ORIN_TRUSTED_PROXIES", "LOCALHOST_TRUST_ENABLED", "WEB_DIST_DIR", "AGENTOS_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)


def _server(**overrides):
    values = {"DATABASE_URL": "sqlite:///x.db", "ORIN_MODE": "server", "ORIN_PUBLIC_URL": "https://orin.example.com", "WEB_DIST_DIR": "/srv/web", "AGENTOS_ENV": "production"}
    values.update(overrides)
    return ProductionSettings(**values)


def test_server_mode_accepts_a_complete_configuration():
    settings = _server()
    assert settings.ORIN_MODE is RuntimeMode.SERVER
    assert settings.public_origin == "https://orin.example.com"


def test_public_origin_drops_path_and_keeps_port():
    assert _server(ORIN_PUBLIC_URL="https://orin.example.com:8443/app/").public_origin == "https://orin.example.com:8443"


@pytest.mark.parametrize("url", [None, "orin.example.com", "ftp://orin.example.com", "http://orin.example.com"])
def test_server_mode_requires_an_absolute_https_public_url(url):
    with pytest.raises(ValidationError):
        _server(ORIN_PUBLIC_URL=url)


def test_plain_http_is_allowed_only_for_loopback_development():
    assert _server(ORIN_PUBLIC_URL="http://127.0.0.1:49200").public_origin == "http://127.0.0.1:49200"
    assert _server(ORIN_PUBLIC_URL="http://localhost:49200").public_origin == "http://localhost:49200"


def test_server_mode_refuses_loopback_trust():
    with pytest.raises(ValidationError):
        _server(LOCALHOST_TRUST_ENABLED=True, AGENTOS_ENV="local")


def test_server_mode_requires_the_web_build():
    with pytest.raises(ValidationError):
        _server(WEB_DIST_DIR=None)


def test_server_mode_requires_a_stable_encryption_key(monkeypatch):
    monkeypatch.delenv("AGENTOS_PROVIDER_ENCRYPTION_KEY")
    monkeypatch.delenv("APP_MASTER_KEY", raising=False)
    with pytest.raises(ValidationError):
        _server()


def test_local_mode_is_unchanged():
    settings = ProductionSettings(DATABASE_URL="sqlite:///x.db")
    assert settings.ORIN_MODE is RuntimeMode.LOCAL
    assert settings.public_origin is None
```

Crie `tests/unit/configuration/__init__.py` e `tests/unit/bootstrap/__init__.py` vazios se não existirem.

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/configuration/test_runtime_mode.py tests/unit/bootstrap/test_server_settings.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.configuration.mode'`.

- [ ] **Step 3: Implementar**

`src/agentos/configuration/mode.py`:

```python
"""Which deployment shape this process runs as.

``local`` is the personal install (loopback trust, one fixed principal).
``server`` is a network-facing instance shared by named user profiles.
"""
from __future__ import annotations

import os
from enum import StrEnum
from typing import Mapping


class RuntimeMode(StrEnum):
    LOCAL = "local"
    SERVER = "server"


def current_mode(environ: Mapping[str, str] | None = None) -> RuntimeMode:
    """The mode from ``ORIN_MODE``; anything unknown is a configuration error, never a silent default."""
    raw = (environ if environ is not None else os.environ).get("ORIN_MODE", "").strip().lower()
    if not raw:
        return RuntimeMode.LOCAL
    return RuntimeMode(raw)


__all__ = ["RuntimeMode", "current_mode"]
```

`src/agentos/configuration/capabilities.py`:

```python
"""What this instance offers. The single source of truth for every gate.

The gateway, the agent runtime, the MCP service, the hook engine and the web
client all read this object instead of re-deriving policy from the mode, so a
later stage can flip one field (e.g. ``shell`` once a sandbox exists) without
touching the places that consult it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from .mode import RuntimeMode


class CapabilityUnavailable(RuntimeError):
    def __init__(self, capability: str) -> None:
        super().__init__(f"capability '{capability}' is not available on this instance")
        self.capability = capability


@dataclass(frozen=True, slots=True)
class InstanceCapabilities:
    shell: bool
    mcp_stdio: bool
    plugin_hooks: bool
    omniroute: bool
    host_folders: bool
    profile_files: bool
    open_in_desktop_app: bool
    ui_updater: bool
    user_admin: bool

    @classmethod
    def for_mode(cls, mode: RuntimeMode) -> "InstanceCapabilities":
        if mode is RuntimeMode.SERVER:
            # Everything that would start a process on the host stays closed
            # until the per-profile sandbox (stage 2) exists.
            return cls(
                shell=False, mcp_stdio=False, plugin_hooks=False, omniroute=False,
                host_folders=False, profile_files=True, open_in_desktop_app=False,
                ui_updater=False, user_admin=True,
            )
        return cls(
            shell=True, mcp_stdio=True, plugin_hooks=True, omniroute=True,
            host_folders=True, profile_files=False, open_in_desktop_app=True,
            ui_updater=True, user_admin=False,
        )

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)

    def require(self, name: str) -> None:
        if name not in {item.name for item in fields(self)}:
            raise KeyError(name)
        if not getattr(self, name):
            raise CapabilityUnavailable(name)


__all__ = ["CapabilityUnavailable", "InstanceCapabilities"]
```

Em `src/agentos/bootstrap/production.py`, acrescente os imports `from urllib.parse import urlsplit` e `from agentos.configuration.mode import RuntimeMode`, e troque a classe `ProductionSettings` por:

```python
class ProductionSettings(AgentOSSettings):
    """Flat, explicit deployment configuration; secrets remain redacted."""

    # This is the production specialization of AgentOSSettings. Flat aliases
    # are required for deployment tooling while the base model preserves the
    # typed local-secret and provider-catalog validation from RFC 604.
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    DATABASE_URL: str
    AGENTOS_ENV: str = "development"
    LOCALHOST_TRUST_ENABLED: bool = False
    WEB_DIST_DIR: str | None = None
    AGENTOS_ACTIVITY_CURSOR_SECRET: SecretStr | None = None
    ORIN_MODE: RuntimeMode = RuntimeMode.LOCAL
    ORIN_PUBLIC_URL: str | None = None
    ORIN_TRUSTED_PROXIES: str = ""
    OPENROUTER_ENABLED: bool = False
    OPENROUTER_API_KEY: SecretStr | None = None
    OPENROUTER_MODEL: str | None = None
    ANTHROPIC_ENABLED: bool = False
    ANTHROPIC_API_KEY: SecretStr | None = None
    ANTHROPIC_MODEL: str | None = None
    OPENAI_ENABLED: bool = False
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_MODEL: str | None = None

    @property
    def public_origin(self) -> str | None:
        if not self.ORIN_PUBLIC_URL:
            return None
        parsed = urlsplit(self.ORIN_PUBLIC_URL.strip())
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    @model_validator(mode="after")
    def _validate_enabled_providers(self) -> "ProductionSettings":
        if self.LOCALHOST_TRUST_ENABLED and self.AGENTOS_ENV.strip().lower() not in {"development", "local"}:
            raise ValueError("LOCALHOST_TRUST_ENABLED is allowed only when AGENTOS_ENV is development or local")
        if self.LOCALHOST_TRUST_ENABLED and not self.WEB_DIST_DIR:
            raise ValueError("LOCALHOST_TRUST_ENABLED requires WEB_DIST_DIR with the built frontend")
        if self.ORIN_MODE is RuntimeMode.SERVER:
            self._validate_server()
        for name in ("OPENROUTER", "ANTHROPIC", "OPENAI"):
            if getattr(self, f"{name}_ENABLED") and getattr(self, f"{name}_API_KEY") is None:
                raise ValueError(f"enabled {name} provider requires API key")
        return self

    def _validate_server(self) -> None:
        if self.LOCALHOST_TRUST_ENABLED:
            raise ValueError("ORIN_MODE=server never trusts loopback; unset LOCALHOST_TRUST_ENABLED")
        if not self.WEB_DIST_DIR:
            raise ValueError("ORIN_MODE=server requires WEB_DIST_DIR with the built frontend")
        if not (os.getenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "").strip() or os.getenv("APP_MASTER_KEY", "").strip()):
            raise ValueError("ORIN_MODE=server requires AGENTOS_PROVIDER_ENCRYPTION_KEY")
        parsed = urlsplit((self.ORIN_PUBLIC_URL or "").strip())
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("ORIN_MODE=server requires ORIN_PUBLIC_URL as an absolute https:// URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("ORIN_PUBLIC_URL must use https:// unless it points at loopback for development")
```

(`os` já é importado em `production.py`; confira no topo do arquivo e importe se faltar.)

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/configuration/test_runtime_mode.py tests/unit/bootstrap/test_server_settings.py -q`
Expected: PASS.

Run: `python -m pytest tests/unit -q -k "settings or production"`
Expected: PASS (nada do modo local mudou).

- [ ] **Step 5: Commit**

```bash
git add src/agentos/configuration/mode.py src/agentos/configuration/capabilities.py src/agentos/bootstrap/production.py tests/unit/configuration tests/unit/bootstrap
git commit -m "feat(server): add the server runtime mode and instance capabilities

One object decides what an instance offers so every gate reads the same
answer, and server settings refuse to boot half-configured.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Senhas e erros de conta

**Files:**
- Create: `src/agentos/accounts/__init__.py`
- Create: `src/agentos/accounts/clock.py`
- Create: `src/agentos/accounts/errors.py`
- Create: `src/agentos/accounts/passwords.py`
- Test: `tests/unit/accounts/test_passwords.py`

**Interfaces:**
- Produces:
  - `utcnow() -> datetime` e `aware(value: datetime) -> datetime` (anexa UTC a datetime ingênuo).
  - `class AccountError(Exception)` com atributos de classe `status: int`, `code: str`, `category: str`, `retryable: bool = False` e de instância `retry_after: int | None = None`.
  - Subclasses: `InvalidCredentials` (401, `invalid_credentials`, `AUTHENTICATION`), `LoginLocked(retry_after: int)` (429, `login_locked`, `RATE_LIMITED`, retryable), `SetupRequired` (409, `setup_required`, `CONFLICT`), `SetupCompleted` (409, `setup_completed`, `CONFLICT`), `InvalidSetupToken` (401, `invalid_setup_token`, `AUTHENTICATION`), `UsernameTaken` (409, `username_taken`, `CONFLICT`), `InvalidUsername` (422, `invalid_username`, `VALIDATION`), `WeakPassword` (422, `weak_password`, `VALIDATION`), `LastAdmin` (409, `last_admin`, `CONFLICT`), `UserNotFound` (404, `resource_not_found`, `NOT_FOUND`).
  - `MIN_PASSWORD_LENGTH = 10`, `MAX_PASSWORD_LENGTH = 256`.
  - `validate_password(password: str) -> None` (levanta `WeakPassword`).
  - `hash_password(password: str) -> str`.
  - `verify_password(password: str, encoded: str) -> bool` (nunca levanta; hash malformado devolve `False`).
  - `burn_password_check(password: str) -> None`: roda um scrypt contra um hash fixo, para usuário inexistente custar o mesmo tempo.
  - `generate_temporary_password() -> str` (16 caracteres URL-safe).

- [ ] **Step 1: Escrever o teste que falha**

`tests/unit/accounts/test_passwords.py` (crie também `tests/unit/accounts/__init__.py` vazio):

```python
import pytest

from agentos.accounts.errors import LoginLocked, WeakPassword
from agentos.accounts.passwords import (
    MAX_PASSWORD_LENGTH, burn_password_check, generate_temporary_password, hash_password,
    validate_password, verify_password,
)


def test_hash_round_trip_and_wrong_password():
    encoded = hash_password("correct horse battery")
    assert encoded.startswith("scrypt$32768$8$1$")
    assert verify_password("correct horse battery", encoded)
    assert not verify_password("correct horse batterY", encoded)


def test_each_hash_has_its_own_salt():
    assert hash_password("same password!") != hash_password("same password!")


def test_parameters_are_read_from_the_stored_hash():
    # A hash written with lighter parameters keeps verifying after the default is raised.
    from agentos.accounts import passwords
    light = passwords._hash_with("legacy-password", n=2**14, r=8, p=1)
    assert light.startswith("scrypt$16384$8$1$")
    assert verify_password("legacy-password", light)


@pytest.mark.parametrize("encoded", ["", "bcrypt$x", "scrypt$1$2$3", "scrypt$abc$8$1$AA$AA", "scrypt$32768$8$1$***$***"])
def test_malformed_hash_never_raises(encoded):
    assert verify_password("anything at all", encoded) is False


@pytest.mark.parametrize("password", ["short", "x" * 9, "y" * (MAX_PASSWORD_LENGTH + 1)])
def test_password_length_is_bounded(password):
    with pytest.raises(WeakPassword):
        validate_password(password)


def test_valid_password_passes_validation():
    validate_password("ten chars!")


def test_temporary_password_is_strong_enough_to_pass_validation():
    password = generate_temporary_password()
    assert len(password) == 16
    validate_password(password)
    assert generate_temporary_password() != password


def test_burn_check_runs_without_error():
    burn_password_check("whatever")


def test_login_locked_carries_retry_after():
    error = LoginLocked(retry_after=900)
    assert (error.status, error.code, error.retryable, error.retry_after) == (429, "login_locked", True, 900)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/accounts/test_passwords.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.accounts'`.

- [ ] **Step 3: Implementar**

`src/agentos/accounts/clock.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; everything Orin writes is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
```

`src/agentos/accounts/errors.py`:

```python
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
```

`src/agentos/accounts/passwords.py`:

```python
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
```

`src/agentos/accounts/__init__.py`:

```python
"""User accounts for server mode: passwords, users, setup, sign-in protection and sessions."""
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/accounts/test_passwords.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/accounts tests/unit/accounts
git commit -m "feat(accounts): hash passwords with stdlib scrypt and type account errors

scrypt keeps the frozen build free of a new native dependency, and each
account error carries its own HTTP shape so the gateway maps them once.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Tabela de usuários, migração e `UserStore`

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py` (depois de `security_rate_limit_hits`, ~linha 302)
- Create: `src/agentos/persistence/postgres/migrations/versions/0046_user_accounts.py`
- Create: `src/agentos/accounts/store.py`
- Test: `tests/unit/accounts/test_user_store.py`
- Test: `tests/unit/persistence/test_migrations.py` (acrescentar um teste)

**Interfaces:**
- Consumes: `hash_password`, `verify_password`, `validate_password`, erros da Task 2; `utcnow`, `aware`.
- Produces:
  - Tabelas `users`, `instance_setup`, `auth_login_attempts`; colunas `security_sessions.expires_at` e `security_sessions.last_seen_at` (ambas `DateTime(timezone=True)`, anuláveis).
  - `FIRST_USER_ID = "local-user"`.
  - `normalize_username(raw: str) -> str` (levanta `InvalidUsername`).
  - `@dataclass(frozen=True, slots=True) class UserRecord` com `user_id`, `username`, `display_name`, `role` (`"admin" | "member"`), `active`, `must_change_password`, `created_at`, `updated_at`, `password_changed_at`; método `public() -> dict[str, object]` (datas em ISO 8601, sem hash).
  - `class UserStore(engine)` com:
    - `count() -> int`
    - `create(*, username: str, password: str, role: str = "member", must_change_password: bool = False, display_name: str | None = None) -> UserRecord`
    - `get(user_id: str) -> UserRecord | None`
    - `find_for_login(username: str) -> tuple[UserRecord, str] | None` (registro e hash; username é normalizado; inválido devolve `None`)
    - `list() -> list[UserRecord]` (ordem de criação)
    - `update(user_id: str, *, display_name: str | None = None, role: str | None = None, active: bool | None = None) -> UserRecord`
    - `set_password(user_id: str, password: str, *, temporary: bool) -> UserRecord`
    - `is_active(user_id: str) -> bool` (usuário inexistente devolve `False`)

- [ ] **Step 1: Escrever os testes que falham**

`tests/unit/accounts/test_user_store.py`:

```python
import pytest
from sqlalchemy import create_engine

from agentos.accounts.errors import InvalidUsername, LastAdmin, UserNotFound, UsernameTaken, WeakPassword
from agentos.accounts.passwords import verify_password
from agentos.accounts.store import FIRST_USER_ID, UserStore, normalize_username
from agentos.persistence.postgres.schema import metadata


@pytest.fixture()
def store(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    return UserStore(engine)


def test_first_user_inherits_the_local_profile_id(store):
    admin = store.create(username="Carla", password="a long password", role="admin")
    member = store.create(username="bruno", password="another password")
    assert admin.user_id == FIRST_USER_ID
    assert admin.username == "carla"
    assert member.user_id.startswith("usr_") and len(member.user_id) == 36
    assert store.count() == 2


@pytest.mark.parametrize("raw", ["ab", "-carla", "carla smith", "c" * 65, "çarla", ""])
def test_invalid_usernames_are_rejected(raw):
    with pytest.raises(InvalidUsername):
        normalize_username(raw)


def test_usernames_are_unique_case_insensitively(store):
    store.create(username="carla", password="a long password", role="admin")
    with pytest.raises(UsernameTaken):
        store.create(username="CARLA", password="a long password")


def test_weak_password_is_refused_on_create(store):
    with pytest.raises(WeakPassword):
        store.create(username="carla", password="short")


def test_find_for_login_returns_the_hash(store):
    store.create(username="carla", password="a long password", role="admin")
    record, encoded = store.find_for_login(" Carla ")
    assert record.username == "carla"
    assert verify_password("a long password", encoded)
    assert store.find_for_login("nobody") is None
    assert store.find_for_login("x y") is None


def test_public_view_never_contains_the_hash(store):
    record = store.create(username="carla", password="a long password", role="admin")
    public = record.public()
    assert set(public) == {"user_id", "username", "display_name", "role", "active", "must_change_password", "created_at", "updated_at", "password_changed_at"}
    assert public["display_name"] == "carla"


def test_temporary_password_sets_and_clears_the_flag(store):
    admin = store.create(username="carla", password="a long password", role="admin")
    reset = store.set_password(admin.user_id, "temporary-123", temporary=True)
    assert reset.must_change_password is True
    changed = store.set_password(admin.user_id, "my new password", temporary=False)
    assert changed.must_change_password is False
    _, encoded = store.find_for_login("carla")
    assert verify_password("my new password", encoded)


def test_cannot_remove_the_last_active_admin(store):
    admin = store.create(username="carla", password="a long password", role="admin")
    with pytest.raises(LastAdmin):
        store.update(admin.user_id, role="member")
    with pytest.raises(LastAdmin):
        store.update(admin.user_id, active=False)
    second = store.create(username="bruno", password="a long password", role="admin")
    store.update(admin.user_id, active=False)
    assert store.is_active(admin.user_id) is False
    with pytest.raises(LastAdmin):
        store.update(second.user_id, role="member")


def test_update_unknown_user(store):
    with pytest.raises(UserNotFound):
        store.update("usr_missing", display_name="x")
    assert store.is_active("usr_missing") is False
    assert store.get("usr_missing") is None


def test_list_keeps_creation_order(store):
    store.create(username="carla", password="a long password", role="admin")
    store.create(username="bruno", password="a long password")
    assert [item.username for item in store.list()] == ["carla", "bruno"]
```

Em `tests/unit/persistence/test_migrations.py`, acrescente:

```python
def test_migration_0046_creates_accounts_and_session_expiry():
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"users", "instance_setup", "auth_login_attempts"} <= tables
    columns = {column["name"] for column in inspect(engine).get_columns("security_sessions")}
    assert {"expires_at", "last_seen_at"} <= columns
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/accounts/test_user_store.py tests/unit/persistence/test_migrations.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.accounts.store'` e o teste de migração falhando por falta de `users`.

- [ ] **Step 3: Implementar**

Em `src/agentos/persistence/postgres/schema.py`, dentro de `security_sessions`, acrescente depois de `Column("created_at", ...)`:

```python
    Column("expires_at", DateTime(timezone=True)),
    Column("last_seen_at", DateTime(timezone=True)),
```

E, logo depois do bloco `security_rate_limit_hits`:

```python
users = Table(
    "users", metadata,
    Column("user_id", String(255), primary_key=True),
    Column("username", String(64), nullable=False),
    Column("display_name", String(128), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(16), nullable=False),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("must_change_password", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("password_changed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("username", name="uq_users_username"),
    CheckConstraint("role IN ('admin', 'member')", name="ck_users_role"),
)

# At most one row: the digest of the one-time token that creates the first admin.
instance_setup = Table(
    "instance_setup", metadata,
    Column("id", Integer, primary_key=True),
    Column("token_digest", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

auth_login_attempts = Table(
    "auth_login_attempts", metadata,
    Column("id", Integer, primary_key=True),
    Column("key", String(320), nullable=False),
    Column("succeeded", Boolean, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)
Index("ix_auth_login_attempts_key_time", auth_login_attempts.c.key, auth_login_attempts.c.occurred_at)
```

`src/agentos/persistence/postgres/migrations/versions/0046_user_accounts.py`:

```python
"""User accounts, first-admin setup, sign-in attempts and session expiry

Revision ID: 0046_user_accounts
Revises: 0045_mcp_oauth
"""
from alembic import op
import sqlalchemy as sa


revision = "0046_user_accounts"
down_revision = "0045_mcp_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_users_role"),
    )
    op.create_table(
        "instance_setup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(320), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_login_attempts_key_time", "auth_login_attempts", ["key", "occurred_at"])
    with op.batch_alter_table("security_sessions") as batch:
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("security_sessions") as batch:
        batch.drop_column("last_seen_at")
        batch.drop_column("expires_at")
    op.drop_index("ix_auth_login_attempts_key_time", table_name="auth_login_attempts")
    op.drop_table("auth_login_attempts")
    op.drop_table("instance_setup")
    op.drop_table("users")
```

`src/agentos/accounts/store.py`:

```python
"""Durable user accounts. The only place that reads or writes ``users``."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentos.persistence.postgres.schema import users

from .clock import aware, utcnow
from .errors import InvalidUsername, LastAdmin, UserNotFound, UsernameTaken
from .passwords import hash_password, validate_password

# A local install that becomes a server keeps its data: the first account
# owns everything the loopback principal already wrote.
FIRST_USER_ID = "local-user"
ROLES = ("admin", "member")
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def normalize_username(raw: str) -> str:
    candidate = (raw or "").strip().lower()
    if not _USERNAME.fullmatch(candidate):
        raise InvalidUsername("username must be 3-64 characters of a-z, 0-9, '.', '_' or '-'")
    return candidate


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    username: str
    display_name: str
    role: str
    active: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
    password_changed_at: datetime

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict[str, object]:
        return {
            "user_id": self.user_id, "username": self.username, "display_name": self.display_name,
            "role": self.role, "active": self.active, "must_change_password": self.must_change_password,
            "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat(),
            "password_changed_at": self.password_changed_at.isoformat(),
        }


def _record(row) -> UserRecord:
    return UserRecord(
        user_id=row["user_id"], username=row["username"], display_name=row["display_name"],
        role=row["role"], active=bool(row["active"]), must_change_password=bool(row["must_change_password"]),
        created_at=aware(row["created_at"]), updated_at=aware(row["updated_at"]),
        password_changed_at=aware(row["password_changed_at"]),
    )


class UserStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def count(self) -> int:
        with self._engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(users)).scalar_one())

    def create(self, *, username: str, password: str, role: str = "member", must_change_password: bool = False, display_name: str | None = None) -> UserRecord:
        name = normalize_username(username)
        validate_password(password)
        if role not in ROLES:
            raise ValueError("role must be admin or member")
        now = utcnow()
        try:
            with self._engine.begin() as connection:
                existing = int(connection.execute(select(func.count()).select_from(users)).scalar_one())
                user_id = FIRST_USER_ID if existing == 0 else f"usr_{uuid4().hex}"
                connection.execute(insert(users).values(
                    user_id=user_id, username=name, display_name=(display_name or "").strip()[:128] or name,
                    password_hash=hash_password(password), role=role, active=True,
                    must_change_password=must_change_password, created_at=now, updated_at=now, password_changed_at=now,
                ))
        except IntegrityError as error:
            raise UsernameTaken(f"username '{name}' is already taken") from error
        record = self.get(user_id)
        assert record is not None
        return record

    def get(self, user_id: str) -> UserRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(users).where(users.c.user_id == user_id)).mappings().first()
        return _record(row) if row is not None else None

    def find_for_login(self, username: str) -> tuple[UserRecord, str] | None:
        try:
            name = normalize_username(username)
        except InvalidUsername:
            return None
        with self._engine.connect() as connection:
            row = connection.execute(select(users).where(users.c.username == name)).mappings().first()
        return (_record(row), str(row["password_hash"])) if row is not None else None

    def list(self) -> list[UserRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(users).order_by(users.c.created_at, users.c.username)).mappings().all()
        return [_record(row) for row in rows]

    def update(self, user_id: str, *, display_name: str | None = None, role: str | None = None, active: bool | None = None) -> UserRecord:
        if role is not None and role not in ROLES:
            raise ValueError("role must be admin or member")
        with self._engine.begin() as connection:
            row = connection.execute(select(users).where(users.c.user_id == user_id)).mappings().first()
            if row is None:
                raise UserNotFound(user_id)
            stays_admin = (role or row["role"]) == "admin" and (row["active"] if active is None else active)
            if row["role"] == "admin" and row["active"] and not stays_admin:
                others = int(connection.execute(select(func.count()).select_from(users).where(
                    users.c.role == "admin", users.c.active.is_(True), users.c.user_id != user_id,
                )).scalar_one())
                if others == 0:
                    raise LastAdmin("an instance always keeps one active admin")
            values: dict[str, object] = {"updated_at": utcnow()}
            if display_name is not None:
                values["display_name"] = display_name.strip()[:128] or row["username"]
            if role is not None:
                values["role"] = role
            if active is not None:
                values["active"] = active
            connection.execute(update(users).where(users.c.user_id == user_id).values(**values))
        record = self.get(user_id)
        assert record is not None
        return record

    def set_password(self, user_id: str, password: str, *, temporary: bool) -> UserRecord:
        validate_password(password)
        now = utcnow()
        with self._engine.begin() as connection:
            result = connection.execute(update(users).where(users.c.user_id == user_id).values(
                password_hash=hash_password(password), must_change_password=temporary,
                password_changed_at=now, updated_at=now,
            ))
            if result.rowcount != 1:
                raise UserNotFound(user_id)
        record = self.get(user_id)
        assert record is not None
        return record

    def is_active(self, user_id: str) -> bool:
        record = self.get(user_id)
        return record is not None and record.active


__all__ = ["FIRST_USER_ID", "ROLES", "UserRecord", "UserStore", "normalize_username"]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/accounts/test_user_store.py tests/unit/persistence/test_migrations.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/persistence/postgres/schema.py src/agentos/persistence/postgres/migrations/versions/0046_user_accounts.py src/agentos/accounts/store.py tests/unit/accounts/test_user_store.py tests/unit/persistence/test_migrations.py
git commit -m "feat(accounts): store user accounts and keep one active admin

The first account takes the local-user id so a local install moved to a
server keeps its conversations, memory and keys without rewriting owners.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Token de setup e proteção de login

**Files:**
- Create: `src/agentos/accounts/setup.py`
- Create: `src/agentos/accounts/login_guard.py`
- Test: `tests/unit/accounts/test_setup_tokens.py`
- Test: `tests/unit/accounts/test_login_guard.py`

**Interfaces:**
- Consumes: `UserStore`, `utcnow`, `aware`, `LoginLocked`, `SetupCompleted`, `InvalidSetupToken`.
- Produces:
  - `class SetupTokens(engine)`:
    - `issue_if_needed(users: UserStore) -> str | None`: com zero usuários, gera um token novo (`secrets.token_urlsafe(32)`), substitui o digest guardado e devolve o token; com usuários, apaga qualquer digest e devolve `None`.
    - `consume(token: str, users: UserStore) -> None`: levanta `SetupCompleted` se já existe usuário, `InvalidSetupToken` se o token não confere; em sucesso apaga o digest. O token só é consumido de fato quando o admin é criado (ver Task 6).
    - `verify(token: str) -> bool`.
  - `class LoginGuard(engine, *, clock: Callable[[], datetime] = utcnow)`:
    - `check(*, username: str, ip: str) -> None` (levanta `LoginLocked(retry_after=segundos)`).
    - `record(*, username: str, ip: str, succeeded: bool) -> None`.

- [ ] **Step 1: Escrever os testes que falham**

`tests/unit/accounts/test_setup_tokens.py`:

```python
import pytest
from sqlalchemy import create_engine, func, select

from agentos.accounts.errors import InvalidSetupToken, SetupCompleted
from agentos.accounts.setup import SetupTokens
from agentos.accounts.store import UserStore
from agentos.persistence.postgres.schema import instance_setup, metadata


@pytest.fixture()
def world(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    return engine, SetupTokens(engine), UserStore(engine)


def _rows(engine):
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(instance_setup)).scalar_one()


def test_a_fresh_instance_issues_a_token_and_stores_only_its_digest(world):
    engine, tokens, users = world
    token = tokens.issue_if_needed(users)
    assert token and len(token) >= 40
    with engine.connect() as connection:
        stored = connection.execute(select(instance_setup.c.token_digest)).scalar_one()
    assert token not in stored and len(stored) == 64
    assert tokens.verify(token) and not tokens.verify("wrong")


def test_reissuing_replaces_the_previous_token(world):
    engine, tokens, users = world
    first = tokens.issue_if_needed(users)
    second = tokens.issue_if_needed(users)
    assert first != second
    assert not tokens.verify(first) and tokens.verify(second)
    assert _rows(engine) == 1


def test_consume_checks_the_token_and_clears_it(world):
    engine, tokens, users = world
    token = tokens.issue_if_needed(users)
    with pytest.raises(InvalidSetupToken):
        tokens.consume("nope", users)
    tokens.consume(token, users)
    assert _rows(engine) == 0


def test_no_token_once_an_account_exists(world):
    engine, tokens, users = world
    tokens.issue_if_needed(users)
    users.create(username="carla", password="a long password", role="admin")
    assert tokens.issue_if_needed(users) is None
    assert _rows(engine) == 0
    with pytest.raises(SetupCompleted):
        tokens.consume("anything", users)
```

`tests/unit/accounts/test_login_guard.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from agentos.accounts.errors import LoginLocked
from agentos.accounts.login_guard import LoginGuard
from agentos.persistence.postgres.schema import metadata


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def guard(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    clock = Clock()
    return LoginGuard(engine, clock=clock), clock


def _fail(guard, times, *, username="carla", ip="203.0.113.7"):
    for _ in range(times):
        guard.record(username=username, ip=ip, succeeded=False)


def test_five_failures_lock_the_username_for_fifteen_minutes(guard):
    guard, clock = guard
    _fail(guard, 4)
    guard.check(username="carla", ip="203.0.113.7")
    _fail(guard, 1)
    with pytest.raises(LoginLocked) as raised:
        guard.check(username="carla", ip="198.51.100.1")
    assert 0 < raised.value.retry_after <= 900
    clock.now += timedelta(minutes=15, seconds=1)
    guard.check(username="carla", ip="198.51.100.1")


def test_ten_failures_in_an_hour_lock_for_an_hour(guard):
    guard, clock = guard
    _fail(guard, 5)
    clock.now += timedelta(minutes=16)
    _fail(guard, 5)
    with pytest.raises(LoginLocked) as raised:
        guard.check(username="carla", ip="203.0.113.7")
    assert 900 < raised.value.retry_after <= 3600


def test_a_success_resets_the_username_count(guard):
    guard, _ = guard
    _fail(guard, 4)
    guard.record(username="carla", ip="203.0.113.7", succeeded=True)
    _fail(guard, 4)
    guard.check(username="carla", ip="203.0.113.7")


def test_twenty_failures_from_one_ip_lock_that_ip_across_usernames(guard):
    guard, _ = guard
    for index in range(20):
        guard.record(username=f"user{index}", ip="203.0.113.7", succeeded=False)
    with pytest.raises(LoginLocked):
        guard.check(username="fresh", ip="203.0.113.7")
    guard.check(username="fresh", ip="198.51.100.1")


def test_usernames_are_compared_case_insensitively(guard):
    guard, _ = guard
    _fail(guard, 5, username="Carla")
    with pytest.raises(LoginLocked):
        guard.check(username="CARLA", ip="198.51.100.1")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/accounts/test_setup_tokens.py tests/unit/accounts/test_login_guard.py -q`
Expected: FAIL com `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`src/agentos/accounts/setup.py`:

```python
"""The one-time token that creates the first admin of a server instance.

Without it, whoever reaches a fresh public URL first would own the instance.
The raw token only ever exists in the API's log; the database keeps a digest.
"""
from __future__ import annotations

import secrets
from hashlib import sha256
from hmac import compare_digest

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import instance_setup

from .clock import utcnow
from .errors import InvalidSetupToken, SetupCompleted
from .store import UserStore


def _digest(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


class SetupTokens:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def issue_if_needed(self, users: UserStore) -> str | None:
        has_accounts = users.count() > 0
        with self._engine.begin() as connection:
            connection.execute(delete(instance_setup))
            if has_accounts:
                return None
            token = secrets.token_urlsafe(32)
            connection.execute(insert(instance_setup).values(token_digest=_digest(token), created_at=utcnow()))
            return token

    def verify(self, token: str) -> bool:
        with self._engine.connect() as connection:
            stored = connection.execute(select(instance_setup.c.token_digest)).scalar_one_or_none()
        return stored is not None and bool(token) and compare_digest(stored, _digest(token))

    def consume(self, token: str, users: UserStore) -> None:
        if users.count() > 0:
            raise SetupCompleted("this instance already has an account")
        if not self.verify(token):
            raise InvalidSetupToken("setup token is invalid")
        with self._engine.begin() as connection:
            connection.execute(delete(instance_setup))


__all__ = ["SetupTokens"]
```

`src/agentos/accounts/login_guard.py`:

```python
"""Slow down password guessing per username and per client address."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import auth_login_attempts

from .clock import aware, utcnow
from .errors import LoginLocked

_SHORT_WINDOW = timedelta(minutes=15)
_LONG_WINDOW = timedelta(hours=1)
_USERNAME_SHORT_LIMIT = 5
_USERNAME_LONG_LIMIT = 10
_IP_SHORT_LIMIT = 20
_RETENTION = timedelta(days=1)


def _user_key(username: str) -> str:
    return f"user:{username.strip().lower()[:300]}"


def _ip_key(ip: str) -> str:
    return f"ip:{ip.strip()[:300]}"


class LoginGuard:
    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] = utcnow) -> None:
        self._engine = engine
        self._clock = clock

    def _failures(self, connection, key: str, since: datetime) -> list[datetime]:
        last_success = connection.execute(select(auth_login_attempts.c.occurred_at).where(
            auth_login_attempts.c.key == key, auth_login_attempts.c.succeeded.is_(True),
        ).order_by(auth_login_attempts.c.occurred_at.desc()).limit(1)).scalar_one_or_none()
        floor = max(since, aware(last_success)) if last_success is not None else since
        rows = connection.execute(select(auth_login_attempts.c.occurred_at).where(and_(
            auth_login_attempts.c.key == key, auth_login_attempts.c.succeeded.is_(False),
            auth_login_attempts.c.occurred_at > floor,
        )).order_by(auth_login_attempts.c.occurred_at)).scalars().all()
        return [aware(item) for item in rows]

    def check(self, *, username: str, ip: str) -> None:
        now = self._clock()
        retry_after = 0
        with self._engine.connect() as connection:
            user_hour = self._failures(connection, _user_key(username), now - _LONG_WINDOW)
            user_short = [item for item in user_hour if item > now - _SHORT_WINDOW]
            if len(user_hour) >= _USERNAME_LONG_LIMIT:
                retry_after = max(retry_after, int((user_hour[-1] + _LONG_WINDOW - now).total_seconds()))
            elif len(user_short) >= _USERNAME_SHORT_LIMIT:
                retry_after = max(retry_after, int((user_short[-1] + _SHORT_WINDOW - now).total_seconds()))
            ip_short = self._failures(connection, _ip_key(ip), now - _SHORT_WINDOW)
            if len(ip_short) >= _IP_SHORT_LIMIT:
                retry_after = max(retry_after, int((ip_short[-1] + _SHORT_WINDOW - now).total_seconds()))
        if retry_after > 0:
            raise LoginLocked(retry_after=retry_after)

    def record(self, *, username: str, ip: str, succeeded: bool) -> None:
        now = self._clock()
        with self._engine.begin() as connection:
            connection.execute(delete(auth_login_attempts).where(auth_login_attempts.c.occurred_at < now - _RETENTION))
            connection.execute(insert(auth_login_attempts), [
                {"key": _user_key(username), "succeeded": succeeded, "occurred_at": now},
                {"key": _ip_key(ip), "succeeded": succeeded, "occurred_at": now},
            ])


__all__ = ["LoginGuard"]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/accounts -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/accounts/setup.py src/agentos/accounts/login_guard.py tests/unit/accounts/test_setup_tokens.py tests/unit/accounts/test_login_guard.py
git commit -m "feat(accounts): gate the first admin behind a setup token and slow guessing

A fresh public URL cannot be claimed by whoever reaches it first, and
repeated wrong passwords lock the username or the client address.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `SessionSecurityService` — sessões com expiração, CSRF estável e papéis

**Files:**
- Modify: `src/agentos/api/security.py` (novas exceções)
- Create: `src/agentos/accounts/session_security.py`
- Create: `src/agentos/accounts/services.py`
- Test: `tests/unit/accounts/test_session_security.py`

**Interfaces:**
- Consumes: `PostgresSecurityService` (`persistence/postgres/security.py`), `AuthenticatedPrincipal`, `AuthenticationError`, `AuthorizationError` (`api/security.py`), `UserStore`, `UserRecord`, `SetupTokens`, `LoginGuard`.
- Produces:
  - Em `api/security.py`: `class AdminRequiredError(AuthorizationError)` e `class PasswordChangeRequiredError(AuthorizationError)`.
  - `SESSION_COOKIE = "agentos_session"`, `SESSION_TTL = timedelta(days=30)`, `TOUCH_INTERVAL = timedelta(minutes=5)`.
  - `derive_csrf_secret(encryption_key: str) -> bytes`.
  - `class SessionSecurityService(PostgresSecurityService)`:
    - `__init__(engine, *, users: UserStore, public_origin: str, csrf_secret: bytes, clock=utcnow, maximum_requests: int = 120)`
    - `requires_loopback_client = False`
    - `public_origin: str` (atributo)
    - `csrf_for(session_id: str) -> str`
    - `open_session(user: UserRecord) -> tuple[str, str]` (session_id, csrf)
    - `close_session(session_id: str) -> None`
    - `revoke_user(user_id: str, *, keep_session_id: str | None = None) -> None`
    - `authenticate`, `validate_csrf`, `authorize` sobrescritos (porta `SecurityService`).
    - Scopes do principal de sessão: `{"api"}`, mais `"admin"` para admin; `{"password_change"}` (só isso) quando `must_change_password`.
  - `@dataclass(frozen=True, slots=True) class AccountServices` com `users: UserStore`, `setup: SetupTokens`, `guard: LoginGuard`, `sessions: SessionSecurityService`.

- [ ] **Step 1: Escrever o teste que falha**

`tests/unit/accounts/test_session_security.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

from agentos.accounts.session_security import SessionSecurityService, derive_csrf_secret
from agentos.accounts.store import UserStore
from agentos.api.security import (
    AdminRequiredError, AuthenticatedPrincipal, AuthenticationError, AuthorizationError, PasswordChangeRequiredError,
)
from agentos.persistence.postgres.schema import metadata, security_sessions

ORIGIN = "https://orin.example.com"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def world(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    users = UserStore(engine)
    clock = Clock()
    sessions = SessionSecurityService(engine, users=users, public_origin=ORIGIN, csrf_secret=derive_csrf_secret("k" * 44), clock=clock)
    admin = users.create(username="carla", password="a long password", role="admin")
    member = users.create(username="bruno", password="a long password")
    return engine, users, sessions, clock, admin, member


def test_a_session_authenticates_with_role_scopes(world):
    _, _, sessions, _, admin, member = world
    admin_sid, _ = sessions.open_session(admin)
    member_sid, _ = sessions.open_session(member)
    admin_principal = sessions.authenticate(bearer_token=None, session_id=admin_sid)
    member_principal = sessions.authenticate(bearer_token=None, session_id=member_sid)
    assert admin_principal.user_id == "local-user"
    assert admin_principal.credential_kind == "session"
    assert admin_principal.scopes == frozenset({"api", "admin"})
    assert member_principal.scopes == frozenset({"api"})
    assert admin_principal.credential_ref == f"session:{admin_sid}"


def test_the_csrf_token_is_stable_and_bound_to_the_session(world):
    _, _, sessions, _, admin, _ = world
    sid, csrf = sessions.open_session(admin)
    other_sid, other_csrf = sessions.open_session(admin)
    assert sessions.csrf_for(sid) == csrf == sessions.csrf_for(sid)
    assert csrf != other_csrf
    principal = sessions.authenticate(bearer_token=None, session_id=sid)
    sessions.validate_csrf(principal, csrf, ORIGIN)
    for token, origin in ((other_csrf, ORIGIN), (csrf, "https://evil.example"), (csrf, None), (None, ORIGIN)):
        with pytest.raises(AuthorizationError):
            sessions.validate_csrf(principal, token, origin)


def test_unknown_or_closed_sessions_are_rejected(world):
    _, _, sessions, _, admin, _ = world
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id="nope")
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=None)
    sid, _ = sessions.open_session(admin)
    sessions.close_session(sid)
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=sid)


def test_sessions_slide_and_expire_after_thirty_idle_days(world):
    _, _, sessions, clock, admin, _ = world
    sid, _ = sessions.open_session(admin)
    clock.now += timedelta(days=29)
    sessions.authenticate(bearer_token=None, session_id=sid)
    clock.now += timedelta(days=29)
    sessions.authenticate(bearer_token=None, session_id=sid)
    clock.now += timedelta(days=30, seconds=1)
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=sid)


def test_last_seen_is_written_at_most_every_five_minutes(world):
    engine, _, sessions, clock, admin, _ = world
    sid, _ = sessions.open_session(admin)

    def last_seen():
        with engine.connect() as connection:
            return connection.execute(select(security_sessions.c.last_seen_at).where(security_sessions.c.session_id == sid)).scalar_one()

    first = last_seen()
    clock.now += timedelta(minutes=4)
    sessions.authenticate(bearer_token=None, session_id=sid)
    assert last_seen() == first
    clock.now += timedelta(minutes=2)
    sessions.authenticate(bearer_token=None, session_id=sid)
    assert last_seen() != first


def test_a_deactivated_user_loses_every_session(world):
    _, users, sessions, _, _, member = world
    sid, _ = sessions.open_session(member)
    users.update(member.user_id, active=False)
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=sid)


def test_a_temporary_password_only_grants_the_password_change_scope(world):
    _, users, sessions, _, _, member = world
    users.set_password(member.user_id, "temporary-123", temporary=True)
    sid, _ = sessions.open_session(users.get(member.user_id))
    principal = sessions.authenticate(bearer_token=None, session_id=sid)
    assert principal.scopes == frozenset({"password_change"})
    with pytest.raises(PasswordChangeRequiredError):
        sessions.authorize(principal, action="conversation.read", resource_id=None, purpose="conversation.read")


def test_admin_actions_require_the_admin_scope(world):
    _, _, sessions, _, admin, member = world
    member_principal = sessions.authenticate(bearer_token=None, session_id=sessions.open_session(member)[0])
    admin_principal = sessions.authenticate(bearer_token=None, session_id=sessions.open_session(admin)[0])
    with pytest.raises(AdminRequiredError):
        sessions.authorize(member_principal, action="admin.users", resource_id=None, purpose="admin.users.list")
    sessions.authorize(admin_principal, action="admin.users", resource_id=None, purpose="admin.users.list")
    sessions.authorize(member_principal, action="conversation.read", resource_id=None, purpose="conversation.read")


def test_revoke_user_can_keep_the_current_session(world):
    _, _, sessions, _, admin, _ = world
    keep, _ = sessions.open_session(admin)
    drop, _ = sessions.open_session(admin)
    sessions.revoke_user(admin.user_id, keep_session_id=keep)
    sessions.authenticate(bearer_token=None, session_id=keep)
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=drop)


def test_personal_access_tokens_still_work(world):
    _, _, sessions, _, _, _ = world
    sessions.add_pat("pat-token", AuthenticatedPrincipal("local-user", "pat-1", frozenset({"api"})))
    principal = sessions.authenticate(bearer_token="pat-token", session_id=None)
    assert principal.credential_kind == "pat"
    sessions.validate_csrf(principal, None, None)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/accounts/test_session_security.py -q`
Expected: FAIL com `ImportError` (`AdminRequiredError` e `agentos.accounts.session_security` não existem).

- [ ] **Step 3: Implementar**

Em `src/agentos/api/security.py`, logo depois de `class AuthorizationError`:

```python
class AdminRequiredError(AuthorizationError):
    """An instance-wide action asked by a profile that is not an admin."""


class PasswordChangeRequiredError(AuthorizationError):
    """A profile signed in with a temporary password and must replace it first."""
```

`src/agentos/accounts/session_security.py`:

```python
"""Browser sessions for server mode on top of the durable security adapter.

Differences from ``PostgresSecurityService``: sessions expire (30 days,
sliding), the CSRF token is derived from the session id so every tab gets the
same one from ``/v1/auth/me``, the request Origin must be the public URL, and
scopes come from the live account (role, active, temporary password) on every
request rather than from whatever was stored when the session opened.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from typing import Callable

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.security import (
    AdminRequiredError, AuthenticatedPrincipal, AuthenticationError, AuthorizationError, PasswordChangeRequiredError,
)
from agentos.persistence.postgres.schema import security_sessions
from agentos.persistence.postgres.security import PostgresSecurityService

from .clock import aware, utcnow
from .store import UserRecord, UserStore

SESSION_COOKIE = "agentos_session"
SESSION_TTL = timedelta(days=30)
TOUCH_INTERVAL = timedelta(minutes=5)


def derive_csrf_secret(encryption_key: str) -> bytes:
    return sha256(b"orin-session-csrf:" + encryption_key.encode("utf-8")).digest()


class SessionSecurityService(PostgresSecurityService):
    requires_loopback_client = False

    def __init__(self, engine: Engine, *, users: UserStore, public_origin: str, csrf_secret: bytes, clock: Callable[[], datetime] = utcnow, maximum_requests: int = 120) -> None:
        super().__init__(engine, maximum_requests=maximum_requests)
        self._users = users
        self.public_origin = public_origin
        self._csrf_secret = csrf_secret
        self._clock = clock

    def csrf_for(self, session_id: str) -> str:
        return hmac.new(self._csrf_secret, session_id.encode("utf-8"), sha256).hexdigest()

    def open_session(self, user: UserRecord) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf = self.csrf_for(session_id)
        now = self._clock()
        with self._engine.begin() as connection:
            connection.execute(insert(security_sessions).values(
                session_id=session_id, user_id=user.user_id, credential_ref=f"session:{session_id}",
                csrf_digest=self._digest(csrf), scopes=["api"], revoked=False,
                created_at=now, expires_at=now + SESSION_TTL, last_seen_at=now,
            ))
        return session_id, csrf

    def close_session(self, session_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(update(security_sessions).where(security_sessions.c.session_id == session_id).values(revoked=True))

    def revoke_user(self, user_id: str, *, keep_session_id: str | None = None) -> None:
        condition = security_sessions.c.user_id == user_id
        if keep_session_id is not None:
            condition = condition & (security_sessions.c.session_id != keep_session_id)
        with self._engine.begin() as connection:
            connection.execute(update(security_sessions).where(condition).values(revoked=True))

    def authenticate(self, *, bearer_token: str | None, session_id: str | None) -> AuthenticatedPrincipal:
        if bearer_token:
            return super().authenticate(bearer_token=bearer_token, session_id=None)
        if not session_id:
            raise AuthenticationError("credential is required")
        now = self._clock()
        with self._engine.connect() as connection:
            row = connection.execute(select(security_sessions).where(security_sessions.c.session_id == session_id)).mappings().first()
        if row is None or row["revoked"] or row["expires_at"] is None or aware(row["expires_at"]) <= now:
            raise AuthenticationError("session is invalid")
        user = self._users.get(str(row["user_id"]))
        if user is None or not user.active:
            raise AuthenticationError("session is invalid")
        last_seen = aware(row["last_seen_at"]) if row["last_seen_at"] is not None else None
        if last_seen is None or now - last_seen >= TOUCH_INTERVAL:
            with self._engine.begin() as connection:
                connection.execute(update(security_sessions).where(security_sessions.c.session_id == session_id).values(
                    last_seen_at=now, expires_at=now + SESSION_TTL,
                ))
        if user.must_change_password:
            scopes = frozenset({"password_change"})
        else:
            scopes = frozenset({"api", "admin"} if user.is_admin else {"api"})
        return AuthenticatedPrincipal(user.user_id, str(row["credential_ref"]), scopes, "session", False, session_id=session_id)

    def validate_csrf(self, principal: AuthenticatedPrincipal, token: str | None, origin: str | None) -> None:
        if principal.credential_kind != "session":
            return
        if origin != self.public_origin or not token or not compare_digest(token, self.csrf_for(principal.session_id or "")):
            raise AuthorizationError("csrf validation failed")

    def authorize(self, principal: AuthenticatedPrincipal, *, action: str, resource_id: str | None, purpose: str) -> None:
        if "password_change" in principal.scopes and "api" not in principal.scopes:
            raise PasswordChangeRequiredError("password change required")
        if action.startswith("admin.") and "admin" not in principal.scopes:
            raise AdminRequiredError("admin required")
        super().authorize(principal, action=action, resource_id=resource_id, purpose=purpose)


__all__ = ["SESSION_COOKIE", "SESSION_TTL", "SessionSecurityService", "TOUCH_INTERVAL", "derive_csrf_secret"]
```

`src/agentos/accounts/services.py`:

```python
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/accounts -q`
Expected: PASS.

Run: `python -m pytest tests/unit -q -k security`
Expected: PASS (as exceções novas só acrescentam subclasses).

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api/security.py src/agentos/accounts/session_security.py src/agentos/accounts/services.py tests/unit/accounts/test_session_security.py
git commit -m "feat(accounts): expiring browser sessions with a per-session csrf token

The token is derived from the session id so every open tab reads the same
one, the Origin must be the public URL, and role, deactivation and a
temporary password take effect on the very next request.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Rotas de autenticação e de administração de perfis

**Files:**
- Create: `src/agentos/api/auth_routes.py`
- Modify: `src/agentos/api/gateway.py` (`ApiServices.__init__` em `:297-342`; handlers em `create_app` depois de `:362`; `principal_for` em `:439-450`; chamada de registro no fim de `create_app`, antes do `return app`)
- Test: `tests/integration/api/test_auth_routes.py`

**Interfaces:**
- Consumes: `AccountServices`, `SessionSecurityService.open_session/close_session/revoke_user/csrf_for`, `UserStore`, `SetupTokens`, `LoginGuard`, `InstanceCapabilities`, `CapabilityUnavailable`, `AccountError`, `AdminRequiredError`, `PasswordChangeRequiredError`, `burn_password_check`, `verify_password`, `validate_password`, `normalize_username`, `generate_temporary_password`, `SESSION_COOKIE`, `SESSION_TTL`.
- Produces:
  - `ApiServices(..., accounts: AccountServices | None = None, capabilities: InstanceCapabilities | None = None, public_origin: str | None = None)`; `services.capabilities` nunca é `None` depois do `__init__` (padrão = `for_mode(LOCAL)`).
  - `principal_for(request, *, mutable=False, allow_pending_password=False)`: levanta `PasswordChangeRequiredError` para principal com só `password_change`, a menos que `allow_pending_password=True`.
  - Handlers: `AccountError` → envelope com o status/código da exceção; `AdminRequiredError` → `403 AUTHORIZATION admin_required`; `PasswordChangeRequiredError` → `403 AUTHORIZATION password_change_required`; `CapabilityUnavailable` → `404 NOT_FOUND capability_unavailable`.
  - `register_auth_routes(app: FastAPI, services: ApiServices, principal_for: Callable[..., AuthenticatedPrincipal]) -> None`, registrando:
    - `GET /v1/auth/me` → `200 {"user", "csrf_token", "capabilities"}`
    - `POST /v1/auth/setup` → `201 {"user", "csrf_token", "capabilities"}` + cookie
    - `POST /v1/auth/login` → `200 {"user", "csrf_token", "capabilities"}` + cookie
    - `POST /v1/auth/logout` → `204` e apaga o cookie
    - `POST /v1/auth/password` → `200 {"user", "csrf_token", "capabilities"}`
    - `GET /v1/admin/users` → `200 {"items": [user...]}`
    - `POST /v1/admin/users` → `201 {"user", "temporary_password"}`
    - `PATCH /v1/admin/users/{user_id}` → `200 {"user"}`
    - `POST /v1/admin/users/{user_id}/reset-password` → `200 {"user", "temporary_password"}`
  - Respostas com senha provisória ou CSRF levam `Cache-Control: no-store`.

- [ ] **Step 1: Escrever o teste que falha**

`tests/integration/api/test_auth_routes.py`:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/integration/api/test_auth_routes.py -q`
Expected: FAIL com `TypeError: ApiServices.__init__() got an unexpected keyword argument 'accounts'`.

- [ ] **Step 3: Implementar**

Em `src/agentos/api/gateway.py`:

1. Imports, junto aos existentes:

```python
from agentos.accounts.errors import AccountError
from agentos.configuration.capabilities import CapabilityUnavailable, InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from .security import AdminRequiredError, PasswordChangeRequiredError
```

(`AdminRequiredError` e `PasswordChangeRequiredError` entram na linha que já importa de `.security`.)

2. `ApiServices.__init__`: acrescente os parâmetros `accounts: object | None = None`, `capabilities: InstanceCapabilities | None = None`, `public_origin: str | None = None`, e no corpo:

```python
        self.accounts = accounts
        self.capabilities = capabilities or InstanceCapabilities.for_mode(RuntimeMode.LOCAL)
        self.public_origin = public_origin
```

3. Handlers, logo depois do handler de `RateLimitError`:

```python
    @app.exception_handler(AccountError)
    async def account_error(_: Request, exc: AccountError) -> JSONResponse:
        return _error(exc.status, exc.category, exc.code, retryable=exc.retryable, retry_after=exc.retry_after)

    @app.exception_handler(AdminRequiredError)
    async def admin_required(_: Request, __: AdminRequiredError) -> JSONResponse:
        return _error(403, "AUTHORIZATION", "admin_required", retryable=False)

    @app.exception_handler(PasswordChangeRequiredError)
    async def password_change_required(_: Request, __: PasswordChangeRequiredError) -> JSONResponse:
        return _error(403, "AUTHORIZATION", "password_change_required", retryable=False)

    @app.exception_handler(CapabilityUnavailable)
    async def capability_unavailable(_: Request, __: CapabilityUnavailable) -> JSONResponse:
        return _error(404, "NOT_FOUND", "capability_unavailable", retryable=False)
```

4. `principal_for`:

```python
    def principal_for(request: Request, *, mutable: bool = False, allow_pending_password: bool = False) -> AuthenticatedPrincipal:
        if getattr(services.security, "requires_loopback_client", False):
            client_host = request.client.host if request.client is not None else None
            if not _is_loopback_client(client_host):
                raise AuthenticationError("loopback access is required")
        authorization = request.headers.get("authorization", "")
        bearer = authorization[7:] if authorization.lower().startswith("bearer ") else None
        principal = services.security.authenticate(bearer_token=bearer, session_id=request.cookies.get("agentos_session"))
        scopes = getattr(principal, "scopes", frozenset())
        # Checked here rather than only in authorize(): a route that forgets to
        # call authorize() must still not serve a profile holding a temporary
        # password.
        if "password_change" in scopes and "api" not in scopes and not allow_pending_password:
            raise PasswordChangeRequiredError("password change required")
        if mutable:
            services.security.validate_csrf(principal, request.headers.get("x-csrf-token"), request.headers.get("origin"))
        return principal
```

5. No fim de `create_app`, imediatamente antes de `return app`:

```python
    from .auth_routes import register_auth_routes
    register_auth_routes(app, services, principal_for)
```

(Se `create_app` não terminar com `return app` diretamente, coloque a chamada antes das rotas genéricas `/v1/{resource}` registradas em `:1677-1681`, para que `/v1/auth/me` e `/v1/admin/users` não sejam capturadas por elas. Confira com `grep -n 'f"/v1/{resource}' src/agentos/api/gateway.py` e registre antes dessa linha.)

`src/agentos/api/auth_routes.py`:

```python
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/integration/api/test_auth_routes.py -q`
Expected: PASS.

Run: `python -m pytest tests/unit tests/integration -q -x`
Expected: PASS (o modo local não mudou).

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api/auth_routes.py src/agentos/api/gateway.py tests/integration/api/test_auth_routes.py
git commit -m "feat(api): sign-in, first-admin setup and profile administration routes

Anonymous routes accept only the public Origin, failures never reveal which
usernames exist, and a temporary password confines a profile to /v1/auth
until it is replaced.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Composição do modo servidor, frontend `session` e token de setup no boot

**Files:**
- Modify: `src/agentos/bootstrap/production.py` (`compose_production_services` em `:243-302`, `create_production_app` em `:305-375`, `_mount_local_frontend` em `:379-416`)
- Modify: `src/agentos/api/asgi.py`
- Modify: `src/agentos/launcher/internal.py:22-39` (`run_backend`)
- Test: `tests/integration/api/test_server_composition.py`
- Test: `tests/unit/launcher/test_backend_proxy_headers.py`

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces:
  - `compose_production_services(engine, *, localhost_trust_enabled=False, activity_cursor_secret=None, multi_agent_coordinator=None, mode: RuntimeMode = RuntimeMode.LOCAL, public_origin: str | None = None) -> ApiServices`. No modo `server`, `security` é um `SessionSecurityService`, `accounts` é um `AccountServices`, `capabilities = for_mode(SERVER)` e `public_origin` vem preenchido.
  - `_mount_frontend(app, directory, *, auth_mode: str)` (substitui `_mount_local_frontend`; `auth_mode` é `"loopback"` ou `"session"`).
  - No startup do app, com `services.accounts` presente, o token de setup é emitido e escrito no log `orin.setup` (nível WARNING) e em `sys.stderr`, num bloco que começa com a linha `ORIN SETUP TOKEN`.
  - `run_backend()` liga `proxy_headers=True` e `forwarded_allow_ips=<ORIN_TRUSTED_PROXIES>` apenas quando `ORIN_MODE=server` e `ORIN_TRUSTED_PROXIES` não está vazio.

- [ ] **Step 1: Escrever os testes que falham**

`tests/integration/api/test_server_composition.py`:

```python
import logging

import pytest
from fastapi.testclient import TestClient

from agentos.bootstrap.production import ProductionSettings, compose_production_services, create_production_app
from agentos.configuration.mode import RuntimeMode
from agentos.installation import reset_cached_paths
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.sqlite import create_local_engine

KEY = "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA="
ORIGIN = "https://orin.test"


@pytest.fixture()
def server_app(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)
    reset_cached_paths()
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<html><head><meta name="agentos-auth-mode" content=""></head><body></body></html>', encoding="utf-8")
    url = f"sqlite:///{tmp_path / 'orin.db'}"
    engine = create_local_engine(url)
    upgrade(engine)
    settings = ProductionSettings(DATABASE_URL=url, ORIN_MODE="server", ORIN_PUBLIC_URL=ORIGIN, WEB_DIST_DIR=str(web), AGENTOS_ENV="production")
    services = compose_production_services(engine, mode=RuntimeMode.SERVER, public_origin=settings.public_origin)
    yield create_production_app(settings, services=services), services
    reset_cached_paths()


def test_server_mode_composes_accounts_and_server_capabilities(server_app):
    _, services = server_app
    assert services.accounts is not None
    assert services.security is services.accounts.sessions
    assert services.capabilities.user_admin and not services.capabilities.shell
    assert services.public_origin == ORIGIN


def test_server_mode_serves_the_web_client_in_session_mode(server_app):
    app, _ = server_app
    with TestClient(app, base_url=ORIGIN) as api:
        page = api.get("/")
    assert 'content="session"' in page.text


def test_startup_prints_a_setup_token_that_works(server_app, caplog):
    app, services = server_app
    with caplog.at_level(logging.WARNING, logger="orin.setup"):
        with TestClient(app, base_url=ORIGIN) as api:
            assert api.get("/v1/auth/me").json()["error"]["code"] == "setup_required"
    lines = [record.getMessage() for record in caplog.records if record.name == "orin.setup"]
    token = next(line.split(":", 1)[1].strip() for line in lines if line.startswith("token:"))
    assert services.accounts.setup.verify(token)


def test_local_mode_composition_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)
    reset_cached_paths()
    engine = create_local_engine(f"sqlite:///{tmp_path / 'orin.db'}")
    upgrade(engine)
    services = compose_production_services(engine, localhost_trust_enabled=True)
    assert services.accounts is None
    assert services.capabilities.shell is True
    assert getattr(services.security, "requires_loopback_client", False) is True
    reset_cached_paths()
```

`tests/unit/launcher/test_backend_proxy_headers.py`:

```python
import uvicorn

from agentos.launcher import internal


def _capture(monkeypatch):
    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: captured.update(kwargs))
    return captured


def test_local_mode_never_trusts_forwarded_headers(monkeypatch):
    monkeypatch.delenv("ORIN_MODE", raising=False)
    monkeypatch.setenv("ORIN_TRUSTED_PROXIES", "10.0.0.2")
    captured = _capture(monkeypatch)
    internal.run_backend()
    assert captured["proxy_headers"] is False and captured["forwarded_allow_ips"] is None


def test_server_mode_trusts_only_the_configured_proxies(monkeypatch):
    monkeypatch.setenv("ORIN_MODE", "server")
    monkeypatch.setenv("ORIN_TRUSTED_PROXIES", "172.18.0.0/16")
    captured = _capture(monkeypatch)
    internal.run_backend()
    assert captured["proxy_headers"] is True and captured["forwarded_allow_ips"] == "172.18.0.0/16"


def test_server_mode_without_proxies_keeps_the_peer_address(monkeypatch):
    monkeypatch.setenv("ORIN_MODE", "server")
    monkeypatch.delenv("ORIN_TRUSTED_PROXIES", raising=False)
    captured = _capture(monkeypatch)
    internal.run_backend()
    assert captured["proxy_headers"] is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/integration/api/test_server_composition.py tests/unit/launcher/test_backend_proxy_headers.py -q`
Expected: FAIL com `TypeError: compose_production_services() got an unexpected keyword argument 'mode'` e asserções de proxy falhando.

- [ ] **Step 3: Implementar**

Em `src/agentos/bootstrap/production.py`:

1. Imports:

```python
import logging
import sys

from agentos.accounts.login_guard import LoginGuard
from agentos.accounts.services import AccountServices
from agentos.accounts.session_security import SessionSecurityService, derive_csrf_secret
from agentos.accounts.setup import SetupTokens
from agentos.accounts.store import UserStore
from agentos.configuration.capabilities import InstanceCapabilities
```

2. `compose_production_services`: troque a assinatura e o começo do corpo para:

```python
def compose_production_services(engine: Engine, *, localhost_trust_enabled: bool = False, activity_cursor_secret: str | None = None, multi_agent_coordinator=None, mode: RuntimeMode = RuntimeMode.LOCAL, public_origin: str | None = None) -> ApiServices:
```

e, antes do `return ApiServices(...)`:

```python
    capabilities = InstanceCapabilities.for_mode(mode)
    accounts: AccountServices | None = None
    if mode is RuntimeMode.SERVER:
        if not public_origin:
            raise ValueError("server mode requires the public origin")
        users = UserStore(engine)
        encryption_key = os.getenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "").strip() or os.getenv("APP_MASTER_KEY", "").strip()
        sessions = SessionSecurityService(engine, users=users, public_origin=public_origin, csrf_secret=derive_csrf_secret(encryption_key))
        accounts = AccountServices(users=users, setup=SetupTokens(engine), guard=LoginGuard(engine), sessions=sessions)
        security = sessions
    else:
        security = LoopbackSecurityService() if localhost_trust_enabled else PostgresSecurityService(engine)
```

e, no `ApiServices(...)`, troque `security=LoopbackSecurityService() if localhost_trust_enabled else PostgresSecurityService(engine),` por `security=security,` e acrescente:

```python
        accounts=accounts,
        capabilities=capabilities,
        public_origin=public_origin,
```

3. `create_production_app`: troque

```python
    if settings.LOCALHOST_TRUST_ENABLED:
        _mount_local_frontend(app, settings.WEB_DIST_DIR)
```

por

```python
    if settings.ORIN_MODE is RuntimeMode.SERVER:
        _mount_frontend(app, settings.WEB_DIST_DIR, auth_mode="session")
    elif settings.LOCALHOST_TRUST_ENABLED:
        _mount_frontend(app, settings.WEB_DIST_DIR, auth_mode="loopback")

    @app.on_event("startup")
    async def _announce_setup_token() -> None:
        accounts = getattr(services, "accounts", None) if services is not None else None
        if accounts is None:
            return
        token = accounts.setup.issue_if_needed(accounts.users)
        if token is None:
            return
        # The only place the raw token exists. The operator reads it from the
        # container log and pastes it into /setup to create the first admin.
        logger = logging.getLogger("orin.setup")
        lines = ("ORIN SETUP TOKEN", f"token: {token}", "Abra /setup na URL pública e cole este token para criar o primeiro admin.")
        for line in lines:
            logger.warning(line)
        sys.stderr.write("\n" + "\n".join(lines) + "\n\n")
        sys.stderr.flush()
```

4. Renomeie `_mount_local_frontend(app, directory)` para `_mount_frontend(app, directory, *, auth_mode: str)`, troque o docstring para `"""Serve the built SPA, stamped with how this instance authenticates the browser."""` e, em `document()`, substitua o `replace(...)` por:

```python
        return index.read_text(encoding="utf-8").replace(
            'name="agentos-auth-mode" content=""',
            f'name="agentos-auth-mode" content="{auth_mode}"',
            1,
        )
```

Depois rode `grep -rn "_mount_local_frontend" src tests` e atualize qualquer referência restante para `_mount_frontend(..., auth_mode="loopback")`.

5. Em `_start_configured_omniroute`, no começo:

```python
        if services is not None and not services.capabilities.omniroute:
            return
```

`src/agentos/api/asgi.py`:

```python
"""Production ASGI entry point composed with durable adapters."""

from agentos.bootstrap.production import ProductionSettings, compose_production_services, create_production_app
from agentos.persistence.sqlite import create_local_engine

settings = ProductionSettings()
engine = create_local_engine(settings.DATABASE_URL)
app = create_production_app(
    settings,
    services=compose_production_services(
        engine,
        localhost_trust_enabled=settings.LOCALHOST_TRUST_ENABLED,
        activity_cursor_secret=settings.AGENTOS_ACTIVITY_CURSOR_SECRET.get_secret_value() if settings.AGENTOS_ACTIVITY_CURSOR_SECRET else None,
        mode=settings.ORIN_MODE,
        public_origin=settings.public_origin,
    ),
)
```

`src/agentos/launcher/internal.py`, `run_backend`:

```python
def run_backend() -> int:
    """HTTP, SSE, and the built web interface. Never calls a provider."""
    import uvicorn

    from agentos.configuration.mode import RuntimeMode, current_mode

    host = os.getenv("ORIN_BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("ORIN_BACKEND_PORT", str(DEFAULT_PORT)))
    trusted = os.getenv("ORIN_TRUSTED_PROXIES", "").strip()
    # The local profile authenticates the loopback peer itself, so it never
    # trusts forwarded headers. A server behind a reverse proxy trusts them
    # only from the proxy addresses the operator listed.
    behind_proxy = current_mode() is RuntimeMode.SERVER and bool(trusted)
    uvicorn.run(
        "agentos.api.asgi:app",
        host=host,
        port=port,
        proxy_headers=behind_proxy,
        forwarded_allow_ips=trusted if behind_proxy else None,
        log_level=os.getenv("ORIN_LOG_LEVEL", "info"),
        access_log=os.getenv("ORIN_ACCESS_LOG", "").strip().lower() in {"1", "true", "yes"},
    )
    return 0
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/integration/api/test_server_composition.py tests/unit/launcher/test_backend_proxy_headers.py -q`
Expected: PASS.

Run: `python -m pytest tests/unit tests/integration -q -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/bootstrap/production.py src/agentos/api/asgi.py src/agentos/launcher/internal.py tests/integration/api/test_server_composition.py tests/unit/launcher/test_backend_proxy_headers.py
git commit -m "feat(server): compose accounts for server mode and print the setup token

The web client is stamped with session auth, forwarded headers are trusted
only from configured proxies, and the one-time setup token reaches the
operator through the log.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Travas do gateway por capacidade

**Files:**
- Modify: `src/agentos/api/gateway.py` (rotas em `:533` `/v1/workspaces/inspect`, `:810` `/v1/conversations/{id}/workspace/inspect`, `:877` `/files/{path}/open`, `:1406-1484` OmniRoute, `:1537` e `:1545` instalação)
- Test: `tests/integration/api/test_server_capability_gates.py`

**Interfaces:**
- Consumes: `services.capabilities.require(name)`, handler de `CapabilityUnavailable` (Task 6).
- Produces: no modo server, `POST .../files/{path}/open`, `DELETE /v1/installation/versions/{v}`, `POST /v1/installation/update` e todas as rotas `/v1/providers/omniroute/*` respondem `404 capability_unavailable`; as rotas de inspeção com `path: null` respondem `{"dialog_unavailable": true}` sem tocar no seletor nativo.

- [ ] **Step 1: Escrever o teste que falha**

`tests/integration/api/test_server_capability_gates.py`:

```python
import pytest
from fastapi.testclient import TestClient

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode

HEADERS = {"Authorization": "Bearer pat", "Idempotency-Key": "k-1"}


@pytest.fixture()
def api(tmp_path):
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    services = ApiServices(security=security, workspace_root=tmp_path, capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER))
    return TestClient(create_app(services))


@pytest.mark.parametrize(("method", "path"), [
    ("post", "/v1/conversations/chat_1/files/notes.md/open"),
    ("post", "/v1/installation/update"),
    ("delete", "/v1/installation/versions/0.3.0"),
    ("post", "/v1/providers/omniroute/install"),
    ("get", "/v1/providers/omniroute/install"),
    ("get", "/v1/providers/omniroute/runtime"),
    ("put", "/v1/providers/omniroute/runtime"),
    ("post", "/v1/providers/omniroute/runtime/actions"),
    ("post", "/v1/providers/omniroute/test"),
])
def test_desktop_only_routes_are_closed_in_server_mode(api, method, path):
    kwargs = {"json": {}} if method in {"post", "put"} else {}
    response = getattr(api, method)(path, headers=HEADERS, **kwargs)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "capability_unavailable"


def test_the_native_folder_dialog_never_opens_in_server_mode(api, monkeypatch):
    def explode():
        raise AssertionError("the native dialog must not open on a server")
    monkeypatch.setattr("agentos.api.gateway.choose_folder", explode)
    response = api.post("/v1/workspaces/inspect", json={"path": None}, headers=HEADERS)
    assert response.json() == {"dialog_unavailable": True}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/integration/api/test_server_capability_gates.py -q`
Expected: FAIL (as rotas respondem 200/422/500, não `capability_unavailable`).

- [ ] **Step 3: Implementar**

Em `src/agentos/api/gateway.py`:

1. Importe `Depends` (`from fastapi import Depends, FastAPI, File, Request, UploadFile`) e, dentro de `create_app`, logo depois de `principal_for`, crie a dependência:

```python
    def capability(name: str):
        """Route dependency: FastAPI calls dependencies before validating the
        body, so a closed capability answers 404 even for a malformed request."""
        def check() -> None:
            services.capabilities.require(name)
        return Depends(check)
```

2. Acrescente `dependencies=[capability(...)]` ao decorador de cada rota fechada, sem mexer no corpo da função:
   - `@app.post("/v1/conversations/{conversation_id}/files/{path:path}/open", dependencies=[capability("open_in_desktop_app")])` (`:877`)
   - `@app.delete("/v1/installation/versions/{version}", dependencies=[capability("ui_updater")])` (`:1537`)
   - `@app.post("/v1/installation/update", dependencies=[capability("ui_updater")])` (`:1545`)
   - as seis rotas `/v1/providers/omniroute/...` (`test` `:1406`, `install` POST `:1435` e GET `:1450`, `runtime` GET `:1463` e PUT `:1470`, `runtime/actions` `:1477`) com `dependencies=[capability("omniroute")]`.

   Preserve os argumentos que cada decorador já tem (por exemplo `status_code=...`).

3. Em `inspect_new_workspace` (`:533`) e `inspect_conversation_workspace` (`:810`), troque o bloco `if chosen is None:` por:

```python
        if chosen is None:
            client_host = request.client.host if request.client is not None else None
            if not services.capabilities.host_folders or not _is_loopback_client(client_host):
                return JSONResponse({"dialog_unavailable": True}, status_code=200)
            result = await run_in_threadpool(choose_folder)
            if not result.available:
                return JSONResponse({"dialog_unavailable": True}, status_code=200)
            if result.cancelled or not result.path:
                return JSONResponse({"cancelled": True}, status_code=200)
            chosen = result.path
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/integration/api/test_server_capability_gates.py -q`
Expected: PASS.

Run: `python -m pytest tests/unit tests/integration -q -x -k "omniroute or installation or workspace or gateway"`
Expected: PASS (modo local inalterado).

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api/gateway.py tests/integration/api/test_server_capability_gates.py
git commit -m "feat(api): close desktop-only routes when the instance runs as a server

Opening files in a desktop app, the in-app updater, OmniRoute and the native
folder dialog have no meaning on a VPS; they answer capability_unavailable
before touching the host.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Travas no runtime — terminal, diagnóstico, MCP stdio e hooks

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py:972-1010` (`_with_file_diagnostic`)
- Modify: `src/agentos/agentic/session.py:560-640` (`TurnSession.__init__`) e `:1004-1037` (`_toolset`)
- Modify: `src/agentos/mcp/service.py:68-125, 143-232, 252-300`
- Modify: `src/agentos/plugins/hook_engine.py:25-60`
- Modify: `src/agentos/workers/chat.py:315-360, 880-925, 996-1015`
- Modify: `src/agentos/bootstrap/production.py` (`compose_production_services`)
- Test: `tests/unit/agentic/test_agent_tools.py` (acrescentar)
- Test: `tests/unit/agentic/test_turn_session.py` (acrescentar)
- Test: `tests/unit/mcp/test_stdio_capability.py`
- Test: `tests/unit/plugins/test_hook_engine_disabled.py`
- Test: `tests/unit/workers/test_chat_worker_capabilities.py`

**Interfaces:**
- Consumes: `InstanceCapabilities`, `CapabilityUnavailable`, `current_mode`.
- Produces:
  - `AgentToolset(enable_terminal=False)` também desliga o diagnóstico pós-escrita.
  - `TurnSession(..., enable_terminal: bool = True)` repassa para `AgentToolset`.
  - `McpServerService(engine, *, allow_stdio: bool = True)`: com `allow_stdio=False`, `propose`, `approve`, `test` e `activate_after_authorization` de um servidor `stdio` levantam `CapabilityUnavailable("mcp_stdio")`, e `active_servers` omite servidores `stdio`.
  - `HookEngine(*, executor=None, enabled: bool = True)`: desligado, `register` não guarda nada e `dispatch` devolve `()`.
  - `ChatWorker(..., capabilities: InstanceCapabilities | None = None)`; padrão `for_mode(current_mode())`; atributo `self._capabilities`.

- [ ] **Step 1: Escrever os testes que falham**

Em `tests/unit/agentic/test_agent_tools.py`, acrescente:

```python
def test_without_terminal_there_are_no_shell_tools_and_no_write_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_server"), enable_terminal=False)
    names = {item.name for item in tools.definitions()}
    assert not names & {"run_command", "read_process_output", "stop_process", "verify_project"}

    def forbid(*args, **kwargs):
        raise AssertionError("no process may start without the terminal capability")
    monkeypatch.setattr(agent_tools, "file_diagnostic_command", lambda path, project_root: "ruff check x.py")
    monkeypatch.setattr(agent_tools.subprocess, "run", forbid)
    outcome = tools.invoke("write_file", {"path": "x.py", "content": "print('ok')\n"})
    assert outcome.status == "succeeded"
    assert "diagnóstico automático" not in outcome.content
```

Em `tests/unit/agentic/test_turn_session.py`, acrescente (reaproveitando `RecordingStore` e `MemoryAgentsStore` do próprio arquivo):

```python
def test_a_session_without_terminal_offers_no_shell_tools(tmp_path: Path) -> None:
    session = TurnSession(
        turn=dict(TURN), store=RecordingStore(), agents_store=MemoryAgentsStore(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_terminal=False,
    )
    names = {item.name for item in session._toolset(subagents=False).definitions()}
    assert "run_command" not in names and "verify_project" not in names
```

`tests/unit/mcp/test_stdio_capability.py` (crie `tests/unit/mcp/__init__.py` se não existir):

```python
import pytest
from sqlalchemy import create_engine, update

from agentos.configuration.capabilities import CapabilityUnavailable
from agentos.mcp.service import McpServerService
from agentos.persistence.postgres.schema import mcp_servers, metadata


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    return engine


def test_stdio_servers_cannot_be_proposed_without_the_capability(engine):
    service = McpServerService(engine, allow_stdio=False)
    with pytest.raises(CapabilityUnavailable):
        service.propose({"user_id": "u1", "display_name": "Files", "transport": "stdio", "command": "npx"})
    remote = service.propose({"user_id": "u1", "display_name": "Remote", "transport": "http", "url": "https://mcp.example.com/mcp"})
    assert remote["transport"] == "http"


def test_existing_stdio_servers_never_reach_a_turn_or_connect(engine):
    permissive = McpServerService(engine)
    server = permissive.propose({"user_id": "u1", "display_name": "Files", "transport": "stdio", "command": "npx"})
    with engine.begin() as connection:
        connection.execute(update(mcp_servers).values(state="active"))
    closed = McpServerService(engine, allow_stdio=False)
    assert closed.active_servers("u1") == []

    def connect(config, secrets):
        raise AssertionError("must not spawn")
    with pytest.raises(CapabilityUnavailable):
        closed.approve(user_id="u1", server_id=server["server_id"], secrets={}, connect=connect)
    with pytest.raises(CapabilityUnavailable):
        closed.test("u1", server["slug"], connect)
    with pytest.raises(CapabilityUnavailable):
        closed.activate_after_authorization("u1", server["server_id"], connect)
    assert len(permissive.active_servers("u1")) == 1
```

`tests/unit/plugins/test_hook_engine_disabled.py` (crie `tests/unit/plugins/__init__.py` se não existir):

```python
from pathlib import Path
from types import SimpleNamespace

from agentos.plugins.hook_engine import HookEngine


class Executor:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        raise AssertionError("a disabled engine must not execute hooks")


def test_a_disabled_engine_registers_and_runs_nothing(tmp_path: Path):
    executor = Executor()
    engine = HookEngine(executor=executor, enabled=False)
    hook = SimpleNamespace(event="PreToolUse", matcher="", command="echo hi")
    engine.register(user_id="u1", plugin_id="p1", install_path=tmp_path, hooks=[hook], enabled=True)
    assert engine.dispatch(user_id="u1", event="PreToolUse", payload={"tool_name": "write_file"}) == ()
    assert executor.calls == 0
    assert engine.enabled is False
```

`tests/unit/workers/test_chat_worker_capabilities.py` (crie `tests/unit/workers/__init__.py` se não existir):

```python
from sqlalchemy import create_engine

from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import metadata
from agentos.workers.chat import ChatWorker


def _store(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    return PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "cursor-secret"))


def test_a_server_worker_closes_hooks_and_the_terminal(tmp_path):
    worker = ChatWorker(_store(tmp_path), capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER))
    assert worker._capabilities.shell is False
    assert worker._hook_engine.enabled is False


def test_a_worker_defaults_to_the_process_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_MODE", "server")
    assert ChatWorker(_store(tmp_path))._capabilities.shell is False
    monkeypatch.setenv("ORIN_MODE", "local")
    assert ChatWorker(_store(tmp_path))._capabilities.shell is True
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/agentic/test_agent_tools.py tests/unit/agentic/test_turn_session.py tests/unit/mcp/test_stdio_capability.py tests/unit/plugins/test_hook_engine_disabled.py tests/unit/workers/test_chat_worker_capabilities.py -q`
Expected: FAIL (`unexpected keyword argument` para `enable_terminal`, `allow_stdio`, `enabled`, `capabilities`; diagnóstico ainda roda).

- [ ] **Step 3: Implementar**

`src/agentos/agentic/agent_tools.py`, em `_with_file_diagnostic`, logo depois da docstring:

```python
        if not self._enable_terminal:
            # Linters configured by the project (eslint, ruff plugins) are code
            # execution. Without a terminal capability nothing runs at all.
            return outcome
```

`src/agentos/agentic/session.py`:
- Em `TurnSession.__init__`, acrescente o parâmetro `enable_terminal: bool = True` (depois de `hook_engine=None`) e, no corpo, `self.enable_terminal = bool(enable_terminal)`.
- Em `_toolset`, na chamada `AgentToolset(...)` (`:1011`), acrescente `enable_terminal=self.enable_terminal,`.

`src/agentos/mcp/service.py`:
- Import: `from agentos.configuration.capabilities import CapabilityUnavailable`.
- Construtor:

```python
class McpServerService:
    def __init__(self, engine: Engine, *, allow_stdio: bool = True) -> None:
        self.engine = engine
        self.allow_stdio = allow_stdio

    def _require_transport(self, transport: McpTransport | str) -> None:
        if not self.allow_stdio and McpTransport(transport) is McpTransport.STDIO:
            raise CapabilityUnavailable("mcp_stdio")
```

(`McpTransport` vem de `agentos/mcp/models.py`; `McpTransport.STDIO.value == "stdio"`.)
- Em `active_servers`, depois de montar `rows`, filtre: `rows = [row for row in rows if self.allow_stdio or row["transport"] != McpTransport.STDIO.value]`.
- Em `propose`, logo depois de resolver `transport`: `self._require_transport(transport)`.
- Em `approve` e `activate_after_authorization`, logo depois de `config = row_to_config(row)`: `self._require_transport(config.transport)`.
- Em `test`, logo depois de `config = row_to_config(row)`: `self._require_transport(config.transport)`.

`src/agentos/plugins/hook_engine.py`:

```python
class HookEngine:
    def __init__(self, *, executor=None, enabled: bool = True) -> None:
        self.executor = executor or HookExecutor()
        self.enabled = enabled
        self._hooks: dict[str, list[RegisteredHook]] = {}
        self._lock = RLock()

    def register(self, *, user_id: str, plugin_id: str, install_path: Path, hooks, enabled: bool) -> None:
        with self._lock:
            registry = self._hooks.setdefault(user_id, [])
            registry[:] = [item for item in registry if item.plugin_id != plugin_id]
            if not enabled or not self.enabled:
                return
            registry.extend(RegisteredHook(plugin_id, Path(install_path), item) for item in hooks)
```

e, em `dispatch`, como primeira linha: `if not self.enabled: return ()`.

`src/agentos/workers/chat.py`:
- Imports: `from agentos.configuration.capabilities import InstanceCapabilities` e `from agentos.configuration.mode import current_mode`.
- `ChatWorker.__init__`: acrescente o parâmetro `capabilities: InstanceCapabilities | None = None` e, no corpo, antes de `self._hook_engine`:

```python
        self._capabilities = capabilities or InstanceCapabilities.for_mode(current_mode())
```

e troque `self._hook_engine = HookEngine()` por `self._hook_engine = HookEngine(enabled=self._capabilities.plugin_hooks)`.
- Em `run`, troque `mcp_service = McpServerService(engine)` por `mcp_service = McpServerService(engine, allow_stdio=self._capabilities.mcp_stdio)`.
- Na construção de `TurnSession(...)` (`:913`), acrescente `enable_terminal=self._capabilities.shell,`.

`src/agentos/bootstrap/production.py`, em `compose_production_services`, troque `mcp_service = McpServerService(engine)` por `mcp_service = McpServerService(engine, allow_stdio=capabilities.mcp_stdio)` e `hook_engine = HookEngine()` por `hook_engine = HookEngine(enabled=capabilities.plugin_hooks)`. Mova a linha `capabilities = InstanceCapabilities.for_mode(mode)` (da Task 7) para o começo da função, antes de `mcp_service`.

No gateway, `McpServerService.propose` já é chamado em `/v1/mcp/servers` e via plugins; `CapabilityUnavailable` já vira 404 pelo handler da Task 6.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/agentic/test_agent_tools.py tests/unit/agentic/test_turn_session.py tests/unit/mcp/test_stdio_capability.py tests/unit/plugins/test_hook_engine_disabled.py tests/unit/workers/test_chat_worker_capabilities.py -q`
Expected: PASS.

Run: `python -m pytest tests/unit tests/integration -q -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/agent_tools.py src/agentos/agentic/session.py src/agentos/mcp/service.py src/agentos/plugins/hook_engine.py src/agentos/workers/chat.py src/agentos/bootstrap/production.py tests/unit/agentic tests/unit/mcp tests/unit/plugins tests/unit/workers
git commit -m "feat(server): keep every host process closed in server mode

The agent gets no shell tools and no post-write linters, stdio MCP servers
never spawn or reach a turn, and plugin hooks do not run until the
per-profile sandbox exists.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Workspaces gerenciados por perfil e migração de layout

**Files:**
- Modify: `src/agentos/installation/paths.py` (`OrinPaths`)
- Create: `src/agentos/installation/layout.py`
- Modify: `src/agentos/api/gateway.py` (`ApiServices.__init__` `:339`; usos de `services.workspace_root` em `:516` e `:863`)
- Modify: `src/agentos/agentic/session.py:626-630`
- Modify: `src/agentos/workers/chat.py:334`
- Modify: `src/agentos/launcher/supervisor.py:178-196` (`_step_services`)
- Test: `tests/unit/installation/test_user_paths.py`
- Test: `tests/unit/installation/test_layout_migration.py`
- Test: `tests/integration/api/test_managed_root_per_user.py`

**Interfaces:**
- Consumes: nada novo.
- Produces:
  - `OrinPaths.user_root(user_id: str) -> Path` (`<data>/users/<user_id>`; levanta `ValueError` se o id não casar `^[A-Za-z0-9_-]{1,64}$`), `OrinPaths.user_workspaces(user_id) -> Path`, `OrinPaths.user_files(user_id) -> Path`. Nenhum deles cria diretórios.
  - `LAYOUT_MARKER = ".layout-v2"`, `class LayoutMigrationError(RuntimeError)`, `migrate_data_layout(paths: OrinPaths) -> bool` (devolve `True` se moveu algo).
  - `ApiServices.managed_root_for(user_id: str) -> Path`: o `workspace_root` passado no construtor (testes) ou `orin_paths().user_workspaces(user_id)`.
  - `TurnSession` sem `workspace_root` usa `orin_paths().user_workspaces(turn["user_id"])`; `ChatWorker` sem `workspace_root` deixa de fixar a raiz global.

- [ ] **Step 1: Escrever os testes que falham**

`tests/unit/installation/test_user_paths.py` (crie `tests/unit/installation/__init__.py` se não existir):

```python
from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run")


def test_user_roots_live_under_data_users(tmp_path):
    paths = _paths(tmp_path)
    assert paths.user_root("local-user") == tmp_path / "data" / "users" / "local-user"
    assert paths.user_workspaces("usr_ab12") == tmp_path / "data" / "users" / "usr_ab12" / "workspaces"
    assert paths.user_files("usr_ab12") == tmp_path / "data" / "users" / "usr_ab12" / "files"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("user_id", ["", "../etc", "a/b", "a b", "x" * 65, "ção"])
def test_unsafe_user_ids_are_refused(tmp_path, user_id):
    with pytest.raises(ValueError):
        _paths(tmp_path).user_root(user_id)
```

`tests/unit/installation/test_layout_migration.py`:

```python
from pathlib import Path

import pytest

from agentos.installation.layout import LAYOUT_MARKER, LayoutMigrationError, migrate_data_layout
from agentos.installation.paths import OrinPaths


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run")


def test_existing_workspaces_move_to_the_local_profile(tmp_path):
    paths = _paths(tmp_path)
    (paths.data / "workspaces" / "chat_1").mkdir(parents=True)
    (paths.data / "workspaces" / "chat_1" / "notes.md").write_text("oi", encoding="utf-8")

    assert migrate_data_layout(paths) is True

    moved = paths.user_workspaces("local-user") / "chat_1" / "notes.md"
    assert moved.read_text(encoding="utf-8") == "oi"
    assert not (paths.data / "workspaces").exists()
    assert (paths.data / LAYOUT_MARKER).is_file()


def test_running_twice_does_nothing_the_second_time(tmp_path):
    paths = _paths(tmp_path)
    (paths.data / "workspaces" / "chat_1").mkdir(parents=True)
    migrate_data_layout(paths)
    assert migrate_data_layout(paths) is False


def test_a_fresh_install_just_writes_the_marker(tmp_path):
    paths = _paths(tmp_path)
    assert migrate_data_layout(paths) is False
    assert (paths.data / LAYOUT_MARKER).is_file()


def test_an_interrupted_migration_resumes(tmp_path):
    paths = _paths(tmp_path)
    (paths.data / "workspaces" / "chat_2").mkdir(parents=True)
    paths.user_workspaces("local-user").mkdir(parents=True)
    (paths.user_workspaces("local-user") / "chat_1").mkdir()  # moved before the crash
    assert migrate_data_layout(paths) is True
    assert sorted(item.name for item in paths.user_workspaces("local-user").iterdir()) == ["chat_1", "chat_2"]


def test_a_name_present_on_both_sides_stops_the_boot(tmp_path):
    paths = _paths(tmp_path)
    (paths.data / "workspaces" / "chat_1").mkdir(parents=True)
    (paths.user_workspaces("local-user") / "chat_1").mkdir(parents=True)
    with pytest.raises(LayoutMigrationError):
        migrate_data_layout(paths)
    assert not (paths.data / LAYOUT_MARKER).exists()
```

`tests/integration/api/test_managed_root_per_user.py`:

```python
from agentos.api import ApiServices
from agentos.installation import orin_paths, reset_cached_paths


def test_without_an_override_each_user_gets_their_own_managed_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path))
    reset_cached_paths()
    services = ApiServices()
    assert services.managed_root_for("local-user") == orin_paths().user_workspaces("local-user")
    assert services.managed_root_for("usr_b") != services.managed_root_for("local-user")
    reset_cached_paths()


def test_an_explicit_root_still_wins_for_tests(tmp_path):
    services = ApiServices(workspace_root=tmp_path)
    assert services.managed_root_for("anyone") == tmp_path
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/installation/test_user_paths.py tests/unit/installation/test_layout_migration.py tests/integration/api/test_managed_root_per_user.py -q`
Expected: FAIL (`AttributeError: 'OrinPaths' object has no attribute 'user_root'`, `ModuleNotFoundError: agentos.installation.layout`).

- [ ] **Step 3: Implementar**

`src/agentos/installation/paths.py`: acrescente `import re` no topo, a constante `_USER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")` depois dos imports, e dentro de `OrinPaths`, depois da propriedade `workspaces`:

```python
    def user_root(self, user_id: str) -> Path:
        """Everything a sandbox may later mount for one profile lives under here."""
        if not isinstance(user_id, str) or not _USER_ID.fullmatch(user_id):
            raise ValueError("user id is not safe to use as a directory name")
        return self.data / "users" / user_id

    def user_workspaces(self, user_id: str) -> Path:
        return self.user_root(user_id) / "workspaces"

    def user_files(self, user_id: str) -> Path:
        return self.user_root(user_id) / "files"
```

Mantenha a propriedade `workspaces` (a migração ainda precisa do caminho antigo), mas troque a docstring para `"""Legacy shared workspace root, only read by the layout migration."""`.

`src/agentos/installation/layout.py`:

```python
"""Move pre-profile data into the per-profile layout, once.

Before profiles existed every managed workspace lived in ``<data>/workspaces``
and belonged to the single loopback principal, ``local-user``. Each entry is
moved with a rename, so a crash leaves every entry either fully on the old
side or fully on the new one and the next boot resumes.
"""
from __future__ import annotations

import os
import shutil

from .paths import OrinPaths

LAYOUT_MARKER = ".layout-v2"
_LEGACY_OWNER = "local-user"


class LayoutMigrationError(RuntimeError):
    pass


def migrate_data_layout(paths: OrinPaths) -> bool:
    marker = paths.data / LAYOUT_MARKER
    if marker.is_file():
        return False
    legacy = paths.workspaces
    target = paths.user_workspaces(_LEGACY_OWNER)
    moved = False
    if legacy.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for entry in sorted(legacy.iterdir()):
            destination = target / entry.name
            if destination.exists():
                raise LayoutMigrationError(f"{entry.name} exists in both {legacy} and {target}; resolve it by hand and start again")
            try:
                os.replace(entry, destination)
            except OSError:
                shutil.move(str(entry), str(destination))
            moved = True
        legacy.rmdir()
    paths.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("users/<user_id>/workspaces\n", encoding="utf-8")
    return moved


__all__ = ["LAYOUT_MARKER", "LayoutMigrationError", "migrate_data_layout"]
```

`src/agentos/api/gateway.py`:
- Em `ApiServices.__init__`, troque `self.workspace_root = Path(workspace_root) if workspace_root is not None else orin_paths().workspaces` por:

```python
        self.workspace_root = Path(workspace_root) if workspace_root is not None else None
```

e acrescente o método na classe:

```python
    def managed_root_for(self, user_id: str) -> Path:
        """Where a profile's managed workspaces live; an explicit root (tests) wins."""
        return self.workspace_root if self.workspace_root is not None else orin_paths().user_workspaces(user_id)
```

- Em `workspace_for` (`:516`) e `conversation_workspace` (`:863`), troque `managed_root=services.workspace_root` por `managed_root=services.managed_root_for(principal.user_id)`.
- Rode `grep -rn "services.workspace_root\|\.workspace_root\b" src/agentos/api tests` e troque qualquer outro uso de leitura por `managed_root_for(...)`.

`src/agentos/agentic/session.py`, em `TurnSession.__init__`:

```python
        self.workspace = resolve_workspace(
            resolve_effective_workspace_id(turn),
            managed_root=workspace_root or orin_paths().user_workspaces(str(turn.get("user_id") or "")),
            local_root=local_root if isinstance(local_root, str) else None,
        )
```

`src/agentos/workers/chat.py:334`: troque por `self._workspace_root = workspace_root` (sem fallback global; `TurnSession` resolve por usuário).

`src/agentos/launcher/supervisor.py`: importe `from agentos.installation.layout import migrate_data_layout` e, em `_step_services`, logo depois de `apply_migrations(self.environment, self.profile, log=self.log)`:

```python
            if migrate_data_layout(self.paths):
                self.log.info("moved managed workspaces into %s", self.paths.user_workspaces("local-user"))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/installation tests/integration/api/test_managed_root_per_user.py -q`
Expected: PASS.

Run: `python -m pytest tests/unit tests/integration -q -x`
Expected: PASS. Se algum teste antigo construía `TurnSession` sem `workspace_root` e com `user_id` inválido como nome de pasta, passe `workspace_root=tmp_path` no teste (o comportamento de produção é o novo).

- [ ] **Step 5: Commit**

```bash
git add src/agentos/installation src/agentos/api/gateway.py src/agentos/agentic/session.py src/agentos/workers/chat.py src/agentos/launcher/supervisor.py tests/unit/installation tests/integration/api/test_managed_root_per_user.py
git commit -m "feat(workspaces): keep each profile's managed workspaces under its own root

Workspaces move from data/workspaces to data/users/<id>/workspaces, the
directory a per-profile sandbox will mount, and existing ones are moved to
the local profile once at boot.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Área de arquivos do perfil e vínculo de pasta no modo servidor

**Files:**
- Create: `src/agentos/profile_files/__init__.py`
- Create: `src/agentos/profile_files/binding.py`
- Create: `src/agentos/profile_files/listing.py`
- Create: `src/agentos/profile_files/archive.py`
- Create: `src/agentos/api/files_routes.py`
- Modify: `src/agentos/api/gateway.py` (inspeção e vínculo em `:533-610`, `:780-850`; handler de `ArchiveRejected`; registro das rotas)
- Create: `tests/integration/server_world.py`
- Test: `tests/unit/profile_files/test_binding.py`
- Test: `tests/unit/profile_files/test_listing.py`
- Test: `tests/unit/profile_files/test_archive.py`
- Test: `tests/integration/api/test_profile_files.py`

**Interfaces:**
- Consumes: `OrinPaths.user_files`, `FolderInspection` e `FolderRejected` (`local_workspace/paths.py`), `InstanceCapabilities.profile_files`, Tasks 6 e 7 (para o helper de teste).
- Produces:
  - `class ProfileFolderRejected(FolderRejected)`.
  - `files_root(user_id: str) -> Path` (cria o diretório).
  - `resolve_inside(root: Path, relative: str) -> Path`: aceita `""`/`"."` (a raiz) e caminhos relativos com `/` ou `\`; recusa absolutos, letra de drive, `..` e qualquer coisa que resolva fora da raiz (symlink incluído).
  - `relative_display(root: Path, absolute: str | Path) -> str | None` (`"."` para a raiz; `None` se estiver fora).
  - `inspect_profile_folder(root: Path, relative: str) -> FolderInspection` (com `path` relativo e `risk="none"`).
  - `list_folder(root: Path, relative: str) -> dict` com `{"path", "entries": [{"name", "kind", "bytes", "modified_at"}], "truncated"}`; pastas primeiro, depois arquivos, cada grupo por nome; no máximo 1000 entradas; symlinks são omitidos.
  - `make_folder(root: Path, parent: str, name: str) -> str` (caminho relativo criado).
  - `class ArchiveRejected(ValueError)`; `extract_zip(source: BinaryIO, root: Path, parent: str, folder_name: str, *, max_total_bytes: int = 2 * 1024**3, max_entries: int = 50_000, max_ratio: int = 200) -> str`.
  - Rotas: `GET /v1/files?path=`, `POST /v1/files/folders` (`{"parent", "name"}` → `201 {"path"}`), `POST /v1/files/import` (multipart `file` + campo `folder_name` opcional → `201 {"path"}`), `GET /v1/files/download?path=` (anexo). Todas exigem a capacidade `profile_files`.
  - No modo server, as rotas de inspeção e vínculo interpretam `path` como relativo à área de arquivos; a resposta traz o caminho relativo; `workspace_state` mostra `path` relativo e, para um vínculo antigo fora da área, `{"kind": "unavailable", "path": null, "folder_name": <nome>}`.
  - Handler: `ArchiveRejected` → `422 VALIDATION archive_rejected`.
  - Helper de teste `tests/integration/server_world.py`: `build_server_world(tmp_path, monkeypatch) -> ServerWorld`; `ServerWorld.admin() -> Session`, `ServerWorld.member(username: str) -> Session`, `ServerWorld.client() -> TestClient`; `Session.api`, `Session.csrf`, `Session.user_id`, `Session.headers() -> dict[str, str]`.

- [ ] **Step 1: Escrever os testes que falham**

`tests/unit/profile_files/test_binding.py` (crie `tests/unit/profile_files/__init__.py`):

```python
import os
from pathlib import Path

import pytest

from agentos.local_workspace import FolderRejected
from agentos.profile_files.binding import ProfileFolderRejected, inspect_profile_folder, relative_display, resolve_inside


def test_relative_paths_resolve_inside_the_root(tmp_path):
    (tmp_path / "proj" / "app").mkdir(parents=True)
    assert resolve_inside(tmp_path, "proj/app") == (tmp_path / "proj" / "app").resolve()
    assert resolve_inside(tmp_path, "proj\\app") == (tmp_path / "proj" / "app").resolve()
    assert resolve_inside(tmp_path, "") == tmp_path.resolve()
    assert resolve_inside(tmp_path, ".") == tmp_path.resolve()


@pytest.mark.parametrize("raw", ["../x", "proj/../../x", "/etc", "C:\\Windows", "C:/Windows", "\\\\server\\share"])
def test_escapes_are_refused(tmp_path, raw):
    with pytest.raises(ProfileFolderRejected):
        resolve_inside(tmp_path, raw)


def test_the_rejection_is_a_folder_rejection(tmp_path):
    with pytest.raises(FolderRejected):
        resolve_inside(tmp_path, "../x")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_out_of_the_root_is_refused(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProfileFolderRejected):
        resolve_inside(root, "link")


def test_inspection_reports_a_relative_path_without_risk(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "a.txt").write_text("a", encoding="utf-8")
    inspection = inspect_profile_folder(tmp_path, "proj")
    assert (inspection.path, inspection.is_directory, inspection.writable, inspection.entry_count, inspection.risk) == ("proj", True, True, 1, "none")
    missing = inspect_profile_folder(tmp_path, "nope")
    assert (missing.exists, missing.is_directory) == (False, False)


def test_relative_display(tmp_path):
    (tmp_path / "proj").mkdir()
    assert relative_display(tmp_path, tmp_path / "proj") == "proj"
    assert relative_display(tmp_path, tmp_path) == "."
    assert relative_display(tmp_path, tmp_path.parent) is None
```

`tests/unit/profile_files/test_listing.py`:

```python
import pytest

from agentos.profile_files.binding import ProfileFolderRejected
from agentos.profile_files.listing import list_folder, make_folder


def test_listing_puts_folders_first_and_reports_sizes(tmp_path):
    (tmp_path / "b-dir").mkdir()
    (tmp_path / "a-dir").mkdir()
    (tmp_path / "z.txt").write_text("123", encoding="utf-8")
    listing = list_folder(tmp_path, "")
    assert listing["path"] == "."
    assert [(item["name"], item["kind"]) for item in listing["entries"]] == [("a-dir", "directory"), ("b-dir", "directory"), ("z.txt", "file")]
    assert listing["entries"][2]["bytes"] == 3
    assert listing["truncated"] is False


def test_listing_a_missing_folder_is_rejected(tmp_path):
    with pytest.raises(ProfileFolderRejected):
        list_folder(tmp_path, "nope")


def test_make_folder_creates_a_child_and_returns_its_relative_path(tmp_path):
    assert make_folder(tmp_path, "", "projetos") == "projetos"
    assert make_folder(tmp_path, "projetos", "app") == "projetos/app"
    assert (tmp_path / "projetos" / "app").is_dir()


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "x" * 256])
def test_make_folder_refuses_unsafe_names(tmp_path, name):
    with pytest.raises(ProfileFolderRejected):
        make_folder(tmp_path, "", name)
```

`tests/unit/profile_files/test_archive.py`:

```python
import io
import stat
import zipfile

import pytest

from agentos.profile_files.archive import ArchiveRejected, extract_zip


def _zip(entries: dict[str, bytes], *, symlink: str | None = None) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    buffer.seek(0)
    return buffer


def test_a_normal_archive_lands_in_a_new_folder(tmp_path):
    path = extract_zip(_zip({"src/main.py": b"print(1)\n", "README.md": b"oi"}), tmp_path, "", "app")
    assert path == "app"
    assert (tmp_path / "app" / "src" / "main.py").read_bytes() == b"print(1)\n"
    assert not any(item.name.startswith(".import-") for item in tmp_path.iterdir())


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt", "C:/win.txt", "a/../../evil.txt"])
def test_entries_that_escape_are_rejected(tmp_path, name):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({name: b"x"}), tmp_path, "", "app")
    assert not (tmp_path / "app").exists()
    assert not (tmp_path.parent / "evil.txt").exists()


def test_symlinks_are_skipped(tmp_path):
    extract_zip(_zip({"ok.txt": b"ok"}, symlink="link"), tmp_path, "", "app")
    assert (tmp_path / "app" / "ok.txt").exists()
    assert not (tmp_path / "app" / "link").exists()


def test_the_total_size_is_enforced_while_writing(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({"big.bin": b"0" * 5000}), tmp_path, "", "app", max_total_bytes=4000)
    assert not (tmp_path / "app").exists()


def test_a_compression_bomb_is_rejected(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({"bomb.bin": b"\0" * (4 * 1024 * 1024)}), tmp_path, "", "app", max_ratio=50)


def test_too_many_entries_are_rejected(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({f"f{i}.txt": b"x" for i in range(11)}), tmp_path, "", "app", max_entries=10)


def test_an_existing_destination_is_not_overwritten(tmp_path):
    (tmp_path / "app").mkdir()
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({"a.txt": b"a"}), tmp_path, "", "app")


def test_not_a_zip(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(io.BytesIO(b"definitely not a zip"), tmp_path, "", "app")
```

`tests/integration/server_world.py` (helper compartilhado, sem testes próprios):

```python
"""A real server-mode API over SQLite, with helpers to sign profiles in."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from agentos.api import create_app
from agentos.bootstrap.production import compose_production_services
from agentos.configuration.mode import RuntimeMode
from agentos.installation import reset_cached_paths
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.sqlite import create_local_engine

KEY = "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA="
ORIGIN = "https://orin.test"
PASSWORD = "a long password"


@dataclass
class Session:
    api: TestClient
    csrf: str
    user_id: str

    def headers(self) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": self.csrf, "Idempotency-Key": uuid4().hex}


@dataclass
class ServerWorld:
    app: object
    services: object
    engine: object
    home: Path

    def client(self) -> TestClient:
        return TestClient(self.app, base_url=ORIGIN)

    def admin(self, username: str = "carla") -> Session:
        api = self.client()
        token = self.services.accounts.setup.issue_if_needed(self.services.accounts.users)
        body = api.post("/v1/auth/setup", json={"token": token, "username": username, "password": PASSWORD}, headers={"Origin": ORIGIN}).json()
        return Session(api, body["csrf_token"], body["user"]["user_id"])

    def member(self, username: str, *, admin: Session | None = None) -> Session:
        admin = admin or self._admin_session()
        created = admin.api.post("/v1/admin/users", json={"username": username}, headers=admin.headers()).json()
        api = self.client()
        signed = api.post("/v1/auth/login", json={"username": username, "password": created["temporary_password"]}, headers={"Origin": ORIGIN}).json()
        session = Session(api, signed["csrf_token"], signed["user"]["user_id"])
        changed = api.post("/v1/auth/password", json={"current_password": created["temporary_password"], "new_password": PASSWORD}, headers=session.headers())
        assert changed.status_code == 200, changed.text
        return session

    def _admin_session(self) -> Session:
        api = self.client()
        body = api.post("/v1/auth/login", json={"username": "carla", "password": PASSWORD}, headers={"Origin": ORIGIN}).json()
        return Session(api, body["csrf_token"], body["user"]["user_id"])


def build_server_world(tmp_path: Path, monkeypatch) -> ServerWorld:
    home = tmp_path / "home"
    monkeypatch.setenv("ORIN_HOME", str(home))
    monkeypatch.setenv("ORIN_MODE", "server")
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)
    reset_cached_paths()
    engine = create_local_engine(f"sqlite:///{tmp_path / 'orin.db'}")
    upgrade(engine)
    services = compose_production_services(engine, mode=RuntimeMode.SERVER, public_origin=ORIGIN)
    return ServerWorld(create_app(services), services, engine, home)
```

`tests/integration/api/test_profile_files.py`:

```python
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from agentos.api import ApiServices, create_app
from agentos.installation import orin_paths, reset_cached_paths
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore

from tests.integration.server_world import build_server_world


@pytest.fixture()
def world(tmp_path, monkeypatch):
    yield build_server_world(tmp_path, monkeypatch)
    reset_cached_paths()


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _conversation(world, user_id):
    store = PostgresChatStore(world.engine, PostgresAgenticActivityStore(world.engine, "cursor-secret"))
    return store.create(user_id=user_id, message="oi", provider="openrouter", model_id="m", idempotency_key=f"seed-{user_id}").conversation_id


def test_folders_import_listing_and_download(world):
    admin = world.admin()
    assert admin.api.post("/v1/files/folders", json={"parent": "", "name": "projetos"}, headers=admin.headers()).json() == {"path": "projetos"}
    imported = admin.api.post(
        "/v1/files/import", data={"folder_name": "app"},
        files={"file": ("app.zip", _zip({"src/main.py": b"print(1)\n"}), "application/zip")},
        headers=admin.headers(),
    )
    assert imported.status_code == 201, imported.text
    assert imported.json() == {"path": "app"}
    names = [item["name"] for item in admin.api.get("/v1/files", params={"path": ""}).json()["entries"]]
    assert names == ["app", "projetos"]
    download = admin.api.get("/v1/files/download", params={"path": "app/src/main.py"})
    assert download.status_code == 200 and download.content == b"print(1)\n"
    assert "attachment" in download.headers["content-disposition"]


def test_a_zip_slip_archive_is_rejected(world):
    admin = world.admin()
    response = admin.api.post(
        "/v1/files/import", files={"file": ("evil.zip", _zip({"../evil.txt": b"x"}), "application/zip")},
        headers=admin.headers(),
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "archive_rejected")


def test_each_profile_sees_only_its_own_files(world):
    admin = world.admin()
    admin.api.post("/v1/files/folders", json={"parent": "", "name": "segredo-a"}, headers=admin.headers())
    member = world.member("bruno", admin=admin)
    assert member.api.get("/v1/files", params={"path": ""}).json()["entries"] == []
    assert member.api.get("/v1/files", params={"path": "segredo-a"}).status_code == 404
    assert member.api.get("/v1/files/download", params={"path": "../local-user/files/segredo-a"}).status_code == 404


def test_inspection_and_binding_use_the_profile_area(world):
    admin = world.admin()
    admin.api.post("/v1/files/folders", json={"parent": "", "name": "proj"}, headers=admin.headers())
    inspected = admin.api.post("/v1/workspaces/inspect", json={"path": "proj"}, headers=admin.headers()).json()
    assert (inspected["path"], inspected["is_directory"], inspected["risk"]) == ("proj", True, "none")
    for raw in ("../escape", "/etc"):
        assert admin.api.post("/v1/workspaces/inspect", json={"path": raw}, headers=admin.headers()).status_code == 422

    conversation_id = _conversation(world, admin.user_id)
    attached = admin.api.put(f"/v1/conversations/{conversation_id}/workspace", json={"path": "proj", "acknowledged_risk": False}, headers=admin.headers())
    assert attached.status_code == 200, attached.text
    assert (attached.json()["kind"], attached.json()["path"], attached.json()["folder_name"]) == ("local", "proj", "proj")
    stored = PostgresLocalWorkspaceStore(world.engine).root_for(conversation_id, admin.user_id)
    assert stored == str((orin_paths().user_files(admin.user_id) / "proj").resolve())


def test_a_legacy_host_folder_shows_as_unavailable(world, tmp_path):
    admin = world.admin()
    conversation_id = _conversation(world, admin.user_id)
    PostgresLocalWorkspaceStore(world.engine).set_root(conversation_id, admin.user_id, str(tmp_path / "host-folder"))
    workspace = admin.api.get(f"/v1/conversations/{conversation_id}").json()["workspace"]
    assert (workspace["kind"], workspace["path"], workspace["folder_name"]) == ("unavailable", None, "host-folder")


def test_files_routes_do_not_exist_in_local_mode():
    response = TestClient(create_app(ApiServices())).get("/v1/files")
    assert response.json()["error"]["code"] == "capability_unavailable"
```

Crie `tests/integration/__init__.py` se não existir, para `from tests.integration.server_world import ...` funcionar (o `pythonpath` do pytest já inclui `.`).

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/profile_files tests/integration/api/test_profile_files.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.profile_files'`.

- [ ] **Step 3: Implementar**

`src/agentos/profile_files/__init__.py`:

```python
"""A profile's own file area on a server instance: browse, import, bind, download."""
```

`src/agentos/profile_files/binding.py`:

```python
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from agentos.installation import orin_paths
from agentos.local_workspace import FolderRejected
from agentos.local_workspace.paths import FolderInspection, _count_entries

_DRIVE = re.compile(r"^[A-Za-z]:")


class ProfileFolderRejected(FolderRejected):
    """A path that is not inside the profile's own file area."""


def files_root(user_id: str) -> Path:
    root = orin_paths().user_files(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_inside(root: Path, relative: str) -> Path:
    raw = (relative or "").strip().replace("\\", "/")
    if raw.startswith("/") or _DRIVE.match(raw):
        raise ProfileFolderRejected("path must be relative to the profile area")
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ProfileFolderRejected("path must not leave the profile area")
    base = Path(root).resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and not target.is_relative_to(base):
        raise ProfileFolderRejected("path resolves outside the profile area")
    return target


def relative_display(root: Path, absolute: str | Path) -> str | None:
    base = Path(root).resolve()
    try:
        target = Path(absolute).resolve()
    except OSError:
        return None
    if target == base:
        return "."
    if not target.is_relative_to(base):
        return None
    return target.relative_to(base).as_posix()


def inspect_profile_folder(root: Path, relative: str) -> FolderInspection:
    target = resolve_inside(root, relative)
    is_directory = target.is_dir()
    count, truncated = _count_entries(target) if is_directory else (0, False)
    display = relative_display(root, target) or "."
    return FolderInspection(display, target.exists(), is_directory, is_directory and os.access(target, os.W_OK), count, truncated, "none")


__all__ = ["ProfileFolderRejected", "files_root", "inspect_profile_folder", "relative_display", "resolve_inside"]
```

`src/agentos/profile_files/listing.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .binding import ProfileFolderRejected, relative_display, resolve_inside

MAX_ENTRIES = 1000


def list_folder(root: Path, relative: str) -> dict[str, object]:
    target = resolve_inside(root, relative)
    if not target.is_dir():
        raise ProfileFolderRejected("folder does not exist")
    directories, files = [], []
    for item in target.iterdir():
        if item.is_symlink():
            continue
        stat = item.stat()
        entry = {"name": item.name, "kind": "directory" if item.is_dir() else "file", "bytes": 0 if item.is_dir() else stat.st_size,
                 "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()}
        (directories if item.is_dir() else files).append(entry)
    entries = sorted(directories, key=lambda e: e["name"]) + sorted(files, key=lambda e: e["name"])
    return {"path": relative_display(root, target) or ".", "entries": entries[:MAX_ENTRIES], "truncated": len(entries) > MAX_ENTRIES}


def make_folder(root: Path, parent: str, name: str) -> str:
    clean = (name or "").strip()
    if clean in ("", ".", "..") or "/" in clean or "\\" in clean or len(clean) > 255:
        raise ProfileFolderRejected("folder name is not valid")
    base = resolve_inside(root, parent)
    if not base.is_dir():
        raise ProfileFolderRejected("parent folder does not exist")
    created = resolve_inside(root, f"{relative_display(root, base)}/{clean}")
    created.mkdir(exist_ok=True)
    return relative_display(root, created) or "."


__all__ = ["MAX_ENTRIES", "list_folder", "make_folder"]
```

`src/agentos/profile_files/archive.py`:

```python
"""Safe .zip import into a profile's file area.

Header sizes are only used for a quick refusal; the real limit is enforced on
the bytes actually written. Everything lands in a hidden temporary folder and
is renamed into place only when the whole archive was accepted.
"""
from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from .binding import ProfileFolderRejected, relative_display, resolve_inside

_CHUNK = 1024 * 1024


class ArchiveRejected(ValueError):
    pass


def _safe_parts(name: str) -> list[str]:
    raw = name.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ArchiveRejected(f"absolute path in archive: {name}")
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ArchiveRejected(f"path escapes the archive: {name}")
    return parts


def extract_zip(source: BinaryIO, root: Path, parent: str, folder_name: str, *, max_total_bytes: int = 2 * 1024**3, max_entries: int = 50_000, max_ratio: int = 200) -> str:
    clean = (folder_name or "").strip()
    if clean in ("", ".", "..") or "/" in clean or "\\" in clean or len(clean) > 255:
        raise ArchiveRejected("folder name is not valid")
    try:
        base = resolve_inside(root, parent)
    except ProfileFolderRejected as error:
        raise ArchiveRejected(str(error)) from error
    destination = base / clean
    if destination.exists():
        raise ArchiveRejected("destination already exists")
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as error:
        raise ArchiveRejected("not a zip archive") from error
    staging = base / f".import-{uuid4().hex}"
    try:
        with archive:
            members = archive.infolist()
            if len(members) > max_entries:
                raise ArchiveRejected("archive has too many entries")
            written = 0
            staging.mkdir()
            for member in members:
                parts = _safe_parts(member.filename)
                if not parts:
                    continue
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    continue
                target = staging.joinpath(*parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.file_size > _CHUNK and member.file_size > max_ratio * max(member.compress_size, 1):
                    raise ArchiveRejected(f"suspicious compression ratio: {member.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as reader, open(target, "wb") as writer:
                    while chunk := reader.read(_CHUNK):
                        written += len(chunk)
                        if written > max_total_bytes:
                            raise ArchiveRejected("archive is larger than allowed")
                        writer.write(chunk)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return relative_display(root, destination) or clean


__all__ = ["ArchiveRejected", "extract_zip"]
```

`src/agentos/api/files_routes.py`:

```python
"""The profile file area: browse, create folders, import a .zip, download."""
from __future__ import annotations

import shutil
import tempfile
from typing import Callable

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.file_preview import media_type_for
from agentos.profile_files.archive import ArchiveRejected, extract_zip
from agentos.profile_files.binding import ProfileFolderRejected, files_root, resolve_inside
from agentos.profile_files.listing import list_folder, make_folder

from .contracts import ApplicationNotFoundError, ApplicationValidationError
from .security import AuthenticatedPrincipal

MAX_IMPORT_BYTES = 512 * 1024 * 1024


class CreateFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent: str = Field(default="", max_length=4096)
    name: str = Field(min_length=1, max_length=255)


def register_files_routes(app: FastAPI, services, principal_for: Callable[..., AuthenticatedPrincipal]) -> None:
    def principal(request: Request, *, mutable: bool, purpose: str) -> AuthenticatedPrincipal:
        services.capabilities.require("profile_files")
        current = principal_for(request, mutable=mutable)
        services.security.authorize(current, action="files.write" if mutable else "files.read", resource_id=None, purpose=purpose)
        return current

    @app.get("/v1/files")
    async def list_files(request: Request, path: str = "") -> JSONResponse:
        current = principal(request, mutable=False, purpose="files.list")
        try:
            return JSONResponse(list_folder(files_root(current.user_id), path))
        except ProfileFolderRejected as error:
            raise ApplicationNotFoundError(path) from error

    @app.post("/v1/files/folders", status_code=201)
    async def create_folder(payload: CreateFolderRequest, request: Request) -> JSONResponse:
        current = principal(request, mutable=True, purpose="files.folder.create")
        try:
            created = make_folder(files_root(current.user_id), payload.parent, payload.name)
        except ProfileFolderRejected as error:
            raise ApplicationValidationError(str(error)) from error
        return JSONResponse({"path": created}, status_code=201)

    @app.post("/v1/files/import", status_code=201)
    async def import_archive(request: Request, file: UploadFile = File(...), folder_name: str = Form(default=""), parent: str = Form(default="")) -> JSONResponse:
        current = principal(request, mutable=True, purpose="files.import")
        name = folder_name.strip() or (file.filename or "importado").rsplit(".", 1)[0]
        with tempfile.TemporaryFile() as spooled:
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise ArchiveRejected("upload is larger than allowed")
                spooled.write(chunk)
            spooled.seek(0)
            created = await run_in_threadpool(extract_zip, spooled, files_root(current.user_id), parent, name)
        return JSONResponse({"path": created}, status_code=201)

    @app.get("/v1/files/download")
    async def download_file(request: Request, path: str) -> FileResponse:
        current = principal(request, mutable=False, purpose="files.download")
        try:
            target = resolve_inside(files_root(current.user_id), path)
        except ProfileFolderRejected as error:
            raise ApplicationNotFoundError(path) from error
        if not target.is_file():
            raise ApplicationNotFoundError(path)
        return FileResponse(target, media_type=media_type_for(target), filename=target.name, content_disposition_type="attachment", headers={"X-Content-Type-Options": "nosniff"})


__all__ = ["register_files_routes"]
```

(Remova o `import shutil` se o linter acusar import não usado.)

`src/agentos/api/gateway.py`:

1. Imports:

```python
from agentos.local_workspace.paths import FolderInspection
from agentos.profile_files.archive import ArchiveRejected
from agentos.profile_files.binding import files_root, inspect_profile_folder, relative_display, resolve_inside
```

2. Handler, junto aos da Task 6:

```python
    @app.exception_handler(ArchiveRejected)
    async def archive_rejected(_: Request, __: ArchiveRejected) -> JSONResponse:
        return _error(422, "VALIDATION", "archive_rejected", retryable=False)
```

3. Helpers dentro de `create_app`, logo antes de `local_root_for` (`:780`):

```python
    def inspect_for(principal: AuthenticatedPrincipal, raw: str) -> FolderInspection:
        if services.capabilities.profile_files:
            return inspect_profile_folder(files_root(principal.user_id), raw)
        return inspect_folder(raw, home=Path.home(), orin_data=orin_paths().data)

    def bind_path_for(principal: AuthenticatedPrincipal, inspection: FolderInspection) -> str:
        if services.capabilities.profile_files:
            return str(resolve_inside(files_root(principal.user_id), inspection.path))
        return inspection.path
```

4. Troque as quatro chamadas `inspect_folder(<x>, home=Path.home(), orin_data=orin_paths().data)` (`:556`, `:580`, `:827`, `:838`) por `inspect_for(principal, <x>)`. Nas duas chamadas `set_root(..., inspection.path)` (`:589` e `:848`), troque `inspection.path` por `bind_path_for(principal, inspection)`. Os `except FolderRejected` existentes já capturam `ProfileFolderRejected`.

5. `local_root_for` e `workspace_state`:

```python
    def local_root_for(workspace_id: str, principal: AuthenticatedPrincipal) -> str | None:
        store = services.local_workspaces
        if store is None:
            return None
        root = store.root_for(workspace_id, principal.user_id)
        if root and services.capabilities.profile_files and relative_display(files_root(principal.user_id), root) is None:
            # A host folder bound before this instance became a server is never
            # served; the conversation falls back to its managed workspace.
            return None
        return root

    def workspace_state(conversation: dict[str, object], principal: AuthenticatedPrincipal) -> dict[str, object]:
        workspace_id, project_name = effective_workspace_id(conversation, principal)
        store = services.local_workspaces
        stored = store.root_for(workspace_id, principal.user_id) if store is not None else None
        if stored and services.capabilities.profile_files:
            shown = relative_display(files_root(principal.user_id), stored)
            if shown is None:
                return {"kind": "unavailable", "path": None, "folder_name": Path(stored).name, **_workspace_scope(conversation, project_name)}
            return {"kind": "local", "path": shown, "folder_name": Path(stored).name, **_workspace_scope(conversation, project_name)}
        root = local_root_for(workspace_id, principal)
        ...  # resto do corpo original, inalterado
```

Leia o corpo original de `workspace_state` (`:786-800`) e extraia as chaves de escopo que ele já monta (`scope`, `project_name`) para uma função local `_workspace_scope(conversation, project_name) -> dict[str, object]`, usada pelos três retornos, para os três formatos terem exatamente as mesmas chaves.

6. No fim de `create_app`, junto ao registro da Task 6:

```python
    from .files_routes import register_files_routes
    register_files_routes(app, services, principal_for)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/profile_files tests/integration/api/test_profile_files.py -q`
Expected: PASS.

Run: `python -m pytest tests/unit tests/integration -q -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/profile_files src/agentos/api/files_routes.py src/agentos/api/gateway.py tests/integration/server_world.py tests/integration/__init__.py tests/unit/profile_files tests/integration/api/test_profile_files.py
git commit -m "feat(files): give each profile a file area and bind projects inside it

On a server a project folder is chosen from the profile's own area, code
arrives as a .zip that cannot write outside its folder, and a host folder
bound before the switch is shown as unavailable instead of being served.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Dono inativo e pasta fora da área no worker e no scheduler

**Files:**
- Modify: `src/agentos/workers/chat.py` (`ChatWorker.run`, `:355-360`)
- Modify: `src/agentos/scheduler/scheduled_chats.py:244`
- Test: `tests/unit/workers/test_chat_worker_preflight.py`
- Test: `tests/unit/scheduler/test_scheduled_chats.py` (acrescentar)

**Interfaces:**
- Consumes: `UserStore.is_active`, `InstanceCapabilities`, `orin_paths().user_files`, `relative_display`, `PostgresChatStore.finish`.
- Produces:
  - `ChatWorker._preflight_refusal(turn: Mapping[str, object]) -> str | None`: `"owner_inactive"` quando a instância tem `user_admin` e o dono não está ativo; `"workspace_unavailable"` quando a instância tem `profile_files` e `workspace_root_path` está fora de `user_files(dono)`; senão `None`.
  - `run` termina o turno com `store.finish(turn, failed=True, code=<motivo>)` antes de qualquer trabalho quando há recusa.
  - `run_due` não materializa agendamentos de donos marcados `active = false` em `users`. Dono sem linha em `users` (modo local) continua agendando.

- [ ] **Step 1: Escrever os testes que falham**

`tests/unit/workers/test_chat_worker_preflight.py`:

```python
import pytest
from sqlalchemy import create_engine, select

from agentos.accounts.store import UserStore
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.conversations.chat import PostgresChatStore
from agentos.installation import orin_paths, reset_cached_paths
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import conversation_dispatches, metadata
from agentos.workers.chat import ChatWorker

SERVER = InstanceCapabilities.for_mode(RuntimeMode.SERVER)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    reset_cached_paths()
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "cursor-secret"))
    users = UserStore(engine)
    admin = users.create(username="carla", password="a long password", role="admin")
    member = users.create(username="bruno", password="a long password")
    yield engine, store, users, admin, member
    reset_cached_paths()


def _never(turn):
    raise AssertionError("a refused turn must not build a runtime")


def _outcome(engine, turn_id):
    with engine.connect() as connection:
        row = connection.execute(select(conversation_dispatches.c.state, conversation_dispatches.c.last_error).where(conversation_dispatches.c.turn_id == turn_id)).one()
    return tuple(row)


def test_a_turn_from_a_deactivated_profile_never_runs(world):
    engine, store, users, _, member = world
    receipt = store.create(user_id=member.user_id, message="oi", provider="openrouter", model_id="m", idempotency_key="k1")
    users.update(member.user_id, active=False)
    ChatWorker(store, runtime_factory=_never, capabilities=SERVER).run(receipt.turn_id)
    assert _outcome(engine, receipt.turn_id) == ("failed", "owner_inactive")


def test_a_project_bound_outside_the_profile_area_is_refused(world, tmp_path):
    engine, store, _, admin, _ = world
    receipt = store.create(user_id=admin.user_id, message="oi", provider="openrouter", model_id="m", idempotency_key="k2")
    PostgresLocalWorkspaceStore(engine).set_root(receipt.conversation_id, admin.user_id, str(tmp_path / "host"))
    ChatWorker(store, runtime_factory=_never, capabilities=SERVER).run(receipt.turn_id)
    assert _outcome(engine, receipt.turn_id) == ("failed", "workspace_unavailable")


def test_preflight_accepts_a_folder_inside_the_area(world):
    engine, store, _, admin, _ = world
    inside = orin_paths().user_files(admin.user_id) / "proj"
    inside.mkdir(parents=True)
    receipt = store.create(user_id=admin.user_id, message="oi", provider="openrouter", model_id="m", idempotency_key="k3")
    PostgresLocalWorkspaceStore(engine).set_root(receipt.conversation_id, admin.user_id, str(inside))
    worker = ChatWorker(store, runtime_factory=_never, capabilities=SERVER)
    turn = store.claim(receipt.turn_id)
    assert worker._preflight_refusal(turn) is None


def test_local_mode_has_no_preflight(world, tmp_path):
    engine, store, _, admin, _ = world
    worker = ChatWorker(store, runtime_factory=_never, capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL))
    receipt = store.create(user_id="someone-without-account", message="oi", provider="openrouter", model_id="m", idempotency_key="k4")
    PostgresLocalWorkspaceStore(engine).set_root(receipt.conversation_id, "someone-without-account", str(tmp_path / "anywhere"))
    assert worker._preflight_refusal(store.claim(receipt.turn_id)) is None
```

Em `tests/unit/scheduler/test_scheduled_chats.py`, acrescente:

```python
def test_a_deactivated_owner_does_not_fire_until_reactivated():
    from agentos.accounts.store import UserStore
    from sqlalchemy import update
    from agentos.persistence.postgres.schema import schedules

    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    engine = create_local_engine("sqlite+pysqlite://")
    metadata.create_all(engine)
    users = UserStore(engine)
    users.create(username="admin", password="a long password", role="admin")
    owner = users.create(username="bruno", password="a long password")
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id=owner.user_id, provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id=owner.user_id, provider="openrouter", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))
    service = ScheduledChatService(engine, clock=lambda: now)
    service.create(owner.user_id, ScheduledChatInput("x", "openrouter", "model-1", "UTC", "hourly"), idempotency_key="s1")
    users.update(owner.user_id, active=False)
    assert service.run_due(worker_id="w", due_before=now + timedelta(hours=1)) == ()
    users.update(owner.user_id, active=True)
    assert service.run_due(worker_id="w", due_before=now + timedelta(hours=1))
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/workers/test_chat_worker_preflight.py tests/unit/scheduler/test_scheduled_chats.py -q`
Expected: FAIL (`AttributeError: '_preflight_refusal'`, e o agendamento dispara com o dono inativo).

- [ ] **Step 3: Implementar**

`src/agentos/workers/chat.py`:
- Imports: `from agentos.accounts.store import UserStore` e `from agentos.profile_files.binding import relative_display`.
- Método novo em `ChatWorker`:

```python
    def _preflight_refusal(self, turn: Mapping[str, object]) -> str | None:
        """Why this turn must not run at all on this instance, if anything."""
        user_id = str(turn.get("user_id") or "")
        if self._capabilities.user_admin and not UserStore(self.store._engine).is_active(user_id):
            return "owner_inactive"
        local_root = turn.get("workspace_root_path")
        if self._capabilities.profile_files and isinstance(local_root, str) and local_root.strip():
            if relative_display(orin_paths().user_files(user_id), local_root) is None:
                return "workspace_unavailable"
        return None
```

(Se `Mapping` não estiver importado em `chat.py`, use `dict[str, object]` na anotação.)
- Em `run`, logo depois de `if turn is None: return`:

```python
        refusal = self._preflight_refusal(turn)
        if refusal is not None:
            self.store.finish(turn, failed=True, code=refusal)
            return
```

`src/agentos/scheduler/scheduled_chats.py`: importe `users` de `agentos.persistence.postgres.schema` (junto aos imports de schema existentes) e troque a consulta da linha 244 por:

```python
            inactive_owners = select(users.c.user_id).where(users.c.active.is_(False))
            rows = connection.execute(select(schedules).where(
                schedules.c.state == "ACTIVE", schedules.c.next_fire_at <= due,
                schedules.c.user_id.not_in(inactive_owners),
            ).with_for_update()).mappings().all()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/workers tests/unit/scheduler -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/workers/chat.py src/agentos/scheduler/scheduled_chats.py tests/unit/workers/test_chat_worker_preflight.py tests/unit/scheduler/test_scheduled_chats.py
git commit -m "feat(server): never run work for a deactivated profile or a foreign folder

Queued turns of a deactivated profile end as owner_inactive, its schedules
pause until it is reactivated, and a project still bound to a host folder
ends as workspace_unavailable instead of running elsewhere.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Plugins só de fontes públicas e browser sem loopback no servidor

**Files:**
- Modify: `src/agentos/plugins/fetcher.py:28-31, 82-107`
- Modify: `src/agentos/bootstrap/production.py` (`PluginService(...)` em `compose_production_services`)
- Modify: `src/agentos/browser/conversation_worker.py:286-297` (`_policy_for`)
- Test: `tests/unit/plugins/test_fetcher_remote_only.py`
- Test: `tests/unit/browser/test_server_network_policy.py`

**Interfaces:**
- Consumes: `agentos.oauth.netpolicy.public_https`, `OAuthUrlRefused`, `current_mode`.
- Produces:
  - `PluginFetcher(root, *, max_bytes=..., max_files=..., timeout=..., remote_only: bool = False)`: com `remote_only=True`, fonte `path` levanta `FetchRejected`; `git` só aceita URL que passe por `public_https` (https público, DNS checado) e roda o clone com `GIT_ALLOW_PROTOCOL=https`, `GIT_TERMINAL_PROMPT=0` e `-c protocol.file.allow=never`.
  - `compose_production_services` passa `fetcher=PluginFetcher(plugin_root, remote_only=not capabilities.host_folders)`.
  - `_policy_for(capability: str, *, allow_loopback: bool | None = None) -> NetworkPolicy`: `None` significa "loopback só fora do modo server".

- [ ] **Step 1: Escrever os testes que falham**

`tests/unit/plugins/test_fetcher_remote_only.py`:

```python
import subprocess

import pytest

from agentos.oauth.netpolicy import OAuthUrlRefused
from agentos.plugins import fetcher as fetcher_module
from agentos.plugins.fetcher import FetchRejected, PluginFetcher
from agentos.plugins.sources import PluginSource


def test_a_host_path_source_is_refused(tmp_path):
    with pytest.raises(FetchRejected):
        PluginFetcher(tmp_path / "cache", remote_only=True).fetch(PluginSource(kind="path", path=str(tmp_path)))


@pytest.mark.parametrize("url", ["file:///etc", "ext::sh -c id", "http://example.com/repo.git", "git@github.com:a/b.git"])
def test_non_https_git_urls_are_refused(tmp_path, url):
    with pytest.raises(FetchRejected):
        PluginFetcher(tmp_path / "cache", remote_only=True).fetch(PluginSource(kind="git", url=url))


def test_private_hosts_are_refused(tmp_path, monkeypatch):
    def refuse(url):
        raise OAuthUrlRefused("private")
    monkeypatch.setattr(fetcher_module, "public_https", refuse)
    with pytest.raises(FetchRejected):
        PluginFetcher(tmp_path / "cache", remote_only=True).fetch(PluginSource(kind="git", url="https://10.0.0.5/repo.git"))


def test_a_public_clone_runs_with_https_only(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher_module, "public_https", lambda url: url)
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["env"] = command, kwargs.get("env")
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(fetcher_module.subprocess, "run", fake_run)
    with pytest.raises(FetchRejected):
        PluginFetcher(tmp_path / "cache", remote_only=True).fetch(PluginSource(kind="git", url="https://github.com/a/b.git"))
    assert seen["command"][:3] == ["git", "-c", "protocol.file.allow=never"]
    assert seen["env"]["GIT_ALLOW_PROTOCOL"] == "https" and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
```

`tests/unit/browser/test_server_network_policy.py` (crie `tests/unit/browser/__init__.py` se não existir):

```python
import pytest

from agentos.browser.conversation_worker import _policy_for
from agentos.browser.security import NetworkPolicyError, validate_url


def test_local_mode_keeps_loopback_for_dev_servers(monkeypatch):
    monkeypatch.delenv("ORIN_MODE", raising=False)
    assert _policy_for("full").allow_loopback is True


def test_server_mode_blocks_loopback_at_every_level(monkeypatch):
    monkeypatch.setenv("ORIN_MODE", "server")
    for capability in ("interact", "full"):
        policy = _policy_for(capability)
        assert policy.allow_loopback is False
        with pytest.raises(NetworkPolicyError):
            validate_url("http://127.0.0.1:49200/v1/auth/me", policy)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/plugins/test_fetcher_remote_only.py tests/unit/browser/test_server_network_policy.py -q`
Expected: FAIL (`unexpected keyword argument 'remote_only'`; loopback continua liberado).

- [ ] **Step 3: Implementar**

`src/agentos/plugins/fetcher.py`:
- Imports: `import os` e `from agentos.oauth.netpolicy import OAuthUrlRefused, public_https`.
- Construtor:

```python
    def __init__(self, root: Path, *, max_bytes: int = 25_000_000, max_files: int = 4000, timeout: int = 120, remote_only: bool = False) -> None:
        self.root = Path(root)
        self.max_bytes, self.max_files, self.timeout = max_bytes, max_files, timeout
        self.remote_only = remote_only
```

- Em `_clone_into`, no começo:

```python
        if self.remote_only and source.kind == "path":
            raise FetchRejected("a server instance installs plugins only from public https repositories")
```

- No ramo `git`, troque a montagem do comando e a chamada por:

```python
            command = ["git", "clone", "--depth", "1", "--no-tags", "--recurse-submodules=no", "--config", "core.symlinks=false"]
            environment = None
            if self.remote_only:
                try:
                    public_https(source.url or "")
                except OAuthUrlRefused as error:
                    raise FetchRejected("plugin repository must be a public https URL") from error
                command = ["git", "-c", "protocol.file.allow=never", *command[1:]]
                environment = {**os.environ, "GIT_ALLOW_PROTOCOL": "https", "GIT_TERMINAL_PROMPT": "0"}
            if source.ref:
                command += ["--branch", source.ref]
            command += [source.url or "", str(staging)]
            try:
                subprocess.run(command, check=True, shell=False, timeout=self.timeout, capture_output=True, text=True, env=environment)
            except (OSError, subprocess.SubprocessError) as error:
                raise FetchRejected("plugin repository could not be fetched") from error
```

`src/agentos/bootstrap/production.py`: importe `from agentos.plugins.fetcher import PluginFetcher` e, no `PluginService(...)` de `compose_production_services`, acrescente `fetcher=PluginFetcher(orin_paths().data / "plugins", remote_only=not capabilities.host_folders),`.

`src/agentos/browser/conversation_worker.py`: importe `from agentos.configuration.mode import RuntimeMode, current_mode` e troque `_policy_for` por:

```python
def _policy_for(capability: str, *, allow_loopback: bool | None = None) -> NetworkPolicy:
    """The network policy for this session's capability level.

    On a personal install, local development servers on loopback are reachable
    so Code mode can exercise the application it is changing. On a server,
    loopback is the Orin API and the host's own services, so it is closed.
    LAN, link-local, metadata and reserved addresses are always blocked.
    """
    loopback = current_mode() is not RuntimeMode.SERVER if allow_loopback is None else allow_loopback
    if capability == "full":
        return NetworkPolicy(allowed_schemes=("http", "https"), allowed_ports=(), allow_subresources=True, allow_loopback=loopback)
    return NetworkPolicy(allow_subresources=True, allow_loopback=loopback)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/plugins tests/unit/browser -q`
Expected: PASS.

Run: `python -m pytest tests/integration/test_plugin_install.py -q`
Expected: PASS (modo local inalterado).

- [ ] **Step 5: Commit**

```bash
git add src/agentos/plugins/fetcher.py src/agentos/bootstrap/production.py src/agentos/browser/conversation_worker.py tests/unit/plugins/test_fetcher_remote_only.py tests/unit/browser
git commit -m "feat(server): fetch plugins only from public https and close loopback

A server never copies a plugin from a host path or clones through file and
ext transports, and the agent browser cannot reach the Orin API or other
services on the host.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: `orin serve` e `orin user`

**Files:**
- Modify: `src/agentos/launcher/environment.py` (nova função)
- Create: `src/agentos/launcher/serve.py`
- Create: `src/agentos/launcher/users.py`
- Modify: `src/agentos/accounts/session_security.py` (função de módulo `revoke_user_sessions`)
- Modify: `src/agentos/launcher/cli.py` (`build_parser`, `main`)
- Test: `tests/unit/launcher/test_server_environment.py`
- Test: `tests/unit/launcher/test_serve_supervisor.py`
- Test: `tests/unit/launcher/test_user_commands.py`

**Interfaces:**
- Consumes: `parse_env_file`, `write_default_configuration`, `sqlite_url`, `ConfigurationError`, `RuntimeEnvironment` (`launcher/environment.py`), `apply_migrations` (`launcher/services.py`), `migrate_data_layout`, `UserStore`, `generate_temporary_password`, `upgrade`.
- Produces:
  - `load_server_environment(paths: OrinPaths, profile) -> RuntimeEnvironment`: mesma precedência de arquivos de `load_environment`; cria `orin.env` com chave nova se não houver arquivo; força `ORIN_MODE=server`, `LOCALHOST_TRUST_ENABLED=false`, `AGENTOS_ENV` padrão `production`, `ORIN_BACKEND_HOST` padrão `127.0.0.1`, `ORIN_BACKEND_PORT` padrão `49200`; `DATABASE_URL` padrão `sqlite:///<data>/orin.db` (só SQLite); exige `ORIN_PUBLIC_URL` (levanta `ConfigurationError` com instrução).
  - `class ServeSupervisor(commands: Mapping[str, Sequence[str]], environment: Mapping[str, str], *, cwd: Path, popen=subprocess.Popen, poll_interval: float = 0.5)` com `run() -> int` (sobe todos, devolve o código do primeiro que sair e encerra os outros) e `stop() -> None`.
  - `command_serve(paths, profile, console) -> int`.
  - `revoke_user_sessions(engine, user_id: str, *, keep_session_id: str | None = None) -> None` (o método `SessionSecurityService.revoke_user` passa a delegar para ela).
  - `create_user(engine, *, username: str, password: str, admin: bool, display_name: str | None = None) -> UserRecord`.
  - `reset_password(engine, *, username: str) -> str` (devolve a senha provisória; revoga as sessões).
  - `command_user(arguments, paths, console) -> int`.
  - CLI: `orin serve`, `orin user create <username> [--admin] [--display-name NOME]`, `orin user reset-password <username>`.

- [ ] **Step 1: Escrever os testes que falham**

`tests/unit/launcher/test_server_environment.py`:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentos.installation.paths import OrinPaths
from agentos.launcher.environment import ConfigurationError, load_server_environment


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def _profile(tmp_path: Path):
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<html></html>", encoding="utf-8")
    return SimpleNamespace(environment_files=lambda config: tuple(sorted(config.glob("*.env"))), web_dist=web, root=tmp_path, is_development=False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("ORIN_MODE", "ORIN_PUBLIC_URL", "LOCALHOST_TRUST_ENABLED", "DATABASE_URL", "AGENTOS_ENV", "ORIN_BACKEND_HOST", "AGENTOS_PROVIDER_ENCRYPTION_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_server_environment_forces_server_mode_and_creates_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_PUBLIC_URL", "https://orin.example.com")
    paths = _paths(tmp_path)
    environment = load_server_environment(paths, _profile(tmp_path))
    values = environment.values
    assert values["ORIN_MODE"] == "server"
    assert values["LOCALHOST_TRUST_ENABLED"] == "false"
    assert values["AGENTOS_ENV"] == "production"
    assert values["ORIN_BACKEND_HOST"] == "127.0.0.1"
    assert values["DATABASE_URL"].startswith("sqlite") and values["DATABASE_URL"].endswith("orin.db")
    assert values["AGENTOS_PROVIDER_ENCRYPTION_KEY"]
    assert (paths.config / "orin.env").is_file()


def test_operator_values_win(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_PUBLIC_URL", "https://orin.example.com")
    monkeypatch.setenv("ORIN_BACKEND_HOST", "0.0.0.0")
    monkeypatch.setenv("LOCALHOST_TRUST_ENABLED", "true")
    values = load_server_environment(_paths(tmp_path), _profile(tmp_path)).values
    assert values["ORIN_BACKEND_HOST"] == "0.0.0.0"
    assert values["LOCALHOST_TRUST_ENABLED"] == "false"


def test_the_public_url_is_required(tmp_path):
    with pytest.raises(ConfigurationError) as raised:
        load_server_environment(_paths(tmp_path), _profile(tmp_path))
    assert "ORIN_PUBLIC_URL" in str(raised.value)


def test_only_sqlite_is_supported(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_PUBLIC_URL", "https://orin.example.com")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    with pytest.raises(ConfigurationError):
        load_server_environment(_paths(tmp_path), _profile(tmp_path))
```

`tests/unit/launcher/test_serve_supervisor.py`:

```python
import os
import sys
import time

from agentos.launcher.serve import ServeSupervisor


def test_the_first_child_to_exit_stops_the_others_and_sets_the_exit_code(tmp_path):
    commands = {
        "backend": [sys.executable, "-c", "import time; time.sleep(60)"],
        "worker": [sys.executable, "-c", "import sys; sys.exit(3)"],
    }
    # The full environment: on Windows the interpreter cannot start without SYSTEMROOT.
    supervisor = ServeSupervisor(commands, dict(os.environ), cwd=tmp_path, poll_interval=0.05)
    started = time.monotonic()
    assert supervisor.run() == 3
    assert time.monotonic() - started < 30
    assert all(child.poll() is not None for child in supervisor.children.values())


def test_stop_ends_every_child(tmp_path):
    commands = {"backend": [sys.executable, "-c", "import time; time.sleep(60)"]}
    supervisor = ServeSupervisor(commands, dict(os.environ), cwd=tmp_path, poll_interval=0.05)
    supervisor.start()
    supervisor.stop()
    assert all(child.poll() is not None for child in supervisor.children.values())
```

`tests/unit/launcher/test_user_commands.py`:

```python
import pytest
from sqlalchemy import create_engine

from agentos.accounts.errors import UserNotFound
from agentos.accounts.passwords import verify_password
from agentos.accounts.session_security import SessionSecurityService, derive_csrf_secret
from agentos.accounts.store import UserStore
from agentos.api.security import AuthenticationError
from agentos.launcher.users import create_user, reset_password
from agentos.persistence.postgres.migrate import upgrade


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'orin.db'}")
    upgrade(engine)
    return engine


def test_create_user_from_the_terminal(engine):
    admin = create_user(engine, username="carla", password="a long password", admin=True)
    assert (admin.user_id, admin.role) == ("local-user", "admin")


def test_reset_password_returns_a_temporary_one_and_ends_sessions(engine):
    users = UserStore(engine)
    admin = create_user(engine, username="carla", password="a long password", admin=True)
    sessions = SessionSecurityService(engine, users=users, public_origin="https://o.test", csrf_secret=derive_csrf_secret("k"))
    sid, _ = sessions.open_session(admin)
    temporary = reset_password(engine, username="carla")
    _, encoded = users.find_for_login("carla")
    assert verify_password(temporary, encoded)
    assert users.get(admin.user_id).must_change_password is True
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=sid)


def test_reset_password_for_an_unknown_user(engine):
    with pytest.raises(UserNotFound):
        reset_password(engine, username="ghost")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/launcher/test_server_environment.py tests/unit/launcher/test_serve_supervisor.py tests/unit/launcher/test_user_commands.py -q`
Expected: FAIL com `ImportError`.

- [ ] **Step 3: Implementar**

`src/agentos/launcher/environment.py`, depois de `load_environment`:

```python
def load_server_environment(paths: OrinPaths, profile: RuntimeProfile) -> RuntimeEnvironment:
    """The environment for ``orin serve``: the same files, never loopback trust."""
    files = profile.environment_files(paths.config)
    created: Path | None = None
    default_database_url = sqlite_url(paths.data / "orin.db")
    if not files:
        created = write_default_configuration(paths.config / "orin.env", database_url=default_database_url)
        files = (created,)
    values: dict[str, str] = {}
    for path in files:
        values.update(parse_env_file(path))
    # Exported variables win over files: a container is configured through its
    # environment, the file only carries the generated encryption key.
    values.update(os.environ)
    values.setdefault("DATABASE_URL", default_database_url)
    values["ORIN_MODE"] = "server"
    values["LOCALHOST_TRUST_ENABLED"] = "false"
    values.setdefault("AGENTOS_ENV", "production")
    values.setdefault("ORIN_BACKEND_HOST", "127.0.0.1")
    values.setdefault("ORIN_BACKEND_PORT", "49200")
    web = profile.web_dist
    if web is None or not (web / "index.html").is_file():
        raise ConfigurationError(f"The Orin web interface is missing from this installation (expected {web}).")
    values["WEB_DIST_DIR"] = str(web.resolve())
    values.update(paths.as_environment())
    if not values.get("ORIN_PUBLIC_URL", "").strip():
        raise ConfigurationError(
            "ORIN_PUBLIC_URL is not set. It is the address people open in the browser, e.g.\n"
            "  ORIN_PUBLIC_URL=https://orin.example.com"
        )
    if not values.get("AGENTOS_PROVIDER_ENCRYPTION_KEY", "").strip():
        raise ConfigurationError("AGENTOS_PROVIDER_ENCRYPTION_KEY is not set and no configuration file provides one.")
    if not values["DATABASE_URL"].startswith("sqlite"):
        raise ConfigurationError("Orin server mode only supports the bundled SQLite database.")
    return RuntimeEnvironment(values, tuple(files), created)
```

Acrescente `"load_server_environment"` ao `__all__`. Se `write_default_configuration` não gravar `AGENTOS_PROVIDER_ENCRYPTION_KEY` no arquivo criado, confira `environment.py:61-86`: a spec da Linux release diz que ele grava a chave Fernet nova; o teste `test_server_environment_forces_server_mode_and_creates_a_key` verifica isso.

`src/agentos/launcher/serve.py`:

```python
"""``orin serve`` — run the server profile in the foreground.

Built for a container or a systemd unit: no browser, no desktop window, no
per-user instance lock, logs on stdout. If any service exits, everything
stops and the exit code is returned so the process manager restarts it.
"""
from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from time import sleep
from typing import Callable, Mapping, Sequence

from agentos.installation import OrinPaths, RuntimeProfile
from agentos.installation.layout import migrate_data_layout

from .environment import ConfigurationError, load_server_environment
from .internal import SERVICES
from .processes import child_environment
from .services import apply_migrations
from .ui import Console


class ServeSupervisor:
    def __init__(self, commands: Mapping[str, Sequence[str]], environment: Mapping[str, str], *, cwd: Path, popen: Callable[..., subprocess.Popen] = subprocess.Popen, poll_interval: float = 0.5) -> None:
        self.commands = {name: list(command) for name, command in commands.items()}
        self.environment = dict(environment)
        self.cwd = cwd
        self._popen = popen
        self._poll_interval = poll_interval
        self.children: dict[str, subprocess.Popen] = {}
        self._stopping = False

    def start(self) -> None:
        for name, command in self.commands.items():
            self.children[name] = self._popen(command, cwd=str(self.cwd), env=self.environment, stdin=subprocess.DEVNULL)

    def stop(self) -> None:
        self._stopping = True
        for child in self.children.values():
            if child.poll() is None:
                child.terminate()
        for child in self.children.values():
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)

    def run(self) -> int:
        self.start()
        try:
            while not self._stopping:
                for child in self.children.values():
                    code = child.poll()
                    if code is not None:
                        return code
                sleep(self._poll_interval)
            return 0
        finally:
            self.stop()


def command_serve(paths: OrinPaths, profile: RuntimeProfile, console: Console) -> int:
    import logging

    log = logging.getLogger("orin.serve")
    paths.ensure()
    try:
        environment = load_server_environment(paths, profile)
    except ConfigurationError as error:
        console.error(str(error))
        return 2
    apply_migrations(environment, profile, log=log)
    if migrate_data_layout(paths):
        log.info("moved managed workspaces into the per-profile layout")
    commands = {name: profile.service_command(name) for name in SERVICES}
    supervisor = ServeSupervisor(commands, child_environment(environment.values, {}), cwd=profile.repository or paths.data)

    def request_stop(_signum, _frame) -> None:  # noqa: ANN001 - signal handler signature
        supervisor._stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    console.line(f"Orin server em {environment.values['ORIN_PUBLIC_URL']} (escutando {environment.values['ORIN_BACKEND_HOST']}:{environment.values['ORIN_BACKEND_PORT']})")
    return supervisor.run()


__all__ = ["ServeSupervisor", "command_serve"]
```


`src/agentos/accounts/session_security.py`: acrescente a função de módulo e faça o método delegar:

```python
def revoke_user_sessions(engine: Engine, user_id: str, *, keep_session_id: str | None = None) -> None:
    condition = security_sessions.c.user_id == user_id
    if keep_session_id is not None:
        condition = condition & (security_sessions.c.session_id != keep_session_id)
    with engine.begin() as connection:
        connection.execute(update(security_sessions).where(condition).values(revoked=True))
```

```python
    def revoke_user(self, user_id: str, *, keep_session_id: str | None = None) -> None:
        revoke_user_sessions(self._engine, user_id, keep_session_id=keep_session_id)
```

e inclua `"revoke_user_sessions"` no `__all__`.

`src/agentos/launcher/users.py`:

```python
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
from agentos.persistence.sqlite import create_local_engine

from agentos.persistence.sqlite import sqlite_url

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
```


`src/agentos/launcher/cli.py`:
- Em `build_parser`, depois de `commands.add_parser("status", ...)`:

```python
    commands.add_parser("serve", help="run Orin as a multi-profile server in the foreground (needs ORIN_PUBLIC_URL)")
    user = commands.add_parser("user", help="manage server profiles from the terminal")
    user_commands = user.add_subparsers(dest="user_command", metavar="action", required=True)
    create = user_commands.add_parser("create", help="create a profile (asks for the password)")
    create.add_argument("username")
    create.add_argument("--admin", action="store_true", help="give the profile admin rights")
    create.add_argument("--display-name", default=None)
    reset = user_commands.add_parser("reset-password", help="issue a temporary password and end the profile's sessions")
    reset.add_argument("username")
```

e acrescente ao `epilog` as linhas `"  orin serve              run as a server (behind a reverse proxy)\n"` e `"  orin user create ana    create a server profile\n"`.
- Em `main`, antes de `if command in (None, "start"):`:

```python
        if command == "serve":
            from .serve import command_serve
            return command_serve(paths, profile, console)
        if command == "user":
            from .users import command_user
            return command_user(arguments, paths, console)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/launcher -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/launcher src/agentos/accounts/session_security.py tests/unit/launcher
git commit -m "feat(launcher): add orin serve and orin user for server instances

serve runs the three services in the foreground with server settings and
exits when any of them dies, so a container or systemd restarts it; user
creates profiles and resets a forgotten admin password from the terminal.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Suíte de isolamento entre perfis

**Files:**
- Test: `tests/integration/api/test_profile_isolation.py`
- Modify (conforme o que a suíte revelar): stores em `src/agentos/**` — nunca o gateway como único filtro.

**Interfaces:**
- Consumes: `build_server_world` (Task 11), `PostgresProjectStore.create`, `PostgresChatStore.create`, `ScheduledChatService.create`, tabela `agent_memories`, rotas `/v1/skills`, `/v1/mcp/servers`, `/v1/uploads`, `/v1/runtime/settings`.
- Produces: o critério de pronto da etapa — nenhuma rota entrega ou altera dado de outro perfil, e nenhuma rota fica sem classificação.

- [ ] **Step 1: Escrever a suíte**

`tests/integration/api/test_profile_isolation.py`:

```python
"""Profile B must never read, change or observe profile A's data.

Every /v1 route must be classified below. A new route without a class fails
``test_every_route_is_classified`` until someone decides how it is isolated.
"""
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import insert

from agentos.conversations.chat import PostgresChatStore
from agentos.installation import reset_cached_paths
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import agent_memories, provider_configurations, provider_model_catalog
from agentos.projects.store import PostgresProjectStore
from agentos.scheduler.scheduled_chats import ScheduledChatInput, ScheduledChatService

from tests.integration.server_world import build_server_world

SECRET = "SEGREDO-A"

# A route is OWNED when its path names a resource that belongs to someone;
# B must get 404 (reads) or 404/422 (writes with a placeholder body) and A's
# data must be intact afterwards. LISTING routes must not mention A's data.
# The other classes hold no other profile's data by construction.
OWNED = {
    ("DELETE", "/v1/uploads/{upload_id}"),
    ("GET", "/v1/projects/{project_id}"), ("PATCH", "/v1/projects/{project_id}"),
    ("POST", "/v1/projects/{project_id}/archive"), ("POST", "/v1/projects/{project_id}/conversations"),
    ("GET", "/v1/projects/{project_id}/memories"), ("DELETE", "/v1/projects/{project_id}/memories/{memory_id}"),
    ("DELETE", "/v1/schedules/{schedule_id}"),
    ("DELETE", "/v1/memories/{memory_id}"), ("PATCH", "/v1/memories/{memory_id}"),
    ("GET", "/v1/conversations/{conversation_id}"),
    ("POST", "/v1/conversations/{conversation_id}/workspace/inspect"),
    ("PUT", "/v1/conversations/{conversation_id}/workspace"),
    ("DELETE", "/v1/conversations/{conversation_id}/workspace"),
    ("GET", "/v1/conversations/{conversation_id}/files/{path:path}"),
    ("POST", "/v1/conversations/{conversation_id}/files/{path:path}/open"),
    ("POST", "/v1/conversations/{conversation_id}/messages"),
    ("POST", "/v1/conversations/{conversation_id}/cancel"),
    ("GET", "/v1/conversations/{conversation_id}/overview"),
    ("GET", "/v1/conversations/{conversation_id}/events"),
    ("GET", "/v1/skills/{skill_id}"), ("PUT", "/v1/skills/{skill_id}"),
    ("DELETE", "/v1/skills/{skill_id}/versions/{version}"), ("GET", "/v1/skills/{skill_id}/agents"),
    ("GET", "/v1/mcp/servers/{server_id}"), ("POST", "/v1/mcp/servers/{server_id}/approve"),
    ("POST", "/v1/mcp/servers/{server_id}/test"), ("POST", "/v1/mcp/servers/{server_id}/oauth/start"),
    ("POST", "/v1/mcp/servers/{server_id}/oauth/cancel"), ("PUT", "/v1/mcp/servers/{server_id}/enabled"),
    ("PUT", "/v1/mcp/servers/{server_id}/tools/{tool_name}/enabled"), ("DELETE", "/v1/mcp/servers/{server_id}"),
    ("POST", "/v1/executions/{execution_id}/control"), ("POST", "/v1/executions/{execution_id}/input"),
    ("GET", "/v1/executions/{execution_id}"),
    ("GET", "/v1/agents/{agent_id}/skills"), ("PUT", "/v1/agents/{agent_id}/skills"),
    ("POST", "/v1/plugins/{plugin_id}/approve"), ("PUT", "/v1/plugins/{plugin_id}/enabled"),
    ("PUT", "/v1/plugins/{plugin_id}/hooks-enabled"), ("DELETE", "/v1/plugins/{plugin_id}"),
    ("PATCH", "/v1/providers/{provider}/keys/{key_id}"), ("DELETE", "/v1/providers/{provider}/keys/{key_id}"),
    ("POST", "/v1/events/streams/{stream_id}/read"), ("GET", "/v1/events/streams/{stream_id}"),
    *{("GET", f"/v1/{resource}/{{resource_id}}") for resource in ("agents", "capabilities", "tools", "workspaces", "artifacts")},
}
LISTING = {
    ("GET", "/v1/conversations"), ("GET", "/v1/schedules"), ("GET", "/v1/projects"), ("GET", "/v1/projects/sidebar"),
    ("GET", "/v1/memories"), ("GET", "/v1/skills"), ("GET", "/v1/plugins"), ("GET", "/v1/plugins/commands"),
    ("GET", "/v1/plugins/marketplaces"), ("GET", "/v1/mcp/servers"), ("GET", "/v1/executions"),
    ("GET", "/v1/runtime/quality"), ("GET", "/v1/files"),
    *{("GET", f"/v1/{resource}") for resource in ("agents", "capabilities", "tools", "workspaces", "artifacts")},
}
SCOPED = {
    ("GET", "/v1/providers/{provider}"), ("PUT", "/v1/providers/{provider}"), ("DELETE", "/v1/providers/{provider}"),
    ("GET", "/v1/providers/{provider}/keys"), ("POST", "/v1/providers/{provider}/keys"),
    ("PUT", "/v1/providers/{provider}/keys:reorder"), ("PUT", "/v1/providers/{provider}/keys:cooldown"),
    ("POST", "/v1/providers/{provider}/models:refresh"), ("GET", "/v1/providers/{provider}/models"),
    ("POST", "/v1/providers/{provider}/models"), ("DELETE", "/v1/providers/{provider}/custom-models/{model_id:path}"),
    ("PUT", "/v1/providers/{provider}/favorites/{model_id:path}"), ("DELETE", "/v1/providers/{provider}/favorites/{model_id:path}"),
    ("POST", "/v1/providers/ollama/test"),
    ("GET", "/v1/runtime/settings"), ("PUT", "/v1/runtime/settings"),
    ("GET", "/v1/code-mode/settings"), ("PUT", "/v1/code-mode/settings"),
    ("GET", "/v1/settings/vision-model"), ("PUT", "/v1/settings/vision-model"),
    ("GET", "/v1/files/download"),
}
CREATE = {
    ("POST", "/v1/executions"), ("POST", "/v1/uploads"), ("POST", "/v1/workspaces/inspect"), ("POST", "/v1/conversations"),
    ("POST", "/v1/schedules"), ("POST", "/v1/projects"), ("POST", "/v1/skills"), ("POST", "/v1/plugins/inspect"),
    ("POST", "/v1/plugins/library/infer-mcp"), ("POST", "/v1/plugins/marketplaces"), ("POST", "/v1/mcp/servers"),
    ("POST", "/v1/events/streams"), ("POST", "/v1/files/folders"), ("POST", "/v1/files/import"),
}
INSTANCE = {
    ("GET", "/v1/plugins/library"), ("GET", "/v1/mcp/catalog"), ("GET", "/v1/installation/status"),
    ("DELETE", "/v1/installation/versions/{version}"), ("POST", "/v1/installation/update"),
    ("POST", "/v1/providers/omniroute/test"), ("POST", "/v1/providers/omniroute/install"), ("GET", "/v1/providers/omniroute/install"),
    ("GET", "/v1/providers/omniroute/runtime"), ("PUT", "/v1/providers/omniroute/runtime"), ("POST", "/v1/providers/omniroute/runtime/actions"),
    # Bound to a single-use state value created by the profile that started the sign-in.
    ("GET", "/v1/mcp/oauth/callback"),
}
AUTH = {("GET", "/v1/auth/me"), ("POST", "/v1/auth/setup"), ("POST", "/v1/auth/login"), ("POST", "/v1/auth/logout"), ("POST", "/v1/auth/password")}
ADMIN = {("GET", "/v1/admin/users"), ("POST", "/v1/admin/users"), ("PATCH", "/v1/admin/users/{user_id}"), ("POST", "/v1/admin/users/{user_id}/reset-password")}
CLASSIFIED = OWNED | LISTING | SCOPED | CREATE | INSTANCE | AUTH | ADMIN


@pytest.fixture()
def world(tmp_path, monkeypatch):
    yield build_server_world(tmp_path, monkeypatch)
    reset_cached_paths()


def _routes(app):
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/v1"):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                yield method, route.path


def test_every_route_is_classified(world):
    unclassified = sorted(set(_routes(world.app)) - CLASSIFIED)
    assert not unclassified, f"classify these routes in test_profile_isolation.py: {unclassified}"
    stale = sorted(CLASSIFIED - set(_routes(world.app)))
    assert not stale, f"these classified routes no longer exist: {stale}"


def _seed(world, a):
    now = datetime.now(UTC)
    engine = world.engine
    project = PostgresProjectStore(engine).create(user_id=a.user_id, name=f"{SECRET} projeto", description=None)
    chat = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "cursor-secret"))
    conversation = chat.create(user_id=a.user_id, message=f"{SECRET} conversa", provider="openrouter", model_id="model-1", idempotency_key="seed-a")
    with engine.begin() as connection:
        connection.execute(insert(agent_memories).values(memory_id="mem_a", user_id=a.user_id, fact=f"{SECRET} memória", tags=[], created_at=now, updated_at=now))
        connection.execute(insert(provider_model_catalog).values(
            user_id=a.user_id, provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id=a.user_id, provider="openrouter", enabled=True, model=None, base_url=None, secret_ref="test",
            key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))
    schedule = ScheduledChatService(engine).create(a.user_id, ScheduledChatInput(f"{SECRET} agenda", "openrouter", "model-1", "UTC", "hourly"), idempotency_key="seed-a")
    skill = a.api.post("/v1/skills", json={"name": f"{SECRET} skill", "description": "d", "instructions": "faça"}, headers=a.headers())
    assert skill.status_code == 201, skill.text
    server = a.api.post("/v1/mcp/servers", json={"display_name": f"{SECRET} mcp", "transport": "http", "url": "https://mcp.example.com/mcp"}, headers=a.headers())
    assert server.status_code == 201, server.text
    upload = a.api.post("/v1/uploads", files={"file": ("a.txt", f"{SECRET} upload".encode(), "text/plain")}, headers=a.headers())
    assert upload.status_code == 201, upload.text
    a.api.post("/v1/files/folders", json={"parent": "", "name": "segredo-a"}, headers=a.headers())
    return {
        "project_id": project.project_id,
        "conversation_id": conversation.conversation_id,
        "memory_id": "mem_a",
        "schedule_id": schedule["schedule_id"],
        "skill_id": skill.json()["skill_id"],
        "version": "1.0.0",
        "server_id": server.json()["server_id"],
        "tool_name": "anything",
        "upload_id": upload.json()["upload_id"],
        "path": "notes.md",
    }


SEEDED_PARAMS = {"project_id", "conversation_id", "memory_id", "schedule_id", "skill_id", "version", "server_id", "tool_name", "upload_id", "path"}


def _fill(path: str, seeds: dict[str, str]) -> tuple[str, bool]:
    seeded = True
    for segment in path.split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            name = segment[1:-1].split(":")[0]
            value = seeds.get(name)
            if value is None:
                value, seeded = f"missing-{name}", False
            path = path.replace(segment, value)
    return path, seeded


def _snapshot(a, seeds):
    paths = [f"/v1/projects/{seeds['project_id']}", f"/v1/conversations/{seeds['conversation_id']}",
             f"/v1/skills/{seeds['skill_id']}", f"/v1/mcp/servers/{seeds['server_id']}", "/v1/memories", "/v1/schedules"]
    snapshot = {}
    for path in paths:
        response = a.api.get(path)
        assert response.status_code == 200, (path, response.text)
        snapshot[path] = response.json()
    return snapshot


def _without_volatile(value):
    if isinstance(value, dict):
        return {key: _without_volatile(item) for key, item in value.items() if key not in {"updated_at", "last_seen_at", "next_fire_at", "checked_at"}}
    if isinstance(value, list):
        return [_without_volatile(item) for item in value]
    return value


def test_profile_b_cannot_reach_profile_a(world):
    a = world.admin()
    b = world.member("bruno", admin=a)
    seeds = _seed(world, a)
    before = _without_volatile(_snapshot(a, seeds))
    leaks: list[str] = []

    for method, template in sorted(OWNED):
        path, seeded = _fill(template, seeds)
        headers = {**b.headers(), "Content-Type": "application/json"}
        if template.endswith("/events") or template == "/v1/events/streams/{stream_id}":
            with b.api.stream(method, path, headers=headers) as response:
                status, text = response.status_code, ""
        else:
            response = b.api.request(method, path, headers=headers, content=json.dumps({}) if method != "GET" else None)
            status, text = response.status_code, response.text
        if SECRET in text:
            leaks.append(f"{method} {template} leaked A's data")
        if seeded:
            allowed = {404} if method in {"GET", "DELETE"} else {404, 422}
        else:
            allowed = set(range(400, 600))
        if status not in allowed:
            leaks.append(f"{method} {template} answered {status} to profile B")

    for method, template in sorted(LISTING):
        response = b.api.request(method, template, params={"path": ""} if template == "/v1/files" else None)
        if SECRET in response.text or any(str(value) in response.text for key, value in seeds.items() if key in {"project_id", "conversation_id", "skill_id", "server_id", "schedule_id"}):
            leaks.append(f"{method} {template} lists A's data")
        if template == "/v1/files" and "segredo-a" in response.text:
            leaks.append("B sees A's file area")

    assert not leaks, "\n".join(leaks)
    assert _without_volatile(_snapshot(a, seeds)) == before


def test_admin_routes_refuse_a_member(world):
    a = world.admin()
    b = world.member("bruno", admin=a)
    for method, template in sorted(ADMIN):
        path, _ = _fill(template, {"user_id": a.user_id})
        response = b.api.request(method, path, headers={**b.headers(), "Content-Type": "application/json"}, content=json.dumps({}) if method != "GET" else None)
        assert response.status_code in {403, 422}, (method, template, response.status_code)
        if response.status_code == 403:
            assert response.json()["error"]["code"] == "admin_required"


def test_per_profile_settings_do_not_cross(world):
    a = world.admin()
    b = world.member("bruno", admin=a)
    assert a.api.put("/v1/runtime/settings", json={"max_iterations": 7}, headers=a.headers()).status_code == 200
    assert b.api.get("/v1/runtime/settings").json().get("max_iterations") != 7
```

Antes de rodar, confira os formatos que a suíte supõe e ajuste o teste (não a produção) se divergirem: corpo de `PUT /v1/runtime/settings` (`grep -n "class .*RuntimeSettings.*Request" -A4 src/agentos/api/gateway.py`), chave `skill_id` na resposta de `POST /v1/skills`, chave `schedule_id` no retorno de `ScheduledChatService.create`, e o atributo `project_id` de `ProjectRecord`.

- [ ] **Step 2: Rodar**

Run: `python -m pytest tests/integration/api/test_profile_isolation.py -q`
Expected: `test_every_route_is_classified` PASS (ajuste as tabelas até casarem exatamente com as rotas registradas; os nomes de parâmetro vêm de `route.path`). Nos demais, qualquer falha lista as rotas que vazam ou respondem 2xx para o perfil B.

- [ ] **Step 3: Corrigir cada vazamento no store dono do recurso**

Para cada linha listada pela falha:
1. Localize o handler no gateway e a chamada de store/porta que ele faz.
2. Corrija o store para filtrar por `user_id` (consultas) ou exigir `user_id` na condição do `UPDATE`/`DELETE` (escritas), levantando `ApplicationNotFoundError` (ou o "not found" próprio do domínio, como `McpServerNotFound`) quando não houver linha do dono.
3. Acrescente um teste unitário no arquivo de testes do store que prove o filtro, com dois `user_id`.
4. Rode a suíte de novo.

Não acrescente checagens de dono só no gateway: a ADR 006 exige o escopo no store. Se a correção exigir mudar a assinatura de um store usado pelo worker, atualize também a chamada do worker.

- [ ] **Step 4: Rodar tudo**

Run: `python -m pytest tests/integration/api/test_profile_isolation.py -q`
Expected: PASS.

Run: `python -m pytest tests/unit tests/integration -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/api/test_profile_isolation.py src/agentos
git commit -m "test(server): prove profiles cannot reach each other through any route

Every /v1 route is classified, profile B probes each of profile A's
resources by id and every listing, and any leak found was fixed in the
owning store rather than in the gateway.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Se o Step 3 corrigiu algo, separe as correções em commits `fix(<escopo>): scope <recurso> to its owner` antes deste.)

---

### Task 16: Frontend — estado de sessão, cliente e chamadas de autenticação

**Files:**
- Create: `frontend/src/api/session.ts`
- Create: `frontend/src/api/auth.ts`
- Modify: `frontend/src/api/browserSession.ts`
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/tests/unit/sessionClient.test.ts`
- Test: `frontend/tests/unit/authApi.test.ts`

**Interfaces:**
- Produces:
  - `session.ts`:
    - `type Capabilities = { shell: boolean; mcp_stdio: boolean; plugin_hooks: boolean; omniroute: boolean; host_folders: boolean; profile_files: boolean; open_in_desktop_app: boolean; ui_updater: boolean; user_admin: boolean }`
    - `LOCAL_CAPABILITIES: Capabilities` (igual ao `for_mode(LOCAL)` do backend)
    - `setSessionCsrf(token: string | undefined): void`, `getSessionCsrf(): string | undefined`
    - `notifyUnauthenticated(): void`, `onUnauthenticated(listener: () => void): () => void`
  - `browserSession.ts`: `BrowserSessionBootstrap` ganha `{ status: 'session' }` quando `agentos-auth-mode` é `session`.
  - `client.ts`: `ApiClientOptions.csrfToken?: string | (() => string | undefined)` e `ApiClientOptions.onUnauthenticated?: () => void` (chamado em toda resposta 401 de `request` e `upload`); `createBrowserApiClient()` no modo `session` usa `getSessionCsrf` e `notifyUnauthenticated`.
  - `auth.ts`:
    - `type SessionUser = { userId: string; username: string; displayName: string; role: 'admin' | 'member'; active: boolean; mustChangePassword: boolean }`
    - `type SessionState = { user: SessionUser; csrfToken: string | null; capabilities: Capabilities }`
    - `getMe(client, signal?) => Promise<SessionState>`, `setupInstance(client, { token, username, password }) => Promise<SessionState>`, `login(client, { username, password }) => Promise<SessionState>`, `logout(client) => Promise<void>`, `changePassword(client, { currentPassword, newPassword }) => Promise<SessionState>`
    - `listUsers(client) => Promise<SessionUser[]>`, `createUser(client, { username, displayName, role }) => Promise<{ user: SessionUser; temporaryPassword: string }>`, `updateUser(client, userId, { displayName?, role?, active? }) => Promise<SessionUser>`, `resetUserPassword(client, userId) => Promise<{ user: SessionUser; temporaryPassword: string }>`
    - `parseSessionState(value: unknown): SessionState`, `parseUser(value: unknown): SessionUser`

- [ ] **Step 1: Escrever os testes que falham**

`frontend/tests/unit/sessionClient.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { readBrowserSessionBootstrap } from '../../src/api/browserSession'
import { getSessionCsrf, notifyUnauthenticated, onUnauthenticated, setSessionCsrf } from '../../src/api/session'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => setSessionCsrf(undefined))

describe('session-aware client', () => {
  it('reads the CSRF token at request time', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({})))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, csrfToken: getSessionCsrf })
    setSessionCsrf('first')
    await client.request({ path: '/v1/a', method: 'POST', body: {}, parse: (v) => v })
    setSessionCsrf('second')
    await client.request({ path: '/v1/a', method: 'POST', body: {}, parse: (v) => v })
    const tokens = fetchImpl.mock.calls.map(([, init]) => new Headers(init?.headers).get('X-CSRF-Token'))
    expect(tokens).toEqual(['first', 'second'])
  })

  it('reports a 401 before throwing', async () => {
    const onUnauthenticated = vi.fn()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ error: { code: 'authentication_required', category: 'AUTHENTICATION', retryable: false } }, 401)))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, onUnauthenticated })
    await expect(client.request({ path: '/v1/a', parse: (v) => v })).rejects.toMatchObject({ status: 401 })
    expect(onUnauthenticated).toHaveBeenCalledOnce()
  })

  it('broadcasts unauthenticated events to subscribers', () => {
    const listener = vi.fn()
    const unsubscribe = onUnauthenticated(listener)
    notifyUnauthenticated()
    unsubscribe()
    notifyUnauthenticated()
    expect(listener).toHaveBeenCalledOnce()
  })

  it('recognises the session auth mode', () => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="session">'
    expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'session' })
    document.head.innerHTML = ''
  })
})
```

`frontend/tests/unit/authApi.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { createUser, getMe, login, parseSessionState } from '../../src/api/auth'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const USER = { user_id: 'local-user', username: 'carla', display_name: 'Carla', role: 'admin', active: true, must_change_password: false, created_at: '2026-10-02T12:00:00+00:00', updated_at: '2026-10-02T12:00:00+00:00', password_changed_at: '2026-10-02T12:00:00+00:00' }
const CAPS = { shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }

describe('auth api', () => {
  it('parses the session state', () => {
    expect(parseSessionState({ user: USER, csrf_token: 'abc', capabilities: CAPS })).toEqual({
      user: { userId: 'local-user', username: 'carla', displayName: 'Carla', role: 'admin', active: true, mustChangePassword: false },
      csrfToken: 'abc',
      capabilities: CAPS,
    })
  })

  it('rejects a malformed session', () => {
    expect(() => parseSessionState({ user: { ...USER, role: 'root' }, csrf_token: 'x', capabilities: CAPS })).toThrow()
    expect(() => parseSessionState({ user: USER, csrf_token: 'x', capabilities: { ...CAPS, shell: 'yes' } })).toThrow()
  })

  it('posts credentials and reads me', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ user: USER, csrf_token: 'abc', capabilities: CAPS })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
    await login(client, { username: 'carla', password: 'a long password' })
    await getMe(client)
    expect(fetchImpl.mock.calls[0][0]).toBe('/v1/auth/login')
    expect(JSON.parse(String(fetchImpl.mock.calls[0][1]?.body))).toEqual({ username: 'carla', password: 'a long password' })
    expect(fetchImpl.mock.calls[1][0]).toBe('/v1/auth/me')
  })

  it('returns the temporary password of a new profile', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ user: { ...USER, user_id: 'usr_1', username: 'bruno', role: 'member', must_change_password: true }, temporary_password: 'Tmp-123456789012' }, 201)))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
    const created = await createUser(client, { username: 'bruno', displayName: '', role: 'member' })
    expect(created.temporaryPassword).toBe('Tmp-123456789012')
    expect(created.user.mustChangePassword).toBe(true)
  })
})
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd frontend && npx vitest run tests/unit/sessionClient.test.ts tests/unit/authApi.test.ts`
Expected: FAIL (módulos `session` e `auth` não existem).

- [ ] **Step 3: Implementar**

`frontend/src/api/session.ts`:

```ts
/** Module-level session state shared by every ApiClient the app creates. */
export type Capabilities = {
  shell: boolean
  mcp_stdio: boolean
  plugin_hooks: boolean
  omniroute: boolean
  host_folders: boolean
  profile_files: boolean
  open_in_desktop_app: boolean
  ui_updater: boolean
  user_admin: boolean
}

export const CAPABILITY_NAMES = ['shell', 'mcp_stdio', 'plugin_hooks', 'omniroute', 'host_folders', 'profile_files', 'open_in_desktop_app', 'ui_updater', 'user_admin'] as const

export const LOCAL_CAPABILITIES: Capabilities = Object.freeze({
  shell: true, mcp_stdio: true, plugin_hooks: true, omniroute: true, host_folders: true,
  profile_files: false, open_in_desktop_app: true, ui_updater: true, user_admin: false,
})

let csrfToken: string | undefined
const UNAUTHENTICATED_EVENT = 'orin:unauthenticated'

export function setSessionCsrf(token: string | undefined): void {
  csrfToken = token || undefined
}

export function getSessionCsrf(): string | undefined {
  return csrfToken
}

export function notifyUnauthenticated(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT))
}

export function onUnauthenticated(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined
  window.addEventListener(UNAUTHENTICATED_EVENT, listener)
  return () => window.removeEventListener(UNAUTHENTICATED_EVENT, listener)
}
```

`frontend/src/api/browserSession.ts`:

```ts
export type BrowserSessionBootstrap =
  | { status: 'ready'; csrfToken: string }
  | { status: 'loopback' }
  | { status: 'session' }
  | { status: 'missing_csrf' }

export function readBrowserSessionBootstrap(documentRef?: Document): BrowserSessionBootstrap {
  const authMode = documentRef
    ?.querySelector<HTMLMetaElement>('meta[name="agentos-auth-mode"]')
    ?.content
    .trim()
  if (authMode === 'loopback') return { status: 'loopback' }
  // Server mode: the CSRF token arrives from /v1/auth/me after sign-in.
  if (authMode === 'session') return { status: 'session' }

  const csrfToken = documentRef
    ?.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')
    ?.content
    .trim()

  if (!csrfToken || csrfToken.length > 255) return { status: 'missing_csrf' }
  return { status: 'ready', csrfToken }
}
```

`frontend/src/api/client.ts`:
- Import: `import { getSessionCsrf, notifyUnauthenticated } from './session'`.
- Em `ApiClientOptions`, troque `csrfToken?: string` por `csrfToken?: string | (() => string | undefined)` e acrescente `onUnauthenticated?: () => void`.
- Na classe, troque `private readonly csrfToken?: string` por `private readonly csrfSource?: string | (() => string | undefined)` e acrescente `private readonly onUnauthenticated?: () => void`; no construtor, `this.csrfSource = options.csrfToken` e `this.onUnauthenticated = options.onUnauthenticated`; acrescente:

```ts
  private csrf(): string | undefined {
    return typeof this.csrfSource === 'function' ? this.csrfSource() : this.csrfSource
  }
```

- Em `request` e `upload`, troque `this.csrfToken` por `this.csrf()`. Em `request`, logo antes de `const error = await parseApiErrorResponse(response)`, e em `upload`, logo antes de `throw await parseApiErrorResponse(response)`, acrescente:

```ts
          if (response.status === 401) this.onUnauthenticated?.()
```

- `createBrowserApiClient`:

```ts
export function createBrowserApiClient(options: BrowserApiClientOptions = {}): ApiClient {
  const bootstrap = typeof document === 'undefined'
    ? { status: 'missing_csrf' as const }
    : readBrowserSessionBootstrap(document)
  if (bootstrap.status === 'session') {
    return new ApiClient({ ...options, csrfToken: getSessionCsrf, onUnauthenticated: notifyUnauthenticated, maxAttempts: 1 })
  }
  const csrfToken = bootstrap.status === 'ready' ? bootstrap.csrfToken : undefined
  return new ApiClient({ ...options, csrfToken, maxAttempts: 1 })
}
```

`frontend/src/api/auth.ts`:

```ts
import type { ApiClient } from './client'
import { invalidResponseError } from './errors'
import { CAPABILITY_NAMES, type Capabilities } from './session'

export type SessionUser = {
  userId: string
  username: string
  displayName: string
  role: 'admin' | 'member'
  active: boolean
  mustChangePassword: boolean
}

export type SessionState = { user: SessionUser; csrfToken: string | null; capabilities: Capabilities }
export type CreatedUser = { user: SessionUser; temporaryPassword: string }

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

export function parseUser(value: unknown): SessionUser {
  const data = record(value)
  if (typeof data.user_id !== 'string' || typeof data.username !== 'string') throw invalidResponseError()
  if (data.role !== 'admin' && data.role !== 'member') throw invalidResponseError()
  return {
    userId: data.user_id,
    username: data.username,
    displayName: typeof data.display_name === 'string' && data.display_name ? data.display_name : data.username,
    role: data.role,
    active: data.active === true,
    mustChangePassword: data.must_change_password === true,
  }
}

function parseCapabilities(value: unknown): Capabilities {
  const data = record(value)
  const result = {} as Capabilities
  for (const name of CAPABILITY_NAMES) {
    if (typeof data[name] !== 'boolean') throw invalidResponseError()
    result[name] = data[name] as boolean
  }
  return result
}

export function parseSessionState(value: unknown): SessionState {
  const data = record(value)
  return {
    user: parseUser(data.user),
    csrfToken: typeof data.csrf_token === 'string' ? data.csrf_token : null,
    capabilities: parseCapabilities(data.capabilities),
  }
}

function parseCreated(value: unknown): CreatedUser {
  const data = record(value)
  if (typeof data.temporary_password !== 'string') throw invalidResponseError()
  return { user: parseUser(data.user), temporaryPassword: data.temporary_password }
}

export function getMe(client: ApiClient, signal?: AbortSignal): Promise<SessionState> {
  return client.request({ path: '/v1/auth/me', signal, parse: parseSessionState })
}

export function setupInstance(client: ApiClient, input: { token: string; username: string; password: string }): Promise<SessionState> {
  return client.request({ path: '/v1/auth/setup', method: 'POST', body: input, expectedStatus: 201, parse: parseSessionState })
}

export function login(client: ApiClient, input: { username: string; password: string }): Promise<SessionState> {
  return client.request({ path: '/v1/auth/login', method: 'POST', body: input, parse: parseSessionState })
}

export function logout(client: ApiClient): Promise<void> {
  return client.request({ path: '/v1/auth/logout', method: 'POST', expectedStatus: 204, parse: () => undefined })
}

export function changePassword(client: ApiClient, input: { currentPassword: string; newPassword: string }): Promise<SessionState> {
  return client.request({
    path: '/v1/auth/password', method: 'POST',
    body: { current_password: input.currentPassword, new_password: input.newPassword },
    parse: parseSessionState,
  })
}

export function listUsers(client: ApiClient): Promise<SessionUser[]> {
  return client.request({
    path: '/v1/admin/users',
    parse: (value) => {
      const items = record(value).items
      if (!Array.isArray(items)) throw invalidResponseError()
      return items.map(parseUser)
    },
  })
}

export function createUser(client: ApiClient, input: { username: string; displayName: string; role: 'admin' | 'member' }): Promise<CreatedUser> {
  return client.request({
    path: '/v1/admin/users', method: 'POST', expectedStatus: 201,
    body: { username: input.username, display_name: input.displayName || null, role: input.role },
    parse: parseCreated,
  })
}

export function updateUser(client: ApiClient, userId: string, patch: { displayName?: string; role?: 'admin' | 'member'; active?: boolean }): Promise<SessionUser> {
  return client.request({
    path: `/v1/admin/users/${encodeURIComponent(userId)}`, method: 'PATCH',
    body: { display_name: patch.displayName, role: patch.role, active: patch.active },
    parse: (value) => parseUser(record(value).user),
  })
}

export function resetUserPassword(client: ApiClient, userId: string): Promise<CreatedUser> {
  return client.request({ path: `/v1/admin/users/${encodeURIComponent(userId)}/reset-password`, method: 'POST', parse: parseCreated })
}
```

(`JSON.stringify` omite chaves `undefined`, então o `PATCH` só envia o que mudou.)

- [ ] **Step 4: Rodar e ver passar**

Run: `cd frontend && npx vitest run tests/unit/sessionClient.test.ts tests/unit/authApi.test.ts && npx tsc -b --noEmit`
Expected: PASS.

Run: `cd frontend && npx vitest run`
Expected: PASS (clientes existentes continuam iguais nos modos `loopback` e `ready`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/session.ts frontend/src/api/auth.ts frontend/src/api/browserSession.ts frontend/src/api/client.ts frontend/tests/unit/sessionClient.test.ts frontend/tests/unit/authApi.test.ts
git commit -m "feat(web): session-mode client with a live csrf token and auth calls

Every client reads the token issued at sign-in when it sends a request, and
any 401 is broadcast so the app can return to the sign-in screen.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Frontend — portão de sessão e telas de login, setup e troca de senha

**Files:**
- Create: `frontend/src/app/useSession.ts`
- Create: `frontend/src/app/SessionGate.tsx`
- Create: `frontend/src/features/auth/AuthLayout.tsx`
- Create: `frontend/src/features/auth/LoginPage.tsx`
- Create: `frontend/src/features/auth/SetupPage.tsx`
- Create: `frontend/src/features/auth/ChangePasswordPage.tsx`
- Create: `frontend/src/features/auth/authErrors.ts`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/styles/agentos.css` (estilos `.auth-*` no fim)
- Test: `frontend/tests/unit/SessionGate.test.tsx`

**Interfaces:**
- Consumes: Task 16.
- Produces:
  - `useSession.ts`: `type SessionContextValue = { mode: 'local' | 'session'; user: SessionUser | null; capabilities: Capabilities; isAdmin: boolean; refresh: () => Promise<void>; signOut: () => Promise<void> }`, `SessionContext`, `useSession(): SessionContextValue` (sem provider → modo local com `LOCAL_CAPABILITIES`).
  - `SessionGate({ children, client? })`: no modo `local` renderiza `children` direto; no modo `session` chama `getMe` e decide entre carregando, `/setup`, `/login`, `/change-password` e o app; guarda o CSRF com `setSessionCsrf`; volta para `/login?next=<rota>` em qualquer 401 notificado.
  - `authMessage(error: unknown): string` em `authErrors.ts`.

- [ ] **Step 1: Escrever o teste que falha**

`frontend/tests/unit/SessionGate.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { getSessionCsrf, notifyUnauthenticated, setSessionCsrf } from '../../src/api/session'
import { SessionGate } from '../../src/app/SessionGate'
import { useSession } from '../../src/app/useSession'

const USER = { user_id: 'local-user', username: 'carla', display_name: 'Carla', role: 'admin', active: true, must_change_password: false, created_at: 'x', updated_at: 'x', password_changed_at: 'x' }
const CAPS = { shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
function apiError(code: string, status: number, retryAfter: number | null = null): Response {
  return json({ error: { code, category: 'X', message_key: code, correlation_id: 'c', retryable: false, retry_after: retryAfter } }, status)
}

function Home() {
  const session = useSession()
  return <p>app de {session.user?.displayName}</p>
}

function renderGate(fetchImpl: typeof fetch, path = '/') {
  const client = new ApiClient({ fetchImpl, maxAttempts: 1, csrfToken: getSessionCsrf })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionGate client={client}>
        <Routes><Route path="*" element={<Home />} /></Routes>
      </SessionGate>
    </MemoryRouter>,
  )
}

beforeEach(() => { document.head.innerHTML = '<meta name="agentos-auth-mode" content="session">' })
afterEach(() => { document.head.innerHTML = ''; setSessionCsrf(undefined) })

describe('SessionGate', () => {
  it('renders the app directly in local mode', async () => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
    const fetchImpl = vi.fn<typeof fetch>()
    renderGate(fetchImpl)
    expect(await screen.findByText(/app de/)).toBeInTheDocument()
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('enters the app with a valid session and keeps the csrf token', async () => {
    renderGate(vi.fn<typeof fetch>(() => Promise.resolve(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }))))
    expect(await screen.findByText('app de Carla')).toBeInTheDocument()
    expect(getSessionCsrf()).toBe('tok')
  })

  it('signs in from the login screen', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(apiError('authentication_required', 401))
      .mockResolvedValueOnce(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Usuário'), 'carla')
    await userEvent.type(screen.getByLabelText('Senha'), 'a long password')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText('app de Carla')).toBeInTheDocument()
  })

  it('shows a generic error and the lock time', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(apiError('authentication_required', 401))
      .mockResolvedValueOnce(apiError('invalid_credentials', 401))
      .mockResolvedValueOnce(apiError('login_locked', 429, 600))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Usuário'), 'carla')
    await userEvent.type(screen.getByLabelText('Senha'), 'wrong password')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Usuário ou senha incorretos.')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Tente de novo em 10 min.')
  })

  it('asks for the setup token on a fresh instance', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(apiError('setup_required', 409))
      .mockResolvedValueOnce(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }, 201))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Token de setup'), 'the-token')
    await userEvent.type(screen.getByLabelText('Usuário'), 'carla')
    await userEvent.type(screen.getByLabelText('Senha'), 'a long password')
    await userEvent.type(screen.getByLabelText('Confirmar senha'), 'a long password')
    await userEvent.click(screen.getByRole('button', { name: 'Criar admin' }))
    expect(await screen.findByText('app de Carla')).toBeInTheDocument()
    expect(JSON.parse(String(fetchImpl.mock.calls[1][1]?.body))).toEqual({ token: 'the-token', username: 'carla', password: 'a long password' })
  })

  it('refuses mismatched setup passwords without calling the api', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(apiError('setup_required', 409))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Token de setup'), 't')
    await userEvent.type(screen.getByLabelText('Usuário'), 'carla')
    await userEvent.type(screen.getByLabelText('Senha'), 'a long password')
    await userEvent.type(screen.getByLabelText('Confirmar senha'), 'another password')
    await userEvent.click(screen.getByRole('button', { name: 'Criar admin' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('As senhas não conferem.')
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('forces a temporary password to be changed first', async () => {
    const pending = { ...USER, must_change_password: true }
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ user: pending, csrf_token: 'tok', capabilities: CAPS }))
      .mockResolvedValueOnce(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }))
    renderGate(fetchImpl)
    await userEvent.type(await screen.findByLabelText('Senha atual'), 'Tmp-123456789012')
    await userEvent.type(screen.getByLabelText('Nova senha'), 'my own password')
    await userEvent.type(screen.getByLabelText('Confirmar nova senha'), 'my own password')
    await userEvent.click(screen.getByRole('button', { name: 'Trocar senha' }))
    expect(await screen.findByText('app de Carla')).toBeInTheDocument()
  })

  it('goes back to the login screen when a request reports 401', async () => {
    renderGate(vi.fn<typeof fetch>(() => Promise.resolve(json({ user: USER, csrf_token: 'tok', capabilities: CAPS }))))
    await screen.findByText('app de Carla')
    notifyUnauthenticated()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Entrar' })).toBeInTheDocument())
    expect(getSessionCsrf()).toBeUndefined()
  })
})
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd frontend && npx vitest run tests/unit/SessionGate.test.tsx`
Expected: FAIL (módulos não existem).

- [ ] **Step 3: Implementar**

`frontend/src/app/useSession.ts`:

```ts
import { createContext, useContext } from 'react'
import type { SessionUser } from '../api/auth'
import { LOCAL_CAPABILITIES, type Capabilities } from '../api/session'

export type SessionContextValue = {
  mode: 'local' | 'session'
  user: SessionUser | null
  capabilities: Capabilities
  isAdmin: boolean
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

const LOCAL_SESSION: SessionContextValue = {
  mode: 'local', user: null, capabilities: LOCAL_CAPABILITIES, isAdmin: false,
  refresh: async () => undefined, signOut: async () => undefined,
}

export const SessionContext = createContext<SessionContextValue>(LOCAL_SESSION)

export function useSession(): SessionContextValue {
  return useContext(SessionContext)
}
```

`frontend/src/features/auth/authErrors.ts`:

```ts
import { ApiError } from '../../api/errors'

const MESSAGES: Record<string, string> = {
  invalid_credentials: 'Usuário ou senha incorretos.',
  invalid_setup_token: 'Token de setup inválido. Copie de novo do log do servidor.',
  setup_completed: 'Esta instância já tem um admin. Entre com usuário e senha.',
  weak_password: 'A senha precisa ter entre 10 e 256 caracteres.',
  invalid_username: 'Use de 3 a 64 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado.',
  username_taken: 'Esse usuário já existe.',
  last_admin: 'A instância precisa de pelo menos um admin ativo.',
}

export function authMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'login_locked') {
      const minutes = Math.max(1, Math.ceil((error.retryAfter ?? 60) / 60))
      return `Muitas tentativas. Tente de novo em ${minutes} min.`
    }
    if (MESSAGES[error.code]) return MESSAGES[error.code]
    if (error.status === 0) return 'Sem conexão com o servidor. Tente de novo.'
  }
  return 'Não foi possível concluir. Tente de novo.'
}
```

`frontend/src/features/auth/AuthLayout.tsx`:

```tsx
import type { ReactNode } from 'react'

export function AuthLayout({ title, lede, children }: { title: string; lede?: string; children: ReactNode }) {
  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <p className="eyebrow">ORIN</p>
        <h1 id="auth-title">{title}</h1>
        {lede && <p className="auth-card__lede">{lede}</p>}
        {children}
      </section>
    </main>
  )
}
```

`frontend/src/features/auth/LoginPage.tsx`:

```tsx
import { useState, type FormEvent } from 'react'
import { login, type SessionState } from '../../api/auth'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { AuthLayout } from './AuthLayout'
import { authMessage } from './authErrors'

export function LoginPage({ client, onSignedIn, onSetupRequired }: { client: ApiClient; onSignedIn: (session: SessionState) => void; onSetupRequired: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError(null)
    try {
      onSignedIn(await login(client, { username, password }))
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === 'setup_required') { onSetupRequired(); return }
      setError(authMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Entrar">
      <form className="auth-form" onSubmit={(event) => void submit(event)}>
        <label>Usuário<input name="username" autoComplete="username" autoCapitalize="none" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
        <label>Senha<input name="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        {error && <p className="auth-form__error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary" disabled={busy}>{busy ? 'Entrando…' : 'Entrar'}</button>
      </form>
    </AuthLayout>
  )
}
```

`frontend/src/features/auth/SetupPage.tsx`:

```tsx
import { useState, type FormEvent } from 'react'
import { setupInstance, type SessionState } from '../../api/auth'
import type { ApiClient } from '../../api/client'
import { AuthLayout } from './AuthLayout'
import { authMessage } from './authErrors'

export function SetupPage({ client, onReady }: { client: ApiClient; onReady: (session: SessionState) => void }) {
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (password !== confirmation) { setError('As senhas não conferem.'); return }
    setBusy(true); setError(null)
    try {
      onReady(await setupInstance(client, { token: token.trim(), username, password }))
    } catch (failure) {
      setError(authMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Criar o primeiro admin" lede="O token de setup aparece no log do servidor quando o Orin sobe pela primeira vez.">
      <form className="auth-form" onSubmit={(event) => void submit(event)}>
        <label>Token de setup<input name="token" autoComplete="off" autoCapitalize="none" spellCheck={false} value={token} onChange={(event) => setToken(event.target.value)} required /></label>
        <label>Usuário<input name="username" autoComplete="username" autoCapitalize="none" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
        <label>Senha<input name="password" type="password" autoComplete="new-password" minLength={10} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        <label>Confirmar senha<input name="confirmation" type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
        {error && <p className="auth-form__error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary" disabled={busy}>{busy ? 'Criando…' : 'Criar admin'}</button>
      </form>
    </AuthLayout>
  )
}
```

`frontend/src/features/auth/ChangePasswordPage.tsx`:

```tsx
import { useState, type FormEvent } from 'react'
import { changePassword, type SessionState, type SessionUser } from '../../api/auth'
import type { ApiClient } from '../../api/client'
import { AuthLayout } from './AuthLayout'
import { authMessage } from './authErrors'

export function ChangePasswordPage({ client, user, required, onChanged, onCancel, onSignOut }: {
  client: ApiClient
  user: SessionUser
  required: boolean
  onChanged: (session: SessionState) => void
  onCancel?: () => void
  onSignOut: () => void
}) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (next !== confirmation) { setError('As senhas não conferem.'); return }
    setBusy(true); setError(null)
    try {
      onChanged(await changePassword(client, { currentPassword: current, newPassword: next }))
    } catch (failure) {
      setError(authMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Trocar senha" lede={required ? `Olá, ${user.displayName}. Defina sua própria senha antes de continuar.` : undefined}>
      <form className="auth-form" onSubmit={(event) => void submit(event)}>
        <input type="text" name="username" autoComplete="username" value={user.username} readOnly hidden />
        <label>Senha atual<input name="current" type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} required /></label>
        <label>Nova senha<input name="new" type="password" autoComplete="new-password" minLength={10} value={next} onChange={(event) => setNext(event.target.value)} required /></label>
        <label>Confirmar nova senha<input name="confirmation" type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
        {error && <p className="auth-form__error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary" disabled={busy}>{busy ? 'Salvando…' : 'Trocar senha'}</button>
        {required
          ? <button type="button" className="button button--quiet" onClick={onSignOut}>Sair</button>
          : <button type="button" className="button button--quiet" onClick={onCancel}>Cancelar</button>}
      </form>
    </AuthLayout>
  )
}
```

`frontend/src/app/SessionGate.tsx`:

```tsx
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { getMe, logout, type SessionState } from '../api/auth'
import { readBrowserSessionBootstrap } from '../api/browserSession'
import { createBrowserApiClient, type ApiClient } from '../api/client'
import { ApiError } from '../api/errors'
import { onUnauthenticated, setSessionCsrf } from '../api/session'
import { ChangePasswordPage } from '../features/auth/ChangePasswordPage'
import { LoginPage } from '../features/auth/LoginPage'
import { SetupPage } from '../features/auth/SetupPage'
import { SessionContext, type SessionContextValue } from './useSession'

type GateState =
  | { status: 'loading' }
  | { status: 'anonymous' }
  | { status: 'setup' }
  | { status: 'error' }
  | { status: 'ready'; session: SessionState }

const AUTH_PATHS = new Set(['/login', '/setup', '/change-password'])

export function SessionGate({ children, client: providedClient }: { children: ReactNode; client?: ApiClient }) {
  const sessionMode = typeof document !== 'undefined' && readBrowserSessionBootstrap(document).status === 'session'
  if (!sessionMode) return <>{children}</>
  return <ServerSessionGate client={providedClient}>{children}</ServerSessionGate>
}

function ServerSessionGate({ children, client: providedClient }: { children: ReactNode; client?: ApiClient }) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const navigate = useNavigate()
  const location = useLocation()
  const [state, setState] = useState<GateState>({ status: 'loading' })

  const accept = useCallback((session: SessionState) => {
    setSessionCsrf(session.csrfToken ?? undefined)
    setState({ status: 'ready', session })
  }, [])

  const load = useCallback(async () => {
    try {
      accept(await getMe(client))
    } catch (error) {
      setSessionCsrf(undefined)
      if (error instanceof ApiError && error.code === 'setup_required') setState({ status: 'setup' })
      else if (error instanceof ApiError && error.status === 401) setState({ status: 'anonymous' })
      else setState({ status: 'error' })
    }
  }, [accept, client])

  useEffect(() => { void load() }, [load])
  useEffect(() => onUnauthenticated(() => { setSessionCsrf(undefined); setState({ status: 'anonymous' }) }), [])

  const nextPath = new URLSearchParams(location.search).get('next')
  useEffect(() => {
    const here = location.pathname
    if (state.status === 'anonymous' && here !== '/login') navigate(`/login?next=${encodeURIComponent(here + location.search)}`, { replace: true })
    if (state.status === 'setup' && here !== '/setup') navigate('/setup', { replace: true })
    if (state.status === 'ready' && state.session.user.mustChangePassword && here !== '/change-password') navigate('/change-password', { replace: true })
    if (state.status === 'ready' && !state.session.user.mustChangePassword && (here === '/login' || here === '/setup')) {
      navigate(nextPath && nextPath.startsWith('/') && !nextPath.startsWith('//') ? nextPath : '/', { replace: true })
    }
  }, [state, location.pathname, location.search, navigate, nextPath])

  const signOut = useCallback(async () => {
    try { await logout(client) } catch { /* the session may already be gone */ }
    setSessionCsrf(undefined)
    setState({ status: 'anonymous' })
  }, [client])

  if (state.status === 'loading') return <main className="auth-page" aria-busy="true"><p className="auth-card__lede">Carregando…</p></main>
  if (state.status === 'error') return <main className="auth-page"><p role="alert">Não foi possível falar com o servidor.</p><button type="button" className="button button--quiet" onClick={() => void load()}>Tentar de novo</button></main>
  if (state.status === 'setup') return <SetupPage client={client} onReady={accept} />
  if (state.status === 'anonymous') return <LoginPage client={client} onSignedIn={accept} onSetupRequired={() => setState({ status: 'setup' })} />

  const { session } = state
  if (session.user.mustChangePassword || location.pathname === '/change-password') {
    return <ChangePasswordPage
      client={client} user={session.user} required={session.user.mustChangePassword}
      onChanged={(updated) => { accept(updated); navigate('/', { replace: true }) }}
      onCancel={() => navigate(-1)} onSignOut={() => void signOut()}
    />
  }
  const value: SessionContextValue = {
    mode: 'session', user: session.user, capabilities: session.capabilities, isAdmin: session.user.role === 'admin',
    refresh: load, signOut,
  }
  return <SessionContext.Provider value={value}>{AUTH_PATHS.has(location.pathname) ? null : children}</SessionContext.Provider>
}
```

`frontend/src/app/App.tsx`:

```tsx
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { routes } from './routes'
import { SessionGate } from './SessionGate'
import { UpdateBanner } from '../components/UpdateBanner'

export function App() {
  return (
    <>
      <UpdateBanner />
      <BrowserRouter>
        <SessionGate>
          <Routes>
            {routes.map((route) => <Route key={route.path} path={route.path} element={route.element} />)}
            <Route path="*" element={routes[0].element} />
          </Routes>
        </SessionGate>
      </BrowserRouter>
    </>
  )
}
```

(Se `UpdateBanner` consultar `/v1/installation/status` antes do login, mova-o para dentro do `SessionGate`, logo antes de `<Routes>`, para não gerar 401 na tela de login.)

No fim de `frontend/src/styles/agentos.css`:

```css
/* Sign-in, setup and password screens (server mode). One column that works on a phone. */
.auth-page { min-height: 100dvh; display: grid; place-items: center; padding: 24px 16px; background: var(--orin-ink, #0b0a14); }
.auth-card { width: min(100%, 380px); display: grid; gap: 12px; }
.auth-card h1 { margin: 0; font-size: 22px; }
.auth-card__lede { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
.auth-form { display: grid; gap: 14px; margin-top: 8px; }
.auth-form label { display: grid; gap: 6px; font-size: 12px; color: var(--muted); }
.auth-form input { min-height: 44px; padding: 10px 12px; border: 1px solid var(--line-strong); border-radius: 8px; background: transparent; color: var(--text); font-size: 16px; }
.auth-form input:focus-visible { outline: 2px solid var(--signal-dim); outline-offset: 1px; }
.auth-form button { min-height: 44px; }
.auth-form__error { margin: 0; color: var(--danger, #f87171); font-size: 13px; }
```

(Confirme os nomes dos tokens em `frontend/src/styles/theme.css` e `agentos.css` — `--muted`, `--text`, `--line-strong`, `--signal-dim` já são usados por `.button--quiet`; troque `--orin-ink` e `--danger` pelos nomes reais se diferirem. `font-size: 16px` nos inputs evita o zoom automático do iOS.)

- [ ] **Step 4: Rodar e ver passar**

Run: `cd frontend && npx vitest run tests/unit/SessionGate.test.tsx && npx tsc -b --noEmit && npx eslint . --max-warnings=0`
Expected: PASS.

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app frontend/src/features/auth frontend/src/styles/agentos.css frontend/tests/unit/SessionGate.test.tsx
git commit -m "feat(web): sign-in, first-admin setup and password screens for server mode

The app asks /v1/auth/me before rendering, sends a fresh instance to setup,
a temporary password to the change screen, and any expired session back to
sign-in with the page it was on.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Frontend — menu do perfil, gestão de perfis e interface guiada por capacidades

**Files:**
- Create: `frontend/src/features/auth/ProfileMenu.tsx`
- Create: `frontend/src/features/users/UsersSection.tsx`
- Modify: `frontend/src/features/projects/WorkspaceNavigation.tsx`
- Modify: `frontend/src/features/settings/sections.ts`, `frontend/src/features/settings/SettingsNav.tsx`, `frontend/src/components/CommandPalette.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/features/settings/AboutSection.tsx`
- Modify: `frontend/src/features/providers/ProviderGrid.tsx`, `frontend/src/features/providers/ProvidersSection.tsx`
- Modify: `frontend/src/features/mcp/McpServerForm.tsx`
- Modify: `frontend/src/features/conversations/WorkspaceFileCard.tsx`
- Modify: `frontend/src/styles/agentos.css`
- Test: `frontend/tests/unit/UsersSection.test.tsx`
- Test: `frontend/tests/unit/capabilityUi.test.tsx`

**Interfaces:**
- Consumes: `useSession`, `SessionContext`, `listUsers`, `createUser`, `updateUser`, `resetUserPassword`, `authMessage`.
- Produces:
  - `SettingsItem.requires?: 'admin'`; item `users` (`/settings/users`, label `Perfis`) no grupo `Sistema`.
  - `visibleSettingsGroups(session: { isAdmin: boolean; capabilities: Capabilities }): SettingsGroup[]` e `visibleSettingsItems(session)`.
  - `ProfileMenu()`: nada no modo local; no modo sessão, botão com a inicial e o nome, menu com `Trocar senha` (`/change-password`) e `Sair`.
  - `UsersSection({ client? })`.
  - Regras de capacidade: sem `ui_updater`, About não mostra botões de instalar/excluir versão e mostra `A atualização desta instância é feita por quem administra o servidor.`; sem `omniroute`, o card do OmniRoute some e `/settings/providers/omniroute` redireciona para `/settings/providers`; sem `mcp_stdio`, o formulário manual de MCP começa em `http` e não oferece `stdio`; sem `open_in_desktop_app`, o botão `Abrir` some dos cards e da prévia de arquivo.

- [ ] **Step 1: Escrever os testes que falham**

`frontend/tests/unit/UsersSection.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { SessionContext } from '../../src/app/useSession'
import { UsersSection } from '../../src/features/users/UsersSection'

const CAPS = { shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }
const ADMIN = { user_id: 'local-user', username: 'carla', display_name: 'Carla', role: 'admin', active: true, must_change_password: false, created_at: 'x', updated_at: 'x', password_changed_at: 'x' }
const MEMBER = { ...ADMIN, user_id: 'usr_1', username: 'bruno', display_name: 'Bruno', role: 'member' }

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function renderSection(fetchImpl: typeof fetch) {
  const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
  const session = { mode: 'session' as const, user: { userId: 'local-user', username: 'carla', displayName: 'Carla', role: 'admin' as const, active: true, mustChangePassword: false }, capabilities: CAPS, isAdmin: true, refresh: async () => undefined, signOut: async () => undefined }
  return render(<MemoryRouter initialEntries={['/settings/users']}><SessionContext.Provider value={session}><UsersSection client={client} /></SessionContext.Provider></MemoryRouter>)
}

describe('UsersSection', () => {
  it('lists profiles and shows a new temporary password once', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ items: [ADMIN] }))
      .mockResolvedValueOnce(json({ user: { ...MEMBER, must_change_password: true }, temporary_password: 'Tmp-123456789012' }, 201))
      .mockResolvedValueOnce(json({ items: [ADMIN, MEMBER] }))
    renderSection(fetchImpl)
    expect(await screen.findByText('Carla')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Usuário do novo perfil'), 'bruno')
    await userEvent.click(screen.getByRole('button', { name: 'Criar perfil' }))
    expect(await screen.findByText('Tmp-123456789012')).toBeInTheDocument()
    expect(screen.getByText(/Ela não será mostrada de novo/)).toBeInTheDocument()
    expect(await screen.findByText('Bruno')).toBeInTheDocument()
  })

  it('explains why the last admin cannot be demoted', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ items: [ADMIN] }))
      .mockResolvedValueOnce(json({ error: { code: 'last_admin', category: 'CONFLICT', message_key: 'last_admin', correlation_id: 'c', retryable: false, retry_after: null } }, 409))
    renderSection(fetchImpl)
    const row = (await screen.findByText('Carla')).closest('tr') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: 'Tornar membro' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A instância precisa de pelo menos um admin ativo.')
  })
})
```

`frontend/tests/unit/capabilityUi.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { LOCAL_CAPABILITIES } from '../../src/api/session'
import { SessionContext, type SessionContextValue } from '../../src/app/useSession'
import { ProfileMenu } from '../../src/features/auth/ProfileMenu'
import { WorkspaceFileCard } from '../../src/features/conversations/WorkspaceFileCard'
import { McpServerForm } from '../../src/features/mcp/McpServerForm'
import { ProviderGrid } from '../../src/features/providers/ProviderGrid'
import { visibleSettingsItems } from '../../src/features/settings/sections'

const SERVER_CAPS = { ...LOCAL_CAPABILITIES, shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }

function server(isAdmin: boolean, signOut = vi.fn(async () => undefined)): SessionContextValue {
  return {
    mode: 'session', capabilities: SERVER_CAPS, isAdmin, refresh: async () => undefined, signOut,
    user: { userId: 'u', username: 'bruno', displayName: 'Bruno', role: isAdmin ? 'admin' : 'member', active: true, mustChangePassword: false },
  }
}

function withSession(session: SessionContextValue, node: React.ReactNode) {
  return render(<MemoryRouter><SessionContext.Provider value={session}>{node}</SessionContext.Provider></MemoryRouter>)
}

describe('capability-driven interface', () => {
  it('lists the profiles settings only for an admin of a server', () => {
    const ids = (session: { isAdmin: boolean; capabilities: typeof SERVER_CAPS }) => visibleSettingsItems(session).map((item) => item.id)
    expect(ids({ isAdmin: true, capabilities: SERVER_CAPS })).toContain('users')
    expect(ids({ isAdmin: false, capabilities: SERVER_CAPS })).not.toContain('users')
    expect(ids({ isAdmin: false, capabilities: LOCAL_CAPABILITIES })).not.toContain('users')
  })

  it('hides the open-in-desktop action and keeps download', () => {
    withSession(server(false), <WorkspaceFileCard reference={{ conversationId: 'c', path: 'a.md' }} client={new ApiClient()} />)
    expect(screen.queryByRole('button', { name: 'Abrir a.md no sistema' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Baixar a.md' })).toBeInTheDocument()
  })

  it('hides OmniRoute without the capability', () => {
    withSession(server(false), <ProviderGrid states={{} as never} />)
    expect(screen.queryByText(/OmniRoute/i)).not.toBeInTheDocument()
  })

  it('offers only http transport for MCP without stdio', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    withSession(server(false), <McpServerForm client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onCreated={() => undefined} onCancel={() => undefined} />)
    await userEvent.click(await screen.findByRole('button', { name: /manual/i }))
    expect(screen.queryByRole('option', { name: 'stdio' })).not.toBeInTheDocument()
  })

  it('shows the profile menu with sign-out only in session mode', async () => {
    const signOut = vi.fn(async () => undefined)
    withSession(server(false, signOut), <ProfileMenu />)
    await userEvent.click(screen.getByRole('button', { name: 'Perfil: Bruno' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Sair' }))
    expect(signOut).toHaveBeenCalledOnce()
  })

  it('renders no profile menu locally', () => {
    const { container } = render(<MemoryRouter><ProfileMenu /></MemoryRouter>)
    expect(container).toBeEmptyDOMElement()
  })
})
```

Antes de rodar, confira em `frontend/src/features/mcp/McpServerForm.tsx` o nome exato das props (`onCreated`, `onCancel`) e o rótulo do botão que abre o modo manual, e ajuste o teste (não o componente) para casar.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd frontend && npx vitest run tests/unit/UsersSection.test.tsx tests/unit/capabilityUi.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`frontend/src/features/settings/sections.ts`:
- Em `SettingsItem`, acrescente `requires?: 'admin'`.
- No grupo `Sistema`, antes de `about`: `{ id: 'users', label: 'Perfis', path: '/settings/users', lede: 'Quem acessa esta instância e com qual papel.', requires: 'admin' },`.
- No fim do arquivo:

```ts
import type { Capabilities } from '../../api/session'

export type SettingsAudience = { isAdmin: boolean; capabilities: Capabilities }

export function visibleSettingsGroups(audience: SettingsAudience): SettingsGroup[] {
  return SETTINGS_GROUPS
    .map((group) => ({ ...group, items: group.items.filter((item) => item.requires !== 'admin' || (audience.isAdmin && audience.capabilities.user_admin)) }))
    .filter((group) => group.items.length > 0)
}

export function visibleSettingsItems(audience: SettingsAudience): SettingsItem[] {
  return visibleSettingsGroups(audience).flatMap((group) => group.items)
}
```

(Coloque o `import type` no topo do arquivo, junto aos demais.)
- `SettingsNav.tsx`: `const session = useSession()` e troque `SETTINGS_GROUPS.map` por `visibleSettingsGroups(session).map`.
- `CommandPalette.tsx:40`: troque `settingsItems()` por `visibleSettingsItems(session)`, com `const session = useSession()` no componente.

`frontend/src/features/auth/ProfileMenu.tsx`:

```tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSession } from '../../app/useSession'

export function ProfileMenu() {
  const session = useSession()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  if (session.mode !== 'session' || !session.user) return null
  const name = session.user.displayName
  return (
    <div className="profile-menu">
      <button type="button" className="profile-menu__button" aria-haspopup="menu" aria-expanded={open} aria-label={`Perfil: ${name}`} onClick={() => setOpen((value) => !value)}>
        <span className="profile-menu__avatar" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>
        <span className="profile-menu__name">{name}</span>
      </button>
      {open && (
        <div className="profile-menu__list" role="menu">
          <button type="button" role="menuitem" onClick={() => { setOpen(false); navigate('/change-password') }}>Trocar senha</button>
          <button type="button" role="menuitem" onClick={() => { setOpen(false); void session.signOut() }}>Sair</button>
        </div>
      )}
    </div>
  )
}
```

`WorkspaceNavigation.tsx`: importe `ProfileMenu` e renderize `<ProfileMenu />` como último elemento do fragmento retornado (depois do `<p className="workspace-navigation__error">`).

`frontend/src/features/users/UsersSection.tsx`:

```tsx
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { createUser, listUsers, resetUserPassword, updateUser, type SessionUser } from '../../api/auth'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { useSession } from '../../app/useSession'
import { authMessage } from '../auth/authErrors'
import { SettingsSection } from '../settings/SettingsSection'

type Revealed = { username: string; password: string }

export function UsersSection({ client: providedClient }: { client?: ApiClient }) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const session = useSession()
  const [users, setUsers] = useState<SessionUser[]>([])
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState<'admin' | 'member'>('member')
  const [revealed, setRevealed] = useState<Revealed | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try { setUsers(await listUsers(client)) } catch (failure) { setError(authMessage(failure)) }
  }, [client])

  useEffect(() => { void reload() }, [reload])

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(null)
    try { await action(); await reload() } catch (failure) { setError(authMessage(failure)) } finally { setBusy(false) }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void run(async () => {
      const created = await createUser(client, { username, displayName, role })
      setRevealed({ username: created.user.username, password: created.temporaryPassword })
      setUsername(''); setDisplayName(''); setRole('member')
    })
  }

  return (
    <SettingsSection eyebrow="SISTEMA / PERFIS">
      {revealed && (
        <div className="users-reveal" role="status">
          <p>Senha provisória de <strong>{revealed.username}</strong>: <code>{revealed.password}</code></p>
          <p>Ela não será mostrada de novo. Envie para a pessoa; ela troca no primeiro login.</p>
          <button type="button" className="button button--quiet" onClick={() => void navigator.clipboard?.writeText(revealed.password)}>Copiar</button>
          <button type="button" className="button button--quiet" onClick={() => setRevealed(null)}>Fechar</button>
        </div>
      )}
      {error && <p className="users-error" role="alert">{error}</p>}
      <table className="users-table">
        <thead><tr><th scope="col">Nome</th><th scope="col">Usuário</th><th scope="col">Papel</th><th scope="col">Estado</th><th scope="col"><span className="visually-hidden">Ações</span></th></tr></thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.userId}>
              <td>{user.displayName}</td>
              <td><code>{user.username}</code></td>
              <td>{user.role === 'admin' ? 'Admin' : 'Membro'}</td>
              <td>{user.active ? (user.mustChangePassword ? 'Aguardando troca de senha' : 'Ativo') : 'Desativado'}</td>
              <td className="users-table__actions">
                <button type="button" disabled={busy} onClick={() => void run(async () => { await updateUser(client, user.userId, { role: user.role === 'admin' ? 'member' : 'admin' }) })}>{user.role === 'admin' ? 'Tornar membro' : 'Tornar admin'}</button>
                {user.userId !== session.user?.userId && (
                  <button type="button" disabled={busy} onClick={() => void run(async () => { await updateUser(client, user.userId, { active: !user.active }) })}>{user.active ? 'Desativar' : 'Reativar'}</button>
                )}
                <button type="button" disabled={busy} onClick={() => void run(async () => {
                  const reset = await resetUserPassword(client, user.userId)
                  setRevealed({ username: reset.user.username, password: reset.temporaryPassword })
                })}>Resetar senha</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="users-create" onSubmit={submit}>
        <h2>Novo perfil</h2>
        <label>Usuário do novo perfil<input value={username} autoCapitalize="none" onChange={(event) => setUsername(event.target.value)} required /></label>
        <label>Nome de exibição<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
        <label>Papel<select value={role} onChange={(event) => setRole(event.target.value as 'admin' | 'member')}><option value="member">Membro</option><option value="admin">Admin</option></select></label>
        <button type="submit" className="button button--primary" disabled={busy}>Criar perfil</button>
      </form>
    </SettingsSection>
  )
}
```

(Se a classe `visually-hidden` não existir em `agentos.css`, crie-a no bloco de estilos abaixo.)

`frontend/src/app/routes.tsx`:
- Import `UsersSection` e `useSession`.
- Rota nova: `{ path: '/settings/users', element: <AdminSettingsRoute><UsersSection /></AdminSettingsRoute> },`.
- Componente:

```tsx
function AdminSettingsRoute({ children }: { children: ReactNode }) {
  const session = useSession()
  if (!session.isAdmin || !session.capabilities.user_admin) return <Navigate to="/settings/general" replace />
  return <SettingsRoute>{children}</SettingsRoute>
}
```

- Em `ProviderSettingsRoute`, logo depois de calcular `provider`: `const { capabilities } = useSession()` e `if (provider === 'omniroute' && !capabilities.omniroute) return <Navigate to="/settings/providers" replace />` (chame o hook antes de qualquer `return`).

`frontend/src/features/providers/ProviderGrid.tsx`: `const { capabilities } = useSession()` e itere `PROVIDER_NAMES.filter((provider) => provider !== 'omniroute' || capabilities.omniroute)`. Em `ProvidersSection.tsx`, filtre do mesmo jeito a lista usada no `Promise.all`, para não consultar o OmniRoute.

`frontend/src/features/settings/AboutSection.tsx`: `const { capabilities } = useSession()`; renderize o bloco do botão `Instalar v…` e os botões `Excluir versão antiga` só quando `capabilities.ui_updater`; quando falso, renderize no lugar do bloco de atualização `<p className="installation-status__managed">A atualização desta instância é feita por quem administra o servidor.</p>`.

`frontend/src/features/mcp/McpServerForm.tsx`: `const { capabilities } = useSession()`; inicialize `useState<McpTransport>(capabilities.mcp_stdio ? 'stdio' : 'http')`; renderize `<option value="stdio">stdio</option>` só quando `capabilities.mcp_stdio`. Se o catálogo marcar entradas `stdio`, esconda essas entradas também quando `!capabilities.mcp_stdio` (filtre `entries` pelo campo de transporte da entrada; confira o nome do campo em `frontend/src/api/mcp.ts`).

`frontend/src/features/conversations/WorkspaceFileCard.tsx`: em `WorkspaceFileCard` e em `WorkspaceFilePreview`, `const { capabilities } = useSession()` e renderize o botão `Abrir` só quando `capabilities.open_in_desktop_app`.

No fim de `agentos.css`:

```css
.profile-menu { position: relative; margin-top: auto; padding: 12px 8px; border-top: 1px solid var(--line-strong); }
.profile-menu__button { display: flex; align-items: center; gap: 10px; width: 100%; padding: 6px 8px; border: 0; border-radius: 8px; background: transparent; color: var(--text); cursor: pointer; }
.profile-menu__button:hover, .profile-menu__button:focus-visible { background: rgba(255, 255, 255, .04); }
.profile-menu__avatar { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--signal-dim); font: 12px var(--mono); }
.profile-menu__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.profile-menu__list { position: absolute; left: 8px; right: 8px; bottom: calc(100% - 4px); display: grid; padding: 4px; border: 1px solid var(--line-strong); border-radius: 8px; background: var(--orin-surface, #13111f); }
.profile-menu__list button { padding: 8px 10px; border: 0; border-radius: 6px; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.profile-menu__list button:hover, .profile-menu__list button:focus-visible { background: rgba(255, 255, 255, .06); }
.users-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.users-table th, .users-table td { padding: 10px 8px; border-bottom: 1px solid var(--line-strong); text-align: left; }
.users-table__actions { display: flex; flex-wrap: wrap; gap: 6px; }
.users-create { display: grid; gap: 10px; max-width: 420px; margin-top: 28px; }
.users-reveal { display: grid; gap: 8px; margin-bottom: 20px; padding: 14px; border: 1px solid var(--signal-dim); border-radius: 8px; }
.users-error { color: var(--danger, #f87171); }
.installation-status__managed { color: var(--muted); font-size: 13px; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (max-width: 720px) { .users-table thead { display: none; } .users-table tr { display: grid; padding: 8px 0; } .users-table td { border: 0; padding: 2px 0; } }
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd frontend && npx vitest run tests/unit/UsersSection.test.tsx tests/unit/capabilityUi.test.tsx && npx tsc -b --noEmit && npx eslint . --max-warnings=0`
Expected: PASS.

Run: `cd frontend && npx vitest run`
Expected: PASS (sem provider de sessão, todo componente se comporta como no modo local).

- [ ] **Step 5: Commit**

```bash
git add frontend/src frontend/tests/unit/UsersSection.test.tsx frontend/tests/unit/capabilityUi.test.tsx
git commit -m "feat(web): profile menu, profile administration and capability-driven ui

Admins manage profiles from settings, everyone gets a sign-out menu, and
desktop-only actions disappear when the instance does not offer them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Frontend — seletor da área de arquivos e importação de .zip

**Files:**
- Create: `frontend/src/api/files.ts`
- Create: `frontend/src/features/files/ProfileFolderBrowser.tsx`
- Modify: `frontend/src/api/workspace.ts` (`WorkspaceState.kind`, `parseWorkspaceState`)
- Modify: `frontend/src/features/conversations/WorkspaceFolderButton.tsx`
- Modify: `frontend/src/app/Home.tsx:231` e `frontend/src/features/conversations/ChatPage.tsx:831` (passar o cliente para o botão)
- Modify: `frontend/src/styles/agentos.css`
- Test: `frontend/tests/unit/ProfileFolderBrowser.test.tsx`

**Interfaces:**
- Consumes: `useSession`, `ApiClient.request/upload`.
- Produces:
  - `files.ts`: `type FileEntry = { name: string; kind: 'directory' | 'file'; bytes: number; modifiedAt: string }`, `type FileListing = { path: string; entries: FileEntry[]; truncated: boolean }`, `listFiles(client, path, signal?)`, `createFolder(client, parent, name) => Promise<string>`, `importArchive(client, file: File, folderName: string, parent?: string) => Promise<string>`, `fileDownloadUrl(path: string): string`.
  - `WorkspaceState.kind` passa a ser `'managed' | 'local' | 'unavailable'`.
  - `ProfileFolderBrowser({ client, onChoose })`: navega pela área do perfil, cria pasta, importa .zip e devolve o caminho relativo escolhido.
  - `WorkspaceFolderButton` ganha a prop opcional `client?: ApiClient`; com `capabilities.profile_files`, o painel mostra o `ProfileFolderBrowser` no lugar de "Selecionar diretório…" e do campo de caminho, e o estado `unavailable` aparece como `Pasta indisponível no servidor`.

- [ ] **Step 1: Escrever o teste que falha**

`frontend/tests/unit/ProfileFolderBrowser.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { LOCAL_CAPABILITIES } from '../../src/api/session'
import { parseWorkspaceState } from '../../src/api/workspace'
import { SessionContext } from '../../src/app/useSession'
import { WorkspaceFolderButton } from '../../src/features/conversations/WorkspaceFolderButton'
import { ProfileFolderBrowser } from '../../src/features/files/ProfileFolderBrowser'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const root = { path: '.', entries: [{ name: 'proj', kind: 'directory', bytes: 0, modified_at: 'x' }, { name: 'a.txt', kind: 'file', bytes: 3, modified_at: 'x' }], truncated: false }
const proj = { path: 'proj', entries: [], truncated: false }

describe('ProfileFolderBrowser', () => {
  it('navigates into a folder and chooses it', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(json(root)).mockResolvedValueOnce(json(proj))
    const onChoose = vi.fn()
    render(<ProfileFolderBrowser client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onChoose={onChoose} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Abrir pasta proj' }))
    await screen.findByText('proj', { selector: '.profile-files__path' })
    await userEvent.click(screen.getByRole('button', { name: 'Usar esta pasta' }))
    expect(onChoose).toHaveBeenCalledWith('proj')
    expect(String(fetchImpl.mock.calls[1][0])).toBe('/v1/files?path=proj')
  })

  it('creates a folder and imports a zip into the current folder', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json(root))
      .mockResolvedValueOnce(json({ path: 'novo' }, 201))
      .mockResolvedValueOnce(json(root))
      .mockResolvedValueOnce(json({ path: 'app' }, 201))
      .mockResolvedValueOnce(json(root))
    render(<ProfileFolderBrowser client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onChoose={() => undefined} />)
    await userEvent.type(await screen.findByLabelText('Nome da nova pasta'), 'novo')
    await userEvent.click(screen.getByRole('button', { name: 'Criar pasta' }))
    const zip = new File(['PK'], 'app.zip', { type: 'application/zip' })
    await userEvent.upload(screen.getByLabelText('Importar .zip'), zip)
    expect(fetchImpl.mock.calls[3][0]).toBe('/v1/files/import')
    const body = fetchImpl.mock.calls[3][1]?.body as FormData
    expect((body.get('file') as File).name).toBe('app.zip')
    expect(body.get('folder_name')).toBe('app')
  })

  it('parses an unavailable workspace', () => {
    expect(parseWorkspaceState({ kind: 'unavailable', path: null, folder_name: 'host', scope: 'chat' }).kind).toBe('unavailable')
  })

  it('replaces the native picker with the profile browser on a server', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json(root)))
    const session = { mode: 'session' as const, user: null, isAdmin: false, refresh: async () => undefined, signOut: async () => undefined, capabilities: { ...LOCAL_CAPABILITIES, host_folders: false, profile_files: true } }
    render(
      <MemoryRouter><SessionContext.Provider value={session}>
        <WorkspaceFolderButton
          client={new ApiClient({ fetchImpl, maxAttempts: 1 })}
          state={{ kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null }}
          onInspect={vi.fn()} onAttach={vi.fn()} onDetach={vi.fn()} onChange={vi.fn()}
        />
      </SessionContext.Provider></MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Adicionar pasta ao workspace' }))
    expect(screen.queryByRole('button', { name: 'Selecionar diretório…' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Abrir pasta proj' })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd frontend && npx vitest run tests/unit/ProfileFolderBrowser.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`frontend/src/api/files.ts`:

```ts
import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type FileEntry = { name: string; kind: 'directory' | 'file'; bytes: number; modifiedAt: string }
export type FileListing = { path: string; entries: FileEntry[]; truncated: boolean }

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function parseListing(value: unknown): FileListing {
  const data = record(value)
  if (typeof data.path !== 'string' || !Array.isArray(data.entries)) throw invalidResponseError()
  return {
    path: data.path,
    truncated: data.truncated === true,
    entries: data.entries.map((item) => {
      const entry = record(item)
      if (typeof entry.name !== 'string' || (entry.kind !== 'directory' && entry.kind !== 'file')) throw invalidResponseError()
      return { name: entry.name, kind: entry.kind, bytes: typeof entry.bytes === 'number' ? entry.bytes : 0, modifiedAt: String(entry.modified_at ?? '') }
    }),
  }
}

function parsePath(value: unknown): string {
  const data = record(value)
  if (typeof data.path !== 'string') throw invalidResponseError()
  return data.path
}

export function listFiles(client: ApiClient, path: string, signal?: AbortSignal): Promise<FileListing> {
  return client.request({ path: '/v1/files', query: { path }, signal, parse: parseListing })
}

export function createFolder(client: ApiClient, parent: string, name: string): Promise<string> {
  return client.request({ path: '/v1/files/folders', method: 'POST', body: { parent, name }, expectedStatus: 201, parse: parsePath })
}

export function importArchive(client: ApiClient, file: File, folderName: string, parent = ''): Promise<string> {
  const body = new FormData()
  body.set('file', file)
  body.set('folder_name', folderName)
  body.set('parent', parent)
  return client.upload({ path: '/v1/files/import', body, expectedStatus: 201, parse: parsePath })
}

export function fileDownloadUrl(path: string): string {
  return `/v1/files/download?${new URLSearchParams({ path }).toString()}`
}
```

`frontend/src/features/files/ProfileFolderBrowser.tsx`:

```tsx
import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { createFolder, importArchive, listFiles, type FileListing } from '../../api/files'

function parentOf(path: string): string {
  if (path === '.' || !path.includes('/')) return ''
  return path.slice(0, path.lastIndexOf('/'))
}

function childOf(path: string, name: string): string {
  return path === '.' || path === '' ? name : `${path}/${name}`
}

export function ProfileFolderBrowser({ client, onChoose }: { client: ApiClient; onChoose: (path: string) => void }) {
  const [listing, setListing] = useState<FileListing | null>(null)
  const [current, setCurrent] = useState('')
  const [newFolder, setNewFolder] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (path: string) => {
    setError(null)
    try {
      const result = await listFiles(client, path)
      setListing(result); setCurrent(result.path === '.' ? '' : result.path)
    } catch { setError('Não foi possível abrir esta pasta.') }
  }, [client])

  useEffect(() => { void load('') }, [load])

  async function submitFolder(event: FormEvent) {
    event.preventDefault()
    if (!newFolder.trim()) return
    setBusy(true)
    try { await createFolder(client, current, newFolder.trim()); setNewFolder(''); await load(current) }
    catch { setError('Não foi possível criar a pasta.') }
    finally { setBusy(false) }
  }

  async function importZip(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(true); setError(null)
    try { await importArchive(client, file, file.name.replace(/\.zip$/i, ''), current); await load(current) }
    catch (failure) { setError(failure instanceof ApiError && failure.code === 'archive_rejected' ? 'O arquivo .zip foi recusado (caminho inválido, muito grande ou a pasta já existe).' : 'Não foi possível importar o arquivo.') }
    finally { setBusy(false) }
  }

  return (
    <div className="profile-files">
      <div className="profile-files__bar">
        <button type="button" disabled={!current || busy} onClick={() => void load(parentOf(current))} aria-label="Voltar para a pasta acima">↑</button>
        <span className="profile-files__path">{current || 'Meus arquivos'}</span>
      </div>
      {error && <p className="profile-files__error" role="alert">{error}</p>}
      <ul className="profile-files__list">
        {listing?.entries.filter((entry) => entry.kind === 'directory').map((entry) => (
          <li key={entry.name}><button type="button" onClick={() => void load(childOf(current, entry.name))} aria-label={`Abrir pasta ${entry.name}`}>▸ {entry.name}</button></li>
        ))}
        {listing && listing.entries.every((entry) => entry.kind !== 'directory') && <li className="profile-files__empty">Nenhuma subpasta.</li>}
      </ul>
      <button type="button" className="button button--primary" disabled={busy} onClick={() => onChoose(current || '.')}>Usar esta pasta</button>
      <form className="profile-files__create" onSubmit={(event) => void submitFolder(event)}>
        <label>Nome da nova pasta<input value={newFolder} onChange={(event) => setNewFolder(event.target.value)} maxLength={255} /></label>
        <button type="submit" disabled={busy}>Criar pasta</button>
      </form>
      <label className="profile-files__import">Importar .zip<input type="file" accept=".zip,application/zip" disabled={busy} onChange={(event) => void importZip(event)} /></label>
    </div>
  )
}
```

`frontend/src/api/workspace.ts`: troque `kind: 'managed' | 'local'` por `kind: 'managed' | 'local' | 'unavailable'` e, em `parseWorkspaceState`, `const kind = data.kind === 'local' ? 'local' : data.kind === 'unavailable' ? 'unavailable' : 'managed'`.

`frontend/src/features/conversations/WorkspaceFolderButton.tsx`:
- Props: acrescente `client?: ApiClient` (import `type ApiClient` de `../../api/client`).
- `const { capabilities } = useSession()`.
- Rótulo: quando `state.kind === 'unavailable'`, o botão mostra `Pasta indisponível` e o painel mostra `<p className="workspace-folder__risk">A pasta {state.folderName} era do computador onde o Orin rodava antes. Neste servidor, escolha uma pasta da sua área de arquivos.</p>`.
- No painel sem candidato, quando `capabilities.profile_files && client`, troque o botão `Selecionar diretório…` e o `<label className="workspace-folder__field">…</label>` por:

```tsx
                <ProfileFolderBrowser client={client} onChoose={(path) => void inspect(path)} />
```

(O `inspect(path)` existente chama `onInspect`, que no modo servidor recebe o caminho relativo e devolve a inspeção relativa; o fluxo de confirmação continua o mesmo.)
- O texto `Workspace local` vira `Pasta do projeto` quando `capabilities.profile_files`.

Em `Home.tsx:231` e `ChatPage.tsx:831`, passe `client={apiClient}` / `client={client}` para `WorkspaceFolderButton`.

No fim de `agentos.css`:

```css
.profile-files { display: grid; gap: 10px; }
.profile-files__bar { display: flex; align-items: center; gap: 8px; }
.profile-files__path { font: 12px var(--mono); color: var(--muted); overflow-wrap: anywhere; }
.profile-files__list { display: grid; gap: 2px; max-height: 220px; margin: 0; padding: 0; overflow: auto; list-style: none; }
.profile-files__list button { width: 100%; padding: 8px; border: 0; border-radius: 6px; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.profile-files__list button:hover, .profile-files__list button:focus-visible { background: rgba(255, 255, 255, .05); }
.profile-files__empty { color: var(--muted); font-size: 12px; padding: 8px; }
.profile-files__create { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; }
.profile-files__import { font-size: 12px; color: var(--muted); }
.profile-files__error { color: var(--danger, #f87171); font-size: 12px; }
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd frontend && npx vitest run tests/unit/ProfileFolderBrowser.test.tsx && npx tsc -b --noEmit && npx eslint . --max-warnings=0 && npx vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src frontend/tests/unit/ProfileFolderBrowser.test.tsx
git commit -m "feat(web): choose a project folder from the profile's file area

On a server the native picker and the typed host path give way to a
browser of the profile's own files, with folder creation and .zip import,
and a host folder from a previous install shows as unavailable.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Fluxo ponta a ponta no navegador, documentação e verificação final

**Files:**
- Create: `frontend/tests/e2e/server-session.spec.ts`
- Create: `docs/SERVER.md`
- Test: suítes completas

**Interfaces:**
- Consumes: tudo acima.
- Produces: o fluxo setup → login → criar perfil → troca de senha obrigatória → membro sem acesso a dados do admin, exercitado num navegador real com API simulada; documentação de desenvolvimento do modo servidor.

- [ ] **Step 1: Escrever o teste e2e**

`frontend/tests/e2e/server-session.spec.ts`:

```ts
import { expect, test, type Page, type Route } from '@playwright/test'

// Test-only fake of the server-mode auth contract documented in
// docs/superpowers/specs/2026-10-02-server-mode-profiles-design.md §4.3.
const CAPS = { shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }

function user(id: string, username: string, role: 'admin' | 'member', mustChange = false) {
  return { user_id: id, username, display_name: username, role, active: true, must_change_password: mustChange, created_at: 'x', updated_at: 'x', password_changed_at: 'x' }
}

async function fulfill(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

function error(code: string, status: number) {
  return { error: { code, category: 'X', message_key: code, correlation_id: 'c', retryable: false, retry_after: null } }
}

async function sessionHtml(page: Page) {
  await page.route(/^http:\/\/127\.0\.0\.1:4173\/(login|setup|change-password|settings.*)?(\?.*)?$/, async (route) => {
    const response = await route.fetch()
    const body = (await response.text()).replace('name="agentos-auth-mode" content=""', 'name="agentos-auth-mode" content="session"')
    await route.fulfill({ response, body })
  })
}

test('a fresh instance goes from setup to a member who changes the temporary password', async ({ page }) => {
  let current: ReturnType<typeof user> | null = null
  let accounts = 0
  const seenCsrf: string[] = []

  await sessionHtml(page)
  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() !== 'GET') seenCsrf.push(request.headers()['x-csrf-token'] ?? '')
    if (path === '/v1/auth/me') {
      if (accounts === 0) return fulfill(route, error('setup_required', 409), 409)
      if (!current) return fulfill(route, error('authentication_required', 401), 401)
      return fulfill(route, { user: current, csrf_token: `csrf-${current.username}`, capabilities: CAPS })
    }
    if (path === '/v1/auth/setup') {
      accounts = 1; current = user('local-user', 'carla', 'admin')
      return fulfill(route, { user: current, csrf_token: 'csrf-carla', capabilities: CAPS }, 201)
    }
    if (path === '/v1/admin/users' && request.method() === 'POST') {
      accounts = 2
      return fulfill(route, { user: user('usr_1', 'bruno', 'member', true), temporary_password: 'Tmp-123456789012' }, 201)
    }
    if (path === '/v1/admin/users') return fulfill(route, { items: accounts > 1 ? [user('local-user', 'carla', 'admin'), user('usr_1', 'bruno', 'member', true)] : [user('local-user', 'carla', 'admin')] })
    if (path === '/v1/auth/logout') { current = null; return route.fulfill({ status: 204 }) }
    if (path === '/v1/auth/login') {
      current = user('usr_1', 'bruno', 'member', true)
      return fulfill(route, { user: current, csrf_token: 'csrf-bruno', capabilities: CAPS })
    }
    if (path === '/v1/auth/password') {
      current = user('usr_1', 'bruno', 'member')
      return fulfill(route, { user: current, csrf_token: 'csrf-bruno', capabilities: CAPS })
    }
    if (path === '/v1/conversations' || path === '/v1/projects/sidebar') return fulfill(route, { items: [] })
    return fulfill(route, {})
  })

  await page.goto('/')
  await expect(page).toHaveURL(/\/setup$/)
  await page.getByLabel('Token de setup').fill('the-token')
  await page.getByLabel('Usuário').fill('carla')
  await page.getByLabel('Senha', { exact: true }).fill('a long password')
  await page.getByLabel('Confirmar senha').fill('a long password')
  await page.getByRole('button', { name: 'Criar admin' }).click()

  await page.goto('/settings/users')
  await page.getByLabel('Usuário do novo perfil').fill('bruno')
  await page.getByRole('button', { name: 'Criar perfil' }).click()
  await expect(page.getByText('Tmp-123456789012')).toBeVisible()
  expect(seenCsrf.at(-1)).toBe('csrf-carla')

  await page.goto('/')
  await page.getByRole('button', { name: 'Perfil: carla' }).click()
  await page.getByRole('menuitem', { name: 'Sair' }).click()
  await page.getByLabel('Usuário').fill('bruno')
  await page.getByLabel('Senha').fill('Tmp-123456789012')
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL(/\/change-password$/)
  await page.getByLabel('Senha atual').fill('Tmp-123456789012')
  await page.getByLabel('Nova senha').fill('bruno own password')
  await page.getByLabel('Confirmar nova senha').fill('bruno own password')
  await page.getByRole('button', { name: 'Trocar senha' }).click()
  await expect(page).toHaveURL(/\/$/)

  await page.goto('/settings/users')
  await expect(page).toHaveURL(/\/settings\/general$/)
})
```

- [ ] **Step 2: Rodar o e2e**

Run: `cd frontend && npx playwright test tests/e2e/server-session.spec.ts`
Expected: PASS. Se um seletor não casar com o markup real, ajuste o seletor do teste; se o fluxo em si falhar, corrija o componente.

- [ ] **Step 3: Escrever `docs/SERVER.md`**

```markdown
# Modo servidor (em desenvolvimento)

> Ainda não publicado em release. O modo servidor só sai junto com o sandbox por perfil (etapa 2). Até lá, shell, terminal, MCP stdio, hooks de plugin e OmniRoute ficam desligados nele.

O modo servidor roda o Orin numa máquina acessada pelo navegador, com perfis protegidos por usuário e senha. Cada perfil tem as próprias conversas, memórias, projetos, chaves de provedor e área de arquivos.

## Subir para testar

1. Defina a URL pública e, se houver proxy reverso, os IPs dele:

   ```bash
   export ORIN_PUBLIC_URL=https://orin.example.com
   export ORIN_TRUSTED_PROXIES=127.0.0.1
   export ORIN_BACKEND_HOST=127.0.0.1
   ```

   Para testar localmente sem HTTPS, use `ORIN_PUBLIC_URL=http://127.0.0.1:49200`.

2. Rode `orin serve`. Na primeira subida, o log mostra um bloco `ORIN SETUP TOKEN`.
3. Abra `ORIN_PUBLIC_URL` no navegador, cole o token em `/setup` e crie o primeiro admin.
4. Em **Settings → Perfis**, crie os perfis. Cada um recebe uma senha provisória, trocada no primeiro login.

O primeiro admin herda os dados de uma instalação local que já existia no mesmo diretório de dados.

## Recuperar acesso

- `orin user reset-password <usuário>` gera uma senha provisória e encerra as sessões do perfil.
- `orin user create <usuário> --admin` cria um admin sem passar pela tela de setup.

## Onde ficam os dados

- Banco: `<data>/orin.db`
- Workspaces gerenciados: `<data>/users/<id>/workspaces/`
- Área de arquivos: `<data>/users/<id>/files/`

## Segurança

- Sessão em cookie `HttpOnly`, `Secure` e `SameSite=Lax`, válida por 30 dias sem uso.
- Toda alteração exige o token CSRF da sessão e a `Origin` igual a `ORIN_PUBLIC_URL`.
- O login bloqueia temporariamente depois de várias senhas erradas, por usuário e por IP.
- Recursos de outro perfil respondem 404.
```

- [ ] **Step 4: Verificação completa**

Run: `python -m pytest tests/unit tests/integration -q`
Expected: PASS, sem testes pulados novos além dos opcionais de Postgres já existentes.

Run: `cd frontend && npx tsc -b --noEmit && npx eslint . --max-warnings=0 && npx vitest run && npm run build && npx playwright test`
Expected: PASS.

Smoke manual (registre a saída no corpo do commit ou na descrição do PR):
1. `ORIN_PUBLIC_URL=http://127.0.0.1:49200 ORIN_HOME=$(mktemp -d) orin serve` (no Windows, `$env:ORIN_PUBLIC_URL="http://127.0.0.1:49200"; $env:ORIN_HOME="$env:TEMP\orin-serve"; orin serve`).
2. Copiar o token do log, criar o admin em `http://127.0.0.1:49200/setup`, criar um membro, entrar como membro em uma janela anônima e confirmar que a sidebar dele não mostra as conversas do admin.
3. `orin` (modo local) num `ORIN_HOME` separado continua abrindo direto no chat, sem tela de login.

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/e2e/server-session.spec.ts docs/SERVER.md
git commit -m "test(web): exercise the server sign-in flow end to end and document it

Covers setup, profile creation, sign-out, a member's forced password change
and the admin-only settings guard in a real browser, and documents how to
run server mode while it is unreleased.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

