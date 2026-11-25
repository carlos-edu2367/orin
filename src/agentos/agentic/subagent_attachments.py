"""Prepare and validate file attachments passed to subagents (Frente B)."""
from __future__ import annotations

from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Literal, Sequence

from agentos.agentic.provider_content import image_block
from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError
from agentos.providers.antigravity_cli.media_gate import (
    ALLOWED_AUDIO_EXTENSIONS,
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    _is_reparse_or_symlink,
)
from agentos.reading.extract import extract_text
from agentos.reading.render import ImageTooLarge, normalize_image, render_pdf_pages

MAX_SUBAGENT_ATTACHMENTS = 12
MAX_SUBAGENT_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB per image
MAX_SUBAGENT_TEXT_FILE_BYTES = 200 * 1024  # 200 KB per text file
MAX_SUBAGENT_TOTAL_TEXT_BYTES = 500 * 1024  # 500 KB total inline text
MAX_SUBAGENT_DOCUMENT_BYTES = 50 * 1024 * 1024  # 50 MB per PDF / Office file
MAX_SUBAGENT_MEDIA_BYTES = 200 * 1024 * 1024  # 200 MB per audio / video file
MAX_SUBAGENT_PDF_RENDERED_PAGES = 6

AttachmentKind = Literal["image", "text", "pdf", "office", "audio", "video"]

_OFFICE_EXTENSIONS: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log", ".ini", ".cfg", ".conf",
        ".toml", ".yaml", ".yml", ".json", ".jsonl", ".ndjson", ".xml", ".svg", ".html", ".htm",
        ".css", ".scss", ".sass", ".less", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts",
        ".tsx", ".py", ".pyi", ".rb", ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".c", ".h",
        ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".php", ".swift", ".m", ".mm", ".sh", ".bash",
        ".zsh", ".fish", ".ps1", ".bat", ".cmd", ".sql", ".graphql", ".gql", ".proto", ".tf",
        ".tfvars", ".dockerfile", ".env", ".gitignore", ".editorconfig", ".diff", ".patch",
        ".vue", ".svelte", ".astro", ".lua", ".r", ".dart", ".ex", ".exs", ".erl", ".hs",
        ".clj", ".cljs", ".vim", ".tex", ".bib",
    }
)

_TEXT_FILENAMES: frozenset[str] = frozenset(
    {
        "dockerfile", "makefile", "rakefile", "gemfile", "procfile", "readme", "license",
        "changelog", "notice", "authors", ".env", ".gitignore", ".dockerignore", ".editorconfig",
    }
)

_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_FORBIDDEN_DIR_SEQUENCE: tuple[str, ...] = (".gemini", "antigravity-cli", "brain")


class SubagentAttachmentError(ValueError):
    """Raised when a subagent attachment fails validation or model capability checks."""

    def __init__(self, message: str, *, code: str = "INVALID_ARGUMENTS") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SubagentAttachment:
    original_ref: str
    resolved_path: Path
    display_name: str
    kind: AttachmentKind
    media_type: str
    bytes: int
    staged_path: Path | None = None

    @property
    def delivery_path(self) -> Path:
        return self.staged_path if self.staged_path is not None else self.resolved_path


@dataclass(frozen=True, slots=True)
class PreparedSubagentAttachments:
    items: tuple[SubagentAttachment, ...]
    content_blocks: tuple[dict[str, Any], ...]
    turn_attachments: tuple[dict[str, object], ...]
    allowed_media_files: tuple[Path, ...]
    has_images: bool
    has_native_media: bool
    summary_names: tuple[str, ...]


def _has_control_chars(value: str) -> bool:
    return any(
        ord(ch) < 32 or ord(ch) == 127 or ch in {"\x85", "\u2028", "\u2029"}
        for ch in value
    )


def _contains_forbidden_dir(path: Path) -> bool:
    parts = tuple(p.lower() for p in path.parts)
    seq = _FORBIDDEN_DIR_SEQUENCE
    n = len(seq)
    if len(parts) < n:
        return False
    for idx in range(len(parts) - n + 1):
        if parts[idx : idx + n] == seq:
            return True
    return False


