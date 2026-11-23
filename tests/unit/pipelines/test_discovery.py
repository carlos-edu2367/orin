from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentos.permissions.trust import WorkspaceTrustStore
from agentos.pipelines import paths
from agentos.pipelines.discovery import DiscoveryDisabled, discover, get_pipeline

USER = "user-1"


def doc(pipeline_id: str, name: str = "N", command: str = "pytest -q") -> str:
    return (
        f"version: 1\nid: {pipeline_id}\nname: {name}\nbudget: {{max_tokens: 1000}}\n"
        f"steps:\n  - id: verificar\n    gate:\n      all:\n        - command: {command}\n"
    )


@pytest.fixture
def world(tmp_path: Path):
    root = tmp_path / "repo"
    project = root / ".orin" / "pipelines"
    user = tmp_path / "home" / "pipelines"
    builtin = tmp_path / "builtin"
    for folder in (project, user, builtin):
        folder.mkdir(parents=True)
    trust = WorkspaceTrustStore(tmp_path / "trusted.json")

    class World:
        pass

    w = World()
    w.root, w.project, w.user, w.builtin, w.trust = root, project, user, builtin, trust
    w.run = lambda **kw: discover(  # type: ignore[attr-defined]
        USER, root, trust_store=trust, user_dir=user, builtin_dir=builtin, **kw
    )
    return w


def test_same_id_in_three_levels_project_wins_and_the_rest_is_shadowed(world) -> None:
    world.trust.trust(USER, world.root)
    (world.project / "dup.yaml").write_text(doc("dup", "do projeto"), encoding="utf-8")
    (world.user / "dup.yaml").write_text(doc("dup", "do usuario"), encoding="utf-8")
    (world.builtin / "dup.yaml").write_text(doc("dup", "embutido"), encoding="utf-8")
    result = world.run()
    assert [(e.id, e.source) for e in result.entries] == [("dup", "project")]
    assert result.entries[0].definition is not None and result.entries[0].definition.name == "do projeto"
    assert sorted(e.source for e in result.shadowed) == ["builtin", "user"]


def test_levels_are_listed_together_in_precedence_order(world) -> None:
    world.trust.trust(USER, world.root)
    (world.project / "b-proj.yaml").write_text(doc("b-proj"), encoding="utf-8")
    (world.user / "a-user.yaml").write_text(doc("a-user"), encoding="utf-8")
    (world.builtin / "c-built.yaml").write_text(doc("c-built"), encoding="utf-8")
    result = world.run()
    assert [(e.id, e.source) for e in result.entries] == [("b-proj", "project"), ("a-user", "user"), ("c-built", "builtin")]
    assert all(len(e.definition_hash) == 64 for e in result.entries)
    assert result.project_skipped == 0


def test_untrusted_workspace_lists_nothing_from_the_project_and_only_counts(world) -> None:
    (world.project / "p1.yaml").write_text(doc("p1"), encoding="utf-8")
    (world.project / "p2.yml").write_text(doc("p2"), encoding="utf-8")
    (world.user / "u1.yaml").write_text(doc("u1"), encoding="utf-8")
    result = world.run()
    assert [e.id for e in result.entries] == ["u1"]
    assert result.project_skipped == 2
    flat = repr(result)
    assert "p1" not in flat and "p2" not in flat
    assert result.shadowed == ()


def test_trust_comes_from_the_store_not_from_the_caller(world) -> None:
    (world.project / "p1.yaml").write_text(doc("p1"), encoding="utf-8")
    assert world.run().project_skipped == 1
    world.trust.trust(USER, world.root)
    assert [e.id for e in world.run().entries] == ["p1"]
    world.trust.revoke(USER, world.root)
    assert world.run().project_skipped == 1
    other = discover("user-2", world.root, trust_store=world.trust, user_dir=world.user, builtin_dir=world.builtin)
    assert other.project_skipped == 1


def test_yaml_and_yml_extensions(world) -> None:
    (world.user / "a.yaml").write_text(doc("a"), encoding="utf-8")
    (world.user / "b.yml").write_text(doc("b"), encoding="utf-8")
    (world.user / "c.txt").write_text(doc("c"), encoding="utf-8")
    (world.user / "sub").mkdir()
    (world.user / "sub" / "d.yaml").write_text(doc("d"), encoding="utf-8")
    assert [e.id for e in world.run().entries] == ["a", "b"]


def test_file_id_must_match_the_file_name(world) -> None:
    (world.user / "alpha.yaml").write_text(doc("beta"), encoding="utf-8")
    entry = world.run().entries[0]
    assert entry.id == "alpha"
    assert entry.definition is None
    assert [e.code for e in entry.errors] == ["E_ID"]
    assert "alpha" in entry.errors[0].message and "beta" in entry.errors[0].message


