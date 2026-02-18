"""UE Asset inspection tools using AssetParser (C#) for read-only operations.

Provides configuration management, asset listing, and inspection via the
AssetParser CLI which parses .uasset files directly.
"""

import subprocess
import json
import os
import sys
from typing import Optional

from pathutil import to_game_path_sep

# Set to True (or UE_AGENT_DEBUG=1) to see the exact commands being run
DEBUG = os.environ.get("UE_AGENT_DEBUG", "").lower() in ("1", "true", "yes")

# =============================================================================
# Formatting Helpers
# =============================================================================


def format_eta(seconds: float) -> str:
    """Format seconds as human-readable duration (e.g., '2m 30s')."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


# =============================================================================
# Configuration
# =============================================================================

_TOOL_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(_TOOL_DIR, "config.json")

# These get set by _load_config() or configure()
UE_EDITOR = ""
PROJECT = ""


def _load_config():
    """Load configuration from config.json, or auto-detect project if none exists."""
    global UE_EDITOR, PROJECT

    # Try to load existing config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)

            active = config.get("active_project", "")
            projects = config.get("projects", {})

            if active and active in projects:
                proj_config = projects[active]
                PROJECT = proj_config.get("project_path", "")
                UE_EDITOR = proj_config.get("engine_path", "")

                # Config loaded successfully
                if PROJECT and os.path.exists(PROJECT):
                    return

        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Failed to load config: {e}", file=sys.stderr)

    # Auto-detect: look for .uproject in parent directories
    _auto_detect_project()


def _auto_detect_project():
    """Auto-detect .uproject file in parent directories of Tools folder."""
    global UE_EDITOR, PROJECT

    # Tools folder is typically at ProjectRoot/Tools/UnrealAgent
    # So look 2 levels up for .uproject
    tool_dir = os.path.dirname(__file__)  # UnrealAgent
    tools_parent = os.path.dirname(tool_dir)  # Tools
    project_root = os.path.dirname(tools_parent)  # ProjectRoot

    # Look for .uproject files
    uproject_files = (
        [f for f in os.listdir(project_root) if f.endswith(".uproject")]
        if os.path.exists(project_root)
        else []
    )

    if len(uproject_files) == 1:
        uproject_path = os.path.join(project_root, uproject_files[0])
        PROJECT = uproject_path

        # Try to detect engine path
        UE_EDITOR = _detect_engine_path(uproject_path)

        # Auto-create config for convenience
        _auto_create_config(uproject_path, UE_EDITOR)
    elif len(uproject_files) > 1:
        if DEBUG:
            print(
                f"[DEBUG] Multiple .uproject files found, cannot auto-detect: {uproject_files}",
                file=sys.stderr,
            )


def _auto_create_config(project_path: str, engine_path: str):
    """Auto-create config.json with detected project."""
    if os.path.exists(CONFIG_FILE):
        # Don't overwrite existing config
        return

    project_name = os.path.splitext(os.path.basename(project_path))[0].lower()

    config = {
        "active_project": project_name,
        "projects": {
            project_name: {"project_path": project_path, "engine_path": engine_path}
        },
        "tools": {"timeout_seconds": 120, "default_asset_path": "/Game"},
    }

    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        if DEBUG:
            print(
                f"[DEBUG] Auto-created config.json for {project_name}", file=sys.stderr
            )
    except Exception as e:
        if DEBUG:
            print(f"[DEBUG] Failed to auto-create config: {e}", file=sys.stderr)


def configure(
    project_path: str = None, engine_path: str = None, project_name: str = None
):
    """Configure the tool for a specific UE project.

    Args:
        project_path: Path to .uproject file (direct)
        engine_path: Optional path to UnrealEditor-Cmd.exe
        project_name: Name of project in config.json (e.g., "lyra", "agent")
    """
    global PROJECT, UE_EDITOR

    # If project_name specified, load from config
    if project_name:
        if not os.path.exists(CONFIG_FILE):
            raise FileNotFoundError(f"Config not found: {CONFIG_FILE}")

        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)

        projects = config.get("projects", {})
        if project_name not in projects:
            available = ", ".join(projects.keys())
            raise ValueError(
                f"Project '{project_name}' not in config. Available: {available}"
            )

        proj_config = projects[project_name]
        project_path = proj_config.get("project_path", "")
        engine_path = engine_path or proj_config.get("engine_path", "")

    if not project_path:
        raise ValueError("Either project_path or project_name is required")

    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Project not found: {project_path}")

    PROJECT = project_path

    if engine_path:
        UE_EDITOR = engine_path
    elif not UE_EDITOR:
        UE_EDITOR = _detect_engine_path(project_path)


def set_active_project(project_name: str):
    """Set the active project in config.json and reload."""
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"Config not found: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)

    projects = config.get("projects", {})
    if project_name not in projects:
        available = ", ".join(projects.keys())
        raise ValueError(
            f"Project '{project_name}' not in config. Available: {available}"
        )

    config["active_project"] = project_name

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    # Reload
    configure(project_name=project_name)


def _detect_engine_path(project_path: str) -> str:
    """Try to detect UE Editor path from project file."""
    import platform

    system = platform.system()

    try:
        with open(project_path, "r") as f:
            proj = json.load(f)
            engine_assoc = proj.get("EngineAssociation", "")

            possible_paths = []

            if system == "Windows":
                possible_paths = [
                    # Installed builds (Epic launcher)
                    rf"C:\Program Files\Epic Games\UE_{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe",
                    # Source builds
                    rf"D:\UnrealDev\UE_{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe",
                    rf"D:\UnrealDev\{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe",
                ]
            elif system == "Darwin":
                # macOS paths
                possible_paths = [
                    # Epic launcher installs
                    f"/Users/Shared/Epic Games/UE_{engine_assoc}/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor",
                    f"/Users/Shared/Epic Games/UE_{engine_assoc}/Engine/Binaries/Mac/UnrealEditor-Cmd",
                    # Source builds
                    os.path.expanduser(
                        f"~/UnrealEngine/UE_{engine_assoc}/Engine/Binaries/Mac/UnrealEditor-Cmd"
                    ),
                ]
            else:
                # Linux paths
                possible_paths = [
                    os.path.expanduser(
                        f"~/UnrealEngine/UE_{engine_assoc}/Engine/Binaries/Linux/UnrealEditor-Cmd"
                    ),
                    f"/opt/unreal-engine/UE_{engine_assoc}/Engine/Binaries/Linux/UnrealEditor-Cmd",
                ]

            for path in possible_paths:
                if os.path.exists(path):
                    return path

    except Exception:
        pass

    return ""


def add_project(
    name: str, project_path: str, engine_path: str = None, set_active: bool = True
):
    """Add a new project to config.json.

    This allows registering any UE project from anywhere, not just when
    the tool is installed inside the project folder.

    Args:
        name: Short name for the project (e.g., "rivemac", "myproject")
        project_path: Full path to the .uproject file
        engine_path: Optional path to UnrealEditor-Cmd (auto-detected if not provided)
        set_active: Whether to make this the active project (default: True)

    Example:
        add_project("rivemac", "/Users/me/Projects/RiveMac/RiveMac.uproject")
    """
    global PROJECT, UE_EDITOR

    # Expand and normalize path
    project_path = os.path.abspath(os.path.expanduser(project_path))

    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Project not found: {project_path}")

    if not project_path.endswith(".uproject"):
        raise ValueError(f"Expected .uproject file, got: {project_path}")

    # Auto-detect engine if not provided
    if not engine_path:
        engine_path = _detect_engine_path(project_path)

    # Load or create config
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    else:
        config = {
            "active_project": "",
            "projects": {},
            "tools": {"timeout_seconds": 120, "default_asset_path": "/Game"},
        }

    # Add/update project
    config["projects"][name] = {
        "project_path": project_path,
        "engine_path": engine_path or "",
    }

    if set_active:
        config["active_project"] = name

    # Save config
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    # Update globals if set as active
    if set_active:
        PROJECT = project_path
        UE_EDITOR = engine_path or ""

    return {
        "name": name,
        "project_path": project_path,
        "engine_path": engine_path or "(not detected)",
        "active": set_active,
    }


def list_projects():
    """List all configured projects.

    Returns:
        dict with active project and list of all projects
    """
    if not os.path.exists(CONFIG_FILE):
        return {"active": None, "projects": {}}

    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)

    return {
        "active": config.get("active_project", ""),
        "projects": config.get("projects", {}),
    }


def get_active_project_name() -> Optional[str]:
    """Get the name of the active project from config.

    Returns:
        Project name (e.g., "lyra") or None if no project configured
    """
    if not os.path.exists(CONFIG_FILE):
        return None

    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)

    return config.get("active_project") or None


def get_project_db_path(project_name: str = None) -> str:
    """Get the database path for a project.

    Per-project databases are stored at: data/{project_name}.db

    Args:
        project_name: Project name, or None for active project

    Returns:
        Full path to the project's database file
    """
    if not project_name:
        project_name = get_active_project_name()

    if not project_name:
        # Fallback for backwards compatibility
        return os.path.join(_TOOL_DIR, "data", "knowledge_index.db")

    return os.path.join(_TOOL_DIR, "data", f"{project_name}.db")


# Plugin mount points cache: mount_point -> content_path
# e.g., {"ShooterCore": "C:/Project/Plugins/GameFeatures/ShooterCore/Content"}
_plugin_paths: dict[str, str] = {}


def _discover_plugins():
    """Discover plugin content folders and their mount points.

    Called lazily when needed. Caches results in _plugin_paths.
    """
    global _plugin_paths

    if _plugin_paths:  # Already discovered
        return

    if not PROJECT:
        return

    project_dir = os.path.dirname(PROJECT)
    plugins_dir = os.path.join(project_dir, "Plugins")

    if not os.path.exists(plugins_dir):
        return

    # Recursively find all Content folders under Plugins
    for root, dirs, files in os.walk(plugins_dir):
        if os.path.basename(root) == "Content":
            # Check if this Content folder has any .uasset files
            has_assets = any(f.endswith(".uasset") for f in files)
            if not has_assets:
                # Check subdirectories
                for d in dirs:
                    subdir = os.path.join(root, d)
                    if any(
                        f.endswith(".uasset")
                        for f in os.listdir(subdir)
                        if os.path.isfile(os.path.join(subdir, f))
                    ):
                        has_assets = True
                        break
                if not has_assets:
                    # Recursively check for any .uasset
                    for _, _, subfiles in os.walk(root):
                        if any(f.endswith(".uasset") for f in subfiles):
                            has_assets = True
                            break

            if has_assets:
                # Mount point is the parent folder name (the plugin name)
                mount_point = os.path.basename(os.path.dirname(root))
                if mount_point not in _plugin_paths:
                    _plugin_paths[mount_point] = root
                    if DEBUG:
                        print(
                            f"[DEBUG] Found plugin: {mount_point} -> {root}",
                            file=sys.stderr,
                        )


def get_plugin_paths() -> dict[str, str]:
    """Get discovered plugin mount points and their content paths."""
    _discover_plugins()
    return _plugin_paths.copy()


# Load config on module import
_load_config()


# =============================================================================
# Tool Functions - These are what the agent calls
# =============================================================================


def list_assets(
    path: str = "/Game",
    type_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    use_ue: bool = False,
) -> str:
    """List assets in a path with pagination to avoid context bloat.

    By default, uses fast file-system scan (no UE required).
    Set use_ue=True to use UE Asset Registry (slower, requires UE).

    Args:
        path: Asset path to search (e.g., /Game, /Game/Characters)
        type_filter: Class name to filter by (e.g., Blueprint, WidgetBlueprint)
        limit: Maximum number of assets to return (default 50, max 100)
        offset: Number of assets to skip for pagination
        use_ue: Use UE Asset Registry instead of filesystem scan
    """
    # Clamp limit to prevent accidental context bloat
    limit = min(max(1, limit), 100)

    if use_ue:
        # Use UE Python script (requires working UE setup)
        args = [path]
        if type_filter:
            args.append(type_filter)
        result = run_ue_script("list_assets.py", *args)
        # Apply pagination to UE results too
        if isinstance(result, list):
            return _paginate_results(result, limit, offset)
        return json.dumps(result, indent=2)

    # File-system scan (fast, works without UE)
    return _list_assets_filesystem(path, type_filter, limit, offset)


def _paginate_results(results: list, limit: int, offset: int) -> str:
    """Apply pagination to results and return JSON with metadata."""
    total = len(results)
    paginated = results[offset : offset + limit]

    return json.dumps(
        {
            "assets": paginated,
            "pagination": {
                "total": total,
                "returned": len(paginated),
                "offset": offset,
                "limit": limit,
                "has_more": (offset + limit) < total,
            },
            "hint": f"Showing {len(paginated)} of {total} assets."
            + (
                f" Use offset={offset + limit} for next page."
                if (offset + limit) < total
                else ""
            ),
        },
        indent=2,
    )


def _guess_asset_type_from_name(asset_name: str, file_path: str) -> Optional[str]:
    """Fast heuristic to guess asset type from naming conventions.

    Returns None if uncertain - caller should use AssetParser for definitive answer.
    Note: Heuristics are imperfect but provide ~10-100x speedup over parsing every file.
    """
    name_lower = asset_name.lower()
    path_lower = file_path.lower()

    # Skip built data and generated files (these are usually not what users want)
    if "_builtdata" in name_lower:
        return "_BuiltData"  # Special marker to skip

    # Common UE naming prefixes (high confidence)
    if name_lower.startswith("bp_"):
        return "Blueprint"
    if name_lower.startswith("wbp_") or name_lower.startswith("wb_"):
        return "WidgetBlueprint"
    if name_lower.startswith("dt_"):
        return "DataTable"
    if name_lower.startswith("da_"):
        return "DataAsset"
    if name_lower.startswith("mi_"):
        return "MaterialInstance"
    if name_lower.startswith("mf_"):
        return "MaterialFunction"
    if name_lower.startswith("m_"):
        return "Material"
    if name_lower.startswith("t_"):
        return "Texture2D"
    if name_lower.startswith("sm_"):
        return "StaticMesh"
    if name_lower.startswith("sk_") or name_lower.startswith("skm_"):
        return "SkeletalMesh"
    if name_lower.startswith("abp_"):
        return "AnimBlueprint"
    if name_lower.startswith("am_"):
        return "AnimMontage"
    if name_lower.startswith("gc_"):
        return "GameplayCue"
    if name_lower.startswith("ga_"):
        return "GameplayAbility"
    if name_lower.startswith("ge_"):
        return "GameplayEffect"

    # Project-specific patterns (less strict prefixes)
    if name_lower.startswith("w_") and ("/ui/" in path_lower or "widget" in name_lower):
        return "WidgetBlueprint"
    # TODO(P1): replace with profile-driven path patterns
    if name_lower.startswith("b_") and "/experiences/" in path_lower:
        return "Blueprint"  # Experience blueprints

    # Path-based heuristics (lower confidence)
    if "/ui/" in path_lower or "/widgets/" in path_lower:
        if "widget" in name_lower or name_lower.startswith("w_"):
            return "WidgetBlueprint"
    if "/datatables/" in path_lower:
        return "DataTable"

    return None


def _list_assets_filesystem(
    path: str = "/Game",
    type_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """List assets by scanning the Content folder directly with pagination.

    Type filtering uses fast naming convention heuristics first, then falls back
    to AssetParser only for uncertain assets (up to a limit).
    """
    import glob as globmod

    # Convert /Game/Foo to Content/Foo
    if path.startswith("/Game"):
        relative_path = path[6:] if len(path) > 6 else ""  # Remove "/Game" or "/Game/"
        relative_path = relative_path.lstrip("/")
    else:
        relative_path = path.lstrip("/")

    # Get project content folder
    if not PROJECT:
        return json.dumps({"error": "No project configured. Call configure() first."})

    project_dir = os.path.dirname(PROJECT)
    content_dir = os.path.join(project_dir, "Content", relative_path)

    if not os.path.exists(content_dir):
        return json.dumps({"error": f"Path not found: {content_dir}"})

    # Find all .uasset files
    pattern = os.path.join(content_dir, "**", "*.uasset")
    files = globmod.glob(pattern, recursive=True)

    results = []
    skipped_uncertain = 0
    asset_parser = _get_asset_parser_path()
    has_parser = os.path.exists(asset_parser)

    # Track how many expensive parser calls we make
    parser_calls = 0
    MAX_PARSER_CALLS = 20  # Limit expensive operations

    for file_path in files:
        # Convert file path back to asset path
        rel_path = os.path.relpath(file_path, os.path.join(project_dir, "Content"))
        asset_path = "/Game/" + to_game_path_sep(rel_path).replace(".uasset", "")
        asset_name = os.path.basename(file_path).replace(".uasset", "")

        asset_class = None

        if type_filter:
            # First try fast heuristic
            guessed_type = _guess_asset_type_from_name(asset_name, file_path)

            if guessed_type:
                # We have a confident guess
                if guessed_type != type_filter:
                    continue  # Skip non-matching
                asset_class = guessed_type
            elif has_parser and parser_calls < MAX_PARSER_CALLS:
                # Uncertain - use parser but limit calls
                try:
                    result = subprocess.run(
                        [asset_parser, "summary", file_path],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    parser_calls += 1
                    if result.returncode == 0:
                        summary = json.loads(result.stdout)
                        asset_class = summary.get("asset_type", "Unknown")
                        if asset_class != type_filter:
                            continue
                except (
                    subprocess.TimeoutExpired,
                    subprocess.SubprocessError,
                    json.JSONDecodeError,
                    OSError,
                ):
                    skipped_uncertain += 1
                    continue
            else:
                # Can't determine type, skip
                skipped_uncertain += 1
                continue

        results.append({"path": asset_path, "name": asset_name, "class": asset_class})

    # Apply pagination
    paginated_result = json.loads(_paginate_results(results, limit, offset))

    # Add warning if we skipped uncertain assets
    if skipped_uncertain > 0:
        paginated_result["warning"] = (
            f"Skipped {skipped_uncertain} assets with uncertain types. "
            "Use more specific paths or remove type_filter for complete results."
        )
    if not type_filter and paginated_result.get("assets"):
        if not any(asset.get("class") for asset in paginated_result["assets"]):
            paginated_result["hint"] += (
                " Asset class values may be null before indexing; "
                "run `python index.py --all` to populate detailed type metadata."
            )
    if parser_calls >= MAX_PARSER_CALLS:
        paginated_result["note"] = (
            f"Limited to {MAX_PARSER_CALLS} type detections to avoid slowdown. "
            "Results may be incomplete for type_filter queries on large folders."
        )

    return json.dumps(paginated_result, indent=2)


def list_asset_folders(path: str = "/Game") -> str:
    """List subfolders and asset counts in a path (lightweight discovery).

    Use this to explore the project structure before listing specific assets.
    Returns folder names with asset counts, much lighter than listing all assets.
    """
    import glob as globmod

    # Convert /Game/Foo to Content/Foo
    if path.startswith("/Game"):
        relative_path = path[6:] if len(path) > 6 else ""
        relative_path = relative_path.lstrip("/")
    else:
        relative_path = path.lstrip("/")

    if not PROJECT:
        return json.dumps({"error": "No project configured. Call configure() first."})

    project_dir = os.path.dirname(PROJECT)
    content_dir = os.path.join(project_dir, "Content", relative_path)

    if not os.path.exists(content_dir):
        return json.dumps({"error": f"Path not found: {content_dir}"})

    # Get immediate subfolders
    folders = []
    direct_assets = 0

    for item in os.listdir(content_dir):
        item_path = os.path.join(content_dir, item)
        if os.path.isdir(item_path):
            # Count assets in this folder (recursive)
            pattern = os.path.join(item_path, "**", "*.uasset")
            asset_count = len(globmod.glob(pattern, recursive=True))
            folders.append(
                {
                    "name": item,
                    "path": f"{path.rstrip('/')}/{item}",
                    "asset_count": asset_count,
                }
            )
        elif item.endswith(".uasset"):
            direct_assets += 1

    # Sort by asset count descending
    folders.sort(key=lambda x: x["asset_count"], reverse=True)

    return json.dumps(
        {
            "path": path,
            "folders": folders,
            "direct_assets": direct_assets,
            "total_subfolders": len(folders),
            "hint": "Use list_assets with a specific folder path to see assets in that folder.",
        },
        indent=2,
    )


def inspect_asset(
    asset_path: str,
    summarize: bool = False,
    type_only: bool = False,
    detail: Optional[str] = None,
) -> str:
    """Get all properties and values of an asset.

    Uses AssetParser (C# tool) to parse the .uasset binary directly.
    This provides comprehensive asset inspection without requiring UE Editor.

    Args:
        asset_path: Unreal asset path (e.g., /Game/Materials/M_Base)
        summarize: If True, returns focused data based on asset type instead of full dump.
                   Recommended for materials, blueprints, datatables, and widgets.
        type_only: If True, returns just asset type and metadata (fast, no content parsing).
                   Use this when you just need to identify what kind of asset it is.
        detail: For Blueprints, request deeper analysis:
                "graph" = K2Node visual graph (pin connections, data flow between nodes)
                "bytecode" = control flow graph with pseudocode (branches, loops, execution logic)
    """
    file_path = _asset_path_to_file(asset_path)

    # Fast path: just return type/metadata
    if type_only:
        return _run_asset_parser("summary", file_path)

    # Deep Blueprint analysis modes
    if detail == "graph":
        return _run_asset_parser("graph", file_path)
    elif detail == "bytecode":
        return _run_asset_parser("bytecode", file_path)

    if not summarize:
        return _run_asset_parser("inspect", file_path)

    # Smart summarization: detect type and route to specialized command
    summary_json = _run_asset_parser("summary", file_path)
    try:
        summary = json.loads(summary_json)
    except json.JSONDecodeError:
        # Fall back to full inspect if summary fails
        return _run_asset_parser("inspect", file_path)

    if "error" in summary:
        return summary_json

    asset_type = summary.get("asset_type", "Unknown")

    # Route to specialized extraction based on type
    if asset_type in ("Material", "MaterialInstance"):
        return _run_asset_parser("material", file_path)
    elif asset_type == "MaterialFunction":
        return _run_asset_parser("materialfunction", file_path)
    elif asset_type == "WidgetBlueprint":
        return _run_asset_parser("widgets", file_path)
    elif asset_type == "DataTable":
        return _run_asset_parser("datatable", file_path)
    elif asset_type == "Blueprint":
        return _run_asset_parser("blueprint", file_path)
    else:
        # No specialized handler, return full inspect
        return _run_asset_parser("inspect", file_path)


def _get_asset_parser_path() -> str:
    """Get path to AssetParser CLI executable.

    Checks in order:
    1. local_config.json (created by setup.sh with platform-specific path)
    2. Platform-appropriate default paths
    """
    base_dir = os.path.dirname(__file__)

    # Check local_config.json first (created by setup.sh)
    local_config_path = os.path.join(base_dir, "local_config.json")
    if os.path.exists(local_config_path):
        try:
            with open(local_config_path, "r") as f:
                local_config = json.load(f)
                if "asset_parser_path" in local_config:
                    parser_path = local_config["asset_parser_path"]
                    if os.path.exists(parser_path):
                        return parser_path
        except (json.JSONDecodeError, IOError):
            pass  # Fall through to defaults

    # Platform-specific defaults
    import platform

    system = platform.system()
    machine = platform.machine()

    parser_dir = os.path.join(base_dir, "..", "AssetParser", "bin", "Release", "net8.0")

    if system == "Windows":
        return os.path.join(parser_dir, "AssetParser.exe")
    elif system == "Darwin":
        # macOS: check for self-contained build
        rid = "osx-arm64" if machine == "arm64" else "osx-x64"
        self_contained = os.path.join(parser_dir, rid, "publish", "AssetParser")
        if os.path.exists(self_contained):
            return self_contained
        return os.path.join(parser_dir, "AssetParser")
    else:
        # Linux: check for self-contained build
        rid = "linux-arm64" if machine == "aarch64" else "linux-x64"
        self_contained = os.path.join(parser_dir, rid, "publish", "AssetParser")
        if os.path.exists(self_contained):
            return self_contained
        return os.path.join(parser_dir, "AssetParser")


def _asset_path_to_file(asset_path: str) -> str:
    """Convert Unreal asset path to file system path.

    /Game/Blueprints/MyWidget -> Content/Blueprints/MyWidget.uasset
    /ShooterCore/UI/Widget -> Plugins/GameFeatures/ShooterCore/Content/UI/Widget.uasset
    """
    # Handle /Game/ paths (main content)
    if asset_path.startswith("/Game/"):
        relative_path = asset_path[6:]  # Remove "/Game/"
        return os.path.join(
            os.path.dirname(PROJECT), "Content", relative_path + ".uasset"
        )

    # Handle plugin paths (e.g., /ShooterCore/, /LyraExampleContent/)
    # Plugin paths start with /<PluginName>/
    if asset_path.startswith("/") and not asset_path.startswith("/Script/"):
        _discover_plugins()  # Ensure plugins are discovered

        # Extract potential mount point from path
        parts = asset_path.split("/")
        if len(parts) >= 2:
            mount_point = parts[1]  # e.g., "ShooterCore" from "/ShooterCore/UI/Widget"

            if mount_point in _plugin_paths:
                # Found matching plugin
                relative_path = "/".join(parts[2:])  # e.g., "UI/Widget"
                return os.path.join(
                    _plugin_paths[mount_point], relative_path + ".uasset"
                )

    # Fallback: return as-is (might already be a filesystem path)
    return asset_path


def _run_asset_parser(command: str, file_path: str) -> str:
    """Run AssetParser with the given command and file path.

    Args:
        command: AssetParser command (summary, inspect, widgets, datatable, blueprint)
        file_path: Path to the .uasset file

    Returns:
        JSON string with result or error
    """
    # Check if file exists
    if not os.path.exists(file_path):
        return json.dumps({"error": f"Asset file not found: {file_path}"}, indent=2)

    asset_parser = _get_asset_parser_path()

    if not os.path.exists(asset_parser):
        return json.dumps(
            {
                "error": "AssetParser not built",
                "hint": "Run: cd Tools/AssetParser && dotnet build -c Release",
            },
            indent=2,
        )

    try:
        result = subprocess.run(
            [asset_parser, command, file_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return result.stdout
        else:
            return json.dumps(
                {
                    "error": f"AssetParser {command} failed",
                    "stderr": result.stderr[:500] if result.stderr else "",
                    "stdout": result.stdout[:500] if result.stdout else "",
                },
                indent=2,
            )

    except subprocess.TimeoutExpired:
        return json.dumps({"error": "AssetParser timed out"}, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to run AssetParser: {e}"}, indent=2)


def inspect_widget(asset_path: str) -> str:
    """Get widget hierarchy from a Widget Blueprint.

    Uses UAssetAPI (C# tool) to parse the .uasset binary directly.
    This bypasses the protected WidgetTree limitation in Python.
    """
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("widgets", file_path)


def inspect_datatable(asset_path: str) -> str:
    """Get all rows and values from a DataTable asset.

    Returns the row struct type, column names, and all row data.
    Use this for DataTable assets specifically.
    """
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("datatable", file_path)


def inspect_blueprint(asset_path: str) -> str:
    """Get detailed Blueprint information including functions, variables, and class hierarchy.

    Returns all functions with their flags and bytecode info, class properties,
    parent class chain, and interfaces. Use this for Blueprint and WidgetBlueprint assets.
    """
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("blueprint", file_path)


def inspect_blueprint_graph(asset_path: str) -> str:
    """Get Blueprint visual graph data: nodes, pin connections, and data flow.

    Returns each function's node graph with K2Node types, pin details (name,
    direction, type, default values), and node-to-node connections.
    Use this to understand how a Blueprint's logic is wired together.
    """
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("graph", file_path)


def inspect_blueprint_bytecode(asset_path: str) -> str:
    """Get Blueprint bytecode as control flow graph with pseudocode.

    Returns per-function control flow graphs with basic blocks, edges,
    branch conditions, loop detection, and pseudocode for each instruction.
    Use this to understand the precise execution logic of a Blueprint.
    """
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("bytecode", file_path)


def inspect_material(asset_path: str) -> str:
    """Get Material or MaterialInstance parameters.

    Returns scalar parameters (name, default value, group), vector parameters
    (name, RGBA default, group), texture parameters (name, texture reference),
    material domain, blend mode, shading model, and parent material for instances.

    Use this for Material and MaterialInstance assets.
    """
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("material", file_path)


def inspect_materialfunction(asset_path: str) -> str:
    """Get MaterialFunction inputs, outputs, and parameters.

    Returns function inputs (name, type, priority), function outputs (name, priority),
    and parameters (scalar, vector, static switch) with their defaults and groups.

    Use this for MaterialFunction assets (MF_* prefix).
    """
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("materialfunction", file_path)


# =============================================================================
# Story Tools - Now handled by sub-agents in agent.py
# =============================================================================
# The explore_folder and explain_asset functions have been moved to sub-agents.
# See story_agents.py for the implementation and agent.py for integration.
# The atomic tools below are used by the sub-agents.


# =============================================================================
# Tool Registry
# =============================================================================

TOOLS = [
    # === ATOMIC TOOLS ===
    # Story tools (explore_folder, explain_asset) are now handled by sub-agents in agent.py
    # These atomic tools are used by the sub-agents for their exploration/explanation.
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
        "description": "Get all properties and values of an asset. Use summarize=True for focused output, type_only=True for just asset type/metadata. For Blueprints, use detail='graph' for visual node wiring or detail='bytecode' for control flow pseudocode.",
        "function": inspect_asset,
        "parameters": {
            "asset_path": {"type": "string"},
            "summarize": {"type": "boolean", "default": False, "optional": True},
            "type_only": {"type": "boolean", "default": False, "optional": True},
            "detail": {"type": "string", "optional": True, "enum": ["graph", "bytecode"]},
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
        "name": "inspect_blueprint_bytecode",
        "description": "Internal. Get Blueprint bytecode CFG. Prefer inspect_asset(detail='bytecode').",
        "function": inspect_blueprint_bytecode,
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
        print("  python index.py --all                 # Full hybrid index")
        print("  python index.py --quick               # High-value types only")
        print("  python index.py --source              # Index C++ source files")
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

        from knowledge_index import KnowledgeStore

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

        # Show C++ source summary
        cpp_types = ["source_file", "cpp_class", "cpp_func", "cpp_property"]
        cpp_count = sum(status.docs_by_type.get(t, 0) for t in cpp_types)
        if cpp_count > 0:
            print()
            print("C++ Source Summary:")
            print(f"  Total C++ documents: {cpp_count}")

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

        from knowledge_index import KnowledgeStore, AssetIndexer

        print("Building semantic index...")
        print(f"  Content: {content_path}")
        print(f"  Database: {db_path}")
        print()

        from project_profile import load_profile
        store = KnowledgeStore(db_path)
        project_profile = load_profile(emit_info=False)
        if project_profile.profile_name == "_defaults":
            print("INFO: Using engine defaults. Profile not required for standard UE projects.")
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

        from knowledge_index import KnowledgeStore, AssetIndexer

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
                from knowledge_index.indexer import create_sentence_transformer_embedder

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

        from project_profile import load_profile
        store = KnowledgeStore(db_path)
        project_profile = load_profile(emit_info=False)
        if project_profile.profile_name == "_defaults":
            print("INFO: Using engine defaults. Profile not required for standard UE projects.")
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

        from knowledge_index import KnowledgeStore, AssetIndexer

        print("Loading sentence-transformers for embedding backfill...")
        try:
            from knowledge_index.indexer import create_sentence_transformer_embedder

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

    if arg == "--index-source":
        from pathlib import Path

        if not PROJECT:
            print("ERROR: No project configured")
            print(
                "Make sure config.json is set up or a .uproject file is in the parent directory"
            )
            sys.exit(1)

        project_root = Path(os.path.dirname(PROJECT))
        source_path = project_root / "Source"
        plugins_path = project_root / "Plugins"

        if not source_path.exists() and not plugins_path.exists():
            print("ERROR: No Source/ or Plugins/ folder found")
            print(f"Looked in: {project_root}")
            sys.exit(1)

        db_path = Path(get_project_db_path())
        db_path.parent.mkdir(parents=True, exist_ok=True)

        from knowledge_index import KnowledgeStore, SourceIndexer

        print("Indexing C++ source files...")
        print(f"  Project: {project_root}")
        print(f"  Database: {db_path}")
        print()

        store = KnowledgeStore(db_path)
        indexer = SourceIndexer(store, PROJECT)

        # Progress tracking with ETA
        import time as time_module

        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        progress_state = {"idx": 0, "start_time": None}

        def progress(path, current, total):
            now = time_module.time()
            if progress_state["start_time"] is None:
                progress_state["start_time"] = now

            # Get file name from path
            file_name = path.split("/")[-1] if "/" in path else path
            if len(file_name) > 35:
                file_name = file_name[:32] + "..."

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
                f"\r  {spinner} [{current}/{total}] {pct}%{eta_str} - {file_name:<30}"
            )
            sys.stdout.flush()

        # Index Source/ folder
        if source_path.exists():
            print("Indexing Source/...")
            stats1 = indexer.index_source(progress_callback=progress)
            sys.stdout.write("\r" + " " * 80 + "\r")
            print(
                f"  Source/: {stats1.get('indexed', 0)} files indexed, {stats1.get('unchanged', 0)} unchanged"
            )
        else:
            stats1 = {"total": 0, "indexed": 0, "unchanged": 0, "errors": 0}
            print("  Source/ not found, skipping")

        # Index Plugins/ folder
        if plugins_path.exists():
            print("Indexing Plugins/...")
            stats2 = indexer.index_plugins(progress_callback=progress)
            sys.stdout.write("\r" + " " * 80 + "\r")
            print(
                f"  Plugins/: {stats2.get('indexed', 0)} files indexed, {stats2.get('unchanged', 0)} unchanged"
            )
        else:
            stats2 = {"total": 0, "indexed": 0, "unchanged": 0, "errors": 0}
            print("  Plugins/ not found, skipping")

        print()
        total_indexed = stats1.get("indexed", 0) + stats2.get("indexed", 0)
        total_unchanged = stats1.get("unchanged", 0) + stats2.get("unchanged", 0)
        total_errors = stats1.get("errors", 0) + stats2.get("errors", 0)
        total_purged = stats1.get("purged_docs", 0) + stats2.get("purged_docs", 0)
        print("C++ source indexing complete:")
        print(f"  Total indexed: {total_indexed}")
        print(f"  Total unchanged: {total_unchanged}")
        if total_purged > 0:
            print(f"  Purged generated/intermediate docs: {total_purged}")
        if total_errors > 0:
            print(f"  Errors: {total_errors}")
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

        from knowledge_index import KnowledgeStore, AssetIndexer, SourceIndexer

        print("Building full semantic index (assets + source)...")
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
            from project_profile import load_profile
            project_profile = load_profile(emit_info=False)
            if project_profile.profile_name == "_defaults":
                print("INFO: Using engine defaults. Profile not required for standard UE projects.")
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

        # Index source
        source_path = project_root / "Source"
        plugins_path = project_root / "Plugins"
        if source_path.exists() or plugins_path.exists():
            print("Indexing C++ source...")
            source_indexer = SourceIndexer(store, PROJECT)
            source_stats = source_indexer.index_all(progress_callback=progress)
            sys.stdout.write("\r" + " " * 80 + "\r")
            print(
                f"  Source: {source_stats.get('indexed', 0)} indexed, {source_stats.get('unchanged', 0)} unchanged"
            )
            if source_stats.get("purged_docs", 0) > 0:
                print(
                    f"  Source purge: {source_stats.get('purged_docs', 0)} generated/intermediate docs removed"
                )
        else:
            print("  Source/Plugins not found, skipping C++ source")

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
