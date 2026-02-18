"""MCP Server for Unreal Engine Asset Tools.

Two tools:
  - unreal_search: Find assets, code, concepts via semantic/fuzzy/exact search
  - inspect_asset: Get detailed structured data about a specific asset

Requires: Build the index first with `python index.py --all`

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
import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from tools import PROJECT, inspect_asset as _raw_inspect, get_project_db_path, get_active_project_name, get_plugin_paths

# Create the MCP server
server = Server("unreal-asset-tools")

# Lazy-loaded retriever
_retriever = None
_store = None


def _get_store():
    """Get or create the knowledge store for the active project."""
    global _store
    if _store is None:
        db_path = Path(get_project_db_path())
        if not db_path.exists():
            project = get_active_project_name() or "unknown"
            raise RuntimeError(
                f"Knowledge index not found for project '{project}' at {db_path}. "
                "Run 'python index.py --all' first."
            )
        from knowledge_index import KnowledgeStore
        _store = KnowledgeStore(db_path)
    return _store


def _get_retriever():
    """Get or create the retriever with embeddings."""
    global _retriever
    if _retriever is None:
        store = _get_store()
        from knowledge_index import HybridRetriever
        from knowledge_index.indexer import create_sentence_transformer_embedder

        # Try to load embedding function
        embed_fn = None
        try:
            embed_fn = create_sentence_transformer_embedder()
        except ImportError:
            pass  # Will fall back to FTS-only search

        _retriever = HybridRetriever(store, embed_fn=embed_fn)
    return _retriever


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
        search_type: "auto" (default), "name", "semantic", or "refs"
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
    retriever = _get_retriever()

    results = []
    query_mode = search_type

    # Auto-detect search type
    if search_type == "auto":
        # Check for asset path patterns (including plugin paths like /ShooterCore/, /Lyra/, etc.)
        if query.startswith("/") and not query.startswith("/Script/"):
            query_mode = "name"
        # Check for common prefixes (BP_, WBP_, M_, etc.)
        elif any(query.upper().startswith(p) for p in ["BP_", "WBP_", "M_", "MI_", "DT_", "DA_"]):
            query_mode = "name"
        # Check for "where is X used" patterns
        elif "where" in query.lower() and ("used" in query.lower() or "placed" in query.lower()):
            query_mode = "refs"
        else:
            query_mode = "semantic"

    # Note: asset_types filtering is done post-retrieval (line ~210)
    # The retriever's filter only handles single values, not lists
    type_filters = None

    if query_mode == "refs":
        # Reference search: find what uses/references an asset
        # Handles: "where is BP_Enemy placed?", "what's in Main_Menu level?"
        import re

        # Check for level query pattern ("what's in X level")
        level_match = re.search(r"what'?s?\s+in\s+(\w+)\s*level", query, re.IGNORECASE)
        if level_match:
            level_name = level_match.group(1)
            # Query OFPA files under __ExternalActors__/LevelName/
            # This returns all actors placed in that level
            conn = store._get_connection()
            try:
                rows = conn.execute("""
                    SELECT path, name, asset_type, references
                    FROM lightweight_assets
                    WHERE path LIKE ?
                    LIMIT ?
                """, (f"%__ExternalActors__%{level_name}%", limit)).fetchall()

                for row in rows:
                    refs = json.loads(row["references"]) if row["references"] else []
                    # Get the source blueprint from references
                    source_bp = next((r for r in refs if "/Game/" in r and "__External" not in r), None)
                    results.append({
                        "path": row["path"],
                        "name": row["name"],
                        "type": row["asset_type"],
                        "snippet": f"In level {level_name}" + (f", instance of {source_bp}" if source_bp else ""),
                        "score": 1.0,
                    })
            finally:
                conn.close()
        else:
            # Asset reference query: "where is BP_X placed/used?"
            match = re.search(r'(BP_\w+|WBP_\w+|M_\w+|MI_\w+|DT_\w+|DA_\w+|/Game/[\w/]+)', query, re.IGNORECASE)
            if match:
                asset_name = match.group(1)
                # Search lightweight_assets for references to this asset
                refs = store.find_assets_referencing(asset_name, limit=limit)

                for ref in refs:
                    # Check if this is an OFPA file (level placement)
                    is_level_placement = "__ExternalActors__" in ref["path"]
                    snippet = f"Placed in level" if is_level_placement else f"References {asset_name}"
                    results.append({
                        "path": ref["path"],
                        "name": ref["name"],
                        "type": ref["asset_type"],
                        "snippet": snippet,
                        "score": 1.0,
                    })

    elif query_mode == "name":
        # Check if query is a prefix pattern (ends with _)
        # Common UE prefixes: BP_, WBP_, B_, W_, M_, MI_, MF_, T_, SM_, SK_, A_, ABP_, DT_, DA_
        is_prefix_search = query.endswith("_")
        query_lower = query.lower()

        if is_prefix_search:
            # Prefix aliases: map standard UE conventions to project-specific variants
            # This allows BP_ to find B_ assets (Lyra-style) and vice versa
            PREFIX_ALIASES = {
                "BP_": ["BP_", "B_"],      # Blueprint
                "B_": ["B_", "BP_"],
                "WBP_": ["WBP_", "W_"],    # Widget Blueprint
                "W_": ["W_", "WBP_"],
                "SM_": ["SM_", "S_"],      # Static Mesh
                "SK_": ["SK_", "S_"],      # Skeletal Mesh
                "S_": ["S_", "SM_", "SK_"],
            }

            # Get all prefixes to search (original + aliases)
            prefixes_to_search = PREFIX_ALIASES.get(query.upper(), [query])

            # Prefix search: use direct SQL LIKE query (FTS5 doesn't handle prefixes well)
            conn = store._get_connection()
            try:
                for prefix in prefixes_to_search:
                    # Escape _ for LIKE pattern and add wildcard
                    like_pattern = prefix.replace("_", "\\_") + "%"
                    # Search both docs and lightweight_assets tables
                    rows = conn.execute("""
                        SELECT DISTINCT path, name, asset_type, text FROM docs WHERE name LIKE ? ESCAPE '\\'
                        UNION
                        SELECT DISTINCT path, name, asset_type, '' as text FROM lightweight_assets WHERE name LIKE ? ESCAPE '\\'
                        LIMIT ?
                    """, (like_pattern, like_pattern, limit)).fetchall()
                    for row in rows:
                        results.append({
                            "path": row[0],
                            "name": row[1],
                            "type": row[2] or "Unknown",
                            "snippet": (row[3] or "")[:200],
                            "score": 1.0,
                        })
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
                    results.append({
                        "path": r.doc.path,
                        "name": r.doc.name,
                        "type": r.doc.asset_type or r.doc.type,
                        "snippet": r.doc.text[:200] if r.doc.text else "",
                        "score": round(r.score, 3),
                    })

            # Also search lightweight_assets for name matches (plugin assets, etc.)
            conn = store._get_connection()
            try:
                like_pattern = f"%{query}%"
                lightweight_rows = conn.execute(
                    "SELECT path, name, asset_type FROM lightweight_assets WHERE name LIKE ? LIMIT ?",
                    (like_pattern, limit)
                ).fetchall()
                for row in lightweight_rows:
                    if query_lower in row[1].lower():
                        results.append({
                            "path": row[0],
                            "name": row[1],
                            "type": row[2] or "Unknown",
                            "snippet": "",
                            "score": 0.9,
                        })
            finally:
                conn.close()

    else:  # semantic
        # Full semantic search (FTS + vector if available)
        bundle = retriever.retrieve(query=query, filters=type_filters, k=limit)
        for r in bundle.results[:limit]:
            if r.doc:
                results.append({
                    "path": r.doc.path,
                    "name": r.doc.name,
                    "type": r.doc.asset_type or r.doc.type,
                    "snippet": r.doc.text[:200] if r.doc.text else "",
                    "score": round(r.score, 3),
                })

    # Filter by asset types if specified
    if asset_types and results:
        results = [r for r in results if r["type"] in asset_types]

    # Deduplicate by path, keeping highest scoring entry
    seen_paths = {}
    for r in results:
        path = r["path"]
        if path not in seen_paths or r["score"] > seen_paths[path]["score"]:
            seen_paths[path] = r
    results = list(seen_paths.values())
    # Re-sort by score after dedup
    results.sort(key=lambda x: x["score"], reverse=True)

    return {
        "query": query,
        "search_type": query_mode,
        "count": len(results),
        "results": results[:limit],
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


def inspect_asset(
    path_or_query: str,
    fuzzy: bool = False,
) -> dict:
    """
    Get detailed structured data about a specific asset.

    Args:
        path_or_query: Asset path (/Game/..., /PluginName/...) or search query if fuzzy=True
        fuzzy: If True, search for the asset first, then inspect top match

    Returns:
        Type-specific structured data about the asset
    """
    asset_path = path_or_query
    search_result = None

    if fuzzy or not _is_valid_asset_path(path_or_query):
        # Search for the asset first - try name search, fall back to semantic
        search = unreal_search(path_or_query, search_type="name", limit=1)
        if not search["results"]:
            # Name search failed, try semantic search
            search = unreal_search(path_or_query, search_type="semantic", limit=1)
        if search["results"]:
            asset_path = search["results"][0]["path"]
            search_result = search["results"][0]
        else:
            return {
                "error": f"No asset found matching '{path_or_query}'",
                "suggestion": "Try a different search term or use the full path",
            }

    # Call the raw inspect function
    try:
        raw_result = _raw_inspect(asset_path, summarize=True, type_only=False)

        # Parse the result (it returns a string)
        if isinstance(raw_result, str):
            # Try to detect if it's XML or text
            if raw_result.strip().startswith("<"):
                # Return as structured XML result
                result = {
                    "path": asset_path,
                    "format": "xml",
                    "data": raw_result,
                }
            elif raw_result.strip().startswith("{"):
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
                        "description": "Search query - asset name, concept, or natural language"
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["auto", "name", "semantic", "refs"],
                        "description": "Search mode: auto (default), name (exact), semantic (meaning), refs (find usages)",
                        "default": "auto"
                    },
                    "asset_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by types: Blueprint, WidgetBlueprint, Material, DataTable, CppClass, etc."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20)",
                        "default": 20
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="inspect_asset",
            description="""Get detailed information about a specific Unreal asset.

