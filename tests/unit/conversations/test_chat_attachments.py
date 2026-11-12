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


def test_attachments_for_turn_returns_the_turns_user_message_attachments():
    store = _store()
    receipt = store.create(
        user_id="user-1", message="veja isto", provider="anthropic", model_id="m",
        idempotency_key="k1", attachments=[ATTACHMENT],
    )
    turn = store.claim(receipt.turn_id)
    assert store.attachments_for_turn(turn) == [{
        "path": "uploads/nota.pdf", "original_name": "nota.pdf",
        "media_type": "application/pdf", "kind": "pdf", "bytes": 2048,
    }]


def test_attachments_for_turn_is_empty_without_attachments():
    store = _store()
    receipt = store.create(user_id="user-1", message="oi", provider="anthropic", model_id="m", idempotency_key="k1")
    turn = store.claim(receipt.turn_id)
    assert store.attachments_for_turn(turn) == []


TEXT_ATTACHMENT = {
    "path": ".orin/uploads/writing-block.md", "original_name": "writing-block.md",
    "media_type": "text/markdown", "kind": "text", "bytes": 4096,
}


def _history_for(message: str, attachments):
    store = _store()
    receipt = store.create(user_id="user-1", message=message, provider="anthropic", model_id="m",
                           idempotency_key="k1", attachments=attachments)
    return store.history_for_turn(store.claim(receipt.turn_id))


def test_an_attachment_only_message_tells_the_model_the_text_file_is_the_instruction():
    first_line = _history_for("", [TEXT_ATTACHMENT])[0]["content"].splitlines()[0]
    assert first_line == (
        "[mensagem sem texto: a pessoa enviou só anexos. O conteúdo de .orin/uploads/writing-block.md "
        "é a instrução desta mensagem: leia e siga.]"
    )


def test_an_attachment_only_message_with_visual_files_points_to_them():
    first_line = _history_for("", [ATTACHMENT])[0]["content"].splitlines()[0]
    assert first_line == (
        "[mensagem sem texto: a pessoa enviou só anexos (uploads/nota.pdf). "
        "Abra-os e trate o que pedem como a instrução desta mensagem.]"
    )


def test_a_message_with_text_keeps_its_text_first():
    assert _history_for("veja isto", [TEXT_ATTACHMENT])[0]["content"].startswith("veja isto")


def test_history_marker_gives_the_absolute_path_and_the_native_reader_on_agy():
    from pathlib import Path

    store = _store()
    image = {"path": ".orin/uploads/foto.png", "original_name": "foto.png", "media_type": "image/png",
             "kind": "image", "bytes": 4096}
    receipt = store.create(
        user_id="user-1", message="o que tem aqui?", provider="google-antigravity",
        model_id="claude-opus-5-5-high", idempotency_key="k-agy", attachments=[image],
    )
    turn = store.claim(receipt.turn_id)
    content = store.history_for_turn(turn)[0]["content"]
    listed = content.split("[anexos enviados pela pessoa: ", 1)[1].split(" (imagem", 1)[0]
    assert Path(listed).is_absolute() and listed.replace("\\", "/").endswith(".orin/uploads/foto.png")
    assert "ferramenta nativa read_file" in content and "caminho ABSOLUTO" in content


def test_history_marker_of_a_local_workspace_uses_its_root(tmp_path):
    store = _store()
    audio = {"path": ".orin/uploads/fala.wav", "original_name": "fala.wav", "media_type": "audio/wav",
             "kind": "audio", "bytes": 4096}
    receipt = store.create(
        user_id="user-1", message="transcreva", provider="google-antigravity",
        model_id="gemini-3.8-flash-high", idempotency_key="k-agy2", attachments=[audio],
    )
    turn = {**store.claim(receipt.turn_id), "workspace_root_path": str(tmp_path)}
    content = store.history_for_turn(turn)[0]["content"]
    assert f"{(tmp_path.resolve() / '.orin/uploads/fala.wav').as_posix()} (áudio, 4 KB)" in content
    assert "áudio" in content and "ferramenta nativa read_file" in content
