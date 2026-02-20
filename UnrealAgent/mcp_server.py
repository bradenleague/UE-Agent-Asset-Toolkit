"""MCP Server for Unreal Engine Asset Tools.

Two tools:
  - unreal_search: Find assets, code, concepts via semantic/fuzzy/exact search
  - inspect_asset: Get detailed structured data about a specific asset

Requires: Build the index first with `python index.py`

Usage:
    # Run directly (stdio transport)
    python mcp_server.py

    # Add to Claude Desktop config:
    {
        "mcpServers": {
            "unreal": {
                "command": "python",
                "args": ["/path/to/UnrealAgent/mcp_server.py"]
            }
        }
    }
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger("unreal-asset-tools")

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Add project root for package imports (from UnrealAgent.xxx)
# and UnrealAgent/ for local script imports (from tools import ...)
_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir.parent))
sys.path.insert(0, str(_this_dir))

from tools import (
    PROJECT,
    inspect_asset as _raw_inspect,
    get_project_db_path,
    get_active_project_name,
    get_plugin_paths,
)

# Create the MCP server
server = Server("unreal-asset-tools")

# Lazy-loaded retriever
_retriever = None
_store = None
_profile = None
_embedder_attempted = False
_embedder_error = None


def _get_store():
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
        from knowledge_index import KnowledgeStore

        _store = KnowledgeStore(db_path)
    return _store


def _get_profile():
    """Get or create the project profile for the active project."""
    global _profile
    if _profile is None:
        from project_profile import load_profile

        _profile = load_profile()
    return _profile


def _get_retriever(enable_embeddings: bool = False):
    """Get or create the retriever; embeddings are optional and loaded lazily."""
    global _retriever, _embedder_attempted, _embedder_error
    if _retriever is None:
        store = _get_store()
        from knowledge_index import HybridRetriever

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
            from knowledge_index.indexer import create_sentence_transformer_embedder

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


def _detect_query_intents(query: str) -> set[str]:
    """Infer coarse intent for ranking and snippet shaping."""
    q = query.lower()
    intents = set()

    if any(
        t in q
        for t in [
            "blueprint",
            "event",
            "function",
            "graph",
            "logic",
            "node",
            "call",
            "native",
            "c++",
            "cpp",
        ]
    ):
        intents.add("blueprint")
    if re.search(r"\b(bp_|b_)\w+", query, re.IGNORECASE):
        intents.add("blueprint")

    if any(t in q for t in ["widget", "umg", "hud", "ui"]):
        intents.add("widget")
    if re.search(r"\b(wbp_|w_)\w+", query, re.IGNORECASE):
        intents.add("widget")

    if "datatable" in q or re.search(r"\bdt_\w+", query, re.IGNORECASE):
        intents.add("datatable")

    if any(t in q for t in ["material", "shader", "surface", "instance"]):
        intents.add("material")
    if re.search(r"\b(mi_|m_|mf_)\w+", query, re.IGNORECASE):
        intents.add("material")

    if any(
        t in q
        for t in [
            "where is",
            "used",
            "references",
            "depends on",
            "interact",
            "interaction",
        ]
    ):
        intents.add("interaction")

    return intents


def _build_semantic_snippet(doc) -> str:
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


def _apply_semantic_reranking(results: list[dict], query: str):
    """Apply lightweight intent-aware reranking on semantic results."""
    intents = _detect_query_intents(query)
    query_lower = query.lower()
    stop_words = {
        "the",
        "and",
        "or",
        "for",
        "with",
        "from",
        "into",
        "onto",
        "what",
        "when",
        "where",
        "which",
        "that",
        "this",
        "player",
        "level",
        "map",
    }
    query_tokens = [
        tok
        for tok in re.findall(r"[a-z0-9_]+", query_lower)
        if len(tok) >= 4 and tok not in stop_words
    ]

    for r in results:
        base_score = float(r.get("score", 0.0))
        result_type = (r.get("type") or "").lower()
        name = (r.get("name") or "").lower()
        snippet = (r.get("snippet") or "").lower()
        result_text = f"{name} {snippet}"
        boost = 1.0

        if "blueprint" in intents:
            if (
                result_type == "blueprint"
                or "bp_graph" in result_type
                or name.startswith(("bp_", "b_"))
            ):
                boost *= 1.35
            if "material" in result_type:
                boost *= 0.88

        if "widget" in intents:
            if (
                result_type == "widgetblueprint"
                or "widget" in result_type
                or name.startswith(("wbp_", "w_"))
            ):
                boost *= 1.35

        if "datatable" in intents:
            if result_type == "datatable":
                boost *= 1.4
            elif "material" in result_type:
                boost *= 0.9

        if "material" in intents:
            if "material" in result_type or name.startswith(("mi_", "m_", "mf_")):
                boost *= 1.25

        if "interaction" in intents:
            if result_type == "blueprint" or "bp_graph" in result_type:
                boost *= 1.2

        # Demote low-information blueprint summaries that frequently rank as noise
        # for semantic queries (e.g., Parent: Unknown with no callable members).
        if result_type in {"blueprint", "widgetblueprint"}:
            has_unknown_parent = "parent: unknown" in snippet
            has_member_signal = any(
                token in snippet for token in ("functions:", "events:", "variables:")
            )
            if has_unknown_parent and not has_member_signal:
                boost *= 0.6

        # Prefer results that actually mention key query terms.
        if query_tokens:
            overlap = sum(1 for tok in query_tokens if tok in result_text)
            if overlap == 0:
                boost *= 0.65
            elif overlap == 1:
                boost *= 0.9
            else:
                boost *= 1.1

        # Generic guardrail: don't over-rank Save* assets unless query asks for save semantics.
        if "save" in name and not any(
            t in query_lower for t in ("save", "checkpoint", "respawn", "load")
        ):
            boost *= 0.65

        r["score"] = round(base_score * boost, 3)


def _normalize_output_scores(results: list[dict]):
    """Normalize result scores to 0.0-1.0 for consistent MCP output."""
    if not results:
        return
    max_score = max(r["score"] for r in results)
    if max_score > 0:
        for r in results:
            r["score"] = round(r["score"] / max_score, 3)


def _build_token_aliases(token: str) -> list[str]:
    """Build common UE naming aliases for a symbol token."""
    if not token:
        return []
    aliases = [token]
    upper = token.upper()

    def add_alias(value: str):
        if value and value not in aliases:
            aliases.append(value)

    if upper.startswith("BP_"):
        add_alias("B_" + token[3:])
    elif upper.startswith("B_"):
        add_alias("BP_" + token[2:])

    if upper.startswith("WBP_"):
        add_alias("W_" + token[4:])
    elif upper.startswith("W_"):
        add_alias("WBP_" + token[2:])

    return aliases


_VISUAL_ASSET_TYPES = frozenset(
    {
        "Material",
        "MaterialInstance",
        "MaterialFunction",
        "Texture",
        "Texture2D",
        "StaticMesh",
        "SkeletalMesh",
        "Sound",
        "SoundWave",
        "SoundCue",
        "NiagaraSystem",
        "ParticleSystem",
        "Animation",
        "AnimSequence",
        "AnimMontage",
    }
)
_BASE_STRUCTURAL_ASSET_TYPES = frozenset(
    {
        "WidgetBlueprint",
        "Blueprint",
        "DataAsset",
        "DataTable",
        "GameFeatureData",
        "InputAction",
        "InputMappingContext",
    }
)
_structural_asset_types_cache: frozenset | None = None


def _get_structural_asset_types() -> frozenset:
    """Return structural asset types, extended with profile game_feature_types."""
    global _structural_asset_types_cache
    if _structural_asset_types_cache is None:
        try:
            profile = _get_profile()
            extra = set(profile.game_feature_types) | set(profile.semantic_types)
        except Exception:
            extra = set()
        _structural_asset_types_cache = _BASE_STRUCTURAL_ASSET_TYPES | extra
    return _structural_asset_types_cache


def _classify_asset_dep(asset_type: str | None, name: str) -> str:
    """Classify an asset dependency as 'structural' or 'visual'."""
    if asset_type:
        if asset_type in _get_structural_asset_types() or "GameFeature" in asset_type:
            return "structural"
        if asset_type in _VISUAL_ASSET_TYPES:
            return "visual"
    upper = name.upper()
    if upper.startswith(
        ("B_", "BP_", "W_", "WBP_", "DA_", "DT_", "GE_", "GA_", "GCN_")
    ):
        return "structural"
    if upper.startswith(
        ("M_", "MI_", "MF_", "T_", "SM_", "SK_", "A_", "S_", "NS_", "PS_")
    ):
        return "visual"
    return "visual"


def _result_quality_key(result: dict) -> tuple[float, int, int]:
    """Tie-break key for deduplicating search hits by path."""
    score = float(result.get("score", 0.0))
    r_type = (result.get("type") or "").strip().lower()
    known_type = 1 if r_type and r_type != "unknown" else 0
    has_snippet = 1 if (result.get("snippet") or "").strip() else 0
    return (score, known_type, has_snippet)


def _compact_snippet(text: str, max_len: int = 180) -> str:
    """Normalize and trim snippets to keep tool responses compact."""
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:max_len]


def _enrich_results_with_full_docs(results: list[dict], store) -> str:
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


def _should_try_tag_search(query: str) -> bool:
    """Return True if query looks like a GameplayTag (dotted PascalCase).

    Examples that match: InputTag.Ability.Dash, Cosmetic.SurfaceType.Concrete
    Examples that don't: BP_PlayerCharacter, player health widget, W_Healthbar
    """
    # Explicit tag: prefix always matches
    if query.lower().startswith("tag:"):
        return True
    # Dotted PascalCase: at least two segments separated by dots, each starting uppercase
    return bool(re.match(r"^[A-Z][A-Za-z0-9]+(\.[A-Z][A-Za-z0-9]*)+(\.\*)?$", query))


def _extract_trace_target(query: str) -> Optional[str]:
    """Extract target symbol/path from system-trace style questions."""
    query = query.strip()
    if not query:
        return None

    patterns = [
        r"what\s+systems?\s+does\s+(.+?)\s+(?:talk\s+t(?:o|oo)|interact\s+with|use|depend\s+on)\??$",
        r"how\s+does\s+(.+?)\s+work\??$",
        r"trace\s+(.+?)\s+(?:systems?|flow|ownership)\??$",
    ]
    for pattern in patterns:
        m = re.search(pattern, query, re.IGNORECASE)
        if m:
            target = m.group(1).strip(" ?\"'")
            for article in ("the ", "a ", "an "):
                if target.lower().startswith(article):
                    target = target[len(article) :].strip()
                    break
            return target

    # Explicit "systems" phrasing fallback.
    if "system" in query.lower() and "talk" in query.lower():
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query)
        if tokens:
            return tokens[-1]

    return None


def _resolve_asset_paths_by_token(
    store,
    asset_token: str,
    limit: int = 20,
    prefer_asset_types: Optional[list[str]] = None,
    prefer_prefixes: Optional[list[str]] = None,
) -> list[str]:
    """Resolve symbol-like input to concrete asset paths."""
    if not asset_token:
        return []

    if asset_token.startswith("/Game/"):
        return [asset_token]

    aliases = _build_token_aliases(asset_token) or [asset_token]
    # Helpful fallbacks for plain-language tokens like "healthbar".
    simple = asset_token.strip()
    if "/" not in simple and "_" not in simple and len(simple) > 2:
        aliases.extend([f"W_{simple}", f"WBP_{simple}", f"BP_{simple}", f"B_{simple}"])
    aliases = [a for i, a in enumerate(aliases) if a and a not in aliases[:i]]

    prefer_asset_types = prefer_asset_types or []
    prefer_prefixes = [p.lower() for p in (prefer_prefixes or [])]
    alias_lowers = [a.lower() for a in aliases]
    compact_target = (
        simple.replace("_", "").lower()
        if simple
        else asset_token.replace("_", "").lower()
    )

    conn = store._get_connection()
    try:
        exact_placeholders = ",".join("?" * len(alias_lowers))
        exact_params = tuple(alias_lowers + alias_lowers + [max(limit * 3, 20)])
        exact_rows = conn.execute(
            """
            SELECT path, name, asset_type FROM docs
            WHERE path LIKE '/%' AND LOWER(name) IN ({exact_placeholders})
            UNION
            SELECT path, name, asset_type FROM lightweight_assets
            WHERE path LIKE '/%' AND LOWER(name) IN ({exact_placeholders})
            LIMIT ?
            """.format(exact_placeholders=exact_placeholders),
            exact_params,
        ).fetchall()
        target_rows = list(exact_rows)

        if not target_rows:
            like_clauses = " OR ".join(["LOWER(name) LIKE ?"] * len(alias_lowers))
            like_params = [f"%{a}%" for a in alias_lowers]
            like_rows = conn.execute(
                """
                SELECT path, name, asset_type FROM docs
                WHERE path LIKE '/%' AND ({like_clauses})
                UNION
                SELECT path, name, asset_type FROM lightweight_assets
                WHERE path LIKE '/%' AND ({like_clauses})
                LIMIT ?
                """.format(like_clauses=like_clauses),
                tuple(like_params + like_params + [max(limit * 8, 80)]),
            ).fetchall()
            target_rows = list(like_rows)

        def rank_row(row) -> float:
            name = (row["name"] or "").lower()
            asset_type = row["asset_type"] or ""
            path = row["path"] or ""
            score = 0.0
            if name in alias_lowers:
                score += 100.0
            if compact_target and name.replace("_", "") == compact_target:
                score += 45.0
            if any(a in name for a in alias_lowers):
                score += 20.0
            if any(name.startswith(prefix) for prefix in prefer_prefixes):
                score += 12.0
            if asset_type in prefer_asset_types:
                score += 15.0
            if "health" in compact_target and "/UI/Hud/" in path:
                score += 8.0
            if path.startswith("/Game/"):
                score += 2.0
            return score

        ranked = sorted(
            target_rows,
            key=lambda r: (rank_row(r), (r["name"] or "").lower()),
            reverse=True,
        )

        resolved = []
        seen = set()
        for row in ranked:
            path = row["path"]
            if path in seen:
                continue
            seen.add(path)
            resolved.append(path)
            if len(resolved) >= limit:
                break

        return resolved
    finally:
        conn.close()


def _build_ownership_chain(
    conn, target_path: str, target_name: str, max_depth: int = 4
) -> str | None:
    """Walk inbound edges upward from target to build a spawn/ownership chain.

    Returns a formatted string like "A -> B -> Target" or None if no chain found.
    """
    logger.debug(
        "ownership_chain: starting walk from %s (%s)", target_name, target_path
    )
    chain = [target_name]
    current_path = target_path
    visited = {target_path}

    for depth in range(max_depth):
        # Gather candidates from semantic edges
        candidates = []
        sem_rows = conn.execute(
            """
            SELECT DISTINCT d.path, d.name, d.asset_type, d.type
            FROM edges e
            JOIN docs d ON e.from_id = d.doc_id
            WHERE e.to_id = ?
            """,
            (f"asset:{current_path}",),
        ).fetchall()
        for row in sem_rows:
            if row["path"] not in visited:
                candidates.append(row)

        # Gather candidates from lightweight refs
        lw_rows = conn.execute(
            """
            SELECT la.path, la.name, la.asset_type, 'lightweight' AS type
            FROM lightweight_refs lr
            JOIN lightweight_assets la ON lr.asset_path = la.path
            WHERE lr.ref_path = ?
            """,
            (current_path,),
        ).fetchall()
        for row in lw_rows:
            if row["path"] not in visited:
                candidates.append(row)

        logger.debug(
            "ownership_chain: depth=%d, current=%s, candidates=%d (sem=%d, lw=%d)",
            depth,
            current_path,
            len(candidates),
            len(sem_rows),
            len(lw_rows),
        )
        for c in candidates:
            logger.debug(
                "  candidate: %s (type=%s, asset_type=%s)",
                c["name"],
                c["type"],
                c["asset_type"],
            )

        if not candidates:
            break

        # Score candidates: prefer structural types and GameFeature registrations
        def _chain_score(row):
            score = 0.0
            atype = row["asset_type"] or ""
            name = row["name"] or ""
            if atype in _get_structural_asset_types() or "GameFeature" in atype:
                score += 10
            if "GameFeature" in name or "GameFeature" in atype:
                score += 5
            score -= 0.1 * depth
            return score

        best = max(candidates, key=_chain_score)
        logger.debug("  picked: %s (score=%.1f)", best["name"], _chain_score(best))
        visited.add(best["path"])
        chain.append(best["name"] or best["path"].split("/")[-1])
        current_path = best["path"]

    result = " -> ".join(reversed(chain)) if len(chain) > 1 else None
    logger.debug("ownership_chain: result=%s", result)
    return result


def _build_asset_system_trace(
    store, target_path: str, limit: int = 20
) -> tuple[dict, list[dict]]:
    """
    Build a compact trace of systems/assets connected to a target asset path.

    Returns:
        (trace_payload, flattened_results)
    """
    conn = store._get_connection()
    try:
        target_docs = conn.execute(
            """
            SELECT doc_id, type, path, name, asset_type
            FROM docs
            WHERE path = ?
            ORDER BY CASE
                WHEN type = 'asset_summary' THEN 0
                WHEN type = 'umg_widget_tree' THEN 1
                ELSE 2
            END
            """,
            (target_path,),
        ).fetchall()

        target_name = (
            target_docs[0]["name"] if target_docs else target_path.split("/")[-1]
        )
        target_type = (
            (target_docs[0]["asset_type"] or target_docs[0]["type"])
            if target_docs
            else "Unknown"
        )
        source_doc_ids = [row["doc_id"] for row in target_docs]

        systems = []
        structural_deps = []
        visual_deps = []
        unresolved_script_refs = []
        inbound_references = []
        probable_owners = []
        seen_systems = set()
        seen_assets = set()
        seen_inbound = set()

        if source_doc_ids:
            placeholders = ",".join("?" * len(source_doc_ids))
            edge_rows = conn.execute(
                f"""
                SELECT e.to_id, e.edge_type,
                       d.type AS to_type, d.path AS to_path, d.name AS to_name,
                       d.asset_type AS to_asset_type, d.text AS to_text
                FROM edges e
                LEFT JOIN docs d ON e.to_id = d.doc_id
                WHERE e.from_id IN ({placeholders})
                """,
                tuple(source_doc_ids),
            ).fetchall()

            for row in edge_rows:
                to_id = row["to_id"] or ""
                edge_type = row["edge_type"] or "uses_asset"
                to_text = _compact_snippet(row["to_text"] or "")
                if to_id.startswith("asset:"):
                    dep_path = to_id[len("asset:") :]
                    if dep_path == target_path or dep_path in seen_assets:
                        continue
                    seen_assets.add(dep_path)
                    dep_name = row["to_name"] or dep_path.split("/")[-1]
                    dep_type = row["to_asset_type"] or row["to_type"] or "Asset"
                    # Use edge_type if it's a typed edge, otherwise classify by asset
                    if edge_type != "uses_asset":
                        relationship = edge_type
                        score = (
                            1.8
                            if edge_type
                            in (
                                "registers_widget",
                                "adds_component",
                                "uses_layout",
                                "maps_input",
                                "targets_actor",
                            )
                            else 1.5
                        )
                        structural_deps.append(
                            {
                                "path": row["to_path"] or dep_path,
                                "name": dep_name,
                                "type": dep_type,
                                "snippet": to_text or f"Referenced by {target_name}",
                                "score": score,
                                "relationship": relationship,
                            }
                        )
                    else:
                        kind = _classify_asset_dep(row["to_asset_type"], dep_name)
                        if kind == "structural":
                            structural_deps.append(
                                {
                                    "path": row["to_path"] or dep_path,
                                    "name": dep_name,
                                    "type": dep_type,
                                    "snippet": to_text
                                    or f"Referenced by {target_name}",
                                    "score": 1.8,
                                    "relationship": "structural_dependency",
                                }
                            )
                        else:
                            visual_deps.append(
                                {
                                    "path": row["to_path"] or dep_path,
                                    "name": dep_name,
                                    "type": dep_type,
                                    "snippet": to_text
                                    or f"Referenced by {target_name}",
                                    "score": 1.0,
                                    "relationship": "visual_dependency",
                                }
                            )
                    continue

                if to_id.startswith(
                    ("cpp_class:", "cpp_func:", "cpp_prop:", "source:")
                ):
                    if to_id in seen_systems:
                        continue
                    seen_systems.add(to_id)
                    systems.append(
                        {
                            "path": row["to_path"]
                            or to_id.split(":", 1)[-1].split("::")[0],
                            "name": row["to_name"] or to_id.split("::")[-1],
                            "type": row["to_type"] or "CppSymbol",
                            "snippet": to_text or f"System ref from {target_name}",
                            "score": 2.2,
                            "relationship": "system_ref",
                        }
                    )
                    continue

                if to_id.startswith("script:"):
                    script_ref = to_id[len("script:") :]
                    if script_ref.startswith("/Script/"):
                        unresolved_script_refs.append(script_ref)

            # Inbound ownership/reference lookup (semantic docs)
            inbound_rows = conn.execute(
                """
                SELECT DISTINCT d.path, d.name, d.asset_type, d.type, d.text, e.edge_type
                FROM edges e
                JOIN docs d ON e.from_id = d.doc_id
                WHERE e.to_id = ?
                LIMIT ?
                """,
                (f"asset:{target_path}", max(limit, 12)),
            ).fetchall()

            for row in inbound_rows:
                key = row["path"]
                if key in seen_inbound:
                    continue
                seen_inbound.add(key)
                edge_type = row["edge_type"] or "uses_asset"
                # Use typed edge info for better relationship labels
                relationship = edge_type if edge_type != "uses_asset" else "inbound_ref"
                inbound_references.append(
                    {
                        "path": row["path"],
                        "name": row["name"],
                        "type": row["asset_type"] or row["type"] or "Unknown",
                        "snippet": _compact_snippet(
                            row["text"] or f"References {target_name}"
                        ),
                        "score": 1.7,
                        "relationship": relationship,
                    }
                )

        # Inbound lookup from lightweight refs
        lw_rows = conn.execute(
            """
            SELECT la.path, la.name, la.asset_type
            FROM lightweight_refs lr
            JOIN lightweight_assets la ON lr.asset_path = la.path
            WHERE lr.ref_path = ?
            LIMIT ?
            """,
            (target_path, max(limit, 12)),
        ).fetchall()
        for row in lw_rows:
            key = row["path"]
            if key in seen_inbound:
                continue
            seen_inbound.add(key)
            inbound_references.append(
                {
                    "path": row["path"],
                    "name": row["name"],
                    "type": row["asset_type"] or "Unknown",
                    "snippet": f"References {target_path}",
                    "score": 1.6,
                    "relationship": "inbound_ref",
                }
            )

        # If ownership is still unknown, probe likely UI registration callsites.
        if not inbound_references:
            base_token = target_name
            token_variants = [base_token]
            if base_token.upper().startswith("W_"):
                token_variants.append(base_token[2:])
            elif base_token.upper().startswith("WBP_"):
                token_variants.append(base_token[4:])

            owner_rows = []
            for variant in token_variants[:2]:
                like = f"%{variant}%"
                owner_rows.extend(
                    conn.execute(
                        """
                        SELECT type, path, name, text
                        FROM docs
                        WHERE type IN ('source_file', 'cpp_class', 'cpp_func')
                          AND (name LIKE ? OR text LIKE ?)
                        LIMIT 30
                        """,
                        (like, like),
                    ).fetchall()
                )

            _base_rank_terms = [
                "HUD",
                "Widget",
                "UIExtension",
                "AddWidget",
                "ExtensionPoint",
            ]
            try:
                _prof = _get_profile()
                rank_terms = tuple(_base_rank_terms + _prof.widget_rank_terms)
            except Exception:
                rank_terms = tuple(_base_rank_terms)
            seen_owner_paths = set()
            for row in owner_rows:
                text_blob = " ".join(
                    str(v) for v in (row["path"], row["name"], row["text"]) if v
                )
                if not any(term.lower() in text_blob.lower() for term in rank_terms):
                    continue
                if row["path"] in seen_owner_paths:
                    continue
                seen_owner_paths.add(row["path"])
                probable_owners.append(
                    {
                        "path": row["path"],
                        "name": row["name"],
                        "type": row["type"],
                        "snippet": _compact_snippet(row["text"] or ""),
                        "score": 1.2,
                        "relationship": "possible_owner",
                    }
                )
                if len(probable_owners) >= max(4, min(limit, 10)):
                    break

        # Widget ownership is often runtime-registered (not direct asset refs).
        # Provide common UI registration surfaces as fallback context.
        if not probable_owners and str(target_type).lower() == "widgetblueprint":
            _base_fallback = [
                "%GameFeatureAction_AddWidget%",
                "%UIExtensionPointWidget%",
                "%PrimaryGameLayout%",
            ]
            try:
                _prof = _get_profile()
                _all_fallback = _base_fallback + _prof.widget_fallback_patterns
            except Exception:
                _all_fallback = _base_fallback
            or_clauses = " OR ".join("name LIKE ?" for _ in _all_fallback)
            fallback_rows = conn.execute(
                f"""
                SELECT type, path, name, text
                FROM docs
                WHERE type IN ('source_file', 'cpp_class', 'cpp_func')
                  AND ({or_clauses})
                LIMIT 10
                """,
                _all_fallback,
            ).fetchall()
            for row in fallback_rows:
                probable_owners.append(
                    {
                        "path": row["path"],
                        "name": row["name"],
                        "type": row["type"],
                        "snippet": _compact_snippet(row["text"] or ""),
                        "score": 1.1,
                        "relationship": "possible_owner",
                    }
                )

        # Keep response compact and deterministic.
        systems = systems[: max(4, min(limit, 12))]
        structural_deps = structural_deps[: max(4, min(limit, 12))]
        visual_deps = visual_deps[: max(4, min(limit, 12))]
        inbound_references = inbound_references[: max(4, min(limit, 12))]
        probable_owners = probable_owners[: max(4, min(limit, 8))]
        unresolved_script_refs = sorted(set(unresolved_script_refs))[:12]

        ownership_chain = _build_ownership_chain(conn, target_path, target_name)

        trace = {
            "target": {
                "path": target_path,
                "name": target_name,
                "type": target_type,
            },
            "ownership_chain": ownership_chain,
            "systems": systems,
            "possible_owners": probable_owners,
            "inbound_references": inbound_references,
            "structural_dependencies": structural_deps,
            "visual_dependencies": visual_deps,
            "unresolved_script_refs": unresolved_script_refs,
            "note": (
                "Direct owner callsites may be empty when widget attachment is runtime-driven "
                "(HUD layout/extension registration)."
                if not inbound_references
                else None
            ),
        }
        if ownership_chain is None:
            del trace["ownership_chain"]

        logger.debug(
            "trace_raw: target=%s | systems=%d | inbound=%d | owners=%d | structural=%d | visual=%d | chain=%s",
            target_name,
            len(systems),
            len(inbound_references),
            len(probable_owners),
            len(structural_deps),
            len(visual_deps),
            ownership_chain,
        )
        logger.debug("trace_json:\n%s", json.dumps(trace, indent=2))

        flattened = (
            systems
            + inbound_references
            + probable_owners
            + structural_deps
            + visual_deps
        )
        return trace, flattened
    finally:
        conn.close()


# =============================================================================
# Tool: unreal_search
# =============================================================================


def unreal_search(
    query: str,
    search_type: str = "auto",
    asset_types: list[str] = None,
    limit: int = 20,
) -> dict:
    """
    Unified search across the knowledge index.

    Args:
        query: Search query (asset name, concept, or natural language)
        search_type: "auto" (default), "name", "semantic", "refs", or "trace"
        asset_types: Filter by types (Blueprint, WidgetBlueprint, Material, etc.)
        limit: Max results to return

    Returns:
        Structured search results with paths, types, snippets, scores
    """
    # Validate query
    if not query or not query.strip():
        return {
            "query": query,
            "search_type": search_type,
            "count": 0,
            "results": [],
            "error": "Query cannot be empty",
        }

    store = _get_store()

    results = []
    query_mode = search_type

    # Handle explicit tag: prefix
    tag_query = None
    if query.lower().startswith("tag:"):
        tag_query = query[4:].strip()
        query_mode = "tags"
    elif search_type == "tags":
        tag_query = query.strip()
        query_mode = "tags"

    # Auto-detect search type
    if search_type == "auto" and query_mode != "tags":
        if _extract_trace_target(query):
            query_mode = "trace"
        # Check for asset path patterns (including plugin paths like /ShooterCore/, /Lyra/, etc.)
        elif query.startswith("/") and not query.startswith("/Script/"):
            query_mode = "name"
        # Check for common UE asset prefixes.
        elif any(
            query.upper().startswith(p)
            for p in [
                "BP_",
                "B_",
                "ABP_",
                "WBP_",
                "W_",
                "M_",
                "MI_",
                "MF_",
                "DT_",
                "DA_",
                "SK_",
                "SM_",
                "T_",
                "A_",
                "GA_",
                "GE_",
                "GCN_",
            ]
        ):
            query_mode = "name"
        # Check for "where is X used" patterns
        elif "where" in query.lower() and (
            "used" in query.lower() or "placed" in query.lower()
        ):
            query_mode = "refs"
        # Auto-detect GameplayTag patterns (dotted PascalCase) with DB verify
        elif _should_try_tag_search(query):
            tag_results = store.search_by_tag(query, limit=limit)
            if tag_results:
                tag_query = query
                query_mode = "tags"
                # Results already fetched, skip re-query below
            else:
                query_mode = "semantic"
        else:
            query_mode = "semantic"

    # Note: asset_types filtering is done post-retrieval.
    # The retriever's filter only handles single values, not lists.
    type_filters = None
    trace_payload = None

    if query_mode == "tags":
        if tag_query:
            tag_results = store.search_by_tag(tag_query, limit=limit)
            for tr in tag_results:
                results.append(
                    {
                        "path": tr["path"],
                        "name": tr["name"],
                        "type": tr["asset_type"],
                        "snippet": f"Tag: {tr['tag']}",
                        "score": 1.0,
                    }
                )

    elif query_mode == "trace":
        trace_token = _extract_trace_target(query) or query.strip()
        target_paths = _resolve_asset_paths_by_token(
            store,
            trace_token,
            limit=max(limit, 10),
            prefer_asset_types=["WidgetBlueprint", "Blueprint", "DataAsset"],
            prefer_prefixes=["W_", "WBP_", "B_", "BP_"],
        )
        if not target_paths and trace_token.startswith("/Game/"):
            target_paths = [trace_token]

        traces = []
        for path in target_paths[:3]:
            trace, trace_results = _build_asset_system_trace(
                store, path, limit=max(limit, 8)
            )
            traces.append(trace)
            results.extend(trace_results)

        if traces:
            trace_payload = traces[0] if len(traces) == 1 else traces

    elif query_mode == "refs":
        retriever = None  # Not used in refs mode
        # Reference search: find what uses/references an asset
        # Handles: "where is BP_Enemy placed?", "what's in Main_Menu level?"
        # Check for level query pattern ("what's in X level")
        level_match = re.search(r"what'?s?\s+in\s+(\w+)\s*level", query, re.IGNORECASE)
        if level_match:
            level_name = level_match.group(1)
            # Query OFPA files under __ExternalActors__/LevelName/
            # This returns all actors placed in that level
            conn = store._get_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT path, name, asset_type, references
                    FROM lightweight_assets
                    WHERE path LIKE ?
                    LIMIT ?
                """,
                    (f"%__ExternalActors__%{level_name}%", limit),
                ).fetchall()

                for row in rows:
                    refs = json.loads(row["references"]) if row["references"] else []
                    # Get the source blueprint from references
                    source_bp = next(
                        (r for r in refs if "/Game/" in r and "__External" not in r),
                        None,
                    )
                    results.append(
                        {
                            "path": row["path"],
                            "name": row["name"],
                            "type": row["asset_type"],
                            "snippet": f"In level {level_name}"
                            + (f", instance of {source_bp}" if source_bp else ""),
                            "score": 1.0,
                        }
                    )
            finally:
                conn.close()
        else:
            # Asset reference query: "where is BP_X placed/used?"
            match = re.search(
                r"(BP_\w+|B_\w+|WBP_\w+|W_\w+|M_\w+|MI_\w+|MF_\w+|DT_\w+|DA_\w+|ABP_\w+|SK_\w+|SM_\w+|T_\w+|A_\w+|GA_\w+|GE_\w+|GCN_\w+|/Game/[\w/.-]+)",
                query,
                re.IGNORECASE,
            )
            asset_token = match.group(1) if match else None

            # Fallback: support plain-language refs queries like
            # "where is LyraGameMode used" without UE prefix.
            if not asset_token:
                phrase_match = re.search(
                    r"where\s+is\s+(.+?)\s+(?:used|placed|referenced)",
                    query,
                    re.IGNORECASE,
                )
                if phrase_match:
                    asset_token = phrase_match.group(1).strip(" ?\"'")

            # Final fallback for explicit refs mode with a direct token.
            if not asset_token and search_type == "refs":
                asset_token = query.strip()

            if asset_token:
                # Resolve symbol names (e.g., BP_Foo, WBP_HUD) to concrete /Game paths first.
                target_paths = _resolve_asset_paths_by_token(
                    store, asset_token, limit=max(limit, 10)
                )

                # If still unresolved, keep the legacy behavior as a final fallback.
                if not target_paths:
                    target_paths = [asset_token]

                seen_paths = set()
                for target_path in target_paths:
                    if len(results) >= limit:
                        break
                    refs = store.find_assets_referencing(
                        target_path, limit=limit - len(results)
                    )
                    for ref in refs:
                        if ref["path"] in seen_paths:
                            continue
                        seen_paths.add(ref["path"])
                        is_level_placement = "__ExternalActors__" in ref["path"]
                        snippet = (
                            "Placed in level"
                            if is_level_placement
                            else f"References {target_path}"
                        )
                        results.append(
                            {
                                "path": ref["path"],
                                "name": ref["name"],
                                "type": ref["asset_type"],
                                "snippet": snippet,
                                "score": 1.0,
                            }
                        )

    elif query_mode == "name":
        retriever = _get_retriever(enable_embeddings=False)
        # Check if query is a prefix pattern (ends with _)
        # Common UE prefixes: BP_, WBP_, B_, W_, M_, MI_, MF_, T_, SM_, SK_, A_, ABP_, DT_, DA_
        is_prefix_search = query.endswith("_")
        query_lower = query.lower()

        if is_prefix_search:
            # Prefix aliases: map standard UE conventions to project-specific variants
            # This allows BP_ to find B_ assets (Lyra-style) and vice versa
            PREFIX_ALIASES = {
                "BP_": ["BP_", "B_"],  # Blueprint
                "B_": ["B_", "BP_"],
                "WBP_": ["WBP_", "W_"],  # Widget Blueprint
                "W_": ["W_", "WBP_"],
                "SM_": ["SM_", "S_"],  # Static Mesh
                "SK_": ["SK_", "S_"],  # Skeletal Mesh
                "S_": ["S_", "SM_", "SK_"],
            }

            # Get all prefixes to search (original + aliases)
            prefixes_to_search = PREFIX_ALIASES.get(query.upper(), [query])

            # Prefix search: use direct SQL LIKE query (FTS5 doesn't handle prefixes well)
            conn = store._get_connection()
            try:
                for prefix in prefixes_to_search:
                    # Prefix range query is index-friendly (faster than LIKE on large tables).
                    # Example: prefix "B_" matches names in [B_, B_\\uffff)
                    prefix_upper = prefix + "\uffff"

                    # Search both docs and lightweight_assets tables
                    rows = conn.execute(
                        """
                        SELECT DISTINCT path, name, asset_type, text
                        FROM docs
                        WHERE name >= ? AND name < ?
                        UNION
                        SELECT DISTINCT path, name, asset_type, '' as text
                        FROM lightweight_assets
                        WHERE name >= ? AND name < ?
                        LIMIT ?
                    """,
                        (prefix, prefix_upper, prefix, prefix_upper, limit),
                    ).fetchall()
                    for row in rows:
                        results.append(
                            {
                                "path": row[0],
                                "name": row[1],
                                "type": row[2] or "Unknown",
                                "snippet": (row[3] or "")[:200],
                                "score": 1.0,
                            }
                        )
            finally:
                conn.close()
        else:
            # Substring search: use FTS then filter
            bundle = retriever.search_exact(query, filters=type_filters, k=limit * 3)
            for r in bundle:
                if r.doc:
                    # Filter: query must appear in asset name, not just text content
                    if query_lower not in r.doc.name.lower():
                        continue
                    results.append(
                        {
                            "path": r.doc.path,
                            "name": r.doc.name,
                            "type": r.doc.asset_type or r.doc.type,
                            "snippet": r.doc.text[:200] if r.doc.text else "",
                            "score": round(r.score, 3),
                        }
                    )

            # Also search lightweight_assets for name matches (plugin assets, etc.)
            conn = store._get_connection()
            try:
                like_pattern = f"%{query}%"
                lightweight_rows = conn.execute(
                    "SELECT path, name, asset_type FROM lightweight_assets WHERE name LIKE ? LIMIT ?",
                    (like_pattern, limit),
                ).fetchall()
                for row in lightweight_rows:
                    if query_lower in row[1].lower():
                        results.append(
                            {
                                "path": row[0],
                                "name": row[1],
                                "type": row[2] or "Unknown",
                                "snippet": "",
                                "score": 0.9,
                            }
                        )
            finally:
                conn.close()

    else:  # semantic
        # Broad 1-2 token queries (e.g., "player") are high-cardinality and can
        # make vector similarity expensive on large indexes. Route these through
        # exact FTS to keep semantic requests responsive.
        query_words = query.strip().split()
        is_short_keyword_query = len(query_words) <= 2 and not any(
            w in query.lower()
            for w in ["how", "what", "why", "where", "when", "which", "explain"]
        )

        retriever = _get_retriever(enable_embeddings=not is_short_keyword_query)
        # Route short keyword queries through exact search to avoid truncation.
        semantic_query_type = "exact" if is_short_keyword_query else "semantic"
        bundle = retriever.retrieve(
            query=query,
            filters=type_filters,
            k=limit,
            query_type=semantic_query_type,
            allow_semantic_fallback=not is_short_keyword_query,
        )
        semantic_results = bundle.results

        for r in semantic_results[:limit]:
            if r.doc:
                results.append(
                    {
                        "path": r.doc.path,
                        "name": r.doc.name,
                        "type": r.doc.asset_type or r.doc.type,
                        "snippet": _build_semantic_snippet(r.doc),
                        "score": round(r.score, 3),
                    }
                )

    # Filter by asset types if specified
    if asset_types and results:
        results = [r for r in results if r["type"] in asset_types]

    # Deduplicate by path, keeping the best quality entry.
    seen_paths = {}
    for r in results:
        path = r["path"]
        if path not in seen_paths or _result_quality_key(r) > _result_quality_key(
            seen_paths[path]
        ):
            seen_paths[path] = r
    results = list(seen_paths.values())

    if query_mode in ("semantic", "name"):
        _apply_semantic_reranking(results, query)

    # Re-sort after dedup, with quality-aware tie-breaking.
    results.sort(key=lambda x: (x.get("name") or "").lower())
    results.sort(key=_result_quality_key, reverse=True)

    # Normalize scores to 0.0-1.0 for consistent MCP output.
    # Skip trace mode — its fixed scores encode relationship-type info, not relevance.
    if query_mode != "trace":
        _normalize_output_scores(results)

    # Enrich narrow result sets with full doc content so inspect_asset is
    # rarely needed.  Name searches always enrich; semantic only when <=3.
    detail_level = "summary"
    if query_mode == "name":
        detail_level = _enrich_results_with_full_docs(results, store)
    elif query_mode == "semantic" and len(results) <= 3:
        detail_level = _enrich_results_with_full_docs(results, store)

    return {
        "query": query,
        "search_type": query_mode,
        "detail": detail_level,
        "count": len(results),
        "results": results[:limit],
        **({"trace": trace_payload} if query_mode == "trace" and trace_payload else {}),
        **(
            {
                "note": f"Semantic embeddings unavailable; using FTS-only search ({(_embedder_error or '').splitlines()[0]})"
            }
            if query_mode == "semantic" and _embedder_error
            else {}
        ),
    }


