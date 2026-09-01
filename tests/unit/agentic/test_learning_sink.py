from agentos.agentic.learning import LearnedMemory


class _Memory:
    def __init__(self):
        self.saved = []

    def save(self, fact, tags=(), *, kind="fact", confidence=1.0, source="user_explicit"):
        self.saved.append((fact, kind, confidence, source))
        return {"memory_id": f"mem_{len(self.saved)}", "fact": fact, "created": True, "superseded": []}


def _learned(fact="o build é pnpm"):
    return LearnedMemory(fact=fact, kind="operational", scope="project", confidence=0.7, source="mechanical", tags=("comando",))


def test_the_sink_stores_the_memory_and_announces_it():
    from agentos.agentic.session import learning_sink_for

    memory, recorded = _Memory(), []
    sink = learning_sink_for(memory, lambda event_type, summary, payload: recorded.append((event_type, summary, payload)), "project:a")
    sink((_learned(),))

    assert memory.saved == [("o build é pnpm", "operational", 0.7, "mechanical")]
    assert recorded[0][0].value == "memory.learned"
    assert recorded[0][2] == {
        "memory_id": "mem_1", "fact": "o build é pnpm", "kind": "operational",
        "scope": "project", "project_id": "project:a", "source": "mechanical",
    }


def test_a_memory_that_was_already_known_is_not_announced_again():
    from agentos.agentic.session import learning_sink_for

    class _Known(_Memory):
        def save(self, fact, tags=(), *, kind="fact", confidence=1.0, source="user_explicit"):
            super().save(fact, tags, kind=kind, confidence=confidence, source=source)
            return {"memory_id": "mem_old", "fact": fact, "created": False, "superseded": []}

    recorded = []
    learning_sink_for(_Known(), lambda *args: recorded.append(args), None)((_learned(),))

    assert recorded == []


def test_a_store_that_raises_never_escapes_the_sink():
    from agentos.agentic.session import learning_sink_for

    class _Broken:
        def save(self, *args, **kwargs):
            raise RuntimeError("the database is gone")

    learning_sink_for(_Broken(), lambda *args: None, None)((_learned(),))  # must not raise


def test_no_memory_store_makes_the_sink_a_no_op():
    from agentos.agentic.session import learning_sink_for

    assert learning_sink_for(None, lambda *args: None, None) is None
