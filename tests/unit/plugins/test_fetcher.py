import json
from agentos.plugins.fetcher import FetchRejected, PluginFetcher
from agentos.plugins.sources import resolve_source

def _package(root):
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name":"demo","version":"1.0.0"}), encoding="utf-8")
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\nversion: 1.0.0\ndescription: d\n---\n\nbody", encoding="utf-8")
    return root

def test_fetch_keeps_old_digest_and_stores_republished_version_separately(tmp_path):
    source = _package(tmp_path / "src")
    fetcher = PluginFetcher(tmp_path / "plugins")
    first = fetcher.fetch(resolve_source(str(source)))
    (source / "skills" / "demo" / "SKILL.md").write_text("changed", encoding="utf-8")
    second = fetcher.fetch(resolve_source(str(source)))

    assert len(first.package_digest) == 64
    assert second.package_digest != first.package_digest
    assert second.path != first.path
    assert first.path.exists() and second.path.exists()


def _repo_without_manifest(root):
    root.mkdir(parents=True)
    (root / "README.md").write_text("no manifest here", encoding="utf-8")
    (root / "package.json").write_text('{"name": "demo-mcp", "bin": "./cli.js"}', encoding="utf-8")
    return root


def test_fetch_raw_clones_a_repository_with_no_manifest_and_cleans_up(tmp_path):
    source_dir = _repo_without_manifest(tmp_path / "raw-src")
    fetcher = PluginFetcher(tmp_path / "plugins")
    source = resolve_source(str(source_dir))
    captured_path = None
    with fetcher.fetch_raw(source) as path:
        captured_path = path
        assert (path / "package.json").is_file()
        assert not (path / ".claude-plugin").exists()
    assert not captured_path.exists()
    assert not (tmp_path / "plugins" / "demo-mcp").exists()


def test_fetch_raw_still_enforces_the_size_and_symlink_guards(tmp_path):
    source_dir = tmp_path / "raw-src"
    source_dir.mkdir()
    fetcher = PluginFetcher(tmp_path / "plugins", max_files=0)
    (source_dir / "file.txt").write_text("x", encoding="utf-8")
    source = resolve_source(str(source_dir))
    try:
        with fetcher.fetch_raw(source):
            pass
    except FetchRejected:
        pass
    else:
        raise AssertionError("expected fetch_raw to enforce the file-count budget")
