import json
import re
from typing import Optional

from .retriever import get_store, get_retriever_instance, get_embedder_error, enrich_results_with_full_docs, build_semantic_snippet
from .reranker import result_quality_key, apply_semantic_reranking, normalize_output_scores
from .trace import extract_trace_target, resolve_asset_paths_by_token, build_asset_system_trace, should_try_tag_search

_INHERITS_RE = re.compile(
    r"(?:what\s+)?(?:inherits?\s+from|subclass(?:es)?\s+of|children\s+of|class(?:es)?\s+extending)\s+(.+)",
    re.IGNORECASE,
)


def _extract_inherits_target(query: str) -> Optional[str]:
    """Extract the parent class/asset from an inheritance-style query."""
    m = _INHERITS_RE.search(query.strip())
    if m:
        target = m.group(1).strip(" ?\"'")
        for article in ("the ", "a ", "an "):
            if target.lower().startswith(article):
                target = target[len(article) :].strip()
                break
        return target
    return None


def _normalize_ue_path(path: str) -> str:
    """Normalize UE object-style paths to package paths."""
    if not path or not path.startswith("/"):
        return path
    if path.startswith("/Script/"):
        return path
    if "." in path.split("/")[-1]:
        path = path.rsplit(".", 1)[0]
    if path.endswith("_C"):
        path = path[:-2]
    return path


