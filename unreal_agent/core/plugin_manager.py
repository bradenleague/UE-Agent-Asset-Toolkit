import os
import sys

from .config import PROJECT, DEBUG

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
