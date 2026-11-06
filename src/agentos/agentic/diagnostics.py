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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tomllib
from typing import Literal


# Canonical order: cheap static checks before anything that has to actually
# run the program. A typecheck failure is worth seeing before spending time on
# a build that would fail for the same reason.
STEP_ORDER = ("install", "typecheck", "lint", "build", "test")


@dataclass(frozen=True, slots=True)
class ProjectRecipe:
    kind: str
    manifest: str
    commands: dict[str, str]
    opt_in_steps: frozenset[str] = frozenset()

    def command_for(self, step: str) -> str | None:
        return self.commands.get(step)

    def available_steps(self) -> tuple[str, ...]:
        return tuple(step for step in STEP_ORDER if step in self.commands and step not in self.opt_in_steps)


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


def python_runner(root: Path) -> str:
    if (root / "uv.lock").is_file():
        return "uv run python"
    if (root / "poetry.lock").is_file():
        return "poetry run python"
    sub = Path("Scripts", "python.exe") if os.name == "nt" else Path("bin", "python")
    for venv_name in (".venv", "venv"):
        rel = Path(venv_name) / sub
        if (root / rel).is_file():
            return f'"{rel}"'
    return "python"


def _python_recipe(root: Path) -> ProjectRecipe:
    pyproject_path = root / "pyproject.toml"
    pyproject = _read_toml(pyproject_path) if pyproject_path.is_file() else {}
    runner = python_runner(root)
    commands: dict[str, str] = {}
    if (root / "uv.lock").is_file():
        commands["install"] = "uv sync"
    elif (root / "poetry.lock").is_file():
        commands["install"] = "poetry install"
    elif runner != "python":
        if pyproject_path.is_file() and "project" in pyproject:
            commands["install"] = f"{runner} -m pip install -e ."
        elif (root / "requirements.txt").is_file():
            commands["install"] = f"{runner} -m pip install -r requirements.txt"
    else:
        if pyproject_path.is_file() and "project" in pyproject:
            commands["install"] = "pip install -e ."
        elif (root / "requirements.txt").is_file():
            commands["install"] = "pip install -r requirements.txt"
    if _has_tool_config(pyproject, "ruff", root / "ruff.toml", root / ".ruff.toml"):
        commands["lint"] = f"{runner} -m ruff check ."
    if _has_tool_config(pyproject, "mypy", root / "mypy.ini") or (root / "pyrightconfig.json").is_file():
        commands["typecheck"] = (
            f"{runner} -m mypy ."
            if _has_tool_config(pyproject, "mypy", root / "mypy.ini")
            else f"{runner} -m pyright"
        )
    if _has_tool_config(pyproject, "pytest", root / "pytest.ini") or (root / "tests").is_dir():
        commands["test"] = f"{runner} -m pytest"
    manifest = "pyproject.toml" if pyproject_path.is_file() else "requirements.txt"
    return ProjectRecipe(kind="python", manifest=manifest, commands=commands, opt_in_steps=frozenset({"install"}))


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


@dataclass(frozen=True, slots=True)
class FileDiagnosticCommand:
    command: str
    format: Literal["ruff_json", "eslint_json"]
    argv: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True, slots=True)
class FileDiagnostic:
    line: int
    column: int
    code: str
    message: str


# Single-file checks run automatically after a write/edit, kept intentionally
# narrow: fast enough to run on every save, and only for a stack whose config
# is already visible so a bare-metal script directory does not get commands
# it never asked for.
def file_diagnostic_command(path: str, *, project_root: Path) -> FileDiagnosticCommand | None:
    lowered = path.lower()
    if lowered.endswith(".py"):
        return FileDiagnosticCommand(
            command=f'python -m ruff check --output-format=json "{path}"',
            format="ruff_json",
            argv=("python", "-m", "ruff", "check", "--output-format=json", path),
        )
    if (
        lowered.endswith((".ts", ".tsx", ".js", ".jsx"))
        and (project_root / "package.json").is_file()
        and (
            (project_root / "node_modules" / ".bin" / "eslint").exists()
            or (project_root / "node_modules" / ".bin" / "eslint.cmd").exists()
        )
    ):
        return FileDiagnosticCommand(
            command=f'npx --no-install eslint --format json "{path}"',
            format="eslint_json",
            argv=("npx", "--no-install", "eslint", "--format", "json", path),
        )
    return None


