"""Unit tests for Discord Markdown chunking, Embed/Component formatting, REST anti-SSRF, and rate-limit handling."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agentos.discord import (
    ALLOWED_DISCORD_ATTACHMENT_HOSTS,
    BUTTON_MAX_LABEL_CHARS,
    CUSTOM_ID_MAX_CHARS,
    DISCORD_MESSAGE_MAX_CHARS,
    DiscordApiError,
    DiscordAttachmentError,
    DiscordFileAttachment,
    DiscordRestClient,
    EMBED_MAX_DESCRIPTION_CHARS,
    EMBED_MAX_FIELDS,
    EMBED_MAX_FIELD_NAME_CHARS,
    EMBED_MAX_FIELD_VALUE_CHARS,
    EMBED_MAX_TITLE_CHARS,
    SAFE_ALLOWED_MENTIONS,
    SELECT_MAX_OPTIONS,
    SELECT_OPTION_MAX_CHARS,
    build_decision_card,
    build_live_progress_embed,
    build_progress_components,
    build_resolved_decision_components,
    compute_progress_digest_hash,
    split_discord_markdown,
    strip_residual_speak_tags,
    validate_discord_attachment_url,
)


def test_strip_residual_speak_tags_cleans_voice_wrappers() -> None:
    raw = "<speak>Olá Carlos!</speak>\n\n\n<SPEAK lang='pt-BR'>Tudo pronto no projeto.</SPEAK>"
    cleaned = strip_residual_speak_tags(raw)
    assert "<speak" not in cleaned.lower()
    assert "</speak>" not in cleaned.lower()
    assert cleaned == "Olá Carlos!\n\nTudo pronto no projeto."


def test_split_discord_markdown_preserves_code_fences_and_respects_2000_limit() -> None:
    code_lines = [f"def funcao_{i}(x: int) -> int:\n    return x + {i}" for i in range(120)]
    long_markdown = (
        "<speak>Resumo da execução:</speak>\n\n"
        "Aqui está o código gerado:\n"
        "```python\n"
        + "\n".join(code_lines)
        + "\n```\n"
        "Finalizado com sucesso!"
    )

    chunks = split_discord_markdown(long_markdown, limit=1950)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert len(chunk) <= DISCORD_MESSAGE_MAX_CHARS
        assert len(chunk) <= 1950
        assert "<speak>" not in chunk

    # Every intermediate chunk that splits the python block must close ``` at the end
    # and the following chunk must reopen ```python at the start.
    fence_chunks = [c for c in chunks if "def funcao_" in c]
    assert len(fence_chunks) >= 2
    assert fence_chunks[0].rstrip().endswith("```")
    assert fence_chunks[1].lstrip().startswith("```python\n")


def test_split_discord_markdown_splits_single_huge_line_safely() -> None:
    huge_line = "A" * 4500
    chunks = split_discord_markdown(huge_line, limit=1900)
    assert len(chunks) == 3
    assert all(len(c) <= 1900 for c in chunks)
    assert "".join(chunks) == huge_line


def test_live_progress_embed_and_digest_hash_enforce_discord_limits() -> None:
    snapshot = {
        "conversation_id": "chat_target_123",
        "title": "T" * 400,
        "mode": "code",
        "state": "running",
        "project_name": "Educly App",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5",
        "permission_mode": "full_autonomy",
        "current_step": "S" * 5000,
        "todos": [
            {"content": f"Tarefa {i} " + ("X" * 120), "status": "completed" if i < 5 else "in_progress"}
            for i in range(20)
        ],
        "files_modified": [f"src/module_{i}/arquivo_muito_longo_{'y' * 80}.py" for i in range(20)],
    }

    embed = build_live_progress_embed(snapshot)
    assert len(embed["title"]) <= EMBED_MAX_TITLE_CHARS
    assert len(embed["description"]) <= EMBED_MAX_DESCRIPTION_CHARS
    assert len(embed["fields"]) <= EMBED_MAX_FIELDS
    for field in embed["fields"]:
        assert len(field["name"]) <= EMBED_MAX_FIELD_NAME_CHARS
        assert len(field["value"]) <= EMBED_MAX_FIELD_VALUE_CHARS

    hash_1 = compute_progress_digest_hash(snapshot)
    hash_2 = compute_progress_digest_hash(dict(snapshot))
    assert hash_1 == hash_2

    modified_snapshot = {**snapshot, "state": "completed"}
    assert compute_progress_digest_hash(modified_snapshot) != hash_1

    active_components = build_progress_components("dbnd_stop123", active=True)
    assert len(active_components) == 1
    btn = active_components[0]["components"][0]
    assert btn["type"] == 2
    assert btn["custom_id"] == "orin:v1:dbnd_stop123"
    assert len(btn["custom_id"]) <= CUSTOM_ID_MAX_CHARS
    assert build_progress_components("dbnd_stop123", active=False) == []


def test_build_decision_card_buttons_for_up_to_5_and_string_select_for_6_to_25() -> None:
    # 1. Up to 5 options -> Buttons (component_type=2)
    bindings_3 = [
        {"binding_id": f"dbnd_opt_{i}", "action_kind": "ask_user", "payload": {"label": f"Opção {i}", "answer": f"ans_{i}"}}
        for i in range(1, 4)
    ]
    embed_btn, comps_btn = build_decision_card(
        {
            "kind": "ask_user",
            "title": "Sessão Backend",
            "conversation_id": "chat_1",
            "question": "Qual banco usar na migração?",
        },
        bindings_3,
    )
    assert "❓" in embed_btn["title"]
    assert len(comps_btn) == 1
    assert all(c["type"] == 2 for c in comps_btn[0]["components"])
    assert [c["custom_id"] for c in comps_btn[0]["components"]] == [
        "orin:v1:dbnd_opt_1",
        "orin:v1:dbnd_opt_2",
        "orin:v1:dbnd_opt_3",
    ]

    # 2. 6..25 options via single binding payload -> StringSelect (component_type=3)
    many_options = [
        {"label": f"Modelo {i} " + ("M" * 120), "value": f"model_{i}", "description": f"Descrição {i}"}
        for i in range(1, 9)
    ]
    embed_sel, comps_sel = build_decision_card(
        {
            "kind": "ask_user",
            "title": "Escolha de Modelo",
            "conversation_id": "chat_2",
            "questions": [{"header": "Modelo", "question": "Selecione um dos 8 modelos:", "options": many_options}],
        },
        [
            {
                "binding_id": "dbnd_select_all",
                "action_kind": "ask_user",
                "payload": {"options": many_options},
            }
        ],
    )
    assert len(comps_sel) == 1
    select_comp = comps_sel[0]["components"][0]
    assert select_comp["type"] == 3
    assert select_comp["custom_id"] == "orin:v1:dbnd_select_all"
    assert len(select_comp["options"]) == 8
    for opt in select_comp["options"]:
        assert len(opt["label"]) <= SELECT_OPTION_MAX_CHARS
        assert len(opt["value"]) <= SELECT_OPTION_MAX_CHARS

    # 3. 6..25 separate bindings -> StringSelect (component_type=3)
    bindings_7 = [
        {"binding_id": f"dbnd_many_{i}", "action_kind": "ask_user", "payload": {"label": f"Item {i}", "value": f"v_{i}"}}
        for i in range(7)
    ]
    _, comps_sel_multi = build_decision_card({"kind": "ask_user", "question": "Escolha:"}, bindings_7)
    assert comps_sel_multi[0]["components"][0]["type"] == 3
    assert len(comps_sel_multi[0]["components"][0]["options"]) == 7

    # 4. Resolved decision components disable button
    resolved = build_resolved_decision_components("Aprovar")
    assert resolved[0]["components"][0]["disabled"] is True
    assert resolved[0]["components"][0]["label"].startswith("✔")


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "http://cdn.discordapp.com/attachments/1/2/file.png",
        "https://evil.com/attachments/1/2/file.png",
        "https://cdn.discordapp.com.evil.com/attachments/1/2/file.png",
        "https://127.0.0.1/attachments/1/2/file.png",
        "https://169.254.169.254/latest/meta-data/",
        "https://192.168.1.10/file.png",
        "https://[::1]/file.png",
        "https://user:pass@cdn.discordapp.com/attachments/1/2/file.png",
        "https://cdn.discordapp.com:8443/attachments/1/2/file.png",
        "https://cdn.discordapp.com/",
    ],
)
def test_validate_discord_attachment_url_blocks_ssrf_and_non_cdn_hosts(bad_url: str) -> None:
    with pytest.raises(DiscordAttachmentError):
        validate_discord_attachment_url(bad_url)


def test_validate_discord_attachment_url_accepts_official_discord_cdn_hosts() -> None:
    for host in ALLOWED_DISCORD_ATTACHMENT_HOSTS:
        url = f"https://{host}/attachments/123456789012345678/987654321098765432/print.png?ex=1&is=2&hm=3"
        assert validate_discord_attachment_url(url) == url


@pytest.mark.anyio
async def test_rest_client_download_attachment_enforces_anti_ssrf_redirects_and_max_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/redirect-evil.png"):
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8080/secret"})
        if request.url.path.endswith("/too-big-header.png"):
            return httpx.Response(200, headers={"Content-Length": "5000"}, content=b"x" * 100)
        if request.url.path.endswith("/too-big-stream.png"):
            return httpx.Response(200, content=b"y" * 2048)
        return httpx.Response(200, content=b"valid-image-bytes")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        rest = DiscordRestClient("bot-token-xyz", client=http_client)

        # Valid download from media.discordapp.net
        data = await rest.download_attachment(
            "https://media.discordapp.net/attachments/111/222/ok.png",
            max_bytes=1024,
        )
        assert data == b"valid-image-bytes"

        # Redirect to internal IP must be blocked by anti-SSRF
        with pytest.raises(DiscordAttachmentError):
            await rest.download_attachment(
                "https://cdn.discordapp.com/attachments/111/222/redirect-evil.png",
                max_bytes=1024,
            )

        # Declared Content-Length over max_bytes rejected immediately
        with pytest.raises(DiscordAttachmentError):
            await rest.download_attachment(
                "https://cdn.discordapp.com/attachments/111/222/too-big-header.png",
                max_bytes=1024,
            )

        # Streamed body exceeding max_bytes rejected during stream
        with pytest.raises(DiscordAttachmentError):
            await rest.download_attachment(
                "https://cdn.discordapp.com/attachments/111/222/too-big-stream.png",
                max_bytes=1024,
            )


@pytest.mark.anyio
async def test_rest_client_enforces_safe_allowed_mentions_and_retries_429() -> None:
    recorded_requests: list[httpx.Request] = []
    slept_intervals: list[float] = []
    attempt_counter = 0

    async def fake_sleep(seconds: float) -> None:
        slept_intervals.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_counter
        recorded_requests.append(request)
        if request.url.path.endswith("/messages") and request.method == "POST":
            attempt_counter += 1
            if attempt_counter == 1:
                return httpx.Response(
                    429,
                    json={"message": "You are being rate limited.", "retry_after": 0.25},
                )
            return httpx.Response(200, json={"id": "123456789012345678", "channel_id": "999999999999999999"})
        if request.url.path.endswith("/callback") and request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "123456789012345678"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        rest = DiscordRestClient("secret-bot-token", client=http_client, sleep_fn=fake_sleep)

        sent = await rest.send_message(
            "999999999999999999",
            content="Olá @everyone @here",
            reply_to_message_id="888888888888888888",
        )
        assert sent["id"] == "123456789012345678"
        assert attempt_counter == 2
        assert slept_intervals == [0.25]

        post_body = json.loads(recorded_requests[1].content.decode("utf-8"))
        assert post_body["allowed_mentions"] == SAFE_ALLOWED_MENTIONS
        assert post_body["message_reference"]["message_id"] == "888888888888888888"

        # Edit message also enforces allowed_mentions={"parse": []}
        await rest.edit_message("999999999999999999", "123456789012345678", content="Editado")
        patch_body = json.loads(recorded_requests[-1].content.decode("utf-8"))
        assert patch_body["allowed_mentions"] == SAFE_ALLOWED_MENTIONS

        # Interaction callback with data also enforces allowed_mentions={"parse": []}
        await rest.ack_interaction(
            "777777777777777777",
            "tok_abc",
            response_type=4,
            data={"content": "Acesso não autorizado.", "flags": 64},
        )
        ack_body = json.loads(recorded_requests[-1].content.decode("utf-8"))
        assert ack_body["type"] == 4
        assert ack_body["data"]["allowed_mentions"] == SAFE_ALLOWED_MENTIONS


def test_sanitize_custom_id_strips_control_chars_and_normalizes_prefix_colons() -> None:
    from agentos.discord.formatter import CUSTOM_ID_MAX_CHARS, sanitize_custom_id

    cid = sanitize_custom_id("dbnd_12345\n\r\t", prefix="orin:v1:extra:colon")
    assert "\n" not in cid
    assert "\r" not in cid
    assert len(cid) <= CUSTOM_ID_MAX_CHARS
    # Must still parse cleanly via split(":", 2) in bridge_service._extract_binding_id
    parts = cid.split(":", 2)
    assert len(parts) == 3
    assert parts[2] == "dbnd_12345"

    long_cid = sanitize_custom_id("dbnd_" + ("x" * 150))
    assert len(long_cid) == CUSTOM_ID_MAX_CHARS

