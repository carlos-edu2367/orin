from __future__ import annotations

import time

import pytest

from agentos.discord.bridge_service import (
    DiscordBridgeService,
    _iter_markdown_targets,
    _iter_plain_media_paths,
)
from agentos.discord.formatter import iter_markdown_target_spans, strip_attached_workspace_links
from agentos.redaction import REDACTED, redact_text, redact_value


def test_markdown_and_media_scanners_linear_on_adversarial_inputs() -> None:
    # 1. Exact unclosed mid-stream pattern that froze live backend PID 30912 (scaled to 100 KB)
    midstream = "[caption.txt](workspace://instagram/studio/renders/2026-11-15/reel-01" + ("a" * 100000)
    t0 = time.perf_counter()
    assert list(_iter_markdown_targets(midstream)) == []
    assert (time.perf_counter() - t0) < 0.05

    # 2. Repeated 50-100 KB unclosed links, images, angles, quotes, CRLF, and plain media tokens
    adversarial_cases = [
        ("[x](" + ("b" * 120) + " ") * 800,
        "![a](" * 20000,
        "[a](workspace://" * 6500,
        "[bad](<http://x " * 6500,
        '[bad](http://x "unclosed ' * 4000,
        "[bad](http://x 'unclosed " * 4000,
        "[bad](\r\n  <http://x\r\n" * 4500,
    ]
    for payload in adversarial_cases:
        t0 = time.perf_counter()
        assert list(iter_markdown_target_spans(payload)) == []
        stripped = strip_attached_workspace_links(payload, ["workspace://shot.png"])
        elapsed = time.perf_counter() - t0
        assert isinstance(stripped, str)
        assert elapsed < 0.12, f"Markdown scanner/stripper took {elapsed:.3f}s on len={len(payload)}"

    media_adversarial = "=/a" * 35000
    t0 = time.perf_counter()
    assert list(_iter_plain_media_paths(media_adversarial)) == []
    assert (time.perf_counter() - t0) < 0.08


def test_markdown_scanner_preserves_valid_links_and_all_four_edge_cases() -> None:
    sample = (
        'Veja ![preview](workspace://renders/frame_(1).png "Titulo") '
        "e [relatorio](<workspace://docs/meu arquivo.pdf>) "
        "e [site](https://example.com/a(b)c 'ok')."
    )
    assert list(_iter_markdown_targets(sample)) == [
        (True, "preview", "workspace://renders/frame_(1).png"),
        (False, "relatorio", "workspace://docs/meu arquivo.pdf"),
        (False, "site", "https://example.com/a(b)c"),
    ]

    # Edge case 1: clickable image [![alt](img)](doc) yields BOTH inner image and outer link
    clickable = "[![alt](workspace://img.png)](<workspace://doc.pdf>)"
    assert list(_iter_markdown_targets(clickable)) == [
        (True, "alt", "workspace://img.png"),
        (False, "![alt](workspace://img.png)", "workspace://doc.pdf"),
    ]

    # Edge case 2: escaped bracket inside label
    escaped = r"[a \] b](workspace://doc.pdf)"
    assert list(_iter_markdown_targets(escaped)) == [(False, r"a \] b", "workspace://doc.pdf")]

    # Edge case 3: CRLF / multiline-wrapped target
    multiline = "[doc](\r\n  <workspace://a.pdf>\r\n)"
    assert list(_iter_markdown_targets(multiline)) == [(False, "doc", "workspace://a.pdf")]

    # Edge case 4: valid link on the same line after unclosed `<`, quote, or `(`
    for prefix in (
        "[bad](<http://x ",
        '[bad](http://x "unclosed ',
        "[bad](http://x 'unclosed ",
        "[bad](http://x(y ",
        "[bad](http://x(y",
        "[bad](http://x",
    ):
        recovered = f"{prefix}![ok](workspace://shot.png)"
        assert list(_iter_markdown_targets(recovered)) == [(True, "ok", "workspace://shot.png")]

    cands = DiscordBridgeService._extract_outgoing_file_candidates({}, f"{clickable}\nscreenshots/final.png.")
    targets = [c for _, c in cands]
    assert "workspace://img.png" in targets
    assert "workspace://doc.pdf" in targets
    assert "screenshots/final.png" in targets

    cleaned = strip_attached_workspace_links(
        "Resultado: ![preview](workspace://img.png) e [Abrir](workspace://doc.pdf)",
        ["workspace://img.png", "workspace://doc.pdf"],
    )
    assert "workspace://img.png" not in cleaned
    assert "`Abrir`" in cleaned

    # Nested clickable image stripping: both attached vs only inner attached
    cleaned_both = strip_attached_workspace_links(
        f"Veja {clickable}",
        ["workspace://img.png", "workspace://doc.pdf"],
    )
    assert "workspace://img.png" not in cleaned_both
    assert "workspace://doc.pdf" not in cleaned_both
    assert "`doc.pdf`" in cleaned_both

    cleaned_inner_only = strip_attached_workspace_links(
        "[Ver ![alt](workspace://img.png)](https://example.com/external)",
        ["workspace://img.png"],
    )
    assert "workspace://img.png" not in cleaned_inner_only
    assert cleaned_inner_only == "[Ver](https://example.com/external)"

    cleaned_inner_empty_label = strip_attached_workspace_links(
        "[![alt](workspace://img.png)](https://example.com/external)",
        ["workspace://img.png"],
    )
    assert cleaned_inner_empty_label == "[](https://example.com/external)"


