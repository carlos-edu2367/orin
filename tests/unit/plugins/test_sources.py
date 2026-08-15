import pytest
from agentos.plugins.sources import SourceRejected, resolve_source

def test_sources_are_resolved_without_private_network_targets(tmp_path):
    assert resolve_source("obra/superpowers").url.endswith("superpowers.git")
    source = resolve_source("https://github.com/a/b/tree/main/plugins/x")
    assert source.ref == "main" and source.subdirectory == "plugins/x"
    assert resolve_source(str(tmp_path)).kind == "path"
    with pytest.raises(SourceRejected): resolve_source("https://127.0.0.1/x/y")
