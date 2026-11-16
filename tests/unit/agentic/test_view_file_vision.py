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
    monkeypatch.setattr("agentos.agentic.tools.files.read.normalize_image", lambda path, **_: ("QUJD", "image/jpeg"))
    reader = _Reader()
    toolset = _toolset(tmp_path, visual_reader=reader)
    (toolset.workspace.root / "foto.png").write_bytes(PNG)
    result = toolset.view_file("foto.png", question="Qual o total?")
    assert "Nota fiscal nº 42" in result["content"]
    assert result["images"] == []
    assert reader.calls[0][1] == "Qual o total?"


def test_the_model_that_sees_gets_the_image_instead_of_a_transcription(tmp_path, monkeypatch):
    monkeypatch.setattr("agentos.agentic.tools.files.read.normalize_image", lambda path, **_: ("QUJD", "image/jpeg"))
    reader = _Reader()
    toolset = _toolset(tmp_path, visual_reader=reader, model_sees_images=True)
    (toolset.workspace.root / "foto.png").write_bytes(PNG)
    result = toolset.view_file("foto.png")
    assert result["images"][0]["data"] == "QUJD"
    assert reader.calls == []


def test_a_scanned_pdf_page_goes_through_the_reader(tmp_path, monkeypatch):
    monkeypatch.setattr("agentos.agentic.tools.files.read.render_pdf_pages", lambda path, pages, **_: [("QUJD", "image/jpeg")])
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

    monkeypatch.setattr("agentos.agentic.tools.files.read.normalize_image", lambda path, **_: ("QUJD", "image/jpeg"))

    class _Broken:
        def transcribe(self, images, *, instruction=""):
            raise VisionUnavailable("no model")

    toolset = _toolset(tmp_path, visual_reader=_Broken())
    (toolset.workspace.root / "foto.png").write_bytes(PNG)
    result = toolset.view_file("foto.png")
    assert "leitura visual" in result["content"].lower()


def test_codex_turn_view_file_and_read_file_return_image_blocks_without_calling_visual_reader(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime
    from sqlalchemy import create_engine, insert
    from agentos.agentic.tools.public_names import public_schema
    from agentos.agentic.tools.registry import default_registry
    from agentos.persistence.postgres.schema import metadata, provider_model_catalog
    from agentos.workers.chat import ChatWorker

    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "unit-test-view-file-codex-key")
    monkeypatch.setattr("agentos.agentic.tools.files.read.normalize_image", lambda path, **_: ("QUJD", "image/png"))

    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(insert(provider_model_catalog), [{
            "user_id": "user-1",
            "provider": "openai-codex",
            "model_id": "gpt-6.1-sol",
            "display_name": "GPT-6.1-Sol",
            "capabilities": ["tools"],
            "input_modalities": ["text"],  # even a stale catalog row
            "output_modalities": ["text"],
            "route_kind": "model",
            "refreshed_at": now,
            "created_at": now,
            "updated_at": now,
        }])

    class _Store:
        def __init__(self, eng):
            self._engine = eng

    worker = ChatWorker(_Store(engine))
    sees_images = worker._model_sees_images({
        "user_id": "user-1",
        "provider": "openai-codex",
        "model_id": "gpt-6.1-sol",
    })
    assert sees_images is True

    reader = _Reader()
    toolset = _toolset(tmp_path, visual_reader=reader, model_sees_images=sees_images)
    (toolset.workspace.root / "foto.png").write_bytes(PNG)

    view_res = toolset.view_file("foto.png")
    read_res = toolset.read_file("foto.png")
    assert view_res["images"] == [{"type": "image", "media_type": "image/png", "data": "QUJD"}]
    assert read_res["images"] == [{"type": "image", "media_type": "image/png", "data": "QUJD"}]
    assert reader.calls == []

    read_pub_desc, _ = public_schema("read_file")
    view_spec = default_registry().get("view_file")
    expected_note = 'For an image the result\'s content holds an image block (type "image").'
    assert expected_note in read_pub_desc
    assert view_spec is not None and expected_note in view_spec.description