def _has_symlink_between(root: Path, target: Path) -> bool:
    """Check whether ``target`` or any intermediate segment below ``root`` is a symlink or junction."""
    if _is_reparse_or_symlink(target):
        return True
    try:
        rel = target.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in rel.parts:
        current = current / part
        if _is_reparse_or_symlink(current):
            return True
    return False


def _allowed_roots(workspace: ConversationWorkspace) -> tuple[Path, ...]:
    roots: list[Path] = []
    for raw in (workspace.root, *getattr(workspace, "additional_roots", ())):
        try:
            resolved = Path(raw).resolve(strict=True)
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _resolve_attachment_path(
    raw_ref: str,
    workspace: ConversationWorkspace,
    *,
    host_folders: bool,
) -> Path:
    if not isinstance(raw_ref, str) or not raw_ref.strip():
        raise SubagentAttachmentError("Attachment path must be a non-blank string.")
    cleaned = raw_ref.strip()
    if len(cleaned) > 1024:
        raise SubagentAttachmentError(f"Attachment path is too long: '{cleaned[:80]}'.")
    if _has_control_chars(cleaned):
        raise SubagentAttachmentError(f"Attachment path contains invalid control characters: '{cleaned[:80]}'.")
    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://", "data:")):
        raise SubagentAttachmentError(f"Remote or data URLs are not allowed in attachments: '{cleaned[:80]}'.")
    if cleaned.startswith("workspace://"):
        cleaned = cleaned[len("workspace://") :].lstrip("/")
        if not cleaned:
            raise SubagentAttachmentError("Attachment 'workspace://' URI must point to a file.")
    elif lowered.startswith("file://"):
        cleaned = cleaned[len("file://") :]
        if os.name == "nt" and cleaned.startswith("/") and len(cleaned) >= 3 and cleaned[2] == ":":
            cleaned = cleaned[1:]
    if cleaned.startswith(("\\\\", "//")):
        raise SubagentAttachmentError(f"UNC or network paths are not allowed in attachments: '{raw_ref}'.")

    after_drive = cleaned[2:] if _DRIVE_PREFIX_RE.match(cleaned) else cleaned
    if ":" in after_drive:
        raise SubagentAttachmentError(f"Attachment path contains an invalid ':' character: '{raw_ref}'.")

    raw_segments = [seg for seg in re.split(r"[\\/]+", after_drive) if seg != ""]
    if not raw_segments or any(seg == ".." for seg in raw_segments):
        raise SubagentAttachmentError(f"Path traversal is not allowed in attachments: '{raw_ref}'.")

    effective_host_folders = bool(host_folders or getattr(workspace, "unrestricted", False))
    roots = _allowed_roots(workspace)
    if not roots:
        raise SubagentAttachmentError("Workspace root does not exist.")
    ws_root = roots[0]

    expanded = Path(cleaned).expanduser() if cleaned.startswith("~") else Path(cleaned)
    is_abs = bool(_DRIVE_PREFIX_RE.match(cleaned) or expanded.is_absolute() or (os.name != "nt" and cleaned.startswith("/")))

    if is_abs:
        candidate = expanded
        if not effective_host_folders:
            # Must stay inside workspace root or additional allowed roots without symlinks.
            matching_root = None
            for root in roots:
                try:
                    candidate.relative_to(root)
                    matching_root = root
                    break
                except ValueError:
                    continue
            if matching_root is None:
                # Try Workspace.resolve as fallback (handles case/normalization differences on Windows)
                try:
                    ws_resolved = workspace.resolve(cleaned)
                except WorkspaceError as exc:
                    raise SubagentAttachmentError(
                        f"Attachment '{raw_ref}' is outside the allowed workspace."
                    ) from exc
                for root in roots:
                    if ws_resolved == root or ws_resolved.is_relative_to(root):
                        matching_root = root
                        break
                if matching_root is None:
                    raise SubagentAttachmentError(
                        f"Attachment '{raw_ref}' is outside the allowed workspace."
                    )
            if _has_symlink_between(matching_root, candidate):
                raise SubagentAttachmentError(
                    f"Symlinks and junctions are not allowed in attachments: '{raw_ref}'."
                )
    else:
        rel_posix = cleaned.replace("\\", "/").lstrip("/")
        candidate = ws_root / rel_posix
        if _has_symlink_between(ws_root, candidate):
            raise SubagentAttachmentError(
                f"Symlinks and junctions are not allowed in attachments: '{raw_ref}'."
            )

    if _is_reparse_or_symlink(candidate):
        raise SubagentAttachmentError(
            f"Symlinks and junctions are not allowed in attachments: '{raw_ref}'."
        )

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SubagentAttachmentError(f"Attachment file not found: '{raw_ref}'.") from exc
    except OSError as exc:
        raise SubagentAttachmentError(f"Attachment file cannot be accessed: '{raw_ref}' ({exc}).") from exc

    if _is_reparse_or_symlink(resolved):
        raise SubagentAttachmentError(
            f"Symlinks and junctions are not allowed in attachments: '{raw_ref}'."
        )

    if _contains_forbidden_dir(resolved):
        raise SubagentAttachmentError(f"Attachment path is in a restricted directory: '{raw_ref}'.")

    if not effective_host_folders:
        if not any(resolved != root and resolved.is_relative_to(root) for root in roots):
            raise SubagentAttachmentError(
                f"Attachment '{raw_ref}' resolves outside the allowed workspace."
            )
    else:
        # Even with host_folders enabled, verify no component in the candidate path is a symlink.
        cur = candidate
        for _ in range(len(candidate.parts)):
            if _is_reparse_or_symlink(cur):
                raise SubagentAttachmentError(
                    f"Symlinks and junctions are not allowed in attachments: '{raw_ref}'."
                )
            if cur.parent == cur:
                break
            cur = cur.parent

    try:
        st = os.lstat(resolved)
    except OSError as exc:
        raise SubagentAttachmentError(f"Attachment file cannot be inspected: '{raw_ref}' ({exc}).") from exc

    if stat.S_ISDIR(st.st_mode):
        raise SubagentAttachmentError(f"Attachment path is a directory, not a file: '{raw_ref}'.")
    if not stat.S_ISREG(st.st_mode):
        raise SubagentAttachmentError(f"Attachment path is not a regular file: '{raw_ref}'.")

    return resolved


