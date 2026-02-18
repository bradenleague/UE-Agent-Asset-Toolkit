"""
Knowledge Store - SQLite-based storage for the semantic index.

Provides:
- Document metadata storage with SQLite
- Full-text search with FTS5
- Vector similarity search (numpy-based cosine similarity)
- Reference graph with edges table
"""

import sqlite3
import json
import os
import time
import threading
from datetime import datetime
from typing import Optional
from pathlib import Path

from .schemas import DocChunk, SearchResult, ReferenceGraph, IndexStatus

# Optional: numpy for vector similarity search
try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class KnowledgeStore:
    """
    SQLite-based knowledge store with full-text search and vector similarity.

    Schema:
    - docs: Document metadata and text
    - docs_fts: Full-text search virtual table
    - docs_embeddings: Vector embeddings (if enabled)
    - edges: Reference graph edges
    """

    SCHEMA_VERSION = 1
    DEFAULT_EMBEDDING_DIM = 1536  # OpenAI ada-002

    def __init__(
        self,
        db_path: str | Path,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        use_vector_search: bool = True,
    ):
        """
        Initialize the knowledge store.

        Args:
            db_path: Path to SQLite database file
            embedding_dim: Dimension of embedding vectors
            use_vector_search: Whether to enable vector similarity search
        """
        self.db_path = Path(db_path)
        self.embedding_dim = embedding_dim
        self.use_vector_search = use_vector_search and HAS_NUMPY
        self._write_lock = threading.RLock()
        self._write_conn: Optional[sqlite3.Connection] = None
        self._write_conn_pid = os.getpid()

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

    def _configure_connection(self, conn: sqlite3.Connection):
        """Apply consistent sqlite settings to a connection."""
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")

    def _get_connection(self) -> sqlite3.Connection:
        """Get a short-lived DB connection (primarily for reads)."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        self._configure_connection(conn)
        return conn

    def _get_write_connection(self) -> sqlite3.Connection:
        """Get a reusable writer connection for high-volume write workloads."""
        current_pid = os.getpid()
        if self._write_conn is not None and self._write_conn_pid != current_pid:
            self._reset_write_connection()

        if self._write_conn is None:
            self._write_conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                check_same_thread=False,
            )
            self._configure_connection(self._write_conn)
            self._write_conn_pid = current_pid

        return self._write_conn

    def _reset_write_connection(self):
        """Close and clear cached writer connection."""
        if self._write_conn is not None:
            try:
                self._write_conn.close()
            except Exception:
                pass
            self._write_conn = None

    def close(self):
        """Close cached resources explicitly."""
        with self._write_lock:
            self._reset_write_connection()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        try:
            # Main documents table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS docs (
                    doc_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    module TEXT,
                    asset_type TEXT,
                    text TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    references_out TEXT DEFAULT '[]',
                    fingerprint TEXT NOT NULL,
                    schema_version INTEGER DEFAULT 1,
                    embed_model TEXT,
                    embed_version TEXT,
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # FTS5 virtual table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                    doc_id,
                    name,
                    path,
                    text,
                    content='docs',
                    content_rowid='rowid'
                )
            """)

            # NOTE: We intentionally avoid automatic FTS triggers here.
            # On Linux we observed intermittent SQLITE_CANTOPEN errors during
            # docs writes when triggers were active. We mark FTS "dirty" on
            # writes and rebuild on demand instead.
            conn.execute("DROP TRIGGER IF EXISTS docs_ai")
            conn.execute("DROP TRIGGER IF EXISTS docs_ad")
            conn.execute("DROP TRIGGER IF EXISTS docs_au")

            # Embeddings table (separate for flexibility)
            if self.use_vector_search:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS docs_embeddings (
                        doc_id TEXT PRIMARY KEY,
                        embedding BLOB NOT NULL,
                        embed_model TEXT,
                        embed_version TEXT,
                        FOREIGN KEY (doc_id) REFERENCES docs(doc_id) ON DELETE CASCADE
                    )
                """)

            # Reference graph edges
            conn.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    metadata TEXT,
                    PRIMARY KEY (from_id, to_id, edge_type)
                )
            """)

            # Lightweight assets table (path + refs only, no embeddings)
            # Used for low-value asset types: textures, meshes, animations, OFPA
            # Enables "where is X used?" queries without full semantic indexing
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lightweight_assets (
                    path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    "references" TEXT DEFAULT '[]',
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Normalized lightweight references for fast "where is X used?" lookups.
            # Keeps one row per (asset_path -> referenced_path) edge.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lightweight_refs (
                    asset_path TEXT NOT NULL,
                    ref_path TEXT NOT NULL,
                    PRIMARY KEY (asset_path, ref_path),
                    FOREIGN KEY (asset_path) REFERENCES lightweight_assets(path) ON DELETE CASCADE
                )
            """)

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_type ON docs(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_path ON docs(path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_name ON docs(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_module ON docs(module)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_docs_fingerprint ON docs(fingerprint)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lightweight_type ON lightweight_assets(asset_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lightweight_name ON lightweight_assets(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lightweight_refs_ref ON lightweight_refs(ref_path)"
            )

            # Metadata table for index-level info
            conn.execute("""
                CREATE TABLE IF NOT EXISTS index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO index_meta (key, value) VALUES ('fts_dirty', '0')"
            )

            # File metadata for incremental indexing
            # Tracks file mtime/size to skip unchanged files BEFORE parsing
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_meta (
                    path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    asset_type TEXT,
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_meta_type ON file_meta(asset_type)"
            )

            # GameplayTag index for "what assets use tag X?" queries
            conn.execute("""
                CREATE TABLE IF NOT EXISTS asset_tags (
                    path TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (path, tag)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag)"
            )

            # C++ class name index for cross-referencing with Blueprints
            # Maps simple class name (e.g., "UCharacterMovementComponent") to doc_id
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cpp_class_index (
                    class_name TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    source_path TEXT
                )
            """)

            # Backfill normalized lightweight refs on first run after migration.
            existing_ref_rows = conn.execute(
                "SELECT COUNT(*) FROM lightweight_refs"
            ).fetchone()[0]
            if existing_ref_rows == 0:
                rows = conn.execute(
                    'SELECT path, "references" FROM lightweight_assets WHERE "references" IS NOT NULL AND "references" != \'[]\''
                ).fetchall()
                ref_rows = []
                for row in rows:
                    path = row["path"]
                    try:
                        refs = json.loads(row["references"] or "[]")
                    except Exception:
                        refs = []
                    if not isinstance(refs, list):
                        continue
                    for ref in refs:
                        if isinstance(ref, str) and ref:
                            ref_rows.append((path, ref))

                if ref_rows:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO lightweight_refs (asset_path, ref_path)
                        VALUES (?, ?)
                    """,
                        ref_rows,
                    )

            conn.commit()
        finally:
            conn.close()

    def upsert_doc(
        self, doc: DocChunk, embedding: list[float] = None, force: bool = False
    ) -> bool:
        """
        Insert or update a document.

        Args:
            doc: Document chunk to store
            embedding: Optional embedding vector
            force: If True, skip fingerprint check and always update

        Returns:
            True if document was inserted/updated, False if unchanged
        """
        attempts = 0
        while attempts < 3:
            with self._write_lock:
                conn = self._get_write_connection()
                try:
                    # Check if document exists and has same fingerprint (unless force=True)
                    if not force:
                        existing = conn.execute(
                            "SELECT fingerprint FROM docs WHERE doc_id = ?",
                            (doc.doc_id,),
                        ).fetchone()

                        if existing and existing["fingerprint"] == doc.fingerprint:
                            return False  # No change

                    # Upsert document
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO docs
                        (doc_id, type, path, name, module, asset_type, text, metadata,
                         references_out, fingerprint, schema_version, embed_model, embed_version, indexed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            doc.doc_id,
                            doc.type,
                            doc.path,
                            doc.name,
                            doc.module,
                            doc.asset_type,
                            doc.text,
                            json.dumps(doc.metadata) if doc.metadata else "{}",
                            json.dumps(doc.references_out)
                            if doc.references_out
                            else "[]",
                            doc.fingerprint,
                            doc.schema_version,
                            doc.embed_model,
                            doc.embed_version,
                            datetime.now().isoformat(),
                        ),
                    )

                    # Store embedding if provided
                    if embedding is not None and self.use_vector_search:
                        embedding_blob = self._embedding_to_blob(embedding)
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO docs_embeddings
                            (doc_id, embedding, embed_model, embed_version)
                            VALUES (?, ?, ?, ?)
                        """,
                            (
                                doc.doc_id,
                                embedding_blob,
                                doc.embed_model,
                                doc.embed_version,
                            ),
                        )

                    # Update edges
                    self._update_edges(conn, doc)

                    # Auto-populate asset_tags from metadata
                    self._update_asset_tags(conn, doc)

                    # FTS is maintained via on-demand rebuilds.
                    self._set_fts_dirty(conn)

                    conn.commit()
                    return True
                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if self._is_transient_open_error(e) and attempts < 2:
                        attempts += 1
                        self._reset_write_connection()
                        time.sleep(0.02 * attempts)
                        continue
                    raise
        return False

    def upsert_docs_batch(
        self,
        docs: list[DocChunk],
        embeddings: list[list[float]] = None,
        force: bool = False,
    ) -> dict:
        """
        Batch insert/update documents for significantly better performance.

        Uses executemany and single transaction for 10-100x speedup over
        individual upsert_doc calls.

        Args:
            docs: List of DocChunk objects to store
            embeddings: Optional list of embedding vectors (same length as docs, or None)
            force: If True, skip fingerprint checks and always update

        Returns:
            Dict with 'inserted', 'unchanged', 'errors' counts
        """
        if not docs:
            return {"inserted": 0, "unchanged": 0, "errors": 0}

        stats = {"inserted": 0, "unchanged": 0, "errors": 0}
        batch_attempts = 0
        while batch_attempts < 2:
            with self._write_lock:
                conn = self._get_write_connection()
                docs_to_insert = docs
                batch_embeddings = embeddings
                try:
                    # If not forcing, check fingerprints to skip unchanged docs
                    if not force:
                        doc_ids = [doc.doc_id for doc in docs]
                        # SQLite has a limit on query variables - chunk the lookup
                        existing_fps = {}
                        chunk_size = 500
                        for i in range(0, len(doc_ids), chunk_size):
                            chunk = doc_ids[i : i + chunk_size]
                            placeholders = ",".join("?" * len(chunk))
                            existing = conn.execute(
                                f"SELECT doc_id, fingerprint FROM docs WHERE doc_id IN ({placeholders})",
                                chunk,
                            ).fetchall()
                            for row in existing:
                                existing_fps[row["doc_id"]] = row["fingerprint"]

                        docs_to_insert = []
                        embeddings_to_insert = []
                        for i, doc in enumerate(docs):
                            if (
                                doc.doc_id in existing_fps
                                and existing_fps[doc.doc_id] == doc.fingerprint
                            ):
                                stats["unchanged"] += 1
                            else:
                                docs_to_insert.append(doc)
                                if embeddings:
                                    embeddings_to_insert.append(
                                        embeddings[i] if i < len(embeddings) else None
                                    )

                        batch_embeddings = embeddings_to_insert if embeddings else None

                    if not docs_to_insert:
                        return stats

                    now = datetime.now().isoformat()

                    # Prepare batch data for docs
                    doc_data = [
                        (
                            doc.doc_id,
                            doc.type,
                            doc.path,
                            doc.name,
                            doc.module,
                            doc.asset_type,
                            doc.text,
                            json.dumps(doc.metadata) if doc.metadata else "{}",
                            json.dumps(doc.references_out)
                            if doc.references_out
                            else "[]",
                            doc.fingerprint,
                            doc.schema_version,
                            doc.embed_model,
                            doc.embed_version,
                            now,
                        )
                        for doc in docs_to_insert
                    ]

                    # Batch insert docs
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO docs
                        (doc_id, type, path, name, module, asset_type, text, metadata,
                         references_out, fingerprint, schema_version, embed_model, embed_version, indexed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        doc_data,
                    )

                    # Batch insert embeddings if provided
                    if batch_embeddings and self.use_vector_search:
                        embedding_data = []
                        for i, doc in enumerate(docs_to_insert):
                            if (
                                i < len(batch_embeddings)
                                and batch_embeddings[i] is not None
                            ):
                                embedding_data.append(
                                    (
                                        doc.doc_id,
                                        self._embedding_to_blob(batch_embeddings[i]),
                                        doc.embed_model,
                                        doc.embed_version,
                                    )
                                )

                        if embedding_data:
                            conn.executemany(
                                """
                                INSERT OR REPLACE INTO docs_embeddings
                                (doc_id, embedding, embed_model, embed_version)
                                VALUES (?, ?, ?, ?)
                            """,
                                embedding_data,
                            )

                    # Batch update edges
                    # First, delete all existing edges for these docs
                    doc_ids_to_update = [doc.doc_id for doc in docs_to_insert]
                    # SQLite has a limit on query variables - chunk the delete
                    chunk_size = 500
                    for i in range(0, len(doc_ids_to_update), chunk_size):
                        chunk = doc_ids_to_update[i : i + chunk_size]
                        placeholders = ",".join("?" * len(chunk))
                        conn.execute(
                            f"DELETE FROM edges WHERE from_id IN ({placeholders})",
                            chunk,
                        )

                    # Then batch insert new edges
                    edge_data = []
                    for doc in docs_to_insert:
                        typed = getattr(doc, "typed_references_out", None) or {}
                        for ref in doc.references_out:
                            to_id = self._normalize_reference(ref, conn=conn)
                            edge_type = typed.get(ref, "uses_asset")
                            edge_data.append((doc.doc_id, to_id, edge_type))

                    if edge_data:
                        conn.executemany(
                            """
                            INSERT OR IGNORE INTO edges (from_id, to_id, edge_type)
                            VALUES (?, ?, ?)
                        """,
                            edge_data,
                        )

                    # Batch auto-populate asset_tags from metadata
                    tag_rows = []
                    tag_paths_to_clear = []
                    for doc in docs_to_insert:
                        meta = doc.metadata if isinstance(doc.metadata, dict) else {}
                        tags = meta.get("gameplay_tags")
                        if tags:
                            tag_paths_to_clear.append(doc.path)
                            for tag in tags:
                                if isinstance(tag, str) and tag:
                                    tag_rows.append((doc.path, tag))
                    if tag_paths_to_clear:
                        chunk_size = 500
                        for i in range(0, len(tag_paths_to_clear), chunk_size):
                            chunk = tag_paths_to_clear[i : i + chunk_size]
                            placeholders = ",".join("?" * len(chunk))
                            conn.execute(
                                f"DELETE FROM asset_tags WHERE path IN ({placeholders})",
                                chunk,
                            )
                    if tag_rows:
                        conn.executemany(
                            "INSERT OR IGNORE INTO asset_tags (path, tag) VALUES (?, ?)",
                            tag_rows,
                        )

                    self._set_fts_dirty(conn)

                    conn.commit()
                    stats["inserted"] = len(docs_to_insert)
                    return stats

                except Exception as e:
                    # Rollback on any error to prevent partial data corruption
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                    if self._is_transient_open_error(e) and batch_attempts < 1:
                        batch_attempts += 1
                        self._reset_write_connection()
                        time.sleep(0.03 * batch_attempts)
                        continue

                    # Keep the batch-level error for diagnostics, but retry per-doc
                    # so one transient DB issue doesn't drop the full batch.
                    stats["batch_error"] = str(e)
                    stats["fallback_used"] = True

                    embeddings_for_fallback = (
                        batch_embeddings if batch_embeddings else None
                    )

                    fallback_last_error = None
                    for i, doc in enumerate(docs_to_insert):
                        emb = None
                        if embeddings_for_fallback and i < len(embeddings_for_fallback):
                            emb = embeddings_for_fallback[i]
                        attempts = 0
                        while attempts < 3:
                            attempts += 1
                            try:
                                changed = self.upsert_doc(
                                    doc, embedding=emb, force=force
                                )
                                if changed:
                                    stats["inserted"] += 1
                                else:
                                    stats["unchanged"] += 1
                                break
                            except Exception as single_err:
                                is_transient_open = self._is_transient_open_error(
                                    single_err
                                )
                                if is_transient_open and attempts < 3:
                                    # Brief backoff for intermittent Linux SQLITE_CANTOPEN spikes.
                                    time.sleep(0.02 * attempts)
                                    continue
                                stats["errors"] += 1
                                fallback_last_error = str(single_err)
                                break

                    if fallback_last_error:
                        stats["last_error"] = fallback_last_error
                    return stats

        return stats

    def _update_edges(self, conn: sqlite3.Connection, doc: DocChunk):
        """Update reference graph edges for a document.

        Uses ``doc.typed_references_out`` (ref → edge_type) when available,
        falling back to ``'uses_asset'`` for refs not in the typed dict.
        """
        # Delete existing outgoing edges
        conn.execute("DELETE FROM edges WHERE from_id = ?", (doc.doc_id,))

        typed = getattr(doc, "typed_references_out", None) or {}

        # Insert new edges
        for ref in doc.references_out:
            # Normalize reference to doc_id format
            to_id = self._normalize_reference(ref, conn=conn)
            edge_type = typed.get(ref, "uses_asset")
            conn.execute(
                """
                INSERT OR IGNORE INTO edges (from_id, to_id, edge_type, metadata)
                VALUES (?, ?, ?, ?)
            """,
                (doc.doc_id, to_id, edge_type, None),
            )

    def _update_asset_tags(self, conn: sqlite3.Connection, doc: DocChunk):
        """Auto-populate asset_tags from doc metadata['gameplay_tags']."""
        meta = doc.metadata if isinstance(doc.metadata, dict) else {}
        tags = meta.get("gameplay_tags")
        if not tags:
            return
        conn.execute("DELETE FROM asset_tags WHERE path = ?", (doc.path,))
        tag_rows = [(doc.path, tag) for tag in tags if isinstance(tag, str) and tag]
        if tag_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO asset_tags (path, tag) VALUES (?, ?)",
                tag_rows,
            )

    def upsert_asset_tags(self, path: str, tags: list[str]):
        """Explicitly replace all tags for a given asset path."""
        with self._write_lock:
            conn = self._get_write_connection()
            conn.execute("DELETE FROM asset_tags WHERE path = ?", (path,))
            if tags:
                rows = [(path, t) for t in tags if isinstance(t, str) and t]
                if rows:
                    conn.executemany(
                        "INSERT OR IGNORE INTO asset_tags (path, tag) VALUES (?, ?)",
                        rows,
                    )
            conn.commit()

    def search_by_tag(self, tag_query: str, limit: int = 50) -> list[dict]:
        """Search assets by GameplayTag.

        Supports:
          - Exact match: ``"InputTag.Ability.Dash"``
          - Prefix wildcard: ``"InputTag.*"`` or ``"InputTag.Ability.*"``
          - Substring: ``"%Footstep%"`` (LIKE pattern)
          - Plain text auto-fallback: ``"Ability"`` tries exact → prefix → substring
        """
        conn = self._get_connection()
        try:
            if tag_query.endswith(".*"):
                # Prefix search
                prefix = tag_query[:-1]  # keep trailing dot
                rows = conn.execute(
                    """
                    SELECT at.path, at.tag, d.name, d.asset_type
                    FROM asset_tags at
                    LEFT JOIN docs d ON d.path = at.path
                    WHERE at.tag LIKE ?
                    ORDER BY at.tag, at.path
                    LIMIT ?
                    """,
                    (prefix + "%", limit),
                ).fetchall()
            elif "%" in tag_query:
                # LIKE pattern
                rows = conn.execute(
                    """
                    SELECT at.path, at.tag, d.name, d.asset_type
                    FROM asset_tags at
                    LEFT JOIN docs d ON d.path = at.path
                    WHERE at.tag LIKE ?
                    ORDER BY at.tag, at.path
                    LIMIT ?
                    """,
                    (tag_query, limit),
                ).fetchall()
            else:
                # Exact match, then fallback to prefix, then substring
                rows = conn.execute(
                    """
                    SELECT at.path, at.tag, d.name, d.asset_type
                    FROM asset_tags at
                    LEFT JOIN docs d ON d.path = at.path
                    WHERE at.tag = ?
                    ORDER BY at.path
                    LIMIT ?
                    """,
                    (tag_query, limit),
                ).fetchall()
                if not rows:
                    # Try as prefix: "Ability" -> "Ability.%"
                    rows = conn.execute(
                        """
                        SELECT at.path, at.tag, d.name, d.asset_type
                        FROM asset_tags at
                        LEFT JOIN docs d ON d.path = at.path
                        WHERE at.tag LIKE ?
                        ORDER BY at.tag, at.path
                        LIMIT ?
                        """,
                        (tag_query + ".%", limit),
                    ).fetchall()
                if not rows:
                    # Try as substring: "Ability" -> "%Ability%"
                    rows = conn.execute(
                        """
                        SELECT at.path, at.tag, d.name, d.asset_type
                        FROM asset_tags at
                        LEFT JOIN docs d ON d.path = at.path
                        WHERE at.tag LIKE ?
                        ORDER BY at.tag, at.path
                        LIMIT ?
                        """,
                        ("%" + tag_query + "%", limit),
                    ).fetchall()

            return [
                {
                    "path": row["path"],
                    "tag": row["tag"],
                    "name": row["name"] or row["path"].split("/")[-1],
                    "asset_type": row["asset_type"] or "Unknown",
                }
                for row in rows
            ]
        finally:
            conn.close()

    def get_tag_stats(self, prefix: str = None, limit: int = 100) -> list[dict]:
        """Get distinct tags with asset counts, optionally filtered by prefix."""
        conn = self._get_connection()
        try:
            if prefix:
                rows = conn.execute(
                    """
                    SELECT tag, COUNT(DISTINCT path) as asset_count
                    FROM asset_tags
                    WHERE tag LIKE ?
                    GROUP BY tag
                    ORDER BY asset_count DESC
                    LIMIT ?
                    """,
                    (prefix + "%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT tag, COUNT(DISTINCT path) as asset_count
                    FROM asset_tags
                    GROUP BY tag
                    ORDER BY asset_count DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            return [
                {"tag": row["tag"], "asset_count": row["asset_count"]} for row in rows
            ]
        finally:
            conn.close()

    def _normalize_reference(self, ref: str, conn: sqlite3.Connection = None) -> str:
        """Normalize a reference path to doc_id format."""
        # If already a doc_id format, return as is
        if (
            ref.startswith("asset:")
            or ref.startswith("material:")
            or ref.startswith("widget:")
        ):
            return ref
        if (
            ref.startswith("cpp_class:")
            or ref.startswith("cpp_func:")
            or ref.startswith("source:")
        ):
            return ref

        # Convert /Game/... path to asset: format
        if ref.startswith("/Game/"):
            return f"asset:{ref}"

        # For /Script/ references, try to resolve to C++ class doc_id
        if ref.startswith("/Script/"):
            resolved = self.resolve_script_class(ref, conn=conn)
            if resolved:
                return resolved  # Links directly to cpp_class doc
            # Fallback: keep script: prefix for unresolved refs
            return f"script:{ref}"

        return f"asset:{ref}"

    def get_doc(self, doc_id: str) -> Optional[DocChunk]:
        """Get a document by ID."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM docs WHERE doc_id = ?", (doc_id,)
            ).fetchone()

            if row:
                return self._row_to_doc(row)
            return None
        finally:
            conn.close()

    def get_docs(self, doc_ids: list[str]) -> list[DocChunk]:
        """Get multiple documents by ID."""
        if not doc_ids:
            return []

        conn = self._get_connection()
        try:
            # SQLite has a limit on query variables - chunk the lookup
            results = []
            chunk_size = 500
            for i in range(0, len(doc_ids), chunk_size):
                chunk = doc_ids[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT * FROM docs WHERE doc_id IN ({placeholders})", chunk
                ).fetchall()
                results.extend(self._row_to_doc(row) for row in rows)
            return results
        finally:
            conn.close()

    def delete_doc(self, doc_id: str) -> bool:
        """Delete a document."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
            if cursor.rowcount > 0:
                self._set_fts_dirty(conn)
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def search_fts(
        self,
        query: str,
        filters: dict = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResult]:
        """
        Full-text search using FTS5.

        Args:
            query: Search query (supports FTS5 syntax)
            filters: Optional filters {type, path_prefix, module, asset_type}
            limit: Maximum results
            offset: Results offset

        Returns:
            List of search results with scores
        """
        if self.is_fts_dirty():
            self.rebuild_fts()

        conn = self._get_connection()
        try:
            # Build the query
            sql = """
                SELECT docs.*, bm25(docs_fts, 0.0, 10.0, 5.0, 1.0) as score
                FROM docs_fts
                JOIN docs ON docs_fts.doc_id = docs.doc_id
                WHERE docs_fts MATCH ?
            """
            params = [query]

            # Apply filters
            if filters:
                if filters.get("type"):
                    sql += " AND docs.type = ?"
                    params.append(filters["type"])
                if filters.get("path_prefix"):
                    sql += " AND docs.path LIKE ?"
                    params.append(f"{filters['path_prefix']}%")
                if filters.get("module"):
                    sql += " AND docs.module = ?"
                    params.append(filters["module"])
                if filters.get("asset_type"):
                    sql += " AND docs.asset_type = ?"
                    params.append(filters["asset_type"])

            sql += " ORDER BY score LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(sql, params).fetchall()

            results = []
            for row in rows:
                doc = self._row_to_doc(row)
                results.append(
                    SearchResult(
                        doc_id=doc.doc_id,
                        score=-row[
                            "score"
                        ],  # BM25 returns negative scores (lower = better)
                        doc=doc,
                    )
                )

            return results
        except sqlite3.OperationalError:
            # FTS query syntax error - return empty
            return []
        finally:
            conn.close()

    def search_vector(
        self,
        query_embedding: list[float],
        filters: dict = None,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """
        Vector similarity search using cosine similarity.

        Args:
            query_embedding: Query embedding vector
            filters: Optional filters {type, path_prefix, module, asset_type}
            limit: Maximum results
            min_score: Minimum similarity score (0-1)

        Returns:
            List of search results with similarity scores
        """
        if not self.use_vector_search or not HAS_NUMPY:
            return []

        conn = self._get_connection()
        try:
            # Build filter clause
            filter_sql = ""
            filter_params = []

            if filters:
                filter_clauses = []
                if filters.get("type"):
                    filter_clauses.append("docs.type = ?")
                    filter_params.append(filters["type"])
                if filters.get("path_prefix"):
                    filter_clauses.append("docs.path LIKE ?")
                    filter_params.append(f"{filters['path_prefix']}%")
                if filters.get("module"):
                    filter_clauses.append("docs.module = ?")
                    filter_params.append(filters["module"])
                if filters.get("asset_type"):
                    filter_clauses.append("docs.asset_type = ?")
                    filter_params.append(filters["asset_type"])

                if filter_clauses:
                    filter_sql = " WHERE " + " AND ".join(filter_clauses)

            # Get all embeddings (with optional filtering)
            sql = f"""
                SELECT docs.*, docs_embeddings.embedding
                FROM docs_embeddings
                JOIN docs ON docs_embeddings.doc_id = docs.doc_id
                {filter_sql}
            """

            try:
                rows = conn.execute(sql, filter_params).fetchall()
            except sqlite3.OperationalError:
                # Table doesn't exist (index built without embeddings)
                return []

            if not rows:
                return []

            # Calculate cosine similarities
            query_vec = np.array(query_embedding, dtype=np.float32)
            query_norm = np.linalg.norm(query_vec)

            results = []
            for row in rows:
                embedding = self._blob_to_embedding(row["embedding"])
                emb_vec = np.array(embedding, dtype=np.float32)
                emb_norm = np.linalg.norm(emb_vec)

                if query_norm > 0 and emb_norm > 0:
                    similarity = np.dot(query_vec, emb_vec) / (query_norm * emb_norm)
                else:
                    similarity = 0.0

                if similarity >= min_score:
                    doc = self._row_to_doc(row)
                    results.append(
                        SearchResult(
                            doc_id=doc.doc_id,
                            score=float(similarity),
                            doc=doc,
                        )
                    )

            # Sort by similarity descending
            results.sort(key=lambda r: r.score, reverse=True)
            return results[:limit]

        finally:
            conn.close()

    def expand_refs(
        self,
        doc_id: str,
        direction: str = "both",
        depth: int = 1,
        max_nodes: int = 50,
        type_filters: list[str] = None,
    ) -> ReferenceGraph:
        """
        Expand reference graph from a seed document.

        Args:
            doc_id: Starting document ID
            direction: "forward", "reverse", or "both"
            depth: Maximum traversal depth
            max_nodes: Maximum nodes to return
            type_filters: Only include nodes of these types

        Returns:
            ReferenceGraph with forward and reverse references
        """
        conn = self._get_connection()
        try:
            forward_refs = {}
            reverse_refs = {}
            visited = {doc_id}
            nodes = {}

            # Get seed document
            seed_doc = self.get_doc(doc_id)
            if seed_doc:
                nodes[doc_id] = seed_doc

            # BFS traversal
            current_level = [doc_id]

            for d in range(depth):
                next_level = []

                for current_id in current_level:
                    if len(nodes) >= max_nodes:
                        break

                    # Forward references
                    if direction in ("forward", "both"):
                        rows = conn.execute(
                            "SELECT to_id FROM edges WHERE from_id = ?", (current_id,)
                        ).fetchall()

                        refs = []
                        for row in rows:
                            to_id = row["to_id"]
                            refs.append(to_id)

                            if to_id not in visited:
                                visited.add(to_id)
                                next_level.append(to_id)

                                # Get the document
                                doc = self.get_doc(to_id)
                                if doc and len(nodes) < max_nodes:
                                    if not type_filters or doc.type in type_filters:
                                        nodes[to_id] = doc

                        if refs:
                            forward_refs[current_id] = refs

                    # Reverse references
                    if direction in ("reverse", "both"):
                        rows = conn.execute(
                            "SELECT from_id FROM edges WHERE to_id = ?", (current_id,)
                        ).fetchall()

                        refs = []
                        for row in rows:
                            from_id = row["from_id"]
                            refs.append(from_id)

                            if from_id not in visited:
                                visited.add(from_id)
                                next_level.append(from_id)

                                # Get the document
                                doc = self.get_doc(from_id)
                                if doc and len(nodes) < max_nodes:
                                    if not type_filters or doc.type in type_filters:
                                        nodes[from_id] = doc

                        if refs:
                            if current_id not in reverse_refs:
                                reverse_refs[current_id] = []
                            reverse_refs[current_id].extend(refs)

                current_level = next_level

            return ReferenceGraph(
                seed_id=doc_id,
                forward_refs=forward_refs,
                reverse_refs=reverse_refs,
                nodes=nodes,
                depth=depth,
            )

        finally:
            conn.close()

    def get_status(self) -> IndexStatus:
        """Get index status and statistics."""
        conn = self._get_connection()
        try:
            # Total docs
            total = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

            # Docs by type
            rows = conn.execute(
                "SELECT type, COUNT(*) as cnt FROM docs GROUP BY type"
            ).fetchall()
            by_type = {row["type"]: row["cnt"] for row in rows}

            # Total edges
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

            # Last indexed
            last = conn.execute("SELECT MAX(indexed_at) FROM docs").fetchone()[0]
            last_indexed = datetime.fromisoformat(last) if last else None

            # Embed model
            embed_model = conn.execute(
                "SELECT embed_model FROM docs WHERE embed_model IS NOT NULL LIMIT 1"
            ).fetchone()
            embed_model = embed_model["embed_model"] if embed_model else None

            # Lightweight assets stats
            lightweight_total = conn.execute(
                "SELECT COUNT(*) FROM lightweight_assets"
            ).fetchone()[0]

            lightweight_by_type = {}
            rows = conn.execute(
                "SELECT asset_type, COUNT(*) as cnt FROM lightweight_assets GROUP BY asset_type"
            ).fetchall()
            for row in rows:
                lightweight_by_type[row["asset_type"]] = row["cnt"]

            return IndexStatus(
                total_docs=total,
                docs_by_type=by_type,
                total_edges=edge_count,
                last_indexed=last_indexed,
                pending_updates=0,  # Could track this with a queue table
                embed_model=embed_model,
                schema_version=self.SCHEMA_VERSION,
                lightweight_total=lightweight_total,
                lightweight_by_type=lightweight_by_type,
            )

        finally:
            conn.close()

    def _row_to_doc(self, row: sqlite3.Row) -> DocChunk:
        """Convert database row to DocChunk."""
        return DocChunk.from_dict(dict(row))

    def _embedding_to_blob(self, embedding: list[float]) -> bytes:
        """Convert embedding list to binary blob."""
        if HAS_NUMPY:
            return np.array(embedding, dtype=np.float32).tobytes()
        return json.dumps(embedding).encode()

    def _blob_to_embedding(self, blob: bytes) -> list[float]:
        """Convert binary blob to embedding list."""
        if HAS_NUMPY:
            return np.frombuffer(blob, dtype=np.float32).tolist()
        return json.loads(blob.decode())

    def store_embedding(
        self,
        doc_id: str,
        embedding: list[float],
        model: str = None,
        version: str = None,
    ):
        """Store or update embedding for a document."""
        if not self.use_vector_search:
            return

        conn = self._get_connection()
        try:
            embedding_blob = self._embedding_to_blob(embedding)
            conn.execute(
                """
                INSERT OR REPLACE INTO docs_embeddings
                (doc_id, embedding, embed_model, embed_version)
                VALUES (?, ?, ?, ?)
            """,
                (doc_id, embedding_blob, model, version),
            )
            conn.commit()
        finally:
            conn.close()

    def get_docs_needing_embedding(
        self, embed_model: str, embed_version: str, limit: int = 100
    ) -> list[DocChunk]:
        """Get documents that need embedding (new or different model/version)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT docs.*
                FROM docs
                LEFT JOIN docs_embeddings ON docs.doc_id = docs_embeddings.doc_id
                WHERE docs_embeddings.doc_id IS NULL
                   OR docs_embeddings.embed_model != ?
                   OR docs_embeddings.embed_version != ?
                LIMIT ?
            """,
                (embed_model, embed_version, limit),
            ).fetchall()

            return [self._row_to_doc(row) for row in rows]
        finally:
            conn.close()

    def get_docs_without_embeddings(
        self, min_text_len: int = 20
    ) -> list[tuple[str, str]]:
        """Get (doc_id, text) pairs for docs that have no embedding yet.

        Only returns docs with text longer than *min_text_len* characters
        to skip trivially short entries.
        """
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT d.doc_id, d.text FROM docs d
                LEFT JOIN docs_embeddings e ON d.doc_id = e.doc_id
                WHERE e.doc_id IS NULL AND d.text IS NOT NULL AND LENGTH(d.text) > ?
            """,
                (min_text_len,),
            ).fetchall()
            return [(row["doc_id"], row["text"]) for row in rows]
        finally:
            conn.close()

    def upsert_embeddings_batch(
        self,
        items: list[tuple[str, list[float]]],
        model: str = None,
        version: str = None,
    ):
        """Batch insert/update embeddings.

        Args:
            items: List of (doc_id, embedding_vector) tuples
            model: Embedding model name
            version: Embedding model version
        """
        if not items or not self.use_vector_search:
            return

        with self._write_lock:
            conn = self._get_write_connection()
            data = [
                (doc_id, self._embedding_to_blob(emb), model, version)
                for doc_id, emb in items
            ]
            conn.executemany(
                """
                INSERT OR REPLACE INTO docs_embeddings
                (doc_id, embedding, embed_model, embed_version)
                VALUES (?, ?, ?, ?)
            """,
                data,
            )
            conn.commit()

    def clear(self):
        """Clear all data from the index."""
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM docs_embeddings")
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM asset_tags")
            conn.execute("DELETE FROM docs")
            conn.execute("DELETE FROM lightweight_assets")
            self._set_fts_dirty(conn)
            conn.commit()
        finally:
            conn.close()

    def _set_fts_dirty(self, conn: sqlite3.Connection):
        """Mark FTS as needing a rebuild after docs table mutations."""
        conn.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('fts_dirty', '1')"
        )

    def is_fts_dirty(self) -> bool:
        """Check whether docs_fts needs a rebuild."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM index_meta WHERE key = 'fts_dirty'"
            ).fetchone()
            return (row["value"] if row else "0") == "1"
        finally:
            conn.close()

    def rebuild_fts(self):
        """Rebuild FTS5 index from docs table. Use after bulk operations or corruption."""
        conn = self._get_connection()
        try:
            conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
            conn.execute(
                "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('fts_dirty', '0')"
            )
            conn.commit()
        finally:
            conn.close()

    # =========================================================================
    # FILE METADATA - For incremental indexing (skip unchanged files)
    # =========================================================================

    def get_file_meta_batch(self, paths: list[str]) -> dict[str, tuple[float, int]]:
        """
        Get file metadata for multiple paths in one query.

        Args:
            paths: List of file paths to check

        Returns:
            Dict mapping path -> (mtime, size) for files that exist in DB
        """
        if not paths:
            return {}

        conn = self._get_connection()
        try:
            result = {}
            # SQLite has a limit on query variables (typically 999)
            # Chunk into batches of 500 to stay safely under the limit
            chunk_size = 500
            for i in range(0, len(paths), chunk_size):
                chunk = paths[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT path, mtime, size FROM file_meta WHERE path IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    result[row["path"]] = (row["mtime"], row["size"])
            return result
        finally:
            conn.close()

    def upsert_file_meta_batch(self, file_data: list[tuple[str, float, int, str]]):
        """
        Batch upsert file metadata.

        Args:
            file_data: List of (path, mtime, size, asset_type) tuples
        """
        if not file_data:
            return

        with self._write_lock:
            conn = self._get_write_connection()
            conn.executemany(
                """
                INSERT OR REPLACE INTO file_meta (path, mtime, size, asset_type, indexed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                file_data,
            )
            conn.commit()

    def get_all_indexed_paths(self) -> set[str]:
        """Get all file paths currently in the index."""
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT path FROM file_meta").fetchall()
            return {row["path"] for row in rows}
        finally:
            conn.close()

    def delete_file_meta(self, paths: list[str]):
        """Delete file metadata for paths that no longer exist."""
        if not paths:
            return

        with self._write_lock:
            conn = self._get_write_connection()
            # SQLite has a limit on query variables - chunk into batches
            chunk_size = 500
            for i in range(0, len(paths), chunk_size):
                chunk = paths[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                conn.execute(
                    f"DELETE FROM file_meta WHERE path IN ({placeholders})", chunk
                )
            conn.commit()

    # =========================================================================
    # LIGHTWEIGHT ASSETS - Path + refs only, for low-value asset types
    # =========================================================================

    def _replace_lightweight_refs(
        self,
        conn: sqlite3.Connection,
        refs_by_path: dict[str, list[str]],
    ):
        """
        Replace normalized lightweight reference edges for one or more assets.

        Args:
            conn: Active DB connection within current transaction
            refs_by_path: Mapping of asset path -> referenced /Game paths
        """
        if not refs_by_path:
            return

        paths = list(refs_by_path.keys())

        # Delete prior edges for these assets in chunks.
        chunk_size = 500
        for i in range(0, len(paths), chunk_size):
            chunk = paths[i : i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(
                f"DELETE FROM lightweight_refs WHERE asset_path IN ({placeholders})",
                chunk,
            )

        # Insert new normalized edges.
        edge_rows = []
        for asset_path, refs in refs_by_path.items():
            seen = set()
            for ref in refs or []:
                if not isinstance(ref, str) or not ref:
                    continue
                if ref in seen:
                    continue
                seen.add(ref)
                edge_rows.append((asset_path, ref))

        if edge_rows:
            conn.executemany(
                """
                INSERT OR IGNORE INTO lightweight_refs (asset_path, ref_path)
                VALUES (?, ?)
            """,
                edge_rows,
            )

    @staticmethod
    def _is_transient_open_error(error: Exception) -> bool:
        """Return True for intermittent sqlite open-file failures."""
        return "unable to open database file" in str(error).lower()

    def upsert_lightweight_asset(
        self,
        path: str,
        name: str,
        asset_type: str,
        references: list[str],
    ) -> bool:
        """
        Insert or update a lightweight asset entry.

        Lightweight assets store only path, name, type, and references -
        no semantic text or embeddings. Used for textures, meshes, animations,
        OFPA files, and other low-value-for-search asset types.

        Args:
            path: Game path (e.g., /Game/Textures/T_Example)
            name: Asset name
            asset_type: Type string (e.g., Texture, StaticMesh)
            references: List of /Game/ paths this asset references

        Returns:
            True if inserted/updated, False if unchanged
        """
        attempts = 0
        while attempts < 3:
            with self._write_lock:
                conn = self._get_write_connection()
                try:
                    refs_json = json.dumps(references)

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO lightweight_assets
                        (path, name, asset_type, "references", indexed_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                        (path, name, asset_type, refs_json),
                    )
                    self._replace_lightweight_refs(conn, {path: references})
                    conn.commit()
                    return True
                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if self._is_transient_open_error(e) and attempts < 2:
                        attempts += 1
                        self._reset_write_connection()
                        time.sleep(0.02 * attempts)
                        continue
                    raise
        return False

    def upsert_lightweight_batch(
        self,
        assets: list[dict],
    ) -> int:
        """
        Batch insert/update lightweight assets for performance.

        Args:
            assets: List of dicts with keys: path, name, asset_type, references

        Returns:
            Number of assets processed
        """
        if not assets:
            return 0

        attempts = 0
        while attempts < 3:
            with self._write_lock:
                conn = self._get_write_connection()
                try:
                    # Prepare batch data for executemany
                    batch_data = [
                        (
                            asset["path"],
                            asset["name"],
                            asset["asset_type"],
                            json.dumps(asset.get("references", [])),
                        )
                        for asset in assets
                    ]

                    # Use executemany for batch insert - significantly faster than individual inserts
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO lightweight_assets
                        (path, name, asset_type, "references", indexed_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                        batch_data,
                    )

                    refs_by_path = {
                        asset["path"]: asset.get("references", []) for asset in assets
                    }
                    self._replace_lightweight_refs(conn, refs_by_path)

                    conn.commit()
                    return len(assets)
                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if self._is_transient_open_error(e) and attempts < 2:
                        attempts += 1
                        self._reset_write_connection()
                        time.sleep(0.02 * attempts)
                        continue

                    # Final fallback: best-effort per-asset writes so long runs can continue.
                    processed = 0
                    for asset in assets:
                        try:
                            changed = self.upsert_lightweight_asset(
                                path=asset["path"],
                                name=asset["name"],
                                asset_type=asset["asset_type"],
                                references=asset.get("references", []),
                            )
                            if changed:
                                processed += 1
                        except Exception:
                            pass
                    return processed
        return 0

    def get_lightweight_asset(self, path: str) -> Optional[dict]:
        """Get a lightweight asset by path."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM lightweight_assets WHERE path = ?", (path,)
            ).fetchone()
            if row:
                return {
                    "path": row["path"],
                    "name": row["name"],
                    "asset_type": row["asset_type"],
                    "references": json.loads(row["references"]),
                    "indexed_at": row["indexed_at"],
                }
            return None
        finally:
            conn.close()

    def find_assets_referencing(self, target_path: str, limit: int = 100) -> list[dict]:
        """
        Find assets that reference a given path.

        Useful for "where is BP_X used?" queries across OFPA files and other assets.

        Args:
            target_path: The /Game/ path to search for in references
            limit: Maximum results to return

        Returns:
            List of dicts with path, name, asset_type
        """
        conn = self._get_connection()
        try:
            # First pass: exact ref lookup using normalized table (fast and precise).
            rows = conn.execute(
                """
                SELECT la.path, la.name, la.asset_type
                FROM lightweight_refs lr
                JOIN lightweight_assets la ON lr.asset_path = la.path
                WHERE lr.ref_path = ?
                LIMIT ?
            """,
                (target_path, limit),
            ).fetchall()

            results = [
                {
                    "path": row["path"],
                    "name": row["name"],
                    "asset_type": row["asset_type"],
                }
                for row in rows
            ]

            # Also check edges table for full semantic docs
            remaining = max(0, limit - len(results))
            edge_rows = []
            if remaining > 0:
                if target_path.startswith("/Game/"):
                    normalized_target = f"asset:{target_path}"
                    edge_rows = conn.execute(
                        """
                        SELECT DISTINCT d.path, d.name, d.asset_type
                        FROM edges e
                        JOIN docs d ON e.from_id = d.doc_id
                        WHERE e.to_id = ?
                        LIMIT ?
                    """,
                        (normalized_target, remaining),
                    ).fetchall()
                else:
                    edge_rows = conn.execute(
                        """
                        SELECT DISTINCT d.path, d.name, d.asset_type
                        FROM edges e
                        JOIN docs d ON e.from_id = d.doc_id
                        WHERE e.to_id LIKE ?
                        LIMIT ?
                    """,
                        (f"%{target_path}%", remaining),
                    ).fetchall()

            for row in edge_rows:
                if row["path"] not in [r["path"] for r in results]:
                    results.append(
                        {
                            "path": row["path"],
                            "name": row["name"],
                            "asset_type": row["asset_type"],
                        }
                    )

            return results[:limit]
        finally:
            conn.close()

    def delete_lightweight_paths(self, paths: list[str]):
        """
        Delete lightweight rows for paths now covered by semantic docs.

        Keeps lightweight tables focused on low-value types and avoids stale
        Unknown entries after type/classification improvements.
        """
        if not paths:
            return

        with self._write_lock:
            conn = self._get_write_connection()
            chunk_size = 500
            for i in range(0, len(paths), chunk_size):
                chunk = paths[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                conn.execute(
                    f"DELETE FROM lightweight_assets WHERE path IN ({placeholders})",
                    chunk,
                )
            conn.commit()

    def get_lightweight_stats(self) -> dict:
        """Get statistics about lightweight assets."""
        conn = self._get_connection()
        try:
            total = conn.execute("SELECT COUNT(*) FROM lightweight_assets").fetchone()[
                0
            ]

            by_type = {}
            rows = conn.execute(
                "SELECT asset_type, COUNT(*) as cnt FROM lightweight_assets GROUP BY asset_type"
            ).fetchall()
            for row in rows:
                by_type[row["asset_type"]] = row["cnt"]

            return {
                "total": total,
                "by_type": by_type,
            }
        finally:
            conn.close()

    # =========================================================================
    # C++ CLASS INDEX - For cross-referencing Blueprints with C++ source
    # =========================================================================

    def upsert_cpp_class(self, class_name: str, doc_id: str, source_path: str = None):
        """
        Register a C++ class name for cross-referencing.

        Called when indexing C++ source files to build the lookup table.

        Args:
            class_name: Simple class name (e.g., "UCharacterMovementComponent")
            doc_id: Full doc_id (e.g., "cpp_class:Source/.../CharacterMovementComponent.h::UCharacterMovementComponent")
            source_path: Optional source file path for debugging
        """
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO cpp_class_index (class_name, doc_id, source_path)
                VALUES (?, ?, ?)
            """,
                (class_name, doc_id, source_path),
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_cpp_classes_batch(self, class_data: list[tuple[str, str, str]]):
        """
        Batch register C++ class names.

        Args:
            class_data: List of (class_name, doc_id, source_path) tuples
        """
        if not class_data:
            return

        conn = self._get_connection()
        try:
            conn.executemany(
                """
                INSERT OR REPLACE INTO cpp_class_index (class_name, doc_id, source_path)
                VALUES (?, ?, ?)
            """,
                class_data,
            )
            conn.commit()
        finally:
            conn.close()

    def resolve_cpp_class(self, class_name: str) -> Optional[str]:
        """
        Resolve a C++ class name to its doc_id.

        Args:
            class_name: Simple class name (e.g., "UCharacterMovementComponent")

        Returns:
            doc_id if found, None otherwise
        """
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT doc_id FROM cpp_class_index WHERE class_name = ?", (class_name,)
            ).fetchone()
            return row["doc_id"] if row else None
        finally:
            conn.close()

    def resolve_script_class(
        self, script_ref: str, conn: sqlite3.Connection = None
    ) -> Optional[str]:
        """
        Resolve a /Script/ reference to a cpp_class doc_id.

        Handles various formats:
        - /Script/Engine.UClassName -> resolves UClassName
        - /Script/UClassName -> resolves UClassName
        - UClassName_C -> strips _C suffix and resolves UClassName

        Args:
            script_ref: Script reference path or class name

        Returns:
            cpp_class doc_id if found, None otherwise
        """
        # Extract class name from /Script/Module.ClassName format
        if script_ref.startswith("/Script/"):
            # Remove /Script/ prefix
            remainder = script_ref[8:]  # len("/Script/") = 8
            # Take the part after the dot (if any)
            if "." in remainder:
                class_name = remainder.split(".")[-1]
            else:
                class_name = remainder
        else:
            class_name = script_ref

        # Strip _C suffix (Blueprint generated class suffix)
        if class_name.endswith("_C"):
            class_name = class_name[:-2]

        candidate_names = [class_name]

        # Many Unreal refs omit U/A/F/I prefixes (e.g. LyraHealthComponent),
        # while C++ declarations include them. Probe common prefixes.
        if class_name and class_name[0] not in ("U", "A", "F", "I", "S", "E", "T"):
            for prefix in ("U", "A", "F", "I", "S", "E", "T"):
                candidate_names.append(prefix + class_name)

        # Reuse current connection when available to avoid nested DB locks
        # during batch write transactions.
        if conn is not None:
            placeholders = ",".join("?" * len(candidate_names))
            row = conn.execute(
                f"SELECT doc_id FROM cpp_class_index WHERE class_name IN ({placeholders}) LIMIT 1",
                tuple(candidate_names),
            ).fetchone()
            if row:
                return row["doc_id"]

            # Fallback to source file doc when class extraction missed the UCLASS.
            source_candidates = (f"{class_name}.h", f"{class_name}.cpp")
            row = conn.execute(
                "SELECT doc_id FROM docs WHERE type = 'source_file' AND name IN (?, ?) LIMIT 1",
                source_candidates,
            ).fetchone()
            return row["doc_id"] if row else None

        for candidate in candidate_names:
            resolved = self.resolve_cpp_class(candidate)
            if resolved:
                return resolved

        source_doc = self._resolve_source_file_doc(class_name)
        return source_doc

    def _resolve_source_file_doc(self, base_name: str) -> Optional[str]:
        """Resolve a source_file doc from a bare type name."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT doc_id FROM docs WHERE type = 'source_file' AND name IN (?, ?) LIMIT 1",
                (f"{base_name}.h", f"{base_name}.cpp"),
            ).fetchone()
            return row["doc_id"] if row else None
        finally:
            conn.close()

    def get_cpp_class_stats(self) -> dict:
        """Get statistics about the C++ class index."""
        conn = self._get_connection()
        try:
            total = conn.execute("SELECT COUNT(*) FROM cpp_class_index").fetchone()[0]
            return {"total_classes": total}
        finally:
            conn.close()
