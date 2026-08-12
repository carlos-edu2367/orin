from pathlib import Path

import pytest

from agentos.local_workspace.paths import FolderRejected, classify_risk, inspect_folder, normalize_path


def test_normalize_expands_home_and_resolves(tmp_path: Path) -> None:
    target = tmp_path / "projeto"
    target.mkdir()
    assert normalize_path(f"  {target}  ") == target.resolve()


def test_normalize_rejects_blank_and_relative() -> None:
    with pytest.raises(FolderRejected):
        normalize_path("   ")
    with pytest.raises(FolderRejected):
        normalize_path("projetos/site")


def test_classify_risk_names_the_broad_choices(tmp_path: Path) -> None:
    home = tmp_path / "home"
    orin = tmp_path / "home" / ".orin"
    (tmp_path / "home" / "codigo").mkdir(parents=True)
    orin.mkdir(parents=True)
    root = Path(tmp_path.anchor)

    assert classify_risk(root, home=home, orin_data=orin, system_prefixes=()) == "drive_root"
    assert classify_risk(home, home=home, orin_data=orin, system_prefixes=()) == "home_root"
    assert classify_risk(orin / "workspaces", home=home, orin_data=orin, system_prefixes=()) == "orin_data"
    assert classify_risk(tmp_path / "sys" / "bin", home=home, orin_data=orin, system_prefixes=(tmp_path / "sys",)) == "system"
    assert classify_risk(home / "codigo", home=home, orin_data=orin, system_prefixes=()) == "none"


def test_inspect_reports_a_usable_folder(tmp_path: Path) -> None:
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_text("<h1>oi</h1>", encoding="utf-8")
    (folder / "src").mkdir()

    result = inspect_folder(str(folder), home=tmp_path, orin_data=tmp_path / ".orin")

    assert result.path == str(folder.resolve())
    assert result.exists is True
    assert result.is_directory is True
    assert result.writable is True
    assert result.entry_count == 2
    assert result.entries_truncated is False
    assert result.risk == "none"


def test_inspect_reports_missing_and_non_directory(tmp_path: Path) -> None:
    missing = inspect_folder(str(tmp_path / "nao-existe"), home=tmp_path, orin_data=tmp_path / ".orin")
    assert missing.exists is False and missing.is_directory is False and missing.entry_count == 0

    file_path = tmp_path / "arquivo.txt"
    file_path.write_text("x", encoding="utf-8")
    as_file = inspect_folder(str(file_path), home=tmp_path, orin_data=tmp_path / ".orin")
    assert as_file.exists is True and as_file.is_directory is False


def test_inspect_caps_the_entry_count(tmp_path: Path) -> None:
    folder = tmp_path / "grande"
    folder.mkdir()
    for index in range(505):
        (folder / f"f{index}.txt").write_text("x", encoding="utf-8")

    result = inspect_folder(str(folder), home=tmp_path, orin_data=tmp_path / ".orin")

    assert result.entry_count == 500
    assert result.entries_truncated is True
