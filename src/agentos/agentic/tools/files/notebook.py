"""Notebook tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any, Mapping
from uuid import uuid4
from agentos.agentic.tools._shared import (
    AgentToolError,
)


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
        rel = target.relative_to(self.workspace.root).as_posix()
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

        self._checkpoint_store.snapshot_before_write(self.turn_id or "turn-default", notebook_path)
        target_cell_id = ""
        if mode == "replace":
            idx = _resolve_cell_idx(cell_id)
            cell = cells[idx]
            if not isinstance(cell, dict):
                raise AgentToolError(f"Cell at index {idx} is invalid.")
            if cell_type is not None:
                clean_type = str(cell_type).strip().lower()
                if clean_type not in {"code", "markdown"}:
                    raise AgentToolError("cell_type must be 'code' or 'markdown'.")
                cell["cell_type"] = clean_type
            cell["source"] = _format_source_lines(new_source)
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
            target_cell_id = str(cell.get("id") or idx)
        elif mode == "insert":
            eff_type = str(cell_type or "code").strip().lower()
            if eff_type not in {"code", "markdown"}:
                raise AgentToolError("cell_type must be 'code' or 'markdown'.")
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
            target_cell_id = str(removed.get("id") if isinstance(removed, Mapping) else idx) or str(idx)

        new_text = json.dumps(nb, ensure_ascii=False, indent=1) + "\n"
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
        }
