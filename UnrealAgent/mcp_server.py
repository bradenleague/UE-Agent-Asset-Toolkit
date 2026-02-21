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

logger = logging.getLogger("unreal-asset-tools")

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Add project root for package imports (from UnrealAgent.xxx)
# and UnrealAgent/ for local script imports (from tools import ...)
_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir.parent))
sys.path.insert(0, str(_this_dir))

from core import (
    PROJECT,
    get_project_db_path,
    get_active_project_name,
    get_plugin_paths,
)
from assets import inspect_asset as _raw_inspect
from search import unreal_search, get_store, get_retriever_instance

# Create the MCP server
server = Server("unreal-asset-tools")


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
    - Exact name match (case-insensitive) → return immediately
    - Name substring match (query in name or vice versa) → accept top result
    - Score gap > 0.15 between top and second result → accept top result
    - Otherwise → reject (ambiguous cluster)
    """
    if not results:
        return None

    query_lower = query.lower()

    # Exact name match — highest confidence, prevents longer names from winning
    for r in results:
        if (r.get("name") or "").lower() == query_lower:
            return r

    top = results[0]
    top_name = (top.get("name") or "").lower()

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
        raw_result = _raw_inspect(
            asset_path, summarize=True, type_only=False, detail=detail
        )

        # Parse the result (it returns a string)
        if isinstance(raw_result, str):
            raw_stripped = raw_result.strip()

            if raw_stripped.startswith("<"):
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
                        "enum": [
                            "auto",
                            "name",
                            "semantic",
                            "refs",
                            "trace",
                            "tags",
                            "inherits",
                        ],
                        "description": "Search mode: auto (default), name (exact), semantic (meaning), refs (find usages), trace (system flow for an asset), tags (GameplayTag lookup), inherits (find subclasses/children)",
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
            store = get_store()
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
            retriever = get_retriever_instance(enable_embeddings=False)
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
