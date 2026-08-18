"""Release state, install, and safe cleanup for packaged installations."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .profile import RuntimeProfile


_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")
_REPOSITORY = os.environ.get("ORIN_RELEASE_REPOSITORY", "carlos-edu2367/orin")


def _version_key(value: str) -> tuple[int, int, int, int, str]:
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ValueError("invalid semantic version")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    prerelease = match.group(4) or ""
    # Stable releases sort above prereleases. The textual suffix is only a
    # deterministic tie-breaker; release ordering is not used for security.
    return major, minor, patch, 1 if not prerelease else 0, prerelease


def _active_version_dir(profile: RuntimeProfile) -> Path | None:
    if profile.is_development or not getattr(sys, "frozen", False):
        return None
    runtime = profile.root.resolve()
    if runtime.name.lower() != "runtime" or runtime.parent.name.lower() != "resources":
        return None
    version_dir = runtime.parent.parent
    if version_dir.name != profile.version or _VERSION.fullmatch(version_dir.name) is None:
        return None
    return version_dir


def _installation_root(profile: RuntimeProfile) -> Path | None:
    active = _active_version_dir(profile)
    return active.parent if active is not None else None


def _latest_release() -> dict[str, str] | None:
    request = Request(
        f"https://api.github.com/repos/{_REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Orin-local-runtime"},
    )
    with urlopen(request, timeout=3.0) as response:  # noqa: S310 - fixed official GitHub endpoint
        payload = json.load(response)
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name")
    version = str(tag)[1:] if isinstance(tag, str) and tag.startswith("v") else str(tag or "")
    if _VERSION.fullmatch(version) is None:
        return None
    html_url = payload.get("html_url")
    result = {"version": version, "url": str(html_url) if isinstance(html_url, str) else f"https://github.com/{_REPOSITORY}/releases/tag/v{version}"}
    published_at = payload.get("published_at")
    if isinstance(published_at, str) and published_at:
        result["published_at"] = published_at
    return result


def _installed_versions(profile: RuntimeProfile) -> list[dict[str, Any]]:
    root = _installation_root(profile)
    active = _active_version_dir(profile)
    if root is None or active is None or not root.is_dir():
        return []
    versions: list[dict[str, Any]] = []
    for candidate in root.iterdir():
        if not candidate.is_dir() or candidate.is_symlink() or _VERSION.fullmatch(candidate.name) is None:
            continue
        versions.append({
            "version": candidate.name,
            "is_current": candidate.resolve() == active,
            "removable": candidate.resolve() != active,
        })
    return sorted(versions, key=lambda item: _version_key(str(item["version"])), reverse=True)


def read_installation_status(profile: RuntimeProfile | None = None) -> dict[str, Any]:
    profile = profile or RuntimeProfile.detect()
    try:
        latest = _latest_release()
        latest_error = None
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        latest = None
        latest_error = "unavailable"
    update_available = False
    if latest is not None:
        try:
            update_available = _version_key(latest["version"]) > _version_key(profile.version)
        except ValueError:
            update_available = False
    return {
        "installation_kind": profile.kind,
        "current_version": profile.version,
        "installed_versions": _installed_versions(profile),
        "latest_release": latest,
        "latest_release_error": latest_error,
        "update_available": update_available,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def start_update(profile: RuntimeProfile | None = None) -> dict[str, Any]:
    """Install the latest verified release side by side with the running one.

    Downloads and verifies the release exactly the way ``orin update`` does
    (``install.ps1``'s SHA-256 check against its signed manifest), but --
    unlike ``orin update`` -- never stops the process handling this request.
    A packaged install is versioned side by side (see this package's module
    docstring): the new version lands in its own directory and only the
    ``current`` pointer changes, which a process already running from the
    old resolved path does not need to still exist for. The user still has
    to close and reopen Orin afterward to actually run the new version --
    the CLI's own ``orin update`` leaves that same follow-up step to the
    user, it just also stops the old process first.
    """
    profile = profile or RuntimeProfile.detect()
    if profile.kind != "installed":
        raise ValueError("installing a release is only available for a packaged installation")
    installer = profile.installer
    if not installer.is_file():
        raise ValueError(f"the release installer is missing: {installer}")
    try:
        result = subprocess.run(
            # -NoDesktopShortcut: this call has no attached terminal (it runs
            # from an API request's threadpool worker), so install.ps1's
            # "create a desktop shortcut? [Y/n]" Read-Host prompt -- which
            # only fires when this flag is absent and no shortcut exists yet
            # -- would otherwise block reading from whatever this process's
            # stdin happens to be, indefinitely or with unpredictable answers.
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer), "-NoDesktopShortcut"],
            check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("the installer did not finish within 5 minutes") from error
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "the installer failed").strip()[:2000])
    return {"started": True}


def remove_installed_version(version: str, profile: RuntimeProfile | None = None) -> dict[str, Any]:
    profile = profile or RuntimeProfile.detect()
    if _VERSION.fullmatch(version) is None:
        raise ValueError("invalid release version")
    root = _installation_root(profile)
    active = _active_version_dir(profile)
    if root is None or active is None:
        raise ValueError("version cleanup is available only in a packaged installation")
    target = root / version
    if target.parent != root or not target.is_dir() or target.is_symlink():
        raise FileNotFoundError(version)
    if target.resolve() == active:
        raise ValueError("the current version cannot be removed")
    shutil.rmtree(target)
    return {"removed_version": version}


__all__ = ["read_installation_status", "remove_installed_version", "start_update"]
