"""Guarda de empacotamento: imports dinâmicos de agentos precisam entrar no bundle.

O PyInstaller não enxerga ``importlib.import_module("agentos....")``. Na 0.13.0 o
scheduler congelado caiu com ModuleNotFoundError em
``agentos.persistence.postgres.memory_promotion`` por causa disso.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "orin.spec"
SRC = ROOT / "src" / "agentos"


def test_orin_spec_collects_all_agentos_submodules() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    assert re.search(r'collect_submodules\(\s*["\']agentos["\']', spec), (
        "orin.spec precisa de *collect_submodules('agentos') em hiddenimports: "
        "módulos carregados via importlib.import_module não são detectados pelo PyInstaller"
    )


def test_dynamic_agentos_imports_point_to_existing_modules() -> None:
    pattern = re.compile(r'import_module\(\s*["\'](agentos\.[\w.]+)["\']\s*\)')
    found = []
    for path in SRC.rglob("*.py"):
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            found.append(name)
            rel = Path(*name.split("."))
            assert (ROOT / "src" / rel).with_suffix(".py").is_file() or (
                ROOT / "src" / rel / "__init__.py"
            ).is_file(), f"import dinâmico aponta para módulo inexistente: {name}"
    assert "agentos.persistence.postgres.memory_promotion" in found
