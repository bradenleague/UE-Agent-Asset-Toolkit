# Knowledge Index for Unreal Projects
# Provides semantic search + reference graph + cross-linked documentation

from pathlib import Path

from .schemas import (
    DocChunk,
    AssetSummary,
    WidgetTreeDoc,
    BlueprintGraphDoc,
    MaterialParamsDoc,
    MaterialFunctionDoc,
    SourceFileDoc,
    CppClassDoc,
    CppFunctionDoc,
    CppPropertyDoc,
    SearchResult,
    ReferenceGraph,
    IndexStatus,
)
from .store import KnowledgeStore
from .indexer import AssetIndexer
from .source_indexer import SourceIndexer
from .cpp_parser import CppParser, CppFileInfo, UClassInfo, UFunctionInfo, UPropertyInfo
from .retriever import HybridRetriever


def ensure_index_exists(
    content_path: Path,
    db_path: Path = None,
    project_path: Path = None,
    index_source: bool = False,
    verbose: bool = True,
    progress_callback=None,
    on_start=None,
    on_complete=None,
) -> KnowledgeStore:
    """Create or open the semantic index, building if empty.

    This is the main entry point for automatic index setup. Call this during
    agent startup to ensure the semantic index is available.

    Args:
        content_path: Path to the project's Content folder
        db_path: Path to store the database (default: data/knowledge_index.db)
        project_path: Path to .uproject file (required for source indexing)
        index_source: Whether to index C++ source files (Source/ and Plugins/)
        verbose: Print progress messages
        progress_callback: Optional callback(path, current, total) for progress updates
        on_start: Optional callback(total) called when indexing starts
        on_complete: Optional callback(stats) called when indexing completes

    Returns:
        KnowledgeStore instance ready for use
    """
    if db_path is None:
        db_path = Path(__file__).parent.parent / "data" / "knowledge_index.db"

    # Ensure data directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    store = KnowledgeStore(db_path)

    # Check if index needs building
    status = store.get_status()
    if status.total_docs == 0:
        if verbose:
            print("Building semantic index (first run)...")

        indexer = AssetIndexer(store, content_path)

        # Use custom callback or default
        if progress_callback:
            callback = progress_callback
        else:
            def callback(path, current, total):
                if verbose and current % 50 == 0:
                    print(f"  Indexed {current}/{total} assets...")

        # Notify start if callback provided
        if on_start:
            # Do a quick count first
            import glob
            pattern = str(content_path / "**" / "*.uasset")
            total = len(glob.glob(pattern, recursive=True))
            on_start(total)

        stats = indexer.index_folder("/Game", progress_callback=callback)

        # Notify completion
        if on_complete:
            on_complete(stats)
        elif verbose:
            print(f"Indexed {stats.get('indexed', 0)} documents ({stats.get('unchanged', 0)} unchanged)")
            if stats.get('errors', 0) > 0:
                print(f"  {stats['errors']} errors")

    # Index C++ source if requested
    if index_source and project_path:
        source_status = store.get_status()
        cpp_count = source_status.docs_by_type.get("source_file", 0)

        if cpp_count == 0:
            if verbose:
                print("Indexing C++ source files...")

            source_indexer = SourceIndexer(store, project_path)

            # Use custom callback or default
            if progress_callback:
                source_callback = progress_callback
            else:
                def source_callback(path, current, total):
                    if verbose and current % 20 == 0:
                        print(f"  Indexed {current}/{total} source files...")

            source_stats = source_indexer.index_all(progress_callback=source_callback)

            if verbose:
                print(f"Indexed {source_stats.get('indexed', 0)} source files ({source_stats.get('unchanged', 0)} unchanged)")
                if source_stats.get('errors', 0) > 0:
                    print(f"  {source_stats['errors']} errors")

    return store


__all__ = [
    # Asset schemas
    "DocChunk",
    "AssetSummary",
    "WidgetTreeDoc",
    "BlueprintGraphDoc",
    "MaterialParamsDoc",
    "MaterialFunctionDoc",
    # C++ source schemas
    "SourceFileDoc",
    "CppClassDoc",
    "CppFunctionDoc",
    "CppPropertyDoc",
    # Search results
    "SearchResult",
    "ReferenceGraph",
    "IndexStatus",
    # Core classes
    "KnowledgeStore",
    "AssetIndexer",
    "SourceIndexer",
    "HybridRetriever",
    # C++ parser
    "CppParser",
    "CppFileInfo",
    "UClassInfo",
    "UFunctionInfo",
    "UPropertyInfo",
    # Entry points
    "ensure_index_exists",
]
