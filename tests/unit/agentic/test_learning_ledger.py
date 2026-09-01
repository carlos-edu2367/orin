from agentos.agentic.learning import LearnedMemory, TurnLearningLedger


def test_a_command_resolved_by_a_sibling_command_becomes_one_operational_memory():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": "npm install"}, "failed")
    ledger.note_tool_outcome("run_command", {"command": "pnpm install"}, "succeeded")

    memories = ledger.mechanical_memories("project")

    assert memories == (
        LearnedMemory(
            fact="Neste workspace, `pnpm install` funciona onde `npm install` falha.",
            kind="operational",
            scope="project",
            confidence=0.7,
            source="mechanical",
            tags=("comando",),
        ),
    )


def test_unrelated_commands_are_not_treated_as_a_resolution():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": "npm install"}, "failed")
    ledger.note_tool_outcome("run_command", {"command": "git status"}, "succeeded")

    assert ledger.mechanical_memories("project") == ()


def test_the_same_command_succeeding_later_is_a_retry_not_a_lesson():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": "npm test"}, "failed")
    ledger.note_tool_outcome("run_command", {"command": "npm test"}, "succeeded")

    assert ledger.mechanical_memories("project") == ()


def test_only_run_command_is_mined_in_this_phase():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("read_file", {"path": "a.txt"}, "failed")
    ledger.note_tool_outcome("read_file", {"path": "b.txt"}, "succeeded")

    assert ledger.mechanical_memories("project") == ()


def test_a_resolution_is_reported_once_even_if_the_command_runs_again():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": "npm install"}, "failed")
    ledger.note_tool_outcome("run_command", {"command": "pnpm install"}, "succeeded")
    ledger.note_tool_outcome("run_command", {"command": "pnpm install"}, "succeeded")

    assert len(ledger.mechanical_memories("project")) == 1


def test_malformed_arguments_never_raise():
    ledger = TurnLearningLedger()
    ledger.note_tool_outcome("run_command", {"command": None}, "failed")
    ledger.note_tool_outcome("run_command", {}, "succeeded")
    ledger.note_tool_outcome("run_command", "not a mapping", "succeeded")  # type: ignore[arg-type]

    assert ledger.mechanical_memories("project") == ()
