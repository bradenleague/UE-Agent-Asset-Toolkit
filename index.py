#!/usr/bin/env python3
"""
UE Asset Toolkit - Index Management

Root-level wrapper for indexing and project management.

Usage:
    python index.py                         Run index using saved/default profile
    python index.py --profile hybrid        Full hybrid index
    python index.py --profile quick         High-value types only
    python index.py --source                Scan C++ headers for class index
    python index.py --status                Show detailed statistics

    python index.py add <path>              Add project + set active
    python index.py use <name>              Switch active project
    python index.py list                    List all projects

    python index.py --project lyra          Index specific project
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
    if not project_path.endswith(".uproject"):
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
    print("  python index.py")


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
            print(
                f"  Index: {status.total_docs} docs, {status.lightweight_total} lightweight"
            )
        else:
            print("  Index: Not built yet")
            print("  Run: python index.py")

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
                print(
                    f"    Index: {status.total_docs} docs, {status.lightweight_total} lightweight"
                )
            except:
                print(f"    Index: {db_path.name}")
        else:
            print("    Index: Not built")
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
        print("  python index.py")
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

    if hasattr(status, "lightweight_total") and status.lightweight_total > 0:
        print()
        print("Lightweight Assets (path + refs only):")
        print(f"  Total: {status.lightweight_total}")
        if hasattr(status, "lightweight_by_type") and status.lightweight_by_type:
            for asset_type, count in sorted(
                status.lightweight_by_type.items(), key=lambda x: -x[1]
            )[:10]:
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
        result = conn.execute(
            "INSERT INTO docs_fts(docs_fts) VALUES('integrity-check')"
        ).fetchall()
        print("Integrity check passed!")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        conn.close()


def _resolve_index_options(args):
    """Resolve CLI args + saved config + env into effective index options.

    Cascade: explicit CLI arg > saved project config > env var > hardcoded default.
    Returns a dict of resolved options used by both cmd_index and cmd_dry_run.
    """
    import tools

    saved_opts = tools.get_project_index_options()

    # Profile resolution:
    # --profile > saved default > "hybrid"
    cli_profile = getattr(args, "profile", None)
    if cli_profile in {"hybrid", "quick"}:
        profile = cli_profile
    else:
        profile = saved_opts.get("default_profile", "hybrid")

    # --plugins: CLI explicit > saved config > False
    include_plugins = getattr(args, "plugins", False)
    if not include_plugins:
        include_plugins = saved_opts.get("include_plugins", False)

    # --batch-size: CLI explicit (non-None) > saved config > env > 500
    cli_batch_size = getattr(args, "batch_size", None)
    if cli_batch_size is not None:
        batch_size = max(1, min(2000, cli_batch_size))
    elif "batch_size" in saved_opts:
        batch_size = max(1, min(2000, saved_opts["batch_size"]))
    else:
        batch_size = max(
            1, min(2000, int(os.environ.get("UE_INDEX_BATCH_SIZE", "500")))
        )

    max_assets = getattr(args, "max_assets", None)
    if max_assets is not None:
        max_assets = max(1, max_assets)

    # --max-batch-memory: set env var so indexer's _get_available_memory_mb() picks it up
    max_batch_memory = getattr(args, "max_batch_memory", None)
    if max_batch_memory is not None:
        os.environ["UE_INDEX_MAX_BATCH_MEMORY"] = str(max(1, max_batch_memory))

    recursive = not getattr(args, "non_recursive", False)

    # --path with Windows shell cleanup
    index_path = getattr(args, "path", None) or "/Game"
    if ":" in index_path or "Program Files" in index_path:
        if "/Game/" in index_path:
            index_path = "/Game/" + index_path.split("/Game/")[-1]
        else:
            index_path = "/Game"
    elif not index_path.startswith("/Game"):
        index_path = "/Game/" + index_path.lstrip("/")

    # OFPA exclusion: CLI --no-ofpa > saved config > off
    _default_ofpa = ["__ExternalActors__", "__ExternalObjects__"]
    cli_no_ofpa = getattr(args, "no_ofpa", False)
    saved_exclude = saved_opts.get("exclude_paths")
    exclude_patterns = None
    if cli_no_ofpa:
        # Explicit CLI flag: always apply defaults (saved may be None/stale)
        exclude_patterns = saved_exclude if saved_exclude else _default_ofpa
    elif saved_exclude:
        # No CLI flag but saved config has patterns
        exclude_patterns = saved_exclude

    # Type filter (quick uses type subset, hybrid does not)
    quick_profile = getattr(args, "quick_profile", "default")
    custom_types_arg = getattr(args, "types", None)
    if profile == "quick":
        if custom_types_arg:
            selected_types = [
                t.strip() for t in custom_types_arg.split(",") if t.strip()
            ]
        else:
            selected_types = QUICK_TYPE_PROFILES.get(
                quick_profile, QUICK_TYPE_PROFILES["default"]
            )
    else:
        selected_types = None

    return {
        "saved_opts": saved_opts,
        "profile": profile,
        "include_plugins": include_plugins,
        "batch_size": batch_size,
        "max_assets": max_assets,
        "recursive": recursive,
        "index_path": index_path,
        "exclude_patterns": exclude_patterns,
        "selected_types": selected_types,
        "quick_profile": quick_profile,
    }


def cmd_index(args):
    """Run indexing."""
    import tools
    from pathlib import Path
    import time as time_module

    # Open log file if requested (writes newline-delimited progress unconditionally)
    log_fh = None
    if getattr(args, "log_file", None):
        log_fh = open(args.log_file, "w")

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

    # Handle --source: scan C++ headers for class index (lightweight, no docs)
    if args.source:
        project_root = Path(os.path.dirname(tools.PROJECT))
        db_path = Path(tools.get_project_db_path())
        db_path.parent.mkdir(parents=True, exist_ok=True)

        from knowledge_index import KnowledgeStore

        store = KnowledgeStore(db_path)
        print("Scanning C++ headers for class index...")
        count = store.scan_cpp_classes(project_root)
        print(f"  Found {count} C++ classes/structs")
        return

    # Resolve all index options (shared with cmd_dry_run)
    opts = _resolve_index_options(args)
    saved_opts = opts["saved_opts"]
    profile = opts["profile"]
    include_plugins = opts["include_plugins"]
    batch_size = opts["batch_size"]
    max_assets = opts["max_assets"]
    recursive = opts["recursive"]
    index_path = opts["index_path"]
    exclude_patterns = opts["exclude_patterns"]
    selected_types = opts["selected_types"]
    quick_profile = opts["quick_profile"]

    use_embeddings = getattr(args, "embed", False)
    force_reindex = getattr(args, "force", False)
    custom_types_arg = getattr(args, "types", None)

    parser_parallelism = getattr(args, "parser_parallelism", None)
    if parser_parallelism is not None:
        parser_parallelism = max(1, parser_parallelism)
        os.environ["UE_ASSETPARSER_MAX_PARALLELISM"] = str(parser_parallelism)
    batch_timeout = getattr(args, "batch_timeout", None)
    if batch_timeout is not None:
        os.environ["UE_INDEX_BATCH_TIMEOUT"] = str(max(1, batch_timeout))
    asset_timeout = getattr(args, "asset_timeout", None)
    if asset_timeout is not None:
        os.environ["UE_INDEX_ASSET_TIMEOUT"] = str(max(1, asset_timeout))

    if exclude_patterns:
        print(f"OFPA exclusion: skipping {', '.join(exclude_patterns)}")

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
    print(
        f"Parser parallelism: {os.environ.get('UE_ASSETPARSER_MAX_PARALLELISM', 'auto')}"
    )
    if not recursive:
        print("Recursive: disabled (current folder only)")
    if max_assets is not None:
        print(f"Asset cap: {max_assets}")
    if profile == "quick":
        if custom_types_arg:
            selected_types = [
                t.strip() for t in custom_types_arg.split(",") if t.strip()
            ]
            print(f"Quick types (custom): {', '.join(selected_types)}")
        else:
            selected_types = QUICK_TYPE_PROFILES.get(
                quick_profile, QUICK_TYPE_PROFILES["default"]
            )
            print(f"Quick profile: {quick_profile}")
            print(f"Quick types: {', '.join(selected_types)}")
    else:
        selected_types = None
    if index_path != "/Game":
        print(f"Path filter: {index_path}")
    if force_reindex:
        print("Force: enabled (will re-index all)")
    print()

    from project_profile import load_profile

    project_profile = load_profile(emit_info=False)
    if project_profile.profile_name == "_defaults":
        print(
            "INFO: Using engine defaults. Profile not required for standard UE projects."
        )
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
        store,
        content_path,
        embed_fn=embed_fn,
        embed_model=embed_model,
        force=force_reindex,
        plugin_paths=plugin_paths if plugin_paths else None,
        profile=project_profile,
    )

    # Progress tracking - clean single-line-per-phase output
    from datetime import datetime
    import re

    _is_tty = sys.stdout.isatty()

    progress_state = {
        "start_time": None,
        "phase_start": None,
        "last_phase": "",
        "last_total": 0,
        "last_nontty_pct": -1,  # track last reported % for non-TTY mode
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
        raw_phase = status_msg.split(":")[0] if ":" in status_msg else status_msg
        phase = re.sub(r" batch \d+$", "", raw_phase)
        phase = re.sub(
            r" \d+$", "", phase
        )  # Also strip trailing numbers like "Batch Blueprint 1"

        # Initialize start time
        if progress_state["start_time"] is None:
            progress_state["start_time"] = now

        # Phase transition - print completion of previous phase
        if phase != progress_state["last_phase"]:
            if progress_state["last_phase"]:
                prev_duration = format_duration(now - progress_state["phase_start"])
                prev_total = progress_state["last_total"]
                if _is_tty:
                    sys.stdout.write(
                        f"\r[{timestamp()}] {progress_state['last_phase']}: {prev_total:,} done ({prev_duration})"
                        + " " * 20
                        + "\n"
                    )
                else:
                    sys.stdout.write(
                        f"[{timestamp()}] {progress_state['last_phase']}: {prev_total:,} done ({prev_duration})\n"
                    )
                sys.stdout.flush()

            # Start new phase
            progress_state["phase_start"] = now
            progress_state["last_phase"] = phase
            progress_state["last_total"] = 0
            progress_state["last_nontty_pct"] = -1

        # Update progress on same line (will be overwritten)
        progress_state["last_total"] = max(progress_state["last_total"], current, total)

        if total > 0:
            pct = int(100 * current / total)
            eta_str = ""
            elapsed = now - progress_state["phase_start"]
            if current > 0 and elapsed > 2:
                rate = current / elapsed
                remaining = total - current
                if rate > 0:
                    eta_str = f" ETA {format_duration(remaining / rate)}"

            if _is_tty:
                sys.stdout.write(
                    f"\r[{timestamp()}] {phase}: {current:,}/{total:,} ({pct}%){eta_str}"
                    + " " * 10
                )
            else:
                # Non-TTY: emit at ~10% intervals to avoid flooding
                pct_bucket = pct // 10 * 10
                if pct_bucket > progress_state["last_nontty_pct"]:
                    progress_state["last_nontty_pct"] = pct_bucket
                    sys.stdout.write(
                        f"[{timestamp()}] {phase}: {current:,}/{total:,} ({pct}%){eta_str}\n"
                    )
        else:
            if _is_tty:
                sys.stdout.write(f"\r[{timestamp()}] {phase}..." + " " * 20)
            # Non-TTY: skip indeterminate progress to avoid spam
        sys.stdout.flush()

        # Log file: always write newline-delimited, every update
        if log_fh and total > 0:
            pct = int(100 * current / total) if total > 0 else 0
            log_fh.write(f"[{timestamp()}] {phase}: {current:,}/{total:,} ({pct}%)\n")
            log_fh.flush()

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
            exclude_patterns=exclude_patterns,
        )
    else:
        stats = indexer.index_folder_batch(
            index_path,
            batch_size=batch_size,
            progress_callback=batch_progress,
            profile=profile,
            recursive=recursive,
            max_assets=max_assets,
            exclude_patterns=exclude_patterns,
        )

    # Close log file
    if log_fh:
        log_fh.close()

    # Finalize - print last phase completion
    end_time = time_module.time()
    if progress_state["last_phase"]:
        prev_duration = format_duration(end_time - progress_state["phase_start"])
        prev_total = progress_state["last_total"]
        if _is_tty:
            sys.stdout.write(
                f"\r[{timestamp()}] {progress_state['last_phase']}: {prev_total:,} done ({prev_duration})"
                + " " * 20
                + "\n"
            )
        else:
            sys.stdout.write(
                f"[{timestamp()}] {progress_state['last_phase']}: {prev_total:,} done ({prev_duration})\n"
            )

    # Clean summary
    total_duration = (
        format_duration(end_time - progress_state["start_time"])
        if progress_state["start_time"]
        else "0s"
    )
    total_found = stats.get("total_found", 0)
    lightweight = stats.get("lightweight_indexed", 0)
    semantic = stats.get("semantic_indexed", 0)
    unchanged = stats.get("unchanged", 0)
    errors = stats.get("errors", 0)

    print()
    print(f"[{timestamp()}] Complete in {total_duration}")
    print(
        f"    {total_found:,} assets: {semantic:,} semantic, {lightweight:,} lightweight, {unchanged:,} unchanged, {errors} errors"
    )

    # Persist effective options if --save was requested
    if getattr(args, "save", False):
        effective_opts = {
            "exclude_paths": exclude_patterns if exclude_patterns else None,
            "include_plugins": include_plugins if include_plugins else None,
            "batch_size": batch_size,
            "default_profile": profile,
        }
        tools.set_project_index_options(effective_opts)
        print("    Saved index options to config for next run")

    # Rebuild FTS5 index when marked dirty by docs writes.
    if store.is_fts_dirty():
        print()
        print("Rebuilding FTS5 index...")
        store.rebuild_fts()
        print("  Done")


def cmd_dry_run(args):
    """Preview what would be indexed without writing to the database."""
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

    if not tools.PROJECT:
        print("ERROR: No project configured")
        print("Run: python index.py add /path/to/Project.uproject")
        sys.exit(1)

    project_name = tools.get_active_project_name()
    print(f"Dry run for project: {project_name}")
    print()

    # Resolve all index options (shared with cmd_index)
    opts = _resolve_index_options(args)
    include_plugins = opts["include_plugins"]
    batch_size = opts["batch_size"]
    max_assets = opts["max_assets"]
    recursive = opts["recursive"]
    index_path = opts["index_path"]
    exclude_patterns = opts["exclude_patterns"]
    type_filter = opts["selected_types"]
    profile = opts["profile"]

    # For the indexer call, quick profile routes through semantic-only with type filter
    indexer_profile = "semantic-only" if profile == "quick" else profile

    if exclude_patterns:
        print(f"OFPA exclusion: skipping {', '.join(exclude_patterns)}")

    # Get content path
    project_root = Path(os.path.dirname(tools.PROJECT))
    content_path = project_root / "Content"
    if not content_path.exists():
        print("ERROR: Could not find Content folder")
        sys.exit(1)

    # Discover plugins if needed
    plugin_paths = []
    if include_plugins:
        plugins_dir = project_root / "Plugins"
        if plugins_dir.exists():
            for content_dir in plugins_dir.rglob("Content"):
                if content_dir.is_dir() and any(content_dir.rglob("*.uasset")):
                    mount_point = content_dir.parent.name
                    if not any(mp == mount_point for mp, _ in plugin_paths):
                        plugin_paths.append((mount_point, content_dir))

    # Use temp file store — deleted after dry run
    import tempfile
    from knowledge_index import KnowledgeStore, AssetIndexer

    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db_path = tmp_db.name
    tmp_db.close()
    store = KnowledgeStore(tmp_db_path)

    from project_profile import load_profile

    project_profile = load_profile(emit_info=False)
    indexer = AssetIndexer(
        store,
        content_path,
        plugin_paths=plugin_paths if plugin_paths else None,
        profile=project_profile,
    )

    # Run Phase 1 only (classification)
    start = time_module.time()

    def progress(status_msg, current, total):
        if total > 0:
            pct = int(100 * current / total)
            sys.stdout.write(
                f"\r  {status_msg}: {current:,}/{total:,} ({pct}%)" + " " * 10
            )
        else:
            sys.stdout.write(f"\r  {status_msg}..." + " " * 20)
        sys.stdout.flush()

    try:
        stats = indexer.index_folder_batch(
            index_path,
            batch_size=batch_size,
            progress_callback=progress,
            profile=indexer_profile,
            type_filter=type_filter,
            exclude_patterns=exclude_patterns,
            recursive=recursive,
            max_assets=max_assets,
            dry_run=True,
        )

        elapsed = time_module.time() - start
        sys.stdout.write("\r" + " " * 80 + "\r")

        # Build summary table from by_type
        by_type = stats.get("by_type", {})
        total_assets = sum(by_type.values())

        # Determine semantic vs lightweight types using profile
        _BASE_SEMANTIC_TYPES = {
            "Blueprint",
            "WidgetBlueprint",
            "DataTable",
            "Material",
            "MaterialInstance",
            "MaterialFunction",
            "DataAsset",
        }

        semantic_count = 0
        lightweight_count = 0
        for t, c in by_type.items():
            if t in _BASE_SEMANTIC_TYPES:
                semantic_count += c
            else:
                lightweight_count += c

        print()
        print(f"{'Type':<40} {'Count':>8}  {'%':>6}")
        print("-" * 58)
        for asset_type, count in sorted(by_type.items(), key=lambda x: -x[1]):
            pct = 100 * count / total_assets if total_assets > 0 else 0
            print(f"  {asset_type:<38} {count:>8,}  {pct:>5.1f}%")
        print("-" * 58)
        print(f"  {'TOTAL':<38} {total_assets:>8,}")
        print()
        print(f"Discovery + classification took {elapsed:.1f}s")

        # Time estimate heuristic
        if total_assets > 0:
            semantic_rate = 2.0  # assets/sec (full parse + embed)
            lightweight_rate = 50.0  # assets/sec (refs only)
            est_seconds = (
                semantic_count / semantic_rate + lightweight_count / lightweight_rate
            )
            if est_seconds < 60:
                est_str = f"~{int(est_seconds)}s"
            elif est_seconds < 3600:
                est_str = f"~{int(est_seconds / 60)}m"
            else:
                est_str = f"~{est_seconds / 3600:.1f} hours"
            print(
                f"Estimated full index time: {est_str} ({semantic_count:,} semantic, {lightweight_count:,} lightweight)"
            )
    finally:
        store.close()
        # Clean up temp database
        try:
            os.unlink(tmp_db_path)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="UE Asset Toolkit - Index & Project Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
        epilog="""
