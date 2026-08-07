from pathlib import Path


def test_local_filesystem_keeps_platform_apis_inside_adapter_module() -> None:
    root = Path(__file__).parents[3] / "src" / "agentos" / "filesystem"
    public = "\n".join((root / name).read_text(encoding="utf-8") for name in ("models.py", "ports.py", "service.py"))
    assert "pathlib" not in public.lower()
    assert "subprocess" not in public.lower()
    assert "physical_path" not in (root / "local.py").read_text(encoding="utf-8").lower()
