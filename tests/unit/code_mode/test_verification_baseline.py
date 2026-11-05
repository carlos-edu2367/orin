from __future__ import annotations

from agentos.code_mode.verification_baseline import (
    VerificationBaseline,
    failure_fingerprint,
    verification_observations,
)


def test_pytest_failures_become_their_test_ids() -> None:
    output = "FAILED tests/test_x.py::test_a - AssertionError\nERROR tests/test_y.py::test_b\n1 failed"
    assert failure_fingerprint(output) == {"tests/test_x.py::test_a", "tests/test_y.py::test_b"}


def test_unrecognized_output_falls_back_to_the_exit_code() -> None:
    assert failure_fingerprint("boom", exit_code=2) == {"exit:2"}


def test_unrecognized_output_falls_back_to_none_exit_code() -> None:
    assert failure_fingerprint("something went wrong", exit_code=None) == {"exit:None"}


def test_tsc_errors_become_their_diagnostics() -> None:
    output = "src/index.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'."
    assert failure_fingerprint(output) == {"src/index.ts(12,5): error TS2322"}


def test_ruff_flake8_errors_become_their_diagnostics() -> None:
    output = "src/agentos/session.py:10:5: F401 'sys' imported but unused"
    assert failure_fingerprint(output) == {"src/agentos/session.py:10:5: F401"}


def test_go_test_failures_become_their_test_names() -> None:
    output = "=== RUN   TestSomething\n--- FAIL: TestSomething (0.01s)\nFAIL"
    assert failure_fingerprint(output) == {"TestSomething"}


def test_cargo_test_failures_become_their_test_names() -> None:
    output = "running 1 test\ntest tests::test_add ... FAILED\n\nfailures:\n"
    assert failure_fingerprint(output) == {"tests::test_add"}


def test_a_failure_seen_before_any_edit_is_the_reference() -> None:
    baseline = VerificationBaseline()
    old = frozenset({"t::old"})
    assert baseline.observe("cmd:pytest", failed=True, fingerprint=old, after_edit=False) is False
    assert baseline.observe("cmd:pytest", failed=True, fingerprint=old, after_edit=True) is False
    assert baseline.observe("cmd:pytest", failed=True, fingerprint=old | {"t::new"}, after_edit=True) is True


def test_without_a_reference_a_failure_after_an_edit_is_new() -> None:
    assert VerificationBaseline().observe("cmd:pytest", failed=True, fingerprint=frozenset({"t::a"}), after_edit=True) is True


def test_passed_verification_before_edit_sets_empty_reference() -> None:
    baseline = VerificationBaseline()
    assert baseline.observe("cmd:pytest", failed=False, fingerprint=frozenset(), after_edit=False) is False
    assert baseline.observe("cmd:pytest", failed=True, fingerprint=frozenset({"t::new"}), after_edit=True) is True


def test_passed_verification_after_edit_does_not_fail() -> None:
    baseline = VerificationBaseline()
    assert baseline.observe("cmd:pytest", failed=False, fingerprint=frozenset(), after_edit=True) is False


def test_verification_observations_for_verify_project() -> None:
    payload = {
        "steps": [
            {"step": "test", "ran": True, "passed": False, "output": "FAILED tests/t.py::test_1 - AssertionError"},
            {"step": "lint", "ran": True, "passed": True, "output": "all good"},
            {"step": "typecheck", "ran": False, "passed": None, "output": ""},
        ]
    }
    obs = verification_observations("verify_project", payload, "content", {})
    assert len(obs) == 2
    assert obs[0] == ("verify:test", True, frozenset({"tests/t.py::test_1"}))
    assert obs[1] == ("verify:lint", False, frozenset({"exit:None"}))


def test_verification_observations_for_verify_project_scaffold_returns_empty() -> None:
    payload = {"scaffold": "python-fastapi", "steps": [{"step": "scaffold", "ran": True, "passed": True}]}
    obs = verification_observations("verify_project", payload, "content", {})
    assert obs == []


def test_verification_observations_for_run_command_normalizes_spaces() -> None:
    payload = {"command": "pytest   -v  \t tests/", "failed": True, "exit_code": 1}
    obs = verification_observations(
        "run_command",
        payload,
        "FAILED tests/t.py::test_1 - AssertionError",
        {"command": "pytest   -v  \t tests/"},
    )
    assert len(obs) == 1
    assert obs[0] == ("cmd:pytest -v tests/", True, frozenset({"tests/t.py::test_1"}))


def test_verification_observations_for_non_verification_command_returns_empty() -> None:
    obs = verification_observations("run_command", {"command": "git status"}, "clean", {})
    assert obs == []
