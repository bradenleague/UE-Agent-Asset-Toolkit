"""Download pre-built AssetParser binaries from GitHub Releases.

Used as a fallback when the parser isn't found in-tree (e.g., pip install
without cloning the full repo).
"""

import platform
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

# GitHub repository for releases
_REPO = "bradenleague/UE-Agent-Asset-Toolkit"
_CACHE_DIR = Path.home() / ".cache" / "unreal-agent-toolkit"
_DOWNLOAD_TIMEOUT = 30  # seconds


def get_runtime_id() -> str:
    """Detect platform runtime identifier matching .NET RIDs."""
    system = platform.system()
    machine = platform.machine()

    if system == "Windows":
        return "win-x64"
    elif system == "Darwin":
        return "osx-arm64" if machine == "arm64" else "osx-x64"
    else:
        return "linux-arm64" if machine == "aarch64" else "linux-x64"


def download_parser(version: str = "latest") -> Path | None:
    """Download the AssetParser binary from GitHub Releases.

    Args:
        version: Release tag (e.g., "v0.1.0") or "latest".

    Returns:
        Path to the downloaded binary, or None on failure.
    """
    rid = get_runtime_id()
    cache_dir = _CACHE_DIR / f"AssetParser-{version}"

    # Check cache first
    binary_name = "AssetParser.exe" if rid.startswith("win") else "AssetParser"
    cached_binary = cache_dir / binary_name
    if cached_binary.exists():
        return cached_binary

    # Resolve release URL
    if version == "latest":
        api_url = f"https://api.github.com/repos/{_REPO}/releases/latest"
    else:
        api_url = f"https://api.github.com/repos/{_REPO}/releases/tags/{version}"

    try:
        print(f"Resolving AssetParser release ({version})...", file=sys.stderr)
        req = urllib.request.Request(api_url)
        req.add_header("Accept", "application/vnd.github+json")
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
            import json

            release = json.loads(resp.read())
    except (urllib.error.URLError, OSError) as e:
        print(
            f"ERROR: Could not fetch release info: {e}\n"
            f"Download manually from: https://github.com/{_REPO}/releases",
            file=sys.stderr,
        )
        return None

    # Find matching asset
    ext = ".zip" if rid.startswith("win") else ".tar.gz"
    asset_name = f"AssetParser-{rid}{ext}"
    download_url = None
    for asset in release.get("assets", []):
        if asset["name"] == asset_name:
            download_url = asset["browser_download_url"]
            break

    if not download_url:
        print(
            f"ERROR: No binary found for platform '{rid}' in release.\n"
            f"Available assets: {[a['name'] for a in release.get('assets', [])]}\n"
            f"Download manually from: https://github.com/{_REPO}/releases",
            file=sys.stderr,
        )
        return None

    # Download
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_path = cache_dir / asset_name

    try:
        file_size = None
        for asset in release.get("assets", []):
            if asset["name"] == asset_name:
                file_size = asset.get("size")
                break

        print(
            f"Downloading {asset_name}"
            + (f" ({file_size / 1024 / 1024:.1f} MB)" if file_size else "")
            + "...",
            file=sys.stderr,
        )

        urllib.request.urlretrieve(download_url, archive_path)
    except (urllib.error.URLError, OSError) as e:
        print(
            f"ERROR: Download failed: {e}\nDownload manually from: {download_url}",
            file=sys.stderr,
        )
        return None

    # Extract
    try:
        if ext == ".zip":
            import zipfile

            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(cache_dir)
        else:
            import tarfile

            with tarfile.open(archive_path) as tf:
                tf.extractall(cache_dir)
    except Exception as e:
        print(f"ERROR: Failed to extract archive: {e}", file=sys.stderr)
        return None
    finally:
        archive_path.unlink(missing_ok=True)

    # Make executable on Unix
    if not rid.startswith("win") and cached_binary.exists():
        cached_binary.chmod(cached_binary.stat().st_mode | stat.S_IEXEC)

    if cached_binary.exists():
        print(f"AssetParser cached at: {cached_binary}", file=sys.stderr)
        return cached_binary

    # Binary might be in a subdirectory (e.g., linux-x64/AssetParser)
    for candidate in cache_dir.rglob(binary_name):
        if candidate.is_file():
            if not rid.startswith("win"):
                candidate.chmod(candidate.stat().st_mode | stat.S_IEXEC)
            return candidate

    print(
        f"ERROR: Binary not found after extraction.\nExpected: {cached_binary}",
        file=sys.stderr,
    )
    return None
