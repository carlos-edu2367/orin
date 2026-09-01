from agentos.agentic.learning import LearnedMemory, TurnLearningLedger


def test_the_sink_receives_what_the_turn_learned():
    from agentos.agentic.runtime import AgenticTurnRuntime

    received: list[tuple[LearnedMemory, ...]] = []
    runtime = object.__new__(AgenticTurnRuntime)
    runtime.ledger = TurnLearningLedger()
    runtime._learning_sink = received.append
    runtime._learning_committed = False
    runtime.ledger.note_tool_outcome("run_command", {"command": "npm install"}, "failed")
    runtime.ledger.note_tool_outcome("run_command", {"command": "pnpm install"}, "succeeded")

    AgenticTurnRuntime._commit_learning(runtime, "project")

    assert len(received) == 1
    assert received[0][0].kind == "operational"


def test_committing_twice_only_learns_once():
    from agentos.agentic.runtime import AgenticTurnRuntime

    received: list[tuple[LearnedMemory, ...]] = []
    runtime = object.__new__(AgenticTurnRuntime)
    runtime.ledger = TurnLearningLedger()
    runtime._learning_sink = received.append
    runtime._learning_committed = False
    runtime.ledger.note_tool_outcome("run_command", {"command": "npm ci"}, "failed")
    runtime.ledger.note_tool_outcome("run_command", {"command": "pnpm ci"}, "succeeded")

    AgenticTurnRuntime._commit_learning(runtime, "project")
    AgenticTurnRuntime._commit_learning(runtime, "project")

    assert len(received) == 1


def test_a_sink_that_raises_never_escapes():
    from agentos.agentic.runtime import AgenticTurnRuntime

    def explode(_):
        raise RuntimeError("the database is gone")

    runtime = object.__new__(AgenticTurnRuntime)
    runtime.ledger = TurnLearningLedger()
    runtime._learning_sink = explode
    runtime._learning_committed = False
    runtime.ledger.note_tool_outcome("run_command", {"command": "npm i"}, "failed")
    runtime.ledger.note_tool_outcome("run_command", {"command": "pnpm i"}, "succeeded")

    AgenticTurnRuntime._commit_learning(runtime, "project")  # must not raise


def test_a_turn_with_no_sink_is_a_no_op():
    from agentos.agentic.runtime import AgenticTurnRuntime

    runtime = object.__new__(AgenticTurnRuntime)
    runtime.ledger = TurnLearningLedger()
    runtime._learning_sink = None
    runtime._learning_committed = False

    AgenticTurnRuntime._commit_learning(runtime, "user")  # must not raise
