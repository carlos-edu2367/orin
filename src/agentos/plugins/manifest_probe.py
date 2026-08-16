"""Checks, with a single request, whether a public GitHub repository has an
installable Claude plugin manifest (``.claude-plugin/plugin.json`` or a root
``plugin.json``) — the same two locations ``PluginFetcher``/
``inspect_plugin_package`` already look for. Keyless and unauthenticated,
same as ``github_search.py``; a failed or rate-limited check returns
``None`` rather than raising, since "couldn't tell" must never be confused
with "confirmed absent."
"""
from __future__ import annotations

from typing import Any, Mapping

import httpx

PROBE_TIMEOUT_SECONDS = 15
DEFAULT_ENDPOINT = "https://api.github.com/repos"


class GithubManifestProbe:
    def __init__(self, client: httpx.Client | None = None, *, endpoint: str = DEFAULT_ENDPOINT) -> None:
        self._endpoint = endpoint
        self._client = client or httpx.Client(timeout=PROBE_TIMEOUT_SECONDS)
        self._owns_client = client is None

    def probe(self, owner: str, repo: str) -> bool | None:
        try:
            response = self._client.get(
                f"{self._endpoint}/{owner}/{repo}/contents",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "orin-plugin-discovery",
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError:
            return None
        except (TypeError, ValueError):
            return False
        return self._has_manifest(body)

    @staticmethod
    def _has_manifest(body: Any) -> bool:
        if not isinstance(body, list):
            return False
        for item in body:
            if not isinstance(item, Mapping):
                continue
            if item.get("name") == ".claude-plugin" and item.get("type") == "dir":
                return True
            if item.get("name") == "plugin.json" and item.get("type") == "file":
                return True
        return False

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["DEFAULT_ENDPOINT", "GithubManifestProbe"]
