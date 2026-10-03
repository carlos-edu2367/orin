import pytest

from agentos.configuration.capabilities import CapabilityUnavailable, InstanceCapabilities
from agentos.configuration.mode import RuntimeMode, current_mode


def test_mode_defaults_to_local_and_reads_orin_mode():
    assert current_mode({}) is RuntimeMode.LOCAL
    assert current_mode({"ORIN_MODE": "Server"}) is RuntimeMode.SERVER
    assert current_mode({"ORIN_MODE": " local "}) is RuntimeMode.LOCAL


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        current_mode({"ORIN_MODE": "cluster"})


def test_local_capabilities_keep_every_desktop_feature():
    caps = InstanceCapabilities.for_mode(RuntimeMode.LOCAL)
    assert caps.as_dict() == {
        "shell": True, "mcp_stdio": True, "plugin_hooks": True, "omniroute": True,
        "host_folders": True, "profile_files": False, "open_in_desktop_app": True,
        "ui_updater": True, "user_admin": False,
    }


def test_server_capabilities_close_everything_that_runs_a_process():
    caps = InstanceCapabilities.for_mode(RuntimeMode.SERVER)
    assert caps.as_dict() == {
        "shell": False, "mcp_stdio": False, "plugin_hooks": False, "omniroute": False,
        "host_folders": False, "profile_files": True, "open_in_desktop_app": False,
        "ui_updater": False, "user_admin": True,
    }


def test_require_raises_for_a_closed_capability():
    caps = InstanceCapabilities.for_mode(RuntimeMode.SERVER)
    caps.require("profile_files")
    with pytest.raises(CapabilityUnavailable) as raised:
        caps.require("shell")
    assert raised.value.capability == "shell"
    with pytest.raises(KeyError):
        caps.require("teleport")
