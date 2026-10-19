"""Notebook tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any, Mapping, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.files import ORIGINAL_FILE_LIMIT
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class NotebookTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def notebook_edit(
        self,
        notebook_path: str,
        new_source: str = "",
        cell_id: str | None = None,
        cell_type: str | None = None,
        edit_mode: str = "replace",
    ) -> dict[str, Any]:
        target = self.workspace.resolve(notebook_path)
        if target.suffix.lower() != ".ipynb":
            raise AgentToolError(f"'{notebook_path}' is not a Jupyter notebook (.ipynb).")
        if not target.exists() or not target.is_file():
            raise AgentToolError(f"Notebook not found: '{notebook_path}'.")
        rel = self.workspace.key(target)
        try:
            raw_text = target.read_text(encoding="utf-8")
            nb = json.loads(raw_text)
        except Exception as error:
            raise AgentToolError(f"Invalid notebook JSON in '{notebook_path}': {error}") from error
        cells = nb.get("cells")
        if not isinstance(cells, list):
            raise AgentToolError(f"Invalid notebook structure in '{notebook_path}': missing 'cells' array.")
        mode = str(edit_mode or "replace").strip().lower()
        if mode not in {"replace", "insert", "delete"}:
            raise AgentToolError(f"Invalid edit_mode '{edit_mode}'. Must be 'replace', 'insert', or 'delete'.")

        def _resolve_cell_idx(cid: str | None) -> int:
            if cid is None or not str(cid).strip():
                if not cells:
                    raise AgentToolError("Notebook has no cells.")
                return 0
            cid_str = str(cid).strip()
            for index, item in enumerate(cells):
                if isinstance(item, Mapping) and str(item.get("id") or "") == cid_str:
                    return index
            if cid_str.isdigit():
                idx = int(cid_str)
                if 0 <= idx < len(cells):
                    return idx
            raise AgentToolError(f"Cell not found: '{cid_str}'.")

        def _format_source_lines(src: str) -> list[str]:
            if not src:
                return []
            return src.splitlines(keepends=True)

        old_source: str | None = None
        kind = "code"
        target_cell_id = ""
        if mode == "replace":
            idx = _resolve_cell_idx(cell_id)
            cell = cells[idx]
            if not isinstance(cell, dict):
                raise AgentToolError(f"Cell at index {idx} is invalid.")
            old_source = _source_text(cell)
            if cell_type is not None:
                clean_type = str(cell_type).strip().lower()
                if clean_type not in {"code", "markdown"}:
                    raise AgentToolError("cell_type must be 'code' or 'markdown'.")
                cell["cell_type"] = clean_type
            kind = str(cell.get("cell_type") or "code")
            cell["source"] = _format_source_lines(new_source)
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
            target_cell_id = str(cell.get("id") or idx)
        elif mode == "insert":
            eff_type = str(cell_type or "code").strip().lower()
            if eff_type not in {"code", "markdown"}:
                raise AgentToolError("cell_type must be 'code' or 'markdown'.")
            kind = eff_type
            new_id = f"cell-{uuid4().hex[:8]}"
            new_cell: dict[str, Any] = {
                "cell_type": eff_type,
                "id": new_id,
                "metadata": {},
                "source": _format_source_lines(new_source),
            }
            if eff_type == "code":
                new_cell["execution_count"] = None
                new_cell["outputs"] = []
            if cell_id is None or not str(cell_id).strip():
                insert_idx = 0
            else:
                insert_idx = _resolve_cell_idx(cell_id) + 1
            cells.insert(insert_idx, new_cell)
            target_cell_id = new_id
        else:  # delete
            idx = _resolve_cell_idx(cell_id)
            removed = cells.pop(idx)
            old_source = _source_text(removed)
            kind = str(removed.get("cell_type") or "code") if isinstance(removed, Mapping) else "code"
            target_cell_id = str(removed.get("id") if isinstance(removed, Mapping) else idx) or str(idx)

        new_text = json.dumps(nb, ensure_ascii=False, indent=1) + "\n"
        with self._recorded_write(notebook_path):
            written = self.workspace.write_text(notebook_path, new_text)
        self._record_file_read(
            notebook_path,
            offset=1,
            limit=400,
            total_lines=len(new_text.splitlines()),
            explicit_read=False,
        )
        payload: dict[str, Any] = {
            "ok": True,
            "path": rel,
            "edit_mode": mode,
            "cell_id": target_cell_id,
            "cell_count": len(cells),
            "bytes_written": written,
            "label": rel,
        }
        return {
            "ok": True,
            "path": rel,
            "edit_mode": mode,
            "cell_id": target_cell_id,
            "cell_count": len(cells),
            "summary": f"Notebook {mode} em {rel} (célula {target_cell_id})",
            "content": f"Notebook '{rel}' updated ({mode} cell '{target_cell_id}', total cells={len(cells)}).",
            "payload": payload,
            "tool_output": {
                "new_source": new_source,
                "old_source": old_source,
                "cell_id": target_cell_id or None,
                "cell_type": kind if kind in {"code", "markdown"} else "code",
                "language": _notebook_language(nb),
                "edit_mode": mode,
                "notebook_path": str(target),
                "original_file": raw_text if len(raw_text) <= ORIGINAL_FILE_LIMIT else None,
                "updated_file": new_text if len(new_text) <= ORIGINAL_FILE_LIMIT else None,
            },
        }


class NotebookEditInput(ToolInput):
    notebook_path: str = Field(description="Path of the .ipynb file.")
    new_source: str = Field(description="New cell source; empty for delete.")
    cell_id: str | None = Field(None, description="Cell id, or 0-based index as a string.")
    cell_type: Literal["code", "markdown"] | None = None
    edit_mode: Literal["replace", "insert", "delete"] | None = None


class NotebookEditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_source: str
    old_source: str | None = None
    cell_id: str | None = None
    cell_type: Literal["code", "markdown"]
    language: str
    edit_mode: Literal["replace", "insert", "delete"]
    notebook_path: str
    original_file: str | None
    updated_file: str | None


def _source_text(cell: Any) -> str:
    raw = cell.get("source") if isinstance(cell, Mapping) else None
    return "".join(str(part) for part in raw) if isinstance(raw, list) else str(raw or "")


def _notebook_language(nb: Mapping[str, Any]) -> str:
    """The notebook's language from its metadata; malformed metadata falls back to Python instead of failing a saved edit."""
    metadata = nb.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    info = metadata.get("language_info")
    kernel = metadata.get("kernelspec")
    candidates = (
        info.get("name") if isinstance(info, Mapping) else None,
        kernel.get("language") if isinstance(kernel, Mapping) else None,
    )
    return next((str(item) for item in candidates if item), "python")


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="notebook_edit",
        description="Edit a Jupyter notebook (.ipynb) cell by cell_id or 0-based index (replace, insert, or delete).",
        legacy_schema=_schema({
            "notebook_path": {**_TEXT, "description": "Workspace-relative path to the .ipynb file."},
            "new_source": {**_TEXT, "description": "New source code or markdown for the cell (required for replace and insert)."},
            "cell_id": {**_TEXT, "description": "Target cell id or 0-based cell index string (e.g. '0')."},
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown"],
                "description": "Cell type ('code' or 'markdown'). Required when inserting a new cell.",
            },
            "edit_mode": {
                "type": "string",
                "enum": ["replace", "insert", "delete"],
                "description": "Operation mode: 'replace' (default), 'insert', or 'delete'.",
            },
        }, ("notebook_path",)),
        handler="notebook_edit",
        kind="filesystem",
        input_model=NotebookEditInput,
        output_model=NotebookEditOutput,
        public_description='Replace, insert or delete one cell of a Jupyter notebook.',
        policy_tags=("mutates",),
        order=60,
    ),
)
