# Windows CI runtime dependencies

The Windows GitHub Actions runner does not provide either the IANA timezone
database or Playwright's Python Chromium binary by default. `tzdata` is now a
runtime dependency so scheduled chats can resolve user timezones on Windows.
The backend CI job installs Chromium through the same Python Playwright package
used by the isolated browser worker before running the suite.

The release build already provisions Chromium to `build/playwright` and passes
that directory to PyInstaller, so this CI adjustment verifies the same runtime
path rather than adding a target-machine dependency.
