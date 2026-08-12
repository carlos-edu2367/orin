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
