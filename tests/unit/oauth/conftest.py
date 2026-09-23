import pytest


@pytest.fixture(autouse=True)
def _skip_dns_resolution(monkeypatch):
    # Hosts like auth.example.com do not resolve in a sandbox. The refusal
    # tests put the real policy back explicitly.
    monkeypatch.setattr("agentos.oauth.netpolicy._public_url", lambda url, resolve_dns=False: url)
