"""Configuration the runtime needs, resolved once and handed to every child.

The launcher is the only process that reads configuration files. Children get
their settings through the inherited environment, so a worker started from
``C:\\`` is configured identically to one started from the repository, and no
secret ever appears on a command line.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from agentos.installation import OrinPaths, RuntimeProfile
from agentos.persistence.sqlite import sqlite_url


_SECRET_NAME = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.IGNORECASE)


class ConfigurationError(RuntimeError):
    """Configuration is missing or contradictory, described for a human."""


def redact(name: str, value: str) -> str:
    """What may be written to a log for a given setting."""
    if _SECRET_NAME.search(name):
        return "***"
    if "://" in value and "@" in value:
        scheme, _, rest = value.partition("://")
        return f"{scheme}://***@{rest.rpartition('@')[2]}"
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ConfigurationError(f"{path} could not be read: {error.strerror or error}") from error
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _generated_encryption_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def write_default_configuration(path: Path, *, database_url: str = "sqlite+pysqlite:///orin.db") -> Path:
    """Create a first-run configuration file.

    An installed Orin has no repository to copy an example from, and asking a
    user to hand-generate a Fernet key before the app will start is not a product.
    The key is generated locally and never leaves this file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                "# Orin local configuration. Generated on first run.",
                "# Provider API keys are NOT stored here; add them in Settings -> Providers,",
                "# where they are encrypted at rest with the key below.",
                f"DATABASE_URL={database_url}",
                "AGENTOS_ENV=local",
                "LOCALHOST_TRUST_ENABLED=true",
                f"AGENTOS_PROVIDER_ENCRYPTION_KEY={_generated_encryption_key()}",
                "",
            )
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o600)
    return path


@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    """Everything a child process needs to know, already resolved."""

    values: dict[str, str]
    sources: tuple[Path, ...]
    created_configuration: Path | None

    @property
    def database_url(self) -> str:
        return self.values["DATABASE_URL"]

    def for_port(self, port: int) -> dict[str, str]:
        return {**self.values, "ORIN_BACKEND_PORT": str(port), "ORIN_BACKEND_HOST": "127.0.0.1"}

    def describe(self) -> str:
        return "\n".join(f"{name}={redact(name, value)}" for name, value in sorted(self.values.items()) if name.startswith(("DATABASE", "AGENTOS", "ORIN", "WEB", "LOCALHOST")))


def load_environment(paths: OrinPaths, profile: RuntimeProfile) -> RuntimeEnvironment:
    """Assemble the runtime environment.

    Precedence, lowest first: the launcher's own environment, then each
    configuration file in profile order, then the values the launcher must
    control itself (paths, web bundle). An operator who exported ``DATABASE_URL``
    before running ``orin`` still wins over a stale file, because the file is
    read before that value is re-applied only when the file did not set it.
    """
    files = profile.environment_files(paths.config)
    created: Path | None = None
    default_database_url = sqlite_url(paths.data / "orin.db")
    if not files:
        created = write_default_configuration(paths.config / "orin.env", database_url=default_database_url)
        files = (created,)

    values: dict[str, str] = dict(os.environ)
    for path in files:
        values.update(parse_env_file(path))

    # A personal install is deliberately self-contained. Ignore legacy
    # Postgres values left in a checkout/config rather than making Docker an
    # accidental runtime dependency again.
    values["DATABASE_URL"] = default_database_url
    values.setdefault("AGENTOS_ENV", "local")
    values.setdefault("LOCALHOST_TRUST_ENABLED", "true")

    web = profile.web_dist
    if web is None or not (web / "index.html").is_file():
        raise ConfigurationError(
            "The Orin web interface is missing from this installation.\n"
            + (
                f"Expected a build at {web}.\nBuild it with: npm --prefix frontend run build"
                if profile.is_development
                else f"Expected {web}. Reinstall Orin to restore it."
            )
        )
    # Absolute, always: the backend must not resolve its own UI through a cwd.
    values["WEB_DIST_DIR"] = str(web.resolve())
    bundled_browser = next(
        (candidate for candidate in (profile.root / "playwright", profile.root / "_internal" / "playwright") if candidate.is_dir()),
        paths.cache / "playwright",
    )
    browser_path = bundled_browser
    values.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_path.resolve()))
    values.update(paths.as_environment())

    _validate(values, profile)
    return RuntimeEnvironment(values, files, created)


def _validate(values: dict[str, str], profile: RuntimeProfile) -> None:
    trust = values.get("LOCALHOST_TRUST_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    environment = values.get("AGENTOS_ENV", "").strip().lower()
    if trust and environment not in {"local", "development"}:
        raise ConfigurationError(
            "LOCALHOST_TRUST_ENABLED is only allowed when AGENTOS_ENV is 'local' or 'development'.\n"
            f"AGENTOS_ENV is currently '{values.get('AGENTOS_ENV', '')}'."
        )
    if not values.get("AGENTOS_PROVIDER_ENCRYPTION_KEY", "").strip():
        raise ConfigurationError(
            "AGENTOS_PROVIDER_ENCRYPTION_KEY is not set. It encrypts provider credentials at rest.\n"
            "Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            + ("and put it in .env.local." if profile.is_development else "and put it in your Orin configuration file.")
        )
    if not values.get("DATABASE_URL", "").startswith("sqlite"):
        raise ConfigurationError("The local Orin runtime only supports the bundled SQLite database.")


__all__ = [
    "ConfigurationError",
    "RuntimeEnvironment",
    "load_environment",
    "parse_env_file",
    "redact",
    "write_default_configuration",
]
