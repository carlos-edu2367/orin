from agentos.persistence.postgres.schema import conversation_message_attachments, metadata, vision_model_selections


def test_attachment_table_is_registered():
    assert "conversation_message_attachments" in metadata.tables
    columns = set(conversation_message_attachments.c.keys())
    assert {"attachment_id", "message_id", "conversation_id", "user_id", "path",
            "original_name", "media_type", "kind", "bytes", "created_at"} <= columns


def test_vision_selection_table_is_registered():
    assert "vision_model_selections" in metadata.tables
    assert set(vision_model_selections.c.keys()) >= {"user_id", "provider", "model_id", "updated_at"}
