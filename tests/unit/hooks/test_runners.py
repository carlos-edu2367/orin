from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

from agentos.agentic.lifecycle import HookEvent as E
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.hooks.config import HookSpec
from agentos.hooks.env_file import read_env_file
from agentos.hooks.runners import AgentHookRunner, CommandHookRunner, HttpHookRunner, PromptHookRunner


def _spec(**values) -> HookSpec:
    base = {"event": E.PRE_TOOL_USE, "matcher": "", "type": "command", "source": "user", "timeout": 30.0}
    base.update(values)
    return HookSpec(**base)


def test_command_runner_passes_payload_and_env(tmp_path: Path) -> None:
    script = tmp_path / "h.py"
    script.write_text(
        "import json, os, sys\n"
        "data = json.load(sys.stdin)\n"
        "print(json.dumps({'tool': data['tool_name'], 'orin': os.environ['ORIN_PROJECT_DIR'], 'claude': os.environ['CLAUDE_PROJECT_DIR'], 'envfile': os.environ.get('CLAUDE_ENV_FILE', '')}))\n",
        encoding="utf-8",
    )
    runner = CommandHookRunner(tmp_path, env_file=tmp_path / "session.env")
    run = runner.run(_spec(command=f'"{sys.executable}" "{script}"'), {"tool_name": "Bash"})
    out = json.loads(run.stdout)
    assert run.exit_code == 0 and out["tool"] == "Bash" and out["orin"] == str(tmp_path) == out["claude"]
    assert out["envfile"] == ""  # only SessionStart gets the env file
    start = runner.run(_spec(event=E.SESSION_START, command=f'"{sys.executable}" "{script}"'), {"tool_name": "x"})
    assert json.loads(start.stdout)["envfile"] == str(tmp_path / "session.env")


def test_http_runner_posts_and_interpolates_only_allowed_vars(monkeypatch) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["other"] = request.headers.get("x-other")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text='{"decision": "block", "reason": "nope"}')

    monkeypatch.setenv("HOOK_TOKEN", "secret-1")
    monkeypatch.setenv("NOT_ALLOWED", "leak")
    runner = HttpHookRunner(lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    spec = _spec(type="http", url="https://hooks.example.com/x", headers=(("Authorization", "Bearer ${HOOK_TOKEN}"), ("X-Other", "$NOT_ALLOWED")), allowed_env_vars=("HOOK_TOKEN",))
    run = runner.run(spec, {"tool_name": "Bash"})
    assert run.exit_code == 0 and '"block"' in run.stdout
    assert seen == {"auth": "Bearer secret-1", "other": "$NOT_ALLOWED", "body": {"tool_name": "Bash"}}
    failing = HttpHookRunner(lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert failing.run(spec, {}).exit_code == 1


def test_prompt_and_agent_runners_return_verdicts() -> None:
    class Provider:
        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text='{"ok": false, "reason": "risky"}'), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")])

    provider = Provider()
    runner = PromptHookRunner(lambda: provider, provider_name="openrouter", default_model="fast")
    verdict = runner.run(_spec(type="prompt", prompt="Is this safe? $ARGUMENTS"), {"tool_name": "Bash"})
    assert verdict == {"ok": False, "reason": "risky"}
    assert '"tool_name": "Bash"' in json.dumps(provider.requests[0]["messages"]) or "Bash" in json.dumps(provider.requests[0]["messages"])
    assert provider.requests[0]["model"] == "fast"
    agent = AgentHookRunner(lambda prompt: 'done {"ok": true, "reason": "fine"}')
    assert agent.run(_spec(type="agent", prompt="check"), {}) == {"ok": True, "reason": "fine"}
    assert AgentHookRunner(None).run(_spec(type="agent", prompt="check"), {}) is None


def test_read_env_file(tmp_path: Path) -> None:
    path = tmp_path / "e.env"
    path.write_text("# c\nexport A=1\nB=\"two words\"\nC='x'\nbad-key=1\nNOEQUALS\n", encoding="utf-8")
    assert read_env_file(path) == {"A": "1", "B": "two words", "C": "x"}
    assert read_env_file(tmp_path / "missing.env") == {}
    big = tmp_path / "big.env"
    big.write_text("A=" + "x" * 300_000, encoding="utf-8")
    assert read_env_file(big) == {}


def test_a_timed_out_agent_verifier_is_cancelled_and_leaves_no_thread_behind() -> None:
    # W2-R8: a verifier that outlives its timeout is told to stop, and the runner does not grow a thread per call.
    import threading
    import time

    observed = []

    def slow_agent(prompt, cancel):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not cancel.is_set():
            time.sleep(0.02)
        observed.append(cancel.is_set())
        return '{"ok": true}'

    runner = AgentHookRunner(slow_agent)
    before = threading.active_count()
    started = time.monotonic()
    for _ in range(5):
        assert runner.run(_spec(type="agent", prompt="check", timeout=0.3), {}) is None
    assert time.monotonic() - started < 4
    deadline = time.monotonic() + 4
    while len(observed) < 5 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert observed == [True] * len(observed) and len(observed) >= 1
    assert threading.active_count() <= before + 2
    runner.close()


def test_a_timed_out_prompt_hook_stops_reading_the_provider_stream(monkeypatch) -> None:
    # W2-R8: the prompt hook's worker does not keep consuming a provider stream after its deadline.
    import time

    from agentos.agentic.runtime import AgenticTurnRuntime

    class Chunk:
        def __init__(self, text):
            self.text = text

    consumed = []

    class Stream:
        def __iter__(self):
            for index in range(100_000):
                consumed.append(index)
                time.sleep(0.001)
                yield Chunk("x")

        def close(self):
            consumed.append("closed")

    class Provider:
        def stream(self, request):
            return Stream()

    monkeypatch.setattr(AgenticTurnRuntime, "_coerce", staticmethod(lambda raw: raw))
    runner = PromptHookRunner(lambda: Provider(), provider_name="openrouter", default_model="m")
    assert runner.run(_spec(type="prompt", prompt="check", timeout=0.2), {}) is None
    time.sleep(0.3)
    assert len(consumed) > 0, "the stream was never read, so the test proves nothing"
    settled = len(consumed)
    time.sleep(0.3)
    assert len(consumed) == settled, "the stream kept being read after the timeout"
    runner.close()


def test_http_hook_body_is_read_only_up_to_its_cap() -> None:
    # W2-R9: a hook endpoint that streams 10 MB is read only as far as the cap, not to its end.
    from agentos.hooks.runners import MAX_HTTP_BODY

    consumed = [0]

    def chunks():
        for _ in range(160):
            consumed[0] += 65_536
            yield b"x" * 65_536

    def handler(request):
        return httpx.Response(200, content=chunks())

    runner = HttpHookRunner(lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    result = runner.run(_spec(type="http", url="https://hooks.example.com/x", timeout=5), {})
    assert result.exit_code == 0 and len(result.stdout) <= MAX_HTTP_BODY
    assert consumed[0] <= 2 * 1024 * 1024, f"read {consumed[0]} bytes of a 10 MB body"
