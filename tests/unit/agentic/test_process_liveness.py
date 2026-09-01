import pytest

from agentos.agentic import agent_tools


def test_a_child_that_already_exited_is_reaped_and_reported_as_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools.os, "name", "posix")
    monkeypatch.setattr(agent_tools.os, "waitpid", lambda pid, options: (pid, 0))

    def kill_should_not_be_needed(pid, sig):
        raise AssertionError("kill(pid, 0) should not run once waitpid already reaped the child")

    monkeypatch.setattr(agent_tools.os, "kill", kill_should_not_be_needed)

    assert agent_tools._process_is_running(4242) is False


def test_a_child_still_running_is_reported_as_running_without_touching_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools.os, "name", "posix")
    # WNOHANG with the child still alive returns (0, 0), not the child's own pid.
    monkeypatch.setattr(agent_tools.os, "waitpid", lambda pid, options: (0, 0))

    def kill_should_not_be_needed(pid, sig):
        raise AssertionError("kill(pid, 0) should not run once waitpid already answered")

    monkeypatch.setattr(agent_tools.os, "kill", kill_should_not_be_needed)

    assert agent_tools._process_is_running(4242) is True


def test_a_pid_that_is_no_longer_our_child_falls_back_to_kill_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracked across an AgentToolset rebuilt in a later turn, or a backend
    restart: waitpid() only works while the original launching process is
    still the one asking."""
    monkeypatch.setattr(agent_tools.os, "name", "posix")

    def waitpid_not_our_child(pid, options):
        raise ChildProcessError("no such child")

    monkeypatch.setattr(agent_tools.os, "waitpid", waitpid_not_our_child)
    monkeypatch.setattr(agent_tools.os, "kill", lambda pid, sig: None)  # succeeds -- pid exists

    assert agent_tools._process_is_running(4242) is True


def test_a_pid_that_is_gone_and_not_our_child_is_reported_as_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools.os, "name", "posix")
    monkeypatch.setattr(agent_tools.os, "waitpid", lambda pid, options: (_ for _ in ()).throw(ChildProcessError()))

    def kill_raises_lookup_error(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(agent_tools.os, "kill", kill_raises_lookup_error)

    assert agent_tools._process_is_running(4242) is False