Project Commands:
  add <path>              Add a .uproject file and set it as active
  use <name>              Switch to a different project
  list                    Show all configured projects

Indexing Options:
  --profile <hybrid|quick>  Primary index mode selector
  --source                Scan C++ headers for class→source bridge
  --plugins               Include plugin Content folders (Plugins/*/Content, Plugins/GameFeatures/*/Content)
  --embed                 Generate sentence-transformer embeddings (slower, better semantic search)
  --rebuild-fts           Fix FTS5 corruption ("missing row X from content table" errors)
  --status                Show detailed index statistics
  --project <name>        Override active project for this command

Examples:
  python index.py add "C:\\Projects\\MyGame\\MyGame.uproject"
  python index.py list
  python index.py use lyra
  python index.py
  python index.py --profile quick
  python index.py --profile hybrid --no-ofpa         # Skip OFPA dirs (large World Partition projects)
  python index.py --plugins                          # Include Game Feature plugins (ShooterCore, TopDownArena, etc.)
  python index.py --embed                            # With embeddings for semantic search
  python index.py --project shootergame
  python index.py --rebuild-fts           # Fix corrupted FTS5 index
""",
    )

    # Subcommands for project management
    subparsers = parser.add_subparsers(dest="command")

    # add command
    add_parser = subparsers.add_parser("add", help="Add a project")
    add_parser.add_argument("path", help="Path to .uproject file")
    add_parser.add_argument(
        "--name", help="Short name for project (default: derived from filename)"
    )

    # use command
    use_parser = subparsers.add_parser("use", help="Switch active project")
    use_parser.add_argument("name", help="Project name to switch to")

    # list command
    subparsers.add_parser("list", help="List all projects")

    # Indexing options (for non-subcommand usage)
    parser.add_argument(
        "--profile",
        choices=["hybrid", "quick"],
        help="Index mode selector",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be indexed (classify only, no DB writes)",
    )
    parser.add_argument(
        "--source",
        action="store_true",
        help="Scan C++ headers for class-to-source bridge (no docs created)",
    )
    parser.add_argument(
        "--plugins",
        action="store_true",
        help="Include plugin Content folders (e.g., Plugins/*/Content)",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Generate embeddings for semantic search (requires sentence-transformers)",
    )
    parser.add_argument(
        "--path", help="Only index assets under this path (e.g., /Game/UI)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-index even if unchanged (bypass fingerprint check)",
    )
    parser.add_argument(
        "--rebuild-fts",
        action="store_true",
        help="Rebuild FTS5 index to fix corruption (missing row errors)",
    )
    parser.add_argument(
        "--status", action="store_true", help="Show detailed index statistics"
    )
    parser.add_argument("--project", help="Override active project for this command")
    parser.add_argument(
        "--timing",
        action="store_true",
        help="Enable detailed timing instrumentation (also set UE_INDEX_TIMING=1)",
    )
    parser.add_argument(
        "--quick-profile",
        choices=sorted(QUICK_TYPE_PROFILES.keys()),
        default="default",
        help="Type profile used when --profile quick (default: default)",
    )
    parser.add_argument(
        "--types",
        help="Comma-separated type override when --profile quick (e.g., Blueprint,WidgetBlueprint,DataTable)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
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
        "--no-ofpa",
        action="store_true",
        help="Exclude __ExternalActors__ and __ExternalObjects__ directories (recommended for large World Partition projects)",
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
    parser.add_argument(
        "--log-file",
        help="Write newline-delimited progress to this file (independent of TTY detection)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Persist effective indexing options to project config (re-used on next run)",
    )
    parser.add_argument(
        "--max-batch-memory",
        type=int,
        help="Cap available memory (MB) used for batch sizing (auto-reduces batch_size when memory is low)",
    )

    args = parser.parse_args()

    # Enable timing via environment variable if --timing flag is set
    if getattr(args, "timing", False):
        os.environ["UE_INDEX_TIMING"] = "1"

    # Route to appropriate handler
    if args.command == "add":
        cmd_add(args)
    elif args.command == "use":
        cmd_use(args)
    elif args.command == "list":
        cmd_list(args)
    elif getattr(args, "rebuild_fts", False):
        cmd_rebuild_fts(args)
    elif getattr(args, "dry_run", False):
        cmd_dry_run(args)
    elif getattr(args, "status", False):
        cmd_status(args)
    else:
        # Default behavior: run indexing with saved/default options.
        # Use --status for read-only stats output.
        cmd_index(args)


if __name__ == "__main__":
    main()
