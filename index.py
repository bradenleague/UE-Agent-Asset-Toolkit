#!/usr/bin/env python3
"""
UE Asset Toolkit - Index Management

Root-level wrapper for indexing and project management.

Usage:
    python index.py                         Show index status
    python index.py --all                   Full hybrid index
    python index.py --quick                 High-value types only
    python index.py --source                Index C++ source files
    python index.py --status                Show detailed statistics

    python index.py add <path>              Add project + set active
    python index.py use <name>              Switch active project
    python index.py list                    List all projects

    python index.py --all --project lyra    Index specific project
"""
import argparse
import os
import sys
from pathlib import Path

# Add UnrealAgent to path
SCRIPT_DIR = Path(__file__).parent.resolve()
UNREAL_AGENT_DIR = SCRIPT_DIR / "UnrealAgent"
sys.path.insert(0, str(UNREAL_AGENT_DIR))

# Change to UnrealAgent directory for proper imports
os.chdir(UNREAL_AGENT_DIR)


QUICK_TYPE_PROFILES = {
    # Current default: very fast UI/data/material-instance focused pass.
    "default": ["WidgetBlueprint", "DataTable", "MaterialInstance"],
    # Deeper project understanding: blueprint logic + widgets + core semantic types.
    "analysis": [
        "Blueprint",
        "WidgetBlueprint",
        "DataTable",
        "DataAsset",
        "Material",
        "MaterialInstance",
        "MaterialFunction",
    ],
}


def cmd_add(args):
    """Add a new project."""
    import tools

    project_path = args.path
    if not project_path.endswith('.uproject'):
        print(f"ERROR: Expected .uproject file, got: {project_path}")
        sys.exit(1)

    # Expand path
    project_path = os.path.abspath(os.path.expanduser(project_path))
    if not os.path.exists(project_path):
        print(f"ERROR: Project not found: {project_path}")
        sys.exit(1)

    # Use provided name or derive from filename
    if args.name:
        name = args.name
    else:
        name = os.path.splitext(os.path.basename(project_path))[0].lower()

    result = tools.add_project(name, project_path, set_active=True)

    print(f"Added project: {result['name']}")
    print(f"  Path: {result['project_path']}")
    print(f"  Engine: {result['engine_path']}")
    print()
    print("Next: Build the index with:")
    print(f"  python index.py --all")


def cmd_use(args):
    """Switch active project."""
    import tools

    try:
        tools.set_active_project(args.name)
        print(f"Switched to project: {args.name}")

        # Show index status
        db_path = Path(tools.get_project_db_path(args.name))
        if db_path.exists():
            from knowledge_index import KnowledgeStore
            store = KnowledgeStore(db_path)
            status = store.get_status()
            print(f"  Index: {status.total_docs} docs, {status.lightweight_total} lightweight")
        else:
            print(f"  Index: Not built yet")
            print(f"  Run: python index.py --all")

    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def cmd_list(args):
    """List all projects."""
    import tools

    result = tools.list_projects()
    active = result["active"]
    projects = result["projects"]

    if not projects:
        print("No projects configured.")
        print()
        print("Add a project with:")
        print("  python index.py add /path/to/Project.uproject")
        return

    print("Projects:")
    for name, config in projects.items():
        marker = " *" if name == active else ""
        print(f"  {name}{marker}")
        print(f"    Path: {config.get('project_path', '(not set)')}")

        # Check index status
        db_path = Path(tools.get_project_db_path(name))
        if db_path.exists():
            try:
                from knowledge_index import KnowledgeStore
                store = KnowledgeStore(db_path)
                status = store.get_status()
                print(f"    Index: {status.total_docs} docs, {status.lightweight_total} lightweight")
            except:
                print(f"    Index: {db_path.name}")
        else:
            print(f"    Index: Not built")
        print()

    print(f"Active: {active or '(none)'}")


