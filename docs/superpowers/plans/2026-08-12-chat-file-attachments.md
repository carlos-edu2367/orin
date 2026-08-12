# Anexos de arquivo no chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pessoa envia arquivos pelo composer, com ou sem texto, e o agente
consegue lê-los — inclusive imagens e PDFs escaneados, quando o modelo do turno
não enxerga.

**Architecture:** O upload vai para um staging, e ao criar o turno é promovido
para `uploads/` dentro do workspace efetivo da conversa; o anexo passa a ser um
arquivo do workspace como qualquer outro. Uma ferramenta nova, `view_file`,
extrai texto nativo (PDF, Office) sem custo de modelo e, para pixel, injeta a
imagem no contexto quando o modelo enxerga ou chama um modelo de visão
configurável quando não. Modelo sem tool-calling recebe a transcrição
pré-executada.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Alembic, pytest; React 19 +
TypeScript + Vitest no cliente. Dependências novas: `python-multipart`, `pypdf`,
`python-docx`, `openpyxl`, `python-pptx` (wheels puros) e, na fase 4,
`pypdfium2` e `Pillow`.

**Spec:** `docs/superpowers/specs/2026-08-12-chat-file-attachments-design.md`

---

## Estrutura de arquivos

**Criar:**

| Arquivo | Responsabilidade |
| --- | --- |
| `src/agentos/uploads/__init__.py` | Exporta o staging e os erros públicos |
| `src/agentos/uploads/media.py` | Saneamento de nome, classificação por magic bytes, limites |
| `src/agentos/uploads/staging.py` | Grava, lê, descarta e expira o upload em staging |
| `src/agentos/uploads/promotion.py` | Move do staging para `uploads/` do workspace |
| `src/agentos/reading/__init__.py` | Exporta o pipeline de leitura |
| `src/agentos/reading/extract.py` | Texto nativo: PDF, docx, xlsx, pptx, texto puro |
| `src/agentos/reading/render.py` | Página de PDF → PNG; normalização de imagem |
| `src/agentos/reading/selection.py` | Escolha do modelo de leitura visual |
| `src/agentos/reading/vision.py` | `VisionReader`: imagem → texto por um modelo |
| `src/agentos/agentic/provider_content.py` | Conteúdo neutro → formato de cada provider |
| `src/agentos/persistence/postgres/migrations/versions/0031_message_attachments.py` | Tabelas novas |
| `frontend/src/api/uploads.ts` | Cliente das rotas de upload |
| `frontend/src/features/conversations/AttachmentChips.tsx` | Chips no composer |
| `frontend/src/features/conversations/MessageAttachments.tsx` | Anexos na mensagem |

**Modificar:**

| Arquivo | Mudança |
| --- | --- |
| `src/agentos/persistence/postgres/schema.py` | `conversation_message_attachments`, `vision_model_selections` |
| `src/agentos/conversations/chat.py` | Anexos na criação, no snapshot e no histórico |
| `src/agentos/api/gateway.py` | Rotas de upload, promoção, rotas de modelo de visão |
| `src/agentos/api/contracts.py` | Assinatura de `ConversationApplication` |
| `src/agentos/agentic/agent_tools.py` | `view_file`, `ToolOutcome.images` |
| `src/agentos/agentic/runtime.py` | Mensagem `user` com imagem após o resultado |
| `src/agentos/agentic/provider_stream.py` | Projeção de conteúdo por provider |
| `src/agentos/agentic/session.py` | Capacidades do modelo, leitor visual, pré-execução |
| `src/agentos/workers/chat.py` | Capacidades do catálogo, transporte de visão |
| `frontend/src/api/client.ts` | `upload()` multipart |
| `frontend/src/api/conversations.ts` | `attachments` no envio e no parse |
| `frontend/src/features/conversations/Composer.tsx` | Botão, colar, arrastar, envio sem texto |
| `frontend/src/features/conversations/ChatPage.tsx` | Estado dos anexos |
| `pyproject.toml` | Dependências novas |

---

# Fase 1 — Upload, promoção e persistência

### Task 1: Saneamento e classificação de arquivo

**Files:**
- Create: `src/agentos/uploads/media.py`
- Create: `src/agentos/uploads/__init__.py`
- Test: `tests/unit/uploads/test_media.py`

- [ ] **Step 1: Write the failing test**

Crie `tests/unit/uploads/__init__.py` vazio e `tests/unit/uploads/test_media.py`:

```python
import pytest

from agentos.uploads.media import UploadRejected, classify, safe_filename

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32
ZIP = b"PK\x03\x04" + b"\x00" * 32


def test_safe_filename_strips_directories_and_control_characters():
    assert safe_filename("../../etc/pa\x00ss wd.txt") == "pa ss wd.txt"


def test_safe_filename_rejects_reserved_windows_names():
    assert safe_filename("CON.txt") == "arquivo.txt"


def test_safe_filename_falls_back_when_nothing_survives():
    assert safe_filename("///") == "arquivo"


def test_safe_filename_bounds_the_length_and_keeps_the_extension():
    result = safe_filename("a" * 300 + ".png")
    assert len(result) <= 120 and result.endswith(".png")


def test_classify_reads_the_content_not_the_extension():
    assert classify("foto.txt", PNG) == ("image/png", "image")
    assert classify("relatorio.png", PDF) == ("application/pdf", "pdf")


def test_classify_accepts_office_by_zip_container_plus_extension():
    assert classify("planilha.xlsx", ZIP) == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "office",
    )


def test_classify_accepts_decodable_text():
    assert classify("notas.md", "olá mundo".encode()) == ("text/markdown", "text")


def test_classify_rejects_an_executable():
    with pytest.raises(UploadRejected):
        classify("setup.exe", b"MZ\x90\x00" + b"\x00" * 32)


def test_classify_rejects_a_zip_that_is_not_office():
    with pytest.raises(UploadRejected):
        classify("pacote.zip", ZIP)


def test_classify_rejects_binary_pretending_to_be_text():
    with pytest.raises(UploadRejected):
        classify("notas.txt", b"\x00\x01\x02\xff\xfe")


def test_classify_accepts_jpeg():
    assert classify("foto.jpg", JPEG) == ("image/jpeg", "image")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/uploads/test_media.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.uploads'`

- [ ] **Step 3: Write the implementation**

`src/agentos/uploads/media.py`:

```python
"""Name sanitation and content-based classification for user uploads.

The workspace an upload lands in is a real directory the agent can also run
commands in, so the type is decided by the bytes rather than by the extension,
and anything outside the allowlist is refused before it ever reaches disk.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 10
MAX_TURN_BYTES = 50 * 1024 * 1024
MAX_FILENAME_CHARS = 120

_UNSAFE = re.compile(r"[^A-Za-z0-9._ ()\-]")
_RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])$")

_TEXT_EXTENSIONS = {
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".json": "application/json", ".yaml": "text/yaml", ".yml": "text/yaml",
    ".py": "text/x-python", ".js": "text/javascript", ".ts": "text/typescript",
    ".tsx": "text/typescript", ".jsx": "text/javascript", ".html": "text/html",
    ".css": "text/css", ".sql": "text/plain", ".sh": "text/x-shellscript",
    ".ini": "text/plain", ".toml": "text/plain", ".log": "text/plain",
    ".xml": "text/xml",
}
_OFFICE_EXTENSIONS = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class UploadRejected(ValueError):
    """The upload was refused before anything was written to disk."""


def safe_filename(name: str) -> str:
    """Reduce a client-supplied name to a bounded, inert file name."""
    base = PurePosixPath(str(name or "").replace("\\", "/")).name
    cleaned = _UNSAFE.sub("", base.replace("\x00", "")).strip(" .")
    if not cleaned:
        return "arquivo"
    stem, dot, extension = cleaned.rpartition(".")
    if not dot:
        stem, extension = cleaned, ""
    if _RESERVED.fullmatch(stem):
        stem = "arquivo"
    if not stem:
        stem = "arquivo"
    suffix = f".{extension}" if extension else ""
    return f"{stem[: MAX_FILENAME_CHARS - len(suffix)]}{suffix}"


def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return False
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def classify(filename: str, data: bytes) -> tuple[str, str]:
    """Return ``(media_type, kind)``; raise ``UploadRejected`` for anything else.

    ``kind`` is one of ``text``, ``image``, ``pdf`` or ``office`` and is what
    the interface and the reading pipeline branch on.
    """
    extension = f".{filename.rpartition('.')[2].lower()}" if "." in filename else ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "image"
    if data.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if data.startswith(b"PK\x03\x04"):
        if extension in _OFFICE_EXTENSIONS:
            return _OFFICE_EXTENSIONS[extension], "office"
        raise UploadRejected("compressed files are not accepted")
    if extension in _TEXT_EXTENSIONS and _is_text(data):
        return _TEXT_EXTENSIONS[extension], "text"
    if not extension and _is_text(data):
        return "text/plain", "text"
    raise UploadRejected("file type is not accepted")


__all__ = [
    "MAX_FILES_PER_MESSAGE", "MAX_TURN_BYTES", "MAX_UPLOAD_BYTES",
    "UploadRejected", "classify", "safe_filename",
]
```

`src/agentos/uploads/__init__.py`:

```python
from .media import MAX_FILES_PER_MESSAGE, MAX_TURN_BYTES, MAX_UPLOAD_BYTES, UploadRejected, classify, safe_filename

__all__ = ["MAX_FILES_PER_MESSAGE", "MAX_TURN_BYTES", "MAX_UPLOAD_BYTES", "UploadRejected", "classify", "safe_filename"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/uploads/test_media.py -q`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/uploads tests/unit/uploads && git commit -m "feat(uploads): sanitize and classify an uploaded file by content"
```

---

### Task 2: Staging do upload

**Files:**
- Create: `src/agentos/uploads/staging.py`
- Modify: `src/agentos/uploads/__init__.py`
- Test: `tests/unit/uploads/test_staging.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import UTC, datetime, timedelta

import pytest

from agentos.uploads.media import UploadRejected
from agentos.uploads.staging import UploadStaging

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_store_returns_a_described_upload(tmp_path):
    staging = UploadStaging(tmp_path)
    staged = staging.store("user-1", "../foto.png", PNG)
    assert staged.filename == "foto.png"
    assert staged.media_type == "image/png"
    assert staged.kind == "image"
    assert staged.bytes == len(PNG)
    assert staged.path.read_bytes() == PNG


def test_store_refuses_an_oversized_file(tmp_path):
    staging = UploadStaging(tmp_path, max_bytes=16)
    with pytest.raises(UploadRejected):
        staging.store("user-1", "foto.png", PNG)


def test_get_is_scoped_to_the_owner(tmp_path):
    staging = UploadStaging(tmp_path)
    staged = staging.store("user-1", "foto.png", PNG)
    assert staging.get("user-1", staged.upload_id).upload_id == staged.upload_id
    with pytest.raises(LookupError):
        staging.get("user-2", staged.upload_id)


def test_get_rejects_a_forged_upload_id(tmp_path):
    staging = UploadStaging(tmp_path)
    with pytest.raises(LookupError):
        staging.get("user-1", "../../etc")


def test_discard_removes_the_file(tmp_path):
    staging = UploadStaging(tmp_path)
    staged = staging.store("user-1", "foto.png", PNG)
    assert staging.discard("user-1", staged.upload_id) is True
    with pytest.raises(LookupError):
        staging.get("user-1", staged.upload_id)


def test_purge_removes_only_what_is_older_than_the_window(tmp_path):
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    staging = UploadStaging(tmp_path, clock=lambda: now)
    old = staging.store("user-1", "velho.png", PNG)
    later = UploadStaging(tmp_path, clock=lambda: now + timedelta(hours=30))
    fresh = later.store("user-1", "novo.png", PNG)
    assert later.purge(older_than=timedelta(hours=24)) == 1
    with pytest.raises(LookupError):
        later.get("user-1", old.upload_id)
    assert later.get("user-1", fresh.upload_id).filename == "novo.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/uploads/test_staging.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.uploads.staging'`

- [ ] **Step 3: Write the implementation**

`src/agentos/uploads/staging.py`:

