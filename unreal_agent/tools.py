"""UE Asset inspection tools using AssetParser (C#) for read-only operations.

Provides configuration management, asset listing, and inspection via the
AssetParser CLI which parses .uasset files directly.
"""

import sys
import os
import json
from pathlib import Path

# Support source-based invocation:
#   python unreal_agent/tools.py --list
#   cd unreal_agent && python tools.py --list
# In these modes, sys.path may not include the repo root required for
# absolute imports like `from unreal_agent...`.
if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

from unreal_agent.core import (  # noqa: F401 — re-exported for cli.py
    UE_EDITOR,
    PROJECT,
    DEBUG,
    configure,
    add_project,
    list_projects,
    set_active_project,
    get_active_project_name,
    get_project_index_options,
    set_project_index_options,
    get_project_db_path,
    get_plugin_paths,
    format_eta,
)
from unreal_agent.core.config import CONFIG_FILE

from unreal_agent.assets import (
    inspect_asset,
    inspect_widget,
    inspect_datatable,
    inspect_blueprint,
    inspect_blueprint_graph,
    inspect_material,
    inspect_materialfunction,
    list_assets,
    list_asset_folders,
)

# Re-export tools logic as they were
TOOLS = [
    {
        "name": "list_asset_folders",
        "description": "Internal. List subfolders and asset counts. Prefer explore_folder for user queries.",
        "function": list_asset_folders,
        "parameters": {"path": {"type": "string", "default": "/Game"}},
    },
    {
        "name": "list_assets",
        "description": "Internal. List assets with pagination. Prefer explore_folder for user queries.",
        "function": list_assets,
        "parameters": {
            "path": {"type": "string", "default": "/Game"},
            "type_filter": {"type": "string", "optional": True},
            "limit": {"type": "integer", "default": 50},
            "offset": {"type": "integer", "default": 0},
        },
    },
    {
        "name": "inspect_asset",
        "description": "Get all properties and values of an asset. Use summarize=True for focused output, type_only=True for just asset type/metadata. For Blueprints, use detail='graph' for visual node wiring.",
        "function": inspect_asset,
        "parameters": {
            "asset_path": {"type": "string"},
            "summarize": {"type": "boolean", "default": False, "optional": True},
            "type_only": {"type": "boolean", "default": False, "optional": True},
            "detail": {"type": "string", "optional": True, "enum": ["graph"]},
        },
    },
    {
        "name": "inspect_widget",
        "description": "Internal. Get widget hierarchy. Prefer explain_asset for user queries.",
        "function": inspect_widget,
        "parameters": {"asset_path": {"type": "string"}},
    },
    {
        "name": "inspect_datatable",
        "description": "Internal. Get DataTable rows. Prefer explain_asset for user queries.",
        "function": inspect_datatable,
        "parameters": {"asset_path": {"type": "string"}},
    },
    {
        "name": "inspect_blueprint",
        "description": "Internal. Get Blueprint functions/variables. Prefer explain_asset for user queries.",
        "function": inspect_blueprint,
        "parameters": {"asset_path": {"type": "string"}},
    },
    {
        "name": "inspect_blueprint_graph",
        "description": "Internal. Get Blueprint visual graph. Prefer inspect_asset(detail='graph').",
        "function": inspect_blueprint_graph,
        "parameters": {"asset_path": {"type": "string"}},
    },
    {
        "name": "inspect_material",
        "description": "Internal. Get Material parameters. Prefer explain_asset for user queries.",
        "function": inspect_material,
        "parameters": {"asset_path": {"type": "string"}},
    },
    {
        "name": "inspect_materialfunction",
        "description": "Internal. Get MaterialFunction inputs/outputs/parameters. Prefer explain_asset for user queries.",
        "function": inspect_materialfunction,
        "parameters": {"asset_path": {"type": "string"}},
    },
]


