"""W5: plugin commands survive a restart — the API process rebuilds a person's command index on first use."""
from __future__ import annotations

from pathlib import Path

from agentos.plugins.command_library import CommandLibrary


class _Command:
    def __init__(self, slug: str) -> None:
        self.command_id = f"plug:{slug}"
        self.slug = slug
        self.description = "d"
        self.argument_hint = ""
        self.relative_path = f"commands/{slug}.md"


def test_a_fresh_library_rebuilds_a_persons_commands_once(tmp_path: Path) -> None:
    calls: list[str] = []
    library = CommandLibrary()

    def rehydrate(user_id: str) -> None:
        calls.append(user_id)
        library.install_plugin_commands(user_id=user_id, plugin_id="plug", install_path=tmp_path, commands=[_Command("ship")])

    library.set_rehydrator(rehydrate)
    assert library.resolve("user-1", "ship") is not None
    assert [item["slug"] for item in library.list("user-1")] == ["ship"]
    assert library.resolve("user-1", "plug:ship") is not None
    assert calls == ["user-1"]  # once per person, not per lookup
    library.list("user-2")
    assert calls == ["user-1", "user-2"]


def test_a_failing_rebuild_is_not_fatal_and_is_retried_later(tmp_path: Path) -> None:
    attempts: list[str] = []
    library = CommandLibrary()

    def rehydrate(user_id: str) -> None:
        attempts.append(user_id)
        if len(attempts) == 1:
            raise RuntimeError("database not ready")

    library.set_rehydrator(rehydrate)
    assert library.list("user-1") == ()
    library.list("user-1")
    assert attempts == ["user-1", "user-1"]


def test_without_a_rehydrator_nothing_changes(tmp_path: Path) -> None:
    library = CommandLibrary()
    assert library.list("user-1") == () and library.resolve("user-1", "x") is None
