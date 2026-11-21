from __future__ import annotations

import asyncio
import gc
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine

from agentos.api import (
    ApiServices,
    AuthenticatedPrincipal,
    InMemoryClientEventStream,
    InMemorySecurityService,
    create_app,
)
from agentos.memory.instruction_lint import InstructionLinter, LintReport
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.migrate import upgrade
from agentos.projects.store import PostgresProjectStore


def _make_engine(tmp_path: Path, name: str = "lint_routes.db"):
    engine = create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
    upgrade(engine, "head")
    return engine


def _make_app(
    *,
    projects=None,
    local_workspaces=None,
    workspace_root: Path | None = None,
    instruction_linter=None,
):
    security = InMemorySecurityService()
    security.add_pat("pat-u1", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    security.add_pat("pat-u2", AuthenticatedPrincipal("user-2", "cred-2", frozenset({"api"})))
    services = ApiServices(
        security=security,
        events=InMemoryClientEventStream(),
        projects=projects,
        local_workspaces=local_workspaces,
        workspace_root=workspace_root,
    )
    if instruction_linter is not None:
        services.instruction_linter = instruction_linter
    return create_app(services)


def test_post_memory_lint_dry_run_and_manual_run_work_even_when_setting_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "ORIN.md").write_text(
        "Use claude-sonnet-4-6 em subagentes.\n",
        encoding="utf-8",
    )

    engine = _make_engine(tmp_path)
    try:
        store = PostgresAgentMemoryStore(engine, user_id="user-1")
        saved = store.save("Usar claude-sonnet-5-5 em subagentes.", kind="preference", source="user_explicit")
        memory_id = str(saved["memory_id"])

        calls = 0

        def fake_caller(_sys: str, _usr: str) -> str:
            nonlocal calls
            calls += 1
            return json.dumps(
                [
                    {
                        "memory_id": memory_id,
                        "file": "ORIN.md",
                        "line": 1,
                        "quoted_line": "Use claude-sonnet-4-6 em subagentes.",
                        "reason": "Conflito entre claude-sonnet-4-6 e claude-sonnet-5-5.",
                    }
                ]
            )

        # Ensure setting memory.instructionLint.enabled is explicitly False
        monkeypatch.setattr(
            "agentos.memory.instruction_lint.is_instruction_lint_enabled",
            lambda _root=None, *, trusted=False: False,
        )

        linter = InstructionLinter(engine=engine, model_caller=fake_caller)
        app = _make_app(
            projects=PostgresProjectStore(engine),
            workspace_root=workspace,
            instruction_linter=linter,
        )
        client = TestClient(app)

        # 1) dry_run=True -> 0 calls, returns estimated_calls
        resp_dry = client.post(
            "/v1/memory/lint",
            headers={"Authorization": "Bearer pat-u1", "Idempotency-Key": "k-dry"},
            json={"workspace_root": str(workspace), "dry_run": True},
        )
        assert resp_dry.status_code == 200
        dry_body = resp_dry.json()
        assert dry_body["dry_run"] is True
        assert dry_body["estimated_calls"] == 1
        assert dry_body["memories_checked"] == 1
        assert dry_body["files_checked"] == ["ORIN.md"]
        assert dry_body["contradictions"] == []
        assert calls == 0

        # 2) dry_run=False -> manual button works even when setting is False
        resp_live = client.post(
            "/v1/memory/lint",
            headers={"Authorization": "Bearer pat-u1", "Idempotency-Key": "k-live"},
            json={"workspace_root": str(workspace), "dry_run": False},
        )
        assert resp_live.status_code == 200
        live_body = resp_live.json()
        assert live_body["dry_run"] is False
        assert calls == 1
        assert len(live_body["contradictions"]) == 1
        assert live_body["contradictions"][0]["memory_id"] == memory_id
        assert live_body["contradictions"][0]["file"] == "ORIN.md"
        assert live_body["contradictions"][0]["line"] == 1
    finally:
        engine.dispose()


