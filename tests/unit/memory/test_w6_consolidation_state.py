"""W6 (W3-R23): the consolidation counters survive concurrent processes, and one run is claimed once."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from agentos.memory.consolidation import MIN_TURNS_BETWEEN_RUNS, ConsolidationState

WORKER = """
import sys
from agentos.memory.consolidation import ConsolidationState
state = ConsolidationState(sys.argv[1])
for _ in range(int(sys.argv[2])):
    state.note_completed_turn("u")
"""

# Each claimer announces it is ready, waits for the barrier file, then claims. The read is slowed (widened window) in every
# run, so the claimers really overlap; ORIN_TEST_NO_CONSOLIDATION_LOCK=1 is the negative control: the lock is a no-op there.
CLAIMER = """
import contextlib, os, sys, time
from datetime import UTC, datetime
from agentos.memory import consolidation
if os.environ.get("ORIN_TEST_NO_CONSOLIDATION_LOCK") == "1":
    @contextlib.contextmanager
    def _no_lock(path, *args, **kwargs):
        yield True
    consolidation._file_lock = _no_lock
_read = consolidation.ConsolidationState._read
def _slow_read(self):
    time.sleep(0.05)
    return _read(self)
consolidation.ConsolidationState._read = _slow_read
open(sys.argv[3], "w").close()
while not os.path.exists(sys.argv[2]):
    time.sleep(0.001)
print("claimed" if consolidation.ConsolidationState(sys.argv[1]).claim_run("u", datetime(2026, 1, 1, tzinfo=UTC)) else "no")
"""


def _race(tmp_path: Path, *, lock_on: bool, claimers: int = 4) -> list[str]:
    """One state file, `claimers` processes started together: they all announce, then the test releases them at once."""
    path = tmp_path / "state.json"
    state = ConsolidationState(path)
    for _ in range(MIN_TURNS_BETWEEN_RUNS):
        state.note_completed_turn("u")
    ready, barrier = tmp_path / "ready", tmp_path / "go"
    ready.mkdir()
    env = {**os.environ, "ORIN_TEST_NO_CONSOLIDATION_LOCK": "" if lock_on else "1"}
    processes = [subprocess.Popen([sys.executable, "-c", CLAIMER, str(path), str(barrier), str(ready / f"r{number}")],
                                  env=env, stdout=subprocess.PIPE, text=True) for number in range(claimers)]
    deadline = time.monotonic() + 60
    while len(list(ready.iterdir())) < claimers and time.monotonic() < deadline:
        time.sleep(0.01)
    barrier.write_text("go", encoding="utf-8")
    return [process.communicate(timeout=120)[0].strip() for process in processes]


def test_concurrent_processes_lose_no_count(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    workers = [subprocess.Popen([sys.executable, "-c", WORKER, str(path), "60"]) for _ in range(4)]
    for worker in workers:
        assert worker.wait(timeout=120) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["u"]["turns"] == 240
    assert not list(tmp_path.glob("*.tmp"))


def test_only_one_process_claims_a_due_run(tmp_path: Path) -> None:
    # Negative control first: with the lock a no-op the same race lets several claimers through, so this test can fail.
    unlocked = _race(tmp_path / "unlocked", lock_on=False)
    assert unlocked.count("claimed") > 1, unlocked
    # The real run: the lock makes the claim one step, so exactly one claimer runs consolidation.
    answers = _race(tmp_path / "locked", lock_on=True)
    assert answers.count("claimed") == 1, answers
    assert ConsolidationState(tmp_path / "locked" / "state.json").claim_run("u", datetime(2026, 1, 1, tzinfo=UTC)) is False
