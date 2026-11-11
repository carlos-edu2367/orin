"""The worker re-queues a turn paused by PROVIDER_STREAM_INTERRUPTED only a bounded number of times.

Each auto-resume refreshed the dispatch's updated_at, so a turn that failed the same way on every run
(agy killed after each Orin tool call in chat mode) stayed inside the 30-minute window and was resumed
forever, alternating with the next turn of the same conversation and spending the user's quota.
"""
from __future__ import annotations

from datetime import timedelta


def _pause_interrupted(worker, store, turn) -> None:
    worker._project(turn, "STARTING", "worker_acquired")
    worker._project(turn, "RUNNING", "provider_started")
    worker._project(turn, "PAUSED", "effect_reconciliation_required")
    store.pause_for_reconciliation(
        turn,
        code="PROVIDER_STREAM_INTERRUPTED",
        end_reason="stream_error",
        message="A conexão com o modelo foi interrompida antes de concluir a resposta.",
    )


def test_worker_stops_auto_resuming_the_same_turn_after_the_cap(tmp_path, monkeypatch) -> None:
    from agentos.conversations.resume import MAX_WORKER_AUTO_RESUMES
    from agentos.workers.chat import ChatWorker
    from tests.integration.server_world import build_server_world

    world = build_server_world(tmp_path, monkeypatch)
    admin = world.admin()
    store = world.services.conversation_application.store
    created = store.create(
        user_id=admin.user_id,
        message="Faz o deploy",
        provider="google-antigravity",
        model_id="gemini-4-argon-high",
        idempotency_key="auto-resume-cap-1",
    )
    conv_id = str(created.conversation_id)
    turn_id = str(created.turn_id)
    worker = ChatWorker(store)

    for _ in range(MAX_WORKER_AUTO_RESUMES):
        turn = store.claim(turn_id)
        assert turn is not None
        _pause_interrupted(worker, store, turn)
        assert turn_id in worker.recover_stale(maximum_age=timedelta(minutes=30))
        assert store.get(conv_id, admin.user_id)["state"] == "queued"

    turn = store.claim(turn_id)
    assert turn is not None
    _pause_interrupted(worker, store, turn)
    assert turn_id not in worker.recover_stale(maximum_age=timedelta(minutes=30))
    assert store.get(conv_id, admin.user_id)["state"] == "paused"

    # The user can still resume it by hand; that does not count towards the worker's cap.
    resumed = admin.api.post(
        f"/v1/conversations/{conv_id}/turns/{turn_id}/resume",
        json={},
        headers=admin.headers(),
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "queued"


def test_cap_is_per_turn(tmp_path, monkeypatch) -> None:
    from agentos.conversations.resume import MAX_WORKER_AUTO_RESUMES
    from agentos.workers.chat import ChatWorker
    from tests.integration.server_world import build_server_world

    world = build_server_world(tmp_path, monkeypatch)
    admin = world.admin()
    store = world.services.conversation_application.store
    worker = ChatWorker(store)

    first = store.create(
        user_id=admin.user_id,
        message="primeira",
        provider="google-antigravity",
        model_id="gemini-4-argon-high",
        idempotency_key="auto-resume-cap-a",
    )
    first_id = str(first.turn_id)
    for _ in range(MAX_WORKER_AUTO_RESUMES + 1):
        turn = store.claim(first_id)
        assert turn is not None
        _pause_interrupted(worker, store, turn)
        worker.recover_stale(maximum_age=timedelta(minutes=30))

    second = store.create(
        user_id=admin.user_id,
        message="segunda",
        provider="google-antigravity",
        model_id="gemini-4-argon-high",
        idempotency_key="auto-resume-cap-b",
    )
    second_id = str(second.turn_id)
    turn = store.claim(second_id)
    assert turn is not None
    _pause_interrupted(worker, store, turn)
    recovered = worker.recover_stale(maximum_age=timedelta(minutes=30))
    assert second_id in recovered
    assert first_id not in recovered
