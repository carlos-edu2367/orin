import pytest

from agentos.mcp.protocol import McpProtocolError, notification, parse_response, request


def test_request_carries_an_explicit_id_and_version():
    assert request(1, "tools/list", {"cursor": None}) == {
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"cursor": None},
    }


def test_notification_has_no_id():
    assert "id" not in notification("notifications/initialized")


def test_parse_response_returns_the_result_for_a_matching_id():
    assert parse_response({"jsonrpc": "2.0", "id": 7, "result": {"tools": []}}, expected_id=7) == {"tools": []}


def test_parse_response_raises_on_an_error_frame():
    with pytest.raises(McpProtocolError) as error:
        parse_response({"jsonrpc": "2.0", "id": 7, "error": {"code": -32601, "message": "no such method"}}, expected_id=7)
    assert "no such method" in str(error.value)


def test_parse_response_raises_when_the_id_does_not_match():
    with pytest.raises(McpProtocolError):
        parse_response({"jsonrpc": "2.0", "id": 9, "result": {}}, expected_id=7)
