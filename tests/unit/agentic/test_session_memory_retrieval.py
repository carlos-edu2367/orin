class _Memory:
    def __init__(self):
        self.calls = []

    def relevant(self, task, *, limit=12):
        self.calls.append(("relevant", task, limit))
        return [{"fact": "prefiro respostas curtas"}]

    def recent(self, *, limit=12):
        self.calls.append(("recent", limit))
        return []


def test_the_turn_asks_for_memories_relevant_to_the_task_not_the_most_recent():
    from agentos.agentic.session import memories_for_task

    memory = _Memory()
    result = memories_for_task(memory, "como faço o deploy?")

    assert memory.calls == [("relevant", "como faço o deploy?", 12)]
    assert result == [{"fact": "prefiro respostas curtas"}]


def test_a_store_without_relevance_still_works():
    """An in-memory double from an older test must not break the turn."""
    from agentos.agentic.session import memories_for_task

    class _Old:
        def recent(self, *, limit=12):
            return [{"fact": "algo antigo"}]

    assert memories_for_task(_Old(), "qualquer coisa") == [{"fact": "algo antigo"}]


def test_no_memory_store_yields_no_memories():
    from agentos.agentic.session import memories_for_task

    assert memories_for_task(None, "qualquer coisa") == []


def test_a_failing_store_never_breaks_the_turn():
    from agentos.agentic.session import memories_for_task

    class _Broken:
        def relevant(self, task, *, limit=12):
            raise RuntimeError("database is gone")

    assert memories_for_task(_Broken(), "qualquer coisa") == []
