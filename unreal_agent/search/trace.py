import re
import json
import logging
from typing import Optional

from .retriever import get_profile
from .reranker import compact_snippet

logger = logging.getLogger("unreal-asset-tools")

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


def build_token_aliases(token: str) -> list[str]:
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


def get_structural_asset_types() -> frozenset:
    """Return structural asset types, extended with profile game_feature_types."""
    global _structural_asset_types_cache
    if _structural_asset_types_cache is None:
        try:
            profile = get_profile()
            extra = set(profile.game_feature_types) | set(profile.semantic_types)
        except Exception:
            extra = set()
        _structural_asset_types_cache = _BASE_STRUCTURAL_ASSET_TYPES | extra
    return _structural_asset_types_cache


def classify_asset_dep(asset_type: str | None, name: str) -> str:
    """Classify an asset dependency as 'structural' or 'visual'."""
    if asset_type:
        if asset_type in get_structural_asset_types() or "GameFeature" in asset_type:
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


def should_try_tag_search(query: str) -> bool:
    """Return True if query looks like a GameplayTag (dotted PascalCase)."""
    if query.lower().startswith("tag:"):
        return True
    return bool(re.match(r"^[A-Z][A-Za-z0-9]+(\.[A-Z][A-Za-z0-9]*)+(\.\*)?$", query))


def extract_trace_target(query: str) -> Optional[str]:
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


def resolve_asset_paths_by_token(
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

    aliases = build_token_aliases(asset_token) or [asset_token]
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


def build_ownership_chain(
    conn, target_path: str, target_name: str, max_depth: int = 4
) -> str | None:
    """Walk inbound edges upward from target to build a spawn/ownership chain."""
    logger.debug(
        "ownership_chain: starting walk from %s (%s)", target_name, target_path
    )
    chain = [target_name]
    current_path = target_path
    visited = {target_path}

    for depth in range(max_depth):
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

        if not candidates:
            break

        def _chain_score(row):
            score = 0.0
            atype = row["asset_type"] or ""
            name = row["name"] or ""
            if atype in get_structural_asset_types() or "GameFeature" in atype:
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


def build_asset_system_trace(
    store, target_path: str, limit: int = 20
) -> tuple[dict, list[dict]]:
    """Build a compact trace of systems/assets connected to a target asset path."""
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
                to_text = compact_snippet(row["to_text"] or "")
                if to_id.startswith("asset:"):
                    dep_path = to_id[len("asset:") :]
                    if dep_path == target_path or dep_path in seen_assets:
                        continue
                    seen_assets.add(dep_path)
                    dep_name = row["to_name"] or dep_path.split("/")[-1]
                    dep_type = row["to_asset_type"] or row["to_type"] or "Asset"
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
                        kind = classify_asset_dep(row["to_asset_type"], dep_name)
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

                if to_id.startswith("script:"):
                    script_ref = to_id[len("script:") :]
                    if script_ref.startswith("/Script/"):
                        unresolved_script_refs.append(script_ref)

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
                relationship = edge_type if edge_type != "uses_asset" else "inbound_ref"
                inbound_references.append(
                    {
                        "path": row["path"],
                        "name": row["name"],
                        "type": row["asset_type"] or row["type"] or "Unknown",
                        "snippet": compact_snippet(
                            row["text"] or f"References {target_name}"
                        ),
                        "score": 1.7,
                        "relationship": relationship,
                    }
                )

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

        if not inbound_references:
            base_token = target_name
            token_variants = [base_token]
            if base_token.upper().startswith("W_"):
                token_variants.append(base_token[2:])
            elif base_token.upper().startswith("WBP_"):
                token_variants.append(base_token[4:])

            owner_rows = []

            _base_rank_terms = [
                "HUD",
                "Widget",
                "UIExtension",
                "AddWidget",
                "ExtensionPoint",
            ]
            try:
                _prof = get_profile()
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
                        "snippet": compact_snippet(row["text"] or ""),
                        "score": 1.2,
                        "relationship": "possible_owner",
                    }
                )
                if len(probable_owners) >= max(4, min(limit, 10)):
                    break

        systems = systems[: max(4, min(limit, 12))]
        structural_deps = structural_deps[: max(4, min(limit, 12))]
        visual_deps = visual_deps[: max(4, min(limit, 12))]
        inbound_references = inbound_references[: max(4, min(limit, 12))]
        probable_owners = probable_owners[: max(4, min(limit, 8))]
        unresolved_script_refs = sorted(set(unresolved_script_refs))[:12]

        ownership_chain = build_ownership_chain(conn, target_path, target_name)

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
