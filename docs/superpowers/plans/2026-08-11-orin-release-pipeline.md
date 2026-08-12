# Orin Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pushing a `v*` tag produces a GitHub Release carrying everything an installer needs — a wheel with the web interface inside it, the five PostgreSQL builds with checksums, and a manifest naming the version.

**Architecture:** One platform-independent wheel, because the only platform-specific thing Orin ships is PostgreSQL, and that is a separate asset. The frontend is built once in CI and copied into the package before `python -m build`, which is all `RuntimeProfile.web_dist` needs to find it in an installation with no repository. A second workflow gates every push, because a release pipeline on top of an untested branch just publishes failures faster.

**Tech Stack:** GitHub Actions, `uv`, `python -m build`, setuptools, Node 20, `gh` CLI.

This is part two of three. **Part one (`2026-08-11-orin-single-datastore.md`) must be merged first** — the release built here has no Docker Compose file and would be unusable without the embedded database. Part three consumes what this publishes.

Spec: [`docs/superpowers/specs/2026-08-11-orin-distribution-design.md`](../specs/2026-08-11-orin-distribution-design.md)

## Global Constraints

- The wheel is `py3-none-any`. Never build platform wheels.
- Asset names are a contract with the installers in part three and with `agentos.services.postgres_binaries.asset_name`. They are: `orin-<version>-py3-none-any.whl`, `postgres-16-<tag>.txz`, `SHA256SUMS`, `manifest.json`, `install.ps1`, `install.sh`.
- The tag and the version in `pyproject.toml` must agree. A mismatch fails the release rather than publishing a lie.
- Workflows pin action versions by major tag (`actions/checkout@v4`), never `@master`.
- `GITHUB_TOKEN` with `contents: write` is the only credential. No PyPI publishing in this plan — the installer fetches the wheel from the release.
- Python 3.13, Node 20.

## File Structure

**Created**

| File | Responsibility |
| --- | --- |
| `scripts/bundle-web.py` | Copies the built frontend into the package. One job, so it can be run locally exactly as CI runs it. |
| `.github/workflows/ci.yml` | The gate on every push and pull request. |
| `.github/workflows/release.yml` | Tag → artifacts → GitHub Release. |
| `tests/unit/installation/test_packaged_web.py` | Proves the profile finds a web bundle placed inside the package. |

**Modified**

| File | Change |
| --- | --- |
| `pyproject.toml` | Package data for the web bundle and the migration scripts; ruff configuration if absent. |
| `.gitignore` | `src/agentos/web/` — a build output that must never be committed. |

---

### Task 1: Put the web interface inside the package

`RuntimeProfile.web_dist` already looks for `_package_root() / "web"` when there is no checkout. Nothing has ever put a bundle there. This task makes that path real and proves it.

**Files:**
- Create: `scripts/bundle-web.py`, `tests/unit/installation/test_packaged_web.py`
- Modify: `pyproject.toml`, `.gitignore`

**Interfaces:**
- Consumes: `RuntimeProfile` from `agentos.installation`.
- Produces: a `src/agentos/web/` directory in built distributions; `scripts/bundle-web.py` as the only supported way to create it.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/installation/test_packaged_web.py` (create `tests/unit/installation/__init__.py` as an empty file if the directory does not exist):

```python
from __future__ import annotations

from pathlib import Path

from agentos.installation.profile import RuntimeProfile


