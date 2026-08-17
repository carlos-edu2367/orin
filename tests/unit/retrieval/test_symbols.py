from __future__ import annotations

from agentos.retrieval.symbols import extract_imports, language_for, resolve_import


def test_language_is_detected_from_the_extension() -> None:
    assert language_for("src/a.py") == "python"
    assert language_for("web/app.tsx") == "typescript"
    assert language_for("web/app.js") == "javascript"
    assert language_for("README.md") == "markdown"
    assert language_for("data/blob.bin") is None


def test_python_imports_are_extracted_in_both_forms() -> None:
    text = "import os\nfrom agentos.retrieval import store\nfrom . import sibling\n"

    assert extract_imports("python", text) == ("os", "agentos.retrieval", ".")


def test_javascript_imports_cover_esm_require_and_reexport() -> None:
    text = (
        "import { a } from './store';\n"
        "const b = require('../filters');\n"
        "export { c } from 'httpx';\n"
    )

    assert extract_imports("javascript", text) == ("./store", "../filters", "httpx")


def test_python_import_resolves_against_an_indexed_path() -> None:
    known = ("src/agentos/retrieval/store.py", "src/agentos/agentic/session.py")

    assert resolve_import("python", "agentos.retrieval.store", "src/agentos/retrieval/indexer.py", known) == "src/agentos/retrieval/store.py"
    assert resolve_import("python", "os", "src/agentos/retrieval/indexer.py", known) is None


def test_relative_javascript_import_resolves_against_the_importer_directory() -> None:
    known = ("web/src/store.ts", "web/src/app.tsx")

    assert resolve_import("typescript", "./store", "web/src/app.tsx", known) == "web/src/store.ts"
    assert resolve_import("typescript", "./missing", "web/src/app.tsx", known) is None
