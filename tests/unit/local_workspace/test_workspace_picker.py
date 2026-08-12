import subprocess

from agentos.local_workspace.picker import PickResult, choose_folder


def test_choose_folder_returns_the_selected_path(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="D:\\projetos\\site\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert choose_folder(command=["fake"]) == PickResult(path="D:\\projetos\\site", cancelled=False, available=True)


def test_empty_output_reads_as_cancelled(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="\n", stderr="User canceled")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert choose_folder(command=["fake"]) == PickResult(path=None, cancelled=True, available=True)


def test_missing_binary_reads_as_unavailable(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert choose_folder(command=["fake"]) == PickResult(path=None, cancelled=False, available=False)


def test_timeout_reads_as_unavailable(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert choose_folder(command=["fake"]) == PickResult(path=None, cancelled=False, available=False)


def test_no_command_for_the_platform_reads_as_unavailable() -> None:
    assert choose_folder(command=[]) == PickResult(path=None, cancelled=False, available=False)
