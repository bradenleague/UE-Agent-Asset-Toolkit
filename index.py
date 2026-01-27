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


def cmd_index(args):
    """Run indexing."""
    import tools
    from pathlib import Path
    from knowledge_index import KnowledgeStore, AssetIndexer
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

    print(f"Profile: {profile}")
    print(f"Content: {content_path}")
    if plugin_paths:
        print(f"Plugins: {len(plugin_paths)} with content")
    if index_path != "/Game":
        print(f"Path filter: {index_path}")
    if force_reindex:
        print(f"Force: enabled (will re-index all)")
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
        plugin_paths=plugin_paths if plugin_paths else None
    )

    # Progress tracking
    batch_state = {'start_time': None, 'phase_start': None, 'last_phase': ''}

    def format_eta(seconds):
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"

    def batch_progress(status_msg, current, total):
        now = time_module.time()
        phase = status_msg.split(':')[0] if ':' in status_msg else status_msg
        if phase != batch_state['last_phase']:
            batch_state['phase_start'] = now
            batch_state['last_phase'] = phase
        if batch_state['start_time'] is None:
            batch_state['start_time'] = now

        if total > 0:
            pct = int(100 * current / total)
            eta_str = ""
            elapsed = now - (batch_state['phase_start'] or now)
            if current > 0 and elapsed > 2:
                rate = current / elapsed
                remaining = total - current
                if rate > 0:
                    eta_str = f" - ETA: {format_eta(remaining / rate)}"
            sys.stdout.write(f"\r  {status_msg}: [{current}/{total}] {pct}%{eta_str}          ")
        else:
            sys.stdout.write(f"\r  {status_msg}...          ")
        sys.stdout.flush()

    # Run indexing
    if profile == "quick":
        stats = indexer.index_folder(
            index_path,
            type_filter=["WidgetBlueprint", "DataTable", "MaterialInstance"],
            progress_callback=lambda p, c, t: batch_progress(f"Indexing {p.split('/')[-1][:30]}", c, t)
        )
    else:
        stats = indexer.index_folder_batch(
            index_path,
            batch_size=500,
            progress_callback=batch_progress,
            profile=profile,
        )

    # Show results
    sys.stdout.write("\r" + " " * 80 + "\r")
    print()
    print("Indexing complete:")
    print(f"  Total found: {stats.get('total_found', 0)}")
    if 'lightweight_indexed' in stats:
        print(f"  Lightweight indexed: {stats.get('lightweight_indexed', 0)}")
        print(f"  Semantic indexed: {stats.get('semantic_indexed', 0)}")
    else:
        print(f"  Indexed: {stats.get('indexed', 0)}")
    print(f"  Unchanged: {stats.get('unchanged', 0)}")
    print(f"  Errors: {stats.get('errors', 0)}")

    # Rebuild FTS5 index if force was used (prevents corruption)
    if force_reindex:
        print()
        print("Rebuilding FTS5 index...")
        store._get_connection().execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
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
  --plugins               Include plugin Content folders (Plugins/*/Content)
  --embed                 Generate sentence-transformer embeddings (slower, better semantic search)
  --status                Show detailed index statistics
  --project <name>        Override active project for this command

Examples:
  python index.py add "C:\\Projects\\MyGame\\MyGame.uproject"
  python index.py list
  python index.py use lyra
  python index.py --all
  python index.py --all --plugins         # Include Game Feature plugins
  python index.py --all --embed           # With embeddings for semantic search
  python index.py --all --project shootergame
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
    parser.add_argument("--status", action="store_true", help="Show detailed index statistics")
    parser.add_argument("--project", help="Override active project for this command")

    args = parser.parse_args()

    # Route to appropriate handler
    if args.command == "add":
        cmd_add(args)
    elif args.command == "use":
        cmd_use(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.all or args.quick or args.source:
        cmd_index(args)
    else:
        # Default: show status
        args.project = getattr(args, 'project', None)
        cmd_status(args)


if __name__ == "__main__":
    main()