def _classify_attachment(path: Path) -> tuple[AttachmentKind, str]:
    suffix = path.suffix.lower()
    if suffix in ALLOWED_IMAGE_EXTENSIONS:
        return "image", _IMAGE_MEDIA_TYPES.get(suffix, "image/png")
    if suffix == ".pdf":
        return "pdf", "application/pdf"
    if suffix in _OFFICE_EXTENSIONS:
        return "office", _OFFICE_EXTENSIONS[suffix]
    if suffix in ALLOWED_AUDIO_EXTENSIONS:
        guessed, _ = mimetypes.guess_type(path.name)
        return "audio", guessed or "audio/mpeg"
    if suffix in ALLOWED_VIDEO_EXTENSIONS:
        guessed, _ = mimetypes.guess_type(path.name)
        return "video", guessed or "video/mp4"
    if suffix in _TEXT_EXTENSIONS or path.name.lower() in _TEXT_FILENAMES:
        guessed, _ = mimetypes.guess_type(path.name)
        return "text", guessed or "text/plain"
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and (guessed.startswith("text/") or guessed in {"application/json", "application/xml"}):
        return "text", guessed
    # Probe small unknown-extension files to allow plain UTF-8 text without NUL bytes.
    try:
        sample = path.read_bytes()[:4096]
    except OSError as exc:
        raise SubagentAttachmentError(f"Attachment '{path.name}' cannot be read: {exc}") from exc
    if b"\x00" not in sample:
        try:
            sample.decode("utf-8")
            return "text", "text/plain"
        except UnicodeDecodeError:
            pass
    raise SubagentAttachmentError(
        f"Unsupported binary attachment '{path.name}'. Supported types: images, text/code, PDF, Office, audio, video."
    )


