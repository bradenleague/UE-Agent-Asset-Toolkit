import os
import json
import sys
from typing import Optional

# Set to True (or UE_AGENT_DEBUG=1) to see the exact commands being run
DEBUG = os.environ.get("UE_AGENT_DEBUG", "").lower() in ("1", "true", "yes")

_CORE_DIR = os.path.dirname(__file__)
_TOOL_DIR = os.path.dirname(_CORE_DIR)
CONFIG_FILE = os.path.join(_TOOL_DIR, "config.json")

# These get set by _load_config() or configure()
UE_EDITOR = ""
PROJECT = ""


def _load_config():
    """Load configuration from config.json, or auto-detect project if none exists."""
    global UE_EDITOR, PROJECT

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

                if PROJECT and os.path.exists(PROJECT):
                    return

        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Failed to load config: {e}", file=sys.stderr)

    _auto_detect_project()


def _auto_detect_project():
    """Auto-detect .uproject file in parent directories of Tools folder."""
    global UE_EDITOR, PROJECT

    # Tools folder is typically at ProjectRoot/Tools/UnrealAgent
    # _TOOL_DIR is UnrealAgent, so look 2 levels up for .uproject
    tools_parent = os.path.dirname(_TOOL_DIR)  # Tools
    project_root = os.path.dirname(tools_parent)  # ProjectRoot

    uproject_files = (
        [f for f in os.listdir(project_root) if f.endswith(".uproject")]
        if os.path.exists(project_root)
        else []
    )

    if len(uproject_files) == 1:
        uproject_path = os.path.join(project_root, uproject_files[0])
        PROJECT = uproject_path
        UE_EDITOR = _detect_engine_path(uproject_path)
        _auto_create_config(uproject_path, UE_EDITOR)
    elif len(uproject_files) > 1 and DEBUG:
        print(
            f"[DEBUG] Multiple .uproject files found, cannot auto-detect: {uproject_files}",
            file=sys.stderr,
        )


def _auto_create_config(project_path: str, engine_path: str):
    if os.path.exists(CONFIG_FILE):
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
            print(f"[DEBUG] Auto-created config.json for {project_name}", file=sys.stderr)
    except Exception as e:
        if DEBUG:
            print(f"[DEBUG] Failed to auto-create config: {e}", file=sys.stderr)


def configure(
    project_path: str = None, engine_path: str = None, project_name: str = None
):
    global PROJECT, UE_EDITOR

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
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"Config not found: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)

    projects = config.get("projects", {})
    if project_name not in projects:
        available = ", ".join(projects.keys())
        raise ValueError(f"Project '{project_name}' not in config. Available: {available}")

    config["active_project"] = project_name

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    configure(project_name=project_name)


def _detect_engine_path(project_path: str) -> str:
    # Top-level import path to engine_detect (up one module)
    try:
        from engine_detect import detect_engine_path
    except ImportError:
        import sys
        sys.path.append(os.path.dirname(os.path.dirname(__file__)))
        from engine_detect import detect_engine_path
        
    return detect_engine_path(project_path)


def add_project(
    name: str, project_path: str, engine_path: str = None, set_active: bool = True
):
    global PROJECT, UE_EDITOR

    project_path = os.path.abspath(os.path.expanduser(project_path))

    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Project not found: {project_path}")
    if not project_path.endswith(".uproject"):
        raise ValueError(f"Expected .uproject file, got: {project_path}")

    if not engine_path:
        engine_path = _detect_engine_path(project_path)

    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    else:
        config = {
            "active_project": "",
            "projects": {},
            "tools": {"timeout_seconds": 120, "default_asset_path": "/Game"},
        }

    config["projects"][name] = {
        "project_path": project_path,
        "engine_path": engine_path or "",
    }

    if set_active:
        config["active_project"] = name

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

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
    if not os.path.exists(CONFIG_FILE):
        return {"active": None, "projects": {}}
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    return {
        "active": config.get("active_project", ""),
        "projects": config.get("projects", {}),
    }


def get_active_project_name() -> Optional[str]:
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    return config.get("active_project") or None


def get_project_index_options(project_name: str = None) -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}
    if not project_name:
        project_name = get_active_project_name()
    if not project_name:
        return {}

    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)

    proj = config.get("projects", {}).get(project_name, {})
    return proj.get("index_options", {})


def set_project_index_options(options: dict, project_name: str = None):
    if not os.path.exists(CONFIG_FILE):
        return
    if not project_name:
        project_name = get_active_project_name()
    if not project_name:
        return

    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)

    proj = config.get("projects", {}).get(project_name)
    if proj is None:
        return

    existing = proj.get("index_options", {})
    for k, v in options.items():
        if v is None:
            existing.pop(k, None)
        else:
            existing[k] = v
    proj["index_options"] = existing

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

# Load config on module import
_load_config()
