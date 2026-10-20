from __future__ import annotations

from collections.abc import Iterator

import pytest

from .live_runtime import LiveRuntime


@pytest.fixture(scope="session")
def orin_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveRuntime]:
    runtime = LiveRuntime(tmp_path_factory.mktemp("orin-code-live")).start()
    try:
        yield runtime
    finally:
        runtime.stop()
