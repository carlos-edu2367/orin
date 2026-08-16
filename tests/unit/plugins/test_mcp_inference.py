import json

from agentos.plugins.mcp_inference import infer_mcp_launch


def test_infers_stdio_from_mcp_json(tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"demo": {"command": "python", "args": ["-m", "demo"], "env": {"API_KEY": ""}}}}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.transport == "stdio"
    assert guess.command == "python"
    assert guess.args == ("-m", "demo")
    assert guess.secret_names == ("API_KEY",)
    assert guess.confidence == "structured"


def test_infers_http_from_smithery_json(tmp_path):
    (tmp_path / "smithery.json").write_text(json.dumps({"url": "https://mcp.example.com/v1"}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.transport == "http"
    assert guess.url == "https://mcp.example.com/v1"
    assert guess.command is None
    assert guess.confidence == "structured"


def test_infers_npx_from_package_json_bin_field(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "@acme/demo-mcp", "bin": "./cli.js"}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.transport == "stdio"
    assert guess.command == "npx"
    assert guess.args == ("-y", "@acme/demo-mcp")
    assert guess.confidence == "structured"


def test_package_json_without_a_bin_field_is_not_a_signal(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "not-a-cli"}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.confidence == "none"


def test_infers_uvx_from_pyproject_scripts(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo-mcp"\n\n[project.scripts]\ndemo-mcp = "demo_mcp:main"\n', encoding="utf-8",
    )
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.transport == "stdio"
    assert guess.command == "uvx"
    assert guess.args == ("demo-mcp",)
    assert guess.confidence == "structured"


def test_mcp_json_takes_priority_over_package_json(tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps({"command": "node", "args": ["server.js"]}), encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"name": "demo-mcp", "bin": "./cli.js"}), encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.command == "node"


def test_returns_a_blank_guess_when_nothing_matches(tmp_path):
    (tmp_path / "README.md").write_text("just docs", encoding="utf-8")
    guess = infer_mcp_launch(tmp_path, suggested_name="demo-repo")
    assert guess.display_name == "demo-repo"
    assert guess.transport is None
    assert guess.command is None
    assert guess.url is None
    assert guess.args == ()
    assert guess.secret_names == ()
    assert guess.confidence == "none"
