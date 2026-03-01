#!/usr/bin/env python3
"""
UE Asset Toolkit Setup Script

Cross-platform setup that:
1. Initializes git submodules
2. Builds UAssetAPI and AssetParser
3. Installs Python dependencies
4. Configures the project

Usage:
    python setup.py                              # Build only
    python setup.py <path-to-uproject>           # Build + configure project
    python setup.py <path-to-uproject> --index   # Build + configure + build index
    python setup.py --help                       # Show help
"""

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path


def run_command(
    cmd: list[str], cwd: Path = None, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    try:
        return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Command failed: {' '.join(cmd)}")
        if e.stdout:
            print(f"  stdout: {e.stdout[:500]}")
        if e.stderr:
            print(f"  stderr: {e.stderr[:500]}")
        raise


def detect_platform() -> tuple[str, str, str]:
    """Detect platform and return (platform_name, runtime_id, exe_extension)."""
    system = platform.system()
    machine = platform.machine()

    if system == "Windows":
        return "windows", "win-x64", ".exe"
    elif system == "Darwin":
        rid = "osx-arm64" if machine == "arm64" else "osx-x64"
        return "macos", rid, ""
    elif system == "Linux":
        rid = "linux-arm64" if machine == "aarch64" else "linux-x64"
        return "linux", rid, ""
    else:
        print(f"ERROR: Unsupported platform: {system}")
        sys.exit(1)


def check_prerequisites() -> tuple[str, str]:
    """Check for required tools and return (dotnet_version, python_cmd)."""
    # Check .NET SDK
    try:
        result = run_command(["dotnet", "--version"])
        dotnet_version = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "ERROR: .NET SDK not found. Install from https://dotnet.microsoft.com/download"
        )
        sys.exit(1)

    # Python is already running, so just get version
    python_version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    return dotnet_version, python_version


