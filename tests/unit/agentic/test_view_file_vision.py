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
