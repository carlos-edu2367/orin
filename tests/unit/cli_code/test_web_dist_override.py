"""W6 Task 5: ORIN_WEB_DIST names the interface build explicitly (packaging, and the runtime tests' stub build)."""
from __future__ import annotations

from pathlib import Path

from agentos.installation.profile import RuntimeProfile


def test_orin_web_dist_is_used_as_is(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repo"
    (repository / "frontend" / "dist").mkdir(parents=True)
    (repository / "frontend" / "dist" / "index.html").write_text("x", encoding="utf-8")
    profile = RuntimeProfile("development", repository, "9.9.9", repository)
    assert profile.web_dist == repository / "frontend" / "dist"
    monkeypatch.setenv("ORIN_WEB_DIST", str(tmp_path / "stub"))
    assert profile.web_dist == tmp_path / "stub"  # even when it has no build: load_environment then says so


def test_a_blank_override_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORIN_WEB_DIST", "  ")
    profile = RuntimeProfile("installed", tmp_path, "9.9.9", None)
    assert profile.web_dist != Path("  ")
