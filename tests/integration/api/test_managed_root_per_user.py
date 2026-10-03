from agentos.api import ApiServices
from agentos.installation import orin_paths, reset_cached_paths


def test_without_an_override_each_user_gets_their_own_managed_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path))
    reset_cached_paths()
    services = ApiServices()
    assert services.managed_root_for("local-user") == orin_paths().user_workspaces("local-user")
    assert services.managed_root_for("usr_b") != services.managed_root_for("local-user")
    reset_cached_paths()


def test_an_explicit_root_still_wins_for_tests(tmp_path):
    services = ApiServices(workspace_root=tmp_path)
    assert services.managed_root_for("anyone") == tmp_path
