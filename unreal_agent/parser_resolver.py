"""Shared AssetParser binary path resolution.

Resolution order:
1. local_config.json (set by setup.py)
2. In-tree platform-specific paths (repo checkout)
3. Download from GitHub Releases (cached) — future, see parser_download.py
"""

import json
import os
import platform
from pathlib import Path


def resolve_parser_path(local_config_dir: Path | None = None) -> str | None:
    """Resolve the AssetParser binary path.

    Args:
        local_config_dir: Directory containing local_config.json.
            Defaults to the unreal_agent package directory.

    Returns:
        Path to the AssetParser binary, or None if not found.
    """
    if local_config_dir is None:
        local_config_dir = Path(__file__).parent

    # 1. Check local_config.json (set by setup.py)
    local_config_path = local_config_dir / "local_config.json"
    if local_config_path.exists():
        try:
            with open(local_config_path, "r") as f:
                local_config = json.load(f)
                if "asset_parser_path" in local_config:
                    parser_path = local_config["asset_parser_path"]
                    if os.path.exists(parser_path):
                        return parser_path
        except (json.JSONDecodeError, IOError):
            pass

    # 2. In-tree platform-specific paths (repo checkout)
    base_dir = local_config_dir
    system = platform.system()
    machine = platform.machine()
    parser_dir = base_dir / ".." / "AssetParser" / "bin" / "Release" / "net8.0"

    if system == "Windows":
        candidate = parser_dir / "AssetParser.exe"
        if candidate.exists():
            return str(candidate)
        return str(candidate)  # return path even if missing for error messages
    else:
        if system == "Darwin":
            rid = "osx-arm64" if machine == "arm64" else "osx-x64"
        else:
            rid = "linux-arm64" if machine == "aarch64" else "linux-x64"

        # Self-contained publish (preferred)
        self_contained = parser_dir / rid / "publish" / "AssetParser"
        if self_contained.exists():
            return str(self_contained)

        # Framework-dependent build
        framework_dependent = parser_dir / "AssetParser"
        if framework_dependent.exists():
            return str(framework_dependent)

        # Return self-contained path as default (for error messages)
        return str(self_contained)
