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


def test_audio_and_video_get_their_own_limit_while_other_kinds_keep_the_small_one(tmp_path):
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
    mp4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + b"\x00" * 64
    staging = UploadStaging(tmp_path, max_bytes=16, max_media_bytes=4096)
    assert staging.store("user-1", "fala.wav", wav).kind == "audio"
    assert staging.store("user-1", "clip.mp4", mp4).kind == "video"
    with pytest.raises(UploadRejected):
        staging.store("user-1", "foto.png", PNG)  # an image keeps the small limit
    with pytest.raises(UploadRejected):
        UploadStaging(tmp_path, max_bytes=16, max_media_bytes=32).store("user-1", "fala.wav", wav)
