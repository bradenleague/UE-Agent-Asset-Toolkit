import os
import sys
import json
import subprocess
import glob as globmod
from typing import Optional

from core import PROJECT, DEBUG, _plugin_paths, _discover_plugins
from pathutil import to_game_path_sep
from .heuristics import _guess_asset_type_from_name

# Re-use _paginate_results from old tools.py
def _paginate_results(results: list, limit: int, offset: int) -> str:
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

def _get_asset_parser_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(__file__))

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
            pass

    import platform
    system = platform.system()
    machine = platform.machine()
    parser_dir = os.path.join(base_dir, "..", "AssetParser", "bin", "Release", "net8.0")

    if system == "Windows":
        return os.path.join(parser_dir, "AssetParser.exe")
    elif system == "Darwin":
        rid = "osx-arm64" if machine == "arm64" else "osx-x64"
        self_contained = os.path.join(parser_dir, rid, "publish", "AssetParser")
        if os.path.exists(self_contained):
            return self_contained
        return os.path.join(parser_dir, "AssetParser")
    else:
        rid = "linux-arm64" if machine == "aarch64" else "linux-x64"
        self_contained = os.path.join(parser_dir, rid, "publish", "AssetParser")
        if os.path.exists(self_contained):
            return self_contained
        return os.path.join(parser_dir, "AssetParser")


def _asset_path_to_file(asset_path: str) -> str:
    from core.config import PROJECT
    
    if asset_path.startswith("/Game/"):
        relative_path = asset_path[6:]
        return os.path.join(
            os.path.dirname(PROJECT), "Content", relative_path + ".uasset"
        )

    if asset_path.startswith("/") and not asset_path.startswith("/Script/"):
        _discover_plugins()
        parts = asset_path.split("/")
        if len(parts) >= 2:
            mount_point = parts[1]
            if mount_point in _plugin_paths:
                relative_path = "/".join(parts[2:])
                return os.path.join(
                    _plugin_paths[mount_point], relative_path + ".uasset"
                )

    return asset_path


def _run_asset_parser(command: str, file_path: str) -> str:
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


def inspect_asset(
    asset_path: str,
    summarize: bool = False,
    type_only: bool = False,
    detail: Optional[str] = None,
) -> str:
    file_path = _asset_path_to_file(asset_path)

    if type_only:
        return _run_asset_parser("summary", file_path)

    if detail == "graph":
        return _run_asset_parser("graph-json", file_path)
    if not summarize:
        return _run_asset_parser("inspect", file_path)

    summary_json = _run_asset_parser("summary", file_path)
    try:
        summary = json.loads(summary_json)
    except json.JSONDecodeError:
        return _run_asset_parser("inspect", file_path)

    if "error" in summary:
        return summary_json

    asset_type = summary.get("asset_type", "Unknown")

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
        return _run_asset_parser("inspect", file_path)


def inspect_widget(asset_path: str) -> str:
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("widgets", file_path)


def inspect_datatable(asset_path: str) -> str:
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("datatable", file_path)


def inspect_blueprint(asset_path: str) -> str:
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("blueprint", file_path)


def inspect_blueprint_graph(asset_path: str) -> str:
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("graph-json", file_path)


def inspect_material(asset_path: str) -> str:
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("material", file_path)


def inspect_materialfunction(asset_path: str) -> str:
    file_path = _asset_path_to_file(asset_path)
    return _run_asset_parser("materialfunction", file_path)


def _list_assets_filesystem(
    path: str = "/Game",
    type_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    from core.config import PROJECT
    
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

    pattern = os.path.join(content_dir, "**", "*.uasset")
    files = globmod.glob(pattern, recursive=True)

    results = []
    skipped_uncertain = 0
    asset_parser = _get_asset_parser_path()
    has_parser = os.path.exists(asset_parser)

    parser_calls = 0
    MAX_PARSER_CALLS = 20

    for file_path in files:
        rel_path = os.path.relpath(file_path, os.path.join(project_dir, "Content"))
        asset_path = "/Game/" + to_game_path_sep(rel_path).replace(".uasset", "")
        asset_name = os.path.basename(file_path).replace(".uasset", "")

        asset_class = None

        if type_filter:
            guessed_type = _guess_asset_type_from_name(asset_name, file_path)

            if guessed_type:
                if guessed_type != type_filter:
                    continue
                asset_class = guessed_type
            elif has_parser and parser_calls < MAX_PARSER_CALLS:
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
                except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError, OSError):
                    skipped_uncertain += 1
                    continue
            else:
                skipped_uncertain += 1
                continue

        results.append({"path": asset_path, "name": asset_name, "class": asset_class})

    paginated_result = json.loads(_paginate_results(results, limit, offset))

    if skipped_uncertain > 0:
        paginated_result["warning"] = (
            f"Skipped {skipped_uncertain} assets with uncertain types. "
            "Use more specific paths or remove type_filter for complete results."
        )
    if not type_filter and paginated_result.get("assets"):
        if not any(asset.get("class") for asset in paginated_result["assets"]):
            paginated_result["hint"] += (
                " Asset class values may be null before indexing; "
                "run `python index.py` to populate detailed type metadata."
            )
    if parser_calls >= MAX_PARSER_CALLS:
        paginated_result["note"] = (
            f"Limited to {MAX_PARSER_CALLS} type detections to avoid slowdown. "
            "Results may be incomplete for type_filter queries on large folders."
        )

    return json.dumps(paginated_result, indent=2)


def list_assets(
    path: str = "/Game",
    type_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    use_ue: bool = False,
) -> str:
    limit = min(max(1, limit), 100)

    if use_ue:
        return json.dumps({"error": "use_ue is currently not implemented (requires missing run_ue_script function)."}, indent=2)

    return _list_assets_filesystem(path, type_filter, limit, offset)


def list_asset_folders(path: str = "/Game") -> str:
    from core.config import PROJECT
    
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

    folders = []
    direct_assets = 0

    for item in os.listdir(content_dir):
        item_path = os.path.join(content_dir, item)
        if os.path.isdir(item_path):
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

