"""Regressions: read/edit outside the root, partial reads, repeated failures, grep/glob filters and context."""
from __future__ import annotations

import os
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.read_state import prepare_carried_state
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.tools.search.glob import GlobTools, expand_braces, parse_glob_filter
from agentos.agentic.workspace import ConversationWorkspace


def _outside_toolset(tmp_path: Path, state: dict | None = None) -> tuple[AgentToolset, Path]:
    root = tmp_path / "ws"
    root.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    target = extra / "a.ts"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    workspace = ConversationWorkspace.at_root(root)
    workspace.additional_roots = (extra.resolve(),)
    return AgentToolset(workspace, code_mode_active=True, read_file_state=state), target


# --- A: read/edit outside the root -------------------------------------------------------------
def test_edit_outside_root_after_read_with_either_separator(tmp_path: Path) -> None:
    toolset, target = _outside_toolset(tmp_path)
    for variant in (str(target), str(target).replace("\\", "/"), str(target).replace("/", "\\")):
        assert toolset.invoke("read_file", {"path": variant, "force_reload": True}).status == "succeeded"
        new = f"x{abs(hash(variant)) % 1000}"
        old = target.read_text(encoding="utf-8").splitlines()[1]
        out = toolset.invoke("edit_file", {"path": variant, "old_text": old, "new_text": new})
        assert out.status == "succeeded", out.content


def test_outside_root_key_is_normalised_on_drive_case(tmp_path: Path) -> None:
    toolset, target = _outside_toolset(tmp_path)
    key = toolset.workspace.key(toolset.workspace.resolve(str(target)))
    assert "\\" not in key
    if os.name == "nt":
        assert key[0].isupper() and key[1] == ":"
        lowered = str(target)[0].lower() + str(target)[1:]
        assert toolset.workspace.key(toolset.workspace.resolve(lowered)) == key


def test_carried_state_keeps_outside_root_reads_before_directories_are_granted(tmp_path: Path) -> None:
    toolset, target = _outside_toolset(tmp_path)
    toolset.invoke("read_file", {"path": str(target)})
    state = toolset._read_file_state
    assert len(state) == 1
    bare = ConversationWorkspace.at_root(tmp_path / "ws")  # additional roots not applied yet
    prepare_carried_state(state, bare)
    assert len(state) == 1
    toolset.workspace.additional_roots = (target.parent.resolve(),)
    out = toolset.invoke("edit_file", {"path": str(target), "old_text": "two", "new_text": "2"})
    assert out.status == "succeeded", out.content


# --- B: partial read is enough -----------------------------------------------------------------
def test_partial_read_allows_edit_of_unread_line_and_stale_still_refused(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "c")
    path = workspace.root / "p.test.ts"
    path.write_text("\n".join(f"line {n}" for n in range(1, 101)) + "\n", encoding="utf-8")
    toolset = AgentToolset(workspace, code_mode_active=True)
    assert toolset.invoke("read_file", {"path": "p.test.ts", "offset": 1, "limit": 10}).status == "succeeded"
    ok = toolset.invoke("edit_file", {"path": "p.test.ts", "old_text": "line 90", "new_text": "LINE 90"})
    assert ok.status == "succeeded", ok.content
    # the file changes behind our back -> stale read is refused
    path.write_text(path.read_text(encoding="utf-8") + "extra\n", encoding="utf-8")
    stale = toolset.invoke("edit_file", {"path": "p.test.ts", "old_text": "line 91", "new_text": "L"})
    assert stale.status == "failed" and stale.error_code == "STALE_FILE_READ"
    # a missing old_text is reported as such
    toolset.invoke("read_file", {"path": "p.test.ts", "offset": 1, "limit": 5})
    missing = toolset.invoke("edit_file", {"path": "p.test.ts", "old_text": "nope", "new_text": "x"})
    assert missing.status == "failed" and "not found" in missing.content


def test_write_after_partial_read_is_allowed(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "c")
    (workspace.root / "w.txt").write_text("a\nb\nc\n", encoding="utf-8")
    toolset = AgentToolset(workspace, code_mode_active=True)
    toolset.invoke("read_file", {"path": "w.txt", "offset": 1, "limit": 1})
    assert toolset.invoke("write_file", {"path": "w.txt", "content": "new\n"}).status == "succeeded"


# --- C: identical retry after a failure ---------------------------------------------------------
class _Toolset:
    def __init__(self) -> None:
        self.fail = True
        self.calls = 0

    def is_read_only(self, name):
        return False

    def argument_names(self, name):
        return frozenset({"path", "q"})

    def invoke(self, name, arguments):
        self.calls += 1
        if name == "edit" and self.fail:
            return ToolOutcome("failed", "bad", "File has not been read yet.", {}, "FILE_NOT_READ")
        return ToolOutcome("succeeded", "ok", "done", {})


def _turn():
    return {"turn_id": "t", "conversation_id": "c", "user_id": "u"}