def test_post_memory_lint_isolates_memories_by_user_id(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "ORIN.md").write_text("Use claude-sonnet-4-6 em subagentes.\n", encoding="utf-8")

    engine = _make_engine(tmp_path, "iso.db")
    try:
        store_u1 = PostgresAgentMemoryStore(engine, user_id="user-1")
        mem_u1 = store_u1.save("Subagentes de user-1 usam claude-sonnet-5-5.", kind="preference", source="user_explicit")

        store_u2 = PostgresAgentMemoryStore(engine, user_id="user-2")
        mem_u2 = store_u2.save("Subagentes de user-2 usam claude-haiku-5-5.", kind="preference", source="user_explicit")

        prompts_by_call: list[str] = []

        def fake_caller(_sys: str, user_prompt: str) -> str:
            prompts_by_call.append(user_prompt)
            # Deliberately try to return BOTH users' memory IDs; only the authenticated user's memory may survive
            return json.dumps(
                [
                    {
                        "memory_id": str(mem_u1["memory_id"]),
                        "file": "ORIN.md",
                        "line": 1,
                        "quoted_line": "Use claude-sonnet-4-6 em subagentes.",
                        "reason": "Conflito u1.",
                    },
                    {
                        "memory_id": str(mem_u2["memory_id"]),
                        "file": "ORIN.md",
                        "line": 1,
                        "quoted_line": "Use claude-sonnet-4-6 em subagentes.",
                        "reason": "Conflito u2.",
                    },
                ]
            )

        linter = InstructionLinter(engine=engine, model_caller=fake_caller)
        app = _make_app(
            projects=PostgresProjectStore(engine),
            workspace_root=workspace,
            instruction_linter=linter,
        )
        client = TestClient(app)

        resp_u2 = client.post(
            "/v1/memory/lint",
            headers={"Authorization": "Bearer pat-u2", "Idempotency-Key": "k-u2"},
            json={"workspace_root": str(workspace), "dry_run": False},
        )
        assert resp_u2.status_code == 200
        body_u2 = resp_u2.json()
        assert len(prompts_by_call) == 1
        assert str(mem_u1["memory_id"]) not in prompts_by_call[0]
        assert str(mem_u2["memory_id"]) in prompts_by_call[0]
        assert [c["memory_id"] for c in body_u2["contradictions"]] == [str(mem_u2["memory_id"])]
    finally:
        engine.dispose()


def test_post_memory_lint_runs_in_threadpool_without_stalling_event_loop(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "ORIN.md").write_text("Use claude-sonnet-4-6 em subagentes.\n", encoding="utf-8")

    class _SlowLinter:
        def run(self, user_id: str, workspace_root: Path, *, project_id=None, dry_run=False, trusted=False):
            time.sleep(0.8)
            return LintReport(
                contradictions=[],
                files_checked=["ORIN.md"],
                memories_checked=0,
                estimated_calls=0,
                dry_run=dry_run,
            )

    app = _make_app(workspace_root=workspace, instruction_linter=_SlowLinter())

    async def _run() -> float:
        transport = ASGITransport(app=app)
        gc.collect()
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            started = time.monotonic()
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                slow = asyncio.ensure_future(
                    client.post(
                        "/v1/memory/lint",
                        headers={"Authorization": "Bearer pat-u1", "Idempotency-Key": "k-slow"},
                        json={"workspace_root": str(workspace), "dry_run": False},
                    )
                )
                await asyncio.sleep(0.2)
                fast_resp = await client.get("/v1/quota", headers={"Authorization": "Bearer pat-u1"})
                assert fast_resp.status_code == 200
                fast_elapsed = time.monotonic() - started
                slow_resp = await slow
                assert slow_resp.status_code == 200
                return fast_elapsed
        finally:
            if gc_was_enabled:
                gc.enable()

    elapsed = asyncio.run(_run())
    assert elapsed < 0.5, f"POST /v1/memory/lint stalled the event loop ({elapsed:.2f}s)"


def test_post_memory_lint_enforces_host_folders_and_project_ownership(tmp_path: Path) -> None:
    from agentos.configuration.capabilities import InstanceCapabilities
    from agentos.configuration.mode import RuntimeMode

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "ORIN.md").write_text("Use pytest.\n", encoding="utf-8")

    engine = _make_engine(tmp_path, "caps.db")
    try:
        projects = PostgresProjectStore(engine)
        proj_u1 = projects.create(user_id="user-1", name="Alpha", description=None)

        security = InMemorySecurityService()
        security.add_pat("pat-u1", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
        security.add_pat("pat-u2", AuthenticatedPrincipal("user-2", "cred-2", frozenset({"api"})))

        # Cloud/remote profile without host_folders capability must reject explicit workspace_root
        app_remote = create_app(
            ApiServices(
                security=security,
                events=InMemoryClientEventStream(),
                projects=projects,
                capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER),
            )
        )
        client_remote = TestClient(app_remote)
        resp_forbidden = client_remote.post(
            "/v1/memory/lint",
            headers={"Authorization": "Bearer pat-u1", "Idempotency-Key": "k-cap"},
            json={"workspace_root": str(workspace), "dry_run": True},
        )
        assert resp_forbidden.status_code in {403, 404, 501}

        # User 2 must not access User 1's project_id
        app_local = _make_app(projects=projects)
        client_local = TestClient(app_local)
        resp_other_proj = client_local.post(
            "/v1/memory/lint",
            headers={"Authorization": "Bearer pat-u2", "Idempotency-Key": "k-other"},
            json={"project_id": proj_u1.project_id, "dry_run": True},
        )
        assert resp_other_proj.status_code == 404

        # Without workspace_root or bound project folder -> 200 with clear warning (no 500)
        resp_no_ws = client_local.post(
            "/v1/memory/lint",
            headers={"Authorization": "Bearer pat-u1", "Idempotency-Key": "k-nows"},
            json={"dry_run": False},
        )
        assert resp_no_ws.status_code == 200
        body = resp_no_ws.json()
        assert body["contradictions"] == []
        assert body["warning"] is not None
    finally:
        engine.dispose()
