"""Write tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import hashlib
from typing import Any
from agentos.agentic.tools._shared import (
    AgentToolError,
    compute_structured_patch,
)


class WriteTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def write_file(self, path: str, content: str, mode: str = "overwrite") -> dict[str, Any]:
        if mode not in {"overwrite", "append"}:
            raise AgentToolError("mode must be 'overwrite' or 'append'.")
        if mode == "append":
            target = self.workspace.resolve(path)
            if target.exists() and target.is_file():
                raw_bytes = target.read_bytes()
                original_sha = hashlib.sha256(raw_bytes).hexdigest()
                old_content = raw_bytes.decode("utf-8", errors="replace")
            else:
                original_sha = ""
                old_content = None
            self._checkpoint_store.snapshot_before_write(self.turn_id or "turn-default", path)
            written, total = self.workspace.append_text(path, content)
            new_content = (old_content or "") + content
            body = f"Appended {written} bytes to {path}; it now holds {total} bytes."
        else:
            old_content, original_sha = self._check_read_file_guard(path)
            self._checkpoint_store.snapshot_before_write(self.turn_id or "turn-default", path)
            written = total = self.workspace.write_text(path, content)
            new_content = content
            body = f"Wrote {written} bytes to {path}."
        updated_sha = self._record_file_read(
            path, offset=1, limit=400, total_lines=len(new_content.splitlines()),
        )
        hunks, added_lines, removed_lines = compute_structured_patch(old_content or "", new_content)
        artifact = dict(self.workspace.file_metadata(path))
        artifact["structured_patch"] = hunks
        artifact["added_lines"] = added_lines
        artifact["removed_lines"] = removed_lines
        artifact["additions"] = added_lines
        artifact["deletions"] = removed_lines
        artifact["original_sha256"] = original_sha
        artifact["updated_sha256"] = updated_sha
        artifacts = [artifact]
        self._queue_reindex(artifacts)
        return {
            "summary": f"{'Acrescentou a' if mode == 'append' else 'Escreveu'} {path}",
            "content": body,
            "payload": {
                "path": path,
                "bytes_written": written,
                "bytes_total": total,
                "mode": mode,
                "label": path,
                "artifacts": artifacts,
                "structured_patch": hunks,
                "added_lines": added_lines,
                "removed_lines": removed_lines,
                "additions": added_lines,
                "deletions": removed_lines,
                "original_sha256": original_sha,
                "updated_sha256": updated_sha,
            },
        }
