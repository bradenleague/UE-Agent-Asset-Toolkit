"""
Knowledge Store - SQLite-based storage for the semantic index.

Provides:
- Document metadata storage with SQLite
- Full-text search with FTS5
- Vector similarity search (numpy-based, with sqlite-vec/LanceDB options)
- Reference graph with edges table
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Optional
from pathlib import Path

from .schemas import DocChunk, SearchResult, ReferenceGraph, IndexStatus

# Optional: try to import vector search backends
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import sqlite_vec
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False


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

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Load sqlite-vec extension if available
        if HAS_SQLITE_VEC:
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
            except Exception:
                pass  # Fall back to numpy-based search

        return conn

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

            # Triggers to keep FTS in sync
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
                    INSERT INTO docs_fts(rowid, doc_id, name, path, text)
                    VALUES (new.rowid, new.doc_id, new.name, new.path, new.text);
                END
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
                    INSERT INTO docs_fts(docs_fts, rowid, doc_id, name, path, text)
                    VALUES('delete', old.rowid, old.doc_id, old.name, old.path, old.text);
                END
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
                    INSERT INTO docs_fts(docs_fts, rowid, doc_id, name, path, text)
                    VALUES('delete', old.rowid, old.doc_id, old.name, old.path, old.text);
                    INSERT INTO docs_fts(rowid, doc_id, name, path, text)
                    VALUES (new.rowid, new.doc_id, new.name, new.path, new.text);
                END
            """)

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

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_type ON docs(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_path ON docs(path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_module ON docs(module)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_fingerprint ON docs(fingerprint)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lightweight_type ON lightweight_assets(asset_type)")

            # Metadata table for index-level info
            conn.execute("""
                CREATE TABLE IF NOT EXISTS index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            conn.commit()
        finally:
            conn.close()

    def upsert_doc(self, doc: DocChunk, embedding: list[float] = None, force: bool = False) -> bool:
        """
        Insert or update a document.

        Args:
            doc: Document chunk to store
            embedding: Optional embedding vector
            force: If True, skip fingerprint check and always update

        Returns:
            True if document was inserted/updated, False if unchanged
        """
        conn = self._get_connection()
        try:
            # Check if document exists and has same fingerprint (unless force=True)
            if not force:
                existing = conn.execute(
                    "SELECT fingerprint FROM docs WHERE doc_id = ?",
                    (doc.doc_id,)
                ).fetchone()

                if existing and existing["fingerprint"] == doc.fingerprint:
                    return False  # No change

            # Upsert document
            conn.execute("""
                INSERT OR REPLACE INTO docs
                (doc_id, type, path, name, module, asset_type, text, metadata,
                 references_out, fingerprint, schema_version, embed_model, embed_version, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc.doc_id,
                doc.type,
                doc.path,
                doc.name,
                doc.module,
                doc.asset_type,
                doc.text,
                json.dumps(doc.metadata) if doc.metadata else "{}",
                json.dumps(doc.references_out) if doc.references_out else "[]",
                doc.fingerprint,
                doc.schema_version,
                doc.embed_model,
                doc.embed_version,
                datetime.now().isoformat(),
            ))

            # Store embedding if provided
            if embedding is not None and self.use_vector_search:
                embedding_blob = self._embedding_to_blob(embedding)
                conn.execute("""
                    INSERT OR REPLACE INTO docs_embeddings
                    (doc_id, embedding, embed_model, embed_version)
                    VALUES (?, ?, ?, ?)
                """, (doc.doc_id, embedding_blob, doc.embed_model, doc.embed_version))

            # Update edges
            self._update_edges(conn, doc)

            conn.commit()
            return True
        finally:
            conn.close()

    def _update_edges(self, conn: sqlite3.Connection, doc: DocChunk):
        """Update reference graph edges for a document."""
        # Delete existing outgoing edges
        conn.execute("DELETE FROM edges WHERE from_id = ?", (doc.doc_id,))

        # Insert new edges
        for ref in doc.references_out:
            # Normalize reference to doc_id format
            to_id = self._normalize_reference(ref)
            conn.execute("""
                INSERT OR IGNORE INTO edges (from_id, to_id, edge_type)
                VALUES (?, ?, 'uses_asset')
            """, (doc.doc_id, to_id))

    def _normalize_reference(self, ref: str) -> str:
        """Normalize a reference path to doc_id format."""
        # If already a doc_id format, return as is
        if ref.startswith("asset:") or ref.startswith("material:") or ref.startswith("widget:"):
            return ref

        # Convert /Game/... path to asset: format
        if ref.startswith("/Game/"):
            return f"asset:{ref}"

        # For /Script/ references, use as-is but with script: prefix
        if ref.startswith("/Script/"):
            return f"script:{ref}"

        return f"asset:{ref}"

    def get_doc(self, doc_id: str) -> Optional[DocChunk]:
        """Get a document by ID."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM docs WHERE doc_id = ?",
                (doc_id,)
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
            placeholders = ",".join("?" * len(doc_ids))
            rows = conn.execute(
                f"SELECT * FROM docs WHERE doc_id IN ({placeholders})",
                doc_ids
            ).fetchall()

            return [self._row_to_doc(row) for row in rows]
        finally:
            conn.close()

    def delete_doc(self, doc_id: str) -> bool:
        """Delete a document."""
        conn = self._get_connection()
        try:
            cursor = conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
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
        conn = self._get_connection()
        try:
            # Build the query
            sql = """
                SELECT docs.*, bm25(docs_fts) as score
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
                results.append(SearchResult(
                    doc_id=doc.doc_id,
                    score=-row["score"],  # BM25 returns negative scores (lower = better)
                    doc=doc,
                ))

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
                    results.append(SearchResult(
                        doc_id=doc.doc_id,
                        score=float(similarity),
                        doc=doc,
                    ))

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
                            "SELECT to_id FROM edges WHERE from_id = ?",
                            (current_id,)
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
                            "SELECT from_id FROM edges WHERE to_id = ?",
                            (current_id,)
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
            last = conn.execute(
                "SELECT MAX(indexed_at) FROM docs"
            ).fetchone()[0]
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

    def store_embedding(self, doc_id: str, embedding: list[float], model: str = None, version: str = None):
        """Store or update embedding for a document."""
        if not self.use_vector_search:
            return

        conn = self._get_connection()
        try:
            embedding_blob = self._embedding_to_blob(embedding)
            conn.execute("""
                INSERT OR REPLACE INTO docs_embeddings
                (doc_id, embedding, embed_model, embed_version)
                VALUES (?, ?, ?, ?)
            """, (doc_id, embedding_blob, model, version))
            conn.commit()
        finally:
            conn.close()

    def get_docs_needing_embedding(self, embed_model: str, embed_version: str, limit: int = 100) -> list[DocChunk]:
        """Get documents that need embedding (new or different model/version)."""
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT docs.*
                FROM docs
                LEFT JOIN docs_embeddings ON docs.doc_id = docs_embeddings.doc_id
                WHERE docs_embeddings.doc_id IS NULL
                   OR docs_embeddings.embed_model != ?
                   OR docs_embeddings.embed_version != ?
                LIMIT ?
            """, (embed_model, embed_version, limit)).fetchall()

            return [self._row_to_doc(row) for row in rows]
        finally:
            conn.close()

    def clear(self):
        """Clear all data from the index."""
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM docs_embeddings")
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM docs")
            conn.execute("DELETE FROM lightweight_assets")
            conn.commit()
        finally:
            conn.close()

    # =========================================================================
    # LIGHTWEIGHT ASSETS - Path + refs only, for low-value asset types
    # =========================================================================

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
        conn = self._get_connection()
        try:
            refs_json = json.dumps(references)

            conn.execute("""
                INSERT OR REPLACE INTO lightweight_assets
                (path, name, asset_type, "references", indexed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (path, name, asset_type, refs_json))
            conn.commit()
            return True
        finally:
            conn.close()

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
        conn = self._get_connection()
        try:
            for asset in assets:
                refs_json = json.dumps(asset.get("references", []))
                conn.execute("""
                    INSERT OR REPLACE INTO lightweight_assets
                    (path, name, asset_type, "references", indexed_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    asset["path"],
                    asset["name"],
                    asset["asset_type"],
                    refs_json,
                ))
            conn.commit()
            return len(assets)
        finally:
            conn.close()

    def get_lightweight_asset(self, path: str) -> Optional[dict]:
        """Get a lightweight asset by path."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM lightweight_assets WHERE path = ?",
                (path,)
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
            # Search in lightweight_assets references
            # Using LIKE for JSON array search (simple but works)
            pattern = f'%"{target_path}"%'
            rows = conn.execute("""
                SELECT path, name, asset_type
                FROM lightweight_assets
                WHERE "references" LIKE ?
                LIMIT ?
            """, (pattern, limit)).fetchall()

            results = [
                {"path": row["path"], "name": row["name"], "asset_type": row["asset_type"]}
                for row in rows
            ]

            # Also check edges table for full semantic docs
            edge_rows = conn.execute("""
                SELECT DISTINCT d.path, d.name, d.asset_type
                FROM edges e
                JOIN docs d ON e.from_id = d.doc_id
                WHERE e.to_id LIKE ?
                LIMIT ?
            """, (f"%{target_path}%", limit - len(results))).fetchall()

            for row in edge_rows:
                if row["path"] not in [r["path"] for r in results]:
                    results.append({
                        "path": row["path"],
                        "name": row["name"],
                        "asset_type": row["asset_type"],
                    })

            return results[:limit]
        finally:
            conn.close()

    def get_lightweight_stats(self) -> dict:
        """Get statistics about lightweight assets."""
        conn = self._get_connection()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM lightweight_assets"
            ).fetchone()[0]

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
