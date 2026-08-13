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