def test_one_broken_file_does_not_drop_the_others(world) -> None:
    (world.user / "bad.yaml").write_text("version: 1\nid: bad\nsteps: [\n", encoding="utf-8")
    (world.user / "good.yaml").write_text(doc("good"), encoding="utf-8")
    entries = {e.id: e for e in world.run().entries}
    assert entries["good"].definition is not None and entries["good"].errors == ()
    assert entries["bad"].definition is None and entries["bad"].errors[0].code == "E_YAML_SYNTAX"


def test_file_over_256_kb_is_ignored_with_an_error(world) -> None:
    big = world.user / "big.yaml"
    big.write_text("# " + "x" * (paths.MAX_FILE_BYTES + 10), encoding="utf-8")
    entry = world.run().entries[0]
    assert entry.id == "big" and entry.definition is None
    assert [e.code for e in entry.errors] == ["E_TOO_LARGE"]


def test_non_utf8_file_is_an_error(world) -> None:
    (world.user / "latin.yaml").write_bytes(b"version: 1\nname: \xff\xfe\n")
    entry = world.run().entries[0]
    assert entry.definition is None and entry.errors[0].code == "E_YAML_SYNTAX"


def test_symlink_pointing_outside_the_folder_is_rejected(world, tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text(doc("evil"), encoding="utf-8")
    link = world.user / "evil.yaml"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this host")
    (world.user / "ok.yaml").write_text(doc("ok"), encoding="utf-8")
    entries = {e.id: e for e in world.run().entries}
    assert entries["evil"].definition is None
    assert entries["evil"].errors[0].code == "E_YAML_FORBIDDEN"
    assert entries["ok"].definition is not None


def _link_directory(link: Path, target: Path) -> bool:
    """A directory symlink, or a junction on Windows (no privilege needed); False when neither is possible."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import subprocess

        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        return done.returncode == 0
    return False


def test_pipelines_folder_linked_outside_the_project_is_not_read(world, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.yaml").write_text(doc("x"), encoding="utf-8")
    import shutil

    shutil.rmtree(world.project)
    if not _link_directory(world.project, outside):
        pytest.skip("no directory link available on this host")
    world.trust.trust(USER, world.root)
    result = world.run()
    assert result.entries == ()
    assert result.project_skipped == 1


def test_missing_folders_are_an_empty_list(tmp_path: Path) -> None:
    result = discover(
        USER, tmp_path / "nowhere", trust_store=WorkspaceTrustStore(tmp_path / "t.json"),
        user_dir=tmp_path / "no-user", builtin_dir=tmp_path / "no-builtin",
    )
    assert result.entries == () and result.shadowed == () and result.project_skipped == 0


def test_no_workspace_root_skips_the_project_level(world) -> None:
    (world.user / "u.yaml").write_text(doc("u"), encoding="utf-8")
    result = discover(USER, None, trust_store=world.trust, user_dir=world.user, builtin_dir=world.builtin)
    assert [e.id for e in result.entries] == ["u"] and result.project_skipped == 0


def test_builtin_directory_defaults_next_to_the_package() -> None:
    assert paths.builtin_dir() == Path(paths.__file__).with_name("builtin")
    assert paths.project_dir(Path("/x")) == Path("/x") / ".orin" / "pipelines"
    assert paths.user_dir().name == "pipelines"


def test_disabled_returns_nothing_and_get_reports_e_disabled(world) -> None:
    (world.user / "u.yaml").write_text(doc("u"), encoding="utf-8")
    assert world.run(enabled=False).entries == ()
    with pytest.raises(DiscoveryDisabled) as info:
        get_pipeline(USER, world.root, "u", trust_store=world.trust, user_dir=world.user, builtin_dir=world.builtin, enabled=False)
    assert info.value.error.code == "E_DISABLED"


def test_get_pipeline_finds_by_id_and_returns_none_when_missing(world) -> None:
    (world.user / "u.yaml").write_text(doc("u"), encoding="utf-8")
    found = get_pipeline(USER, world.root, "u", trust_store=world.trust, user_dir=world.user, builtin_dir=world.builtin)
    assert found is not None and found.source == "user"
    assert get_pipeline(USER, world.root, "zzz", trust_store=world.trust, user_dir=world.user, builtin_dir=world.builtin) is None


def test_source_is_set_on_the_definition(world) -> None:
    world.trust.trust(USER, world.root)
    (world.project / "p.yaml").write_text(doc("p"), encoding="utf-8")
    (world.builtin / "b.yaml").write_text(doc("b"), encoding="utf-8")
    sources = {e.id: e.definition.source for e in world.run().entries if e.definition}
    assert sources == {"p": "project", "b": "builtin"}
