# Vendored binaries

`ripgrep/rg.exe` (Windows) or `ripgrep/rg` (Linux), when present, is bundled into the frozen runtime as
`_internal/bin/` and used by the Grep tool. Download the official release for the target platform from
https://github.com/BurntSushi/ripgrep/releases, verify it against the published SHA-256, and place it here
before running the packaging script. The binary is never committed. Without it Grep uses Orin's Python search.