def _display_name_for(resolved: Path, workspace: ConversationWorkspace) -> str:
    try:
        rel = resolved.relative_to(workspace.root)
        return rel.as_posix()
    except ValueError:
        return resolved.name


def _maybe_stage_media_file(
    resolved: Path,
    workspace: ConversationWorkspace,
    staging_dir: Path | None,
    index: int,
) -> Path | None:
    """Copy a media file into ``staging_dir`` when it sits outside ``workspace.root`` or has a comma in its path.

    Codex CLI's ``--image`` flag requires paths inside the workspace without commas, and staging
    also keeps Antigravity's native ``read_file`` allowlist cleanly rooted.
    """
    if staging_dir is None:
        return None
    needs_staging = False
    try:
        resolved.relative_to(workspace.root)
    except ValueError:
        needs_staging = True
    if "," in str(resolved):
        needs_staging = True
    if not needs_staging:
        return None
    staging_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", resolved.stem).strip("._-") or "attachment"
    safe_suffix = re.sub(r"[^A-Za-z0-9.]+", "", resolved.suffix.lower())
    staged_name = f"{index:02d}_{safe_stem[:48]}{safe_suffix}"
    staged = staging_dir / staged_name
    shutil.copy2(resolved, staged)
    return staged.resolve(strict=True)


