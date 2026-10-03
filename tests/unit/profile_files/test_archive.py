import io
import stat
import zipfile

import pytest

from agentos.profile_files.archive import ArchiveRejected, extract_zip


def _zip(entries: dict[str, bytes], *, symlink: str | None = None) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    buffer.seek(0)
    return buffer


def test_a_normal_archive_lands_in_a_new_folder(tmp_path):
    path = extract_zip(_zip({"src/main.py": b"print(1)\n", "README.md": b"oi"}), tmp_path, "", "app")
    assert path == "app"
    assert (tmp_path / "app" / "src" / "main.py").read_bytes() == b"print(1)\n"
    assert not any(item.name.startswith(".import-") for item in tmp_path.iterdir())


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt", "C:/win.txt", "a/../../evil.txt"])
def test_entries_that_escape_are_rejected(tmp_path, name):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({name: b"x"}), tmp_path, "", "app")
    assert not (tmp_path / "app").exists()
    assert not (tmp_path.parent / "evil.txt").exists()


def test_symlinks_are_skipped(tmp_path):
    extract_zip(_zip({"ok.txt": b"ok"}, symlink="link"), tmp_path, "", "app")
    assert (tmp_path / "app" / "ok.txt").exists()
    assert not (tmp_path / "app" / "link").exists()


def test_the_total_size_is_enforced_while_writing(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({"big.bin": b"0" * 5000}), tmp_path, "", "app", max_total_bytes=4000)
    assert not (tmp_path / "app").exists()


def test_a_compression_bomb_is_rejected(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({"bomb.bin": b"\0" * (4 * 1024 * 1024)}), tmp_path, "", "app", max_ratio=50)


def test_too_many_entries_are_rejected(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({f"f{i}.txt": b"x" for i in range(11)}), tmp_path, "", "app", max_entries=10)


def test_an_existing_destination_is_not_overwritten(tmp_path):
    (tmp_path / "app").mkdir()
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({"a.txt": b"a"}), tmp_path, "", "app")


def test_not_a_zip(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(io.BytesIO(b"definitely not a zip"), tmp_path, "", "app")


def test_a_corrupted_member_is_rejected_and_cleaned_up(tmp_path):
    raw = bytearray(_zip({"a.txt": b"hello world " * 100}).getvalue())
    # Flip a byte inside the compressed data so decompression or the CRC check
    # fails while the member is being written.
    raw[40] ^= 0xFF
    with pytest.raises(ArchiveRejected):
        extract_zip(io.BytesIO(bytes(raw)), tmp_path, "", "app")
    assert list(tmp_path.iterdir()) == []


def test_a_file_and_a_folder_with_the_same_name_are_rejected(tmp_path):
    with pytest.raises(ArchiveRejected):
        extract_zip(_zip({"a": b"file", "a/b.txt": b"child"}), tmp_path, "", "app")
    assert list(tmp_path.iterdir()) == []
