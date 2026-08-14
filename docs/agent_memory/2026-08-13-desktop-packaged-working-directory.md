# Packaged desktop working directory

`orin.exe` is packaged in `resources/runtime`, while Electron Builder places
`Orin Desktop.exe` in the `win-unpacked` root. The frozen launcher must use
that host root as the Electron process working directory. Reusing the
development `desktop` directory created a non-existent `resources/runtime/desktop`
path and failed on Windows with `WinError 267` before Electron started.

The release now has a test that asserts both the packaged executable discovery
and its working directory. The CLI also accepts `orin --update` as an alias for
the existing `orin update` command.

The Python package version must stay aligned with `desktop/package.json` for
each release so `orin --version`, runtime status, the installer manifest, and
the Electron updater all identify the same build.
