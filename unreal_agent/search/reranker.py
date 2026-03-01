import re

def detect_query_intents(query: str) -> set[str]:
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


def apply_semantic_reranking(results: list[dict], query: str):
    """Apply lightweight intent-aware reranking on semantic results."""
    intents = detect_query_intents(query)
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


def normalize_output_scores(results: list[dict]):
    """Normalize result scores to 0.0-1.0 for consistent MCP output."""
    if not results:
        return
    max_score = max(r["score"] for r in results)
    if max_score > 0:
        for r in results:
            r["score"] = round(r["score"] / max_score, 3)


def result_quality_key(result: dict) -> tuple[float, int, int]:
    """Tie-break key for deduplicating search hits by path."""
    score = float(result.get("score", 0.0))
    r_type = (result.get("type") or "").strip().lower()
    known_type = 1 if r_type and r_type != "unknown" else 0
    has_snippet = 1 if (result.get("snippet") or "").strip() else 0
    return (score, known_type, has_snippet)


def compact_snippet(text: str, max_len: int = 180) -> str:
    """Normalize and trim snippets to keep tool responses compact."""
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:max_len]