def prepare_subagent_attachments(
    refs: Sequence[str] | None,
    workspace: ConversationWorkspace,
    *,
    host_folders: bool = False,
    model_sees_images: bool = True,
    native_media_extensions: frozenset[str] | None = None,
    staging_dir: Path | None = None,
) -> PreparedSubagentAttachments:
    """Validate and build multimodal blocks + delivery metadata for subagent attachments."""
    if not refs:
        return PreparedSubagentAttachments(
            items=(),
            content_blocks=(),
            turn_attachments=(),
            allowed_media_files=(),
            has_images=False,
            has_native_media=False,
            summary_names=(),
        )
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        raise SubagentAttachmentError("attachments must be a list of file paths.")
    if len(refs) > MAX_SUBAGENT_ATTACHMENTS:
        raise SubagentAttachmentError(
            f"Too many attachments ({len(refs)}): at most {MAX_SUBAGENT_ATTACHMENTS} attachments are allowed."
        )

    items: list[SubagentAttachment] = []
    content_blocks: list[dict[str, Any]] = []
    turn_attachments: list[dict[str, object]] = []
    allowed_media_files: list[Path] = []
    seen_paths: set[str] = set()
    total_text_bytes = 0
    has_images = False
    has_native_media = False

    allowed_native_exts = (
        frozenset(ext.lower() for ext in native_media_extensions)
        if native_media_extensions is not None
        else (ALLOWED_IMAGE_EXTENSIONS | ALLOWED_AUDIO_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS)
    )

    for index, raw_ref in enumerate(refs, start=1):
        resolved = _resolve_attachment_path(raw_ref, workspace, host_folders=host_folders)
        dedupe_key = os.path.normcase(str(resolved))
        if dedupe_key in seen_paths:
            continue
        seen_paths.add(dedupe_key)

        size_bytes = resolved.stat().st_size
        kind, media_type = _classify_attachment(resolved)
        display_name = _display_name_for(resolved, workspace)

        if kind == "image":
            if size_bytes <= 0:
                raise SubagentAttachmentError(f"Image attachment '{display_name}' is empty.")
            if size_bytes > MAX_SUBAGENT_IMAGE_BYTES:
                raise SubagentAttachmentError(
                    f"Image attachment '{display_name}' exceeds the 20 MB limit ({size_bytes} bytes)."
                )
            if not model_sees_images:
                raise SubagentAttachmentError(
                    f"The selected model does not support vision and cannot receive image attachment '{display_name}'.",
                    code="SUBAGENT_MODEL_NO_VISION",
                )
            try:
                b64_data, normalized_type = normalize_image(resolved)
            except ImageTooLarge as exc:
                raise SubagentAttachmentError(str(exc)) from exc
            except Exception as exc:
                raise SubagentAttachmentError(
                    f"Could not decode image attachment '{display_name}': {exc}"
                ) from exc

            staged = _maybe_stage_media_file(resolved, workspace, staging_dir, index)
            delivery = staged if staged is not None else resolved
            block = image_block(normalized_type, b64_data)
            block["path"] = str(delivery)
            content_blocks.append({"type": "text", "text": f'[Attachment {index}: "{display_name}" ({media_type})]\n'})
            content_blocks.append(block)
            turn_attachments.append(
                {
                    "kind": "image",
                    "name": display_name,
                    "media_type": media_type,
                    "path": str(delivery),
                    "bytes": size_bytes,
                }
            )
            if delivery.suffix.lower() in allowed_native_exts:
                allowed_media_files.append(delivery)
                has_native_media = True
            has_images = True
            items.append(
                SubagentAttachment(
                    original_ref=raw_ref,
                    resolved_path=resolved,
                    display_name=display_name,
                    kind=kind,
                    media_type=media_type,
                    bytes=size_bytes,
                    staged_path=staged,
                )
            )
        elif kind == "text":
            if size_bytes > MAX_SUBAGENT_TEXT_FILE_BYTES:
                raise SubagentAttachmentError(
                    f"Text attachment '{display_name}' exceeds the 200 KB per-file limit ({size_bytes} bytes)."
                )
            if total_text_bytes + size_bytes > MAX_SUBAGENT_TOTAL_TEXT_BYTES:
                raise SubagentAttachmentError(
                    "Total size of text attachments exceeds the 500 KB limit."
                )
            total_text_bytes += size_bytes
            raw_bytes = resolved.read_bytes()
            text_content = raw_bytes.decode("utf-8", errors="replace")
            content_blocks.append(
                {
                    "type": "text",
                    "text": f'<attachment name="{display_name}" media_type="{media_type}">\n{text_content}\n</attachment>',
                }
            )
            turn_attachments.append(
                {
                    "kind": "text",
                    "name": display_name,
                    "media_type": media_type,
                    "path": str(resolved),
                    "bytes": size_bytes,
                }
            )
            items.append(
                SubagentAttachment(
                    original_ref=raw_ref,
                    resolved_path=resolved,
                    display_name=display_name,
                    kind=kind,
                    media_type=media_type,
                    bytes=size_bytes,
                )
            )
        elif kind in {"pdf", "office"}:
            if size_bytes <= 0:
                raise SubagentAttachmentError(f"Document attachment '{display_name}' is empty.")
            if size_bytes > MAX_SUBAGENT_DOCUMENT_BYTES:
                raise SubagentAttachmentError(
                    f"Document attachment '{display_name}' exceeds the 50 MB limit ({size_bytes} bytes)."
                )
            remaining_chars = max(1, MAX_SUBAGENT_TOTAL_TEXT_BYTES - total_text_bytes)
            try:
                extracted = extract_text(resolved, media_type, max_chars=remaining_chars)
            except Exception as exc:
                raise SubagentAttachmentError(
                    f"Could not extract text from '{display_name}': {exc}"
                ) from exc
            extracted_text = extracted.text or ""
            encoded_len = len(extracted_text.encode("utf-8"))
            if total_text_bytes + encoded_len > MAX_SUBAGENT_TOTAL_TEXT_BYTES:
                raise SubagentAttachmentError(
                    "Total extracted text from attachments exceeds the 500 KB limit."
                )
            total_text_bytes += encoded_len
            if extracted_text:
                suffix_note = "\n[truncated]" if extracted.truncated else ""
                content_blocks.append(
                    {
                        "type": "text",
                        "text": f'<attachment name="{display_name}" media_type="{media_type}">\n{extracted_text}{suffix_note}\n</attachment>',
                    }
                )
            if kind == "pdf" and extracted.pages_without_text and model_sees_images:
                pages_to_render = extracted.pages_without_text[:MAX_SUBAGENT_PDF_RENDERED_PAGES]
                try:
                    rendered_pages = render_pdf_pages(
                        resolved, pages_to_render, max_pages=MAX_SUBAGENT_PDF_RENDERED_PAGES
                    )
                except Exception:
                    rendered_pages = []
                for page_num, (b64_page, page_mime) in zip(pages_to_render, rendered_pages):
                    content_blocks.append(
                        {"type": "text", "text": f'[Attachment "{display_name}" — rendered page {page_num}]'}
                    )
                    content_blocks.append(image_block(page_mime, b64_page))
                    has_images = True
            elif kind == "pdf" and not extracted_text and extracted.pages_without_text and not model_sees_images:
                raise SubagentAttachmentError(
                    f"PDF '{display_name}' has no text layer and the selected model does not support vision.",
                    code="SUBAGENT_MODEL_NO_VISION",
                )
            turn_attachments.append(
                {
                    "kind": kind,
                    "name": display_name,
                    "media_type": media_type,
                    "path": str(resolved),
                    "bytes": size_bytes,
                }
            )
            items.append(
                SubagentAttachment(
                    original_ref=raw_ref,
                    resolved_path=resolved,
                    display_name=display_name,
                    kind=kind,
                    media_type=media_type,
                    bytes=size_bytes,
                )
            )
        elif kind in {"audio", "video"}:
            if size_bytes <= 0:
                raise SubagentAttachmentError(f"Media attachment '{display_name}' is empty.")
            if size_bytes > MAX_SUBAGENT_MEDIA_BYTES:
                raise SubagentAttachmentError(
                    f"Media attachment '{display_name}' exceeds the 200 MB limit ({size_bytes} bytes)."
                )
            staged = _maybe_stage_media_file(resolved, workspace, staging_dir, index)
            delivery = staged if staged is not None else resolved
            if delivery.suffix.lower() in allowed_native_exts:
                allowed_media_files.append(delivery)
                has_native_media = True
            content_blocks.append(
                {
                    "type": "text",
                    "text": (
                        f'[Media attachment {index}: "{display_name}" ({media_type}) — '
                        f'absolute path: "{delivery.as_posix()}"]'
                    ),
                }
            )
            turn_attachments.append(
                {
                    "kind": kind,
                    "name": display_name,
                    "media_type": media_type,
                    "path": str(delivery),
                    "bytes": size_bytes,
                }
            )
            items.append(
                SubagentAttachment(
                    original_ref=raw_ref,
                    resolved_path=resolved,
                    display_name=display_name,
                    kind=kind,
                    media_type=media_type,
                    bytes=size_bytes,
                    staged_path=staged,
                )
            )

    return PreparedSubagentAttachments(
        items=tuple(items),
        content_blocks=tuple(content_blocks),
        turn_attachments=tuple(turn_attachments),
        allowed_media_files=tuple(allowed_media_files),
        has_images=has_images,
        has_native_media=has_native_media,
        summary_names=tuple(item.display_name for item in items),
    )


__all__ = [
    "AttachmentKind",
    "MAX_SUBAGENT_ATTACHMENTS",
    "MAX_SUBAGENT_DOCUMENT_BYTES",
    "MAX_SUBAGENT_IMAGE_BYTES",
    "MAX_SUBAGENT_MEDIA_BYTES",
    "MAX_SUBAGENT_PDF_RENDERED_PAGES",
    "MAX_SUBAGENT_TEXT_FILE_BYTES",
    "MAX_SUBAGENT_TOTAL_TEXT_BYTES",
    "PreparedSubagentAttachments",
    "SubagentAttachment",
    "SubagentAttachmentError",
    "prepare_subagent_attachments",
]