def detect_engine_path(uproject_path: Path) -> str:
    """Try to detect UE Editor path from project file."""
    # Import the shared implementation (works whether run from repo root or UnrealAgent/)
    eng_detect_path = Path(__file__).parent / "unreal_agent" / "engine_detect.py"
    if eng_detect_path.exists():
        import importlib.util

        spec = importlib.util.spec_from_file_location("engine_detect", eng_detect_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.detect_engine_path(uproject_path)
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="UE Asset Toolkit Setup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python setup.py
  python setup.py "C:\\Projects\\My Game\\MyGame.uproject"
  python setup.py ~/Projects/MyGame/MyGame.uproject --index

Note: Quote paths containing spaces!
""",
    )
    parser.add_argument(
        "project", nargs="*", help="Path to .uproject file (quote if contains spaces)"
    )
    parser.add_argument(
        "--index", action="store_true", help="Build semantic index after setup"
    )
    args = parser.parse_args()

    # Handle project path (may be split by shell if unquoted spaces)
    if args.project:
        # Join all positional args back together (handles unquoted paths with spaces)
        project_arg = " ".join(args.project)
        args.project = project_arg
    else:
        args.project = None

    # Validate project path if provided
    project_path = None
    if args.project:
        project_path = Path(args.project).expanduser().resolve()
        if not str(project_path).endswith(".uproject"):
            print(f"ERROR: Expected .uproject file, got: {project_path}")
            print()
            print("Hint: If your path contains spaces, wrap it in quotes:")
            print(f'  python setup.py "{args.project}"')
            sys.exit(1)
        if not project_path.exists():
            print(f"ERROR: Project file not found: {project_path}")
            sys.exit(1)

    print("=" * 50)
    print("UE Asset Toolkit Setup")
    print("=" * 50)
    print()

    # Detect platform
    plat, rid, exe_ext = detect_platform()
    print(f"Platform: {plat} ({rid})")

    # Check prerequisites
    dotnet_ver, python_ver = check_prerequisites()
    print(f".NET SDK: {dotnet_ver}")
    print(f"Python: {python_ver}")
    print()

    # Setup paths
    script_dir = Path(__file__).parent.resolve()
    uassetapi_dir = script_dir / "UAssetAPI" / "UAssetAPI"
    assetparser_dir = script_dir / "AssetParser"
    unrealagent_dir = script_dir / "unreal_agent"

    # Calculate total steps
    total_steps = 6
    if project_path:
        total_steps += 1
    if args.index:
        total_steps += 1
    step = 0

    # Step 1: Initialize submodule
    step += 1
    if not (uassetapi_dir / "UAssetAPI.csproj").exists():
        print(f"[{step}/{total_steps}] Initializing UAssetAPI submodule...")
        run_command(
            ["git", "submodule", "update", "--init", "--recursive"], cwd=script_dir
        )
    else:
        print(f"[{step}/{total_steps}] UAssetAPI submodule already initialized")
    print()

    # Step 2: Build UAssetAPI
    step += 1
    print(f"[{step}/{total_steps}] Building UAssetAPI...")
    run_command(
        ["dotnet", "build", "-c", "Release", "--verbosity", "quiet"], cwd=uassetapi_dir
    )
    print("     UAssetAPI built successfully")
    print()

    # Step 3: Build AssetParser
    step += 1
    print(f"[{step}/{total_steps}] Building AssetParser...")

    if plat == "windows":
        run_command(
            ["dotnet", "build", "-c", "Release", "--verbosity", "quiet"],
            cwd=assetparser_dir,
        )
        parser_path = (
            assetparser_dir / "bin" / "Release" / "net8.0" / f"AssetParser{exe_ext}"
        )
    else:
        # macOS/Linux: build self-contained to avoid runtime dependency
        run_command(
            [
                "dotnet",
                "publish",
                "-c",
                "Release",
                "-r",
                rid,
                "--self-contained",
                "true",
                "--verbosity",
                "quiet",
            ],
            cwd=assetparser_dir,
        )
        parser_path = (
            assetparser_dir
            / "bin"
            / "Release"
            / "net8.0"
            / rid
            / "publish"
            / f"AssetParser{exe_ext}"
        )

    print("     AssetParser built successfully")
    print()

    # Step 4: Install Python dependencies
    step += 1
    print(f"[{step}/{total_steps}] Installing Python dependencies...")
    requirements_file = unrealagent_dir / "requirements.txt"
    run_command(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(requirements_file)],
        check=False,
    )
    print("     Python dependencies installed")
    print()

    # Step 5: Create data directory
    step += 1
    print(f"[{step}/{total_steps}] Creating data directory...")
    data_dir = unrealagent_dir / "data"
    data_dir.mkdir(exist_ok=True)
    print("     Data directory ready")
    print()

    # Step 6: Save local config
    step += 1
    print(f"[{step}/{total_steps}] Saving platform configuration...")
    local_config = {
        "asset_parser_path": str(parser_path),
        "platform": plat,
        "runtime_id": rid,
    }
    config_file = unrealagent_dir / "local_config.json"
    with open(config_file, "w") as f:
        json.dump(local_config, f, indent=2)
    print("     Saved to local_config.json")
    print()

    # Step 7: Add project if specified
    project_name = None
    if project_path:
        step += 1
        print(f"[{step}/{total_steps}] Adding project...")

        project_name = project_path.stem.lower()
        engine_path = detect_engine_path(project_path)

        # Create or update config.json
        config_path = unrealagent_dir / "config.json"
        if config_path.exists():
            with open(config_path, "r") as f:
                config = json.load(f)
        else:
            config = {
                "active_project": "",
                "projects": {},
                "tools": {"timeout_seconds": 120, "default_asset_path": "/Game"},
            }

        config["projects"][project_name] = {
            "project_path": str(project_path),
            "engine_path": engine_path,
        }
        config["active_project"] = project_name

        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        print(f"     Added: {project_name}")
        print(f"     Path: {project_path}")
        print(f"     Engine: {engine_path or '(not detected)'}")
        print()

    # Step 8: Build index if requested
    if args.index:
        step += 1
        print(f"[{step}/{total_steps}] Building semantic index...")

        if not project_path:
            print("WARNING: No project specified, skipping index build")
            print("     Run: python index.py")
        else:
            print("     This may take a while for large projects...")
            result = run_command(
                [sys.executable, "-m", "unreal_agent.tools", "--index-batch", "hybrid"],
                cwd=script_dir,
                check=False,
            )
            if result.returncode != 0:
                print("WARNING: Index build had issues, you can retry with:")
                print("     python index.py")
            else:
                print("     Semantic index built")
        print()

    # Done!
    print("=" * 50)
    print("Setup complete!")
    print("=" * 50)
    print()

    if project_name:
        print(f"Project configured: {project_name}")
        print()
        print("Quick test:")
        print("  python index.py list")
        print()
        print("Run the MCP server:")
        print("  python -m unreal_agent.mcp_server")
    else:
        print("Next step - re-run setup with a project:")
        if plat == "windows":
            print("  python setup.py C:\\Path\\To\\YourProject.uproject")
        else:
            print("  python setup.py /path/to/YourProject.uproject")
    print()

    if not args.index and project_path:
        print("Build semantic index (enables natural language search):")
        print("  python index.py")
        print()


if __name__ == "__main__":
    main()
