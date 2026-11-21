"""Registered setting keys for independent verifier change thresholds (WS-A3)."""

from __future__ import annotations

from .registry import Scope, SettingKey, register

DEFAULT_THRESHOLD_FILES: int = 3
DEFAULT_THRESHOLD_LINES: int = 150

DEFAULT_SENSITIVE_PATHS: tuple[str, ...] = (
    "**/migrations/**",
    "**/alembic/**",
    "**/*.sql",
    "**/auth/**",
    "**/security/**",
    "**/permissions/**",
    ".github/workflows/**",
    "**/Dockerfile*",
    "**/.env*",
)

DEFAULT_IGNORE_GLOBS: tuple[str, ...] = (
    "**/*.lock",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/dist/**",
    "**/build/**",
    "**/*.min.*",
    ".orin/**",
)

VERIFIER_THRESHOLD_KEYS: tuple[SettingKey, ...] = (
    SettingKey(
        path="orchestration.verifier.threshold.files",
        type="number",
        default=DEFAULT_THRESHOLD_FILES,
        scope=Scope.ANY,
        group="verifier",
        label="Limiar de arquivos alterados",
        description="Quantidade mínima de arquivos alterados no turno para acionar automaticamente o verificador independente.",
        minimum=1,
        maximum=1000,
    ),
    SettingKey(
        path="orchestration.verifier.threshold.lines",
        type="number",
        default=DEFAULT_THRESHOLD_LINES,
        scope=Scope.ANY,
        group="verifier",
        label="Limiar de linhas alteradas",
        description="Quantidade mínima de linhas adicionadas e removidas no turno para acionar automaticamente o verificador independente.",
        minimum=1,
        maximum=100000,
    ),
    SettingKey(
        path="orchestration.verifier.threshold.sensitivePaths",
        type="string_list",
        default=list(DEFAULT_SENSITIVE_PATHS),
        scope=Scope.ANY,
        group="verifier",
        label="Caminhos sensíveis",
        description="Padrões glob de caminhos que acionam o verificador independente ao serem alterados (pode ficar vazia para desativar).",
    ),
    SettingKey(
        path="orchestration.verifier.threshold.ignore",
        type="string_list",
        default=list(DEFAULT_IGNORE_GLOBS),
        scope=Scope.ANY,
        group="verifier",
        label="Arquivos ignorados na contagem",
        description="Padrões glob de arquivos excluídos da contagem de arquivos, linhas e caminhos sensíveis do limiar.",
    ),
)

for _key in VERIFIER_THRESHOLD_KEYS:
    register(_key)

__all__ = [
    "DEFAULT_IGNORE_GLOBS",
    "DEFAULT_SENSITIVE_PATHS",
    "DEFAULT_THRESHOLD_FILES",
    "DEFAULT_THRESHOLD_LINES",
    "VERIFIER_THRESHOLD_KEYS",
]