def cmd_status(args):
    """Show detailed index status."""
    import tools

    project_name = args.project or tools.get_active_project_name()
    if not project_name:
        print("No project configured.")
        print("Add a project with: python index.py add /path/to/Project.uproject")
        return

    db_path = Path(tools.get_project_db_path(project_name))
    if not db_path.exists():
        print(f"Index not found for project '{project_name}'")
        print(f"Expected at: {db_path}")
        print()
        print("Build an index with:")
        print("  python index.py --all")
        return

    from knowledge_index import KnowledgeStore
    store = KnowledgeStore(db_path)
    status = store.get_status()

    print(f"Project: {project_name}")
    print(f"Database: {db_path}")
    print()
    print("Semantic Index:")
    print(f"  Total documents: {status.total_docs}")
    print(f"  Total edges: {status.total_edges}")
    if status.last_indexed:
        print(f"  Last indexed: {status.last_indexed}")
    if status.embed_model:
        print(f"  Embedding model: {status.embed_model}")
    print(f"  Schema version: {status.schema_version}")
    print()
    print("Documents by type:")
    for doc_type, count in sorted(status.docs_by_type.items()):
        print(f"  {doc_type}: {count}")

    if hasattr(status, 'lightweight_total') and status.lightweight_total > 0:
        print()
        print("Lightweight Assets (path + refs only):")
        print(f"  Total: {status.lightweight_total}")
        if hasattr(status, 'lightweight_by_type') and status.lightweight_by_type:
            for asset_type, count in sorted(status.lightweight_by_type.items(), key=lambda x: -x[1])[:10]:
                print(f"    {asset_type}: {count}")


def cmd_rebuild_fts(args):
    """Rebuild FTS5 index to fix corruption."""
    import tools
    from pathlib import Path
    from knowledge_index import KnowledgeStore

    project_name = args.project or tools.get_active_project_name()
    if not project_name:
        print("No project configured.")
        print("Add a project with: python index.py add /path/to/Project.uproject")
        sys.exit(1)

    db_path = Path(tools.get_project_db_path(project_name))
    if not db_path.exists():
        print(f"Index not found for project '{project_name}'")
        print(f"Expected at: {db_path}")
        sys.exit(1)

    print(f"Rebuilding FTS5 index for: {project_name}")
    print(f"Database: {db_path}")
    print()

    store = KnowledgeStore(db_path)
    conn = store._get_connection()

    try:
        # Rebuild FTS5 index
        print("Running FTS5 rebuild...")
        conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        conn.commit()
        print("Done!")
        print()

        # Verify integrity
        print("Verifying FTS5 integrity...")
        result = conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('integrity-check')").fetchall()
        print("Integrity check passed!")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        conn.close()


