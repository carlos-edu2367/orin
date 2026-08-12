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
