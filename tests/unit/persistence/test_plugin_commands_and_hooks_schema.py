from agentos.persistence.postgres.schema import metadata


def test_command_expansion_and_hook_context_tables_exist():
    assert "conversation_message_commands" in metadata.tables
    assert "conversation_hook_context" in metadata.tables

    columns = {column.name for column in metadata.tables["conversation_message_commands"].columns}
    assert {"message_id", "conversation_id", "plugin_id", "command_id", "arguments", "expanded_body"} <= columns

    columns = {column.name for column in metadata.tables["conversation_hook_context"].columns}
    assert {"conversation_id", "plugin_id", "hook_id", "body", "created_at"} <= columns
