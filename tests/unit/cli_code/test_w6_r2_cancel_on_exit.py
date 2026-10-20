"""W6-R2: `orin code` cancels its running turn when it exits, and keeps the lease of that turn alive while it runs."""
from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from agentos.cli_code import headless
from agentos.cli_code.args import CodeOptions
from agentos.cli_code.errors import CliRuntimeError
from agentos.cli_code.headless import HeadlessRun, _Heartbeat, cancel_active_turns


class _Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.posts: list[str] = []
        self.fail = fail

    def post(self, path, body=None, *, timeout=None):
        self.posts.append(path)
        if self.fail:
            raise CliRuntimeError("the local Orin did not answer")
        return {}

    def get(self, path, **params):
        return {}


def _run(client: _Client, tmp_path: Path) -> HeadlessRun:
    run = HeadlessRun(CodeOptions(prompt="hi"), client=client, data_dir=tmp_path, cwd=tmp_path,
                      stdin=io.StringIO(""), stdout=io.StringIO(), stderr=io.StringIO())
    run.conversation_id = "chat_1"
    run.current_turn_id = "turn_1"
    run._resolve_session = lambda: None  # the turn is already running; no catalog or session is needed
    return run


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_an_exit_during_a_turn_posts_one_cancel_and_keeps_the_exit(tmp_path: Path, error) -> None:
    client = _Client()
    run = _run(client, tmp_path)

    def interrupted(prompt, **kwargs):
        raise error()

    run.answer = interrupted
    with pytest.raises(error):
        run.run()
    assert client.posts == ["/v1/conversations/chat_1/cancel"]


def test_an_exit_between_turns_posts_nothing(tmp_path: Path) -> None:
    client = _Client()
    run = _run(client, tmp_path)
    run.current_turn_id = None

    def interrupted(prompt, **kwargs):
        raise KeyboardInterrupt()

    run.answer = interrupted
    with pytest.raises(KeyboardInterrupt):
        run.run()
    assert client.posts == []


def test_a_failed_cancel_does_not_hide_the_exit(tmp_path: Path) -> None:
    client = _Client(fail=True)
    run = _run(client, tmp_path)

    def interrupted(prompt, **kwargs):
        raise KeyboardInterrupt()

    run.answer = interrupted
    with pytest.raises(KeyboardInterrupt):
        run.run()
    assert client.posts == ["/v1/conversations/chat_1/cancel"]


def test_the_process_level_cancel_posts_once_per_turn(tmp_path: Path) -> None:
    client = _Client()
    run = _run(client, tmp_path)
    headless._ACTIVE.add(run)
    try:
        cancel_active_turns()
        cancel_active_turns()
    finally:
        headless._ACTIVE.discard(run)
    assert client.posts == ["/v1/conversations/chat_1/cancel"]


def test_a_keyboard_interrupt_at_the_top_level_cancels_and_exits_with_one(tmp_path: Path, monkeypatch) -> None:
    from agentos.cli_code import main as cli_main

    client = _Client()
    run = _run(client, tmp_path)

    def interrupted_run_headless(options, *, stdin, stdout, stderr):
        headless._ACTIVE.add(run)
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_main, "run_headless", interrupted_run_headless)
    try:
        code = cli_main.main(["-p", "hi"], stdin=io.StringIO(""), stdout=io.StringIO(), stderr=io.StringIO())
    finally:
        headless._ACTIVE.discard(run)
    assert code == 1
    assert client.posts == ["/v1/conversations/chat_1/cancel"]


def test_the_heartbeat_renews_the_lease_until_it_is_stopped() -> None:
    client = _Client()
    beat = _Heartbeat(client, "chat_1", "turn_1", interval=0.01)
    beat.start()
    time.sleep(0.1)
    beat.stop()
    count = len(client.posts)
    assert count >= 2
    assert set(client.posts) == {"/v1/conversations/chat_1/turns/turn_1/cli-lease"}
    time.sleep(0.05)
    assert len(client.posts) == count


def test_a_termination_signal_leaves_through_the_same_exit_path() -> None:
    import signal

    from agentos.cli_code import main as cli_main

    saved = {name: signal.getsignal(getattr(signal, name)) for name in ("SIGTERM", "SIGBREAK") if hasattr(signal, name)}
    try:
        cli_main._install_exit_signals()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(SystemExit):
            handler(signal.SIGTERM, None)
    finally:
        for name, previous in saved.items():
            signal.signal(getattr(signal, name), previous)
