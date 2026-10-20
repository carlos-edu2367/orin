"""W6 (W3-R23): the consolidation counters survive concurrent processes, and one run is claimed once."""
from __future__ import annotations

import json
import subprocess
import sys
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

CLAIMER = """
import sys
from datetime import UTC, datetime
from agentos.memory.consolidation import ConsolidationState
print("claimed" if ConsolidationState(sys.argv[1]).claim_run("u", datetime(2026, 1, 1, tzinfo=UTC)) else "no")
"""


def test_concurrent_processes_lose_no_count(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    workers = [subprocess.Popen([sys.executable, "-c", WORKER, str(path), "60"]) for _ in range(4)]
    for worker in workers:
        assert worker.wait(timeout=120) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["u"]["turns"] == 240
    assert not list(tmp_path.glob("*.tmp"))


def test_only_one_process_claims_a_due_run(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = ConsolidationState(path)
    for _ in range(MIN_TURNS_BETWEEN_RUNS):
        state.note_completed_turn("u")
    claimers = [subprocess.Popen([sys.executable, "-c", CLAIMER, str(path)], stdout=subprocess.PIPE, text=True) for _ in range(4)]
    answers = [claimer.communicate(timeout=120)[0].strip() for claimer in claimers]
    assert answers.count("claimed") == 1
    assert state.claim_run("u", datetime(2026, 1, 1, tzinfo=UTC)) is False
