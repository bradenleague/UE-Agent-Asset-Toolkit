"""Regression tests for explicit query routing between mcp_server and retriever."""

from unreal_agent import mcp_server
from unreal_agent.search import engine as search_engine
from unreal_agent.knowledge_index.retriever import HybridRetriever
from unreal_agent.knowledge_index.schemas import DocChunk, SearchResult


class _DummyStore:
    pass


class _DummyBundle:
    def __init__(self, results):
        self.results = results


def test_retriever_respects_explicit_semantic_hint(monkeypatch):
    retriever = HybridRetriever(_DummyStore(), embed_fn=lambda _: [0.1])
    calls = []

    def _fake_semantic(query, filters, k):
        calls.append("semantic")
        return [SearchResult(doc_id="semantic:1", score=0.9)]

    def _fake_exact(query, filters, k):
        calls.append("exact")
        return [SearchResult(doc_id="exact:1", score=0.8)]

    monkeypatch.setattr(retriever, "_classify_query", lambda _: "exact")
    monkeypatch.setattr(retriever, "search_semantic", _fake_semantic)
    monkeypatch.setattr(retriever, "search_exact", _fake_exact)

    bundle = retriever.retrieve("player damage", k=4, query_type="semantic")

    assert calls[:2] == ["semantic", "exact"]
    assert any(r.doc_id == "semantic:1" for r in bundle.results)


def test_mcp_semantic_mode_passes_query_type_hint(monkeypatch):
    class _DummyRetriever:
        def __init__(self):
            self.kwargs = None

        def retrieve(self, **kwargs):
            self.kwargs = kwargs
            doc = DocChunk(
                doc_id="asset:/Game/Test/BP_Player",
                type="asset_summary",
                path="/Game/Test/BP_Player",
                name="BP_Player",
                text="Player blueprint",
                asset_type="Blueprint",
            )
            return _DummyBundle([SearchResult(doc_id=doc.doc_id, score=0.9, doc=doc)])

    dummy_retriever = _DummyRetriever()

    monkeypatch.setattr(search_engine, "get_store", lambda: _DummyStore())
    monkeypatch.setattr(
        search_engine,
        "get_retriever_instance",
        lambda enable_embeddings=False: dummy_retriever,
    )
    monkeypatch.setattr(
        search_engine,
        "enrich_results_with_full_docs",
        lambda results, store: "summary",
    )

    result = mcp_server.unreal_search(
        query="player damage system", search_type="semantic", limit=5
    )

    assert result["search_type"] == "semantic"
    assert dummy_retriever.kwargs["query_type"] == "semantic"


def test_mcp_short_keyword_semantic_mode_uses_exact_query_type(monkeypatch):
    class _DummyRetriever:
        def __init__(self):
            self.kwargs = None

        def retrieve(self, **kwargs):
            self.kwargs = kwargs
            doc = DocChunk(
                doc_id="asset:/Game/Test/BP_Player",
                type="asset_summary",
                path="/Game/Test/BP_Player",
                name="BP_Player",
                text="Player blueprint",
                asset_type="Blueprint",
            )
            return _DummyBundle([SearchResult(doc_id=doc.doc_id, score=0.9, doc=doc)])

    dummy_retriever = _DummyRetriever()

    monkeypatch.setattr(search_engine, "get_store", lambda: _DummyStore())
    monkeypatch.setattr(
        search_engine,
        "get_retriever_instance",
        lambda enable_embeddings=False: dummy_retriever,
    )
    monkeypatch.setattr(
        search_engine,
        "enrich_results_with_full_docs",
        lambda results, store: "summary",
    )

    result = mcp_server.unreal_search(
        query="player damage", search_type="semantic", limit=5
    )

    assert result["search_type"] == "semantic"
    assert dummy_retriever.kwargs["query_type"] == "exact"


# --- _classify_query unit tests ---

_test_prefixes = search_engine._ENGINE_PREFIXES + ["LAS_", "GCN_"]


class TestClassifyQuery:
    def test_trailing_underscore_prefix_routes_to_name(self):
        assert search_engine._classify_query("LAS_", _test_prefixes) == "name"

    def test_engine_prefix_routes_to_name(self):
        assert search_engine._classify_query("BP_Player", _test_prefixes) == "name"

    def test_profile_prefix_routes_to_name(self):
        assert (
            search_engine._classify_query("LAS_StandardHUD", _test_prefixes) == "name"
        )

    def test_path_routes_to_name(self):
        assert (
            search_engine._classify_query("/Game/UI/Widget", _test_prefixes) == "name"
        )

    def test_script_path_does_not_route_to_name(self):
        result = search_engine._classify_query("/Script/Engine.Actor", _test_prefixes)
        assert result != "name"

    def test_where_used_routes_to_refs(self):
        assert (
            search_engine._classify_query("where is BP_Player used", _test_prefixes)
            == "refs"
        )

    def test_where_placed_routes_to_refs(self):
        assert (
            search_engine._classify_query("where is BP_Enemy placed", _test_prefixes)
            == "refs"
        )

    def test_inherits_routes_to_inherits(self):
        assert (
            search_engine._classify_query("classes extending Character", _test_prefixes)
            == "inherits"
        )

    def test_natural_language_routes_to_semantic(self):
        assert (
            search_engine._classify_query(
                "explain the inventory system", _test_prefixes
            )
            == "semantic"
        )

    def test_dotted_pascal_case_routes_to_tags(self):
        assert (
            search_engine._classify_query("Input.Movement", _test_prefixes)
            == "tags_candidate"
        )