def test_an_installation_serves_the_web_bundle_shipped_inside_the_package(tmp_path: Path, monkeypatch) -> None:
    # What a wheel looks like once bundle-web.py has run: no repository, and the
    # built interface sitting next to the code that serves it.
    package_root = tmp_path / "site-packages" / "agentos"
    (package_root / "web").mkdir(parents=True)
    (package_root / "web" / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    monkeypatch.setattr("agentos.installation.profile._package_root", lambda: package_root)

    profile = RuntimeProfile("installed", package_root, "1.0.0", None)

    assert profile.web_dist == package_root / "web"


def test_an_installation_without_a_bundle_reports_where_it_looked(tmp_path: Path, monkeypatch) -> None:
    package_root = tmp_path / "site-packages" / "agentos"
    package_root.mkdir(parents=True)
    monkeypatch.setattr("agentos.installation.profile._package_root", lambda: package_root)

    profile = RuntimeProfile("installed", tmp_path / "install", "1.0.0", None)

    assert profile.web_dist is not None
    assert not (profile.web_dist / "index.html").is_file()
```

- [ ] **Step 2: Run it**

```bash
python -m pytest tests/unit/installation/test_packaged_web.py -q
```

Expected: both pass already — `web_dist` supports this. If they fail, `_package_root` was renamed and the test's monkeypatch target needs correcting. This test exists to keep the behaviour from being refactored away, not to drive new code.

- [ ] **Step 3: Write the bundling script**

Create `scripts/bundle-web.py`:

```python
"""Copy the built web interface into the package, ready to be shipped in a wheel.

An installation has no repository and no bundler. The backend serves static
files out of the package itself, which is what ``RuntimeProfile.web_dist``
resolves to when no checkout is found. This script is the only supported way to
put them there, so a release and a local build never disagree about the shape.

    npm --prefix frontend ci
    npm --prefix frontend run build
    python scripts/bundle-web.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "frontend" / "dist"
TARGET = ROOT / "src" / "agentos" / "web"


def main() -> int:
    if not (SOURCE / "index.html").is_file():
        sys.stderr.write(
            f"No web build at {SOURCE}.\n"
            "Build it first:\n"
            "  npm --prefix frontend ci\n"
            "  npm --prefix frontend run build\n"
        )
        return 1
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    files = sum(1 for path in TARGET.rglob("*") if path.is_file())
    print(f"bundled {files} files into {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Declare the package data**

In `pyproject.toml`, add after the `[tool.setuptools.packages.find]` block:

```toml
[tool.setuptools.package-data]
# The web interface and the migration scripts are data the runtime reads at
# execution time. Without this they are simply absent from a wheel, and an
# installation starts, answers /healthz, and hands the browser a 404.
agentos = [
    "web/**/*",
    "persistence/postgres/migrations/**/*",
    "services/postgres_checksums.json",
]
```

- [ ] **Step 5: Ignore the bundling output**

Add to `.gitignore`, beneath the `dist/` entry:

```
# Written by scripts/bundle-web.py; a build output, never a source file.
src/agentos/web/
```

- [ ] **Step 6: Build a wheel and prove the bundle is inside it**

```bash
npm --prefix frontend ci
```

```bash
npm --prefix frontend run build
```

```bash
python scripts/bundle-web.py
```

Expected: `bundled <N> files into .../src/agentos/web`, with N well above 1.

```bash
uv pip install build --quiet
```

```bash
python -m build --wheel
```

```bash
python -c "import zipfile,glob; names=zipfile.ZipFile(sorted(glob.glob('dist/*.whl'))[-1]).namelist(); print('web/index.html:', any(n.endswith('agentos/web/index.html') for n in names)); print('migrations env.py:', any(n.endswith('migrations/env.py') for n in names))"
```

Expected:

```text
web/index.html: True
migrations env.py: True
```

If either is `False`, the `package-data` glob is wrong; fix it and rebuild before continuing.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore scripts/bundle-web.py tests/unit/installation
git commit -m "build: ship the web interface inside the package"
```

---

### Task 2: Gate every push

A release pipeline sitting on top of an ungated branch just publishes failures faster. This is the check that has only ever existed as a habit.

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: a required status check named `python` and one named `frontend`.

- [ ] **Step 1: Confirm the commands pass locally first**

```bash
python -m pytest -q tests/unit
```

```bash
npm --prefix frontend run lint
```

```bash
npm --prefix frontend test
```

Expected: all green. **If `ruff` is not configured in `pyproject.toml`, run `ruff check src tests` now and note the failure count** — if it is large, add `[tool.ruff]` with `line-length = 160` and `lint.select = ["E", "F", "I", "S", "SIM"]` matching the `# noqa` codes already used in the codebase, then fix or explicitly ignore what remains. CI must start green; a workflow that is red on day one gets ignored.

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - name: Install
        run: uv sync --python 3.13 --extra dev || uv pip install --python 3.13 -e ".[dev]" || uv pip install --python 3.13 -e . ruff pytest
      - name: Lint
        run: uv run ruff check src tests
      - name: Unit tests
        # The integration suite needs a database and is exercised by the install
        # smoke matrix instead, where a real cluster exists.
        run: uv run python -m pytest -q tests/unit

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm run lint
      - run: npm test
      - run: npm run build
```

- [ ] **Step 3: Push it on a branch and watch it run**

```bash
git checkout -b ci-gate
```

```bash
git add .github/workflows/ci.yml
git commit -m "ci: gate every push with lint, unit tests and a frontend build"
```

```bash
git push -u origin ci-gate
```

```bash
gh run watch
```

Expected: both jobs green. If the `Install` step's fallback chain is what succeeded, simplify it to the single command that worked — a chain of `||` in CI hides which one is real.

- [ ] **Step 4: Merge**

```bash
gh pr create --fill --title "ci: gate every push" && gh pr merge --squash --delete-branch
```

---

### Task 3: Publish a release from a tag

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `scripts/bundle-web.py` (Task 1), `scripts/mirror-postgres.py` (part one, Task 6).
- Produces: a GitHub Release for tag `v<version>` carrying `orin-<version>-py3-none-any.whl`, `postgres-16-{windows-amd64,darwin-arm64,darwin-amd64,linux-amd64,linux-arm64}.txz`, `SHA256SUMS`, `manifest.json`, `install.ps1`, `install.sh`.
- `manifest.json` shape, which part three's installers parse:

```json
{"version": "0.1.0", "wheel": "orin-0.1.0-py3-none-any.whl", "postgres_major": "16", "python": "3.13"}
```

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/release.yml`:

```yaml
name: release

on:
  push:
    tags: ["v*"]
  workflow_dispatch:
    inputs:
      tag:
        description: "Existing tag to build (dry run; publishes a draft)"
        required: true

permissions:
  contents: write

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.version.outputs.version }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Check the tag against the packaged version
        id: version
        run: |
          set -euo pipefail
          packaged="$(uv run --python 3.13 python -c 'import tomllib,pathlib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
          tag="${GITHUB_REF_NAME#v}"
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then tag="${{ inputs.tag }}"; tag="${tag#v}"; fi
          if [ "$packaged" != "$tag" ]; then
            echo "::error::tag v$tag does not match pyproject version $packaged"
            exit 1
          fi
          echo "version=$packaged" >> "$GITHUB_OUTPUT"

      - name: Build the web interface
        run: |
          npm --prefix frontend ci
          npm --prefix frontend run build
          uv run --python 3.13 python scripts/bundle-web.py

      - name: Build the wheel
        run: |
          uv pip install --python 3.13 --system build
          uv run --python 3.13 python -m build --wheel --sdist

      - name: Verify the wheel carries the interface
        run: |
          uv run --python 3.13 python - <<'PY'
          import glob, sys, zipfile
          wheel = sorted(glob.glob("dist/*.whl"))[-1]
          names = zipfile.ZipFile(wheel).namelist()
          missing = [
              path for path, present in {
                  "agentos/web/index.html": any(n.endswith("agentos/web/index.html") for n in names),
                  "migrations/env.py": any(n.endswith("migrations/env.py") for n in names),
                  "postgres_checksums.json": any(n.endswith("services/postgres_checksums.json") for n in names),
              }.items() if not present
          ]
          if missing:
              sys.exit(f"{wheel} is missing: {', '.join(missing)}")
          print(f"{wheel} is complete")
          PY

      - name: Mirror the PostgreSQL builds
        run: |
          uv pip install --python 3.13 --system httpx
          uv run --python 3.13 python scripts/mirror-postgres.py --out dist/postgres --no-pin

      - name: Confirm the pinned checksums match what is being published
        # The launcher verifies downloads against the checksums committed in the
        # package. If they drift from the published assets, every fresh install
        # fails its integrity check -- so the release refuses to ship that.
        run: |
          uv run --python 3.13 python - <<'PY'
          import json, pathlib, sys
          pinned = json.loads(pathlib.Path("src/agentos/services/postgres_checksums.json").read_text())
          published = {}
          for line in pathlib.Path("dist/postgres/SHA256SUMS").read_text().splitlines():
              digest, name = line.split()
              published[name.removeprefix("postgres-16-").removesuffix(".txz")] = digest
          drift = {tag: (pinned.get(tag), published.get(tag)) for tag in set(pinned) | set(published) if pinned.get(tag) != published.get(tag)}
          if drift:
              sys.exit(f"pinned checksums disagree with the published assets: {drift}\nRun scripts/mirror-postgres.py and commit the result.")
          print(f"{len(pinned)} pinned checksums match")
          PY

      - name: Assemble the release directory
        run: |
          set -euo pipefail
          mkdir -p release
          cp dist/*.whl dist/*.tar.gz release/
          cp dist/postgres/postgres-16-*.txz dist/postgres/SHA256SUMS release/
          cp install.ps1 install.sh release/ 2>/dev/null || echo "installers not present yet (part three)"
          version="${{ steps.version.outputs.version }}"
          wheel="$(cd release && ls orin-*.whl agentos-*.whl 2>/dev/null | head -n1)"
          printf '{"version": "%s", "wheel": "%s", "postgres_major": "16", "python": "3.13"}\n' "$version" "$wheel" > release/manifest.json
          cat release/manifest.json
          ls -la release

      - uses: actions/upload-artifact@v4
        with:
          name: release-${{ steps.version.outputs.version }}
          path: release/

      - name: Publish the release
        if: github.event_name == 'push'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh release create "${GITHUB_REF_NAME}" release/* --generate-notes --title "Orin ${GITHUB_REF_NAME}"

      - name: Publish a draft (manual run)
        if: github.event_name == 'workflow_dispatch'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh release create "${{ inputs.tag }}" release/* --draft --generate-notes --title "Orin ${{ inputs.tag }} (draft)"
```

Note the wheel name: the project is currently named `agentos` in `pyproject.toml`, so the wheel is `agentos-<version>-py3-none-any.whl`. The `manifest.json` step handles both names, and part three reads the name from the manifest rather than assuming it. **If the package is renamed to `orin`, that is a separate decision** — the public command is already `orin` regardless, and renaming the distribution changes the import path expectations of every existing checkout.

- [ ] **Step 2: Dry-run it before tagging anything**

```bash
git add .github/workflows/release.yml && git commit -m "ci: publish a release from a tag"
```

```bash
git push
```

```bash
git tag v0.1.0-rc1 && git push origin v0.1.0-rc1
```

Wait — the version check would fail, because `pyproject.toml` says `0.1.0` and the tag says `0.1.0-rc1`. That is the check working. Instead, dry-run without a tag:

```bash
gh workflow run release.yml -f tag=v0.1.0
```

```bash
gh run watch
```

Expected: every step green through `upload-artifact`, and a **draft** release created. The version check passes because `pyproject.toml` already reads `0.1.0`.

- [ ] **Step 3: Inspect the artifacts by hand**

```bash
gh run download --name release-0.1.0 --dir /tmp/orin-release
```

```bash
ls -la /tmp/orin-release && cat /tmp/orin-release/manifest.json && cat /tmp/orin-release/SHA256SUMS
```

Expected: the wheel, an sdist, five `postgres-16-*.txz` files of roughly 15–30 MB each, `SHA256SUMS` with five lines, and a `manifest.json` naming the wheel that is actually present.

- [ ] **Step 4: Install the built wheel into a clean environment and run it**

This is the proof that the wheel is a working installation and not just a well-formed zip.

```bash
uv venv /tmp/orin-wheel-test --python 3.13
```

```bash
uv pip install --python /tmp/orin-wheel-test /tmp/orin-release/*.whl
```

```bash
ORIN_HOME=/tmp/orin-wheel-home /tmp/orin-wheel-test/bin/orin --version
```

Expected: `orin 0.1.0 (installed)` — the profile must report **installed**, not development. If it reports development, the wheel was installed from a checkout in editable mode; redo it with the built file.

```bash
ORIN_HOME=/tmp/orin-wheel-home /tmp/orin-wheel-test/bin/orin status
```

Expected: `Orin is not running.`, `Profile   installed`, exit code 3.

On Windows use `uv venv C:\Temp\orin-wheel-test --python 3.13`, `C:\Temp\orin-wheel-test\Scripts\orin.exe`, and `$env:ORIN_HOME`.

- [ ] **Step 5: Delete the draft release**

```bash
gh release delete v0.1.0 --yes
```

The real one is created by pushing the tag, which happens in part three once the installers exist to consume it.

- [ ] **Step 6: Commit any fixes and push**

```bash
git add -A && git commit -m "ci: correct the release workflow after a dry run" && git push
```

Skip this step if the dry run needed no changes.

---

## Definition of done

- [ ] `ci.yml` runs green on `main`.
- [ ] A manual `release.yml` run produces a wheel containing `agentos/web/index.html`, the migration scripts, and the pinned checksum file.
- [ ] The five PostgreSQL assets are published and their digests match `src/agentos/services/postgres_checksums.json` — enforced by the workflow, not by inspection.
- [ ] `manifest.json` names the wheel that is actually in the release.
- [ ] The built wheel installs into an empty environment and `orin --version` reports the `installed` profile.
