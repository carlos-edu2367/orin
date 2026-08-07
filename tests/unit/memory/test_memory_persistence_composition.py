from agentos.memory.models import MemoryCommitState, MemoryCommitResult
from agentos.memory.ports import MemoryTransactionalCommitPort


def test_memory_commit_port_exposes_inspection_without_importing_concrete_persistence():
    assert getattr(MemoryTransactionalCommitPort, "_is_protocol", False) is True
    result = MemoryCommitResult(False, False, None, "event:1", MemoryCommitState.UNKNOWN, "transaction:1")
    assert result.commit_state is MemoryCommitState.UNKNOWN
    assert result.transaction_id == "transaction:1"
