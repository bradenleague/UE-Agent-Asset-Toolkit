"""
Hybrid Retriever - Combined exact + semantic search with graph expansion.

Implements the retrieval strategy:
1. Query classification (exact vs semantic)
2. Hybrid search (FTS + vector)
3. Result fusion and deduplication
4. Graph expansion (forward/reverse refs)
5. Context bundle assembly
"""

import re
from typing import Callable, Optional
from dataclasses import dataclass

from .schemas import DocChunk, SearchResult, ReferenceGraph
from .store import KnowledgeStore


@dataclass
class ContextBundle:
    """Bundle of context for agent consumption."""

    query: str
    results: list[SearchResult]
    expanded_refs: Optional[ReferenceGraph]
    total_docs: int
    token_estimate: int

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "expanded_refs": self.expanded_refs.to_dict() if self.expanded_refs else None,
            "total_docs": self.total_docs,
            "token_estimate": self.token_estimate,
        }


class HybridRetriever:
    """
    Hybrid search combining exact and semantic retrieval.

    Strategy:
    - If query looks like a path/symbol, prefer exact search
    - Otherwise, use semantic search
    - Optionally expand results via reference graph
    """

    # Patterns that indicate exact search should be preferred
    EXACT_PATTERNS = [
        r"^/Game/",  # Asset paths
        r"^/Script/",  # Script paths
        r"^WBP_",  # Widget Blueprint prefix
        r"^BP_",  # Blueprint prefix
        r"^M_",  # Material prefix
        r"^MI_",  # Material Instance prefix
        r"^DT_",  # DataTable prefix
        r"^T_",  # Texture prefix
        r"\.uasset$",  # Asset extension
        r"\.cpp$",  # C++ files
        r"\.h$",  # Header files
        r"::",  # Scope resolution (C++/BP function)
        r"^U[A-Z]",  # UClass names
        r"^A[A-Z]",  # AActor names
        r"^F[A-Z]",  # FStruct names
        r"^E[A-Z]",  # EEnum names
        r"UCLASS",  # UCLASS macro search
        r"UFUNCTION",  # UFUNCTION macro search
        r"UPROPERTY",  # UPROPERTY macro search
        r"USTRUCT",  # USTRUCT macro search
        r"UENUM",  # UENUM macro search
        r"^Source/",  # Source path prefix
        r"^Plugins/",  # Plugins path prefix
        r"BlueprintCallable",  # Common specifier
        r"BlueprintReadWrite",  # Common specifier
        r"EditAnywhere",  # Common specifier
    ]

    def __init__(
        self,
        store: KnowledgeStore,
        embed_fn: Callable[[str], list[float]] = None,
    ):
        """
        Initialize the retriever.

        Args:
            store: Knowledge store to search
            embed_fn: Function to generate query embeddings
        """
        self.store = store
        self.embed_fn = embed_fn

    def retrieve(
        self,
        query: str,
        filters: dict = None,
        k: int = 10,
        expand_refs: bool = False,
        ref_direction: str = "both",
        ref_depth: int = 1,
        max_ref_nodes: int = 20,
    ) -> ContextBundle:
        """
        Retrieve relevant documents using hybrid search.

        Args:
            query: Search query
            filters: Optional filters {type, path_prefix, module, asset_type}
            k: Number of results to return
            expand_refs: Whether to expand reference graph
            ref_direction: "forward", "reverse", or "both"
            ref_depth: Depth for reference expansion
            max_ref_nodes: Maximum nodes in reference expansion

        Returns:
            ContextBundle with search results and optional ref graph
        """
        # Classify query
        query_type = self._classify_query(query)

        results = []

        if query_type == "exact":
            # Exact search first, then semantic fallback
            results = self.search_exact(query, filters, k)
            if len(results) < k // 2 and self.embed_fn:
                semantic_results = self.search_semantic(query, filters, k - len(results))
                results = self._merge_results(results, semantic_results)

        elif query_type == "semantic":
            # Semantic search first, supplement with exact
            if self.embed_fn:
                results = self.search_semantic(query, filters, k)
            exact_results = self.search_exact(query, filters, k // 2)
            results = self._merge_results(results, exact_results)

        else:  # hybrid
            # Run both in parallel conceptually, merge results
            exact_results = self.search_exact(query, filters, k)
            semantic_results = []
            if self.embed_fn:
                semantic_results = self.search_semantic(query, filters, k)
            results = self._merge_results(exact_results, semantic_results)

        # Limit to k results
        results = results[:k]

        # Expand references if requested
        expanded = None
        if expand_refs and results:
            # Use top result as seed
            seed_id = results[0].doc_id
            expanded = self.store.expand_refs(
                doc_id=seed_id,
                direction=ref_direction,
                depth=ref_depth,
                max_nodes=max_ref_nodes,
            )

        # Estimate tokens
        token_estimate = self._estimate_tokens(results, expanded)

        return ContextBundle(
            query=query,
            results=results,
            expanded_refs=expanded,
            total_docs=len(results),
            token_estimate=token_estimate,
        )

    def search_exact(
        self,
        query: str,
        filters: dict = None,
        k: int = 10,
    ) -> list[SearchResult]:
        """
        Exact/keyword search using FTS5.

        Args:
            query: Search query
            filters: Optional filters
            k: Maximum results

        Returns:
            List of search results
        """
        # Clean query for FTS5
        fts_query = self._prepare_fts_query(query)
        return self.store.search_fts(fts_query, filters, k)

    def search_semantic(
        self,
        query: str,
        filters: dict = None,
        k: int = 10,
        min_score: float = 0.3,
    ) -> list[SearchResult]:
        """
        Semantic search using embeddings.

        Args:
            query: Search query
            filters: Optional filters
            k: Maximum results
            min_score: Minimum similarity score

        Returns:
            List of search results
        """
        if not self.embed_fn:
            return []

        try:
            query_embedding = self.embed_fn(query)
            return self.store.search_vector(query_embedding, filters, k, min_score)
        except Exception:
            return []

    def get_docs(self, doc_ids: list[str]) -> list[DocChunk]:
        """
        Retrieve full documents by ID.

        Args:
            doc_ids: List of document IDs

        Returns:
            List of documents
        """
        return self.store.get_docs(doc_ids)

    def expand_references(
        self,
        doc_id: str,
        direction: str = "both",
        depth: int = 1,
        max_nodes: int = 50,
        type_filters: list[str] = None,
    ) -> ReferenceGraph:
        """
        Expand reference graph from a document.

        Args:
            doc_id: Starting document ID
            direction: "forward", "reverse", or "both"
            depth: Maximum traversal depth
            max_nodes: Maximum nodes to return
            type_filters: Only include nodes of these types

        Returns:
            ReferenceGraph with connected documents
        """
        return self.store.expand_refs(doc_id, direction, depth, max_nodes, type_filters)

    def _classify_query(self, query: str) -> str:
        """
        Classify query to determine search strategy.

        Returns:
            "exact", "semantic", or "hybrid"
        """
        query_stripped = query.strip()

        # Check for exact patterns
        for pattern in self.EXACT_PATTERNS:
            if re.search(pattern, query_stripped, re.IGNORECASE):
                return "exact"

        # Short queries (likely symbol names)
        words = query_stripped.split()
        if len(words) <= 2 and not any(w in query_stripped.lower() for w in ["how", "what", "why", "where", "when", "which", "explain", "describe"]):
            return "hybrid"

        # Natural language queries
        if any(w in query_stripped.lower() for w in ["how", "what", "why", "where", "when", "which", "explain", "describe", "find", "show", "list"]):
            return "semantic"

        return "hybrid"

    def _prepare_fts_query(self, query: str) -> str:
        """
        Prepare query for FTS5 search.

        Converts natural language to FTS5 query syntax.
        """
        # Remove special characters that FTS5 uses
        cleaned = re.sub(r'["\'\(\)\[\]\{\}]', '', query)

        # Split into words
        words = cleaned.split()

        if not words:
            return query

        # If it looks like a path, search for the path
        if query.startswith("/Game/") or query.startswith("/Script/"):
            return f'"{query}"'

        # Build FTS5 query
        # Use OR for word matching, prefix matching for partial words
        fts_terms = []
        for word in words:
            word = word.strip()
            if len(word) >= 2:
                # Add both exact and prefix match
                fts_terms.append(f"{word}*")

        return " OR ".join(fts_terms)

    def _merge_results(
        self,
        primary: list[SearchResult],
        secondary: list[SearchResult],
    ) -> list[SearchResult]:
        """
        Merge and deduplicate results from multiple sources.

        Primary results are preferred, secondary fills gaps.
        """
        seen = set()
        merged = []

        # Add primary results first
        for r in primary:
            if r.doc_id not in seen:
                seen.add(r.doc_id)
                merged.append(r)

        # Add secondary results
        for r in secondary:
            if r.doc_id not in seen:
                seen.add(r.doc_id)
                merged.append(r)

        return merged

    def _estimate_tokens(
        self,
        results: list[SearchResult],
        expanded: Optional[ReferenceGraph],
    ) -> int:
        """
        Estimate token count for context bundle.

        Rough estimate: 1 token ≈ 4 characters
        """
        total_chars = 0

        for r in results:
            if r.doc:
                total_chars += len(r.doc.text)
                total_chars += len(str(r.doc.metadata))

        if expanded:
            for doc in expanded.nodes.values():
                total_chars += len(doc.text)

        return total_chars // 4