if __name__ == "__main__":
    DEBUG = True

    print("=" * 60)
    print("UE Agent Tool Test")
    print("=" * 60)

    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python tools.py <project-name>        # Use project from config.json")
        print("  python tools.py <path-to-.uproject>   # Use direct path")
        print("  python tools.py --list                # List configured projects")
        print()
        print("For indexing, use index.py from the repo root:")
        print(
            "  python index.py                       # Run with saved/default profile"
        )
        print("  python index.py --profile quick       # High-value types only")
        print()
        print("Examples:")
        print("  python tools.py lyra")
        print("  python tools.py D:\\Projects\\MyGame\\MyGame.uproject")
        sys.exit(1)

    arg = sys.argv[1]

    # List projects
    if arg == "--list":
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
            active = config.get("active_project", "")
            projects = config.get("projects", {})
            print("Configured projects:")
            for name, proj in projects.items():
                marker = " (active)" if name == active else ""
                print(f"  {name}{marker}: {proj.get('project_path', 'N/A')}")
        else:
            print("No config.json found")
        sys.exit(0)

    # Semantic index commands
    if arg == "--index-status":
        from pathlib import Path

        db_path = Path(get_project_db_path())

        if not db_path.exists():
            print("Semantic index not found.")
            print(f"Expected at: {db_path}")
            print("Run 'python tools.py --index' to build it.")
            sys.exit(1)

        from unreal_agent.knowledge_index import KnowledgeStore

        store = KnowledgeStore(db_path)
        status = store.get_status()

        print("Semantic Index Status")
        print(f"  Database: {db_path}")
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

        # Show C++ class index stats
        cpp_stats = store.get_cpp_class_stats()
        if cpp_stats["total_classes"] > 0:
            print()
            print(f"C++ class index: {cpp_stats['total_classes']} classes")

        # Show lightweight assets summary
        if hasattr(status, "lightweight_total") and status.lightweight_total > 0:
            print()
            print("Lightweight Assets (path + refs only):")
            print(f"  Total: {status.lightweight_total}")
            if status.lightweight_by_type:
                for asset_type, count in sorted(status.lightweight_by_type.items()):
                    print(f"    {asset_type}: {count}")
        sys.exit(0)

    if arg == "--index":
        from pathlib import Path

        # Get content path from config or auto-detect
        content_path = None
        if PROJECT:
            content_path = Path(os.path.dirname(PROJECT)) / "Content"

        if not content_path or not content_path.exists():
            print("ERROR: Could not find Content folder")
            print(
                "Make sure config.json is set up or a .uproject file is in the parent directory"
            )
            sys.exit(1)

        db_path = Path(get_project_db_path())
        db_path.parent.mkdir(parents=True, exist_ok=True)

        from unreal_agent.knowledge_index import KnowledgeStore, AssetIndexer

        print("Building semantic index...")
        print(f"  Content: {content_path}")
        print(f"  Database: {db_path}")
        print()

        from unreal_agent.project_profile import load_profile

        store = KnowledgeStore(db_path)
        project_profile = load_profile(emit_info=False)
        if project_profile.profile_name == "_defaults":
            print(
                "INFO: Using engine defaults. Profile not required for standard UE projects."
            )
            print()
        indexer = AssetIndexer(store, content_path, profile=project_profile)

        # Progress tracking with ETA
        import time as time_module

        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        progress_state = {"idx": 0, "start_time": time_module.time(), "last_update": 0}

        def progress(path, current, total):
            # Get asset name from path
            asset_name = path.split("/")[-1] if "/" in path else path
            if len(asset_name) > 35:
                asset_name = asset_name[:32] + "..."

            # Calculate ETA
            elapsed = time_module.time() - progress_state["start_time"]
            eta_str = ""
            if current > 0 and elapsed > 2:  # Wait 2s before showing ETA
                rate = current / elapsed
                remaining = total - current
                if rate > 0:
                    eta_seconds = remaining / rate
                    eta_str = f" - ETA: {format_eta(eta_seconds)}"

            # Update progress line (overwrite previous)
            spinner = spinner_chars[progress_state["idx"] % len(spinner_chars)]
            progress_state["idx"] += 1
            pct = int(100 * current / total) if total > 0 else 0
            sys.stdout.write(
                f"\r  {spinner} [{current}/{total}] {pct}%{eta_str} - {asset_name:<30}"
            )
            sys.stdout.flush()

        stats = indexer.index_folder("/Game", progress_callback=progress)

        # Clear the progress line and show results
        sys.stdout.write("\r" + " " * 80 + "\r")
        print()
        print("Indexing complete:")
        print(f"  Total found: {stats.get('total_found', 0)}")
        print(f"  Indexed: {stats.get('indexed', 0)}")
        print(f"  Unchanged: {stats.get('unchanged', 0)}")
        print(f"  Errors: {stats.get('errors', 0)}")
        if stats.get("by_type"):
            print()
            print("By type:")
            for asset_type, count in sorted(stats["by_type"].items()):
                print(f"  {asset_type}: {count}")
        sys.exit(0)

    if arg == "--index-batch":
        from pathlib import Path

        # Parse arguments
        profile = "hybrid"  # default
        use_embeddings = "--embed" in sys.argv
        force_reindex = "--force" in sys.argv
        index_path = "/Game"  # default
        batch_size = 500  # default
        max_assets = None  # default (no limit)
        type_filter = None  # default (all types)

        # Find --path argument
        for i, a in enumerate(sys.argv):
            if a == "--path" and i + 1 < len(sys.argv):
                index_path = sys.argv[i + 1]
                if not index_path.startswith("/Game"):
                    index_path = "/Game/" + index_path.lstrip("/")
            elif a == "--batch-size" and i + 1 < len(sys.argv):
                try:
                    batch_size = max(10, min(2000, int(sys.argv[i + 1])))
                except ValueError:
                    pass
            elif a == "--max-assets" and i + 1 < len(sys.argv):
                try:
                    max_assets = max(1, int(sys.argv[i + 1]))
                except ValueError:
                    pass
            elif a == "--type-filter" and i + 1 < len(sys.argv):
                type_filter = [
                    t.strip() for t in sys.argv[i + 1].split(",") if t.strip()
                ]

        # Find profile arg (first non-flag arg after --index-batch).
        # Skip values that belong to --flag arguments.
        _flag_args = {"--path", "--batch-size", "--max-assets", "--type-filter"}
        skip_next = False
        for i, a in enumerate(sys.argv[2:], start=2):
            if skip_next:
                skip_next = False
                continue
            if a in _flag_args:
                skip_next = True
                continue
            if not a.startswith("-") and a != index_path:
                profile = a.lower()
                if profile not in (
                    "quick",
                    "hybrid",
                    "lightweight-only",
                    "semantic-only",
                ):
                    print(f"ERROR: Unknown profile '{profile}'")
                    print("Available profiles:")
                    print("  quick           - Index high-value types only (~10 min)")
                    print(
                        "  hybrid          - Full coverage with two-tier strategy (~3-4 hours)"
                    )
                    print(
                        "  lightweight-only - Path + refs only, no semantic search (~20 min)"
                    )
                    print(
                        "  semantic-only   - Semantic types only, skip lightweight (~2-4 hours)"
                    )
                    sys.exit(1)
                break

        # Get content path from config or auto-detect
        content_path = None
        if PROJECT:
            content_path = Path(os.path.dirname(PROJECT)) / "Content"

        if not content_path or not content_path.exists():
            print("ERROR: Could not find Content folder")
            print(
                "Make sure config.json is set up or a .uproject file is in the parent directory"
            )
            sys.exit(1)

        db_path = Path(get_project_db_path())
        db_path.parent.mkdir(parents=True, exist_ok=True)

        from unreal_agent.knowledge_index import KnowledgeStore, AssetIndexer

        print(f"Building semantic index (batch mode, profile: {profile})...")
        print(f"  Content: {content_path}")
        print(f"  Database: {db_path}")
        print(f"  Batch size: {batch_size}")
        if force_reindex:
            print("  Force: re-indexing all assets (ignoring fingerprints)")
        if max_assets:
            print(f"  Max assets: {max_assets}")
        if type_filter:
            print(f"  Type filter: {', '.join(type_filter)}")
        print()

        if profile == "quick":
            print(
                "Quick profile: Indexing WidgetBlueprint, DataTable, MaterialInstance only"
            )
        elif profile == "hybrid":
            print("Hybrid profile: Full coverage with two-tier strategy")
            print("  - Lightweight (path+refs): Textures, Meshes, Animations, OFPA")
            print("  - Semantic (full parse): Widgets, Blueprints, Materials")

        if index_path != "/Game":
            print(f"  Path filter: {index_path}")

        # Set up embeddings if requested
        embed_fn = None
        embed_model = None
        if use_embeddings:
            print()
            print("Loading sentence-transformers for embeddings...")
            try:
                from unreal_agent.knowledge_index.indexer import (
                    create_sentence_transformer_embedder,
                )

                embed_fn = create_sentence_transformer_embedder()
                embed_model = "all-MiniLM-L6-v2"
                print(f"  Model: {embed_model}")
                # Warm up the model
                _ = embed_fn("warmup")
                print("  Embeddings enabled")
            except ImportError:
                print(
                    "  WARNING: sentence-transformers not installed, skipping embeddings"
                )
                print("  Install with: pip install sentence-transformers")
                embed_fn = None
            except Exception as e:
                print(f"  WARNING: Failed to load embeddings: {e}")
                embed_fn = None
        print()

        # Discover plugin content folders so they get indexed too
        _discover_plugins()
        plugin_paths = [(mp, Path(cp)) for mp, cp in _plugin_paths.items()]
        if plugin_paths:
            print(
                f"  Plugins: {len(plugin_paths)} found ({', '.join(mp for mp, _ in plugin_paths)})"
            )

        from unreal_agent.project_profile import load_profile

        store = KnowledgeStore(db_path)
        project_profile = load_profile(emit_info=False)
        if project_profile.profile_name == "_defaults":
            print(
                "INFO: Using engine defaults. Profile not required for standard UE projects."
            )
            print()
        indexer = AssetIndexer(
            store,
            content_path,
            embed_fn=embed_fn,
            embed_model=embed_model,
            force=force_reindex,
            plugin_paths=plugin_paths if plugin_paths else None,
            profile=project_profile,
        )

        # Progress tracking with ETA
        import time as time_module

        batch_state = {"start_time": None, "phase_start": None, "last_phase": ""}

        def batch_progress(status_msg, current, total):
            now = time_module.time()

            # Reset phase timer on new phase
            phase = status_msg.split(":")[0] if ":" in status_msg else status_msg
            if phase != batch_state["last_phase"]:
                batch_state["phase_start"] = now
                batch_state["last_phase"] = phase
            if batch_state["start_time"] is None:
                batch_state["start_time"] = now

            if total > 0:
                pct = int(100 * current / total)
                eta_str = ""
                elapsed = now - (batch_state["phase_start"] or now)
                if current > 0 and elapsed > 2:
                    rate = current / elapsed
                    remaining = total - current
                    if rate > 0:
                        eta_seconds = remaining / rate
                        eta_str = f" - ETA: {format_eta(eta_seconds)}"
                sys.stdout.write(
                    f"\r  {status_msg}: [{current}/{total}] {pct}%{eta_str}          "
                )
            else:
                sys.stdout.write(f"\r  {status_msg}...          ")
            sys.stdout.flush()

        # Quick profile uses type filter on legacy method
        if profile == "quick":
            stats = indexer.index_folder(
                index_path,
                type_filter=["WidgetBlueprint", "DataTable", "MaterialInstance"],
                progress_callback=lambda p, c, t: batch_progress(
                    f"Indexing {p.split('/')[-1][:30]}", c, t
                ),
            )
        else:
            stats = indexer.index_folder_batch(
                index_path,
                batch_size=batch_size,
                progress_callback=batch_progress,
                profile=profile,
                max_assets=max_assets,
                type_filter=type_filter,
            )

        # Clear the progress line and show results
        sys.stdout.write("\r" + " " * 80 + "\r")
        print()
        print("Batch indexing complete:")
        print(f"  Total found: {stats.get('total_found', 0)}")
        if "lightweight_indexed" in stats:
            print(f"  Lightweight indexed: {stats.get('lightweight_indexed', 0)}")
            print(f"  Semantic indexed: {stats.get('semantic_indexed', 0)}")
        else:
            print(f"  Indexed: {stats.get('indexed', 0)}")
        print(f"  Unchanged: {stats.get('unchanged', 0)}")
        print(f"  Errors: {stats.get('errors', 0)}")
        if stats.get("by_type"):
            print()
            print("By type:")
            for asset_type, count in sorted(stats["by_type"].items()):
                print(f"  {asset_type}: {count}")
        sys.exit(0)

    if arg == "--backfill-embeddings":
        from pathlib import Path

        db_path = Path(get_project_db_path())
        if not db_path.exists():
            print(f"ERROR: No index found at {db_path}")
            print("Run --index-batch first to create the index.")
            sys.exit(1)

        batch_size = 100
        for i, a in enumerate(sys.argv):
            if a == "--batch-size" and i + 1 < len(sys.argv):
                try:
                    batch_size = max(10, min(500, int(sys.argv[i + 1])))
                except ValueError:
                    pass

        from unreal_agent.knowledge_index import KnowledgeStore, AssetIndexer

        print("Loading sentence-transformers for embedding backfill...")
        try:
            from unreal_agent.knowledge_index.indexer import (
                create_sentence_transformer_embedder,
            )

            embed_fn = create_sentence_transformer_embedder()
            embed_model = "all-MiniLM-L6-v2"
        except ImportError:
            print("ERROR: sentence-transformers not installed")
            print("Install with: pip install sentence-transformers")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR: Failed to load embedding model: {e}")
            sys.exit(1)

        if embed_fn is None:
            print("ERROR: sentence-transformers not installed")
            print("Install with: pip install sentence-transformers")
            sys.exit(1)

        # Warm up
        _ = embed_fn("warmup")
        print(f"  Model: {embed_model}")
        print(f"  Database: {db_path}")
        print(f"  Batch size: {batch_size}")
        print()

        store = KnowledgeStore(db_path)
        # Create a minimal indexer just for backfill (no content_path needed)
        indexer = AssetIndexer.__new__(AssetIndexer)
        indexer.store = store
        indexer.embed_fn = embed_fn
        indexer.embed_model = embed_model
        indexer.embed_version = "1.0"

        import time as time_module

        backfill_state = {"start_time": None}

        def backfill_progress(status_msg, current, total):
            now = time_module.time()
            if backfill_state["start_time"] is None:
                backfill_state["start_time"] = now
            if total > 0:
                pct = int(100 * current / total)
                eta_str = ""
                elapsed = now - backfill_state["start_time"]
                if current > 0 and elapsed > 2:
                    rate = current / elapsed
                    remaining = total - current
                    if rate > 0:
                        eta_seconds = remaining / rate
                        eta_str = f" - ETA: {format_eta(eta_seconds)}"
                sys.stdout.write(
                    f"\r  {status_msg}: [{current}/{total}] {pct}%{eta_str}          "
                )
            else:
                sys.stdout.write(f"\r  {status_msg}...          ")
            sys.stdout.flush()

        stats = indexer.backfill_embeddings(
            batch_size=batch_size,
            progress_callback=backfill_progress,
        )
        sys.stdout.write("\r" + " " * 80 + "\r")
        print()
        print("Embedding backfill complete:")
        print(f"  Total docs without embeddings: {stats['total']}")
        print(f"  Newly embedded: {stats['embedded']}")
        if stats.get("errors", 0) > 0:
            print(f"  Errors: {stats['errors']}")
        sys.exit(0)

    if arg == "--scan-cpp":
        from pathlib import Path

        if not PROJECT:
            print("ERROR: No project configured")
            sys.exit(1)

        project_root = Path(os.path.dirname(PROJECT))
        db_path = Path(get_project_db_path())
        db_path.parent.mkdir(parents=True, exist_ok=True)

        from unreal_agent.knowledge_index import KnowledgeStore

        store = KnowledgeStore(db_path)
        count = store.scan_cpp_classes(project_root)
        print(f"Scanned {count} C++ classes/structs into cpp_class_index")
        sys.exit(0)

    if arg == "--index-all":
        from pathlib import Path

        if not PROJECT:
            print("ERROR: No project configured")
            print(
                "Make sure config.json is set up or a .uproject file is in the parent directory"
            )
            sys.exit(1)

        content_path = Path(os.path.dirname(PROJECT)) / "Content"
        project_root = Path(os.path.dirname(PROJECT))

        db_path = Path(get_project_db_path())
        db_path.parent.mkdir(parents=True, exist_ok=True)

        from unreal_agent.knowledge_index import KnowledgeStore, AssetIndexer

        print("Building full semantic index (assets + C++ class scan)...")
        print(f"  Project: {project_root}")
        print(f"  Database: {db_path}")
        print()

        store = KnowledgeStore(db_path)

        # Progress tracking with ETA
        import time as time_module

        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        progress_state = {"idx": 0, "start_time": None}

        def progress(path, current, total):
            now = time_module.time()
            if progress_state["start_time"] is None:
                progress_state["start_time"] = now

            name = path.split("/")[-1] if "/" in path else path
            if len(name) > 35:
                name = name[:32] + "..."

            # Calculate ETA
            elapsed = now - progress_state["start_time"]
            eta_str = ""
            if current > 0 and elapsed > 2:
                rate = current / elapsed
                remaining = total - current
                if rate > 0:
                    eta_seconds = remaining / rate
                    eta_str = f" - ETA: {format_eta(eta_seconds)}"

            spinner = spinner_chars[progress_state["idx"] % len(spinner_chars)]
            progress_state["idx"] += 1
            pct = int(100 * current / total) if total > 0 else 0
            sys.stdout.write(
                f"\r  {spinner} [{current}/{total}] {pct}%{eta_str} - {name:<30}"
            )
            sys.stdout.flush()

        # Index assets
        if content_path.exists():
            print("Indexing assets...")
            from unreal_agent.project_profile import load_profile

            project_profile = load_profile(emit_info=False)
            if project_profile.profile_name == "_defaults":
                print(
                    "INFO: Using engine defaults. Profile not required for standard UE projects."
                )
            asset_indexer = AssetIndexer(store, content_path, profile=project_profile)
            asset_stats = asset_indexer.index_folder(
                "/Game", progress_callback=progress
            )
            sys.stdout.write("\r" + " " * 80 + "\r")
            print(
                f"  Assets: {asset_stats.get('indexed', 0)} indexed, {asset_stats.get('unchanged', 0)} unchanged"
            )
        else:
            print("  Content/ not found, skipping assets")

        # Scan C++ headers for class index
        print("Scanning C++ headers...")
        cpp_count = store.scan_cpp_classes(project_root)
        print(f"  C++ classes: {cpp_count} found")

        print()
        print("Full index build complete. Run --index-status to see summary.")
        sys.exit(0)

    # Configure - either by name or direct path
    try:
        if arg.endswith(".uproject"):
            configure(project_path=arg)
        else:
            configure(project_name=arg)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Project: {PROJECT}")
    print(f"UE Editor: {UE_EDITOR}")
    print()

    # Verify paths
    if not UE_EDITOR or not os.path.exists(UE_EDITOR):
        print(f"ERROR: UE Editor not found at {UE_EDITOR}")
        print("Set UE_EDITOR_PATH environment variable or check engine installation")
        sys.exit(1)

    print("All paths verified. Running test...")
    print()
    print("list_assets('/Game'):")
    print(list_assets("/Game"))
