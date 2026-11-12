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


WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 16
MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 32
MP3_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 32
OGG = b"OggS\x00\x02" + b"\x00" * 32
FLAC = b"fLaC\x00\x00\x00\x22" + b"\x00" * 32
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + b"\x00" * 32
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00" + b"\x00" * 32
EBML = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
AAC = b"\xff\xf1\x50\x80" + b"\x00" * 32


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("fala.wav", WAV, ("audio/wav", "audio")),
        ("fala.mp3", MP3_ID3, ("audio/mpeg", "audio")),
        ("fala.mp3", MP3_FRAME, ("audio/mpeg", "audio")),
        ("fala.ogg", OGG, ("audio/ogg", "audio")),
        ("fala.opus", OGG, ("audio/opus", "audio")),
        ("fala.flac", FLAC, ("audio/flac", "audio")),
        ("fala.m4a", M4A, ("audio/mp4", "audio")),
        ("fala.aac", AAC, ("audio/aac", "audio")),
        ("clip.mp4", MP4, ("video/mp4", "video")),
        ("clip.mov", MOV, ("video/quicktime", "video")),
        ("clip.webm", EBML, ("video/webm", "video")),
        ("clip.mkv", EBML, ("video/x-matroska", "video")),
    ],
)
def test_classify_accepts_audio_and_video_by_their_first_bytes(name, data, expected):
    assert classify(name, data) == expected


def test_classify_audio_is_decided_by_the_bytes_not_the_name():
    assert classify("renomeado.txt", WAV) == ("audio/wav", "audio")
    with pytest.raises(UploadRejected):
        classify("fake.mp4", b"MZ\x90\x00" + b"\x00" * 32)
    with pytest.raises(UploadRejected):
        classify("fake.wav", b"just some text, not riff")
    with pytest.raises(UploadRejected):
        classify("fake.mp3", b"\x00\x01\x02\x03" + b"\x00" * 32)  # no signature and no frame sync
    with pytest.raises(UploadRejected):
        classify("frame.txt", MP3_FRAME)  # a bare frame counts only with an audio extension


def test_media_has_its_own_higher_limit():
    from agentos.uploads.media import MAX_MEDIA_UPLOAD_BYTES, MAX_TURN_BYTES, MAX_UPLOAD_BYTES

    assert MAX_MEDIA_UPLOAD_BYTES > MAX_UPLOAD_BYTES
    assert MAX_TURN_BYTES >= MAX_MEDIA_UPLOAD_BYTES
