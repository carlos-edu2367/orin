from __future__ import annotations

import pytest

from agentos.code_mode.models import (
    INTERNAL_PERMISSION_MODES,
    CodeAutonomy,
    CodePermissionMode,
    normalize_code_autonomy,
    normalize_permission_mode,
    public_permission_mode,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("default", CodePermissionMode.ASK_PERMISSIONS),
        ("manual", CodePermissionMode.ASK_PERMISSIONS),
        ("acceptEdits", CodePermissionMode.ACCEPT_EDITS),
        ("plan", CodePermissionMode.PLAN_MODE),
        ("bypassPermissions", CodePermissionMode.FULL_AUTONOMY),
        ("auto", CodePermissionMode.AUTO),
        ("dontAsk", CodePermissionMode.DONT_ASK),
        ("dont_ask", CodePermissionMode.DONT_ASK),
        ("approval_required", CodePermissionMode.ASK_PERMISSIONS),
        ("code_autonomy", CodePermissionMode.ACCEPT_EDITS),
        ("  ACCEPTEDITS ", CodePermissionMode.ACCEPT_EDITS),
        ("nonsense", CodePermissionMode.ASK_PERMISSIONS),
        (None, CodePermissionMode.ASK_PERMISSIONS),
    ],
)
def test_normalize_accepts_claude_code_names(raw, expected) -> None:
    assert normalize_permission_mode(raw) is expected


def test_public_names_round_trip() -> None:
    assert [public_permission_mode(mode) for mode in CodePermissionMode] == [
        "default", "acceptEdits", "plan", "bypassPermissions", "auto", "dontAsk",
    ]
    for mode in CodePermissionMode:
        assert normalize_permission_mode(public_permission_mode(mode)) is mode
    assert public_permission_mode("plan_mode") == "plan"


def test_autonomy_supports_new_modes() -> None:
    assert normalize_code_autonomy("auto") is CodeAutonomy.AUTO
    assert normalize_code_autonomy(CodePermissionMode.DONT_ASK) is CodeAutonomy.DONT_ASK
    assert normalize_permission_mode(CodeAutonomy.AUTO) is CodePermissionMode.AUTO
    assert INTERNAL_PERMISSION_MODES == frozenset({"ask_permissions", "accept_edits", "plan_mode", "full_autonomy", "auto", "dont_ask"})


def test_chat_store_and_gateway_accept_new_modes() -> None:
    from agentos.conversations import chat

    assert {"auto", "dont_ask"} <= set(chat._VALID_PERMISSION_MODES)
    from agentos.api import gateway

    source = open(gateway.__file__, encoding="utf-8").read()
    assert "auto|dont_ask" in source
