from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher.environment import load_environment
from agentos.launcher.services import apply_migrations, ensure_datastores
from agentos.persistence.sqlite import create_local_engine


class _Log:
    def debug(self, *_args) -> None: ...
    def info(self, *_args) -> None: ...


def test_local_runtime_creates_and_migrates_its_own_sqlite_database(tmp_path: Path) -> None:
    paths = OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()
    root = tmp_path / "install"
    (root / "web").mkdir(parents=True)
    (root / "web" / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    profile = RuntimeProfile("installed", root, "1", None)

    environment = load_environment(paths, profile)
    assert environment.database_url.startswith("sqlite+pysqlite:///")
    assert "REDIS_URL" not in environment.values
    assert ensure_datastores(environment, profile, log=_Log()).ready

    apply_migrations(environment, profile, log=_Log())
    engine = create_local_engine(environment.database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        engine.dispose()


def test_frozen_layout_finds_assets_under_pyinstaller_internal_directory(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    internal = root / "_internal"
    (internal / "web").mkdir(parents=True)
    (internal / "web" / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (internal / "playwright").mkdir()
    import os
    installer_name = "install.ps1" if os.name == "nt" else "install.sh"
    (internal / installer_name).write_text("# installer", encoding="utf-8")
    profile = RuntimeProfile("installed", root, "1", None)

    assert profile.web_dist == internal / "web"
    assert profile.installer == internal / installer_name

    paths = OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()
    environment = load_environment(paths, profile)
    assert Path(environment.values["PLAYWRIGHT_BROWSERS_PATH"]) == (internal / "playwright").resolve()
