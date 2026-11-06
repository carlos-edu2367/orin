from __future__ import annotations

from pathlib import Path


def test_no_module_in_claude_cli_or_cli_tools_mcp_imports_opencode() -> None:
    providers_root = Path(__file__).resolve().parents[4] / "src" / "agentos" / "providers"
    checked = 0
    for path in providers_root.rglob("*.py"):
        if "claude_cli" in path.parts or "cli_tools_mcp" in path.parts or path.name == "cli_adapter.py":
            checked += 1
            text = path.read_text(encoding="utf-8")
            assert "provider_catalog.opencode" not in text, f"{path} references opencode"
            assert "opencode" not in text, f"{path} references opencode"
    assert checked >= 7


def test_cli_adapter_does_not_import_claude_cli() -> None:
    cli_adapter_path = (
        Path(__file__).resolve().parents[4]
        / "src"
        / "agentos"
        / "providers"
        / "cli_adapter.py"
    )
    text = cli_adapter_path.read_text(encoding="utf-8")
    assert "claude_cli" not in text
    assert "claude_code" not in text
