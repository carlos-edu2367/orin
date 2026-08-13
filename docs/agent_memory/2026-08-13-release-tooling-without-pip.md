# Release tooling uses the locked uv environment

The release workflow creates its virtual environment with `uv`, which omits
`pip` by default. PyInstaller is therefore a pinned development dependency in
`pyproject.toml`/`uv.lock`, and `scripts/build-windows.ps1` invokes it directly
from the synchronized virtual environment. This keeps the release reproducible
and avoids an undeclared network install during packaging.
