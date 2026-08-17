from __future__ import annotations

from agentos.retrieval.chunking import MAX_CHUNK_LINES, OVERLAP_LINES, HeuristicChunker


def test_python_definitions_become_their_own_chunks() -> None:
    text = (
        "import os\n"
        "\n"
        "def first():\n"
        "    return 1\n"
        "\n"
        "class Second:\n"
        "    def method(self):\n"
        "        return 2\n"
    )

    chunks = HeuristicChunker().split("src/a.py", text)

    assert [chunk.symbol for chunk in chunks] == [None, "first", "Second", "method"]
    assert [chunk.kind for chunk in chunks] == ["block", "definition", "definition", "definition"]
    assert chunks[1].start_line == 3
    assert chunks[1].end_line == 5
    assert "def first():" in chunks[1].text


def test_typescript_exports_and_arrow_consts_are_recognised() -> None:
    text = (
        "export function alpha() {\n"
        "  return 1;\n"
        "}\n"
        "export const beta = (x: number) => {\n"
        "  return x;\n"
        "};\n"
    )

    chunks = HeuristicChunker().split("web/a.ts", text)

    assert [chunk.symbol for chunk in chunks] == ["alpha", "beta"]


def test_markdown_splits_on_headings() -> None:
    text = "# Title\n\nintro\n\n## Section\n\nbody\n"

    chunks = HeuristicChunker().split("docs/a.md", text)

    assert [chunk.symbol for chunk in chunks] == ["Title", "Section"]


def test_a_file_without_definitions_falls_back_to_overlapping_windows() -> None:
    text = "\n".join(f"line {number}" for number in range(1, MAX_CHUNK_LINES * 2 + 1)) + "\n"

    chunks = HeuristicChunker().split("data/a.txt", text)

    assert len(chunks) >= 2
    assert all(chunk.kind == "block" for chunk in chunks)
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == MAX_CHUNK_LINES
    # The second window starts inside the first, so a match on the seam is
    # never split away from its context.
    assert chunks[1].start_line == MAX_CHUNK_LINES - OVERLAP_LINES + 1


def test_a_definition_longer_than_the_window_is_split_but_keeps_its_symbol() -> None:
    body = "\n".join(f"    step_{number}()" for number in range(MAX_CHUNK_LINES * 2))
    text = f"def huge():\n{body}\n"

    chunks = HeuristicChunker().split("src/a.py", text)

    assert len(chunks) >= 2
    assert all(chunk.symbol == "huge" for chunk in chunks)


def test_a_single_line_file_produces_one_chunk() -> None:
    chunks = HeuristicChunker().split("src/a.py", "x = 1\n")

    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 1


def test_blank_content_produces_no_chunks() -> None:
    assert HeuristicChunker().split("src/a.py", "   \n\n") == []
