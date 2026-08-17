from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .manifest import ManifestRejected, PluginManifest, parse_plugin_manifest
from .sources import PluginSource


class FetchRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FetchedPlugin:
    path: Path
    package_digest: str
    manifest: PluginManifest


class PluginFetcher:
    def __init__(self, root: Path, *, max_bytes: int = 25_000_000, max_files: int = 4000, timeout: int = 120) -> None:
        self.root = Path(root)
        self.max_bytes, self.max_files, self.timeout = max_bytes, max_files, timeout

    def fetch(self, source: PluginSource) -> FetchedPlugin:
        with tempfile.TemporaryDirectory(prefix="orin-plugin-") as temporary:
            staging = self._clone_into(source, temporary)
            manifest_path = staging / ".claude-plugin" / "plugin.json"
            if not manifest_path.exists():
                manifest_path = staging / "plugin.json"
            try:
                manifest = parse_plugin_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
            except ManifestRejected as error:
                # A path escaping the package is a distinct, more actionable
                # failure than "no manifest at all"; let it propagate as-is so
                # the service layer can surface its own error code for it.
                if "escape the plugin package" in str(error) or "relative to the plugin package" in str(error):
                    raise
                raise FetchRejected("plugin package has no valid manifest") from error
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise FetchRejected("plugin package has no valid manifest") from error
            self._validate(staging)
            digest = self._digest(staging)
            destination = self.root / manifest.plugin_id / manifest.version
            if destination.exists() and self._digest(destination) != digest:
                # Some real-world plugin repositories republish a version tag
                # after release. Keep the old package immutable, but allow the
                # newly fetched digest to be inspected and explicitly approved
                # instead of turning the cache collision into a generic
                # "plugin could not be inspected" error.
                destination = destination.parent / f"{manifest.version}-{digest}"
            if destination.exists():
                if self._digest(destination) != digest:
                    raise FetchRejected("plugin cache contains a conflicting digest")
                return FetchedPlugin(destination, digest, manifest)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staging, destination, symlinks=False)
            return FetchedPlugin(destination, digest, manifest)

    @contextmanager
    def fetch_raw(self, source: PluginSource):
        """Clone-only variant of ``fetch`` for repositories with no plugin manifest.

        Applies the same clone and validation guards as ``fetch`` but never reads
        a manifest and never persists into ``self.root`` — the yielded path is
        only valid for the lifetime of this context, long enough to read a
        handful of config files before the clone is discarded.
        """
        with tempfile.TemporaryDirectory(prefix="orin-plugin-raw-") as temporary:
            staging = self._clone_into(source, temporary)
            self._validate(staging)
            yield staging

    def _clone_into(self, source: PluginSource, temporary: str) -> Path:
        staging = Path(temporary) / "package"
        if source.kind == "path":
            self._validate(Path(source.path or ""))
            shutil.copytree(Path(source.path or ""), staging, symlinks=False)
        elif source.kind == "git":
            command = ["git", "clone", "--depth", "1", "--no-tags", "--recurse-submodules=no", "--config", "core.symlinks=false"]
            if source.ref:
                command += ["--branch", source.ref]
            command += [source.url or "", str(staging)]
            try:
                subprocess.run(command, check=True, shell=False, timeout=self.timeout, capture_output=True, text=True)
            except (OSError, subprocess.SubprocessError) as error:
                raise FetchRejected("plugin repository could not be fetched") from error
            if source.subdirectory:
                selected = staging / source.subdirectory
                if not selected.is_dir() or not selected.resolve().is_relative_to(staging.resolve()):
                    raise FetchRejected("plugin subdirectory escapes the repository")
                package = Path(temporary) / "selected"
                shutil.copytree(selected, package, symlinks=False)
                shutil.rmtree(staging, ignore_errors=True)
                staging = package
        else:
            raise FetchRejected("source must be resolved to a git or path source before fetching")
        git_dir = staging / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir, ignore_errors=True)
        return staging

    def _validate(self, path: Path) -> None:
        if not path.is_dir():
            raise FetchRejected("plugin package must be a directory")
        total, files = 0, 0
        for item in path.rglob("*"):
            relative = item.relative_to(path)
            if item.is_symlink() or any(part in {"", ".."} for part in relative.parts) or relative.is_absolute():
                raise FetchRejected("plugin package contains an unsafe path or symlink")
            if item.is_file():
                files += 1
                total += item.stat().st_size
        if files > self.max_files or total > self.max_bytes:
            raise FetchRejected("plugin package exceeds its size or file-count budget")

    @staticmethod
    def _digest(path: Path) -> str:
        entries: list[tuple[str, str]] = []
        for item in sorted(path.rglob("*")):
            if item.is_file() and not item.is_symlink():
                content = hashlib.sha256(item.read_bytes()).hexdigest()
                entries.append((item.relative_to(path).as_posix(), content))
        return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()
