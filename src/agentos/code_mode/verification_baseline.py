from __future__ import annotations

import re
from typing import Any, Mapping

from agentos.agentic.tools._shared import is_verification_command

_PYTEST_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_TSC_RE = re.compile(r"(\S+\(\d+,\d+\)): error (TS\d+)")
_RUFF_RE = re.compile(r"^(\S+:\d+:\d+): ([A-Z]+\d+)", re.MULTILINE)
_GO_RE = re.compile(r"^--- FAIL: (\S+)", re.MULTILINE)
_CARGO_RE = re.compile(r"^test (\S+) \.\.\. FAILED", re.MULTILINE)


def failure_fingerprint(output: str, *, exit_code: int | None = None) -> frozenset[str]:
    """Extract failure identifiers from output or fall back to exit code."""
    text = str(output or "")
    identifiers: set[str] = set()

    for match in _PYTEST_RE.finditer(text):
        identifiers.add(match.group(1))

    for match in _TSC_RE.finditer(text):
        identifiers.add(f"{match.group(1)}: error {match.group(2)}")

    for match in _RUFF_RE.finditer(text):
        identifiers.add(f"{match.group(1)}: {match.group(2)}")

    for match in _GO_RE.finditer(text):
        identifiers.add(match.group(1))

    for match in _CARGO_RE.finditer(text):
        identifiers.add(match.group(1))

    if not identifiers:
        return frozenset({f"exit:{exit_code}"})
    return frozenset(identifiers)


class VerificationBaseline:
    """Tracks baseline verification results per key within a turn."""

    def __init__(self) -> None:
        self._references: dict[str, frozenset[str]] = {}

    def observe(
        self,
        key: str,
        *,
        failed: bool,
        fingerprint: frozenset[str],
        after_edit: bool,
    ) -> bool:
        """Return True if this observation represents a new failure that should enter 'fixing'."""
        if key not in self._references:
            if not after_edit:
                self._references[key] = frozenset(fingerprint) if failed else frozenset()
                return False
            # When observed after an edit without a pre-edit reference, failure enters fixing
            return bool(failed)

        if not failed:
            return False

        ref = self._references[key]
        return bool(fingerprint - ref)


def verification_observations(
    name: str,
    payload: Mapping[str, object] | None,
    content: str,
    arguments: Mapping[str, object] | None,
) -> list[tuple[str, bool, frozenset[str]]]:
    """Return a list of (key, failed, fingerprint) verification observations."""
    safe_payload = payload or {}
    safe_args = arguments or {}

    if name == "verify_project":
        if safe_payload.get("scaffold"):
            return []
        raw_steps = safe_payload.get("steps")
        if not isinstance(raw_steps, list):
            return []
        observations: list[tuple[str, bool, frozenset[str]]] = []
        for item in raw_steps:
            if isinstance(item, Mapping) and item.get("ran"):
                step_name = str(item.get("step") or "")
                key = f"verify:{step_name}"
                failed = not bool(item.get("passed"))
                step_output = str(item.get("output") or "")
                fp = failure_fingerprint(step_output)
                observations.append((key, failed, fp))
        return observations

    if name in {"run_command", "powershell"}:
        cmd = str(safe_payload.get("command") or safe_args.get("command") or "").strip()
        if not is_verification_command(cmd):
            return []
        norm_cmd = " ".join(cmd.split())
        key = f"cmd:{norm_cmd}"
        status = str(safe_payload.get("status") or "succeeded")
        failed = status != "succeeded" or bool(safe_payload.get("failed"))

        exit_code: int | None = None
        raw_exit = safe_payload.get("exit_code")
        if raw_exit is not None and not isinstance(raw_exit, bool):
            try:
                exit_code = int(raw_exit)  # type: ignore[arg-type]
            except (ValueError, TypeError):
                exit_code = None

        fp = failure_fingerprint(content, exit_code=exit_code)
        return [(key, failed, fp)]

    return []