```python
"""Holding area for a file the person attached but has not sent yet.

The first message of a conversation has no conversation to write into, and the
person must be able to drop an attachment before deciding to send it. Both are
why the upload lands here first and is promoted only when the turn is created.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .media import MAX_UPLOAD_BYTES, UploadRejected, classify, safe_filename

_UPLOAD_ID = re.compile(r"^upl_[0-9a-f]{32}$")
_OWNER = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True, slots=True)
class StagedUpload:
    upload_id: str
    filename: str
    media_type: str
    kind: str
    bytes: int
    path: Path


class UploadStaging:
    def __init__(self, root: Path | str, *, max_bytes: int = MAX_UPLOAD_BYTES, clock=None) -> None:
        self._root = Path(root)
        self._max_bytes = int(max_bytes)
        self._clock = clock or (lambda: datetime.now(UTC))

    def _owner_directory(self, user_id: str) -> Path:
        owner = _OWNER.sub("_", str(user_id))[:64] or "anonymous"
        return self._root / owner

    def store(self, user_id: str, filename: str, data: bytes) -> StagedUpload:
        if len(data) > self._max_bytes:
            raise UploadRejected("file exceeds the upload limit")
        if not data:
            raise UploadRejected("file is empty")
        name = safe_filename(filename)
        media_type, kind = classify(name, data)
        upload_id = f"upl_{uuid4().hex}"
        directory = self._owner_directory(user_id) / upload_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        target.write_bytes(data)
        return StagedUpload(upload_id, name, media_type, kind, len(data), target)

    def get(self, user_id: str, upload_id: str) -> StagedUpload:
        if not _UPLOAD_ID.fullmatch(str(upload_id)):
            raise LookupError(upload_id)
        directory = self._owner_directory(user_id) / upload_id
        files = sorted(item for item in directory.glob("*") if item.is_file()) if directory.is_dir() else []
        if not files:
            raise LookupError(upload_id)
        target = files[0]
        data = target.read_bytes()
        media_type, kind = classify(target.name, data)
        return StagedUpload(upload_id, target.name, media_type, kind, len(data), target)

    def discard(self, user_id: str, upload_id: str) -> bool:
        if not _UPLOAD_ID.fullmatch(str(upload_id)):
            return False
        directory = self._owner_directory(user_id) / upload_id
        if not directory.is_dir():
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return True

    def purge(self, *, older_than: timedelta = timedelta(hours=24)) -> int:
        """Delete staged uploads nobody sent. Returns how many were removed."""
        if not self._root.is_dir():
            return 0
        cutoff = self._clock() - older_than
        removed = 0
        for owner in self._root.iterdir():
            if not owner.is_dir():
                continue
            for directory in owner.iterdir():
                if not directory.is_dir():
                    continue
                modified = datetime.fromtimestamp(directory.stat().st_mtime, UTC)
                if modified < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
                    removed += 1
        return removed


__all__ = ["StagedUpload", "UploadStaging"]
```

Acrescente a `src/agentos/uploads/__init__.py`:

```python
from .staging import StagedUpload, UploadStaging
```

e inclua `"StagedUpload"` e `"UploadStaging"` em `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/uploads -q`
Expected: PASS, 15 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/uploads tests/unit/uploads && git commit -m "feat(uploads): hold an attachment in staging until the turn is created"
```

---

### Task 3: Promoção do staging para o workspace

**Files:**
- Create: `src/agentos/uploads/promotion.py`
- Test: `tests/unit/uploads/test_promotion.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from agentos.agentic.workspace import ConversationWorkspace
from agentos.uploads.media import UploadRejected
from agentos.uploads.promotion import promote_uploads
from agentos.uploads.staging import UploadStaging

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_promotion_moves_the_file_into_uploads(tmp_path):
    staging = UploadStaging(tmp_path / "staging")
    workspace = ConversationWorkspace(tmp_path / "workspaces", "chat_1")
    staged = staging.store("user-1", "foto.png", PNG)
    records = promote_uploads(staging, workspace, "user-1", [staged.upload_id])
    assert records == [{
        "path": "uploads/foto.png", "original_name": "foto.png",
        "media_type": "image/png", "kind": "image", "bytes": len(PNG),
    }]
    assert (workspace.root / "uploads" / "foto.png").read_bytes() == PNG
    with pytest.raises(LookupError):
        staging.get("user-1", staged.upload_id)


def test_promotion_renames_on_collision(tmp_path):
    staging = UploadStaging(tmp_path / "staging")
    workspace = ConversationWorkspace(tmp_path / "workspaces", "chat_1")
    first = staging.store("user-1", "foto.png", PNG)
    second = staging.store("user-1", "foto.png", PNG)
    promote_uploads(staging, workspace, "user-1", [first.upload_id])
    records = promote_uploads(staging, workspace, "user-1", [second.upload_id])
    assert records[0]["path"] == "uploads/foto (2).png"


def test_promotion_refuses_more_files_than_the_limit(tmp_path):
    staging = UploadStaging(tmp_path / "staging")
    workspace = ConversationWorkspace(tmp_path / "workspaces", "chat_1")
    ids = [staging.store("user-1", f"f{index}.png", PNG).upload_id for index in range(11)]
    with pytest.raises(UploadRejected):
        promote_uploads(staging, workspace, "user-1", ids)


def test_promotion_refuses_a_batch_above_the_turn_budget(tmp_path):
    staging = UploadStaging(tmp_path / "staging")
    workspace = ConversationWorkspace(tmp_path / "workspaces", "chat_1")
    ids = [staging.store("user-1", "foto.png", PNG).upload_id for _ in range(3)]
    with pytest.raises(UploadRejected):
        promote_uploads(staging, workspace, "user-1", ids, max_total_bytes=len(PNG) * 2)


def test_promotion_rolls_back_what_it_already_moved(tmp_path):
    staging = UploadStaging(tmp_path / "staging")
    workspace = ConversationWorkspace(tmp_path / "workspaces", "chat_1")
    staged = staging.store("user-1", "foto.png", PNG)
    records = promote_uploads(staging, workspace, "user-1", [staged.upload_id])
    from agentos.uploads.promotion import discard_promoted
    discard_promoted(workspace, records)
    assert not (workspace.root / "uploads" / "foto.png").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/uploads/test_promotion.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.uploads.promotion'`

- [ ] **Step 3: Write the implementation**

`src/agentos/uploads/promotion.py`:

```python
"""Move staged uploads into the conversation's own workspace.

Promotion happens before the turn row exists, because the publisher can hand
the turn to a worker within milliseconds of it being created and the worker has
to find the file on disk. If turn creation then fails, ``discard_promoted``
removes exactly what this call moved.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from agentos.agentic.workspace import ConversationWorkspace

from .media import MAX_FILES_PER_MESSAGE, MAX_TURN_BYTES, UploadRejected
from .staging import UploadStaging

UPLOAD_DIRECTORY = "uploads"


def _available_name(directory, filename: str) -> str:
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        stem, extension = filename, ""
    suffix = f".{extension}" if extension else ""
    candidate = filename
    index = 1
    while (directory / candidate).exists():
        index += 1
        candidate = f"{stem} ({index}){suffix}"
    return candidate


def promote_uploads(
    staging: UploadStaging,
    workspace: ConversationWorkspace,
    user_id: str,
    upload_ids: Iterable[str],
    *,
    max_files: int = MAX_FILES_PER_MESSAGE,
    max_total_bytes: int = MAX_TURN_BYTES,
) -> list[dict[str, Any]]:
    identifiers = [str(item) for item in upload_ids]
    if len(identifiers) > max_files:
        raise UploadRejected("too many files for one message")
    staged = [staging.get(user_id, item) for item in identifiers]
    if sum(item.bytes for item in staged) > max_total_bytes:
        raise UploadRejected("attachments exceed the per-turn budget")
    directory = workspace.root / UPLOAD_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    try:
        for item in staged:
            name = _available_name(directory, item.filename)
            (directory / name).write_bytes(item.path.read_bytes())
            records.append({
                "path": f"{UPLOAD_DIRECTORY}/{name}", "original_name": item.filename,
                "media_type": item.media_type, "kind": item.kind, "bytes": item.bytes,
            })
    except OSError:
        discard_promoted(workspace, records)
        raise
    for item in staged:
        staging.discard(user_id, item.upload_id)
    return records


def discard_promoted(workspace: ConversationWorkspace, records: Sequence[dict[str, Any]]) -> None:
    """Undo a promotion whose turn was never created."""
    for record in records:
        try:
            target = workspace.resolve(str(record.get("path") or ""))
        except Exception:
            continue
        try:
            target.unlink(missing_ok=True)
        except OSError:
            continue


__all__ = ["UPLOAD_DIRECTORY", "discard_promoted", "promote_uploads"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/uploads -q`
Expected: PASS, 20 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/uploads tests/unit/uploads && git commit -m "feat(uploads): promote a staged file into the conversation workspace"
```

---

### Task 4: Tabela de anexos e migration

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py` (após `conversation_tool_records`, ~linha 548)
- Create: `src/agentos/persistence/postgres/migrations/versions/0031_message_attachments.py`
- Test: `tests/unit/persistence/test_message_attachments_schema.py`

- [ ] **Step 1: Write the failing test**

```python
from agentos.persistence.postgres.schema import conversation_message_attachments, metadata, vision_model_selections


def test_attachment_table_is_registered():
    assert "conversation_message_attachments" in metadata.tables
    columns = set(conversation_message_attachments.c.keys())
    assert {"attachment_id", "message_id", "conversation_id", "user_id", "path",
            "original_name", "media_type", "kind", "bytes", "created_at"} <= columns


def test_vision_selection_table_is_registered():
    assert "vision_model_selections" in metadata.tables
    assert set(vision_model_selections.c.keys()) >= {"user_id", "provider", "model_id", "updated_at"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/persistence/test_message_attachments_schema.py -q`
Expected: FAIL com `ImportError: cannot import name 'conversation_message_attachments'`

- [ ] **Step 3: Write the implementation**

Em `src/agentos/persistence/postgres/schema.py`, logo abaixo do índice de
`conversation_tool_records`:

```python
conversation_message_attachments = Table(
    "conversation_message_attachments", metadata,
    Column("id", Integer, primary_key=True), Column("attachment_id", String(255), nullable=False, unique=True),
    Column("message_id", String(255), nullable=False), Column("conversation_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False), Column("path", String(1024), nullable=False),
    Column("original_name", String(255), nullable=False), Column("media_type", String(128), nullable=False),
    Column("kind", String(16), nullable=False), Column("bytes", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_conversation_message_attachments_message", conversation_message_attachments.c.message_id)

vision_model_selections = Table(
    "vision_model_selections", metadata,
    Column("id", Integer, primary_key=True), Column("user_id", String(255), nullable=False, unique=True),
    Column("provider", String(32), nullable=False), Column("model_id", String(512), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
```

`src/agentos/persistence/postgres/migrations/versions/0031_message_attachments.py`:

```python
"""Persist message attachments and the visual-reading model selection."""
from alembic import op
import sqlalchemy as sa

revision = "0031_message_attachments"
down_revision = "0030_workspace_roots"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "conversation_message_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attachment_id", sa.String(255), nullable=False, unique=True),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_message_attachments_message", "conversation_message_attachments", ["message_id"])
    op.create_table(
        "vision_model_selections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False, unique=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(512), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("vision_model_selections")
    op.drop_index("ix_conversation_message_attachments_message", table_name="conversation_message_attachments")
    op.drop_table("conversation_message_attachments")
```

Confirme que `0030_workspace_roots` é mesmo a cabeça atual:

```bash
grep -rn "down_revision" src/agentos/persistence/postgres/migrations/versions/0030_workspace_roots.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/persistence/test_message_attachments_schema.py -q`
Expected: PASS, 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/persistence tests/unit/persistence && git commit -m "feat(persistence): store message attachments and the visual-reading model"
```

---

### Task 5: Anexos no store de conversas

**Files:**
- Modify: `src/agentos/conversations/chat.py:189-223` (`create`), `:229-255` (`get`), `:330-332` (`history_for_turn`), `:514-524` (`ChatApplication`)
- Test: `tests/unit/conversations/test_chat_attachments.py`

Use o teste existente `tests/unit/conversations/` como referência para montar o
store; se não houver fábrica compartilhada, monte um engine SQLite em memória
com `metadata.create_all`.

- [ ] **Step 1: Write the failing test**

```python
from sqlalchemy import create_engine

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata

ATTACHMENT = {
    "path": "uploads/nota.pdf", "original_name": "nota.pdf",
    "media_type": "application/pdf", "kind": "pdf", "bytes": 2048,
}


def _store():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def test_create_accepts_a_blank_message_when_a_file_is_attached():
    store = _store()
    receipt = store.create(
        user_id="user-1", message="", provider="anthropic", model_id="m",
        idempotency_key="k1", attachments=[ATTACHMENT],
    )
    assert receipt.title == "nota.pdf"


def test_create_still_rejects_a_blank_message_without_attachments():
    store = _store()
    try:
        store.create(user_id="user-1", message="   ", provider="anthropic", model_id="m", idempotency_key="k1")
    except ValueError:
        return
    raise AssertionError("a blank message with no attachment must be rejected")


def test_snapshot_exposes_the_attachments_of_the_user_message():
    store = _store()
    receipt = store.create(
        user_id="user-1", message="veja isto", provider="anthropic", model_id="m",
        idempotency_key="k1", attachments=[ATTACHMENT],
    )
    snapshot = store.get(receipt.conversation_id, "user-1")
    user_message = snapshot["messages"][0]
    assert user_message["attachments"] == [{
        "path": "uploads/nota.pdf", "original_name": "nota.pdf",
        "media_type": "application/pdf", "kind": "pdf", "bytes": 2048,
    }]
    assert snapshot["messages"][1]["attachments"] == []