def parse_file_diagnostics(fmt: str, stdout: str) -> list[FileDiagnostic] | None:
    if not isinstance(stdout, str) or not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    out: list[FileDiagnostic] = []
    if fmt == "ruff_json":
        for item in data:
            if not isinstance(item, Mapping):
                return None
            loc = item.get("location")
            if loc is None and "location" in item:
                continue
            if not isinstance(loc, Mapping):
                return None
            try:
                line = int(loc["row"])
                column = int(loc.get("column", 1))
            except (KeyError, TypeError, ValueError):
                return None
            code = str(item.get("code") or "")
            message = str(item.get("message") or "")
            out.append(FileDiagnostic(line=line, column=column, code=code, message=message))
        return out
    if fmt == "eslint_json":
        for file_item in data:
            if not isinstance(file_item, Mapping):
                return None
            messages = file_item.get("messages")
            if not isinstance(messages, list):
                return None
            for msg in messages:
                if not isinstance(msg, Mapping):
                    return None
                try:
                    line = int(msg.get("line") or 1)
                    column = int(msg.get("column") or 1)
                except (TypeError, ValueError):
                    return None
                code = str(msg.get("ruleId") or "")
                message = str(msg.get("message") or "")
                out.append(FileDiagnostic(line=line, column=column, code=code, message=message))
        return out
    return None


def changed_line_ranges(payload: Mapping[str, object] | None) -> list[tuple[int, int]] | None:
    if not isinstance(payload, Mapping):
        return None
    if "original_sha256" in payload and not payload.get("original_sha256"):
        return None
    patch = payload.get("structured_patch")
    if not isinstance(patch, list) or not patch:
        return None
    ranges: list[tuple[int, int]] = []
    for hunk in patch:
        if not isinstance(hunk, Mapping):
            continue
        try:
            start = int(hunk["newStart"])
            count = int(hunk.get("newLines", 1))
        except (KeyError, TypeError, ValueError):
            continue
        hunk_lines = hunk.get("lines")
        if isinstance(hunk_lines, list) and hunk_lines and all(isinstance(line, str) for line in hunk_lines):
            cursor = start
            max_line = max(start, start + count - 1)
            changed_lines: list[int] = []
            for raw_line in hunk_lines:
                if raw_line.startswith("+"):
                    changed_lines.append(cursor)
                    cursor += 1
                elif raw_line.startswith("-"):
                    changed_lines.append(min(max(start, cursor), max_line))
                elif raw_line.startswith(" "):
                    cursor += 1
            if changed_lines:
                ordered = sorted(set(changed_lines))
                seg_start = ordered[0]
                seg_end = ordered[0]
                for ln in ordered[1:]:
                    if ln == seg_end + 1:
                        seg_end = ln
                    else:
                        ranges.append((seg_start, seg_end))
                        seg_start = seg_end = ln
                ranges.append((seg_start, seg_end))
                continue
        end = max(start, start + count - 1)
        ranges.append((start, end))
    return ranges if ranges else None


def summarize_diagnostics(
    diagnostics: Sequence[FileDiagnostic],
    ranges: Sequence[tuple[int, int]] | None,
    *,
    margin: int = 2,
    max_items: int = 5,
) -> str | None:
    if not diagnostics:
        return None
    if ranges is None:
        in_range = list(diagnostics)
        fora = 0
    else:
        in_range = [
            d
            for d in diagnostics
            if any((start - margin) <= d.line <= (end + margin) for start, end in ranges)
        ]
        fora = len(diagnostics) - len(in_range)
    if not in_range:
        return None
    k = len(in_range)
    header = f"[lint — {k} problema(s) nas linhas alteradas{', +' + str(fora) + ' fora delas' if fora else ''}]"
    lines = [f"{d.line}:{d.column} {d.code} {d.message}".strip() for d in in_range[:max_items]]
    summary = "\n".join([header, *lines])
    return summary[:600]


__all__ = [
    "FileDiagnostic",
    "FileDiagnosticCommand",
    "ProjectRecipe",
    "STEP_ORDER",
    "changed_line_ranges",
    "detect_recipe",
    "file_diagnostic_command",
    "parse_file_diagnostics",
    "python_runner",
    "summarize_diagnostics",
]
