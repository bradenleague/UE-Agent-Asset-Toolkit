"""Unified Unreal Engine editor path detection.

Consolidates engine detection logic previously duplicated in setup.py and tools.py.
Handles both version-string and GUID EngineAssociation values, with registry
lookups on Windows.
"""

import json
import os
import platform
from pathlib import Path


def detect_engine_path(uproject_path: str | Path) -> str:
    """Detect the UE Editor path from a .uproject file.

    Reads EngineAssociation from the project file and searches platform-specific
    install locations. On Windows, also checks the registry for GUID-based
    source build associations and launcher installs.

    Args:
        uproject_path: Path to the .uproject file.

    Returns:
        Path to UnrealEditor-Cmd (or equivalent), or "" if not found.
    """
    uproject_path = Path(uproject_path)

    try:
        with open(uproject_path, "r") as f:
            proj = json.load(f)
    except Exception:
        return ""

    engine_assoc = proj.get("EngineAssociation", "")
    if not engine_assoc:
        return ""

    system = platform.system()

    # GUID associations (source builds registered with the launcher)
    is_guid = _looks_like_guid(engine_assoc)

    if is_guid and system == "Windows":
        path = _check_windows_registry_guid(engine_assoc)
        if path:
            return path

    # Version-string registry lookup (e.g., "5.4", "5.5")
    if not is_guid and system == "Windows":
        path = _check_windows_registry_version(engine_assoc)
        if path:
            return path

    # Filesystem candidate search
    for candidate in _get_candidate_paths(engine_assoc, system, is_guid):
        if candidate.exists():
            return str(candidate)

    return ""


def _looks_like_guid(value: str) -> bool:
    """Check if a string looks like a GUID (e.g., {XXXXXXXX-XXXX-...})."""
    stripped = value.strip("{}")
    parts = stripped.split("-")
    if len(parts) != 5:
        return False
    try:
        int(stripped.replace("-", ""), 16)
        return True
    except ValueError:
        return False


def _get_candidate_paths(engine_assoc: str, system: str, is_guid: bool) -> list[Path]:
    """Build a list of candidate editor paths for the given platform.

    For GUID associations we can't construct a version-based path, so we skip
    the standard locations (registry is the primary lookup for GUIDs).
    """
    if is_guid:
        # GUIDs are resolved via registry on Windows; no standard filesystem
        # paths to check. Return empty — caller already tried registry.
        return []

    if system == "Windows":
        return [
            # Epic Games Launcher installs
            Path(rf"C:\Program Files\Epic Games\UE_{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"),
            # Additional drive letters
            Path(rf"D:\Program Files\Epic Games\UE_{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"),
            Path(rf"E:\Program Files\Epic Games\UE_{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"),
            # Source builds
            Path(rf"D:\UnrealDev\UE_{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"),
            Path(rf"D:\UnrealDev\{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"),
            Path(rf"C:\UnrealEngine\UE_{engine_assoc}\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"),
        ]

    if system == "Darwin":
        home = Path.home()
        return [
            # Epic Games Launcher installs
            Path(f"/Users/Shared/Epic Games/UE_{engine_assoc}/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor"),
            Path(f"/Users/Shared/Epic Games/UE_{engine_assoc}/Engine/Binaries/Mac/UnrealEditor-Cmd"),
            # Source builds
            home / f"UnrealEngine/UE_{engine_assoc}/Engine/Binaries/Mac/UnrealEditor-Cmd",
            home / f"dev/UnrealEngine/UE_{engine_assoc}/Engine/Binaries/Mac/UnrealEditor-Cmd",
        ]

    # Linux
    home = Path.home()
    return [
        home / f"UnrealEngine/UE_{engine_assoc}/Engine/Binaries/Linux/UnrealEditor-Cmd",
        home / f"dev/UnrealEngine/UE_{engine_assoc}/Engine/Binaries/Linux/UnrealEditor-Cmd",
        Path(f"/opt/unreal-engine/UE_{engine_assoc}/Engine/Binaries/Linux/UnrealEditor-Cmd"),
        Path(f"/opt/UnrealEngine/UE_{engine_assoc}/Engine/Binaries/Linux/UnrealEditor-Cmd"),
    ]


def _check_windows_registry_guid(guid: str) -> str | None:
    """Look up a GUID-based engine association in the Windows registry.

    Source builds register under:
        HKCU\\Software\\Epic Games\\Unreal Engine\\Builds\\{GUID} = <engine_root>
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Epic Games\Unreal Engine\Builds",
        )
        try:
            engine_root, _ = winreg.QueryValueEx(key, guid)
            editor = Path(engine_root) / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
            if editor.exists():
                return str(editor)
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass
    return None


def _check_windows_registry_version(version: str) -> str | None:
    """Look up a version-based engine install in the Windows registry.

    Launcher installs register under:
        HKLM\\SOFTWARE\\EpicGames\\Unreal Engine\\{version}\\InstalledDirectory
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\EpicGames\Unreal Engine\{version}",
        )
        try:
            install_dir, _ = winreg.QueryValueEx(key, "InstalledDirectory")
            editor = Path(install_dir) / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
            if editor.exists():
                return str(editor)
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass
    return None
