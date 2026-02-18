"""
Source Indexer - Indexes C++ source files into the knowledge store.

Handles:
- Source/ folder indexing (game code)
- Plugins/ folder indexing
- Incremental updates based on file fingerprints
- Document chunk generation from parsed C++ files
"""

import hashlib
from pathlib import Path
from typing import Callable

from UnrealAgent.pathutil import to_game_path_sep

from .schemas import (
    SourceFileDoc,
    CppClassDoc,
    CppFunctionDoc,
    CppPropertyDoc,
)
from .store import KnowledgeStore
from .cpp_parser import CppParser


class SourceIndexer:
    """
    Indexes C++ source files into the knowledge store.

    Creates document chunks for:
    - Source files (high-level summary)
    - UCLASS declarations
    - UFUNCTION declarations
    - UPROPERTY declarations
    """

    EXCLUDED_DIR_NAMES = {
        "intermediate",
        "binaries",
        "saved",
        ".git",
        ".vs",
        "deriveddatacache",
    }
    EXCLUDED_FILE_SUFFIXES = (
        ".gen.cpp",
        ".generated.h",
    )

    def __init__(
        self,
        store: KnowledgeStore,
        project_path: Path,
        embed_fn: Callable[[str], list[float]] = None,
        embed_model: str = None,
        embed_version: str = "1.0",
    ):
        """
        Initialize the source indexer.

        Args:
            store: Knowledge store to index into
            project_path: Path to .uproject file (or project root directory)
            embed_fn: Function to generate embeddings from text
            embed_model: Name of embedding model
            embed_version: Version of embedding model
        """
        self.store = store
        self.embed_fn = embed_fn
        self.embed_model = embed_model
        self.embed_version = embed_version
        self.parser = CppParser()
        self._purge_done = False

        # Determine project root
        if isinstance(project_path, str):
            project_path = Path(project_path)

        if project_path.suffix == ".uproject":
            self.project_root = project_path.parent
        else:
            self.project_root = project_path

    def index_source(
        self,
        progress_callback: Callable[[str, int, int], None] = None,
    ) -> dict:
        """
        Index the Source/ folder.

        Args:
            progress_callback: Called with (file_path, current, total)

        Returns:
            Dict with indexing statistics
        """
        purged = self._ensure_excluded_docs_purged()
        source_path = self.project_root / "Source"
        stats = self._index_folder(source_path, "game", progress_callback)
        stats["purged_docs"] = purged
        return stats

    def index_plugins(
        self,
        progress_callback: Callable[[str, int, int], None] = None,
    ) -> dict:
        """
        Index the Plugins/ folder.

        Args:
            progress_callback: Called with (file_path, current, total)

        Returns:
            Dict with indexing statistics
        """
        purged = self._ensure_excluded_docs_purged()
        plugins_path = self.project_root / "Plugins"
        stats = self._index_folder(plugins_path, "plugin", progress_callback)
        stats["purged_docs"] = purged
        return stats

    def index_all(
        self,
        progress_callback: Callable[[str, int, int], None] = None,
    ) -> dict:
        """
        Index both Source/ and Plugins/ folders.

        Args:
            progress_callback: Called with (file_path, current, total)

        Returns:
            Combined dict with indexing statistics
        """
        stats = {
            "total": 0,
            "indexed": 0,
            "unchanged": 0,
            "errors": 0,
            "by_type": {},
            "source_stats": None,
            "plugin_stats": None,
            "purged_docs": 0,
        }

        # Remove stale generated/Intermediate docs from older runs so they don't
        # keep polluting search quality and context budget.
        stats["purged_docs"] = self._ensure_excluded_docs_purged()

        # Index Source/
        source_stats = self.index_source(progress_callback)
        stats["source_stats"] = source_stats

        # Index Plugins/
        plugin_stats = self.index_plugins(progress_callback)
        stats["plugin_stats"] = plugin_stats

        # Combine stats
        for key in ["total", "indexed", "unchanged", "errors"]:
            stats[key] = source_stats.get(key, 0) + plugin_stats.get(key, 0)

        # Combine by_type
        for doc_type, count in source_stats.get("by_type", {}).items():
            stats["by_type"][doc_type] = stats["by_type"].get(doc_type, 0) + count
        for doc_type, count in plugin_stats.get("by_type", {}).items():
            stats["by_type"][doc_type] = stats["by_type"].get(doc_type, 0) + count

        return stats

    def _index_folder(
        self,
        folder: Path,
        module_prefix: str,
        progress_callback: Callable[[str, int, int], None] = None,
    ) -> dict:
        """
        Index all C++ files in a folder.

        Args:
            folder: Path to folder to index
            module_prefix: Module prefix for doc IDs ("game" or "plugin")
            progress_callback: Called with (file_path, current, total)

        Returns:
            Dict with indexing statistics
        """
        stats = {
            "total": 0,
            "indexed": 0,
            "unchanged": 0,
            "errors": 0,
            "by_type": {},
        }

        if not folder.exists():
            return stats

        # Find all C++ files, excluding generated/Intermediate paths.
        cpp_files = [
            p for p in folder.glob("**/*.cpp") if self._should_index_source_file(p)
        ]
        h_files = [
            p for p in folder.glob("**/*.h") if self._should_index_source_file(p)
        ]
        all_files = sorted(cpp_files + h_files)
        stats["total"] = len(all_files)

        for i, file_path in enumerate(all_files):
            try:
                if progress_callback:
                    rel_path = file_path.relative_to(self.project_root)
                    progress_callback(str(rel_path), i + 1, len(all_files))

                result = self._index_file(file_path, module_prefix)

                if result == "indexed":
                    stats["indexed"] += 1
                elif result == "unchanged":
                    stats["unchanged"] += 1
                else:
                    stats["errors"] += 1

            except Exception:
                stats["errors"] += 1

        return stats

    def _index_file(self, file_path: Path, module_prefix: str) -> str:
        """
        Index a single source file, creating multiple doc chunks.

        Args:
            file_path: Path to the C++ file
            module_prefix: Module prefix for categorization

        Returns:
            "indexed", "unchanged", or "error"
        """
        # Calculate file fingerprint for change detection
        try:
            content = file_path.read_bytes()
            fingerprint = hashlib.sha256(content).hexdigest()[:16]
        except Exception:
            return "error"

        # Get relative path for doc IDs
        try:
            rel_path = str(file_path.relative_to(self.project_root))
            rel_path = to_game_path_sep(rel_path)
        except ValueError:
            rel_path = str(file_path)

        # Check if file has changed
        existing_doc = self.store.get_doc(f"source:{rel_path}")
        if existing_doc and existing_doc.fingerprint == fingerprint:
            return "unchanged"

        # Parse the file
        info = self.parser.parse_file(file_path)
        if not info:
            return "error"

        # Create document chunks
        chunks = []

        # 1. File-level summary
        file_doc = SourceFileDoc(
            path=rel_path,
            name=file_path.name,
            line_count=info.line_count,
            includes=info.includes,
            class_count=len(info.classes),
            function_count=len(info.functions),
            property_count=len(info.properties),
            module=module_prefix,
        )
        file_doc.fingerprint = fingerprint
        chunks.append(file_doc)

        # 2. Class chunks (one per UCLASS/USTRUCT)
        # Also collect class data for cpp_class_index registration
        cpp_class_data = []
        for cls in info.classes:
            # Collect methods and properties for this class
            class_methods = [f.name for f in info.functions if f.class_name == cls.name]
            class_properties = [
                p.name for p in info.properties if p.class_name == cls.name
            ]

            class_doc = CppClassDoc(
                path=rel_path,
                class_name=cls.name,
                parent_class=cls.parent,
                specifiers=cls.specifiers,
                methods=class_methods,
                properties=class_properties,
                is_uclass=True,  # We only extract UCLASS/USTRUCT
                line_number=cls.line_number,
                module=module_prefix,
            )
            chunks.append(class_doc)

            # Register class name for cross-referencing with Blueprints
            cpp_class_data.append((cls.name, class_doc.doc_id, rel_path))

        # 3. Function chunks (one per UFUNCTION)
        for func in info.functions:
            func_doc = CppFunctionDoc(
                path=rel_path,
                function_name=func.name,
                return_type=func.return_type,
                parameters=func.parameters,
                specifiers=func.specifiers,
                class_name=func.class_name,
                is_ufunction=True,  # We only extract UFUNCTION
                line_number=func.line_number,
                module=module_prefix,
            )
            chunks.append(func_doc)

        # 4. Property chunks (one per UPROPERTY)
        for prop in info.properties:
            prop_doc = CppPropertyDoc(
                path=rel_path,
                property_name=prop.name,
                property_type=prop.type,
                specifiers=prop.specifiers,
                default_value=prop.default_value,
                class_name=prop.class_name,
                line_number=prop.line_number,
                module=module_prefix,
            )
            chunks.append(prop_doc)

        # Store all chunks
        any_changed = False
        for chunk in chunks:
            # Generate embedding if function provided
            embedding = None
            if self.embed_fn:
                try:
                    embedding = self.embed_fn(chunk.text)
                    chunk.embed_model = self.embed_model
                    chunk.embed_version = self.embed_version
                except Exception:
                    pass

            changed = self.store.upsert_doc(chunk, embedding)
            if changed:
                any_changed = True

        # Register C++ class names for cross-referencing with Blueprints
        if cpp_class_data:
            self.store.upsert_cpp_classes_batch(cpp_class_data)

        return "indexed" if any_changed else "unchanged"

    def _should_index_source_file(self, file_path: Path) -> bool:
        """Return True when file is authored source worth indexing."""
        parts = {part.lower() for part in file_path.parts}
        if any(name in parts for name in self.EXCLUDED_DIR_NAMES):
            return False

        name = file_path.name.lower()
        if name.endswith(self.EXCLUDED_FILE_SUFFIXES):
            return False

        return True

    def _ensure_excluded_docs_purged(self) -> int:
        """Purge excluded docs at most once per indexer instance."""
        if self._purge_done:
            return 0
        self._purge_done = True
        return self._purge_excluded_docs()

    def _purge_excluded_docs(self) -> int:
        """Delete previously indexed generated/Intermediate C++ docs."""
        conn = self.store._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT doc_id
                FROM docs
                WHERE type IN ('source_file', 'cpp_class', 'cpp_func', 'cpp_property')
                  AND (
                      path LIKE '%/Intermediate/%'
                      OR path LIKE '%/Binaries/%'
                      OR path LIKE '%/Saved/%'
                      OR path LIKE '%/.vs/%'
                      OR path LIKE '%/DerivedDataCache/%'
                      OR name LIKE '%.gen.cpp'
                      OR name LIKE '%.generated.h'
                  )
                """
            ).fetchall()

            if not rows:
                return 0

            doc_ids = [row["doc_id"] for row in rows]
            chunk_size = 500
            for i in range(0, len(doc_ids), chunk_size):
                chunk = doc_ids[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))

                # Remove edges that reference removed docs.
                conn.execute(
                    f"DELETE FROM edges WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
                    tuple(chunk + chunk),
                )
                if self.store.use_vector_search:
                    conn.execute(
                        f"DELETE FROM docs_embeddings WHERE doc_id IN ({placeholders})",
                        tuple(chunk),
                    )
                conn.execute(
                    f"DELETE FROM docs WHERE doc_id IN ({placeholders})",
                    tuple(chunk),
                )

            conn.execute(
                "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('fts_dirty', '1')"
            )
            conn.commit()
            return len(doc_ids)
        finally:
            conn.close()

    def get_source_status(self) -> dict:
        """
        Get status of indexed C++ source files.

        Returns:
            Dict with counts by document type
        """
        status = self.store.get_status()

        cpp_types = ["source_file", "cpp_class", "cpp_func", "cpp_property"]
        cpp_counts = {t: status.docs_by_type.get(t, 0) for t in cpp_types}

        return {
            "total_cpp_docs": sum(cpp_counts.values()),
            "by_type": cpp_counts,
        }
