from __future__ import annotations

from pathlib import Path

from agentos.agentic.diagnostics import detect_recipe, file_diagnostic_command


def test_detects_a_node_project_and_maps_its_scripts(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts": {"build": "vite build", "test": "vitest run", "lint": "eslint ."}}',
        encoding="utf-8",
    )

    recipe = detect_recipe(tmp_path)

    assert recipe is not None
    assert recipe.kind == "node"
    assert recipe.command_for("install") == "npm install"
    assert recipe.command_for("build") == "npm run build"
    assert recipe.command_for("test") == "npm run test"
    assert recipe.command_for("lint") == "npm run lint"


def test_prefers_pnpm_when_its_lockfile_is_present(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {}}', encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")

    recipe = detect_recipe(tmp_path)

    assert recipe.command_for("install") == "pnpm install"


def test_a_node_project_with_a_tsconfig_gets_a_typecheck_step_even_without_a_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {}}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    recipe = detect_recipe(tmp_path)

    assert recipe.command_for("typecheck") == "npx --no-install tsc --noEmit"


def test_the_default_npm_test_placeholder_does_not_count_as_a_real_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}',
        encoding="utf-8",
    )

    recipe = detect_recipe(tmp_path)

    assert recipe.command_for("test") is None


def test_detects_a_python_project_from_pyproject_with_matching_tool_configs(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"demo\"\n\n[tool.ruff]\nline-length = 100\n\n[tool.pytest.ini_options]\n",
        encoding="utf-8",
    )

    recipe = detect_recipe(tmp_path)

    assert recipe is not None
    assert recipe.kind == "python"
    assert recipe.command_for("install") == "pip install -e ."
    assert recipe.command_for("lint") == "python -m ruff check ."
    assert recipe.command_for("test") == "python -m pytest"
    assert recipe.command_for("typecheck") is None


def test_a_python_project_without_any_tool_config_only_gets_install(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")

    recipe = detect_recipe(tmp_path)

    assert recipe.command_for("install") == "pip install -r requirements.txt"
    assert recipe.command_for("lint") is None
    assert recipe.command_for("test") is None


def test_a_tests_directory_is_enough_to_infer_pytest_even_without_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()

    recipe = detect_recipe(tmp_path)

    assert recipe.command_for("test") == "python -m pytest"


def test_an_unrecognized_directory_has_no_recipe(tmp_path: Path) -> None:
    assert detect_recipe(tmp_path) is None


def test_file_diagnostic_picks_ruff_for_python_files(tmp_path: Path) -> None:
    assert file_diagnostic_command("src/app.py", project_root=tmp_path) == 'python -m ruff check "src/app.py"'


def test_file_diagnostic_only_lints_typescript_when_a_node_project_is_present(tmp_path: Path) -> None:
    assert file_diagnostic_command("src/App.tsx", project_root=tmp_path) is None

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    assert file_diagnostic_command("src/App.tsx", project_root=tmp_path) == 'npx --no-install eslint "src/App.tsx"'


def test_file_diagnostic_has_nothing_to_say_about_an_unrecognized_extension(tmp_path: Path) -> None:
    assert file_diagnostic_command("README.md", project_root=tmp_path) is None
