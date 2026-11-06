from __future__ import annotations

import pytest

from agentos.providers.cli_queue import (
    CliSlotQueue,
    acquire_cli_slot,
    cli_slot_queue,
    free_memory_mb,
    set_cli_slot_queue,
)


@pytest.fixture(autouse=True)
def _reset_global_cli_queue():
    set_cli_slot_queue(None)
    yield
    set_cli_slot_queue(None)


def test_a_conversation_with_slots_yields_to_one_without() -> None:
    q = CliSlotQueue(limit=2, min_free_memory_mb=0)
    a1, a2 = q.enqueue("A"), q.enqueue("A")
    assert a1.wait(0) and a2.wait(0)
    a3, b1 = q.enqueue("A"), q.enqueue("B")
    assert b1.position() == 1 and a3.position() == 2
    assert b1.wait_reason() == "limit"
    a1.release()
    assert b1.wait(1) and not a3.wait(0)
    b1.release()
    a2.release()
    assert a3.wait(1)
    a3.release()


def test_ties_are_first_come_first_served() -> None:
    q = CliSlotQueue(limit=1, min_free_memory_mb=0)
    first = q.enqueue("A")
    assert first.wait(0)
    b, c = q.enqueue("B"), q.enqueue("C")
    assert b.position() == 1 and c.position() == 2
    first.release()
    assert b.wait(1) and not c.wait(0)
    b.release()
    assert c.wait(1)
    c.release()


def test_releasing_a_waiting_ticket_leaks_nothing() -> None:
    q = CliSlotQueue(limit=1, min_free_memory_mb=0)
    held = q.enqueue("A")
    assert held.wait(0)
    gone, next_ = q.enqueue("B"), q.enqueue("C")
    gone.release()
    gone.release()
    assert gone.position() == 0
    assert next_.position() == 1
    held.release()
    assert next_.wait(1) and q.active_count() == 1
    next_.release()
    assert q.active_count() == 0


def test_cancel_while_waiting_returns_none_and_frees_the_place() -> None:
    q = CliSlotQueue(limit=1, min_free_memory_mb=0)
    set_cli_slot_queue(q)
    held = q.enqueue("A")
    assert held.wait(0)
    positions: list[tuple[int, int | None]] = []
    assert (
        acquire_cli_slot(
            "B",
            cancel_check=lambda: True,
            on_wait=lambda p, l: positions.append((p, l)),
        )
        is None
    )
    held.release()
    assert q.active_count() == 0


def test_on_wait_reports_position_changes_until_slot_is_granted() -> None:
    import threading

    q = CliSlotQueue(limit=1, min_free_memory_mb=0)
    set_cli_slot_queue(q)
    a = q.enqueue("A")
    assert a.wait(0)
    b = q.enqueue("B")
    assert not b.wait(0)

    positions: list[tuple[int, int | None]] = []

    def _release_soon() -> None:
        import time

        time.sleep(0.08)
        b.release()
        time.sleep(0.08)
        a.release()

    t = threading.Thread(target=_release_soon, daemon=True)
    t.start()
    ticket = acquire_cli_slot(
        "C",
        cancel_check=lambda: False,
        on_wait=lambda p, l: positions.append((p, l)),
        poll_seconds=0.02,
    )
    assert ticket is not None
    assert positions == [(2, 1), (1, 1)]
    ticket.release()


def test_limit_comes_from_the_environment() -> None:
    assert CliSlotQueue.from_env({}).limit is None
    assert CliSlotQueue.from_env({"ORIN_CLI_MAX_PROCESSES": "5"}).limit == 5
    assert CliSlotQueue.from_env({"ORIN_CLI_MAX_PROCESSES": "x"}).limit is None
    assert CliSlotQueue.from_env({"ORIN_CLI_MAX_PROCESSES": "0"}).limit is None
    assert CliSlotQueue.from_env({"ORIN_CLI_MAX_PROCESSES": "-2"}).limit is None
    assert CliSlotQueue.from_env({"ORIN_CLI_MIN_FREE_MEMORY_MB": "2048"}).min_free_memory_mb == 2048
    assert CliSlotQueue.from_env({"ORIN_CLI_MIN_FREE_MEMORY_MB": "0"}).min_free_memory_mb == 0


def test_ten_conversations_run_at_once_without_a_limit() -> None:
    q = CliSlotQueue(memory_probe=lambda: 16_000)
    tickets = [q.enqueue(f"C{i}") for i in range(10)]
    assert all(t.wait(0) for t in tickets) and q.active_count() == 10
    for t in tickets:
        t.release()
    assert q.active_count() == 0


def test_low_memory_holds_new_processes_but_never_the_first() -> None:
    free = {"mb": 500}
    q = CliSlotQueue(min_free_memory_mb=1024, memory_probe=lambda: free["mb"])
    first, second = q.enqueue("A"), q.enqueue("B")
    assert first.wait(0) and not second.wait(0.1) and second.wait_reason() == "memory"
    free["mb"] = 4000
    assert second.wait(1)
    first.release()
    second.release()


def test_unknown_memory_means_no_memory_guard() -> None:
    q = CliSlotQueue(memory_probe=lambda: None)
    tickets = [q.enqueue(str(i)) for i in range(5)]
    assert all(t.wait(0) for t in tickets)
    for t in tickets:
        t.release()


def test_free_memory_mb_returns_positive_int_or_none() -> None:
    val = free_memory_mb()
    assert val is None or (isinstance(val, int) and val > 0)
