"""W6 Task 5: `orin code` reaches a real local runtime with this run's token (and nothing else gets in with one)."""
from __future__ import annotations

import httpx

from agentos.cli_code.runtime import connect_local
from agentos.installation.api_token import read_api_token

from .live_runtime import LiveRuntime


def test_the_cli_connects_to_the_running_orin_with_the_run_token(orin_runtime: LiveRuntime) -> None:
    client = connect_local(orin_runtime.paths, start=False)
    try:
        info = client.get("/v1/instance")
    finally:
        client.close()
    assert info["mode"] == "local" and info["credential"] == "cli_token"


def test_a_wrong_bearer_is_refused_and_no_bearer_is_still_the_loopback_person(orin_runtime: LiveRuntime) -> None:
    wrong = httpx.get(f"{orin_runtime.url}/v1/instance", headers={"Authorization": "Bearer not-the-token"}, trust_env=False, timeout=10)
    assert wrong.status_code == 401
    plain = httpx.get(f"{orin_runtime.url}/v1/instance", trust_env=False, timeout=10)
    assert plain.status_code == 200 and plain.json()["credential"] == "loopback"


def test_the_fake_provider_answers_a_code_turn(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("smoke")
    with orin_runtime.api() as api:
        created = api.post("/v1/conversations", headers={"Idempotency-Key": "smoke-1"}, json={
            "message": "hello", "selection": {"provider": "ollama", "model_id": orin_runtime.model},
            "workspace_path": str(workspace), "code_mode": "code", "permission_mode": "accept_edits",
        })
        assert created.status_code == 201, created.text
        conversation = created.json()["conversation_id"]
        seen: list[str] = []
        with api.stream("GET", f"/v1/conversations/{conversation}/events", timeout=httpx.Timeout(60.0, connect=5.0)) as stream:
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    seen.append(line[7:])
                    if seen[-1] in {"turn.completed", "turn.failed"}:
                        break
    assert seen[-1] == "turn.completed"
    assert "assistant.delta" in seen
    assert read_api_token(orin_runtime.paths.run)
