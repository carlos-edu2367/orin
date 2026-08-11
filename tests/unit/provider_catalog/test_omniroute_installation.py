from __future__ import annotations

from subprocess import CompletedProcess

from agentos.provider_catalog.installation import OmniRouteInstaller


def test_installer_uses_the_official_global_npm_command_without_exposing_output() -> None:
    calls: list[tuple[list[str], int]] = []

    def run(command: list[str], timeout: int, **_kwargs) -> CompletedProcess[str]:
        calls.append((command, timeout))
        return CompletedProcess(command, 0, stdout="installed omniroute", stderr="")

    installer = OmniRouteInstaller(executable="npm", runner=run)

    assert installer.install() == {"installed": True, "next_step": "omniroute"}
    assert calls == [(["npm", "install", "-g", "omniroute"], 180)]
    assert "installed omniroute" not in repr(installer)
