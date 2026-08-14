# Packaged update flow and runtime version

## Decision

The runtime version is embedded in `src/agentos/version.py` and consumed by `RuntimeProfile`. This avoids stale `agentos-*.dist-info` metadata copied by PyInstaller, which had made `orin --version` report 0.1.8 after the project had moved forward.

The packaged Electron shell sends verified release metadata to the frontend through a narrow preload IPC bridge. The frontend renders a visible update banner with the current and latest versions. Its button invokes the launcher command with the fixed `update` verb; it does not accept a command or executable from the renderer.

## Validation

- Python unit suite: 1254 passed, 3 skipped.
- Frontend suite: 271 passed.
- `dist/runtime/orin.exe --version`: `orin 0.1.11 (installed)`.
- Electron packaged runtime version: `orin 0.1.11 (installed)`.
- Chromium is present under the packaged `_internal/playwright` layout.
- Release archive `Orin-0.1.11-windows-x64.zip` was assembled with a matching `release.json` SHA-256.

## Maintenance note

Each release must update the embedded package version, `pyproject.toml`, and the Electron package/lock versions together before rebuilding the Windows runtime and desktop host.