def test_history_marks_the_attachment_for_the_model():
    store = _store()
    receipt = store.create(
        user_id="user-1", message="veja isto", provider="anthropic", model_id="m",
        idempotency_key="k1", attachments=[ATTACHMENT],
    )
    turn = store.claim(receipt.turn_id)
    history = store.history_for_turn(turn)
    assert history[0]["role"] == "user"
    assert "veja isto" in history[0]["content"]
    assert "uploads/nota.pdf" in history[0]["content"]
    assert "view_file" in history[0]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/conversations/test_chat_attachments.py -q`
Expected: FAIL com `TypeError: create() got an unexpected keyword argument 'attachments'`

- [ ] **Step 3: Write the implementation**

Em `src/agentos/conversations/chat.py`, importe a tabela nova no import de
schema (`conversation_message_attachments`) e acrescente estes helpers de
módulo, ao lado de `_title`:

```python
def _attachment_label(record: Mapping[str, object]) -> str:
    kinds = {"image": "imagem", "pdf": "PDF", "office": "documento", "text": "texto"}
    kind = kinds.get(str(record.get("kind") or ""), "arquivo")
    size = int(record.get("bytes") or 0)
    return f"{record.get('path')} ({kind}, {max(1, round(size / 1024))} KB)"


def _attachment_marker(records: Sequence[Mapping[str, object]]) -> str:
    """The line appended to a user message so the model knows the files exist."""
    listed = ", ".join(_attachment_label(record) for record in records)
    return (
        f"\n\n[anexos enviados pela pessoa: {listed}]\n"
        "Use view_file(path=\"…\") para ler o conteúdo de um anexo visual, "
        "ou read_file para texto."
    )
```

Acrescente `Sequence` ao import de `collections.abc`.

Em `create`, troque a assinatura e a validação:

```python
    def create(self, *, user_id: str, message: str, provider: str, model_id: str, idempotency_key: str, conversation_id: str | None = None, project_id: str | None = None, attachments: Sequence[Mapping[str, object]] = (), new_conversation_id: str | None = None) -> ChatReceipt:
        message = message.strip()
        attachments = list(attachments)
        if len(message) > 16000: raise ValueError("message must be a bounded non-blank string")
        if not message and not attachments: raise ValueError("message must be a bounded non-blank string")
        title_source = message or str(attachments[0].get("original_name") or "Arquivo enviado")
```

Troque `conversation_id = _id("chat")` por
`conversation_id = new_conversation_id or _id("chat")`, e todas as chamadas
`_title(message)` por `_title(title_source)` (duas: no insert de `conversations`
e no `ChatReceipt` final).

Logo após o `c.execute(insert(conversation_messages), [...])`, insira os anexos:

```python
            if attachments:
                c.execute(insert(conversation_message_attachments), [{
                    "attachment_id": _id("att"), "message_id": user_message_id,
                    "conversation_id": conversation_id, "user_id": user_id,
                    "path": str(item["path"]), "original_name": str(item["original_name"]),
                    "media_type": str(item["media_type"]), "kind": str(item["kind"]),
                    "bytes": int(item["bytes"]), "created_at": now,
                } for item in attachments])
```

Em `get`, carregue os anexos e inclua-os na projeção das mensagens. Dentro do
`with self._engine.connect() as c:`, após a leitura de `turns`:

```python
            attachment_rows = c.execute(select(conversation_message_attachments).where(conversation_message_attachments.c.conversation_id == conversation_id).order_by(conversation_message_attachments.c.id)).mappings().all()
        attachments_by_message: dict[str, list[dict[str, object]]] = {}
        for row in attachment_rows:
            attachments_by_message.setdefault(str(row["message_id"]), []).append({
                "path": str(row["path"]), "original_name": str(row["original_name"]),
                "media_type": str(row["media_type"]), "kind": str(row["kind"]), "bytes": int(row["bytes"]),
            })
```

e no dicionário de retorno troque a projeção de `messages` por:

```python
"messages": [{"message_id": m["message_id"], "role": m["role"], "content": m["content"], "status": m["status"], "retryable": bool(m["retryable"]), "attachments": attachments_by_message.get(str(m["message_id"]), [])} for m in messages],
```

Em `history_for_turn`, junte o marcador:

```python
    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        with self._engine.connect() as c:
            rows = c.execute(select(conversation_messages.c.message_id, conversation_messages.c.role, conversation_messages.c.content).where(conversation_messages.c.conversation_id == turn["conversation_id"], conversation_messages.c.sequence <= select(conversation_messages.c.sequence).where(conversation_messages.c.message_id == turn["user_message_id"]).scalar_subquery()).order_by(conversation_messages.c.sequence)).mappings().all()
            attachment_rows = c.execute(select(conversation_message_attachments).where(conversation_message_attachments.c.conversation_id == turn["conversation_id"]).order_by(conversation_message_attachments.c.id)).mappings().all()
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in attachment_rows:
            grouped.setdefault(str(row["message_id"]), []).append(dict(row))
        history: list[dict[str, str]] = []
        for row in rows:
            content = str(row["content"])
            records = grouped.get(str(row["message_id"]), [])
            if records:
                content = f"{content}{_attachment_marker(records)}"
            history.append({"role": str(row["role"]), "content": content})
        return history
```

Em `ChatApplication`, repasse os anexos e exponha a alocação de id:

```python
    def allocate_conversation_id(self) -> str:
        return _id("chat")

    def create(self, context, *, message: str, provider: str, model_id: str, workspace_id: str | None, idempotency_key: str, project_id: str | None = None, attachments=(), new_conversation_id: str | None = None):
        receipt = self.store.create(user_id=context.user_id, message=message, provider=provider, model_id=model_id, idempotency_key=idempotency_key, project_id=project_id, attachments=attachments, new_conversation_id=new_conversation_id)
        self._ensure_execution(receipt, context.user_id, workspace_id, idempotency_key)
        return receipt

    def send(self, user_id: str, conversation_id: str, message: str, idempotency_key: str, attachments=()):
        receipt = self.store.create(user_id=user_id, message=message, provider="", model_id="", idempotency_key=idempotency_key, conversation_id=conversation_id, attachments=attachments)
        self._ensure_execution(receipt, user_id, None, idempotency_key)
        return receipt
```

Atualize `ConversationApplication` em `src/agentos/api/contracts.py` para as
novas assinaturas:

```python
class ConversationApplication(Protocol):
    def allocate_conversation_id(self) -> str: ...
    def create(self, context: object, *, message: str, provider: str, model_id: str, workspace_id: str | None, idempotency_key: str, attachments: object = (), new_conversation_id: str | None = None) -> object: ...
    def send(self, user_id: str, conversation_id: str, message: str, idempotency_key: str, attachments: object = ()) -> object: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/conversations -q`
Expected: PASS, incluindo os 4 testes novos e os existentes

- [ ] **Step 5: Commit**

```bash
git add src/agentos/conversations src/agentos/api/contracts.py tests/unit/conversations && git commit -m "feat(conversations): persist and replay message attachments"
```

---

### Task 6: Rotas de upload e anexo no turno

**Files:**
- Modify: `src/agentos/api/gateway.py` (modelos de request ~linha 106; `ApiServices.__init__` ~linha 171; rotas)
- Modify: `pyproject.toml` (dependência `python-multipart`)
- Modify: `src/agentos/bootstrap/production.py` (injetar o staging)
- Test: `tests/unit/api/test_upload_routes.py`

- [ ] **Step 1: Write the failing test**

Use o helper de app que os testes de `tests/unit/api/` já usam (procure por
`create_app(` em `tests/unit/api/` e reaproveite a fábrica local de serviços).

```python
from fastapi.testclient import TestClient

from agentos.api.gateway import ApiServices, create_app
from agentos.uploads.staging import UploadStaging

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _client(tmp_path):
    services = ApiServices(uploads=UploadStaging(tmp_path / "staging"), workspace_root=tmp_path / "workspaces")
    return TestClient(create_app(services))


def test_upload_returns_the_described_file(tmp_path):
    response = _client(tmp_path).post("/v1/uploads", files={"file": ("foto.png", PNG, "image/png")})
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "foto.png" and body["kind"] == "image" and body["bytes"] == len(PNG)
    assert body["upload_id"].startswith("upl_")


def test_upload_refuses_a_rejected_type(tmp_path):
    response = _client(tmp_path).post("/v1/uploads", files={"file": ("setup.exe", b"MZ\x90\x00" + b"\x00" * 32, "application/octet-stream")})
    assert response.status_code == 422


def test_delete_upload_removes_it(tmp_path):
    client = _client(tmp_path)
    upload_id = client.post("/v1/uploads", files={"file": ("foto.png", PNG, "image/png")}).json()["upload_id"]
    assert client.delete(f"/v1/uploads/{upload_id}").status_code == 204
    assert client.delete(f"/v1/uploads/{upload_id}").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/api/test_upload_routes.py -q`
Expected: FAIL com `TypeError: ApiServices.__init__() got an unexpected keyword argument 'uploads'`

- [ ] **Step 3: Write the implementation**

Adicione `"python-multipart>=0.0.9,<1"` às dependências em `pyproject.toml` e
instale com `uv sync` (ou `pip install python-multipart`).

Em `gateway.py`, importe `File`, `UploadFile` de `fastapi` e o pacote de upload:

```python
from fastapi import FastAPI, File, Request, UploadFile
from agentos.uploads.media import MAX_FILES_PER_MESSAGE, MAX_UPLOAD_BYTES, UploadRejected
from agentos.uploads.promotion import discard_promoted, promote_uploads
```

Acrescente `uploads: object | None = None` ao `ApiServices.__init__` e
`self.uploads = uploads`.

Acrescente o handler de erro junto dos demais:

```python
    @app.exception_handler(UploadRejected)
    async def upload_rejected(_: Request, __: UploadRejected) -> JSONResponse:
        return _error(422, "VALIDATION", "upload_rejected", retryable=False)
```

Campo novo nos dois request models:

```python
class CreateConversationRequest(_RequestModel):
    message: str = Field(default="", max_length=16000)
    selection: ConversationSelectionRequest
    workspace_id: str | None = Field(default=None, min_length=1, max_length=255)
    attachments: list[str] = Field(default_factory=list, max_length=MAX_FILES_PER_MESSAGE)
```

Faça a mesma adição de `attachments` em `SendConversationMessageRequest` e
troque seu `message` para `Field(default="", max_length=16000)`.

Rotas novas, logo antes de `@app.post("/v1/conversations")`:

```python
    @app.post("/v1/uploads", status_code=201)
    async def create_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=None, purpose="conversation.upload")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        staged = require_port(services.uploads).store(principal.user_id, file.filename or "arquivo", data)  # type: ignore[union-attr]
        return JSONResponse({
            "upload_id": staged.upload_id, "filename": staged.filename,
            "media_type": staged.media_type, "kind": staged.kind, "bytes": staged.bytes,
        }, status_code=201)

    @app.delete("/v1/uploads/{upload_id}", status_code=204)
    async def delete_upload(upload_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=None, purpose="conversation.upload")
        if not require_port(services.uploads).discard(principal.user_id, upload_id):  # type: ignore[union-attr]
            raise ApplicationNotFoundError(upload_id)
        return JSONResponse(status_code=204, content=None)
```

Helper de promoção, ao lado de `conversation_workspace`:

```python
    def workspace_for(workspace_id: str, principal: AuthenticatedPrincipal) -> ConversationWorkspace:
        return resolve_workspace(workspace_id, managed_root=services.workspace_root, local_root=local_root_for(workspace_id, principal))

    def promote(workspace_id: str, principal: AuthenticatedPrincipal, upload_ids: list[str]) -> list[dict[str, object]]:
        if not upload_ids:
            return []
        return promote_uploads(require_port(services.uploads), workspace_for(workspace_id, principal), principal.user_id, upload_ids)
