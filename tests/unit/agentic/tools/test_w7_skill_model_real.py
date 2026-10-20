"""W7 Batch F (W6-R17): a skill's or agent file's model is checked by the session's real rule, aliases included."""
from __future__ import annotations

from pathlib import Path


def test_aliases_resolve_and_only_allowed_models_pass_through_the_real_check(tmp_path: Path) -> None:
    from tests.unit.cli_code.test_cli_turn_session import _session

    session = _session(tmp_path)
    session.effective_settings = type("S", (), {"layers": (type("L", (), {"name": "user", "data": {"modelAliases": {"sonnet": "m2"}}})(),)})()
    # Only the person's allow-list is stood in for; the alias, the resolution and the check are the session's own.
    session._child_model_authorizer = lambda model: model == "m2"
    current = session._current_model_id()
    assert session._allowed_requested_model("sonnet", "a skill") == "m2"
    assert session._allowed_requested_model("m3", "a skill") is None
    assert session._allowed_requested_model("inherit", "a skill") is None
    assert current and session._allowed_requested_model(current, "a skill") == current  # the parent's own model is fine
