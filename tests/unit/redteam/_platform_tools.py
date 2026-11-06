"""Cross-platform deferred mutation tool helper for Phase 3 red-team tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agentos.agentic.tool_deferral import ALWAYS_LOADED_CODE_TOOLS, CODE_MODE_CORE_TOOLS
from agentos.agentic.tools.public_names import public_name

_CANDIDATE_DEFERRED = CODE_MODE_CORE_TOOLS - ALWAYS_LOADED_CODE_TOOLS
assert "notebook_edit" in _CANDIDATE_DEFERRED, "notebook_edit must remain a deferred core tool"

DEFERRED_MUTATION_TOOL: str = "powershell" if os.name == "nt" else "notebook_edit"
DEFERRED_MUTATION_PUBLIC_NAME: str = public_name(DEFERRED_MUTATION_TOOL)


def deferred_mutation_arguments(
    workspace_root: Path | None = None,
    *,
    tool_name: str = DEFERRED_MUTATION_TOOL,
) -> dict[str, Any]:
    """Return valid invocation arguments for ``tool_name`` (defaults to ``DEFERRED_MUTATION_TOOL``)."""
    if tool_name == "powershell":
        return {"command": "echo test"}
    if workspace_root is not None:
        nb_path = workspace_root / "demo.ipynb"
        if not nb_path.exists():
            nb_path.write_text(
                json.dumps(
                    {
                        "cells": [
                            {
                                "cell_type": "code",
                                "id": "cell-0",
                                "metadata": {},
                                "outputs": [],
                                "execution_count": None,
                                "source": ["x = 1\n"],
                            }
                        ],
                        "metadata": {},
                        "nbformat": 4,
                        "nbformat_minor": 5,
                    }
                ),
                encoding="utf-8",
            )
    return {
        "notebook_path": "demo.ipynb",
        "new_source": "x = 2\n",
        "cell_id": "cell-0",
        "edit_mode": "replace",
    }
