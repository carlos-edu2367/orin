from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools._shared import ReadFileRecord
from agentos.agentic.workspace import ConversationWorkspace
from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.indexer import ProjectIndexer
from agentos.retrieval.models import EmbedderIdentity
from agentos.retrieval.service import RetrievalService
from agentos.retrieval.store import SCHEMA_VERSION, SqliteChunkStore


class _DummyEmbedder:
    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="fake", model="test", dim=4)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _setup_service(tmp_path: Path) -> tuple[RetrievalService, ProjectIndexer, SqliteChunkStore, Path]:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    embedder = _DummyEmbedder()
    store = SqliteChunkStore(tmp_path / "index.db", embedder.identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    service = RetrievalService(store=store, indexer=indexer, embedder=embedder)
    return service, indexer, store, root


def test_focus_paths_promotes_direct_and_transitive_neighbors_above_generic_utils(tmp_path: Path) -> None:
    service, indexer, _store, root = _setup_service(tmp_path)

    # Generic utility imported by 3 unrelated modules (would win in static most_imported)
    (root / "generic_util.py").write_text("def log_msg():\n    return 'ok'\n", encoding="utf-8")
    for idx in range(3):
        (root / f"unrelated_{idx}.py").write_text(
            "from generic_util import log_msg\n\ndef run_job():\n    return log_msg()\n",
            encoding="utf-8",
        )

    # Active subsystem chain: active_entry.py -> billing_service.py -> ledger_repo.py
    (root / "ledger_repo.py").write_text(
        "def persist_ledger_entry():\n    return True\n",
        encoding="utf-8",
    )
    (root / "billing_service.py").write_text(
        "from ledger_repo import persist_ledger_entry\n\n"
        "def process_invoice_payment():\n"
        "    return persist_ledger_entry()\n",
        encoding="utf-8",
    )
    (root / "active_entry.py").write_text(
        "from billing_service import process_invoice_payment\n\n"
        "def handle_checkout_webhook():\n"
        "    return process_invoice_payment()\n",
        encoding="utf-8",
    )
    indexer.scan(embed=False)

    # Cold start: generic_util is imported by 3 modules so it ranks near the top
    cold_entries = service.project_map(limit=10)
    cold_paths = [str(item["path"]) for item in cold_entries]
    assert "generic_util.py" in cold_paths[:2]

    # Focused on active_entry.py: active_entry, billing_service (direct) and ledger_repo (transitive)
    # must all outrank generic_util.py!
    focused_entries = service.project_map(limit=10, focus_paths=["active_entry.py"])
    focused_paths = [str(item["path"]) for item in focused_entries]

    active_cluster = {"active_entry.py", "billing_service.py", "ledger_repo.py"}
    assert set(focused_paths[:3]) == active_cluster
    assert focused_paths.index("ledger_repo.py") < focused_paths.index("generic_util.py")


def test_query_identifier_and_edge_multipliers_for_compound_private_and_ubiquitous_symbols(
    tmp_path: Path,
) -> None:
    service, indexer, store, root = _setup_service(tmp_path)

    # 1. Compound snake_case >= 8 chars and CamelCase >= 8 chars
    (root / "order_engine.py").write_text(
        "class OrderSettlementEngine:\n    pass\n\ndef calculate_settlement_tax():\n    return 1\n",
        encoding="utf-8",
    )
    (root / "order_caller.py").write_text(
        "from order_engine import OrderSettlementEngine, calculate_settlement_tax\n\n"
        "def trigger_order():\n"
        "    engine = OrderSettlementEngine()\n"
        "    return calculate_settlement_tax()\n",
        encoding="utf-8",
    )

    # 2. Private symbol starting with '_'
    (root / "private_mod.py").write_text(
        "def _internal_secret_helper():\n    return 2\n",
        encoding="utf-8",
    )
    (root / "private_caller.py").write_text(
        "from private_mod import _internal_secret_helper\n\n"
        "def call_priv():\n"
        "    return _internal_secret_helper()\n",
        encoding="utf-8",
    )

    # 3. Ubiquitous symbol defined in > 5 files (6 files)
    for idx in range(6):
        (root / f"ubiq_{idx}.py").write_text(
            "def validate_payload_schema():\n    return True\n",
            encoding="utf-8",
        )
    (root / "ubiq_caller.py").write_text(
        "from ubiq_0 import validate_payload_schema\n\n"
        "def run_validation():\n"
        "    return validate_payload_schema()\n",
        encoding="utf-8",
    )

    indexer.scan(embed=False)

    edges = {
        (src, dst): (weight, syms)
        for src, dst, weight, syms in store.dependency_edges()
    }
    assert ("order_caller.py", "order_engine.py") in edges
    assert ("private_caller.py", "private_mod.py") in edges
    assert ("ubiq_caller.py", "ubiq_0.py") in edges

    compound_weight, compound_syms = edges[("order_caller.py", "order_engine.py")]
    private_weight, private_syms = edges[("private_caller.py", "private_mod.py")]
    ubiq_weight, ubiq_syms = edges[("ubiq_caller.py", "ubiq_0.py")]

    assert "OrderSettlementEngine" in compound_syms
    assert "calculate_settlement_tax" in compound_syms
    assert "_internal_secret_helper" in private_syms
    assert "validate_payload_schema" in ubiq_syms

    # Compound symbol gets 10x while private and ubiquitous (>5 files) get 0.1x
    assert compound_weight > private_weight * 50
    assert compound_weight > ubiq_weight * 50
    assert private_weight < 0.5
    assert ubiq_weight < 0.5

    # Querying for calculate_settlement_tax promotes order_engine.py and order_caller.py to top 2
    queried = service.project_map(limit=10, query="Fix bug in calculate_settlement_tax")
    queried_paths = [str(item["path"]) for item in queried]
    assert set(queried_paths[:2]) == {"order_engine.py", "order_caller.py"}

    # Querying by explicit filename promotes that file
    by_filename = service.project_map(limit=10, query="inspect private_mod.py")
    assert by_filename[0]["path"] == "private_mod.py"


def test_cold_start_promotes_transitively_central_modules_over_isolated_leaves(tmp_path: Path) -> None:
    service, indexer, _store, root = _setup_service(tmp_path)

    # Deep foundation module imported by core_hub, which is imported by 3 leaf services
    (root / "foundation.py").write_text("def base_primitive():\n    return 1\n", encoding="utf-8")
    (root / "core_hub.py").write_text(
        "from foundation import base_primitive\n\ndef hub_dispatch():\n    return base_primitive()\n",
        encoding="utf-8",
    )
    for idx in range(3):
        (root / f"leaf_{idx}.py").write_text(
            "from core_hub import hub_dispatch\n\ndef endpoint():\n    return hub_dispatch()\n",
            encoding="utf-8",
        )
    (root / "isolated.py").write_text("def standalone():\n    return 0\n", encoding="utf-8")

    indexer.scan(embed=False)

    entries = service.project_map(limit=10)
    paths = [str(item["path"]) for item in entries]

    # Both core_hub.py and foundation.py (transitively depended upon by all leaves) rank top 2
    assert set(paths[:2]) == {"core_hub.py", "foundation.py"}
    assert paths[-1] == "isolated.py"


def test_binary_search_respects_max_tokens_and_renders_symbol_start_lines(tmp_path: Path) -> None:
    service, indexer, _store, root = _setup_service(tmp_path)

    # Create 40 modules, each with 10 functions (~1800+ tokens total when all rendered)
    for mod_idx in range(40):
        lines: list[str] = []
        if mod_idx > 0:
            lines.append(f"from mod_{mod_idx - 1:02d} import symbol_{mod_idx - 1:02d}_00")
        for fn_idx in range(10):
            lines.append(f"def symbol_{mod_idx:02d}_{fn_idx:02d}():")
            lines.append(f"    return {mod_idx + fn_idx}")
        (root / f"mod_{mod_idx:02d}.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    indexer.scan(embed=False)

    workspace = ConversationWorkspace.at_root(root)
    toolset = AgentToolset(workspace, retrieval=service)

    for budget in (150, 600, 1200):
        outcome = toolset.invoke("project_map", {"max_tokens": budget, "limit": 50})
        assert outcome.status == "succeeded"
        rendered = outcome.content
        est_tokens = max(1, len(rendered) // 4)
        assert budget * 0.85 <= est_tokens <= budget * 1.15, (
            f"expected tokens within 15% of {budget}, got {est_tokens} (len={len(rendered)})"
        )
        # Verify symbol:start_line format is rendered in tool output
        assert ":1" in rendered or ":2" in rendered

    # Also verify RetrievalService.project_map entry structure directly
    entries = service.project_map(limit=5, max_tokens=300)
    assert entries
    first = entries[0]
    assert "path" in first
    assert "imported_by" in first
    assert "symbols" in first
    assert "symbol_lines" in first
    assert "score" in first
    assert isinstance(first["score"], float)
    sym_lines = first["symbol_lines"]
    assert isinstance(sym_lines, list) and sym_lines
    sym_name, start_line = sym_lines[0]
    assert isinstance(sym_name, str) and isinstance(start_line, int) and start_line >= 1


def test_schema_version_upgrade_from_v1_to_v2_rebuilds_symbol_refs_automatically(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == "2"
    db_path = tmp_path / "legacy_v1.db"
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    lib_file = root / "lib.py"
    app_file = root / "app.py"
    lib_file.write_text("def compute_magic_number():\n    return 42\n", encoding="utf-8")
    app_file.write_text(
        "from lib import compute_magic_number\n\ndef main():\n    return compute_magic_number()\n",
        encoding="utf-8",
    )

    # Simulate a pre-existing v1 database where files were already indexed with current mtime/size
    # so an incremental scan would skip them if v1->v2 reconciliation didn't clear files!
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE files (
            path TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            language TEXT,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            symbol TEXT,
            kind TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, symbol, text, tokenize = 'unicode61');
        CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY, embedding BLOB NOT NULL);
        CREATE TABLE imports (path TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY (path, target));
        CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    for rel, fpath in (("lib.py", lib_file), ("app.py", app_file)):
        st = fpath.stat()
        conn.execute(
            "INSERT INTO files VALUES (?, 'oldhash', ?, ?, 'python', '2026-01-01T00:00:00+00:00')",
            (rel, st.st_size, st.st_mtime_ns),
        )
    conn.executemany(
        "INSERT INTO index_meta(key, value) VALUES (?, ?)",
        [
            ("schema_version", "1"),
            ("embedder_id", "fake"),
            ("model", "test"),
            ("dim", "4"),
            ("last_scan_at", "2026-11-11T00:00:00+00:00"),
        ],
    )
    conn.commit()
    conn.close()

    # Reopen with SqliteChunkStore (v2) and run RetrievalService.project_map
    embedder = _DummyEmbedder()
    store = SqliteChunkStore(db_path, embedder.identity)
    assert store.status().files == 0
    assert store.status().last_scan_at is None

    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    service = RetrievalService(store=store, indexer=indexer, embedder=embedder)

    entries = service.project_map(limit=10)
    assert entries[0]["path"] == "lib.py"
    edges = store.dependency_edges()
    assert any(src == "app.py" and dst == "lib.py" and "compute_magic_number" in syms for src, dst, _, syms in edges)


def test_project_map_tool_automatically_injects_focus_paths_from_read_file_state(tmp_path: Path) -> None:
    service, indexer, _store, root = _setup_service(tmp_path)

    # Generic utility imported by 3 unrelated modules
    (root / "popular_util.py").write_text("def fmt_str():\n    return 'x'\n", encoding="utf-8")
    for idx in range(3):
        (root / f"caller_{idx}.py").write_text(
            "from popular_util import fmt_str\n\ndef c():\n    return fmt_str()\n",
            encoding="utf-8",
        )

    # Subsystem of interest: feature_controller.py -> feature_domain.py
    (root / "feature_domain.py").write_text(
        "def execute_domain_workflow():\n    return 99\n",
        encoding="utf-8",
    )
    (root / "feature_controller.py").write_text(
        "from feature_domain import execute_domain_workflow\n\n"
        "def handle_feature_request():\n"
        "    return execute_domain_workflow()\n",
        encoding="utf-8",
    )
    indexer.scan(embed=False)

    read_state: dict[str, ReadFileRecord] = {}
    workspace = ConversationWorkspace.at_root(root)
    toolset = AgentToolset(workspace, retrieval=service, read_file_state=read_state)

    # Read feature_controller.py through the toolset so _read_file_state is populated
    read_outcome = toolset.invoke("read_file", {"path": "feature_controller.py"})
    assert read_outcome.status == "succeeded"
    assert "feature_controller.py" in read_state

    # Now invoke project_map without arguments: it should automatically inject focus_paths
    map_outcome = toolset.invoke("project_map", {})
    assert map_outcome.status == "succeeded"
    lines = [line for line in map_outcome.content.splitlines() if line.strip()]
    assert lines[0].startswith("feature_controller.py ") or lines[0].startswith("feature_domain.py ")
    top_two = {lines[0].split(" ", 1)[0], lines[1].split(" ", 1)[0]}
    assert top_two == {"feature_controller.py", "feature_domain.py"}
    assert "execute_domain_workflow:1" in map_outcome.content


def test_m6_incremental_scan_fts_filter_absolute_focus_paths_nan_guard_and_tool_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agentos.agentic.tools._shared import AgentToolError
    from agentos.retrieval.pagerank import personalized_pagerank

    service, indexer, store, root = _setup_service(tmp_path)

    (root / "untouched_unrelated.py").write_text("def completely_unrelated():\n    return 1\n", encoding="utf-8")
    (root / "untouched_caller.py").write_text(
        "def caller():\n    return newly_added_symbol_xyz()\n",
        encoding="utf-8",
    )
    (root / "evolving_def.py").write_text("def old_symbol():\n    return 0\n", encoding="utf-8")
    indexer.scan(embed=False)

    # 1. Incremental scan(only=["evolving_def.py"]) with a new symbol should query FTS5
    # and NOT read untouched_unrelated.py from disk!
    (root / "evolving_def.py").write_text(
        "def old_symbol():\n    return 0\n\ndef newly_added_symbol_xyz():\n    return 42\n",
        encoding="utf-8",
    )
    assert store.paths_mentioning_tokens({"newly_added_symbol_xyz"}) == {"untouched_caller.py"}

    read_paths: list[str] = []
    orig_read_bytes = Path.read_bytes

    def _spy_read_bytes(self_path: Path) -> bytes:
        read_paths.append(self_path.name)
        return orig_read_bytes(self_path)

    monkeypatch.setattr(Path, "read_bytes", _spy_read_bytes)
    indexer.scan(only=["evolving_def.py"], embed=False)
    monkeypatch.undo()

    assert "untouched_caller.py" in read_paths
    assert "untouched_unrelated.py" not in read_paths

    # 2. Absolute focus_paths (including Windows backslashes) are relativized against indexer.root
    abs_focus = str(root / "untouched_caller.py").replace("/", "\\")
    entries = service.project_map(limit=10, focus_paths=[abs_focus])
    top_paths = [str(item["path"]) for item in entries[:2]]
    assert set(top_paths) == {"untouched_caller.py", "evolving_def.py"}

    # 3. personalized_pagerank ignores NaN / infinite / non-positive edge weights without producing NaN scores
    pr_scores = personalized_pagerank(
        ["a.py", "b.py"],
        [
            ("a.py", "b.py", float("nan"), ()),
            ("a.py", "b.py", float("inf"), ()),
            ("a.py", "b.py", 2.0, ("newly_added_symbol_xyz",)),
        ],
    )
    assert all(score > 0.0 and score == score for score in pr_scores.values())
    assert pr_scores["b.py"] > pr_scores["a.py"]

    # 4. CodeSearchTools.project_map validates limit/max_tokens and enforces floor of 100 on max_tokens
    workspace = ConversationWorkspace.at_root(root)
    toolset = AgentToolset(workspace, retrieval=service)
    res_floor = toolset.project_map(limit=5, max_tokens=10)
    assert res_floor["payload"]["max_tokens"] == 100

    for bad_kwargs in ({"limit": "not-an-int"}, {"max_tokens": "bad"}, {"limit": True}, {"max_tokens": False}):
        try:
            toolset.project_map(**bad_kwargs)  # type: ignore[arg-type]
        except AgentToolError:
            pass
        else:
            raise AssertionError(f"Expected AgentToolError for {bad_kwargs}")

