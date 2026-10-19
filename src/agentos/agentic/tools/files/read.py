"""Read tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agentos.reading.extract import extract_text
from agentos.reading.render import ImageTooLarge, normalize_image, render_pdf_pages
from agentos.reading.vision import VisionUnavailable
from agentos.agentic.file_preview import media_type_for
from agentos.agentic.lifecycle import HookEvent
from agentos.agentic.tools.public_names import public_text
from agentos.agentic.tools._shared import (
    AgentToolError,
    EXTERNAL_CHANGE_HASH_LIMIT_BYTES,
    ReadFileRecord,
    _READ_ORDER,
    _bounded,
    image_block,
    _TEXT,
    _schema,
)
from agentos.agentic.tools.spec import ToolSpec


class ReadTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _record_file_read(
        self,
        path: str,
        *,
        offset: int = 1,
        limit: int = 400,
        total_lines: int = 0,
        explicit_read: bool = False,
    ) -> str:
        try:
            target = self.workspace.resolve(path)
            if not target.is_file():
                return ""
            norm = target.relative_to(self.workspace.root).as_posix()
            raw_bytes = target.read_bytes()
            stat = target.stat()
            sha = hashlib.sha256(raw_bytes).hexdigest()
            with self._read_file_lock:
                self._read_file_state[norm] = ReadFileRecord(
                    mtime_ns=stat.st_mtime_ns,
                    sha256=sha,
                    offset=offset,
                    limit=limit,
                    total_lines=total_lines,
                    explicit_read=explicit_read,
                    read_seq=next(_READ_ORDER),
                )
            return sha
        except Exception:
            return ""

    def _note_memory_for(self, target: Path) -> None:
        """A read below a directory brings in its instruction files and matching rules (once each)."""
        tracker = self.memory_tracker
        if tracker is None:
            return
        try:
            loaded = list(tracker.on_file_read(target))
        except Exception:  # noqa: BLE001 - memory enrichment never fails a read
            return
        if not loaded:
            return
        from agentos.memory_files import instructions_payload

        for item in loaded:
            if self._lifecycle is not None:
                self._emit_lifecycle(self._lifecycle, HookEvent.INSTRUCTIONS_LOADED, **instructions_payload(item, trigger=target))
            if self.reminders is not None:
                reason = "its directory" if item.load_reason == "nested_traversal" else "a rule matching it"
                try:
                    self.reminders.add(
                        "memory",
                        f"Instructions from {item.path.name} ({reason}) apply to the files you are working on:\n{item.content[:8000]}",
                        key=f"memory:{item.path}",
                    )
                except Exception:  # noqa: BLE001
                    pass

    def detect_external_changes(self, *, limit: int = 200) -> list[tuple[str, str, str]]:
        """Files read in this turn that changed on disk since, other than through these tools; each reported once."""
        with self._read_file_lock:
            items = sorted(self._read_file_state.items(), key=lambda item: item[1].read_seq, reverse=True)[: max(0, int(limit))]
        changes: list[tuple[str, str, str]] = []
        for relative, record in items:
            try:
                target = self.workspace.resolve(relative)
            except Exception:  # noqa: BLE001 - an unresolvable entry is not a change
                continue
            try:
                stat = target.stat()
            except FileNotFoundError:
                event = "unlink"
            except OSError:
                continue
            else:
                if stat.st_mtime_ns == record.mtime_ns:
                    continue
                stamp = (stat.st_mtime_ns, stat.st_size)
                with self._read_file_lock:
                    if self._external_stamps.get(relative) == stamp:
                        continue  # examined at this stamp already: nothing new to report or hash
                    self._external_stamps[relative] = stamp
                if stat.st_size > EXTERNAL_CHANGE_HASH_LIMIT_BYTES:
                    event = "change"  # too large to read on every request: the moved stamp is the change
                else:
                    try:
                        digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    except OSError:
                        continue
                    if digest == record.sha256:
                        with self._read_file_lock:
                            record.mtime_ns = stat.st_mtime_ns  # touched, not changed
                        continue
                    event = "change"
            marker = (relative, record.sha256, event)
            if marker in self._reported_changes:
                continue
            self._reported_changes.add(marker)
            changes.append((relative, str(target), event))
        return changes

    def _check_read_file_guard(self, path: str) -> tuple[str | None, str]:
        """Verify read-before-edit/overwrite and anti-staleness in Code mode.

        Returns ``(old_content_or_none, original_sha256)``.
        """
        target = self.workspace.resolve(path)
        if not target.exists() or not target.is_file():
            return None, ""
        norm = target.relative_to(self.workspace.root).as_posix()
        raw_bytes = target.read_bytes()
        current_sha = hashlib.sha256(raw_bytes).hexdigest()
        old_content = raw_bytes.decode("utf-8", errors="replace")
        if self._code_mode_active and not norm.startswith(".orin/"):
            with self._read_file_lock:
                record = self._read_file_state.get(norm)
            if record is None:
                raise AgentToolError(
                    "File has not been read yet. Read it first with read_file before writing or editing it.",
                    error_code="FILE_NOT_READ",
                )
            if current_sha != record.sha256:
                raise AgentToolError(
                    "File has been modified since read, either by the user or by a linter. Read it again with read_file before attempting to write it.",
                    error_code="STALE_FILE_READ",
                )
        return old_content, current_sha

    @staticmethod
    def _parse_pdf_page_spec(pages_spec: str | None, total_pages: int) -> list[int]:
        if total_pages <= 0:
            return []
        if pages_spec is None or not str(pages_spec).strip():
            return list(range(1, min(total_pages, 20) + 1))
        spec = str(pages_spec).strip()
        selected: list[int] = []
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                pieces = part.split("-", 1)
                if len(pieces) != 2 or not pieces[0].strip().isdigit() or not pieces[1].strip().isdigit():
                    raise AgentToolError(f"Invalid PDF pages range: '{pages_spec}'. Use e.g. '1-5' or '3'.")
                start_p = int(pieces[0].strip())
                end_p = int(pieces[1].strip())
                if start_p < 1 or end_p < start_p:
                    raise AgentToolError(f"Invalid PDF pages range: '{pages_spec}'.")
                selected.extend(range(start_p, min(end_p, total_pages) + 1))
            else:
                if not part.isdigit():
                    raise AgentToolError(f"Invalid PDF page number: '{pages_spec}'.")
                page_num = int(part)
                if page_num < 1:
                    raise AgentToolError(f"Invalid PDF page number: '{pages_spec}'.")
                if page_num <= total_pages:
                    selected.append(page_num)
        deduped = list(dict.fromkeys(selected))
        if len(deduped) > 20:
            raise AgentToolError("Cannot read more than 20 PDF pages at once; specify a narrower page range (e.g. '1-20').")
        return deduped

    def _read_pdf_pages(self, path: str, target: Path, *, pages: str | None = None) -> dict[str, Any]:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise AgentToolError("pypdf is required to read PDF files.") from error
        try:
            reader = PdfReader(str(target))
            total_pages = len(reader.pages)
            selected_pages = self._parse_pdf_page_spec(pages, total_pages)
            out_chunks: list[str] = []
            for p_num in selected_pages:
                txt = reader.pages[p_num - 1].extract_text() or ""
                out_chunks.append(f"--- Page {p_num}/{total_pages} ---\n{txt.strip()}")
        except AgentToolError:
            raise
        except Exception as error:
            raise AgentToolError(f"Failed to read PDF '{path}': {error}") from error
        rel = target.relative_to(self.workspace.root).as_posix()
        sha = self._record_file_read(
            path,
            offset=selected_pages[0] if selected_pages else 1,
            limit=max(1, len(selected_pages)),
            total_lines=total_pages,
            explicit_read=True,
        )
        body = "\n\n".join(out_chunks) if out_chunks else "[empty PDF]"
        payload: dict[str, Any] = {
            "ok": True,
            "path": rel,
            "sha256": sha,
            "pages": selected_pages,
            "pages_read": selected_pages,
            "total_pages": total_pages,
            "truncated": False,
            "label": path,
        }
        return {
            "ok": True,
            "path": rel,
            "sha256": sha,
            "pages": selected_pages,
            "pages_read": selected_pages,
            "total_pages": total_pages,
            "summary": f"Leu PDF {path} ({len(selected_pages)} página(s))",
            "content": body,
            "payload": payload,
        }

    def read_file(
        self,
        path: str,
        offset: int = 1,
        limit: int = 400,
        pages: str | None = None,
        force_reload: bool = False,
    ) -> dict[str, Any]:
        target = self.workspace.resolve(path)
        if target.is_file() and target.suffix.lower() == ".pdf":
            return self._read_pdf_pages(path, target, pages=pages)
        norm_offset = max(1, int(offset or 1))
        norm_limit = max(1, min(int(limit or 400), 800))
        if target.is_file() and not force_reload:
            norm = target.relative_to(self.workspace.root).as_posix()
            with self._read_file_lock:
                prev = self._read_file_state.get(norm)
                is_non_code = norm in self._non_code_edited_paths
            if (
                prev is not None
                and prev.explicit_read
                and not is_non_code
                and prev.offset == norm_offset
                and prev.limit == norm_limit
            ):
                raw_bytes = target.read_bytes()
                current_sha = hashlib.sha256(raw_bytes).hexdigest()
                if current_sha == prev.sha256:
                    all_lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
                    total = len(all_lines)
                    start_line = min(norm_offset, max(1, total))
                    end_line = min(total, norm_offset - 1 + norm_limit)
                    stub = (
                        f"[File unchanged since last read ({norm}, lines {start_line}-{end_line}, "
                        f"sha256={current_sha[:12]}). Refer to previous read_file output in context.]"
                    )
                    if self.name_translator is not None:
                        stub = public_text(stub)
                    payload = {
                        "ok": True,
                        "path": norm,
                        "unchanged": True,
                        "sha256": current_sha,
                        "line_count": total,
                        "first_line": start_line,
                        "start_line": start_line,
                        "end_line": end_line,
                        "returned_lines": max(0, end_line - start_line + 1) if total > 0 else 0,
                        "total_lines": total,
                        "truncated": False,
                        "label": path,
                    }
                    return {
                        "ok": True,
                        "path": norm,
                        "unchanged": True,
                        "sha256": current_sha,
                        "line_count": total,
                        "start_line": start_line,
                        "end_line": end_line,
                        "summary": f"Arquivo inalterado: {path}",
                        "content": stub,
                        "payload": payload,
                    }
        lines, first, total, truncated = self.workspace.read_lines(path, offset=norm_offset, limit=norm_limit)
        sha = self._record_file_read(
            path,
            offset=norm_offset,
            limit=norm_limit,
            total_lines=total,
            explicit_read=True,
        )
        self._note_memory_for(target)
        norm = target.relative_to(self.workspace.root).as_posix() if target.exists() else path
        if not lines:
            body = "[empty file]" if total == 0 else f"[no lines at offset {first}; the file has {total} lines]"
        else:
            body = "\n".join(f"{first + index:6}\t{line}" for index, line in enumerate(lines))
        next_line = first + len(lines)
        if lines and next_line <= total:
            if self.name_translator is not None:
                body += f"\n\n[{total - next_line + 1} more lines; continue with Read(file_path=\"{path}\", offset={next_line})]"
            else:
                body += f"\n\n[{total - next_line + 1} more lines; continue with read_file(path=\"{path}\", offset={next_line})]"
        if truncated:
            body += "\n\n[file exceeds the workspace read limit; the tail was not loaded]"
        end_line = first + len(lines) - 1 if lines else first
        payload = {
            "ok": True,
            "path": path,
            "unchanged": False,
            "sha256": sha,
            "first_line": first,
            "start_line": first,
            "end_line": end_line,
            "returned_lines": len(lines),
            "total_lines": total,
            "line_count": total,
            "truncated": truncated,
            "label": path,
        }
        return {
            "ok": True,
            "path": norm,
            "unchanged": False,
            "sha256": sha,
            "line_count": total,
            "start_line": first,
            "end_line": end_line,
            "summary": f"Leu {path}",
            "content": body,
            "payload": payload,
        }

    def view_file(self, path: str, question: str = "") -> dict[str, Any]:
        target = self.workspace.resolve(path)
        if not target.is_file():
            raise AgentToolError(f"'{path}' is not a file in this workspace.")
        self._record_file_read(path)
        media_type = media_type_for(target)
        if media_type.startswith("image/"):
            return self._view_image(path, target, media_type, question)
        try:
            extracted = extract_text(target, media_type)
        except ValueError as error:
            raise AgentToolError(f"'{path}' ({media_type}) cannot be read as a document.") from error
        if media_type == "application/pdf" and extracted.pages_without_text and not extracted.text.strip():
            return self._view_scanned_pdf(path, target, extracted.pages_without_text, question)
        body, truncated = _bounded(extracted.text)
        if not body.strip() and extracted.pages_without_text:
            body = f"[o PDF não tem camada de texto nas páginas {', '.join(str(number) for number in extracted.pages_without_text)}]"
        if truncated or extracted.truncated:
            body += "\n\n[conteúdo truncado no limite de leitura]"
        return {
            "summary": f"Leu {path}",
            "content": body or "[documento sem texto]",
            "payload": {"path": path, "media_type": media_type, "label": path,
                        "pages_without_text": list(extracted.pages_without_text)},
        }

    def transcribe_pdf(self, path: str) -> dict[str, Any]:
        """Return a PDF's native text layer without rendering any page.

        This intentionally does not fall back to vision: it lets the model
        choose inexpensive, deterministic extraction whenever the user's task
        only needs text, while clearly identifying pages that require the
        existing ``view_file`` visual path.
        """
        target = self.workspace.resolve(path)
        if not target.is_file():
            raise AgentToolError(f"'{path}' is not a file in this workspace.")
        media_type = media_type_for(target)
        if media_type != "application/pdf":
            raise AgentToolError(f"'{path}' is not a PDF.")
        try:
            extracted = extract_text(target, media_type)
        except Exception as error:  # pypdf errors are not safe to expose verbatim
            raise AgentToolError(f"Could not extract the text layer from '{path}'.") from error
        body, bounded = _bounded(extracted.text)
        if not body.strip():
            body = "[o PDF não possui camada de texto extraível]"
        if extracted.pages_without_text:
            pages = ", ".join(str(number) for number in extracted.pages_without_text)
            body += (
                f"\n\n[páginas sem camada de texto: {pages}; use view_file apenas se "
                "o conteúdo visual dessas páginas for necessário]"
            )
        if bounded or extracted.truncated:
            body += "\n\n[transcrição truncada no limite de leitura]"
        return {
            "summary": f"Transcreveu {path}",
            "content": body,
            "payload": {
                "path": path, "media_type": media_type, "label": path,
                "pages_without_text": list(extracted.pages_without_text),
                "truncated": bounded or extracted.truncated,
                "text_extraction": True,
            },
        }

    def _visual_read(self, path: str, images: list[tuple[str, str]], question: str) -> str | None:
        """Transcribe ``images`` or return None when no reader could do it."""
        if self._visual_reader is None:
            return None
        try:
            return self._visual_reader.transcribe(images, instruction=question)
        except VisionUnavailable:
            return None

    def _no_visual_reading(self, path: str, media_type: str) -> dict[str, Any]:
        return {
            "summary": f"Não foi possível ler {path}",
            "content": (
                f"Não há leitura visual disponível para '{path}': o modelo deste turno não enxerga "
                "e nenhum modelo de leitura visual pôde ser usado. Peça à pessoa para escolher um "
                "modelo com visão ou configurar o modelo de leitura visual em Configurações."
            ),
            "payload": {"path": path, "media_type": media_type, "label": path},
            "images": [],
        }

    def _view_image(self, path: str, target, media_type: str, question: str) -> dict[str, Any]:
        try:
            data, normalized_type = normalize_image(target)
        except ImageTooLarge as error:
            raise AgentToolError(str(error)) from error
        if self.model_sees_images:
            return {
                "summary": f"Abriu {path}",
                "content": f"A imagem '{path}' está anexada logo abaixo.",
                "payload": {"path": path, "media_type": media_type, "label": path},
                "images": [image_block(normalized_type, data)],
            }
        text = self._visual_read(path, [(data, normalized_type)], question)
        if text is None:
            return self._no_visual_reading(path, media_type)
        return {
            "summary": f"Leitura visual de {path}",
            "content": f"Leitura visual de '{path}':\n\n{text}",
            "payload": {"path": path, "media_type": media_type, "label": path, "visual_read": True},
            "images": [],
        }

    def _view_scanned_pdf(self, path: str, target, pages: tuple[int, ...], question: str) -> dict[str, Any]:
        rendered = render_pdf_pages(target, pages)
        if not rendered:
            raise AgentToolError(f"'{path}' has no readable page.")
        if self.model_sees_images:
            return {
                "summary": f"Abriu {path}",
                "content": f"As páginas escaneadas de '{path}' estão anexadas logo abaixo.",
                "payload": {"path": path, "media_type": "application/pdf", "label": path, "pages": list(pages)},
                "images": [image_block(media_type, data) for data, media_type in rendered],
            }
        text = self._visual_read(path, rendered, question)
        if text is None:
            return self._no_visual_reading(path, "application/pdf")
        return {
            "summary": f"Leitura visual de {path}",
            "content": f"Leitura visual de '{path}' ({len(rendered)} página(s)):\n\n{text}",
            "payload": {"path": path, "media_type": "application/pdf", "label": path, "visual_read": True, "pages": list(pages)},
            "images": [],
        }


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="read_file",
        description="Read a UTF-8 text file or PDF page range from the conversation workspace. Output is line-numbered. Use offset/limit to read a long file in windows instead of guessing, or pages (e.g. '1-5') for PDFs.",
        legacy_schema=_schema({
            "path": {**_TEXT, "description": "Workspace-relative path, e.g. notes/plan.md"},
            "offset": {"type": "integer", "minimum": 1, "description": "First line to return (1-based). Defaults to 1."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 800, "description": "How many lines to return. Defaults to 400."},
            "pages": {**_TEXT, "description": "Optional page range for PDF files (e.g. '1-5' or '3', max 20 pages)."},
            "force_reload": {"type": "boolean", "description": "Force re-reading full content even if unchanged since last read."},
        }, ("path",)),
        handler="read_file",
        kind="filesystem",
        read_only=True,
        order=10,
    ),
    ToolSpec(
        key="view_file",
        description="Read a document or an image from the conversation workspace: PDF, Word, Excel, PowerPoint, plain text, or a picture. Use this instead of read_file whenever the file is not plain text.",
        legacy_schema=_schema({
            "path": {**_TEXT, "description": "Workspace-relative path, e.g. uploads/nota.pdf"},
            "question": {**_TEXT, "description": "What you need from the file. Guides the visual reading of an image or a scanned page."},
        }, ("path",)),
        handler="view_file",
        kind="filesystem",
        read_only=True,
        order=20,
    ),
    ToolSpec(
        key="transcribe_pdf",
        description="Extract the native text layer from a PDF without visual reading. Prefer this for PDFs when ordinary text is enough; use view_file only when layout, images, tables, diagrams, or pages without a text layer matter.",
        legacy_schema=_schema({
            "path": {**_TEXT, "description": "Workspace-relative PDF path, e.g. uploads/relatorio.pdf"},
        }, ("path",)),
        handler="transcribe_pdf",
        kind="filesystem",
        read_only=True,
        order=30,
    ),
)
