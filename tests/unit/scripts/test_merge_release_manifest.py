import json
import subprocess
import sys
from pathlib import Path

from scripts.merge_release_manifest import merge_manifest


def test_merge_keeps_windows_fields_flat_for_backward_compatibility():
    windows = {"version": "0.3.0", "archive_url": "https://x/Orin-0.3.0-windows-x64.zip", "archive_sha256": "a" * 64}
    linux = {"archive_url": "https://x/Orin-0.3.0-linux-x64.tar.gz", "archive_sha256": "b" * 64}

    manifest = merge_manifest(windows, linux, release_url="https://x/releases/tag/v0.3.0")

    assert manifest["version"] == "0.3.0"
    assert manifest["archive_url"] == windows["archive_url"]
    assert manifest["archive_sha256"] == windows["archive_sha256"]
    assert manifest["release_url"] == "https://x/releases/tag/v0.3.0"


def test_merge_nests_linux_under_platforms():
    windows = {"version": "0.3.0", "archive_url": "https://x/win.zip", "archive_sha256": "a" * 64}
    linux = {"archive_url": "https://x/Orin-0.3.0-linux-x64.tar.gz", "archive_sha256": "b" * 64}

    manifest = merge_manifest(windows, linux, release_url="https://x/releases/tag/v0.3.0")

    assert manifest["platforms"]["linux-x64"] == {
        "archive_url": "https://x/Orin-0.3.0-linux-x64.tar.gz",
        "archive_sha256": "b" * 64,
    }
    # A raiz do manifesto não ganha nenhuma chave nova além de "platforms":
    # um install.ps1 já instalado só sabe procurar as quatro chaves originais.
    assert set(manifest.keys()) == {"version", "archive_url", "archive_sha256", "release_url", "platforms"}


def test_merge_rejects_a_windows_manifest_missing_required_fields():
    import pytest

    with pytest.raises(ValueError, match="archive_sha256"):
        merge_manifest({"version": "0.3.0", "archive_url": "https://x/win.zip"}, {"archive_url": "https://x/l.tar.gz", "archive_sha256": "b" * 64}, release_url="https://x")


def test_cli_writes_the_merged_manifest_to_stdout(tmp_path: Path):
    windows_file = tmp_path / "windows.json"
    linux_file = tmp_path / "linux.json"
    windows_file.write_text(json.dumps({"version": "0.3.0", "archive_url": "https://x/win.zip", "archive_sha256": "a" * 64}), encoding="utf-8")
    linux_file.write_text(json.dumps({"archive_url": "https://x/l.tar.gz", "archive_sha256": "b" * 64}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/merge_release_manifest.py", str(windows_file), str(linux_file), "https://x/releases/tag/v0.3.0"],
        capture_output=True, text=True, check=True,
    )

    output = json.loads(result.stdout)
    assert output["version"] == "0.3.0"
    assert output["platforms"]["linux-x64"]["archive_url"] == "https://x/l.tar.gz"