```

`create_conversation` passa a alocar o id, promover e só então criar:

```python
    @app.post("/v1/conversations", status_code=201)
    async def create_conversation(payload: CreateConversationRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        provider = _provider_name(payload.selection.provider)
        services.security.check_rate_limit(principal, action="conversation.create", origin=request.headers.get("origin"))
        services.security.authorize(principal, action="conversation.create", resource_id=provider, purpose="conversation.create")
        application = require_port(services.conversation_application)
        conversation_id = application.allocate_conversation_id()  # type: ignore[union-attr]
        attachments = promote(conversation_id, principal, payload.attachments)
        try:
            result = application.create(  # type: ignore[union-attr]
                ProviderCatalogContext(principal.user_id, "conversation.create"),
                message=payload.message, provider=provider, model_id=payload.selection.model_id,
                workspace_id=payload.workspace_id, idempotency_key=_idempotency(request),
                attachments=attachments, new_conversation_id=conversation_id,
            )
        except Exception:
            discard_promoted(workspace_for(conversation_id, principal), attachments)
            raise
        data = _jsonable(result)
        if not isinstance(data, dict):
            raise ValueError("conversation response is invalid")
        return JSONResponse({key: data.get(key) for key in ("conversation_id", "title", "turn_id", "message_id", "state")}, status_code=201)
```

`send_conversation_message` resolve o workspace efetivo (que pode ser o do
projeto ou uma pasta local) antes de promover:

```python
    @app.post("/v1/conversations/{conversation_id}/messages", status_code=201)
    async def send_conversation_message(conversation_id: str, payload: SendConversationMessageRequest, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(principal, action="conversation.send", resource_id=conversation_id, purpose="conversation.send")
        attachments: list[dict[str, object]] = []
        workspace_id = conversation_id
        if payload.attachments:
            workspace_id, _ = effective_workspace_id(conversation_record(conversation_id, principal), principal)
            attachments = promote(workspace_id, principal, payload.attachments)
        try:
            result = require_port(services.conversation_application).send(principal.user_id, conversation_id, payload.message, _idempotency(request), attachments=attachments)  # type: ignore[union-attr]
        except Exception:
            discard_promoted(workspace_for(workspace_id, principal), attachments)
            raise
        return JSONResponse(_jsonable(result), status_code=201)
```

Aplique a mesma promoção em `create_project_conversation` (linha ~383), usando
o `workspace_id` do projeto obtido por `require_port(services.projects).get(...)`.

Em `src/agentos/bootstrap/production.py`, construa
`UploadStaging(orin_paths().data / "uploads" / "staging")` e passe como
`uploads=` ao `ApiServices`. Chame `staging.purge()` uma vez nessa mesma
construção, dentro de um `try/except Exception`: é o que impede o staging de
crescer para sempre, e uma falha de limpeza nunca pode impedir a API de subir.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/api -q`
Expected: PASS, incluindo os 3 testes novos

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api src/agentos/bootstrap pyproject.toml tests/unit/api && git commit -m "feat(api): accept file uploads and attach them to a turn"
```

---

### Task 7: Cliente HTTP de upload no frontend

**Files:**
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/api/uploads.ts`
- Modify: `frontend/src/api/conversations.ts:26-54`
- Test: `frontend/tests/unit/uploads.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { deleteUpload, uploadFile } from '../../src/api/uploads'

function clientWith(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response)
  const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: fetchMock as unknown as typeof fetch })
  return { client, fetchMock }
}

describe('uploads api', () => {
  it('posts multipart form data without a JSON content type', async () => {
    const body = { upload_id: 'upl_1', filename: 'foto.png', media_type: 'image/png', kind: 'image', bytes: 40 }
    const { client, fetchMock } = clientWith(new Response(JSON.stringify(body), { status: 201 }))
    const result = await uploadFile(client, new File([new Uint8Array([1, 2, 3])], 'foto.png', { type: 'image/png' }))
    expect(result).toEqual(body)
    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(request.body).toBeInstanceOf(FormData)
    expect(new Headers(request.headers).get('Content-Type')).toBeNull()
  })

  it('deletes an upload', async () => {
    const { client, fetchMock } = clientWith(new Response(null, { status: 204 }))
    await deleteUpload(client, 'upl_1')
    expect(fetchMock.mock.calls[0][0]).toContain('/v1/uploads/upl_1')
  })
})
```

Ajuste a construção de `ApiClient` ao construtor real — leia
`frontend/src/api/client.ts` e `frontend/tests/unit/apiClient.test.ts` e use a
mesma forma de injetar `fetch`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/uploads.test.ts`
Expected: FAIL com "Cannot find module '../../src/api/uploads'"

- [ ] **Step 3: Write the implementation**

Em `client.ts`, acrescente ao `ApiClient` um método que não define
`Content-Type` (o browser precisa gerar o boundary):

```ts
  async upload<T>(options: { path: string; body: FormData; expectedStatus?: number; parse: (value: unknown) => T }): Promise<T> {
    const headers = new Headers({ Accept: 'application/json' })
    if (this.bearerToken) headers.set('Authorization', `Bearer ${this.bearerToken}`)
    if (this.csrfToken) headers.set('X-CSRF-Token', this.csrfToken)
    const response = await this.fetchImpl(`${this.baseUrl}${options.path}`, { method: 'POST', headers, body: options.body, credentials: 'include' })
    if (response.status !== (options.expectedStatus ?? 201)) throw await parseApiErrorResponse(response)
    return options.parse(await response.json())
  }
```

Use exatamente os mesmos nomes de campo privado (`this.fetchImpl`,
`this.baseUrl`, `this.bearerToken`, `this.csrfToken`) e a mesma função de erro
que `request()` já usa nesse arquivo.

`frontend/src/api/uploads.ts`:

```ts
import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type UploadedFile = {
  upload_id: string
  filename: string
  media_type: string
  kind: 'text' | 'image' | 'pdf' | 'office'
  bytes: number
}

export function uploadFile(client: ApiClient, file: File): Promise<UploadedFile> {
  const body = new FormData()
  body.append('file', file, file.name)
  return client.upload({ path: '/v1/uploads', body, expectedStatus: 201, parse: parseUploadedFile })
}

export function deleteUpload(client: ApiClient, uploadId: string): Promise<void> {
  return client.request({ path: `/v1/uploads/${encodeURIComponent(uploadId)}`, method: 'DELETE', expectedStatus: 204, parse: () => undefined })
}

function parseUploadedFile(value: unknown): UploadedFile {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  const kind = String(data.kind)
  if (kind !== 'text' && kind !== 'image' && kind !== 'pdf' && kind !== 'office') throw invalidResponseError()
  return {
    upload_id: String(data.upload_id), filename: String(data.filename),
    media_type: String(data.media_type), kind, bytes: Number(data.bytes),
  }
}
```

Em `conversations.ts`, adicione `attachments?: string[]` a
`CreateConversationInput`, mande `attachments: input.attachments ?? []` no body
de `createConversation`, acrescente o parâmetro `attachments: string[] = []` a
`sendConversationMessage` e inclua-o no body. Acrescente
`attachments: MessageAttachment[]` a `ConversationMessage` e o tipo:

```ts
export type MessageAttachment = { path: string; original_name: string; media_type: string; kind: string; bytes: number }
```

No `parseConversation`, leia `attachments` de cada mensagem com fallback `[]`
para snapshots antigos.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/uploads.test.ts`
Expected: PASS, 2 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api frontend/tests/unit/uploads.test.ts && git commit -m "feat(web): call the upload routes and carry attachments on a turn"
```

---

### Task 8: Chips de anexo no composer

**Files:**
- Create: `frontend/src/features/conversations/AttachmentChips.tsx`
- Modify: `frontend/src/features/conversations/Composer.tsx`
- Test: `frontend/tests/unit/AttachmentChips.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AttachmentChips, type ComposerAttachment } from '../../src/features/conversations/AttachmentChips'
import { Composer } from '../../src/features/conversations/Composer'

const ready: ComposerAttachment = { id: 'a1', filename: 'foto.png', kind: 'image', bytes: 2048, state: 'ready', upload_id: 'upl_1' }

describe('AttachmentChips', () => {
  it('lists each attachment with its name and size', () => {
    render(<AttachmentChips items={[ready]} onRemove={() => {}} />)
    expect(screen.getByText('foto.png')).toBeInTheDocument()
    expect(screen.getByText('2 KB')).toBeInTheDocument()
  })

  it('removes an attachment', () => {
    const onRemove = vi.fn()
    render(<AttachmentChips items={[ready]} onRemove={onRemove} />)
    fireEvent.click(screen.getByRole('button', { name: 'Remover foto.png' }))
    expect(onRemove).toHaveBeenCalledWith('a1')
  })

  it('shows the failure of one file without hiding the others', () => {
    const failed: ComposerAttachment = { id: 'a2', filename: 'setup.exe', kind: 'text', bytes: 10, state: 'failed', error: 'Tipo não aceito' }
    render(<AttachmentChips items={[ready, failed]} onRemove={() => {}} />)
    expect(screen.getByText('Tipo não aceito')).toBeInTheDocument()
    expect(screen.getByText('foto.png')).toBeInTheDocument()
  })
})

describe('Composer with attachments', () => {
  it('allows sending with no text when a file is attached', () => {
    const onSubmit = vi.fn()
    render(<Composer value="" onChange={() => {}} onSubmit={onSubmit} attachments={[ready]} onAttach={() => {}} onRemoveAttachment={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Enviar mensagem' }))
    expect(onSubmit).toHaveBeenCalled()
  })

  it('still refuses to send an empty composer', () => {
    const onSubmit = vi.fn()
    render(<Composer value="   " onChange={() => {}} onSubmit={onSubmit} attachments={[]} onAttach={() => {}} onRemoveAttachment={() => {}} />)
    expect(screen.getByRole('button', { name: 'Enviar mensagem' })).toBeDisabled()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('attaches an image pasted from the clipboard', () => {
    const onAttach = vi.fn()
    render(<Composer value="" onChange={() => {}} onSubmit={() => {}} attachments={[]} onAttach={onAttach} onRemoveAttachment={() => {}} />)
    const file = new File([new Uint8Array([1])], 'print.png', { type: 'image/png' })
    fireEvent.paste(screen.getByLabelText('Mensagem'), { clipboardData: { files: [file], items: [] } })
    expect(onAttach).toHaveBeenCalledWith([file])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/AttachmentChips.test.tsx`
Expected: FAIL com "Cannot find module '.../AttachmentChips'"

- [ ] **Step 3: Write the implementation**

`frontend/src/features/conversations/AttachmentChips.tsx`:

```tsx
export type ComposerAttachment = {
  /** Client-side identity, stable from the moment the file is picked. */
  id: string
  filename: string
  kind: 'text' | 'image' | 'pdf' | 'office'
  bytes: number
  state: 'uploading' | 'ready' | 'failed'
  upload_id?: string
  previewUrl?: string
  error?: string
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const GLYPH: Record<ComposerAttachment['kind'], string> = { text: '≡', image: '▣', pdf: '❐', office: '▤' }

/** The files waiting to be sent, shown under the composer input. */
export function AttachmentChips({ items, onRemove }: { items: ComposerAttachment[]; onRemove: (id: string) => void }) {
  if (items.length === 0) return null
  return (
    <ul className="composer__attachments">
      {items.map((item) => (
        <li key={item.id} className={`attachment-chip attachment-chip--${item.state}`}>
          {item.previewUrl
            ? <img className="attachment-chip__thumb" src={item.previewUrl} alt="" />
            : <span className="attachment-chip__glyph" aria-hidden="true">{GLYPH[item.kind]}</span>}
          <span className="attachment-chip__name">{item.filename}</span>
          <span className="attachment-chip__size">{item.state === 'failed' ? item.error : formatBytes(item.bytes)}</span>
          <button type="button" className="attachment-chip__remove" aria-label={`Remover ${item.filename}`} onClick={() => onRemove(item.id)}>×</button>
        </li>
      ))}
    </ul>
  )
}
```

Em `Composer.tsx`: acrescente `attachments`, `onAttach` e `onRemoveAttachment`
às props (com defaults `[]` e no-ops), importe `AttachmentChips`, e:

- troque a guarda de `submit` e do `onKeyDown` para
  `if (running || disabled || !canSend || (!value.trim() && attachments.length === 0)) return`;
- troque o `disabled` do botão de envio para
  `disabled || !canSend || (!value.trim() && attachments.length === 0)`;
- acrescente um `<input type="file" multiple hidden>` com `ref` e um botão
  `aria-label="Anexar arquivos"` dentro de `composer__settings` que chama
  `fileInputRef.current?.click()`;
- acrescente `onPaste` no `<textarea>` e `onDrop`/`onDragOver` no `<form>`,
  ambos chamando `onAttach(Array.from(event.clipboardData?.files ?? event.dataTransfer.files))`
  quando houver ao menos um arquivo, com `event.preventDefault()`;
- renderize `<AttachmentChips items={attachments} onRemove={onRemoveAttachment} />`
  logo acima de `composer__bar`.

Acrescente estilos para `.composer__attachments`, `.attachment-chip` e seus
modificadores em `frontend/src/styles/`, seguindo o arquivo onde `.composer__bar`
já é estilizado.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/AttachmentChips.test.tsx`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/conversations frontend/src/styles frontend/tests/unit/AttachmentChips.test.tsx && git commit -m "feat(web): attach files from the composer"
```

---

### Task 9: Estado dos anexos no ChatPage e anexos na mensagem

**Files:**
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Create: `frontend/src/features/conversations/MessageAttachments.tsx`
- Test: `frontend/tests/unit/MessageAttachments.test.tsx`, `frontend/tests/unit/ChatPage.test.tsx`

- [ ] **Step 1: Write the failing test**

`frontend/tests/unit/MessageAttachments.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MessageAttachments } from '../../src/features/conversations/MessageAttachments'

describe('MessageAttachments', () => {
  it('renders one card per attachment', () => {
    render(<MessageAttachments conversationId="chat_1" items={[
      { path: 'uploads/nota.pdf', original_name: 'nota.pdf', media_type: 'application/pdf', kind: 'pdf', bytes: 2048 },
    ]} />)
    expect(screen.getByText('nota.pdf')).toBeInTheDocument()
  })

  it('renders nothing when there are no attachments', () => {
    const { container } = render(<MessageAttachments conversationId="chat_1" items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

No `ChatPage.test.tsx`, acrescente um caso que envia só arquivo. Leia o arquivo
de teste existente e siga o mesmo mock de API que ele já usa:

```tsx
it('sends a turn with an attachment and no text', async () => {
  // Monte a página como os testes vizinhos fazem, escolha um arquivo pelo input
  // "Anexar arquivos" e confirme que a chamada de criação recebeu
  // attachments: ['upl_1'] e message: ''.
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/MessageAttachments.test.tsx`
Expected: FAIL com "Cannot find module '.../MessageAttachments'"

- [ ] **Step 3: Write the implementation**

`frontend/src/features/conversations/MessageAttachments.tsx`:

```tsx
import type { MessageAttachment } from '../../api/conversations'
import { WorkspaceFileCard } from './WorkspaceFileCard'

/** The files a person attached, shown under their own message. */
export function MessageAttachments({ conversationId, items }: { conversationId: string; items: MessageAttachment[] }) {
  if (items.length === 0) return null
  return (
    <div className="message-attachments">
      {items.map((item) => (
        <WorkspaceFileCard key={item.path} conversationId={conversationId} path={item.path} label={item.original_name} />
      ))}
    </div>
  )
}
```

Leia `WorkspaceFileCard.tsx` e use exatamente as props que ele já declara; se o
nome do rótulo for outro, ajuste a chamada em vez de mudar o componente.

Em `ChatPage.tsx`:

- estado `const [attachments, setAttachments] = useState<ComposerAttachment[]>([])`;
- `onAttach(files)`: para cada arquivo, insere um chip `uploading` com
  `crypto.randomUUID()`, `URL.createObjectURL(file)` quando `file.type` começa
  com `image/`, chama `uploadFile` e move o chip para `ready` com o `upload_id`
  ou para `failed` com a mensagem de erro;
- `onRemoveAttachment(id)`: chama `deleteUpload` quando já existe `upload_id`,
  revoga o object URL e remove o chip do estado;
- no envio, passa `attachments.filter((item) => item.state === 'ready').map((item) => item.upload_id!)`
  e limpa o estado ao receber o recibo;
- o envio fica bloqueado enquanto algum chip está `uploading`
  (passe `canSend={attachments.every((item) => item.state !== 'uploading')}`);
- renderiza `<MessageAttachments conversationId={id} items={message.attachments} />`
  dentro da bolha de mensagem do usuário.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run` e `npm run lint`
Expected: PASS em toda a suíte

- [ ] **Step 5: Commit**

```bash
git add frontend/src frontend/tests && git commit -m "feat(web): upload attachments and render them on the message"
```

---

# Fase 2 — Leitura de texto nativo

### Task 10: Extração de texto de PDF e Office

**Files:**
- Create: `src/agentos/reading/extract.py`, `src/agentos/reading/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/reading/test_extract.py`

- [ ] **Step 1: Write the failing test**

Crie `tests/unit/reading/__init__.py` vazio e `tests/unit/reading/test_extract.py`:

```python
from pathlib import Path

import pytest

from agentos.reading.extract import ExtractedText, extract_text


def test_plain_text_is_returned_as_is(tmp_path: Path):
    target = tmp_path / "notas.md"
    target.write_text("# Título\n\nCorpo", encoding="utf-8")
    result = extract_text(target, "text/markdown")
    assert isinstance(result, ExtractedText)
    assert "Título" in result.text
    assert result.pages_without_text == ()


def test_text_is_truncated_at_the_limit(tmp_path: Path):
    target = tmp_path / "grande.txt"
    target.write_text("a" * 60_000, encoding="utf-8")
    result = extract_text(target, "text/plain", max_chars=1000)
    assert len(result.text) == 1000 and result.truncated is True


def test_docx_text_is_extracted(tmp_path: Path):
    docx = pytest.importorskip("docx")
    target = tmp_path / "carta.docx"
    document = docx.Document()
    document.add_paragraph("Prezado cliente")
    document.save(target)
    result = extract_text(target, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "Prezado cliente" in result.text


def test_xlsx_cells_are_extracted(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    target = tmp_path / "planilha.xlsx"
    book = openpyxl.Workbook()
    book.active["A1"] = "Receita"
    book.active["B1"] = 1500
    book.save(target)
    result = extract_text(target, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "Receita" in result.text and "1500" in result.text


def test_pdf_without_a_text_layer_reports_its_pages(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    target = tmp_path / "escaneado.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    with target.open("wb") as handle:
        writer.write(handle)
    result = extract_text(target, "application/pdf")
    assert result.pages_without_text == (1, 2)
    assert result.text.strip() == ""


def test_an_unsupported_type_raises(tmp_path: Path):
    target = tmp_path / "foto.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError):
        extract_text(target, "image/png")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/reading/test_extract.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.reading'`

- [ ] **Step 3: Write the implementation**

Acrescente a `pyproject.toml`: `"pypdf>=5,<7"`, `"python-docx>=1.1,<2"`,
`"openpyxl>=3.1,<4"`, `"python-pptx>=1,<2"`. Instale-as.

`src/agentos/reading/extract.py`:

```python
"""Native text extraction — no model is involved here.

Most real PDFs carry a text layer, and every Office document does. Reading them
directly is both free and better than any transcription, so the visual path is
only ever reached for what genuinely has no text.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_EXTRACTED_CHARS = 40_000
MAX_PDF_PAGES = 200


@dataclass(frozen=True, slots=True)
class ExtractedText:
    text: str
    truncated: bool = False
    # 1-based page numbers of a PDF whose page carried no text layer.
    pages_without_text: tuple[int, ...] = ()


def _bounded(value: str, limit: int) -> tuple[str, bool]:
    return (value[:limit], True) if len(value) > limit else (value, False)


def _pdf(path: Path, limit: int) -> ExtractedText:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    empty: list[int] = []
    for number, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        try:
            content = page.extract_text() or ""
        except Exception:
            content = ""
        if content.strip():
            parts.append(f"[página {number}]\n{content.strip()}")
        else:
            empty.append(number)
    text, truncated = _bounded("\n\n".join(parts), limit)
    return ExtractedText(text, truncated, tuple(empty))


def _docx(path: Path, limit: int) -> ExtractedText:
    from docx import Document

    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    text, truncated = _bounded("\n".join(parts), limit)
    return ExtractedText(text, truncated)


def _xlsx(path: Path, limit: int) -> ExtractedText:
    from openpyxl import load_workbook

    book = load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in book.worksheets:
        parts.append(f"[planilha {sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if value is None else str(value) for value in row]
            if any(cell.strip() for cell in cells):
                parts.append(" | ".join(cells))
    book.close()
    text, truncated = _bounded("\n".join(parts), limit)
    return ExtractedText(text, truncated)


def _pptx(path: Path, limit: int) -> ExtractedText:
    from pptx import Presentation

    presentation = Presentation(str(path))
    parts: list[str] = []
    for number, slide in enumerate(presentation.slides, start=1):
        parts.append(f"[slide {number}]")
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    text, truncated = _bounded("\n".join(parts), limit)
    return ExtractedText(text, truncated)


def _plain(path: Path, limit: int) -> ExtractedText:
    data = path.read_bytes()[: limit * 4 + 1]
    text, truncated = _bounded(data.decode("utf-8", "replace"), limit)
    return ExtractedText(text, truncated)


_HANDLERS = {
    "application/pdf": _pdf,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _docx,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _xlsx,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": _pptx,
}


def extract_text(path: Path, media_type: str, *, max_chars: int = MAX_EXTRACTED_CHARS) -> ExtractedText:
    """Extract text from a document. Raises ``ValueError`` for image types."""
    handler = _HANDLERS.get(media_type)
    if handler is not None:
        return handler(path, max_chars)
    if media_type.startswith("text/") or media_type in {"application/json", "application/xml"}:
        return _plain(path, max_chars)
    raise ValueError(f"{media_type} has no text to extract")


__all__ = ["ExtractedText", "MAX_EXTRACTED_CHARS", "extract_text"]
```

`src/agentos/reading/__init__.py`:

```python
from .extract import ExtractedText, extract_text

__all__ = ["ExtractedText", "extract_text"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/reading -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/reading tests/unit/reading pyproject.toml && git commit -m "feat(reading): extract native text from pdf and office documents"
```

---

### Task 11: A ferramenta `view_file` para documentos

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py` (definições ~linha 226; métodos junto de `read_file`, ~linha 475)
- Test: `tests/unit/agentic/test_view_file_tool.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path):
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_1"), enable_terminal=False)


def test_view_file_is_declared(tmp_path):
    names = {item.name for item in _toolset(tmp_path).definitions()}
    assert "view_file" in names


def test_view_file_reads_a_text_document(tmp_path):
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "uploads").mkdir()
    (toolset.workspace.root / "uploads" / "notas.md").write_text("linha um", encoding="utf-8")
    result = toolset.view_file("uploads/notas.md")
    assert "linha um" in result["content"]
    assert result["payload"]["path"] == "uploads/notas.md"


def test_view_file_refuses_a_path_outside_the_workspace(tmp_path):
    with pytest.raises(Exception):
        _toolset(tmp_path).view_file("../../etc/passwd")


def test_view_file_reports_a_missing_file(tmp_path):
    with pytest.raises(AgentToolError):
        _toolset(tmp_path).view_file("uploads/ausente.pdf")


def test_view_file_without_a_reader_explains_the_limit_for_an_image(tmp_path):
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    result = toolset.view_file("foto.png")
    assert "leitura visual" in result["content"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/agentic/test_view_file_tool.py -q`
Expected: FAIL com `AttributeError: 'AgentToolset' object has no attribute 'view_file'`

- [ ] **Step 3: Write the implementation**

Em `agent_tools.py`, importe no topo:

```python
from agentos.reading.extract import extract_text
from .file_preview import media_type_for
```

Acrescente a definição na lista de `_build_definitions`, logo depois de
`read_file`:

```python
            ToolDefinition(
                "view_file",
                "Read a document or an image from the conversation workspace: PDF, Word, Excel, PowerPoint, plain text, or a picture. Use this instead of read_file whenever the file is not plain text.",
                _schema({
                    "path": {**_TEXT, "description": "Workspace-relative path, e.g. uploads/nota.pdf"},
                    "question": {**_TEXT, "description": "What you need from the file. Guides the visual reading of an image or a scanned page."},
                }, ("path",)),
                self.view_file, "filesystem", read_only=True,
            ),
```

E o método, junto de `read_file`:

```python
    def view_file(self, path: str, question: str = "") -> dict[str, Any]:
        target = self.workspace.resolve(path)
        if not target.is_file():
            raise AgentToolError(f"'{path}' is not a file in this workspace.")
        media_type = media_type_for(target)
        if media_type.startswith("image/"):
            return self._view_image(path, target, media_type, question)
        try:
            extracted = extract_text(target, media_type)
        except ValueError as error:
            raise AgentToolError(f"'{path}' ({media_type}) cannot be read as a document.") from error
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

    def _view_image(self, path: str, target, media_type: str, question: str) -> dict[str, Any]:
        return {
            "summary": f"Não foi possível ler {path}",
            "content": (
                f"'{path}' é uma imagem e este turno não tem leitura visual disponível: "
                "o modelo atual não enxerga e nenhum modelo de leitura visual está configurado."
            ),
            "payload": {"path": path, "media_type": media_type, "label": path},
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/agentic -q`
Expected: PASS, incluindo os 5 testes novos

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic tests/unit/agentic && git commit -m "feat(agentic): read documents from the workspace with view_file"
```

---

# Fase 3 — Imagem nativa para modelo com visão

### Task 12: Projeção de conteúdo por provider

**Files:**
- Create: `src/agentos/agentic/provider_content.py`
- Modify: `src/agentos/agentic/provider_stream.py` (dentro de `_anthropic_request`, `_openai_request`, `_ollama_request`)
- Test: `tests/unit/agentic/test_provider_content.py`

- [ ] **Step 1: Write the failing test**

```python
from agentos.agentic.provider_content import image_block, project_messages

IMAGE = image_block("image/png", "QUJD")


def test_anthropic_gets_a_base64_source():
    projected = project_messages([{"role": "user", "content": [{"type": "text", "text": "leia"}, IMAGE]}], "anthropic")
    assert projected[0]["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}


def test_openai_gets_a_data_uri():
    projected = project_messages([{"role": "user", "content": [{"type": "text", "text": "leia"}, IMAGE]}], "openrouter")
    assert projected[0]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}


def test_ollama_moves_images_to_their_own_field():
    projected = project_messages([{"role": "user", "content": [{"type": "text", "text": "leia"}, IMAGE]}], "ollama")
    assert projected[0] == {"role": "user", "content": "leia", "images": ["QUJD"]}


def test_a_text_only_message_is_untouched():
    messages = [{"role": "user", "content": "olá"}]
    assert project_messages(messages, "anthropic") == messages


def test_projection_does_not_mutate_the_input():
    original = [{"role": "user", "content": [IMAGE]}]
    project_messages(original, "ollama")
    assert original[0]["content"] == [IMAGE]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/agentic/test_provider_content.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.agentic.provider_content'`

- [ ] **Step 3: Write the implementation**

`src/agentos/agentic/provider_content.py`:

```python
"""One neutral image block, three provider shapes.

The runtime never learns a provider's content format: it appends
``image_block`` values and this module rewrites them at the transport edge,
which is the only place a provider name is already known.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

IMAGE = "image"


def image_block(media_type: str, data: str) -> dict[str, str]:
    """A provider-neutral image block: base64 payload plus its media type."""
    return {"type": IMAGE, "media_type": str(media_type), "data": str(data)}


def _is_image(block: object) -> bool:
    return isinstance(block, Mapping) and block.get("type") == IMAGE


def _anthropic(block: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": str(block.get("media_type") or "image/png"), "data": str(block.get("data") or "")}}


def _openai(block: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:{block.get('media_type') or 'image/png'};base64,{block.get('data') or ''}"}}


def project_messages(messages: Sequence[Mapping[str, Any]], provider: str) -> list[dict[str, Any]]:
    """Rewrite neutral image blocks into ``provider``'s own representation."""
    name = str(provider or "").lower()
    projected: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list) or not any(_is_image(block) for block in content):
            projected.append(dict(message))
            continue
        if name == "ollama":
            # The native API carries images beside the text, not inside it.
            text = " ".join(str(block.get("text") or "") for block in content if isinstance(block, Mapping) and block.get("type") == "text").strip()
            images = [str(block.get("data") or "") for block in content if _is_image(block)]
            projected.append({**{key: value for key, value in message.items() if key != "content"}, "content": text, "images": images})
            continue
        convert = _anthropic if name == "anthropic" else _openai
        projected.append({**message, "content": [convert(block) if _is_image(block) else dict(block) if isinstance(block, Mapping) else block for block in content]})
    return projected


__all__ = ["image_block", "project_messages"]
```

Em `provider_stream.py`, importe `project_messages` e aplique-o como primeira
linha de cada builder:

```python
    def _anthropic_request(self, messages: list, tools: list, tool_choice: object, requested: object):
        messages = project_messages(messages, "anthropic")
        system_items = [item for item in messages if item.get("role") == "system"]
```

```python
    def _openai_request(self, messages: list, tools: list, tool_choice: object, requested: object):
        messages = project_messages(messages, "openai")
```

```python
    def _ollama_request(self, messages: list, tools: list, tool_choice: object, requested: object):
        messages = project_messages(messages, "ollama")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/agentic -q`
Expected: PASS, incluindo os 5 testes novos e os de `provider_stream`

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic tests/unit/agentic && git commit -m "feat(agentic): project image content into each provider's format"
```

---

### Task 13: O runtime injeta a imagem devolvida por uma ferramenta

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py` (`ToolOutcome`, ~linha 77)
- Modify: `src/agentos/agentic/runtime.py:242-244` e `:590-597`
- Test: `tests/unit/agentic/test_runtime_image_injection.py`

- [ ] **Step 1: Write the failing test**

```python
from agentos.agentic.runtime import AgenticTurnRuntime

TURN = {"turn_id": "t1", "conversation_id": "c1", "provider": "anthropic", "user_id": "u1"}


def test_an_image_result_becomes_a_following_user_message():
    result = {"id": "call-1", "content": "Imagem anexada.", "images": [{"type": "image", "media_type": "image/png", "data": "QUJD"}]}
    messages = AgenticTurnRuntime._tool_result_messages(TURN, result)
    assert len(messages) == 2
    assert messages[0]["content"][0]["type"] == "tool_result"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"][-1] == {"type": "image", "media_type": "image/png", "data": "QUJD"}


def test_a_text_result_still_produces_one_message():
    messages = AgenticTurnRuntime._tool_result_messages(TURN, {"id": "call-1", "content": "ok"})
    assert len(messages) == 1


def test_the_openai_shape_is_preserved_for_the_tool_message():
    turn = {**TURN, "provider": "openrouter"}
    messages = AgenticTurnRuntime._tool_result_messages(turn, {"id": "call-1", "content": "ok", "images": [{"type": "image", "media_type": "image/png", "data": "QUJD"}]})
    assert messages[0]["role"] == "tool" and messages[0]["tool_call_id"] == "call-1"
    assert messages[1]["role"] == "user"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/agentic/test_runtime_image_injection.py -q`
Expected: FAIL com `AttributeError: type object 'AgenticTurnRuntime' has no attribute '_tool_result_messages'`

- [ ] **Step 3: Write the implementation**

Em `agent_tools.py`, acrescente o campo a `ToolOutcome`:

```python
@dataclass(slots=True)
class ToolOutcome:
    status: str
    summary: str
    content: str
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    # Provider-neutral image blocks the runtime appends to the conversation
    # after this tool's result, so a model that sees can look at them.
    images: list[dict[str, str]] = field(default_factory=list)
```

Confirme que o dicionário devolvido por `_run_toolset` repassa `images` — leia
`runtime.py:442-534` e inclua `"images": outcome.images` onde `content` já é
copiado para o resultado.

Em `runtime.py`, substitua `_tool_result_message` por:

```python
    @classmethod
    def _tool_result_messages(cls, turn: Mapping[str, object], result: Mapping[str, object]) -> list[dict[str, object]]:
        """The tool's result, plus a user message when it carried images.

        Only Anthropic accepts an image inside a tool result, so the image is
        appended as an ordinary user message instead: that is understood by
        every provider this runtime speaks to.
        """
        content = str(result["content"]) if "content" in result else cls._redacted_result(result)
        if str(turn.get("provider", "")).lower() == "anthropic":
            messages: list[dict[str, object]] = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(result.get("id", "")), "content": content}]}]
        else:
            messages = [{"role": "tool", "tool_call_id": str(result.get("id", "")), "content": content}]
        images = [dict(item) for item in (result.get("images") or ()) if isinstance(item, Mapping)]
        if images:
            messages.append({"role": "user", "content": [{"type": "text", "text": "Conteúdo visual do arquivo solicitado:"}, *images]})
        return messages
```

E na linha 243 troque:

```python
                for result in results:
                    messages.extend(self._tool_result_message(turn, result))
```

por:

```python
                for result in results:
                    messages.extend(self._tool_result_messages(turn, result))
```

`_age_tool_results` continua igual: a mensagem de imagem tem `role: "user"` e
não é um resultado de ferramenta, então não é comprimida.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/agentic tests/unit/runtime -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic tests/unit/agentic && git commit -m "feat(agentic): hand a tool's image back to a model that can see"
```

---

### Task 14: Capacidades do modelo chegam à sessão e ao toolset

**Files:**
- Modify: `src/agentos/workers/chat.py:200-219` (`_context_window_for` e vizinhos)
- Modify: `src/agentos/agentic/session.py` (construtor de `TurnSession` e `_toolset`)
- Modify: `src/agentos/agentic/agent_tools.py` (`AgentToolset.__init__`)
- Test: `tests/unit/workers/test_model_capabilities.py`

- [ ] **Step 1: Write the failing test**

```python
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


def test_toolset_reports_whether_the_turn_model_sees(tmp_path):
    workspace = ConversationWorkspace(tmp_path, "chat_1")
    assert AgentToolset(workspace, model_sees_images=True).model_sees_images is True
    assert AgentToolset(workspace).model_sees_images is False


def test_view_file_returns_the_image_when_the_model_sees(tmp_path):
    workspace = ConversationWorkspace(tmp_path, "chat_1")
    toolset = AgentToolset(workspace, model_sees_images=True, enable_terminal=False)
    (workspace.root / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    result = toolset.view_file("foto.png")
    assert result["images"][0]["type"] == "image"
    assert result["images"][0]["media_type"] == "image/png"
```

Acrescente também, em `tests/unit/workers/`, um teste do lookup do catálogo
seguindo o padrão do teste existente de `_context_window_for` (procure por
`context_window` em `tests/unit/workers/`) e verificando que
`_model_capabilities_for` devolve `("text", "image")` quando o catálogo tem
`input_modalities` com imagem.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/workers/test_model_capabilities.py -q`
Expected: FAIL com `TypeError: AgentToolset.__init__() got an unexpected keyword argument 'model_sees_images'`

- [ ] **Step 3: Write the implementation**

Em `AgentToolset.__init__`, acrescente o parâmetro nomeado
`model_sees_images: bool = False` e `self.model_sees_images = bool(model_sees_images)`.

Substitua `_view_image` por:

```python
    def _view_image(self, path: str, target, media_type: str, question: str) -> dict[str, Any]:
        if self.model_sees_images:
            import base64

            data = base64.b64encode(target.read_bytes()).decode("ascii")
            return {
                "summary": f"Abriu {path}",
                "content": f"A imagem '{path}' está anexada logo abaixo.",
                "payload": {"path": path, "media_type": media_type, "label": path},
                "images": [image_block(media_type, data)],
            }
        return {
            "summary": f"Não foi possível ler {path}",
            "content": (
                f"'{path}' é uma imagem e este turno não tem leitura visual disponível: "
                "o modelo atual não enxerga e nenhum modelo de leitura visual está configurado."
            ),
            "payload": {"path": path, "media_type": media_type, "label": path},
        }
```

Importe `from .provider_content import image_block` no topo de `agent_tools.py`.

Verifique como o dicionário devolvido por um handler vira `ToolOutcome` (procure
por `ToolOutcome(` em `agent_tools.py`) e repasse `images` na construção, com
default `[]`.

Em `workers/chat.py`, generalize a consulta ao catálogo:

```python
    def _catalog_row_for(self, turn: dict[str, object]) -> dict[str, object] | None:
        try:
            with self.store._engine.connect() as c:
                row = c.execute(
                    select(provider_model_catalog).where(
                        provider_model_catalog.c.user_id == turn["user_id"],
                        provider_model_catalog.c.provider == turn["provider"],
                        provider_model_catalog.c.model_id == turn["model_id"],
                    )
                ).mappings().first()
        except Exception:
            return None
        return dict(row) if row else None

    def _model_sees_images(self, turn: dict[str, object]) -> bool:
        row = self._catalog_row_for(turn) or {}
        modalities = row.get("input_modalities") or ()
        if isinstance(modalities, str):
            modalities = [item.strip() for item in modalities.split(",")]
        return "image" in {str(item).lower() for item in modalities}

    def _model_calls_tools(self, turn: dict[str, object]) -> bool:
        row = self._catalog_row_for(turn) or {}
        capabilities = row.get("capabilities") or ()
        if isinstance(capabilities, str):
            capabilities = [item.strip() for item in capabilities.split(",")]
        names = {str(item).lower() for item in capabilities}
        # An unrefreshed catalog must not silently disable tools: only an
        # explicit capability list that omits tools counts as "cannot".
        return not names or "tools" in names or "tool_use" in names or "function_calling" in names
```

Mantenha `_context_window_for` funcionando: reescreva-o para usar
`_catalog_row_for` e ler `context_window` da linha. Confirme os nomes reais das
colunas em `schema.py` (`provider_model_catalog`) antes de escrever isso.

Passe as duas capacidades para a `TurnSession` onde ela é construída no worker
(`model_sees_images=`, `model_calls_tools=`), guarde-as no construtor da sessão e
repasse `model_sees_images=self.model_sees_images` na construção do
`AgentToolset` dentro de `TurnSession._toolset`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/workers tests/unit/agentic -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos tests/unit && git commit -m "feat(agentic): let the turn know whether its model can see images"
```

---

# Fase 4 — Leitura visual por modelo

### Task 15: Rasterização de página e normalização de imagem

**Files:**
- Create: `src/agentos/reading/render.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/reading/test_render.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

import pytest

from agentos.reading.render import MAX_IMAGE_PIXELS, ImageTooLarge, normalize_image, render_pdf_pages


def test_normalize_returns_base64_and_a_media_type(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")
    target = tmp_path / "foto.png"
    Image.new("RGB", (40, 40), "white").save(target)
    data, media_type = normalize_image(target)
    assert media_type == "image/jpeg"
    assert isinstance(data, str) and len(data) > 32


def test_normalize_shrinks_a_large_image(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")
    import base64
    import io

    target = tmp_path / "grande.png"
    Image.new("RGB", (4000, 1000), "white").save(target)
    data, _ = normalize_image(target)
    restored = Image.open(io.BytesIO(base64.b64decode(data)))
    assert max(restored.size) == 1568


def test_normalize_refuses_a_pixel_bomb(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")
    target = tmp_path / "bomba.png"
    Image.new("RGB", (10, 10), "white").save(target)
    with pytest.raises(ImageTooLarge):
        normalize_image(target, max_pixels=16)


def test_render_pdf_pages_returns_one_image_per_requested_page(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pypdfium2")
    target = tmp_path / "doc.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    with target.open("wb") as handle:
        writer.write(handle)
    images = render_pdf_pages(target, (1, 2))
    assert len(images) == 2
    assert all(media_type == "image/jpeg" for _, media_type in images)


def test_render_pdf_pages_is_bounded(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pypdfium2")
    target = tmp_path / "doc.pdf"
    writer = pypdf.PdfWriter()
    for _ in range(30):
        writer.add_blank_page(width=100, height=100)
    with target.open("wb") as handle:
        writer.write(handle)
    assert len(render_pdf_pages(target, tuple(range(1, 31)), max_pages=4)) == 4


def test_max_image_pixels_is_declared():
    assert MAX_IMAGE_PIXELS > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/reading/test_render.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.reading.render'`

- [ ] **Step 3: Write the implementation**

Acrescente a `pyproject.toml`: `"pypdfium2>=4,<5"` e `"Pillow>=11,<12"`.

`src/agentos/reading/render.py`:

```python
"""Turn a page or a picture into a bounded JPEG a model can be shown."""
from __future__ import annotations

import base64
import io
from pathlib import Path

MAX_IMAGE_EDGE = 1568
MAX_IMAGE_PIXELS = 50_000_000
MAX_RENDERED_PAGES = 20
JPEG_QUALITY = 85


class ImageTooLarge(ValueError):
    """The image would cost more to decode than it is worth."""


def _encode(image) -> str:
    from PIL import Image

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > MAX_IMAGE_EDGE:
        scale = MAX_IMAGE_EDGE / max(image.size)
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def normalize_image(path: Path, *, max_pixels: int = MAX_IMAGE_PIXELS) -> tuple[str, str]:
    """Return ``(base64_jpeg, media_type)`` for one picture on disk."""
    from PIL import Image

    with Image.open(path) as image:
        if image.width * image.height > max_pixels:
            raise ImageTooLarge(f"{path.name} is too large to decode")
        image.load()
        return _encode(image), "image/jpeg"


def render_pdf_pages(path: Path, pages: tuple[int, ...], *, max_pages: int = MAX_RENDERED_PAGES) -> list[tuple[str, str]]:
    """Rasterize 1-based ``pages`` of a PDF into bounded JPEGs."""
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path))
    try:
        total = len(document)
        wanted = [number for number in pages if 1 <= number <= total][:max_pages]
        rendered: list[tuple[str, str]] = []
        for number in wanted:
            page = document[number - 1]
            bitmap = page.render(scale=2)
            rendered.append((_encode(bitmap.to_pil()), "image/jpeg"))
        return rendered
    finally:
        document.close()


__all__ = ["ImageTooLarge", "MAX_IMAGE_EDGE", "MAX_IMAGE_PIXELS", "MAX_RENDERED_PAGES", "normalize_image", "render_pdf_pages"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/reading -q`
Expected: PASS, 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/reading tests/unit/reading pyproject.toml && git commit -m "feat(reading): rasterize pdf pages and normalize images"
```

---

### Task 16: Escolha do modelo de leitura visual

**Files:**
- Create: `src/agentos/reading/selection.py`
- Test: `tests/unit/reading/test_selection.py`

- [ ] **Step 1: Write the failing test**

```python
from agentos.reading.selection import VisionModel, choose_vision_model

CANDIDATES = (
    VisionModel("openrouter", "gpt-4o"),
    VisionModel("ollama", "qwen2.5-vl"),
    VisionModel("anthropic", "claude-sonnet"),
)


def test_the_override_wins():
    chosen = choose_vision_model(CANDIDATES, turn_provider="anthropic", override=VisionModel("openrouter", "gpt-4o"))
    assert chosen == VisionModel("openrouter", "gpt-4o")


def test_an_override_that_is_no_longer_available_is_ignored():
    chosen = choose_vision_model(CANDIDATES, turn_provider="anthropic", override=VisionModel("openai", "sumiu"))
    assert chosen == VisionModel("anthropic", "claude-sonnet")


def test_the_turn_provider_is_preferred():
    assert choose_vision_model(CANDIDATES, turn_provider="anthropic") == VisionModel("anthropic", "claude-sonnet")


def test_a_local_ollama_comes_before_any_cloud_provider():
    assert choose_vision_model(CANDIDATES, turn_provider="mistral") == VisionModel("ollama", "qwen2.5-vl")


def test_any_remaining_candidate_is_used_as_a_last_resort():
    assert choose_vision_model((VisionModel("openrouter", "gpt-4o"),), turn_provider="mistral") == VisionModel("openrouter", "gpt-4o")


def test_no_candidate_returns_none():
    assert choose_vision_model((), turn_provider="anthropic") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/reading/test_selection.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.reading.selection'`

- [ ] **Step 3: Write the implementation**

`src/agentos/reading/selection.py`:

```python
"""Pick the model that reads a picture when the turn's own model cannot.

The order is deliberate: what the person chose, then the provider already in
use for this turn, then a local Ollama — a file the person attached should not
leave the machine when a local model can read it — then anything else.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

LOCAL_PROVIDER = "ollama"


@dataclass(frozen=True, slots=True)
class VisionModel:
    provider: str
    model_id: str


def choose_vision_model(candidates: Sequence[VisionModel], *, turn_provider: str, override: VisionModel | None = None) -> VisionModel | None:
    """The model that will transcribe, or None when nothing can."""
    available = list(candidates)
    if not available:
        return None
    if override is not None and override in available:
        return override
    provider = str(turn_provider or "").lower()
    for item in available:
        if item.provider.lower() == provider:
            return item
    for item in available:
        if item.provider.lower() == LOCAL_PROVIDER:
            return item
    return available[0]


__all__ = ["LOCAL_PROVIDER", "VisionModel", "choose_vision_model"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/reading -q`
Expected: PASS, 18 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/reading tests/unit/reading && git commit -m "feat(reading): choose the model that performs a visual read"
```

---

### Task 17: `VisionReader` transcreve uma imagem

**Files:**
- Create: `src/agentos/reading/vision.py`
- Test: `tests/unit/reading/test_vision.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.reading.selection import VisionModel
from agentos.reading.vision import VisionReader, VisionUnavailable


class _Transport:
    def __init__(self, items):
        self.items = items
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        return iter(self.items)


def _reader(transport):
    return VisionReader(lambda model: transport, model=VisionModel("ollama", "qwen2.5-vl"))


def test_transcription_joins_the_streamed_text():
    transport = _Transport([
        NormalizedStreamItem(StreamKind.TEXT, 1, text="Nota fiscal "),
        NormalizedStreamItem(StreamKind.TEXT, 2, text="nº 42"),
        NormalizedStreamItem(StreamKind.FINISH, 3),
    ])
    assert _reader(transport).transcribe([("QUJD", "image/png")]) == "Nota fiscal nº 42"


def test_the_request_carries_the_image_and_no_tools():
    transport = _Transport([NormalizedStreamItem(StreamKind.TEXT, 1, text="ok")])
    _reader(transport).transcribe([("QUJD", "image/png")], instruction="Liste os valores")
    request = transport.requests[0]
    assert request["tools"] == []
    content = request["messages"][0]["content"]
    assert content[0]["text"].startswith("Liste os valores")
    assert content[1] == {"type": "image", "media_type": "image/png", "data": "QUJD"}


def test_a_stream_error_raises_vision_unavailable():
    transport = _Transport([NormalizedStreamItem(StreamKind.ERROR, 1)])
    with pytest.raises(VisionUnavailable):
        _reader(transport).transcribe([("QUJD", "image/png")])


def test_an_empty_transcription_raises():
    transport = _Transport([NormalizedStreamItem(StreamKind.FINISH, 1)])
    with pytest.raises(VisionUnavailable):
        _reader(transport).transcribe([("QUJD", "image/png")])


def test_without_a_model_it_refuses_before_any_call():
    with pytest.raises(VisionUnavailable):
        VisionReader(lambda model: None, model=None).transcribe([("QUJD", "image/png")])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/reading/test_vision.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agentos.reading.vision'`

- [ ] **Step 3: Write the implementation**

`src/agentos/reading/vision.py`:

```python
"""Read a picture with a model and return its text.

One non-streaming-shaped call over the streaming transport the worker already
builds: reusing it means the provider payload, the error normalization and the
credential handling are the ones the chat loop is already tested on.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from agentos.agentic.provider_content import image_block
from agentos.agentic.provider_stream import StreamKind

from .selection import VisionModel

MAX_TRANSCRIPTION_CHARS = 20_000
DEFAULT_INSTRUCTION = (
    "Transcreva todo o texto visível desta imagem, preservando a ordem de leitura, "
    "títulos, tabelas e valores. Descreva brevemente elementos não textuais "
    "relevantes (gráficos, diagramas, fotos). Não invente conteúdo ausente."
)


class VisionUnavailable(RuntimeError):
    """No model could read the image, or the read produced nothing."""


class VisionReader:
    def __init__(self, transport_factory: Callable[[VisionModel], object], *, model: VisionModel | None) -> None:
        self._transport_factory = transport_factory
        self.model = model

    def transcribe(self, images: Sequence[tuple[str, str]], *, instruction: str = "") -> str:
        """Transcribe ``(base64, media_type)`` images into one text."""
        if self.model is None:
            raise VisionUnavailable("no visual reading model is configured")
        if not images:
            raise VisionUnavailable("no image to read")
        transport = self._transport_factory(self.model)
        if transport is None:
            raise VisionUnavailable("the visual reading model is unavailable")
        content: list[dict[str, str]] = [{"type": "text", "text": f"{instruction.strip()}\n\n{DEFAULT_INSTRUCTION}".strip()}]
        content.extend(image_block(media_type, data) for data, media_type in images)
        parts: list[str] = []
        for item in transport.stream({"messages": [{"role": "user", "content": content}], "tools": []}):
            if item.kind is StreamKind.ERROR:
                raise VisionUnavailable("the visual reading model failed")
            if item.kind is StreamKind.TEXT and item.text:
                parts.append(item.text)
        text = "".join(parts).strip()
        if not text:
            raise VisionUnavailable("the visual reading model returned nothing")
        return text[:MAX_TRANSCRIPTION_CHARS]


__all__ = ["DEFAULT_INSTRUCTION", "VisionReader", "VisionUnavailable"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/reading -q`
Expected: PASS, 23 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentos/reading tests/unit/reading && git commit -m "feat(reading): transcribe an image with a vision model"
```

---

### Task 18: `view_file` usa o leitor visual

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py` (`AgentToolset.__init__`, `view_file`, `_view_image`)
- Test: `tests/unit/agentic/test_view_file_vision.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _Reader:
    def __init__(self, text="Nota fiscal nº 42"):
        self.text = text
        self.calls = []

    def transcribe(self, images, *, instruction=""):
        self.calls.append((list(images), instruction))
        return self.text


def _toolset(tmp_path, **kwargs):
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_1"), enable_terminal=False, **kwargs)


def test_an_image_is_transcribed_when_the_model_cannot_see(tmp_path, monkeypatch):
    monkeypatch.setattr("agentos.agentic.agent_tools.normalize_image", lambda path, **_: ("QUJD", "image/jpeg"))
    reader = _Reader()
    toolset = _toolset(tmp_path, visual_reader=reader)
    (toolset.workspace.root / "foto.png").write_bytes(PNG)
    result = toolset.view_file("foto.png", question="Qual o total?")
    assert "Nota fiscal nº 42" in result["content"]
    assert result["images"] == []
    assert reader.calls[0][1] == "Qual o total?"


def test_the_model_that_sees_gets_the_image_instead_of_a_transcription(tmp_path, monkeypatch):
    monkeypatch.setattr("agentos.agentic.agent_tools.normalize_image", lambda path, **_: ("QUJD", "image/jpeg"))
    reader = _Reader()
    toolset = _toolset(tmp_path, visual_reader=reader, model_sees_images=True)
    (toolset.workspace.root / "foto.png").write_bytes(PNG)
    result = toolset.view_file("foto.png")
    assert result["images"][0]["data"] == "QUJD"
    assert reader.calls == []


def test_a_scanned_pdf_page_goes_through_the_reader(tmp_path, monkeypatch):
    monkeypatch.setattr("agentos.agentic.agent_tools.render_pdf_pages", lambda path, pages, **_: [("QUJD", "image/jpeg")])
    reader = _Reader("Contrato assinado")
    toolset = _toolset(tmp_path, visual_reader=reader)
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with (toolset.workspace.root / "doc.pdf").open("wb") as handle:
        writer.write(handle)
    result = toolset.view_file("doc.pdf")
    assert "Contrato assinado" in result["content"]


def test_a_failed_read_is_explained_rather_than_crashing(tmp_path, monkeypatch):
    from agentos.reading.vision import VisionUnavailable

    monkeypatch.setattr("agentos.agentic.agent_tools.normalize_image", lambda path, **_: ("QUJD", "image/jpeg"))

    class _Broken:
        def transcribe(self, images, *, instruction=""):
            raise VisionUnavailable("no model")

    toolset = _toolset(tmp_path, visual_reader=_Broken())
    (toolset.workspace.root / "foto.png").write_bytes(PNG)
    result = toolset.view_file("foto.png")
    assert "leitura visual" in result["content"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/agentic/test_view_file_vision.py -q`
Expected: FAIL com `TypeError: AgentToolset.__init__() got an unexpected keyword argument 'visual_reader'`

- [ ] **Step 3: Write the implementation**

Importe no topo de `agent_tools.py`:

```python
from agentos.reading.render import ImageTooLarge, normalize_image, render_pdf_pages
from agentos.reading.vision import VisionUnavailable
```

Acrescente `visual_reader: object | None = None` ao `__init__` e
`self._visual_reader = visual_reader`.

Substitua `_view_image` e acrescente o caminho de PDF escaneado dentro de
`view_file`. Em `view_file`, logo após montar `body` para um PDF sem camada de
texto:

```python
        if media_type == "application/pdf" and extracted.pages_without_text and not extracted.text.strip():
            return self._view_scanned_pdf(path, target, extracted.pages_without_text, question)
```

Métodos novos:

```python
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
        }
```

Construa o `VisionReader` em `TurnSession._toolset` e passe-o como
`visual_reader=`; a sessão recebe do worker uma fábrica
`vision_reader_factory: Callable[[], object] | None`. No worker, monte o
`VisionReader` com `choose_vision_model` sobre o catálogo do usuário (linhas de
`provider_model_catalog` cujo `input_modalities` contenha `image`), o override
de `vision_model_selections` e uma fábrica de transporte que reaproveite o
método já existente de construção de `HTTPProviderStreamTransport` — extraia-o
para `_transport_for(user_id, provider, model_id, num_ctx=None)` e faça
`_provider_transport(turn)` chamá-lo.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/agentic tests/unit/workers -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos tests/unit && git commit -m "feat(agentic): read images and scanned pages through the vision model"
```

---

### Task 19: Rotas e tela do modelo de leitura visual

**Files:**
- Modify: `src/agentos/api/gateway.py`
- Modify: `frontend/src/api/providers.ts` (ou crie `frontend/src/api/vision.ts` se o arquivo já estiver grande)
- Modify: `frontend/src/features/settings/` (a página que hoje lista providers)
- Test: `tests/unit/api/test_vision_model_routes.py`, `frontend/tests/unit/VisionModelSetting.test.tsx`

- [ ] **Step 1: Write the failing test**

```python
def test_get_returns_automatic_when_nothing_is_selected(tmp_path):
    response = _client(tmp_path).get("/v1/settings/vision-model")
    assert response.status_code == 200
    assert response.json() == {"provider": None, "model_id": None, "mode": "automatic"}


def test_put_stores_the_override(tmp_path):
    client = _client(tmp_path)
    response = client.put("/v1/settings/vision-model", json={"provider": "ollama", "model_id": "qwen2.5-vl"})
    assert response.status_code == 200
    assert response.json() == {"provider": "ollama", "model_id": "qwen2.5-vl", "mode": "manual"}
    assert client.get("/v1/settings/vision-model").json()["model_id"] == "qwen2.5-vl"


def test_put_null_returns_to_automatic(tmp_path):
    client = _client(tmp_path)
    client.put("/v1/settings/vision-model", json={"provider": "ollama", "model_id": "qwen2.5-vl"})
    assert client.put("/v1/settings/vision-model", json={"provider": None, "model_id": None}).json()["mode"] == "automatic"
```

Use a mesma fábrica `_client` dos outros testes de rota deste diretório,
acrescentando o engine que as tabelas novas exigem.

No frontend, `VisionModelSetting.test.tsx` deve verificar que a página lista os
modelos com visão do catálogo, mostra "Automático" como opção padrão e chama a
API ao escolher um modelo.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/api/test_vision_model_routes.py -q`
Expected: FAIL com 404 nas três chamadas

- [ ] **Step 3: Write the implementation**

Acrescente ao gateway um request model e as duas rotas, escrevendo em
`vision_model_selections` pelo engine que o `ApiServices` já usa para as demais
leituras de catálogo (siga como `provider_catalog` é acessado nas rotas de
`/v1/providers`):

```python
class VisionModelRequest(_RequestModel):
    provider: str | None = Field(default=None, max_length=32)
    model_id: str | None = Field(default=None, max_length=512)
```

`GET` devolve `{"provider", "model_id", "mode"}` com `mode` em
`{"automatic", "manual"}`; `PUT` com ambos nulos apaga a linha e volta para
automático, e com ambos preenchidos faz upsert por `user_id`.

Na tela de Settings, acrescente um seletor "Modelo de leitura visual" logo
abaixo da lista de providers, alimentado pelos modelos do catálogo cujo
`input_modalities` contenha `image`, com "Automático" como primeira opção e um
texto curto explicando que o arquivo é enviado ao provider desse modelo.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/api -q` e `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/api frontend/src frontend/tests tests/unit/api && git commit -m "feat(settings): choose the model that performs visual reading"
```

---

# Fase 5 — Modelo sem tool-calling e acabamento

### Task 20: Pré-execução da leitura para modelo sem ferramentas

**Files:**
- Modify: `src/agentos/agentic/session.py`
- Test: `tests/unit/agentic/test_pre_read_attachments.py`

- [ ] **Step 1: Write the failing test**

```python
from agentos.agentic.session import pre_read_attachments


class _Toolset:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def view_file(self, path, question=""):
        self.calls.append(path)
        return self.outcome


def test_a_visual_attachment_is_read_and_appended():
    toolset = _Toolset({"content": "Leitura visual de 'uploads/nota.pdf':\n\nTotal 42"})
    history = [{"role": "user", "content": "quanto deu?\n\n[anexos enviados pela pessoa: uploads/nota.pdf (PDF, 12 KB)]"}]
    result = pre_read_attachments(history, [{"path": "uploads/nota.pdf", "kind": "pdf"}], toolset)
    assert toolset.calls == ["uploads/nota.pdf"]
    assert "Total 42" in result[-1]["content"]
    assert result[-1]["role"] == "user"


def test_a_text_attachment_is_not_pre_read():
    toolset = _Toolset({"content": "irrelevante"})
    history = [{"role": "user", "content": "leia"}]
    result = pre_read_attachments(history, [{"path": "uploads/notas.md", "kind": "text"}], toolset)
    assert toolset.calls == []
    assert result == history


def test_a_failed_read_does_not_break_the_turn():
    class _Broken:
        def view_file(self, path, question=""):
            raise RuntimeError("boom")

    history = [{"role": "user", "content": "leia"}]
    result = pre_read_attachments(history, [{"path": "uploads/foto.png", "kind": "image"}], _Broken())
    assert result == history


def test_the_number_of_pre_read_files_is_bounded():
    toolset = _Toolset({"content": "ok"})
    attachments = [{"path": f"uploads/f{index}.png", "kind": "image"} for index in range(8)]
    pre_read_attachments([{"role": "user", "content": "leia"}], attachments, toolset, max_files=3)
    assert len(toolset.calls) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/agentic/test_pre_read_attachments.py -q`
Expected: FAIL com `ImportError: cannot import name 'pre_read_attachments'`

- [ ] **Step 3: Write the implementation**

Em `session.py`, função de módulo:

```python
MAX_PRE_READ_FILES = 4


def pre_read_attachments(history, attachments, toolset, *, max_files: int = MAX_PRE_READ_FILES):
    """Read visual attachments before the turn starts.

    A model that cannot call tools would otherwise never look at the file the
    person just attached. This is the only path that reads without the model
    asking, and it exists solely for that case.
    """
    visual = [item for item in attachments if str(item.get("kind")) in {"image", "pdf"}][:max_files]
    if not visual:
        return history
    readings: list[str] = []
    for item in visual:
        try:
            result = toolset.view_file(str(item.get("path") or ""))
        except Exception:
            # A pre-read is an enrichment; it never becomes the reason a turn
            # cannot start.
            continue
        content = str((result or {}).get("content") or "").strip()
        if content:
            readings.append(content)
    if not readings:
        return history
    joined = "\n\n---\n\n".join(readings)
    updated = [dict(item) for item in history]
    for index in range(len(updated) - 1, -1, -1):
        if updated[index].get("role") == "user":
            updated[index]["content"] = f"{updated[index].get('content', '')}\n\n{joined}"
            return updated
    updated.append({"role": "user", "content": joined})
    return updated
```

Acrescente `pre_read_attachments` ao `__all__` do módulo.

Em `TurnSession`, guarde `self.model_calls_tools` (Task 14) e os anexos do turno
(obtidos com uma leitura da tabela nova, por um método novo
`PostgresChatStore.attachments_for_turn(turn)` que devolve os registros do
`user_message_id` do turno). No `_MainAgentStore.history_for_turn` — ou onde a
sessão monta o histórico do agente principal — aplique:

```python
        if not self.session.model_calls_tools:
            history = pre_read_attachments(history, self.session.turn_attachments, toolset)
```

Escreva também o teste de `attachments_for_turn` em
`tests/unit/conversations/test_chat_attachments.py`, no mesmo estilo dos que já
existem lá.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/agentic tests/unit/conversations -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos tests/unit && git commit -m "feat(agentic): pre-read attachments for a model that cannot call tools"
```

---

### Task 21: Aviso do composer conforme a capacidade do modelo

**Files:**
- Create: `frontend/src/features/conversations/attachmentNotice.ts`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Test: `frontend/tests/unit/attachmentNotice.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from 'vitest'
import { attachmentNotice } from '../../src/features/conversations/attachmentNotice'

describe('attachmentNotice', () => {
  it('says nothing when there is no attachment', () => {
    expect(attachmentNotice({ hasVisualAttachment: false, modelSeesImages: false, modelCallsTools: true, visionModelName: 'qwen2.5-vl' })).toBeNull()
  })

  it('says nothing when the model can see', () => {
    expect(attachmentNotice({ hasVisualAttachment: true, modelSeesImages: true, modelCallsTools: true, visionModelName: 'qwen2.5-vl' })).toBeNull()
  })

  it('names the model that will read for a text-only model', () => {
    expect(attachmentNotice({ hasVisualAttachment: true, modelSeesImages: false, modelCallsTools: true, visionModelName: 'qwen2.5-vl' }))
      .toBe('Este modelo não enxerga; o Orin vai ler com qwen2.5-vl.')
  })

  it('warns that the read happens before sending when the model has no tools', () => {
    expect(attachmentNotice({ hasVisualAttachment: true, modelSeesImages: false, modelCallsTools: false, visionModelName: 'qwen2.5-vl' }))
      .toBe('Este modelo não enxerga; o Orin vai ler com qwen2.5-vl antes de enviar.')
  })

  it('points to settings when no vision model is available', () => {
    expect(attachmentNotice({ hasVisualAttachment: true, modelSeesImages: false, modelCallsTools: true, visionModelName: null }))
      .toBe('Nenhum modelo de leitura visual disponível: configure um em Configurações.')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/attachmentNotice.test.ts`
Expected: FAIL com "Cannot find module '.../attachmentNotice'"

- [ ] **Step 3: Write the implementation**

```ts
export type AttachmentNoticeInput = {
  hasVisualAttachment: boolean
  modelSeesImages: boolean
  modelCallsTools: boolean
  visionModelName: string | null
}

/** What the composer says about how this file will actually be read. */
export function attachmentNotice(input: AttachmentNoticeInput): string | null {
  if (!input.hasVisualAttachment || input.modelSeesImages) return null
  if (!input.visionModelName) return 'Nenhum modelo de leitura visual disponível: configure um em Configurações.'
  const tail = input.modelCallsTools ? '.' : ' antes de enviar.'
  return `Este modelo não enxerga; o Orin vai ler com ${input.visionModelName}${tail}`
}
```

No `ChatPage.tsx`, calcule `hasVisualAttachment` a partir dos chips
(`kind === 'image' || kind === 'pdf'`), leia `input_modalities` e `capabilities`
do modelo escolhido — o `/v1/models` já devolve os dois — e passe o resultado
como `notice` ao `Composer`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run && npm run lint && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src frontend/tests && git commit -m "feat(web): explain how an attachment will be read"
```

---

### Task 22: Teste de integração de ponta a ponta

**Files:**
- Test: `tests/integration/agentic/test_attachment_turn.py`

- [ ] **Step 1: Write the failing test**

Siga o padrão dos testes existentes em `tests/integration/agentic/` (leia um
deles primeiro para reaproveitar a fábrica de stack e o provider falso).

```python
def test_a_turn_with_an_image_reaches_a_model_that_sees(...):
    """Cria a conversa com um upload de imagem, roda o turno com um provider
    falso cujo modelo tem input_modalities=('text','image'), e verifica que:
      - o arquivo existe em uploads/ dentro do workspace da conversa;
      - o modelo pediu view_file e recebeu uma mensagem user com bloco de imagem;
      - o turno terminou como completed."""


def test_a_turn_with_an_image_transcribes_for_a_text_only_model(...):
    """Mesmo fluxo com input_modalities=('text',) e um VisionReader falso;
    verifica que o resultado de view_file contém a transcrição e que nenhuma
    mensagem carrega bloco de imagem."""
```

Escreva as duas asserções completas com base na fábrica real do diretório.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/agentic/test_attachment_turn.py -q`
Expected: FAIL antes de a montagem estar completa

- [ ] **Step 3: Make it pass**

Ajuste apenas o que os testes revelarem — nenhuma funcionalidade nova deve ser
necessária neste ponto.

- [ ] **Step 4: Run the whole suite**

```bash
python -m pytest -q tests/unit
```

```bash
cd frontend && npm test && npm run lint && npm run build
```

Expected: PASS nas duas.

- [ ] **Step 5: Commit**

```bash
git add tests/integration && git commit -m "test(agentic): cover an attachment turn for both model capabilities"
```

---

### Task 23: Documentação

**Files:**
- Modify: `README.md` (tabela de ferramentas e seção "Using it")
- Modify: `docs/agentic/TOOL_CAPABILITY_MATRIX.md`

- [ ] **Step 1: Update the tool table**

Acrescente a `README.md`, na tabela de ferramentas:

```markdown
| `view_file` | Lê um documento (PDF, Word, Excel, PowerPoint, texto) ou uma imagem do workspace. Texto nativo é extraído sem custo de modelo; imagem e página escaneada vão para o modelo do turno quando ele enxerga, ou para o modelo de leitura visual configurado. |
```

- [ ] **Step 2: Document the attachment flow**

Em "Using it", acrescente um parágrafo curto: como anexar (botão, arrastar,
colar), que o arquivo é gravado em `uploads/` dentro do workspace da conversa —
inclusive quando é uma pasta local —, e que a leitura visual envia o arquivo ao
provider do modelo escolhido, por isso o modo automático prefere um Ollama
local.

- [ ] **Step 3: Update the capability matrix**

Acrescente `view_file` a `docs/agentic/TOOL_CAPABILITY_MATRIX.md` no mesmo
formato das linhas existentes.

- [ ] **Step 4: Verify**

Run: `python -m pytest -q tests/unit && cd frontend && npm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md docs && git commit -m "docs: document file attachments and visual reading"
```