def _normalize_inherits_target_token(token: str) -> tuple[str, str]:
    """Normalize inherits target input and derive a class-like bare name."""
    normalized = token.strip(" ?\"'")

    while normalized.lower().startswith(("class:", "asset:")):
        normalized = normalized.split(":", 1)[1].strip()

    if normalized.startswith("/"):
        normalized = _normalize_ue_path(normalized)

    bare_name = normalized.split("/")[-1] if normalized else ""
    if normalized.startswith("/Script/") and "." in bare_name:
        bare_name = bare_name.split(".")[-1]
    elif normalized.startswith("/") and "." in bare_name:
        bare_name = bare_name.rsplit(".", 1)[0]
    elif "." in bare_name:
        bare_name = bare_name.split(".")[-1]

    if bare_name.endswith("_C"):
        bare_name = bare_name[:-2]

    return normalized, bare_name


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
        search_type: "auto" (default), "name", "semantic", "refs", "trace", "tags", or "inherits"
        asset_types: Filter by types (Blueprint, WidgetBlueprint, Material, etc.)
        limit: Max results to return

    Returns:
        Structured search results with paths, types, snippets, scores
    """
    if not query or not query.strip():
        return {
            "query": query,
            "search_type": search_type,
            "count": 0,
            "results": [],
            "error": "Query cannot be empty",
        }

    if query.startswith("class:"):
        query = query[6:]
    elif query.startswith("asset:"):
        query = query[6:]

    store = get_store()
    results = []
    query_mode = search_type

    tag_query = None
    if query.lower().startswith("tag:"):
        tag_query = query[4:].strip()
        query_mode = "tags"
    elif search_type == "tags":
        tag_query = query.strip()
        query_mode = "tags"

    if search_type == "auto" and query_mode != "tags":
        if _extract_inherits_target(query):
            query_mode = "inherits"
        elif extract_trace_target(query):
            query_mode = "trace"
        elif query.startswith("/") and not query.startswith("/Script/"):
            query_mode = "name"
        elif any(
            query.upper().startswith(p)
            for p in [
                "BP_", "B_", "ABP_", "WBP_", "W_", "M_", "MI_", "MF_",
                "DT_", "DA_", "SK_", "SM_", "T_", "A_", "GA_", "GE_", "GCN_",
            ]
        ):
            query_mode = "name"
        elif "where" in query.lower() and (
            "used" in query.lower() or "placed" in query.lower()
        ):
            query_mode = "refs"
        elif should_try_tag_search(query):
            tag_results = store.search_by_tag(query, limit=limit)
            if tag_results:
                tag_query = query
                query_mode = "tags"
            else:
                query_mode = "semantic"
        else:
            query_mode = "semantic"

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

    elif query_mode == "inherits":
        inherits_token = _extract_inherits_target(query) or query.strip()
        inherits_token, bare_name = _normalize_inherits_target_token(inherits_token)

        parent_ids: list[str] = []
        if bare_name:
            parent_ids.append(f"class:{bare_name}")

        if inherits_token.startswith("/") and not inherits_token.startswith("/Script/"):
            target_paths = [_normalize_ue_path(inherits_token)]
        else:
            target_paths = resolve_asset_paths_by_token(store, inherits_token, limit=5)

        for target_path in target_paths:
            if not target_path.startswith("/Script/"):
                parent_ids.append(f"asset:{target_path}")

        parent_ids = list(dict.fromkeys(parent_ids))
        children = store.find_children_of(parent_ids, max_depth=4) if parent_ids else []
        parent_display = bare_name or inherits_token or "parent"
        for child in children:
            results.append(
                {
                    "path": child["path"],
                    "name": child["name"],
                    "type": child["asset_type"] or "Unknown",
                    "snippet": f"Inherits from {parent_display} (depth {child['depth']})",
                    "score": round(1.0 / child["depth"], 3),
                }
            )

    elif query_mode == "trace":
        trace_token = extract_trace_target(query) or query.strip()
        target_paths = resolve_asset_paths_by_token(
            store,
            trace_token,
            limit=max(limit, 10),
            prefer_asset_types=["WidgetBlueprint", "Blueprint", "DataAsset"],
            prefer_prefixes=["W_", "WBP_", "B_", "BP_"],
        )
        if not target_paths and trace_token.startswith("/"):
            target_paths = [_normalize_ue_path(trace_token)]

        traces = []
        for path in target_paths[:3]:
            trace, trace_results = build_asset_system_trace(
                store, path, limit=max(limit, 8)
            )
            traces.append(trace)
            results.extend(trace_results)

        if traces:
            trace_payload = traces[0] if len(traces) == 1 else traces

    elif query_mode == "refs":
        level_match = re.search(r"what'?s?\s+in\s+(\w+)\s*level", query, re.IGNORECASE)
        if level_match:
            level_name = level_match.group(1)
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
            match = re.search(
                r"(BP_\w+|B_\w+|WBP_\w+|W_\w+|M_\w+|MI_\w+|MF_\w+|DT_\w+|DA_\w+|ABP_\w+|SK_\w+|SM_\w+|T_\w+|A_\w+|GA_\w+|GE_\w+|GCN_\w+|/Game/[\w/.-]+)",
                query,
                re.IGNORECASE,
            )
            asset_token = match.group(1) if match else None

            if not asset_token:
                phrase_match = re.search(
                    r"where\s+is\s+(.+?)\s+(?:used|placed|referenced)",
                    query,
                    re.IGNORECASE,
                )
                if phrase_match:
                    asset_token = phrase_match.group(1).strip(" ?\"'")

            if not asset_token and search_type == "refs":
                asset_token = query.strip()

            if asset_token:
                target_paths = resolve_asset_paths_by_token(
                    store, asset_token, limit=max(limit, 10)
                )

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
        retriever = get_retriever_instance(enable_embeddings=False)
        is_prefix_search = query.endswith("_")
        query_lower = query.lower()

        if is_prefix_search:
            PREFIX_ALIASES = {
                "BP_": ["BP_", "B_"],
                "B_": ["B_", "BP_"],
                "WBP_": ["WBP_", "W_"],
                "W_": ["W_", "WBP_"],
                "SM_": ["SM_", "S_"],
                "SK_": ["SK_", "S_"],
                "S_": ["S_", "SM_", "SK_"],
            }
            prefixes_to_search = PREFIX_ALIASES.get(query.upper(), [query])

            conn = store._get_connection()
            try:
                for prefix in prefixes_to_search:
                    prefix_upper = prefix + "\uffff"

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
            bundle = retriever.search_exact(query, filters=type_filters, k=limit * 3)
            for r in bundle:
                if r.doc:
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

            conn = store._get_connection()
            try:
                like_pattern = f"%{query}%"
                lw_sql = "SELECT path, name, asset_type FROM lightweight_assets WHERE name LIKE ?"
                lw_params: list = [like_pattern]
                if asset_types:
                    at_lower = {t.lower() for t in asset_types}
                    at_placeholders = ",".join("?" * len(at_lower))
                    lw_sql += f" AND LOWER(asset_type) IN ({at_placeholders})"
                    lw_params.extend(at_lower)
                lw_sql += " LIMIT ?"
                lw_params.append(limit)
                lightweight_rows = conn.execute(lw_sql, lw_params).fetchall()
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

    else:
        query_words = query.strip().split()
        is_short_keyword_query = len(query_words) <= 2 and not any(
            w in query.lower()
            for w in ["how", "what", "why", "where", "when", "which", "explain"]
        )

        retriever = get_retriever_instance(enable_embeddings=not is_short_keyword_query)
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
                        "snippet": build_semantic_snippet(r.doc),
                        "score": round(r.score, 3),
                    }
                )

    if asset_types and results:
        asset_types_lower = {t.lower() for t in asset_types}
        results = [
            r for r in results if (r.get("type") or "").lower() in asset_types_lower
        ]

    seen_paths = {}
    for r in results:
        path = r["path"]
        if path not in seen_paths or result_quality_key(r) > result_quality_key(
            seen_paths[path]
        ):
            seen_paths[path] = r
    results = list(seen_paths.values())

    if query_mode in ("semantic", "name"):
        apply_semantic_reranking(results, query)

    results.sort(key=lambda x: (x.get("name") or "").lower())
    results.sort(key=result_quality_key, reverse=True)

    if query_mode != "trace":
        normalize_output_scores(results)

    detail_level = "summary"
    if query_mode == "name":
        detail_level = enrich_results_with_full_docs(results, store)
    elif query_mode == "semantic" and len(results) <= 3:
        detail_level = enrich_results_with_full_docs(results, store)

    embedder_error = get_embedder_error()
    return {
        "query": query,
        "search_type": query_mode,
        "detail": detail_level,
        "count": len(results),
        "results": results[:limit],
        **({"trace": trace_payload} if query_mode == "trace" and trace_payload else {}),
        **(
            {
                "note": f"Semantic embeddings unavailable; using FTS-only search ({(embedder_error or '').splitlines()[0]})"
            }
            if query_mode == "semantic" and embedder_error
            else {}
        ),
    }
