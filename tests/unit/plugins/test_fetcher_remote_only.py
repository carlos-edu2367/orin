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
