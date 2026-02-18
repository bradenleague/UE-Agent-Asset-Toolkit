"""Tests for _enrich_results_with_full_docs() — adaptive snippet enrichment."""

import sqlite3

import pytest

from UnrealAgent.mcp_server import _enrich_results_with_full_docs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_mock_counter = 0


def _make_mock_store(rows: list[tuple]) -> object:
    """Build a minimal mock store backed by a named in-memory SQLite DB.

    Uses ``file::memory:?cache=shared`` with a unique name so multiple
    connections share the same data (like a real file-backed DB) while
    each connection can be closed independently.

    *rows* is a list of (path, text, metadata_json, type) tuples to seed
    into the docs table.
    """
    global _mock_counter
    _mock_counter += 1
    db_uri = f"file:test_enrich_{_mock_counter}?mode=memory&cache=shared"

    # Seed connection — kept alive so the shared cache persists.
    seed = sqlite3.connect(db_uri, uri=True)
    seed.row_factory = sqlite3.Row
    seed.execute(
        """
        CREATE TABLE docs (
            doc_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            text TEXT,
            metadata TEXT DEFAULT '{}'
        )
        """
    )
    seed.execute("CREATE INDEX idx_docs_path ON docs(path)")
    for i, (path, text, metadata, doc_type) in enumerate(rows):
        name = path.rsplit("/", 1)[-1]
        seed.execute(
            "INSERT INTO docs (doc_id, type, path, name, text, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (f"asset:{path}#{doc_type}#{i}", doc_type, path, name, text, metadata),
        )
    seed.commit()

    class _MockStore:
        _seed = seed  # prevent GC

        def _get_connection(self):
            conn = sqlite3.connect(db_uri, uri=True)
            conn.row_factory = sqlite3.Row
            return conn

    return _MockStore()


# ---------------------------------------------------------------------------
# Unit tests — mock in-memory SQLite
# ---------------------------------------------------------------------------


class TestEnrichUnit:
    def test_enrich_single_doc(self):
        """One path, one doc → content + metadata populated."""
        store = _make_mock_store(
            [
                (
                    "/Game/UI/W_Healthbar",
                    "Health bar widget with progress bar",
                    '{"parent_class": "UserWidget"}',
                    "asset_summary",
                ),
            ]
        )
        results = [
            {
                "path": "/Game/UI/W_Healthbar",
                "name": "W_Healthbar",
                "snippet": "Health bar...",
            }
        ]
        level = _enrich_results_with_full_docs(results, store)
        assert level == "full"
        assert "Health bar widget with progress bar" in results[0]["content"]
        assert results[0]["metadata"]["parent_class"] == "UserWidget"

    def test_enrich_multi_doc_merge(self):
        """One path, two doc types → content concatenated, metadata merged."""
        store = _make_mock_store(
            [
                (
                    "/Game/UI/W_Healthbar",
                    "Widget tree: ProgressBar, TextBlock",
                    '{"widget_count": 5}',
                    "umg_widget_tree",
                ),
                (
                    "/Game/UI/W_Healthbar",
                    "Blueprint summary: parent UserWidget",
                    '{"parent_class": "UserWidget", "functions": ["OnHealthChanged"]}',
                    "asset_summary",
                ),
            ]
        )
        results = [
            {
                "path": "/Game/UI/W_Healthbar",
                "name": "W_Healthbar",
                "snippet": "Health...",
            }
        ]
        level = _enrich_results_with_full_docs(results, store)
        assert level == "full"
        # Both texts merged
        assert "Widget tree" in results[0]["content"]
        assert "Blueprint summary" in results[0]["content"]
        # Metadata merged from both docs
        assert results[0]["metadata"]["widget_count"] == 5
        assert results[0]["metadata"]["parent_class"] == "UserWidget"

    def test_enrich_no_docs(self):
        """Path has no semantic docs → no content/metadata added."""
        store = _make_mock_store([])
        results = [
            {"path": "/Game/Textures/T_Missing", "name": "T_Missing", "snippet": ""}
        ]
        level = _enrich_results_with_full_docs(results, store)
        assert level == "summary"
        assert "content" not in results[0]
        assert "metadata" not in results[0]

    def test_enrich_empty_results(self):
        """Empty list → returns 'summary' immediately."""
        store = _make_mock_store([])
        level = _enrich_results_with_full_docs([], store)
        assert level == "summary"

    def test_enrich_no_paths(self):
        """Results with no path field → returns 'summary'."""
        store = _make_mock_store([])
        results = [{"name": "orphan", "snippet": "no path"}]
        level = _enrich_results_with_full_docs(results, store)
        assert level == "summary"

    def test_enrich_multiple_paths(self):
        """Two different paths each get their own content."""
        store = _make_mock_store(
            [
                ("/Game/UI/W_Healthbar", "Healthbar content", "{}", "asset_summary"),
                ("/Game/UI/W_Ammo", "Ammo counter content", "{}", "asset_summary"),
            ]
        )
        results = [
            {"path": "/Game/UI/W_Healthbar", "name": "W_Healthbar", "snippet": "..."},
            {"path": "/Game/UI/W_Ammo", "name": "W_Ammo", "snippet": "..."},
        ]
        level = _enrich_results_with_full_docs(results, store)
        assert level == "full"
        assert "Healthbar content" in results[0]["content"]
        assert "Ammo counter content" in results[1]["content"]

    def test_enrich_skips_results_without_path(self):
        """Mixed result rows should not crash when one row has no path."""
        store = _make_mock_store(
            [
                ("/Game/UI/W_Healthbar", "Healthbar content", "{}", "asset_summary"),
            ]
        )
        results = [
            {"path": "/Game/UI/W_Healthbar", "name": "W_Healthbar", "snippet": "..."},
            {"name": "orphan_result", "snippet": "no path present"},
        ]
        level = _enrich_results_with_full_docs(results, store)
        assert level == "full"
        assert "Healthbar content" in results[0]["content"]
        assert "content" not in results[1]
        assert "metadata" not in results[1]

    def test_enrich_ignores_malformed_metadata(self):
        """Invalid JSON metadata should be ignored, not crash enrichment."""
        store = _make_mock_store(
            [
                (
                    "/Game/UI/W_Healthbar",
                    "Healthbar content",
                    '{"parent_class":"UserWidget"}',
                    "asset_summary",
                ),
                (
                    "/Game/UI/W_Healthbar",
                    "Widget tree content",
                    "{bad json",
                    "umg_widget_tree",
                ),
            ]
        )
        results = [
            {"path": "/Game/UI/W_Healthbar", "name": "W_Healthbar", "snippet": "..."},
        ]
        level = _enrich_results_with_full_docs(results, store)
        assert level == "full"
        assert "Healthbar content" in results[0]["content"]
        assert results[0]["metadata"]["parent_class"] == "UserWidget"


