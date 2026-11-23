"""Stable error and warning codes of the pipeline loader and validator.

Messages shown to the user are in pt-BR (the format is part of the contract:
``linha N, coluna M: <mensagem> (código E_...)``); codes and names stay in English.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ERROR_CODES: tuple[str, ...] = (
    "E_YAML_SYNTAX",
    "E_YAML_FORBIDDEN",
    "E_TOO_LARGE",
    "E_VERSION",
    "E_ID",
    "E_UNKNOWN_FIELD",
    "E_TYPE",
    "E_REQUIRED",
    "E_STEP_ID_DUP",
    "E_STEP_KIND",
    "E_REF_UNKNOWN",
    "E_REF_FORWARD",
    "E_REF_TAINT",
    "E_EXPR_SYNTAX",
    "E_LOOP_NO_MAX",
    "E_MAP_RANGE",
    "E_AGENT_UNKNOWN",
    "E_MODEL_UNKNOWN",
    "E_ISOLATION_DISABLED",
    "E_ISOLATION_UNAVAILABLE",
    "E_SCHEMA_INVALID",
    "E_BUDGET",
    "E_RETRY",
    "E_INPUT",
    "E_DISABLED",
)

WARNING_CODES: tuple[str, ...] = (
    "W_BUDGET_FANOUT",
    "W_BUDGET_DEFAULT",
    "W_UNREACHABLE_STEP",
    "W_EFFORT_ADJUSTED",
)


@dataclass(frozen=True, slots=True)
class PipelineError:
    """One problem found in a pipeline file, pointing at the line when known."""

    code: str
    message: str
    line: int | None = None
    column: int | None = None
    path: str = ""

    def format(self) -> str:
        if self.line is not None:
            where = f"linha {self.line}"
            if self.column is not None:
                where += f", coluna {self.column}"
            return f"{where}: {self.message} (código {self.code})"
        return f"{self.message} (código {self.code})"

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "path": self.path,
            "text": self.format(),
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    errors: tuple[PipelineError, ...] = ()
    warnings: tuple[PipelineError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def merged(self, other: "ValidationReport") -> "ValidationReport":
        return ValidationReport(self.errors + other.errors, self.warnings + other.warnings)

    def codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.errors)

    def warning_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.warnings)


class PipelineLoadError(Exception):
    """Raised by the YAML loader; carries the structured error."""

    def __init__(self, error: PipelineError) -> None:
        super().__init__(error.format())
        self.error = error


@dataclass(slots=True)
class ReportBuilder:
    """Mutable collector used while validating; ``build`` freezes it."""

    errors: list[PipelineError] = field(default_factory=list)
    warnings: list[PipelineError] = field(default_factory=list)

    def error(self, code: str, message: str, where: tuple[int | None, int | None] = (None, None), path: str = "") -> None:
        self.errors.append(PipelineError(code, message, where[0], where[1], path))

    def warn(self, code: str, message: str, where: tuple[int | None, int | None] = (None, None), path: str = "") -> None:
        self.warnings.append(PipelineError(code, message, where[0], where[1], path))

    def build(self) -> ValidationReport:
        return ValidationReport(tuple(self.errors), tuple(self.warnings))


__all__ = [
    "ERROR_CODES",
    "WARNING_CODES",
    "PipelineError",
    "PipelineLoadError",
    "ReportBuilder",
    "ValidationReport",
]
