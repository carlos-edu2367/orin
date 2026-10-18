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
