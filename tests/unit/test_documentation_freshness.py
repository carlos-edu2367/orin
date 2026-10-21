from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    path = ROOT / rel_path
    assert path.is_file(), f"Expected documentation file to exist: {rel_path}"
    return path.read_text(encoding="utf-8")


def test_readme_reflects_current_architecture_and_features() -> None:
    readme = _read("README.md")

    # Removed internal browser CLI and Chromium installer claims must be gone
    assert "orin browser install" not in readme
    assert "cerca de 150 MB" not in readme

    # Current browser integration via Playwright MCP
    assert "Playwright MCP" in readme
    assert "@playwright/mcp" in readme

    # Orin Code CLI, SDK compatibility, and handoff
    assert "orin code" in readme
    assert "orin-code" in readme
    assert "claude-agent-sdk" in readme

    # OpenCode provider & local bridge
    assert "OpenCode" in readme
    assert "opencode serve" in readme

    # Permission modes, AST shell security, Workspace Trust, Hooks, and LSP
    for mode in ("default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"):
        assert mode in readme, f"Expected permission mode {mode!r} in README.md"
    assert "tree-sitter" in readme
    assert "Workspace Trust" in readme
    assert "docs/ORIN_CODE.md" in readme


def test_launcher_doc_has_no_corrupted_paragraphs_and_lists_all_commands() -> None:
    launcher = _read("docs/LAUNCHER.md")

    assert launcher.count("launcher confirms readiness it provides the chosen loopback URL") <= 1
    assert "a future `orin update`" not in launcher
    assert "orin code" in launcher
    assert "orin serve" in launcher
    assert "orin user" in launcher
    assert "~/.local/share/Orin" in launcher


def test_mcp_plugins_and_skills_docs_reflect_w5_and_lazy_loading() -> None:
    mcp = _read("docs/MCP.md")
    assert "Tools only." not in mcp
    assert "Resources and prompts are not implemented." not in mcp
    for expected in ("sse", "ws", "ListMcpResources", "ReadMcpResource", "search_mcp_tools", "orin code mcp serve"):
        assert expected in mcp, f"Expected {expected!r} in docs/MCP.md"

    plugins = _read("docs/PLUGINS.md")
    assert "hooks/ é detectado e mostrado como aviso, mas não é ativado" not in plugins
    for expected in (".orin-plugin/plugin.json", "hooks/hooks.json", ".lsp.json", "userConfig", "orin code plugin"):
        assert expected in plugins, f"Expected {expected!r} in docs/PLUGINS.md"

    skills = _read("docs/CREATING_SKILLS.md")
    for expected in (".orin/skills", ".claude/skills", "allowed-tools", "context: fork", "$ARGUMENTS"):
        assert expected in skills, f"Expected {expected!r} in docs/CREATING_SKILLS.md"


def test_orin_code_guide_exists_and_covers_core_surfaces() -> None:
    orin_code = _read("docs/ORIN_CODE.md")
    for expected in (
        "orin code -p",
        "stream-json",
        "claude-agent-sdk",
        "acceptEdits",
        "bypassPermissions",
        "ORIN.md",
        "CLAUDE.md",
        "PreToolUse",
        "Workspace Trust",
        "keybindings.json",
        "/context",
        "/rewind",
    ):
        assert expected in orin_code, f"Expected {expected!r} in docs/ORIN_CODE.md"


def test_design_ux_eval_and_adrs_have_no_stale_browser_or_infra_claims() -> None:
    ux = _read("UX-CONTRACT.md")
    assert "acceptEdits" in ux
    assert "Playwright MCP" in ux

    eval_readme = _read("tests/eval/README.md")
    assert "verify_frontend.payload.all_ok" not in eval_readme

    for adr_file in (
        "docs/adr/001-arq-workers.md",
        "docs/adr/002-postgresql-as-system-of-record.md",
        "docs/adr/004-playwright-browser-workers.md",
        "docs/adr/009-redis-for-ephemeral-coordination.md",
        "docs/architecture/400-tools-resources/405-browser.md",
        "docs/architecture/900-extensibility/903-mcp-future.md",
    ):
        content = _read(adr_file)
        assert "SQLite" in content or "Playwright MCP" in content or "ToolSearch" in content, (
            f"Expected local-first/MCP evolution note in {adr_file}"
        )


def test_all_relative_markdown_links_in_core_docs_resolve() -> None:
    doc_files = [
        ROOT / "README.md",
        ROOT / "DESIGN.md",
        ROOT / "UX-CONTRACT.md",
        *sorted((ROOT / "docs").glob("*.md")),
        *sorted((ROOT / "docs" / "adr").glob("*.md")),
    ]
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

    for doc_path in doc_files:
        text = doc_path.read_text(encoding="utf-8")
        # Strip fenced code blocks before scanning links
        text_no_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        for match in link_pattern.finditer(text_no_code):
            raw_target = match.group(1).strip()
            if not raw_target or raw_target.startswith(("#", "http://", "https://", "mailto:", "workspace://")):
                continue
            target_Part = raw_target.split("#", 1)[0].strip()
            if not target_Part:
                continue
            resolved = (doc_path.parent / target_Part).resolve()
            assert resolved.exists(), f"Broken link in {doc_path.relative_to(ROOT)}: {raw_target!r} -> {resolved}"
