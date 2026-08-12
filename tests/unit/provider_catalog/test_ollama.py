from __future__ import annotations

import pytest

from agentos.provider_catalog.ollama import (
    DEFAULT_OLLAMA_BASE_URL,
    OLLAMA_CLOUD_BASE_URL,
    is_ollama_cloud,
    normalize_ollama_base_url,
)


def test_normalizes_the_local_default_without_leaking_credentials() -> None:
    assert normalize_ollama_base_url("http://localhost:11434/") == "http://localhost:11434"
    assert normalize_ollama_base_url(DEFAULT_OLLAMA_BASE_URL) == DEFAULT_OLLAMA_BASE_URL
    with pytest.raises(ValueError):
        normalize_ollama_base_url("ftp://localhost:11434")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("http://key@localhost:11434")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("http://localhost:11434?token=secret")
    with pytest.raises(ValueError):
        normalize_ollama_base_url("   ")


def test_strips_an_api_or_v1_suffix_the_user_may_have_pasted() -> None:
    """The native API lives at /api/*, so the base URL must be the bare origin."""
    assert normalize_ollama_base_url("http://localhost:11434/v1") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://localhost:11434/api/") == "http://localhost:11434"
    assert normalize_ollama_base_url("https://gpu.lan/ollama/v1") == "https://gpu.lan/ollama"


def test_cloud_is_recognized_by_host_not_by_a_stored_mode_flag() -> None:
    assert is_ollama_cloud(OLLAMA_CLOUD_BASE_URL) is True
    assert is_ollama_cloud("https://api.ollama.com") is True
    assert is_ollama_cloud(DEFAULT_OLLAMA_BASE_URL) is False
    assert is_ollama_cloud("http://gpu.lan:11434") is False
    assert is_ollama_cloud("https://notollama.com") is False