def test_redact_text_linear_on_adversarial_inputs() -> None:
    payloads = [
        ("auth_" * 3000) + " end",
        "--token-" * 3000,
        "token-" * 8000,
        "A" * 50000,
        "-" * 50000,
    ]
    t0 = time.perf_counter()
    for p in payloads:
        assert redact_text(p) == p
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.08, f"Expected strict O(N) < 80ms across all adversarial inputs, took {elapsed:.3f}s"

    # 100 KB adversarial inputs
    for p100k in (("auth_" * 20000), ("--token-" * 12500), ("token-" * 17000), ("A" * 100000), ("-" * 100000)):
        t1 = time.perf_counter()
        assert redact_text(p100k) == p100k
        assert (time.perf_counter() - t1) < 0.08


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("GITHUB_TOKEN=ghp_1234567890abcdefghij123456", "ghp_1234567890abcdefghij123456"),
        ('"client_secret": "my-super-secret"', "my-super-secret"),
        ('--api-key="secret123"', "secret123"),
        ("postgres://admin:s3cr3t@localhost:5432/db", "s3cr3t"),
        # 8 regression vectors where naive non-secret candidate consumption or truncation would leak secrets:
        ("curl https://api.example.com/v1?api_key=super_secret_q", "super_secret_q"),
        ('cmd="API_KEY=super_secret_env"', "super_secret_env"),
        ('--header="X-Api-Key: super_secret_hdr"', "super_secret_hdr"),
        ('sh -c "--api-key super_secret_flag"', "super_secret_flag"),
        ("env:API_KEY=super_secret_colon", "super_secret_colon"),
        ("SECRET_KEY_" + ("A" * 135) + "=super_secret_long", "super_secret_long"),
        ('{"api_key": "a\\"super_secret_esc\\"b"}', "super_secret_esc"),
        ('"api_key":\n  "super_secret_multiline"', "super_secret_multiline"),
        ("'api_key': 'a\\'super_secret_sq\\'b'", "super_secret_sq"),
        ("--secret-token-" + ("a" * 135) + "=super_secret_long_flag", "super_secret_long_flag"),
    ],
)
def test_redact_text_zero_secret_leaks(raw: str, secret: str) -> None:
    cleaned = redact_text(raw)
    assert secret not in cleaned
    assert REDACTED in cleaned


def test_redact_text_keeps_non_secrets_and_redacts_containers() -> None:
    plain = "normal_var=visible --verbose=true https://example.com/path"
    assert redact_text(plain) == plain
    nested = redact_value({"headers": {"X-Custom": "raw"}, "safe": "ok"})
    assert nested == {"headers": {"X-Custom": REDACTED}, "safe": "ok"}
