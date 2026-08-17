from __future__ import annotations

import pytest

from agentos.retrieval.models import Chunk, EmbedderIdentity, IndexStatus, SearchHit


def test_chunk_id_is_derived_from_path_and_line_span() -> None:
    chunk = Chunk(path="src/a.py", start_line=10, end_line=25, symbol="run", kind="definition", text="def run():\n    pass")

    assert chunk.chunk_id == "src/a.py:10-25"


def test_chunk_rejects_an_inverted_line_span() -> None:
    with pytest.raises(ValueError):
        Chunk(path="src/a.py", start_line=9, end_line=2, symbol=None, kind="block", text="x")


def test_embedder_identity_is_comparable_by_value() -> None:
    first = EmbedderIdentity(embedder_id="ollama", model="nomic-embed-text", dim=768)
    second = EmbedderIdentity(embedder_id="ollama", model="nomic-embed-text", dim=768)

    assert first == second
    assert first != EmbedderIdentity(embedder_id="ollama", model="mxbai-embed-large", dim=1024)


def test_index_status_reports_lexical_mode_when_there_are_no_vectors() -> None:
    status = IndexStatus(files=3, chunks=40, vectors=0, last_scan_at=None)

    assert status.mode == "lexical"
    assert IndexStatus(files=3, chunks=40, vectors=40, last_scan_at=None).mode == "semantic"


def test_search_hit_renders_a_citable_location() -> None:
    hit = SearchHit(path="src/a.py", start_line=10, end_line=25, symbol="run", score=0.5, text="def run():")

    assert hit.location == "src/a.py:10-25"
