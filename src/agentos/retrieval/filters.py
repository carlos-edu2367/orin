"""What never reaches the index.

The secret denylist is applied before the chunker, not as a result filter. With
a remote embedder configured, indexed content leaves the machine: a ``.env``
inside a chunk would become an API key inside an HTTP request body.

The build/dependency denylist and the ``.gitignore`` reader live in
``agentos.ignore``, shared with the conversation workspace's own tools so a
directory hidden from the agent's file listings is exactly the one never
embedded. This module adds the index-specific half: names and suffixes that
must never leave the machine even when nothing ignores them.
"""
from __future__ import annotations

from agentos.ignore import DENIED_SEGMENTS, GitignoreFilter

DENIED_NAMES = frozenset({"uv.lock", "package-lock.json", "poetry.lock", "yarn.lock", "Cargo.lock"})

# Exact, case-insensitive filenames rejected regardless of extension. These
# are conventional credential filenames that carry no distinguishing prefix or
# suffix of their own — a Google Cloud service-account key is credentials.json,
# an SSH authorized-keys file has no extension at all.
SECRET_NAMES = frozenset({
    "credentials", "credentials.json", "authorized_keys",
    "secrets.yaml", "secrets.yml", "secrets.json",
})

# Prefix/suffix rules for material that must never be embedded. Checked against
# the file name only, so a directory called ``keys`` is not itself excluded.
SECRET_PREFIXES = (".env", "id_rsa", "id_ed25519", "id_ecdsa")
SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks")
SECRET_EXEMPT_SUFFIXES = (".example", ".sample", ".template")


class IndexFilter:
    """The single gate every candidate path passes through before being read."""

    def __init__(self, gitignore: GitignoreFilter) -> None:
        self._gitignore = gitignore

    def rejects(self, relative_path: str) -> bool:
        segments = relative_path.split("/")
        name = segments[-1]
        if any(segment in DENIED_SEGMENTS for segment in segments):
            return True
        if name in DENIED_NAMES:
            return True
        if self._is_secret(name):
            return True
        return self._gitignore.ignores(relative_path)

    @staticmethod
    def _is_secret(name: str) -> bool:
        lowered = name.lower()
        if lowered.endswith(SECRET_EXEMPT_SUFFIXES):
            return False
        if lowered in SECRET_NAMES:
            return True
        return lowered.startswith(SECRET_PREFIXES) or lowered.endswith(SECRET_SUFFIXES)


__all__ = ["DENIED_NAMES", "DENIED_SEGMENTS", "GitignoreFilter", "IndexFilter", "SECRET_NAMES"]
