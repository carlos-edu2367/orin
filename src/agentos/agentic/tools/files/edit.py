"""Edit tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any, Mapping, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools._shared import AgentToolError, compute_structured_patch, _TEXT, _schema
from agentos.agentic.tools.files import ORIGINAL_FILE_LIMIT
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class EditTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    @staticmethod
    def _normalized_edits(old_text: str | None, new_text: str | None, edits: list[Mapping[str, Any]] | None) -> list[tuple[str, str]]:
        if isinstance(edits, list) and len(edits) == 0 and (old_text is not None or new_text is not None):
            edits = None
        if isinstance(edits, list) and len(edits) > 0 and (old_text is None or old_text == "") and (new_text is None or new_text == ""):
            old_text = None
            new_text = None
        single = old_text is not None or new_text is not None
        if single and edits:
            # Some tool providers serialise the same single replacement in both
            # supported shapes. Treat that exact duplication as one edit, while
            # preserving the refusal for genuinely ambiguous requests.
            duplicate_single = (
                len(edits) == 1
                and isinstance(edits[0], Mapping)
                and edits[0].get("old_text") == old_text
                and edits[0].get("new_text") == new_text
            )
            if not duplicate_single:
                raise AgentToolError("provide either old_text/new_text or edits, not both.")
            edits = None
        if single:
            if not isinstance(old_text, str) or not old_text:
                raise AgentToolError("old_text must be a non-blank string")
            if not isinstance(new_text, str):
                raise AgentToolError("new_text must be a string")
            return [(old_text, new_text)]
        if not isinstance(edits, list) or not edits:
            raise AgentToolError("provide old_text/new_text or a non-empty edits array.")
        normalized: list[tuple[str, str]] = []
        for index, item in enumerate(edits):
            if not isinstance(item, Mapping):
                raise AgentToolError(f"edits[{index}] must be an object with old_text and new_text.")
            current, replacement = item.get("old_text"), item.get("new_text")
            if not isinstance(current, str) or not current:
                raise AgentToolError(f"edits[{index}].old_text must be a non-blank string")
            if not isinstance(replacement, str):
                raise AgentToolError(f"edits[{index}].new_text must be a string")
            normalized.append((current, replacement))
        return normalized

    def edit_file(self, path: str, old_text: str | None = None, new_text: str | None = None, edits: list[Mapping[str, Any]] | None = None, replace_all: bool = False) -> dict[str, Any]:
        operations = self._normalized_edits(old_text, new_text, edits)
        for index, (current, replacement) in enumerate(operations):
            if current == replacement:
                raise AgentToolError(f"edit {index + 1}: old and new text are identical; nothing would change.")
        _, original_sha = self._check_read_file_guard(path)
        content, truncated = self.workspace.read_text(path)
        if truncated:
            raise AgentToolError("file is too large to edit safely; split the edit into a smaller file or rewrite it deliberately.")
        # Every edit is validated against the running draft before anything is
        # written, so a bad fragment late in the batch cannot leave the file
        # half-edited.
        draft = content
        for index, (current, replacement) in enumerate(operations):
            matches = draft.count(current)
            if matches == 0:
                raise AgentToolError(f"edit {index + 1}: old_text was not found; read the file and provide the exact fragment.")
            if matches > 1 and not replace_all:
                raise AgentToolError(f"edit {index + 1}: old_text occurs {matches} times; add surrounding text or set replace_all=true.")
            draft = draft.replace(current, replacement) if replace_all else draft.replace(current, replacement, 1)
        self._snapshot_before_write(path)
        written = self.workspace.write_text(path, draft)
        if not self._dedup_after_non_code_edit:
            try:
                self._non_code_edited_paths.add(self.workspace.key(self.workspace.resolve(path)))
            except Exception:
                pass
        updated_sha = self._record_file_read(
            path, offset=1, limit=400, total_lines=len(draft.splitlines()),
        )
        hunks, added_lines, removed_lines = compute_structured_patch(content, draft)
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
            "summary": f"Editou {path}",
            "content": f"Applied {len(operations)} edit(s) to {path} ({written} bytes).",
            "payload": {
                "path": path,
                "bytes_written": written,
                "edits_applied": len(operations),
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
            "tool_output": {
                "filePath": str(self.workspace.resolve(path)),
                "oldString": operations[0][0],
                "newString": operations[0][1],
                "originalFile": content if len(content) <= ORIGINAL_FILE_LIMIT else None,
                "structuredPatch": hunks,
                "userModified": False,
                "replaceAll": bool(replace_all),
                **({"edits": [{"oldString": old, "newString": new} for old, new in operations]} if len(operations) > 1 else {}),
            },
        }


class EditItem(ToolInput):
    old_string: str = Field(description="Exact text to replace, including indentation.")
    new_string: str = Field(description="Replacement text.")


class EditInput(ToolInput):
    file_path: str = Field(description="Path of the file to edit.")
    old_string: str = Field(description="Exact text to replace, including indentation.")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool | None = Field(None, description="Replace every occurrence.")
    edits: list[EditItem] | None = Field(
        None,
        description="Orin: several replacements applied in order as one change; send empty old_string and new_string with it.",
    )


class PatchHunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    oldStart: int
    oldLines: int
    newStart: int
    newLines: int
    lines: list[str]


class EditOutput(BaseModel):
    model_config = ConfigDict(extra="allow")  # Orin adds `edits` for a batch
    filePath: str
    oldString: str
    newString: str
    originalFile: str | None
    structuredPatch: list[PatchHunk]
    userModified: bool
    replaceAll: bool


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="edit_file",
        description="Replace text fragments in a UTF-8 workspace file. Read the file first. Use either old_text/new_text for one replacement or edits for several; the whole batch is rejected if any fragment is missing or ambiguous.",
        legacy_schema=_schema({
            "path": _TEXT,
            "old_text": {**_TEXT, "description": "Single-replacement form. Use together with new_text and omit edits."},
            "new_text": {**_TEXT, "description": "Replacement for old_text in the single-replacement form. Omit when using edits."},
            "edits": {
                "type": "array",
                "description": "Batch form. Each item replaces one fragment; they are applied in order.",
                "items": _schema({"old_text": _TEXT, "new_text": _TEXT}, ("old_text", "new_text")),
            },
            "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring a unique one."},
        }, ("path",)),
        handler="edit_file",
        kind="filesystem",
        input_model=EditInput,
        output_model=EditOutput,
        public_description='Replace an exact string in a file you have read. old_string must occur exactly once unless replace_all is true.',
        policy_tags=("mutates",),
        order=50,
    ),
)
