import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.reading.extract import ExtractedText


def _toolset(tmp_path):
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_1"), enable_terminal=False)


def test_view_file_is_declared(tmp_path):
    names = {item.name for item in _toolset(tmp_path).definitions()}
    assert "view_file" in names


def test_transcribe_pdf_is_declared_and_never_uses_visual_reading(tmp_path, monkeypatch):
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "uploads").mkdir()
    (toolset.workspace.root / "uploads" / "vistoria.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(
        "agentos.agentic.agent_tools.extract_text",
        lambda path, media_type: ExtractedText("[página 1]\\nItem pendente", pages_without_text=(2,)),
    )

    result = toolset.transcribe_pdf("uploads/vistoria.pdf")

    assert "transcribe_pdf" in {item.name for item in toolset.definitions()}
    assert "Item pendente" in result["content"]
    assert "páginas sem camada de texto: 2" in result["content"]
    assert result["payload"]["text_extraction"] is True


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


def _real_png_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="blue").save(buffer, format="PNG")
    return buffer.getvalue()


def test_view_file_without_a_reader_explains_the_limit_for_an_image(tmp_path):
    toolset = _toolset(tmp_path)
    (toolset.workspace.root / "foto.png").write_bytes(_real_png_bytes())
    result = toolset.view_file("foto.png")
    assert "leitura visual" in result["content"].lower()