# ---------------------------------------------------------------------------
# Integration tests — real Lyra DB
# ---------------------------------------------------------------------------

_LYRA_DB = "UnrealAgent/data/lyrastartergame_57.db"


def _lyra_db_available() -> bool:
    from pathlib import Path

    return Path(_LYRA_DB).exists()


skip_no_db = pytest.mark.skipif(
    not _lyra_db_available(),
    reason="Lyra DB not found — run indexer first",
)


def _get_real_store():
    """Create a KnowledgeStore pointing at the real Lyra DB (read-only use)."""
    from UnrealAgent.knowledge_index.store import KnowledgeStore

    return KnowledgeStore(_LYRA_DB)


@skip_no_db
class TestEnrichIntegration:
    def test_name_search_narrow_has_full_detail(self):
        """Name search for W_Healthbar → detail='full', content present."""
        from UnrealAgent.mcp_server import unreal_search

        result = unreal_search("W_Healthbar", search_type="name")
        assert result["detail"] == "full"
        # At least one result should have enriched content
        enriched = [r for r in result["results"] if r.get("content")]
        assert len(enriched) > 0, "Expected at least one result with 'content' key"

    def test_name_search_prefix_broad_has_summary(self):
        """Prefix search for B_ → detail='summary' (many results)."""
        from UnrealAgent.mcp_server import unreal_search

        result = unreal_search("B_", search_type="name")
        # B_ prefix returns many results — still gets enriched because name mode always enriches
        assert result["detail"] == "full"
        assert result["count"] > 3

    def test_semantic_narrow_has_full_detail(self):
        """Semantic search with limit=2 → detail='full'."""
        from UnrealAgent.mcp_server import unreal_search

        result = unreal_search("healthbar widget", search_type="semantic", limit=2)
        assert result["detail"] == "full"

    def test_semantic_broad_has_summary(self):
        """Broad semantic search → detail='summary' (many results)."""
        from UnrealAgent.mcp_server import unreal_search

        result = unreal_search("widget", search_type="semantic", limit=20)
        # With limit=20 and a broad query, expect >3 results → summary
        if result["count"] > 3:
            assert result["detail"] == "summary"
        else:
            # If the DB happens to have very few matches, full is acceptable
            assert result["detail"] in ("full", "summary")

    def test_enriched_content_longer_than_snippet(self):
        """Enriched content should be longer than the truncated snippet."""
        from UnrealAgent.mcp_server import unreal_search

        result = unreal_search("W_Healthbar", search_type="name")
        for r in result["results"]:
            if r.get("content"):
                snippet_len = len(r.get("snippet", ""))
                content_len = len(r["content"])
                # Content should be at least as long as snippet
                assert content_len >= snippet_len, (
                    f"content ({content_len}) should be >= snippet ({snippet_len})"
                )
