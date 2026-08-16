import httpx

from agentos.plugins.manifest_probe import GithubManifestProbe


def test_probe_finds_a_dotted_plugin_directory():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/has-manifest/contents"
        return httpx.Response(200, json=[
            {"name": ".claude-plugin", "type": "dir"},
            {"name": "README.md", "type": "file"},
        ])

    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(handler)))
    assert probe.probe("acme", "has-manifest") is True


def test_probe_finds_a_root_plugin_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "plugin.json", "type": "file"}])

    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(handler)))
    assert probe.probe("acme", "has-manifest") is True


def test_probe_returns_false_when_neither_is_present():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "README.md", "type": "file"}, {"name": "src", "type": "dir"}])

    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(handler)))
    assert probe.probe("acme", "no-manifest") is False


def test_probe_returns_none_on_http_error():
    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403))))
    assert probe.probe("acme", "rate-limited") is None


def test_probe_returns_none_on_a_malformed_body():
    probe = GithubManifestProbe(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"not": "a list"}))))
    assert probe.probe("acme", "weird") is False
