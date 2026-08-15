import json
import pytest
from agentos.plugins.fetcher import FetchRejected, PluginFetcher
from agentos.plugins.sources import resolve_source

def _package(root):
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name":"demo","version":"1.0.0"}), encoding="utf-8")
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\nversion: 1.0.0\ndescription: d\n---\n\nbody", encoding="utf-8")
    return root

def test_local_fetch_is_bounded_and_digest_changes(tmp_path):
    source = _package(tmp_path / "src")
    fetcher = PluginFetcher(tmp_path / "plugins")
    first = fetcher.fetch(resolve_source(str(source)))
    (source / "skills" / "demo" / "SKILL.md").write_text("changed", encoding="utf-8")
    with pytest.raises(FetchRejected): fetcher.fetch(resolve_source(str(source)))
    assert len(first.package_digest) == 64