Returns type-specific structured data:
  - Blueprint: parent class, components, functions, variables, events
  - WidgetBlueprint: widget tree hierarchy, bindings
  - Material: parameters (scalar, vector, texture), domain, blend mode
  - DataTable: row structure, columns, sample data

Use unreal_search first to find assets, then inspect_asset for details.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "path_or_query": {
                        "type": "string",
                        "description": "Asset path (/Game/...) or search term with fuzzy=true"
                    },
                    "fuzzy": {
                        "type": "boolean",
                        "description": "If true, search for the asset first then inspect top match",
                        "default": False
                    }
                },
                "required": ["path_or_query"]
            }
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
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2, default=str)
        )]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)})
        )]


# =============================================================================
# Resources (project info)
# =============================================================================

@server.list_resources()
async def list_resources():
    """Return project info resource."""
    if PROJECT:
        project_name = os.path.splitext(os.path.basename(PROJECT))[0]
        return [{
            "uri": f"unreal://project/{project_name}",
            "name": f"Project: {project_name}",
            "description": f"Unreal Engine project",
            "mimeType": "application/json"
        }]
    return []


@server.read_resource()
async def read_resource(uri: str):
    """Read project info."""
    if uri.startswith("unreal://project/") and PROJECT:
        project_dir = os.path.dirname(PROJECT)
        engine_version = "Unknown"
        try:
            with open(PROJECT, 'r') as f:
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

        return json.dumps({
            "name": os.path.splitext(os.path.basename(PROJECT))[0],
            "project_file": PROJECT,
            "engine_version": engine_version,
            "index": index_stats,
        }, indent=2)

    return json.dumps({"error": f"Unknown resource: {uri}"})


# =============================================================================
# Main
# =============================================================================

async def main():
    """Run the MCP server."""
    import time

    project_name = get_active_project_name() or "(not configured)"
    print(f"Unreal Asset Tools MCP Server", file=sys.stderr)
    print(f"Project: {project_name}", file=sys.stderr)
    print(f"Tools: unreal_search, inspect_asset", file=sys.stderr)

    # Check if index exists for active project
    db_path = Path(get_project_db_path())
    if db_path.exists():
        print(f"Index: {db_path}", file=sys.stderr)
        # Warm up retriever
        print("Loading search index...", file=sys.stderr)
        t0 = time.time()
        try:
            retriever = _get_retriever()
            if retriever.embed_fn:
                _ = retriever.embed_fn("warmup")  # Load embedding model
            print(f"Ready ({time.time() - t0:.1f}s)", file=sys.stderr)
        except Exception as e:
            print(f"Warning: {e}", file=sys.stderr)
    else:
        print(f"Warning: No index found. Run 'python index.py --all' first.", file=sys.stderr)

    # Run server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
