from pathlib import Path


def test_process_primitive_isolated_to_local_adapter_and_shell_is_never_true() -> None:
    package = Path("src/agentos/terminal")
    local = (package / "local.py").read_text(encoding="utf-8")
    domain = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py") if path.name != "local.py")
    assert "shell=True" not in local
    assert "shell=True" not in domain
    assert "subprocess" not in domain
    assert "os.system" not in domain
    assert "native_handle" not in domain
