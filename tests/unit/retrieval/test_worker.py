from __future__ import annotations

import threading

from agentos.retrieval.worker import IndexWorker


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[list[str] | None] = []
        self.seen = threading.Event()

    def reindex(self, paths: list[str] | None = None) -> None:
        self.calls.append(paths)
        self.seen.set()


def test_a_queued_full_scan_runs_on_the_background_thread() -> None:
    service = RecordingService()
    worker = IndexWorker(service)
    worker.start()

    worker.enqueue_full_scan()

    assert worker.wait_idle(timeout=5.0)
    assert service.calls == [None]
    worker.stop()


def test_queued_paths_are_passed_through() -> None:
    service = RecordingService()
    worker = IndexWorker(service)
    worker.start()

    worker.enqueue_paths(["src/a.py", "src/b.py"])

    assert worker.wait_idle(timeout=5.0)
    assert service.calls == [["src/a.py", "src/b.py"]]
    worker.stop()


def test_an_empty_path_list_is_not_queued() -> None:
    service = RecordingService()
    worker = IndexWorker(service)
    worker.start()

    worker.enqueue_paths([])

    assert worker.wait_idle(timeout=5.0)
    assert service.calls == []
    worker.stop()


def test_a_failing_reindex_does_not_kill_the_worker() -> None:
    class BrokenService(RecordingService):
        def reindex(self, paths: list[str] | None = None) -> None:
            super().reindex(paths)
            raise RuntimeError("boom")

    service = BrokenService()
    worker = IndexWorker(service)
    worker.start()

    worker.enqueue_full_scan()
    assert worker.wait_idle(timeout=5.0)
    worker.enqueue_paths(["src/a.py"])
    assert worker.wait_idle(timeout=5.0)

    assert service.calls == [None, ["src/a.py"]]
    worker.stop()
