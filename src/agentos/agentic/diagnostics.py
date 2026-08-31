"""Project detection and canonical verification commands.

A weak model does not get code right by reasoning about it harder; it gets
code right by reacting to something mechanical -- a compiler, a linter, a
failed test. This module answers the question that has to be answered before
any of that can happen: what *is* this project, and what commands would a
competent engineer actually run against it? ``verify_project`` (in
``agent_tools``) executes the recipe this module derives; nothing here runs a
process itself, which is what keeps it trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib


# Canonical order: cheap static checks before anything that has to actually
# run the program. A typecheck failure is worth seeing before spending time on
# a build that would fail for the same reason.
STEP_ORDER = ("install", "typecheck", "lint", "build", "test")


@dataclass(frozen=True, slots=True)
class ProjectRecipe:
    kind: str
    manifest: str
    commands: dict[str, str]

    def command_for(self, step: str) -> str | None:
        return self.commands.get(step)

    def available_steps(self) -> tuple[str, ...]:
        return tuple(step for step in STEP_ORDER if step in self.commands)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _package_manager_for(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    return "npm"


# npm's placeholder for a project that was never given a real test script.
# Running it produces a failing "no test specified" result that would read to
# the model as a genuine test failure, so it is treated as absent instead.
_NPM_TEST_PLACEHOLDER = 'echo "Error: no test specified" && exit 1'


def _node_recipe(manifest: Path) -> ProjectRecipe:
    package_manager = _package_manager_for(manifest.parent)
    data = _read_json(manifest)
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    commands: dict[str, str] = {"install": f"{package_manager} install"}

    def use_script(step: str, *script_names: str) -> None:
        for name in script_names:
            value = scripts.get(name)
            if isinstance(value, str) and value.strip() and value.strip() != _NPM_TEST_PLACEHOLDER:
                commands[step] = f"{package_manager} run {name}"
                return

    use_script("build", "build")
    use_script("test", "test")
    use_script("lint", "lint")
    use_script("dev", "dev", "start")
    if "typecheck" in scripts or (manifest.parent / "tsconfig.json").is_file():
        use_script("typecheck", "typecheck", "type-check")
        commands.setdefault("typecheck", "npx --no-install tsc --noEmit")
    return ProjectRecipe(kind="node", manifest="package.json", commands=commands)


def _has_tool_config(pyproject: dict, table: str, *config_files: Path) -> bool:
    tool = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
    if table in tool:
        return True
    return any(path.is_file() for path in config_files)


def _python_recipe(root: Path) -> ProjectRecipe:
    pyproject_path = root / "pyproject.toml"
    pyproject = _read_toml(pyproject_path) if pyproject_path.is_file() else {}
    commands: dict[str, str] = {}
    if pyproject_path.is_file() and "project" in pyproject:
        commands["install"] = "pip install -e ."
    elif (root / "requirements.txt").is_file():
        commands["install"] = "pip install -r requirements.txt"
    if _has_tool_config(pyproject, "ruff", root / "ruff.toml", root / ".ruff.toml"):
        commands["lint"] = "python -m ruff check ."
    if _has_tool_config(pyproject, "mypy", root / "mypy.ini") or (root / "pyrightconfig.json").is_file():
        commands["typecheck"] = "python -m mypy ." if _has_tool_config(pyproject, "mypy", root / "mypy.ini") else "python -m pyright"
    if _has_tool_config(pyproject, "pytest", root / "pytest.ini") or (root / "tests").is_dir():
        commands["test"] = "python -m pytest"
    manifest = "pyproject.toml" if pyproject_path.is_file() else "requirements.txt"
    return ProjectRecipe(kind="python", manifest=manifest, commands=commands)


def detect_recipe(root: Path) -> ProjectRecipe | None:
    """The build/test recipe for the project at ``root``, or None.

    Only the workspace root is inspected. A monorepo with per-package
    manifests is a deliberately unhandled case: guessing which package the
    person means would be worse than the agent asking or being told.
    """
    if (root / "package.json").is_file():
        return _node_recipe(root / "package.json")
    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file():
        return _python_recipe(root)
    if (root / "go.mod").is_file():
        return ProjectRecipe(kind="go", manifest="go.mod", commands={
            "install": "go mod download", "build": "go build ./...", "test": "go test ./...",
        })
    if (root / "Cargo.toml").is_file():
        return ProjectRecipe(kind="rust", manifest="Cargo.toml", commands={
            "build": "cargo build", "test": "cargo test", "lint": "cargo clippy",
        })
    return None


# Single-file checks run automatically after a write/edit, kept intentionally
# narrow: fast enough to run on every save, and only for a stack whose config
# is already visible so a bare-metal script directory does not get commands
# it never asked for.
def file_diagnostic_command(path: str, *, project_root: Path) -> str | None:
    lowered = path.lower()
    if lowered.endswith(".py"):
        return f'python -m ruff check "{path}"'
    if lowered.endswith((".ts", ".tsx", ".js", ".jsx")) and (project_root / "package.json").is_file():
        return f'npx --no-install eslint "{path}"'
    return None


__all__ = ["ProjectRecipe", "STEP_ORDER", "detect_recipe", "file_diagnostic_command"]
