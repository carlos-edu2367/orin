from __future__ import annotations

from agentos.retrieval.bundle import RetrievalBundle
from agentos.retrieval.registry import RetrievalRegistry


class _FakeWorker:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeService:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _bundle() -> RetrievalBundle:
    return RetrievalBundle(service=_FakeService(), worker=_FakeWorker())  # type: ignore[arg-type]


def test_repeated_acquires_for_the_same_workspace_reuse_the_bundle() -> None:
    built: list[RetrievalBundle] = []

    def factory(workspace_id: str, local_root: str | None) -> RetrievalBundle | None:
        bundle = _bundle()
        built.append(bundle)
        return bundle

    registry = RetrievalRegistry(factory=factory)

    first = registry.acquire(workspace_id="workspace:p", local_root="/proj")
    second = registry.acquire(workspace_id="workspace:p", local_root="/proj")

    assert first is second
    assert len(built) == 1


def test_different_workspaces_get_different_bundles() -> None:
    def factory(workspace_id: str, local_root: str | None) -> RetrievalBundle | None:
        return _bundle()

    registry = RetrievalRegistry(factory=factory)

    first = registry.acquire(workspace_id="workspace:a", local_root="/a")
    second = registry.acquire(workspace_id="workspace:b", local_root="/b")

    assert first is not second


def test_a_none_bundle_from_the_factory_is_never_cached() -> None:
    calls = 0

    def factory(workspace_id: str, local_root: str | None) -> RetrievalBundle | None:
        nonlocal calls
        calls += 1
        return None

    registry = RetrievalRegistry(factory=factory)

    assert registry.acquire(workspace_id="workspace:p", local_root=None) is None
    assert registry.acquire(workspace_id="workspace:p", local_root=None) is None
    assert calls == 2


def test_a_rebound_local_root_closes_the_old_bundle_and_builds_a_new_one() -> None:
    def factory(workspace_id: str, local_root: str | None) -> RetrievalBundle | None:
        return _bundle()

    registry = RetrievalRegistry(factory=factory)

    first = registry.acquire(workspace_id="workspace:p", local_root="/old")
    second = registry.acquire(workspace_id="workspace:p", local_root="/new")

    assert first is not second
    assert first.worker.stopped is True  # type: ignore[attr-defined]
    assert first.service.closed is True  # type: ignore[attr-defined]


def test_idle_eviction_closes_and_forgets_a_stale_bundle() -> None:
    clock_value = [0.0]

    def factory(workspace_id: str, local_root: str | None) -> RetrievalBundle | None:
        return _bundle()

    registry = RetrievalRegistry(factory=factory, idle_seconds=10.0, clock=lambda: clock_value[0])

    first = registry.acquire(workspace_id="workspace:p", local_root="/proj")
    clock_value[0] = 20.0
    second = registry.acquire(workspace_id="workspace:p", local_root="/proj")

    assert first is not second
    assert first.worker.stopped is True  # type: ignore[attr-defined]


def test_close_all_tears_down_every_live_bundle() -> None:
    def factory(workspace_id: str, local_root: str | None) -> RetrievalBundle | None:
        return _bundle()

    registry = RetrievalRegistry(factory=factory)
    first = registry.acquire(workspace_id="workspace:a", local_root="/a")
    second = registry.acquire(workspace_id="workspace:b", local_root="/b")

    registry.close_all()

    assert first.worker.stopped is True and second.worker.stopped is True  # type: ignore[attr-defined]
    assert registry.acquire(workspace_id="workspace:a", local_root="/a") is not first


def test_a_blank_workspace_id_is_never_cached() -> None:
    calls = 0

    def factory(workspace_id: str, local_root: str | None) -> RetrievalBundle | None:
        nonlocal calls
        calls += 1
        return _bundle()

    registry = RetrievalRegistry(factory=factory)

    first = registry.acquire(workspace_id="", local_root="/proj")
    second = registry.acquire(workspace_id="", local_root="/proj")

    assert first is not second
    assert calls == 2
