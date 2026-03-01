import os
import sys
import json
import logging
from pathlib import Path

from unreal_agent.core import get_project_db_path, get_active_project_name

logger = logging.getLogger("unreal-asset-tools")

# Global instances
_store = None
_profile = None
_retriever = None
_embedder_attempted = False
_embedder_error = None


def get_store():
    """Get or create the knowledge store for the active project."""
    global _store
    if _store is None:
        db_path = Path(get_project_db_path())
        if not db_path.exists():
            project = get_active_project_name() or "unknown"
            raise RuntimeError(
                f"Knowledge index not found for project '{project}' at {db_path}. "
                "Run 'python index.py' first."
            )
        from unreal_agent.knowledge_index import KnowledgeStore

        _store = KnowledgeStore(db_path)
    return _store


def get_profile():
    """Get or create the project profile for the active project."""
    global _profile
    if _profile is None:
        from unreal_agent.project_profile import load_profile

        _profile = load_profile()
    return _profile


def get_retriever_instance(enable_embeddings: bool = False):
    """Get or create the retriever; embeddings are optional and loaded lazily."""
    global _retriever, _embedder_attempted, _embedder_error
    if _retriever is None:
        store = get_store()
        from unreal_agent.knowledge_index import HybridRetriever

        # Start with FTS-only retriever so name/refs queries never depend on HF.
        _retriever = HybridRetriever(store, embed_fn=None)

    if enable_embeddings and _retriever.embed_fn is None and not _embedder_attempted:
        enable_embeddings_runtime = os.environ.get(
            "UNREAL_MCP_ENABLE_EMBEDDINGS", "1"
        ).lower() in {"1", "true", "yes", "on"}
        if not enable_embeddings_runtime:
            _embedder_attempted = True
            _embedder_error = (
                "embeddings disabled in MCP runtime "
                "(set UNREAL_MCP_ENABLE_EMBEDDINGS=1 to enable)"
            )
            return _retriever

        _embedder_attempted = True
        try:
            from unreal_agent.knowledge_index.indexer import (
                create_sentence_transformer_embedder,
            )

            _retriever.embed_fn = create_sentence_transformer_embedder(
                local_files_only=True
            )
            if _retriever.embed_fn is None:
                _embedder_error = "sentence-transformers not installed"
        except Exception as e:
            _embedder_error = str(e)
            _retriever.embed_fn = None
            print(
                f"Warning: Embeddings unavailable ({e}); using FTS-only search.",
                file=sys.stderr,
            )

    return _retriever


def get_embedder_error() -> str | None:
    return _embedder_error


def build_semantic_snippet(doc) -> str:
    """Build richer snippets for high-value docs."""
    metadata = doc.metadata if isinstance(doc.metadata, dict) else {}

    if doc.type == "bp_graph_summary":
        parts = [f"Blueprint function {doc.name} in {doc.path}"]
        flags = metadata.get("flags") or []
        calls = metadata.get("calls") or []
        control_flow = metadata.get("control_flow") or {}
        if flags:
            parts.append(f"Flags: {', '.join(flags[:4])}")
        if calls:
            parts.append(f"Calls: {', '.join(calls[:6])}")
        if control_flow.get("has_branches"):
            parts.append("Has conditional branches")
        return ". ".join(parts)[:260]

    if doc.type == "asset_summary" and (doc.asset_type or "").lower() in (
        "blueprint",
        "widgetblueprint",
    ):
        parts = [f"{doc.asset_type} {doc.name}"]
        parent = metadata.get("parent_class")
        if parent:
            parts.append(f"Parent: {parent}")
        functions = metadata.get("functions") or []
        events = metadata.get("events") or []
        variables = metadata.get("variables") or []
        if functions:
            parts.append(f"Functions: {', '.join(functions[:5])}")
        if events:
            parts.append(f"Events: {', '.join(events[:5])}")
        if variables:
            parts.append(f"Variables: {', '.join(variables[:5])}")
        return ". ".join(parts)[:260]

    text = doc.text or ""
    return text[:200]


def enrich_results_with_full_docs(results: list[dict], store) -> str:
    """Enrich narrow result sets with full doc content merged per asset path.

    Replaces truncated snippets with the complete semantic doc text and
    merged metadata so that callers rarely need a follow-up inspect_asset.

    Returns detail level: "full" or "summary".
    """
    if not results:
        return "summary"

    paths = [r["path"] for r in results if r.get("path")]
    if not paths:
        return "summary"

    conn = store._get_connection()
    try:
        unique_paths = list(dict.fromkeys(paths))
        placeholders = ",".join("?" * len(unique_paths))
        rows = conn.execute(
            f"SELECT path, text, metadata, type FROM docs WHERE path IN ({placeholders}) ORDER BY path, type",
            tuple(unique_paths),
        ).fetchall()

        # Group by path
        docs_by_path: dict[str, list] = {}
        for row in rows:
            docs_by_path.setdefault(row["path"], []).append(row)

        enriched_any = False
        for r in results:
            path = r.get("path")
            if not path:
                continue

            path_docs = docs_by_path.get(path, [])
            if not path_docs:
                continue

            # Merge text from all docs for this path
            texts = [row["text"] for row in path_docs if row["text"]]
            if texts:
                r["content"] = "\n\n".join(texts)

            # Merge metadata
            merged_meta = {}
            for row in path_docs:
                if not row["metadata"]:
                    continue
                try:
                    meta = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "Skipping malformed docs metadata for path '%s'", path
                    )
                    continue
                if isinstance(meta, dict):
                    merged_meta.update(meta)

            if merged_meta:
                r["metadata"] = merged_meta
            if texts or merged_meta:
                enriched_any = True
    finally:
        conn.close()

    return "full" if enriched_any else "summary"