# =============================================================================
# Tool: inspect_asset
# =============================================================================


def _is_valid_asset_path(path: str) -> bool:
    """Check if a path is a valid asset path (main content or plugin)."""
    if path.startswith("/Game/"):
        return True

    # Check for plugin paths (e.g., /ShooterCore/, /LyraExampleContent/)
    if path.startswith("/") and not path.startswith("/Script/"):
        parts = path.split("/")
        if len(parts) >= 2:
            mount_point = parts[1]
            plugin_paths = get_plugin_paths()
            return mount_point in plugin_paths

    return False


def _select_fuzzy_match(results: list[dict], query: str) -> dict | None:
    """Select the best fuzzy match from search results, or None if not confident.

    Confidence rules:
    - Name substring match (query in name or vice versa) → accept top result
    - Score gap > 0.15 between top and second result → accept top result
    - Otherwise → reject (ambiguous cluster)
    """
    if not results:
        return None

    top = results[0]
    top_name = (top.get("name") or "").lower()
    query_lower = query.lower()

    # Name substring match — high confidence
    if top_name and (query_lower in top_name or top_name in query_lower):
        return top

    # Single result with no name match — can't assess confidence
    if len(results) < 2:
        return None

    # Score gap check
    gap = top["score"] - results[1]["score"]
    if gap > 0.15:
        return top

    return None


