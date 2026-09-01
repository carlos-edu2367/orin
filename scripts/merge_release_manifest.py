"""Fold the Windows and Linux build outputs into one release manifest.

The flat top-level fields (``archive_url``, ``archive_sha256``) keep
representing Windows exactly as they always have: an ``install.ps1`` already
sitting on someone's machine only ever reads those four names, and it has no
way to learn about a ``platforms`` key it was written before. Breaking that
would silently break ``orin update`` for every existing Windows install the
next time this script runs. Linux — which never existed in the manifest
before this script did — is added purely as a new nested key, never as a
replacement for the flat shape.
"""
from __future__ import annotations

import json
import sys

_REQUIRED_WINDOWS_FIELDS = ("version", "archive_url", "archive_sha256")
_REQUIRED_LINUX_FIELDS = ("archive_url", "archive_sha256")


def merge_manifest(windows: dict, linux: dict, *, release_url: str) -> dict:
    for field in _REQUIRED_WINDOWS_FIELDS:
        if not windows.get(field):
            raise ValueError(f"windows manifest is missing '{field}'")
    for field in _REQUIRED_LINUX_FIELDS:
        if not linux.get(field):
            raise ValueError(f"linux manifest is missing '{field}'")
    return {
        "version": windows["version"],
        "archive_url": windows["archive_url"],
        "archive_sha256": windows["archive_sha256"],
        "release_url": release_url,
        "platforms": {
            "linux-x64": {
                "archive_url": linux["archive_url"],
                "archive_sha256": linux["archive_sha256"],
            },
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: merge_release_manifest.py <windows.json> <linux.json> <release_url>", file=sys.stderr)
        return 2
    windows_path, linux_path, release_url = argv[1], argv[2], argv[3]
    with open(windows_path, encoding="utf-8") as handle:
        windows = json.load(handle)
    with open(linux_path, encoding="utf-8") as handle:
        linux = json.load(handle)
    manifest = merge_manifest(windows, linux, release_url=release_url)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
