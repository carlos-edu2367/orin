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


def test_a_complex_nested_object_schema_survives():
    schema: dict = {"type": "object", "properties": {}}
    cursor = schema
    for _ in range(6):
        cursor["properties"]["step"] = {"type": "object", "properties": {}}
        cursor = cursor["properties"]["step"]
    cursor["properties"]["padding"] = {"type": "string", "description": "x" * 30_000}
    tools = sanitize_tool_descriptors([{"name": "save_funnel_draft", "description": "d", "inputSchema": schema}])
    assert [item.name for item in tools] == ["save_funnel_draft"]


def test_a_deeply_nested_schema_is_dropped():
    schema: dict = {"type": "object", "properties": {}}
    cursor = schema
    for _ in range(12):
        cursor["properties"]["next"] = {"type": "object", "properties": {}}
        cursor = cursor["properties"]["next"]
    assert sanitize_tool_descriptors([{"name": "deep", "description": "", "inputSchema": schema}]) == ()


def test_large_descriptor_batches_are_accepted_without_artificial_cap():
    payload = [
        {"name": f"tool_{index}", "description": f"Tool {index}", "inputSchema": {"type": "object"}}
        for index in range(500)
    ]
    payload.append({"name": "bad tool name!", "description": "dropped", "inputSchema": {"type": "object"}})
    sanitized = sanitize_tool_descriptors(payload)
    assert len(sanitized) == 500
    assert sanitized[0].name == "tool_0"
    assert sanitized[-1].name == "tool_499"



def test_the_description_is_truncated_rather_than_dropped():
    tools = sanitize_tool_descriptors([{"name": "t", "description": "x" * 10_000, "inputSchema": {"type": "object"}}])
    assert len(tools[0].description) == 8192

