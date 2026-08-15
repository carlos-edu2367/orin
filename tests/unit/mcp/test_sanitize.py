import pytest

from agentos.mcp.sanitize import UntrustedDescriptorRejected, sanitize_tool_descriptors


def test_a_well_formed_descriptor_survives():
    tools = sanitize_tool_descriptors([
        {"name": "search", "description": "Search pages", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
    ])
    assert [item.name for item in tools] == ["search"]
    assert tools[0].input_schema["type"] == "object"


def test_a_descriptor_without_an_object_schema_is_dropped():
    assert sanitize_tool_descriptors([{"name": "x", "description": "", "inputSchema": {"type": "string"}}]) == ()


def test_a_name_that_is_not_a_safe_identifier_is_dropped():
    assert sanitize_tool_descriptors([{"name": "rm -rf", "description": "", "inputSchema": {"type": "object"}}]) == ()


def test_a_deeply_nested_schema_is_dropped():
    schema: dict = {"type": "object", "properties": {}}
    cursor = schema
    for _ in range(12):
        cursor["properties"]["next"] = {"type": "object", "properties": {}}
        cursor = cursor["properties"]["next"]
    assert sanitize_tool_descriptors([{"name": "deep", "description": "", "inputSchema": schema}]) == ()


def test_the_descriptor_batch_is_bounded():
    payload = [{"name": f"t{index}", "description": "", "inputSchema": {"type": "object"}} for index in range(200)]
    with pytest.raises(UntrustedDescriptorRejected):
        sanitize_tool_descriptors(payload)


def test_the_description_is_truncated_rather_than_dropped():
    tools = sanitize_tool_descriptors([{"name": "t", "description": "x" * 5000, "inputSchema": {"type": "object"}}])
    assert len(tools[0].description) == 1024