def test_identical_failed_call_is_blocked_then_allowed_after_other_success() -> None:
    toolset = _Toolset()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)
    runtime._failed_signatures = {}
    call = {"id": "1", "name": "edit", "arguments": '{"path": "a"}'}
    assert runtime._run_toolset(_turn(), [call])[0]["status"] == "failed"
    blocked = runtime._run_toolset(_turn(), [{**call, "id": "2"}])[0]
    assert blocked["status"] == "failed" and toolset.calls == 1
    assert "Change the arguments" in blocked["content"] and "succeeded in between" in blocked["content"]
    assert runtime._run_toolset(_turn(), [{"id": "3", "name": "read", "arguments": '{"path": "a"}'}])[0]["status"] == "succeeded"
    toolset.fail = False
    assert runtime._run_toolset(_turn(), [{**call, "id": "4"}])[0]["status"] == "succeeded"
    assert toolset.calls == 3


# --- D/E: globs ---------------------------------------------------------------------------------
def test_brace_expansion_nested_and_lists() -> None:
    assert sorted(expand_braces("{a,b}/*.{ts,tsx}")) == ["a/*.ts", "a/*.tsx", "b/*.ts", "b/*.tsx"]
    assert sorted(expand_braces("x/{a,{b,c}}")) == ["x/a", "x/b", "x/c"]
    assert expand_braces("{oops") == ["{oops"]
    assert parse_glob_filter("*.ts, !**/types.ts *.md") == (["*.ts", "*.md"], ["**/types.ts"])
    assert parse_glob_filter("{docs/n8n/*,src/**}")[0] == ["docs/n8n/*", "src/**"]


def test_glob_matches_braces_and_negation() -> None:
    match = GlobTools._glob_matches_path
    pattern = "{docs/n8n/*,supabase/functions/**,src/**}"
    assert match("docs/n8n/flow.json", pattern)
    assert match("supabase/functions/a/index.ts", pattern)
    assert match("src/a/b.ts", pattern)
    assert not match("lib/a.ts", pattern)
    assert match("src/a.ts", "!**/types.ts") and not match("src/types.ts", "!**/types.ts")
    assert not match("types.ts", "!**/types.ts")
    assert match("a.ts", "*.ts,!**/types.ts") and not match("x/types.ts", "*.ts,!**/types.ts", allow_basename=True)


def _search_toolset(tmp_path: Path) -> AgentToolset:
    workspace = ConversationWorkspace(tmp_path / "w", "c1")
    for rel, text in {
        "docs/n8n/a.md": "needle\n", "supabase/functions/f/index.ts": "needle\n",
        "src/x/types.ts": "needle\n", "src/x/real.ts": "needle\n", "other/z.ts": "needle\n",
    }.items():
        file = workspace.root / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
    return AgentToolset(workspace)


def test_grep_glob_with_braces_and_negation(tmp_path: Path) -> None:
    toolset = _search_toolset(tmp_path)
    out = toolset.invoke("Grep", {"pattern": "needle", "glob": "{docs/n8n/*,supabase/functions/**,src/**}"})
    assert set(out.content.splitlines()) == {
        "docs/n8n/a.md", "supabase/functions/f/index.ts", "src/x/types.ts", "src/x/real.ts",
    }
    neg = toolset.invoke("Grep", {"pattern": "needle", "glob": "!**/types.ts"})
    assert "src/x/types.ts" not in neg.content and "src/x/real.ts" in neg.content and "other/z.ts" in neg.content
    both = toolset.invoke("Grep", {"pattern": "needle", "glob": "src/**,!**/types.ts"})
    assert both.content.splitlines() == ["src/x/real.ts"]


def test_glob_tool_braces_and_negation(tmp_path: Path) -> None:
    toolset = _search_toolset(tmp_path)
    out = toolset.invoke("Glob", {"pattern": "{docs,src}/**/*.{md,ts}"})
    assert set(out.content.splitlines()) == {"docs/n8n/a.md", "src/x/types.ts", "src/x/real.ts"}
    neg = toolset.invoke("Glob", {"pattern": "src/**/*.ts,!**/types.ts"})
    assert neg.content.splitlines() == ["src/x/real.ts"]


# --- F: merged context --------------------------------------------------------------------------
def test_overlapping_context_windows_are_merged(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path / "w", "c1")
    (workspace.root / "m.txt").write_text("\n".join(f"l{n}{' hit' if n in (3, 5, 20) else ''}" for n in range(1, 31)) + "\n", encoding="utf-8")
    toolset = AgentToolset(workspace)
    out = toolset.invoke("Grep", {"pattern": "hit", "output_mode": "content", "-C": 2})
    lines = out.content.splitlines()
    numbered = [line for line in lines if line != "--"]
    assert len(numbered) == len(set(numbered))
    assert lines.count("--") == 1
    assert "m.txt:3: l3 hit" in lines and "m.txt:5: l5 hit" in lines and "m.txt:20: l20 hit" in lines
    assert "m.txt-1: l1" in lines and "m.txt+4: l4" in lines and "m.txt+7: l7" in lines
    assert "m.txt-18: l18" in lines and "m.txt+22: l22" in lines
    assert not any(line.startswith("m.txt") and ": l9" in line for line in lines)
    # 3 matches, 2 windows: 1..7 and 18..22 -> 7 + 5 lines
    assert len(numbered) == 12