def _indent_xml(elem: ET.Element, level: int = 0) -> None:
    """Add indentation to XML for readability."""
    indent = "\n" + ("  " * level)
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        for child in elem:
            _indent_xml(child, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


def _graph_json_to_xml(graph: dict) -> str:
    """Convert parsed Blueprint graph JSON into XML for MCP output."""
    root = ET.Element("graph")
    name_el = ET.SubElement(root, "name")
    name_el.text = str(graph.get("name") or "")

    for func in graph.get("functions") or []:
        func_el = ET.SubElement(
            root,
            "function",
            {"name": str(func.get("name") or "")},
        )
        for node in func.get("nodes") or []:
            node_attrs = {
                "id": str(node.get("id") or ""),
                "type": str(node.get("type") or ""),
            }
            if node.get("target"):
                node_attrs["target"] = str(node.get("target"))
            node_el = ET.SubElement(func_el, "node", node_attrs)

            for pin in node.get("pins") or []:
                pin_attrs = {
                    "name": str(pin.get("name") or ""),
                    "dir": str(pin.get("dir") or ""),
                    "cat": str(pin.get("cat") or ""),
                }
                if pin.get("sub"):
                    pin_attrs["sub"] = str(pin.get("sub"))
                if pin.get("container"):
                    pin_attrs["container"] = str(pin.get("container"))
                if pin.get("default"):
                    pin_attrs["default"] = str(pin.get("default"))
                to_val = pin.get("to")
                if isinstance(to_val, list):
                    if to_val:
                        pin_attrs["to"] = ",".join(str(v) for v in to_val)
                elif isinstance(to_val, str) and to_val:
                    pin_attrs["to"] = to_val
                ET.SubElement(node_el, "pin", pin_attrs)

    for err in graph.get("errors") or []:
        root.append(ET.Comment(f"error: {json.dumps(err)}"))

    _indent_xml(root)
    return ET.tostring(root, encoding="unicode")


def inspect_asset(
    path_or_query: str,
    fuzzy: bool = False,
    detail: str | None = None,
) -> dict:
    """
    Get detailed structured data about a specific asset.

    Args:
        path_or_query: Asset path (/Game/..., /PluginName/...) or search query if fuzzy=True
        fuzzy: If True, search for the asset first, then inspect top match
        detail: For Blueprints: 'graph' (visual node wiring)

    Returns:
        Type-specific structured data about the asset
    """
    asset_path = path_or_query
    search_result = None

    if fuzzy or not _is_valid_asset_path(path_or_query):
        # Search for the asset first - try name search, fall back to semantic
        search = unreal_search(path_or_query, search_type="name", limit=5)
        if not search["results"]:
            # Name search failed, try semantic search
            search = unreal_search(path_or_query, search_type="semantic", limit=5)
        if not search["results"]:
            return {
                "error": f"No asset found matching '{path_or_query}'",
                "suggestion": "Try a different search term or use the full path",
            }

        match = _select_fuzzy_match(search["results"], path_or_query)
        if match is None:
            closest = [
                {"name": r.get("name"), "path": r.get("path"), "score": r.get("score")}
                for r in search["results"][:3]
            ]
            return {
                "error": f"No confident match for '{path_or_query}'",
                "closest_matches": closest,
            }

        asset_path = match["path"]
        search_result = match

    # Call the raw inspect function
    try:
        raw_result = _raw_inspect(asset_path, summarize=True, type_only=False, detail=detail)

        # Parse the result (it returns a string)
        if isinstance(raw_result, str):
            raw_stripped = raw_result.strip()

            if detail == "graph":
                if raw_stripped.startswith("{"):
                    graph_json = json.loads(raw_result)
                    if "error" in graph_json:
                        result = {"path": asset_path, **graph_json}
                    else:
                        xml_data = _graph_json_to_xml(graph_json)
                        result = {
                            "path": asset_path,
                            "format": "xml",
                            "data": xml_data,
                        }
                elif raw_stripped.startswith("<"):
                    result = {
                        "path": asset_path,
                        "format": "xml",
                        "data": raw_result,
                    }
                else:
                    result = {
                        "path": asset_path,
                        "format": "text",
                        "data": raw_result,
                    }
            elif raw_stripped.startswith("<"):
                # Return as structured XML result
                result = {
                    "path": asset_path,
                    "format": "xml",
                    "data": raw_result,
                }
            elif raw_stripped.startswith("{"):
                # JSON result
                result = json.loads(raw_result)
                result["path"] = asset_path
            else:
                # Plain text
                result = {
                    "path": asset_path,
                    "format": "text",
                    "data": raw_result,
                }
        else:
            result = {"path": asset_path, "data": raw_result}

        # Add search context if we searched first
        if search_result:
            result["matched_from"] = path_or_query
            result["match_score"] = search_result.get("score", 1.0)

        return result

    except Exception as e:
        return {
            "path": asset_path,
            "error": str(e),
        }


# =============================================================================
# MCP Tool Definitions
# =============================================================================


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return available tools."""
    return [
        Tool(
            name="unreal_search",
            description="""Search Unreal project assets and C++ source code.

Finds: Blueprints, Widgets, Materials, DataTables, C++ classes/functions.

Examples:
  - "BP_Player" → exact name match
  - "player health widget" → semantic search for HUD elements
  - "where is BP_Enemy used" → find all references/placements
  - "damage calculation" → find relevant blueprints and C++ code

Returns structured results with paths, types, and relevance scores.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query - asset name, concept, or natural language",
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["auto", "name", "semantic", "refs", "trace", "tags"],
                        "description": "Search mode: auto (default), name (exact), semantic (meaning), refs (find usages), trace (system flow for an asset), tags (GameplayTag lookup)",
                        "default": "auto",
                    },
                    "asset_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by types: Blueprint, WidgetBlueprint, Material, DataTable, CppClass, etc.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="inspect_asset",
            description="""Get detailed information about a specific Unreal asset.

Returns type-specific structured data:
  - Blueprint: parent class, components, functions, variables, events
  - WidgetBlueprint: widget tree hierarchy, bindings
  - Material: parameters (scalar, vector, texture), domain, blend mode
  - DataTable: row structure, columns, sample data

For Blueprints, use detail='graph' for visual node wiring.

Use unreal_search first to find assets, then inspect_asset for details.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "path_or_query": {
                        "type": "string",
                        "description": "Asset path (/Game/...) or search term with fuzzy=true",
                    },
                    "fuzzy": {
                        "type": "boolean",
                        "description": "If true, search for the asset first then inspect top match",
                        "default": False,
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["graph"],
                        "description": "For Blueprints: 'graph' (K2Node visual wiring)",
                    },
                },
                "required": ["path_or_query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "unreal_search":
            result = unreal_search(
                query=arguments.get("query", ""),
                search_type=arguments.get("search_type", "auto"),
                asset_types=arguments.get("asset_types"),
                limit=arguments.get("limit", 20),
            )
        elif name == "inspect_asset":
            result = inspect_asset(
                path_or_query=arguments.get("path_or_query", ""),
                fuzzy=arguments.get("fuzzy", False),
                detail=arguments.get("detail"),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [
            TextContent(type="text", text=json.dumps(result, indent=2, default=str))
        ]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


# =============================================================================
# Resources (project info)
# =============================================================================


@server.list_resources()
async def list_resources():
    """Return project info resource."""
    if PROJECT:
        project_name = os.path.splitext(os.path.basename(PROJECT))[0]
        return [
            {
                "uri": f"unreal://project/{project_name}",
                "name": f"Project: {project_name}",
                "description": "Unreal Engine project",
                "mimeType": "application/json",
            }
        ]
    return []


@server.read_resource()
async def read_resource(uri: str):
    """Read project info."""
    if uri.startswith("unreal://project/") and PROJECT:
        project_dir = os.path.dirname(PROJECT)
        engine_version = "Unknown"
        try:
            with open(PROJECT, "r") as f:
                proj = json.load(f)
                engine_version = proj.get("EngineAssociation", "Unknown")
        except (OSError, json.JSONDecodeError):
            pass

        # Get index stats
        index_stats = {}
        try:
            store = _get_store()
            status = store.get_status()
            index_stats = {
                "semantic_docs": status.total_docs,
                "lightweight_assets": status.lightweight_total,
                "total_indexed": status.total_docs + status.lightweight_total,
            }
        except Exception:
            index_stats = {"status": "not built"}

        return json.dumps(
            {
                "name": os.path.splitext(os.path.basename(PROJECT))[0],
                "project_file": PROJECT,
                "engine_version": engine_version,
                "index": index_stats,
            },
            indent=2,
        )

    return json.dumps({"error": f"Unknown resource: {uri}"})


# =============================================================================
# Main
# =============================================================================


async def main():
    """Run the MCP server."""
    import time

    # Enable debug logging when UNREAL_MCP_DEBUG is set
    if os.environ.get("UNREAL_MCP_DEBUG"):
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s %(levelname)s: %(message)s",
            stream=sys.stderr,
        )
    else:
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    project_name = get_active_project_name() or "(not configured)"
    print("Unreal Asset Tools MCP Server", file=sys.stderr)
    print(f"Project: {project_name}", file=sys.stderr)
    print("Tools: unreal_search, inspect_asset", file=sys.stderr)

    # Check if index exists for active project
    db_path = Path(get_project_db_path())
    if db_path.exists():
        print(f"Index: {db_path}", file=sys.stderr)
        # Warm up retriever
        print("Loading search index...", file=sys.stderr)
        t0 = time.time()
        try:
            retriever = _get_retriever(enable_embeddings=False)
            if retriever.embed_fn:
                _ = retriever.embed_fn("warmup")  # Load embedding model
            print(f"Ready ({time.time() - t0:.1f}s)", file=sys.stderr)
        except Exception as e:
            print(f"Warning: {e}", file=sys.stderr)
    else:
        print(
            "Warning: No index found. Run 'python index.py' first.",
            file=sys.stderr,
        )

    # Run server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