def cmd_index(args):
    """Run indexing."""
    import tools
    from pathlib import Path
    import time as time_module

    # Handle --project override
    if args.project:
        try:
            tools.set_active_project(args.project)
        except ValueError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    # Check project is configured
    if not tools.PROJECT:
        print("ERROR: No project configured")
        print("Run: python index.py add /path/to/Project.uproject")
        sys.exit(1)

    project_name = tools.get_active_project_name()
    print(f"Indexing project: {project_name}")
    print(f"Database: {tools.get_project_db_path()}")
    print()

    # Handle --source separately
    if args.source:
        sys.argv = ["tools.py", "--index-source"]
        # exec needs explicit namespace so functions can find module-level globals
        tools_path = str(UNREAL_AGENT_DIR / "tools.py")
        namespace = {"__name__": "__main__", "__file__": tools_path}
        exec(open(tools_path, encoding="utf-8").read(), namespace)
        return

    # Parse options
    profile = "quick" if args.quick else "hybrid"
    use_embeddings = getattr(args, 'embed', False)
    force_reindex = getattr(args, 'force', False)
    include_plugins = getattr(args, 'plugins', False)
    batch_size = max(1, min(2000, getattr(args, 'batch_size', 500)))
    max_assets = getattr(args, 'max_assets', None)
    if max_assets is not None:
        max_assets = max(1, max_assets)
    parser_parallelism = getattr(args, 'parser_parallelism', None)
    if parser_parallelism is not None:
        parser_parallelism = max(1, parser_parallelism)
        os.environ["UE_ASSETPARSER_MAX_PARALLELISM"] = str(parser_parallelism)
    batch_timeout = getattr(args, 'batch_timeout', None)
    if batch_timeout is not None:
        os.environ["UE_INDEX_BATCH_TIMEOUT"] = str(max(1, batch_timeout))
    asset_timeout = getattr(args, 'asset_timeout', None)
    if asset_timeout is not None:
        os.environ["UE_INDEX_ASSET_TIMEOUT"] = str(max(1, asset_timeout))
    recursive = not getattr(args, 'non_recursive', False)
    quick_profile = getattr(args, 'quick_profile', "default")
    custom_types_arg = getattr(args, 'types', None)
    index_path = getattr(args, 'path', None) or "/Game"
    # Handle paths - strip any Windows path conversion artifacts
    if ":" in index_path or "Program Files" in index_path:
        # Shell mangled the path, extract just the /Game part
        if "/Game/" in index_path:
            index_path = "/Game/" + index_path.split("/Game/")[-1]
        else:
            index_path = "/Game"
    elif not index_path.startswith("/Game"):
        index_path = "/Game/" + index_path.lstrip("/")

    # Get content path
    project_root = Path(os.path.dirname(tools.PROJECT))
    content_path = project_root / "Content"
    if not content_path.exists():
        print("ERROR: Could not find Content folder")
        sys.exit(1)

    # Discover plugin content folders if --plugins is set
    # This includes both Plugins/*/Content and Plugins/GameFeatures/*/Content
    plugin_paths = []
    if include_plugins:
        plugins_dir = project_root / "Plugins"
        if plugins_dir.exists():
            # Recursively find all Content folders under Plugins
            for content_dir in plugins_dir.rglob("Content"):
                if content_dir.is_dir() and any(content_dir.rglob("*.uasset")):
                    # Mount point is the parent folder name (the plugin name)
                    mount_point = content_dir.parent.name
                    # Avoid duplicates (same mount point)
                    if not any(mp == mount_point for mp, _ in plugin_paths):
                        plugin_paths.append((mount_point, content_dir))
                        print(f"Found plugin: {mount_point} ({content_dir})")

    db_path = Path(tools.get_project_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    from knowledge_index import KnowledgeStore, AssetIndexer

    print(f"Profile: {profile}")
    print(f"Content: {content_path}")
    if plugin_paths:
        print(f"Plugins: {len(plugin_paths)} with content")
    print(f"Batch size: {batch_size}")
    print(f"Batch timeout: {os.environ.get('UE_INDEX_BATCH_TIMEOUT', '600')}s")
    print(f"Asset timeout: {os.environ.get('UE_INDEX_ASSET_TIMEOUT', '60')}s")
    print(f"Parser parallelism: {os.environ.get('UE_ASSETPARSER_MAX_PARALLELISM', 'auto')}")
    if not recursive:
        print("Recursive: disabled (current folder only)")
    if max_assets is not None:
        print(f"Asset cap: {max_assets}")
    if profile == "quick":
        if custom_types_arg:
            selected_types = [t.strip() for t in custom_types_arg.split(",") if t.strip()]
            print(f"Quick types (custom): {', '.join(selected_types)}")
        else:
            selected_types = QUICK_TYPE_PROFILES.get(quick_profile, QUICK_TYPE_PROFILES["default"])
            print(f"Quick profile: {quick_profile}")
            print(f"Quick types: {', '.join(selected_types)}")
    else:
        selected_types = None
    if index_path != "/Game":
        print(f"Path filter: {index_path}")
    if force_reindex:
        print(f"Force: enabled (will re-index all)")
    print()

    from project_profile import load_profile
    project_profile = load_profile(emit_info=False)
    if project_profile.profile_name == "_defaults":
        print("INFO: Using engine defaults. Profile not required for standard UE projects.")
        print()

    # Set up embeddings if requested
    embed_fn = None
    embed_model = None
    if use_embeddings:
        print("Loading sentence-transformers for embeddings...")
        try:
            from knowledge_index.indexer import create_sentence_transformer_embedder
            embed_fn = create_sentence_transformer_embedder()
            embed_model = "all-MiniLM-L6-v2"
            print(f"  Model: {embed_model}")
            _ = embed_fn("warmup")
        except ImportError:
            print("  WARNING: sentence-transformers not installed, skipping embeddings")
        except Exception as e:
            print(f"  WARNING: Failed to load embeddings: {e}")
        print()

    store = KnowledgeStore(db_path)
    indexer = AssetIndexer(
        store, content_path,
        embed_fn=embed_fn, embed_model=embed_model, force=force_reindex,
        plugin_paths=plugin_paths if plugin_paths else None,
        profile=project_profile,
    )

    # Progress tracking - clean single-line-per-phase output
    from datetime import datetime
    import re

    progress_state = {
        'start_time': None,
        'phase_start': None,
        'last_phase': '',
        'last_total': 0,
    }

    def format_duration(seconds):
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"

    def timestamp():
        return datetime.now().strftime("%H:%M:%S")

    def batch_progress(status_msg, current, total):
        now = time_module.time()

        # Extract phase name, stripping batch numbers and normalizing
        raw_phase = status_msg.split(':')[0] if ':' in status_msg else status_msg
        phase = re.sub(r' batch \d+$', '', raw_phase)
        phase = re.sub(r' \d+$', '', phase)  # Also strip trailing numbers like "Batch Blueprint 1"

        # Initialize start time
        if progress_state['start_time'] is None:
            progress_state['start_time'] = now

        # Phase transition - print completion of previous phase
        if phase != progress_state['last_phase']:
            if progress_state['last_phase']:
                # Print completed phase on its own line
                prev_duration = format_duration(now - progress_state['phase_start'])
                prev_total = progress_state['last_total']
                sys.stdout.write(f"\r[{timestamp()}] {progress_state['last_phase']}: {prev_total:,} done ({prev_duration})" + " " * 20 + "\n")
                sys.stdout.flush()

            # Start new phase
            progress_state['phase_start'] = now
            progress_state['last_phase'] = phase
            progress_state['last_total'] = 0

        # Update progress on same line (will be overwritten)
        progress_state['last_total'] = max(progress_state['last_total'], current, total)

        if total > 0:
            pct = int(100 * current / total)
            eta_str = ""
            elapsed = now - progress_state['phase_start']
            if current > 0 and elapsed > 2:
                rate = current / elapsed
                remaining = total - current
                if rate > 0:
                    eta_str = f" ETA {format_duration(remaining / rate)}"
            sys.stdout.write(f"\r[{timestamp()}] {phase}: {current:,}/{total:,} ({pct}%){eta_str}" + " " * 10)
        else:
            sys.stdout.write(f"\r[{timestamp()}] {phase}..." + " " * 20)
        sys.stdout.flush()

    # Run indexing - all modes now use the fast batch pipeline
    if profile == "quick":
        # Quick mode: use batch-fast classification, then only index high-value types
        stats = indexer.index_folder_batch(
            index_path,
            batch_size=batch_size,
            progress_callback=batch_progress,
            profile="semantic-only",  # Skip lightweight indexing for quick mode
            type_filter=selected_types,
            recursive=recursive,
            max_assets=max_assets,
        )
    else:
        stats = indexer.index_folder_batch(
            index_path,
            batch_size=batch_size,
            progress_callback=batch_progress,
            profile=profile,
            recursive=recursive,
            max_assets=max_assets,
        )

    # Finalize - print last phase completion
    end_time = time_module.time()
    if progress_state['last_phase']:
        prev_duration = format_duration(end_time - progress_state['phase_start'])
        prev_total = progress_state['last_total']
        sys.stdout.write(f"\r[{timestamp()}] {progress_state['last_phase']}: {prev_total:,} done ({prev_duration})" + " " * 20 + "\n")

    # Clean summary
    total_duration = format_duration(end_time - progress_state['start_time']) if progress_state['start_time'] else "0s"
    total_found = stats.get('total_found', 0)
    lightweight = stats.get('lightweight_indexed', 0)
    semantic = stats.get('semantic_indexed', 0)
    unchanged = stats.get('unchanged', 0)
    errors = stats.get('errors', 0)

    print()
    print(f"[{timestamp()}] Complete in {total_duration}")
    print(f"    {total_found:,} assets: {semantic:,} semantic, {lightweight:,} lightweight, {unchanged:,} unchanged, {errors} errors")

    # Rebuild FTS5 index when marked dirty by docs writes.
    if store.is_fts_dirty():
        print()
        print("Rebuilding FTS5 index...")
        store.rebuild_fts()
        print("  Done")


def main():
    parser = argparse.ArgumentParser(
        description="UE Asset Toolkit - Index & Project Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Project Commands:
  add <path>              Add a .uproject file and set it as active
  use <name>              Switch to a different project
  list                    Show all configured projects

Indexing Options:
  --all                   Full hybrid index (lightweight + semantic)
  --quick                 High-value types only (fast)
  --source                C++ source files in Source/ and Plugins/
  --plugins               Include plugin Content folders (Plugins/*/Content, Plugins/GameFeatures/*/Content)
  --embed                 Generate sentence-transformer embeddings (slower, better semantic search)
  --rebuild-fts           Fix FTS5 corruption ("missing row X from content table" errors)
  --status                Show detailed index statistics
  --project <name>        Override active project for this command

Examples:
  python index.py add "C:\\Projects\\MyGame\\MyGame.uproject"
  python index.py list
  python index.py use lyra
  python index.py --all
  python index.py --all --plugins         # Include Game Feature plugins (ShooterCore, TopDownArena, etc.)
  python index.py --all --embed           # With embeddings for semantic search
  python index.py --all --project shootergame
  python index.py --rebuild-fts           # Fix corrupted FTS5 index
"""
    )

    # Subcommands for project management
    subparsers = parser.add_subparsers(dest="command")

    # add command
    add_parser = subparsers.add_parser("add", help="Add a project")
    add_parser.add_argument("path", help="Path to .uproject file")
    add_parser.add_argument("--name", help="Short name for project (default: derived from filename)")

    # use command
    use_parser = subparsers.add_parser("use", help="Switch active project")
    use_parser.add_argument("name", help="Project name to switch to")

    # list command
    subparsers.add_parser("list", help="List all projects")

    # Indexing options (for non-subcommand usage)
    parser.add_argument("--all", action="store_true", help="Full hybrid index")
    parser.add_argument("--quick", action="store_true", help="Quick index of high-value types")
    parser.add_argument("--source", action="store_true", help="Index C++ source files")
    parser.add_argument("--plugins", action="store_true", help="Include plugin Content folders (e.g., Plugins/*/Content)")
    parser.add_argument("--embed", action="store_true", help="Generate embeddings for semantic search (requires sentence-transformers)")
    parser.add_argument("--path", help="Only index assets under this path (e.g., /Game/UI)")
    parser.add_argument("--force", action="store_true", help="Force re-index even if unchanged (bypass fingerprint check)")
    parser.add_argument("--rebuild-fts", action="store_true", help="Rebuild FTS5 index to fix corruption (missing row errors)")
    parser.add_argument("--status", action="store_true", help="Show detailed index statistics")
    parser.add_argument("--project", help="Override active project for this command")
    parser.add_argument("--timing", action="store_true", help="Enable detailed timing instrumentation (also set UE_INDEX_TIMING=1)")
    parser.add_argument(
        "--quick-profile",
        choices=sorted(QUICK_TYPE_PROFILES.keys()),
        default="default",
        help="Type profile used by --quick (default: default)",
    )
    parser.add_argument(
        "--types",
        help="Comma-separated type override for --quick (e.g., Blueprint,WidgetBlueprint,DataTable)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("UE_INDEX_BATCH_SIZE", "500")),
        help="Batch size for indexer subprocess calls (default: 500, env: UE_INDEX_BATCH_SIZE)",
    )
    parser.add_argument(
        "--max-assets",
        type=int,
        help="Cap number of discovered assets processed this run (useful for safe incremental passes)",
    )
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="Only scan the exact folder in --path (skip subfolders)",
    )
    parser.add_argument(
        "--parser-parallelism",
        type=int,
        help="Cap AssetParser batch command parallelism (env override: UE_ASSETPARSER_MAX_PARALLELISM)",
    )
    parser.add_argument(
        "--batch-timeout",
        type=int,
        help="Batch parser timeout in seconds (default 600, env: UE_INDEX_BATCH_TIMEOUT)",
    )
    parser.add_argument(
        "--asset-timeout",
        type=int,
        help="Single-asset parser timeout in seconds (default 60, env: UE_INDEX_ASSET_TIMEOUT)",
    )

    args = parser.parse_args()

    # Enable timing via environment variable if --timing flag is set
    if getattr(args, 'timing', False):
        os.environ["UE_INDEX_TIMING"] = "1"

    # Route to appropriate handler
    if args.command == "add":
        cmd_add(args)
    elif args.command == "use":
        cmd_use(args)
    elif args.command == "list":
        cmd_list(args)
    elif getattr(args, 'rebuild_fts', False):
        cmd_rebuild_fts(args)
    elif args.all or args.quick or args.source:
        cmd_index(args)
    else:
        # Default: show status
        args.project = getattr(args, 'project', None)
        cmd_status(args)


if __name__ == "__main__":
    main()
