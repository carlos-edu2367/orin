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
