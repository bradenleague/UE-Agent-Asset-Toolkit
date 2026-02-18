"""
Asset Indexer - Parses Unreal assets and creates doc chunks.

Handles:
- Asset discovery and parsing
- Doc chunk generation
- Incremental indexing (fingerprint-based)
- Reference extraction
- Embedding generation

Environment variables:
- UE_INDEX_BATCH_TIMEOUT: Timeout in seconds for batch operations (default: 600)
- UE_INDEX_ASSET_TIMEOUT: Timeout in seconds for single asset parsing (default: 60)
- UE_INDEX_TIMING: Set to "1" to enable detailed timing instrumentation
"""

import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Callable
import subprocess
import re
import time

from UnrealAgent.pathutil import to_game_path_sep


def get_batch_timeout() -> int:
    """Resolve batch timeout from env with a safe fallback."""
    raw = os.environ.get("UE_INDEX_BATCH_TIMEOUT", "600")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 600


def get_asset_timeout() -> int:
    """Resolve single-asset timeout from env with a safe fallback."""
    raw = os.environ.get("UE_INDEX_ASSET_TIMEOUT", "60")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 60


from .schemas import (
    DocChunk,
    AssetSummary,
    WidgetTreeDoc,
    BlueprintGraphDoc,
    MaterialParamsDoc,
    MaterialFunctionDoc,
)
from .store import KnowledgeStore

# ---------------------------------------------------------------------------
# DataAsset extractor registry
# Handlers register via @data_asset_extractor("ClassName") and are looked up
# by the profile's data_asset_extractors list at runtime.
# ---------------------------------------------------------------------------
_EXTRACTOR_REGISTRY: dict[str, str] = {}
"""Maps export class name -> method name on AssetIndexer."""


def data_asset_extractor(class_name: str):
    """Decorator that registers a DataAsset per-class extractor method."""

    def decorator(fn):
        _EXTRACTOR_REGISTRY[class_name] = fn.__name__
        return fn

    return decorator


# ---------------------------------------------------------------------------
# GameplayTag extraction helpers (module-level for testability)
# ---------------------------------------------------------------------------


def _get_tag_name(data: dict, key: str) -> str:
    """Extract a GameplayTag name from a dict property.

    Replaces the 4-line pattern repeated across extractors:
        tag_data = data.get(key)
        if isinstance(tag_data, dict):
            tag = tag_data.get("TagName", "")
        ...
    """
    tag_data = data.get(key)
    if isinstance(tag_data, dict):
        tag = tag_data.get("TagName", "")
        return "" if tag == "None" else tag
    return ""


def _extract_gameplay_tags_from_data(data: object, _depth: int = 0) -> list[str]:
    """Recursively walk parsed inspect JSON and collect all GameplayTag values.

    Recognises:
      - ``{"_type": "GameplayTag", "TagName": "..."}``
      - ``{"_type": "GameplayTagContainer", "tags": [...]}``

    Returns deduplicated, sorted list of tag strings (empty/None filtered).
    """
    if _depth > 10:
        return []

    tags: list[str] = []

    if isinstance(data, dict):
        _type = data.get("_type")
        if _type == "GameplayTag":
            tag = data.get("TagName", "")
            if tag and tag != "None":
                tags.append(tag)
        elif _type == "GameplayTagContainer":
            for t in data.get("tags", []):
                if isinstance(t, str) and t and t != "None":
                    tags.append(t)
            # Also recurse into other keys — the parser can nest
            # GameplayTagContainer inside itself (e.g., Context property)
            for k, v in data.items():
                if k not in ("_type", "tags"):
                    tags.extend(_extract_gameplay_tags_from_data(v, _depth + 1))
        else:
            for v in data.values():
                tags.extend(_extract_gameplay_tags_from_data(v, _depth + 1))
    elif isinstance(data, list):
        for item in data:
            tags.extend(_extract_gameplay_tags_from_data(item, _depth + 1))

    # Deduplicate only at top level
    if _depth == 0:
        return sorted(set(tags))
    return tags


class AssetIndexer:
    """
    Indexes Unreal assets into the knowledge store.

    Uses AssetParser CLI for parsing and creates appropriate doc chunks
    based on asset type.

    Two-tier indexing strategy:
    - Semantic: Full text + embeddings for high-value types (widgets, blueprints, materials)
    - Lightweight: Path + refs only for everything else (textures, meshes, animations, OFPA)

    Key insight: Don't skip __ExternalActors__ or __ExternalObjects__ folders.
    These OFPA files contain references to source blueprints, enabling queries like
    "where is BP_Enemy placed?" by finding OFPA instances that reference it.
    """

    # Engine-level asset types that always get full semantic indexing.
    # Profile-specific types are added in _apply_profile().
    _BASE_SEMANTIC_TYPES = {
        "WidgetBlueprint",
        "Blueprint",
        "Material",
        "MaterialInstance",
        "MaterialFunction",
        "DataTable",
        "DataAsset",
        "GameFeatureData",
        "InputAction",
        "InputMappingContext",
    }

    # Batch command mapping for semantic types (type -> parser command)
    _BATCH_COMMAND_MAP = {
        "Blueprint": "batch-blueprint",
        "WidgetBlueprint": "batch-widget",
        "Material": "batch-material",
        "MaterialInstance": "batch-material",
        "MaterialFunction": "batch-material",
        "DataTable": "batch-datatable",
    }

    # Everything NOT in SEMANTIC_TYPES gets lightweight indexing:
    # - Path + name + type + references (no embeddings)
    # - Includes: Animation, Texture, StaticMesh, SkeletalMesh, Sound, OFPA files, Unknown, etc.
    # - Enables: "where is BP_X used?", "what's in Main_Menu level?", path lookups

    # Asset types that don't benefit from reference extraction
    # These are standalone assets where refs aren't useful for search
    # They'll be stored with path/name/type only (from Phase 1), skipping batch-refs
    SKIP_REFS_TYPES = {
        "Sound",  # Audio files - standalone, often large
        "Texture",  # Images - standalone
        "StaticMesh",  # 3D models - material refs not critical
        "SkeletalMesh",  # Rigged models - material/skeleton refs not critical
        "Animation",  # All anim types (montage, sequence, skeleton, blend space)
        "PhysicsAsset",  # Physics collision data
    }

    def __init__(
        self,
        store: KnowledgeStore,
        content_path: str | Path,
        parser_path: str | Path = None,
        embed_fn: Callable[[str], list[float]] = None,
        embed_model: str = None,
        embed_version: str = "1.0",
        force: bool = False,
        plugin_paths: list[tuple[str, Path]] = None,
        profile=None,
    ):
        """
        Initialize the indexer.

        Args:
            store: Knowledge store to index into
            content_path: Path to Content folder of Unreal project
            parser_path: Path to AssetParser.exe (auto-detected if not provided)
            embed_fn: Function to generate embeddings from text
            embed_model: Name of embedding model
            embed_version: Version of embedding model
            force: If True, skip fingerprint checks and always re-index
            plugin_paths: List of (mount_point, content_path) tuples for plugins
                         e.g., [("ShooterCore", Path("Plugins/ShooterCore/Content"))]
            profile: ProjectProfile instance. If None, load_profile() is called.
        """
        self.store = store
        self.content_path = Path(content_path)
        self.embed_fn = embed_fn
        self.embed_model = embed_model
        self.embed_version = embed_version
        self.force = force

        # Plugin content paths: mount_point -> content_path
        # e.g., "ShooterCore" -> Path("D:/Project/Plugins/ShooterCore/Content")
        self.plugin_paths: dict[str, Path] = {}
        if plugin_paths:
            for mount_point, path in plugin_paths:
                self.plugin_paths[mount_point] = Path(path)

        # Auto-detect parser path
        if parser_path:
            self.parser_path = Path(parser_path)
        else:
            self.parser_path = self._detect_parser_path()

        # Apply project profile
        if profile is None:
            from UnrealAgent.project_profile import load_profile

            profile = load_profile()
        self._apply_profile(profile)

    def _apply_profile(self, profile) -> None:
        """Set all profile-derived instance attributes.

        Separated from __init__ for testability — tests can call this with
        a custom profile without going through the full constructor.
        """
        # TODO(P1): auto-detect DataAsset subclasses from class hierarchy
        self._export_class_reclassify = dict(profile.export_class_reclassify)
        self._name_prefixes = dict(profile.name_prefixes)
        self._game_feature_types = {"GameFeatureData"} | set(profile.game_feature_types)
        self._blueprint_parent_redirects = dict(profile.blueprint_parent_redirects)
        self._deep_ref_export_classes = set(profile.deep_ref_export_classes) | {
            "GameFeatureData",
            "DataRegistrySource_DataTable",
            "DataRegistry",
        }
        self._deep_ref_candidates = set(profile.deep_ref_candidates)

        # SEMANTIC_TYPES = engine base + profile additions
        self.SEMANTIC_TYPES = set(self._BASE_SEMANTIC_TYPES) | set(
            profile.semantic_types
        )

        # TODO(P1): auto-detect DataAsset subclasses from class hierarchy
        # Build data asset extractor dispatch from registry + profile whitelist
        self._data_asset_extractors: dict[str, Callable] = {}
        for class_name in profile.data_asset_extractors:
            method_name = _EXTRACTOR_REGISTRY.get(class_name)
            if method_name:
                self._data_asset_extractors[class_name] = getattr(self, method_name)

        # Write resolved parser config for the C# parser
        self._resolved_config_path = self._write_resolved_parser_config(profile)

    def _write_resolved_parser_config(self, profile) -> Optional[Path]:
        """Write merged parser type config to profiles/.resolved/<name>.json."""
        from UnrealAgent.project_profile import get_parser_type_config

        profiles_dir = Path(__file__).parent.parent / "profiles" / ".resolved"
        profiles_dir.mkdir(parents=True, exist_ok=True)

        config = get_parser_type_config(profile)
        name = profile.profile_name or "default"
        path = profiles_dir / f"{name}.json"
        try:
            with open(path, "w") as f:
                json.dump(config, f, indent=2)
            return path
        except IOError:
            return None

    def _parser_cmd(self, command: str, path_or_file: str) -> list[str]:
        """Build the AssetParser command list, including --type-config if available."""
        cmd = [str(self.parser_path), command, str(path_or_file)]
        if self._resolved_config_path and self._resolved_config_path.exists():
            cmd.extend(["--type-config", str(self._resolved_config_path)])
        return cmd

    def _detect_parser_path(self) -> Optional[Path]:
        """Detect AssetParser path across platforms."""
        import platform

        base_dir = Path(__file__).parent.parent  # UnrealAgent/
        parser_base = base_dir.parent / "AssetParser" / "bin" / "Release" / "net8.0"

        # Check local_config.json first (created by setup.sh)
        local_config_path = base_dir / "local_config.json"
        if local_config_path.exists():
            try:
                with open(local_config_path, "r") as f:
                    local_config = json.load(f)
                    if "asset_parser_path" in local_config:
                        p = Path(local_config["asset_parser_path"])
                        if p.exists():
                            return p
            except (json.JSONDecodeError, IOError):
                pass

        # Platform-specific detection
        system = platform.system()
        machine = platform.machine()

        candidates = []
        if system == "Windows":
            candidates = [
                parser_base / "AssetParser.exe",
                base_dir.parent
                / "AssetParser"
                / "bin"
                / "Debug"
                / "net8.0"
                / "AssetParser.exe",
            ]
        elif system == "Darwin":
            # macOS: check for self-contained build first
            rid = "osx-arm64" if machine == "arm64" else "osx-x64"
            candidates = [
                parser_base / rid / "publish" / "AssetParser",
                parser_base / "AssetParser",
            ]
        else:
            # Linux
            rid = "linux-arm64" if machine == "aarch64" else "linux-x64"
            candidates = [
                parser_base / rid / "publish" / "AssetParser",
                parser_base / "AssetParser",
            ]

        for p in candidates:
            if p.exists():
                return p
        return None

    def _reclassify_unknown(
        self, main_class: str, asset_name: str, path: str
    ) -> str | None:
        """Determine a better asset type for an Unknown asset.

        Uses the export's main_class and the asset name to decide whether
        the asset should be promoted to a semantic type.

        Returns the new type string, or None if no reclassification applies.
        """
        # Direct class match (e.g., GameFeatureData, LyraExperienceActionSet)
        new_type = self._export_class_reclassify.get(main_class)
        if new_type:
            return new_type

        # If main_class is a GameFeatureAction_*, infer container type from name
        if main_class.startswith("GameFeatureAction_"):
            # Check name prefixes (LAS_, EAS_ → LyraExperienceActionSet)
            for prefix, rtype in self._name_prefixes.items():
                if asset_name.startswith(prefix):
                    return rtype

            # Plugin root assets (same name as parent folder) → GameFeatureData
            parts = to_game_path_sep(path).split("/")
            if len(parts) >= 2:
                parent_folder = (
                    parts[-2] if parts[-1].endswith(".uasset") else parts[-1]
                )
                # The Content/ folder's parent is the plugin name
                for i, part in enumerate(parts):
                    if part == "Content" and i > 0:
                        plugin_name = parts[i - 1]
                        if asset_name == plugin_name:
                            return "GameFeatureData"
                        break

        return None

    def index_folder(
        self,
        folder_path: str = "/Game",
        type_filter: list[str] = None,
        recursive: bool = True,
        progress_callback: Callable[[str, int, int], None] = None,
    ) -> dict:
        """
        Index all assets in a folder.

        Args:
            folder_path: Asset path prefix (e.g., /Game/UI)
            type_filter: Only index these asset types
            recursive: Whether to index subfolders
            progress_callback: Called with (asset_path, current, total)

        Returns:
            Dict with indexing statistics
        """
        stats = {
            "total_found": 0,
            "indexed": 0,
            "unchanged": 0,
            "errors": 0,
            "by_type": {},
        }

        # Collect assets from all content roots
        assets = []
        pattern = "**/*.uasset" if recursive else "*.uasset"

        # Main content folder
        fs_path = self._game_path_to_fs(folder_path)
        if fs_path.exists():
            assets.extend(fs_path.glob(pattern))

        # Plugin content folders (if configured)
        for mount_point, plugin_content in self.plugin_paths.items():
            if plugin_content.exists():
                assets.extend(plugin_content.glob(pattern))

        assets = list(assets)
        stats["total_found"] = len(assets)

        for i, asset_file in enumerate(assets):
            try:
                # Convert back to game path
                game_path = self._fs_to_game_path(asset_file)

                if progress_callback:
                    progress_callback(game_path, i + 1, len(assets))

                # Get asset summary to determine type
                summary = self._get_asset_summary(asset_file)
                if not summary:
                    stats["errors"] += 1
                    continue

                asset_type = summary.get("asset_type", "Unknown")

                # Filter by type if requested
                if type_filter and asset_type not in type_filter:
                    continue

                # Update type stats
                stats["by_type"][asset_type] = stats["by_type"].get(asset_type, 0) + 1

                # Index the asset
                result = self._index_asset(game_path, asset_file, summary)

                if result == "indexed":
                    stats["indexed"] += 1
                elif result == "unchanged":
                    stats["unchanged"] += 1
                else:
                    stats["errors"] += 1

            except Exception:
                stats["errors"] += 1

        return stats

    def index_folder_batch(
        self,
        folder_path: str = "/Game",
        batch_size: int = 1000,
        progress_callback: Callable[[str, int, int], None] = None,
        profile: str = "hybrid",
        type_filter: list[str] = None,
        recursive: bool = True,
        max_assets: int = None,
    ) -> dict:
        """
        Index assets using batch API for massive speedup.

        Three-phase strategy:
        - Phase 1: batch-fast for ultra-fast classification (header-only, 10-100x faster)
        - Phase 2: batch-refs for lightweight types (path + references only)
        - Phase 3: batch-blueprint/widget/material/datatable for semantic types

        Args:
            folder_path: Asset path prefix (e.g., /Game/UI)
            batch_size: Assets per batch (default 1000, max 2000)
            progress_callback: Called with (status_msg, current, total)
            profile: "hybrid" (default), "lightweight-only", or "semantic-only"
            type_filter: Optional list of asset types to include (e.g., ["WidgetBlueprint", "DataTable"])
                        If provided, only assets matching these types will be indexed after classification.
            recursive: If False, scan only the exact folder (no subfolders)
            max_assets: Optional cap on number of discovered assets to process

        Returns:
            Dict with indexing statistics (includes 'timing' dict if UE_INDEX_TIMING=1)
        """
        import tempfile
        import sys

        # Initialize timing instrumentation
        enable_timing = os.environ.get("UE_INDEX_TIMING", "0") == "1"
        timing_data = {
            "enabled": enable_timing,
            "phases": {},
            "total_start": time.perf_counter(),
            "subprocess_calls": 0,
            "db_writes": 0,
        }

        def record_phase(name: str, start: float, items: int = 0):
            """Record timing for a phase."""
            if enable_timing:
                duration = time.perf_counter() - start
                if name not in timing_data["phases"]:
                    timing_data["phases"][name] = {"duration": 0, "items": 0}
                timing_data["phases"][name]["duration"] += duration
                timing_data["phases"][name]["items"] += items

        stats = {
            "total_found": 0,
            "lightweight_indexed": 0,
            "semantic_indexed": 0,
            "unchanged": 0,
            "errors": 0,
            "by_type": {},
        }

        if not self.parser_path or not self.parser_path.exists():
            return {"error": "AssetParser not found"}

        # Collect assets from all content roots with progress feedback
        assets = []
        discovery_start = time.perf_counter()

        def scan_with_progress(path: Path, label: str) -> list:
            """Scan directory with periodic progress updates."""
            found = []
            last_update = 0
            walker = path.rglob("*.uasset") if recursive else path.glob("*.uasset")
            for asset in walker:
                found.append(asset)
                # Update every 1000 files
                if len(found) - last_update >= 1000:
                    sys.stderr.write(
                        f"\r  Scanning {label}... {len(found):,} files found"
                    )
                    sys.stderr.flush()
                    last_update = len(found)
            # Clear the line and print final count
            if last_update > 0:
                sys.stderr.write("\r" + " " * 60 + "\r")
            return found

        # Main content folder
        fs_path = self._game_path_to_fs(folder_path)
        if fs_path.exists():
            print(f"Scanning {fs_path}...", file=sys.stderr)
            main_assets = scan_with_progress(fs_path, "main content")
            assets.extend(main_assets)
            print(f"Found {len(main_assets):,} assets in main content", file=sys.stderr)

        # Plugin content folders
        for mount_point, plugin_content in self.plugin_paths.items():
            if plugin_content.exists():
                print(f"Scanning {plugin_content} ({mount_point})...", file=sys.stderr)
                plugin_assets = scan_with_progress(plugin_content, mount_point)
                assets.extend(plugin_assets)
                print(
                    f"Found {len(plugin_assets):,} assets in {mount_point}",
                    file=sys.stderr,
                )

        stats["total_found"] = len(assets)
        record_phase("discovery", discovery_start, len(assets))
        print(f"Total: {len(assets):,} assets", file=sys.stderr)

        if not assets:
            return stats

        # Optional guardrail for active development runs on constrained machines.
        if max_assets is not None and max_assets > 0 and len(assets) > max_assets:
            assets = sorted(assets, key=str)[:max_assets]
            stats["total_found"] = len(assets)
            print(
                f"Limiting run to {max_assets:,} assets (--max-assets)", file=sys.stderr
            )

        # File-level change detection: skip unchanged files BEFORE parsing
        # This is the key optimization for incremental indexing
        if not self.force:
            change_detect_start = time.perf_counter()
            print("Checking for file changes...", file=sys.stderr)

            # Get current file stats
            current_stats = {}
            for asset_path in assets:
                try:
                    stat = asset_path.stat()
                    current_stats[str(asset_path)] = (stat.st_mtime, stat.st_size)
                except OSError:
                    pass  # File may have been deleted

            # Get stored file metadata from DB
            stored_meta = self.store.get_file_meta_batch(list(current_stats.keys()))

            # Find changed/new files
            changed_assets = []
            unchanged_count = 0
            for asset_path in assets:
                path_str = str(asset_path)
                if path_str in current_stats:
                    current_mtime, current_size = current_stats[path_str]
                    if path_str in stored_meta:
                        stored_mtime, stored_size = stored_meta[path_str]
                        # File is unchanged if mtime AND size match
                        if (
                            abs(current_mtime - stored_mtime) < 0.001
                            and current_size == stored_size
                        ):
                            unchanged_count += 1
                            continue
                    changed_assets.append(asset_path)

            stats["unchanged"] = unchanged_count
            record_phase("change_detection", change_detect_start, len(assets))

            if unchanged_count > 0:
                print(
                    f"Skipping {unchanged_count:,} unchanged files, processing {len(changed_assets):,} changed/new files",
                    file=sys.stderr,
                )
                assets = changed_assets

            if not assets:
                print("All files unchanged, nothing to index", file=sys.stderr)
                timing_data["total_end"] = time.perf_counter()
                timing_data["total_duration"] = (
                    timing_data["total_end"] - timing_data["total_start"]
                )
                return stats

        # Phase 1: Ultra-fast classification using batch-fast (header-only parsing)
        # This is 10-100x faster than batch-summary - only reads magic number and file size,
        # detects asset type from filename prefixes without loading UAssetAPI
        print("Phase 1: Fast-classifying assets (header-only)...", file=sys.stderr)
        phase1_start = time.perf_counter()
        asset_summaries = {}  # path -> {asset_type, name, size, ...}

        for batch_start in range(0, len(assets), batch_size):
            batch = assets[batch_start : batch_start + batch_size]

            if progress_callback:
                progress_callback(
                    f"Fast-classifying batch {batch_start // batch_size + 1}",
                    batch_start,
                    len(assets),
                )

            # Write batch to temp file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as f:
                for p in batch:
                    f.write(str(p) + "\n")
                batch_file = f.name

            try:
                # Run batch-fast (10-100x faster than batch-summary)
                timing_data["subprocess_calls"] += 1
                result = subprocess.run(
                    self._parser_cmd("batch-fast", batch_file),
                    capture_output=True,
                    text=True,
                    timeout=get_batch_timeout(),
                )

                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        if not line.strip():
                            continue
                        try:
                            summary = json.loads(line)
                            if "error" not in summary:
                                path = summary.get("path", "")
                                asset_summaries[path] = summary
                                asset_type = summary.get("asset_type", "Unknown")
                                stats["by_type"][asset_type] = (
                                    stats["by_type"].get(asset_type, 0) + 1
                                )
                        except json.JSONDecodeError:
                            stats["errors"] += 1
            except subprocess.TimeoutExpired:
                print(
                    f"\nWarning: Batch timed out, skipping {len(batch)} assets",
                    file=sys.stderr,
                )
                stats["errors"] += len(batch)
            finally:
                os.unlink(batch_file)

        record_phase("batch_fast", phase1_start, len(asset_summaries))
        print(f"Fast-classified {len(asset_summaries)} assets", file=sys.stderr)

        # Phase 1.5: Reclassify Unknown non-OFPA assets using batch-summary
        # batch-fast only reads headers so DataAsset subclasses appear as "Unknown".
        # batch-summary fully parses and returns main_class which lets us promote
        # GameFeatureData, LyraExperienceActionSet, etc. to proper semantic types.
        # Only run batch-summary on likely candidates to avoid the cost of
        # full-parsing every Unknown asset. Filter to:
        #   (a) plugin content paths (GameFeatureData/ActionSets live in plugins), or
        #   (b) assets with name patterns that indicate game feature / action set types
        plugin_content_markers = set()
        for _mount, pcontent in self.plugin_paths.items():
            plugin_content_markers.add(str(pcontent))

        def _is_reclass_candidate(path: str) -> bool:
            name = Path(path).stem
            # Known name patterns from profile (e.g., LAS_, EAS_)
            if any(name.startswith(prefix) for prefix in self._name_prefixes):
                return True
            # Plugin root assets (name matches a plugin folder)
            if any(path.startswith(marker) for marker in plugin_content_markers):
                # Only consider non-OFPA, non-deep-nested assets
                if (
                    "__ExternalActors__" not in path
                    and "__ExternalObjects__" not in path
                ):
                    return True
            # Small/medium Unknown assets are likely DataAsset subclasses
            # Most are under 15KB; PrimaryAssetLabel can be ~1MB
            size = asset_summaries.get(path, {}).get("size", 0)
            if size > 0 and size < 2_000_000:
                if (
                    "__ExternalActors__" not in path
                    and "__ExternalObjects__" not in path
                ):
                    return True
            return False

        unknown_candidates = [
            p
            for p, s in asset_summaries.items()
            if s.get("asset_type") == "Unknown" and _is_reclass_candidate(p)
        ]
        if unknown_candidates:
            reclassify_start = time.perf_counter()
            reclassified = 0
            for batch_start in range(0, len(unknown_candidates), batch_size):
                batch = unknown_candidates[batch_start : batch_start + batch_size]
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False, encoding="utf-8"
                ) as f:
                    for p in batch:
                        f.write(p + "\n")
                    batch_file = f.name
                try:
                    timing_data["subprocess_calls"] += 1
                    result = subprocess.run(
                        self._parser_cmd("batch-summary", batch_file),
                        capture_output=True,
                        text=True,
                        timeout=get_batch_timeout(),
                    )
                    if result.returncode == 0:
                        for line in result.stdout.splitlines():
                            if not line.strip():
                                continue
                            try:
                                summ = json.loads(line)
                                path = summ.get("path", "")
                                main_class = summ.get("main_class", "")
                                if path not in asset_summaries or not main_class:
                                    continue

                                new_type = self._reclassify_unknown(
                                    main_class, Path(path).stem, path
                                )
                                if new_type:
                                    old_type = asset_summaries[path].get(
                                        "asset_type", "Unknown"
                                    )
                                    asset_summaries[path]["asset_type"] = new_type
                                    asset_summaries[path]["main_class"] = main_class
                                    # Update stats
                                    prior = stats["by_type"].get(old_type, 0)
                                    if prior <= 1:
                                        stats["by_type"].pop(old_type, None)
                                    else:
                                        stats["by_type"][old_type] = prior - 1
                                    stats["by_type"][new_type] = (
                                        stats["by_type"].get(new_type, 0) + 1
                                    )
                                    reclassified += 1
                            except json.JSONDecodeError:
                                pass
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    os.unlink(batch_file)
            record_phase("reclassify", reclassify_start, len(unknown_candidates))
            if reclassified > 0:
                print(
                    f"Reclassified {reclassified} Unknown assets to semantic types",
                    file=sys.stderr,
                )

        # Keep a full copy for file_meta caching even when type filters are applied.
        # This makes repeated quick runs much faster because unchanged non-semantic
        # files can be skipped before parsing on subsequent passes.
        all_asset_summaries = dict(asset_summaries)

        # Apply type filter if specified (for --quick mode)
        if type_filter:
            type_filter_set = set(type_filter)
            filtered_summaries = {
                p: s
                for p, s in asset_summaries.items()
                if s.get("asset_type", "Unknown") in type_filter_set
            }
            print(
                f"Filtered to {len(filtered_summaries)} assets matching types: {type_filter}",
                file=sys.stderr,
            )
            asset_summaries = filtered_summaries

        # Phase 2: Lightweight assets (everything NOT semantic)
        # Split into: skip-refs (stored directly) and needs-refs (run batch-refs)
        if profile in ("hybrid", "lightweight-only"):
            # Separate assets by whether they need reference extraction
            skip_refs_assets = []
            needs_refs_paths = []

            for p, s in asset_summaries.items():
                asset_type = s.get("asset_type", "Unknown")
                if asset_type in self.SEMANTIC_TYPES:
                    continue  # Will be handled in Phase 3

                if asset_type in self.SKIP_REFS_TYPES:
                    # Store directly from Phase 1 data (no refs needed)
                    game_path = self._fs_to_game_path(Path(p))
                    skip_refs_assets.append(
                        {
                            "path": game_path,
                            "name": Path(p).stem,
                            "asset_type": asset_type,
                            "references": [],
                        }
                    )
                else:
                    # Need refs for OFPA, Unknown, and other types
                    needs_refs_paths.append(p)

            # Store skip-refs assets directly (fast - no parsing needed)
            if skip_refs_assets:
                print(
                    f"Phase 2a: Storing {len(skip_refs_assets)} assets (no refs needed)...",
                    file=sys.stderr,
                )
                phase2a_start = time.perf_counter()
                # Batch insert in chunks
                for batch_start in range(0, len(skip_refs_assets), batch_size):
                    batch = skip_refs_assets[batch_start : batch_start + batch_size]
                    written = self.store.upsert_lightweight_batch(batch)
                    timing_data["db_writes"] += written
                    stats["lightweight_indexed"] += written
                    if written < len(batch):
                        stats["errors"] += len(batch) - written
                    if progress_callback:
                        progress_callback(
                            f"Storing batch {batch_start // batch_size + 1}",
                            batch_start,
                            len(skip_refs_assets),
                        )
                record_phase("skip_refs_store", phase2a_start, len(skip_refs_assets))

            # Process assets that need reference extraction
            if needs_refs_paths:
                print(
                    f"Phase 2b: Extracting refs from {len(needs_refs_paths)} assets...",
                    file=sys.stderr,
                )
                phase2b_start = time.perf_counter()

                for batch_start in range(0, len(needs_refs_paths), batch_size):
                    batch = needs_refs_paths[batch_start : batch_start + batch_size]

                    if progress_callback:
                        progress_callback(
                            f"Refs batch {batch_start // batch_size + 1}",
                            len(skip_refs_assets) + batch_start,
                            len(skip_refs_assets) + len(needs_refs_paths),
                        )

                    # Write batch to temp file
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".txt", delete=False, encoding="utf-8"
                    ) as f:
                        for p in batch:
                            f.write(p + "\n")
                        batch_file = f.name

                    try:
                        # Run batch-refs (longer timeout for network drives/OneDrive)
                        timing_data["subprocess_calls"] += 1
                        result = subprocess.run(
                            self._parser_cmd("batch-refs", batch_file),
                            capture_output=True,
                            text=True,
                            timeout=get_batch_timeout(),
                        )

                        if result.returncode == 0:
                            batch_assets = []
                            for line in result.stdout.splitlines():
                                if not line.strip():
                                    continue
                                try:
                                    refs_data = json.loads(line)
                                    if "error" not in refs_data:
                                        path = refs_data.get("path", "")
                                        if not path:
                                            continue
                                        summary = asset_summaries.get(path, {})
                                        summary_type = summary.get(
                                            "asset_type", "Unknown"
                                        )
                                        resolved_type = (
                                            refs_data.get("asset_type") or summary_type
                                        )

                                        # batch-refs fully parses assets; use its type when available
                                        # so Unknown assets can be upgraded into semantic indexing.
                                        if resolved_type != summary_type:
                                            if path in asset_summaries:
                                                asset_summaries[path]["asset_type"] = (
                                                    resolved_type
                                                )
                                            if path in all_asset_summaries:
                                                all_asset_summaries[path][
                                                    "asset_type"
                                                ] = resolved_type

                                            prior_count = stats["by_type"].get(
                                                summary_type, 0
                                            )
                                            if prior_count > 0:
                                                if prior_count == 1:
                                                    del stats["by_type"][summary_type]
                                                else:
                                                    stats["by_type"][summary_type] = (
                                                        prior_count - 1
                                                    )
                                            stats["by_type"][resolved_type] = (
                                                stats["by_type"].get(resolved_type, 0)
                                                + 1
                                            )

                                        game_path = self._fs_to_game_path(Path(path))
                                        refs = refs_data.get("refs") or []

                                        # Semantic types are handled in Phase 3 as full docs.
                                        # Keep refs from batch output for the later semantic pass.
                                        if resolved_type in self.SEMANTIC_TYPES:
                                            continue

                                        batch_assets.append(
                                            {
                                                "path": game_path,
                                                "name": Path(path).stem,
                                                "asset_type": resolved_type,
                                                "references": refs,
                                            }
                                        )
                                except json.JSONDecodeError:
                                    stats["errors"] += 1

                            # Batch insert into store
                            if batch_assets:
                                written = self.store.upsert_lightweight_batch(
                                    batch_assets
                                )
                                timing_data["db_writes"] += written
                                stats["lightweight_indexed"] += written
                                if written < len(batch_assets):
                                    stats["errors"] += len(batch_assets) - written
                    except subprocess.TimeoutExpired:
                        print(
                            f"\nWarning: Batch timed out, skipping {len(batch)} assets",
                            file=sys.stderr,
                        )
                        stats["errors"] += len(batch)
                    finally:
                        os.unlink(batch_file)

                record_phase("batch_refs", phase2b_start, len(needs_refs_paths))
            print(
                f"Indexed {stats['lightweight_indexed']} lightweight assets",
                file=sys.stderr,
            )

            # Phase 2c: Deep ref extraction for high-value Unknown assets
            # that got 0 refs from batch-refs (struct-embedded asset paths)
            deep_updated = self._deep_ref_extraction(
                asset_summaries,
                needs_refs_paths,
                skip_refs_assets,
                stats,
                timing_data,
                progress_callback,
            )
            if deep_updated > 0:
                stats["lightweight_indexed"] += deep_updated

        # Phase 3: Batch semantic indexing for high-value types
        # Uses batch-blueprint, batch-widget, batch-material, batch-datatable for ~100x speedup
        if profile in ("hybrid", "semantic-only"):
            # Group by type for batch processing
            type_groups = {t: [] for t in self.SEMANTIC_TYPES}

            for p, s in asset_summaries.items():
                asset_type = s.get("asset_type", "Unknown")
                if asset_type in type_groups:
                    type_groups[asset_type].append(p)

            total_semantic = sum(len(v) for v in type_groups.values())
            if total_semantic > 0:
                print(
                    f"Phase 3: Batch indexing {total_semantic} semantic assets...",
                    file=sys.stderr,
                )
                phase3_start = time.perf_counter()

                # Remove stale lightweight rows for paths being promoted to semantic docs.
                semantic_game_paths = []
                for paths in type_groups.values():
                    for p in paths:
                        semantic_game_paths.append(self._fs_to_game_path(Path(p)))
                if semantic_game_paths:
                    self.store.delete_lightweight_paths(semantic_game_paths)

                # Process each type with its batch command (types not in
                # _BATCH_COMMAND_MAP fall back to individual processing)
                batch_commands = {
                    t: self._BATCH_COMMAND_MAP.get(t) for t in self.SEMANTIC_TYPES
                }

                processed = 0
                for asset_type, paths in type_groups.items():
                    if not paths:
                        continue

                    batch_cmd = batch_commands.get(asset_type)
                    if batch_cmd:
                        # Use batch command
                        result = self._batch_semantic_index(
                            paths,
                            asset_type,
                            batch_cmd,
                            batch_size,
                            progress_callback,
                            processed,
                            total_semantic,
                            timing_data=timing_data,
                        )
                        stats["semantic_indexed"] += result["indexed"]
                        stats["errors"] += result["errors"]
                        processed += len(paths)
                    else:
                        # Fall back to individual processing for types without batch commands
                        for path in paths:
                            try:
                                fs_p = Path(path)
                                game_path = self._fs_to_game_path(fs_p)
                                summary = asset_summaries.get(path, {})
                                result = self._index_asset(game_path, fs_p, summary)
                                if result == "indexed":
                                    stats["semantic_indexed"] += 1
                                else:
                                    stats["errors"] += 1
                            except Exception:
                                stats["errors"] += 1
                            processed += 1

                record_phase("semantic_index", phase3_start, total_semantic)
                print(
                    f"Indexed {stats['semantic_indexed']} semantic assets",
                    file=sys.stderr,
                )

        # Store file metadata for incremental indexing
        # This allows future runs to skip unchanged files
        if all_asset_summaries:
            file_meta_start = time.perf_counter()
            file_meta_data = []
            for path_str, summary in all_asset_summaries.items():
                try:
                    p = Path(path_str)
                    stat = p.stat()
                    file_meta_data.append(
                        (
                            path_str,
                            stat.st_mtime,
                            stat.st_size,
                            summary.get("asset_type", "Unknown"),
                        )
                    )
                except OSError:
                    pass  # File may have been deleted

            if file_meta_data:
                self.store.upsert_file_meta_batch(file_meta_data)
                record_phase("file_meta_store", file_meta_start, len(file_meta_data))

        # Finalize timing data
        timing_data["total_end"] = time.perf_counter()
        timing_data["total_duration"] = (
            timing_data["total_end"] - timing_data["total_start"]
        )

        # Print timing report if enabled
        if enable_timing:
            print("\n" + "=" * 60, file=sys.stderr)
            print("TIMING REPORT", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print(f"Total: {timing_data['total_duration']:.1f}s", file=sys.stderr)
            print(f"Assets: {stats['total_found']:,}", file=sys.stderr)
            if timing_data["total_duration"] > 0:
                rate = stats["total_found"] / timing_data["total_duration"]
                print(f"Rate: {rate:.1f} assets/sec", file=sys.stderr)
            print("-" * 60, file=sys.stderr)
            for phase_name, phase_data in timing_data["phases"].items():
                pct = (
                    (phase_data["duration"] / timing_data["total_duration"] * 100)
                    if timing_data["total_duration"] > 0
                    else 0
                )
                rate_str = ""
                if phase_data["items"] > 0 and phase_data["duration"] > 0:
                    rate_str = (
                        f" ({phase_data['items'] / phase_data['duration']:.1f}/sec)"
                    )
                print(
                    f"  {phase_name:20s}: {phase_data['duration']:6.1f}s ({pct:5.1f}%) - {phase_data['items']:,} items{rate_str}",
                    file=sys.stderr,
                )
            print("-" * 60, file=sys.stderr)
            print(
                f"Subprocess calls: {timing_data['subprocess_calls']}", file=sys.stderr
            )
            print(f"Database writes: {timing_data['db_writes']:,}", file=sys.stderr)
            print("=" * 60, file=sys.stderr)

        # Include timing in stats if enabled
        if enable_timing:
            stats["timing"] = timing_data

        return stats

    def index_asset(self, game_path: str) -> str:
        """
        Index a single asset.

        Args:
            game_path: Game path (e.g., /Game/UI/WBP_Main)

        Returns:
            "indexed", "unchanged", or "error"
        """
        fs_path = self._game_path_to_fs(game_path)
        if not fs_path.exists():
            return "error"

        summary = self._get_asset_summary(fs_path)
        if not summary:
            return "error"

        return self._index_asset(game_path, fs_path, summary)

    def _index_asset(self, game_path: str, fs_path: Path, summary: dict) -> str:
        """Internal method to index an asset."""
        asset_type = summary.get("asset_type", "Unknown")
        # Use file name (stem) as asset name - main_export.name is often a component, not the asset
        asset_name = fs_path.stem

        # Create appropriate doc chunks based on type
        chunks = []

        try:
            if asset_type == "WidgetBlueprint":
                chunks.extend(
                    self._create_widget_chunks(game_path, fs_path, asset_name)
                )
            elif asset_type == "Blueprint":
                chunks.extend(
                    self._create_blueprint_chunks(game_path, fs_path, asset_name)
                )
            elif asset_type in ("Material", "MaterialInstance"):
                chunks.extend(
                    self._create_material_chunks(
                        game_path, fs_path, asset_name, asset_type
                    )
                )
            elif asset_type == "MaterialFunction":
                chunks.extend(
                    self._create_materialfunction_chunks(game_path, fs_path, asset_name)
                )
            elif asset_type == "DataTable":
                chunks.extend(
                    self._create_datatable_chunks(game_path, fs_path, asset_name)
                )
            elif asset_type in self._game_feature_types:
                chunks.extend(
                    self._create_game_feature_chunks(
                        game_path, fs_path, asset_name, asset_type
                    )
                )
            elif asset_type == "InputAction":
                chunks.extend(
                    self._create_input_action_chunks(game_path, fs_path, asset_name)
                )
            elif asset_type == "InputMappingContext":
                chunks.extend(
                    self._create_input_mapping_context_chunks(
                        game_path, fs_path, asset_name
                    )
                )
            elif asset_type == "DataAsset":
                chunks.extend(
                    self._create_data_asset_chunks(game_path, fs_path, asset_name)
                )
            else:
                # Generic asset summary
                chunks.append(
                    self._create_generic_chunk(
                        game_path, fs_path, asset_name, asset_type, summary
                    )
                )

        except Exception:
            # Fall back to generic chunk on error
            chunks.append(
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, asset_type, summary
                )
            )

        # Store chunks
        any_changed = False
        for chunk in chunks:
            # Generate embedding if function provided
            embedding = None
            if self.embed_fn:
                try:
                    embedding = self.embed_fn(chunk.text)
                    chunk.embed_model = self.embed_model
                    chunk.embed_version = self.embed_version
                except Exception:
                    pass

            changed = self.store.upsert_doc(chunk, embedding, force=self.force)
            if changed:
                any_changed = True

        return "indexed" if any_changed else "unchanged"

    def backfill_embeddings(
        self,
        batch_size: int = 100,
        progress_callback: Callable[[str, int, int], None] = None,
    ) -> dict:
        """Generate embeddings for docs that don't have them yet.

        Requires ``self.embed_fn`` to be set. Queries docs without an entry in
        docs_embeddings, computes embeddings, and batch-inserts them.

        Returns:
            Dict with 'total', 'embedded', 'errors' counts.
        """
        if not self.embed_fn:
            return {
                "total": 0,
                "embedded": 0,
                "errors": 0,
                "error": "No embed_fn configured",
            }

        rows = self.store.get_docs_without_embeddings(min_text_len=20)
        total = len(rows)
        embedded = 0
        errors = 0

        for i in range(0, total, batch_size):
            batch = rows[i : i + batch_size]
            items: list[tuple[str, list[float]]] = []
            for doc_id, text in batch:
                try:
                    emb = self.embed_fn(text)
                    items.append((doc_id, emb))
                except Exception:
                    errors += 1

            if items:
                self.store.upsert_embeddings_batch(
                    items,
                    model=self.embed_model,
                    version=self.embed_version,
                )
                embedded += len(items)

            if progress_callback:
                progress_callback(
                    "Backfilling embeddings", min(i + len(batch), total), total
                )

        return {"total": total, "embedded": embedded, "errors": errors}

    def _create_widget_chunks(
        self, game_path: str, fs_path: Path, asset_name: str
    ) -> list[DocChunk]:
        """Create chunks for a WidgetBlueprint."""
        chunks = []

        # Get widget tree
        widget_xml = self._run_parser("widgets", fs_path)
        if not widget_xml:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "WidgetBlueprint", {}
                )
            ]

        # Parse XML
        try:
            root = ET.fromstring(widget_xml)
        except ET.ParseError:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "WidgetBlueprint", {}
                )
            ]

        # Extract widget info
        summary = root.find("summary")
        widget_count = int(summary.get("widget-count", 0)) if summary is not None else 0

        hierarchy = root.find("hierarchy")
        widget_names = []
        hierarchy_text = ""

        if hierarchy is not None:
            # Collect widget names and build hierarchy text
            for widget in hierarchy.iter("widget"):
                name = widget.get("name", "")
                if name:
                    widget_names.append(name)

            # Build simple hierarchy representation
            root_widget = hierarchy.find("widget")
            if root_widget is not None:
                hierarchy_text = self._widget_to_text(root_widget)

        # Get references
        refs = self._get_asset_references(fs_path)

        # Create asset summary chunk
        chunks.append(
            AssetSummary(
                path=game_path,
                name=asset_name,
                asset_type="WidgetBlueprint",
                widget_count=widget_count,
                references_out=refs,
            )
        )

        # Create widget tree chunk
        if widget_names:
            chunks.append(
                WidgetTreeDoc(
                    path=game_path,
                    name=asset_name,
                    root_widget=widget_names[0] if widget_names else "Unknown",
                    widget_names=widget_names,
                    widget_hierarchy=hierarchy_text[:500],  # Limit length
                    references_out=refs,
                )
            )

        return chunks

    def _create_blueprint_chunks(
        self, game_path: str, fs_path: Path, asset_name: str
    ) -> list[DocChunk]:
        """Create chunks for a Blueprint."""
        chunks = []

        # Get blueprint data
        bp_xml = self._run_parser("blueprint", fs_path)
        if not bp_xml:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "Blueprint", {}
                )
            ]

        # Parse XML
        try:
            root = ET.fromstring(bp_xml)
        except ET.ParseError:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "Blueprint", {}
                )
            ]

        # Extract info
        parent = root.findtext("parent", "")

        # Blueprint parent redirects: delegate to game-feature handler for CDO extraction
        redirect_type = self._blueprint_parent_redirects.get(parent)
        if redirect_type:
            return self._create_game_feature_chunks(
                game_path, fs_path, asset_name, redirect_type
            )

        events = [e.text for e in root.findall(".//event") if e.text]
        functions = []
        components = [c.text for c in root.findall(".//component") if c.text]
        variables = [v.text for v in root.findall(".//variable") if v.text]
        interfaces = [i.text for i in root.findall(".//interface") if i.text]

        # Extract function details with better naming
        for func_elem in root.findall(".//function"):
            func_name = func_elem.get("name") or func_elem.text
            if func_name:
                # Clean up function name (remove K2Node_ prefix, etc.)
                clean_name = func_name.replace("K2Node_", "").replace(
                    "ExecuteUbergraph_", ""
                )
                if clean_name and clean_name not in functions:
                    functions.append(clean_name)

        # Get references
        refs = self._get_asset_references(fs_path)

        # Create asset summary with full details for better search
        chunks.append(
            AssetSummary(
                path=game_path,
                name=asset_name,
                asset_type="Blueprint",
                parent_class=parent,
                events=events,
                functions=functions,
                components=components,
                variables=variables,
                interfaces=interfaces,
                function_count=len(functions),
                references_out=refs,
            )
        )

        # Create chunks for each function
        for func_elem in root.findall(".//function"):
            func_name = func_elem.get("name") or func_elem.text
            if not func_name:
                continue

            flags = (
                func_elem.get("flags", "").split(",") if func_elem.get("flags") else []
            )
            calls_elem = func_elem.find("calls")
            calls = (
                calls_elem.text.split(", ")
                if calls_elem is not None and calls_elem.text
                else []
            )

            chunks.append(
                BlueprintGraphDoc(
                    path=game_path,
                    asset_name=asset_name,
                    function_name=func_name,
                    flags=flags,
                    calls=calls,
                    variables=variables,
                    references_out=refs,
                )
            )

        return chunks

    def _create_material_chunks(
        self, game_path: str, fs_path: Path, asset_name: str, asset_type: str
    ) -> list[DocChunk]:
        """Create chunks for Material/MaterialInstance."""
        # Get material data
        mat_xml = self._run_parser("material", fs_path)
        if not mat_xml:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, asset_type, {}
                )
            ]

        # Parse XML
        try:
            root = ET.fromstring(mat_xml)
        except ET.ParseError:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, asset_type, {}
                )
            ]

        is_instance = root.tag == "material-instance"
        parent = root.findtext("parent", "")
        domain = root.findtext("domain", "Surface")
        blend_mode = root.findtext("blend-mode", "Opaque")
        shading_model = root.findtext("shading-model", "DefaultLit")

        # Extract parameters
        scalar_params = {}
        vector_params = {}
        texture_params = {}
        static_switches = {}

        params = root.find("parameters")
        if params is not None:
            for scalar in params.findall("scalar"):
                name = scalar.get("name", "")
                value = scalar.get("value", "0")
                if name:
                    try:
                        scalar_params[name] = float(value)
                    except ValueError:
                        scalar_params[name] = value

            for vector in params.findall("vector"):
                name = vector.get("name", "")
                rgba = vector.get("rgba", "0,0,0,1")
                if name:
                    try:
                        vector_params[name] = [float(x) for x in rgba.split(",")]
                    except ValueError:
                        vector_params[name] = rgba

            for texture in params.findall("texture"):
                name = texture.get("name", "")
                ref = texture.get("ref", "")
                if name:
                    texture_params[name] = ref

        switches = root.find("static-switches")
        if switches is not None:
            for switch in switches.findall("switch"):
                name = switch.get("name", "")
                value = switch.get("value", "false") == "true"
                if name:
                    static_switches[name] = value

        # Get references
        refs = self._get_asset_references(fs_path)

        return [
            MaterialParamsDoc(
                path=game_path,
                name=asset_name,
                is_instance=is_instance,
                parent=parent,
                domain=domain,
                blend_mode=blend_mode,
                shading_model=shading_model,
                scalar_params=scalar_params,
                vector_params=vector_params,
                texture_params=texture_params,
                static_switches=static_switches,
                references_out=refs,
            )
        ]

    def _create_materialfunction_chunks(
        self, game_path: str, fs_path: Path, asset_name: str
    ) -> list[DocChunk]:
        """Create chunks for MaterialFunction."""
        # Get material function data
        mf_xml = self._run_parser("materialfunction", fs_path)
        if not mf_xml:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "MaterialFunction", {}
                )
            ]

        # Parse XML
        try:
            root = ET.fromstring(mf_xml)
        except ET.ParseError:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "MaterialFunction", {}
                )
            ]

        # Extract inputs
        inputs = []
        inputs_elem = root.find("inputs")
        if inputs_elem is not None:
            for input_elem in inputs_elem.findall("input"):
                inputs.append(
                    {
                        "name": input_elem.get("name", ""),
                        "type": input_elem.get("type", ""),
                        "priority": int(input_elem.get("priority", "0")),
                    }
                )

        # Extract outputs
        outputs = []
        outputs_elem = root.find("outputs")
        if outputs_elem is not None:
            for output_elem in outputs_elem.findall("output"):
                outputs.append(
                    {
                        "name": output_elem.get("name", ""),
                        "priority": int(output_elem.get("priority", "0")),
                    }
                )

        # Extract parameters
        scalar_params = {}
        vector_params = {}
        static_switches = {}

        params = root.find("parameters")
        if params is not None:
            for scalar in params.findall("scalar"):
                name = scalar.get("name", "")
                value = scalar.get("default", "0")
                if name:
                    try:
                        scalar_params[name] = float(value)
                    except ValueError:
                        scalar_params[name] = value

            for vector in params.findall("vector"):
                name = vector.get("name", "")
                default = vector.get("default", "0,0,0,1")
                if name:
                    try:
                        vector_params[name] = [float(x) for x in default.split(",")]
                    except ValueError:
                        vector_params[name] = default

            for switch in params.findall("switch"):
                name = switch.get("name", "")
                value = switch.get("default", "false") == "true"
                if name:
                    static_switches[name] = value

        # Get references
        refs = self._get_asset_references(fs_path)

        return [
            MaterialFunctionDoc(
                path=game_path,
                name=asset_name,
                inputs=inputs,
                outputs=outputs,
                scalar_params=scalar_params,
                vector_params=vector_params,
                static_switches=static_switches,
                references_out=refs,
            )
        ]

    def _create_datatable_chunks(
        self, game_path: str, fs_path: Path, asset_name: str
    ) -> list[DocChunk]:
        """Create chunks for DataTable."""
        # Get datatable data
        dt_xml = self._run_parser("datatable", fs_path)
        if not dt_xml:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "DataTable", {}
                )
            ]

        # Parse XML
        try:
            root = ET.fromstring(dt_xml)
        except ET.ParseError:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "DataTable", {}
                )
            ]

        row_struct = root.findtext("row-struct", "Unknown")
        row_count = int(root.findtext("row-count", "0"))

        # Extract columns
        columns = []
        for col in root.findall(".//column"):
            col_name = col.get("name", "")
            col_type = col.get("type", "")
            if col_name:
                columns.append(f"{col_name}:{col_type}")

        # Extract sample row keys
        row_keys = []
        for row in root.findall(".//row"):
            key = row.get("key", "")
            if key:
                row_keys.append(key)

        # Build text description
        text = f"DataTable {asset_name} with struct {row_struct}. {row_count} rows. "
        if columns:
            text += f"Columns: {', '.join(columns[:10])}. "
        if row_keys:
            text += f"Sample rows: {', '.join(row_keys[:5])}."

        refs = self._get_asset_references(fs_path)

        return [
            DocChunk(
                doc_id=f"datatable:{game_path}",
                type="datatable",
                path=game_path,
                name=asset_name,
                text=text,
                metadata={
                    "row_struct": row_struct,
                    "row_count": row_count,
                    "columns": columns,
                    "sample_keys": row_keys[:10],
                },
                references_out=refs,
                asset_type="DataTable",
            )
        ]

    # Regex to extract an asset path from UE object reference strings like:
    #   "(, /ShooterCore/UI/W_Foo.W_Foo_C, )"
    #   "(/Script/Engine, GameStateBase, )"
    #   "(/Game/Input/Mappings/IMC_Default, IMC_Default, )"
    _ASSET_PATH_RE = re.compile(r"/(?:Game|[A-Z][A-Za-z0-9_]+)/[A-Za-z0-9_/]+")

    # Regex to extract a class name from UE tuple-style refs:
    #   "(/Script/Engine, GameStateBase, )" → "GameStateBase"
    _CLASS_NAME_RE = re.compile(r"\(\s*/Script/\w+\s*,\s*(\w+)\s*,")

    @staticmethod
    def _extract_path_from_ref(value: str) -> str | None:
        """Extract a clean /Game/ or /PluginMount/ path from a UE object ref string."""
        if not value or not isinstance(value, str):
            return None
        m = AssetIndexer._ASSET_PATH_RE.search(value)
        if m:
            return m.group(0)
        return None

    @staticmethod
    def _extract_class_name(value: str) -> str:
        """Extract a display-friendly name from a UE object reference.

        For asset paths returns the last component.
        For /Script/ tuple refs like ``(/Script/Engine, GameStateBase, )``
        returns the class name (``GameStateBase``).
        """
        if not value or not isinstance(value, str):
            return ""
        # First try to get a nice class name from tuple format
        m = AssetIndexer._CLASS_NAME_RE.search(value)
        if m:
            return m.group(1)
        # Fall back to last path component
        path = AssetIndexer._extract_path_from_ref(value)
        if path:
            return path.split("/")[-1]
        return ""

    def _create_game_feature_chunks(
        self,
        game_path: str,
        fs_path: Path,
        asset_name: str,
        asset_type: str,
    ) -> list[DocChunk]:
        """Create chunks for GameFeatureData / LyraExperienceActionSet.

        Uses the ``inspect`` command to extract structured action data including
        widget registrations, component additions, and input mappings. Produces
        typed references (registers_widget, adds_component, maps_input, etc.)
        for richer graph edges.
        """
        output = self._run_parser("inspect", fs_path)
        if not output:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, asset_type, {}
                )
            ]

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, asset_type, {}
                )
            ]

        exports = data.get("exports", [])

        # Collect structured action information
        actions_info: list[dict] = []
        all_refs: list[str] = []
        typed_refs: dict[str, str] = {}
        gameplay_tags: list[str] = []
        features_to_enable: list[str] = []

        for export in exports:
            cls = export.get("class", "")
            props = export.get("properties", [])

            if cls == "GameFeatureAction_AddWidgets":
                action = {"type": "AddWidgets", "layout": None, "widgets": []}
                for prop in props:
                    if prop.get("name") == "Layout":
                        for entry in prop.get("value", []):
                            if not isinstance(entry, dict):
                                continue
                            layout_ref = self._extract_path_from_ref(
                                entry.get("LayoutClass", "")
                            )
                            tag = _get_tag_name(entry, "LayerID")
                            if layout_ref:
                                action["layout"] = {
                                    "path": layout_ref,
                                    "tag": tag,
                                }
                                all_refs.append(layout_ref)
                                typed_refs[layout_ref] = "uses_layout"
                            if tag:
                                gameplay_tags.append(tag)

                    elif prop.get("name") == "Widgets":
                        for entry in prop.get("value", []):
                            if not isinstance(entry, dict):
                                continue
                            widget_ref = self._extract_path_from_ref(
                                entry.get("WidgetClass", "")
                            )
                            slot_tag = _get_tag_name(entry, "SlotID")
                            if widget_ref:
                                widget_name = widget_ref.split("/")[-1]
                                action["widgets"].append(
                                    {
                                        "path": widget_ref,
                                        "name": widget_name,
                                        "slot": slot_tag,
                                    }
                                )
                                all_refs.append(widget_ref)
                                typed_refs[widget_ref] = "registers_widget"
                            if slot_tag:
                                gameplay_tags.append(slot_tag)

                actions_info.append(action)

            elif cls == "GameFeatureAction_AddComponents":
                action = {"type": "AddComponents", "components": []}
                for prop in props:
                    if prop.get("name") == "ComponentList":
                        for entry in prop.get("value", []):
                            if not isinstance(entry, dict):
                                continue
                            actor_raw = entry.get("ActorClass", "")
                            comp_raw = entry.get("ComponentClass", "")
                            actor_ref = self._extract_path_from_ref(actor_raw)
                            comp_ref = self._extract_path_from_ref(comp_raw)
                            actor_name = self._extract_class_name(actor_raw)
                            comp_name = self._extract_class_name(comp_raw)
                            component_entry = {
                                "actor": actor_name,
                                "component": comp_name,
                                "client": entry.get("bClientComponent", True),
                                "server": entry.get("bServerComponent", True),
                            }
                            action["components"].append(component_entry)
                            if comp_ref:
                                all_refs.append(comp_ref)
                                typed_refs[comp_ref] = "adds_component"
                            if actor_ref:
                                all_refs.append(actor_ref)
                                typed_refs[actor_ref] = "targets_actor"
                actions_info.append(action)

            elif cls == "GameFeatureAction_AddInputContextMapping":
                action = {"type": "AddInputContextMapping", "mappings": []}
                for prop in props:
                    if prop.get("name") == "InputMappings":
                        for entry in prop.get("value", []):
                            if not isinstance(entry, dict):
                                continue
                            imc_ref = self._extract_path_from_ref(
                                entry.get("InputMapping", "")
                            )
                            priority = entry.get("Priority", 0)
                            if imc_ref:
                                imc_name = imc_ref.split("/")[-1]
                                action["mappings"].append(
                                    {
                                        "path": imc_ref,
                                        "name": imc_name,
                                        "priority": priority,
                                    }
                                )
                                all_refs.append(imc_ref)
                                typed_refs[imc_ref] = "maps_input"
                actions_info.append(action)

            elif cls == "GameFeatureAction_AddInputBinding":
                action = {"type": "AddInputBinding", "configs": []}
                for prop in props:
                    if prop.get("name") == "InputConfigs":
                        for entry in prop.get("value", []):
                            if not isinstance(entry, dict):
                                continue
                            config_ref = self._extract_path_from_ref(
                                entry.get("InputConfig", "")
                            )
                            if config_ref:
                                action["configs"].append(config_ref.split("/")[-1])
                                all_refs.append(config_ref)
                                typed_refs[config_ref] = "maps_input"
                actions_info.append(action)

            elif cls == "GameFeatureAction_DataRegistry":
                action = {"type": "DataRegistry", "registries": []}
                for prop in props:
                    if prop.get("name") == "RegistriesToAdd":
                        for entry in prop.get("value", []):
                            reg_ref = self._extract_path_from_ref(str(entry))
                            if reg_ref:
                                action["registries"].append(reg_ref.split("/")[-1])
                                all_refs.append(reg_ref)
                                typed_refs[reg_ref] = "uses_asset"
                actions_info.append(action)

            elif cls == "GameFeatureAction_AddGameplayCuePath":
                action = {"type": "AddGameplayCuePath", "paths": []}
                for prop in props:
                    if prop.get("name") == "DirectoryPathsToAdd":
                        for entry in prop.get("value", []):
                            if isinstance(entry, dict):
                                action["paths"].append(entry.get("Path", ""))
                actions_info.append(action)

            elif cls in self._game_feature_types:
                # Container export — extract GameFeaturesToEnable
                for prop in props:
                    if prop.get("name") == "GameFeaturesToEnable":
                        for val in prop.get("value", []):
                            if isinstance(val, str):
                                features_to_enable.append(val)

            elif (
                export.get("name", "").startswith("Default__")
                and export.get("type") == "NormalExport"
            ):
                # CDO export — extract ActionSets, DefaultPawnData
                for prop in props:
                    pname = prop.get("name", "")
                    pval = prop.get("value", "")
                    if pname == "ActionSets" and isinstance(pval, list):
                        for item in pval:
                            ref = self._extract_path_from_ref(str(item))
                            if ref:
                                all_refs.append(ref)
                                typed_refs[ref] = "includes_action_set"
                    elif pname == "DefaultPawnData":
                        ref = self._extract_path_from_ref(str(pval))
                        if ref:
                            all_refs.append(ref)
                            typed_refs[ref] = "uses_pawn_data"
                    elif pname == "GameFeaturesToEnable":
                        if isinstance(pval, list):
                            for val in pval:
                                if (
                                    isinstance(val, str)
                                    and val not in features_to_enable
                                ):
                                    features_to_enable.append(val)

        # Also get standard import-table refs for anything we missed
        standard_refs = self._get_asset_references(fs_path)
        for ref in standard_refs:
            if ref not in typed_refs and ref not in all_refs:
                all_refs.append(ref)

        # Build text summary
        text_parts = [f"{asset_name} is a {asset_type}"]
        if features_to_enable:
            text_parts.append(f"Enables features: {', '.join(features_to_enable)}")

        for action in actions_info:
            atype = action["type"]
            if atype == "AddWidgets":
                layout = action.get("layout")
                if layout:
                    text_parts.append(
                        f"Layout: {layout['path'].split('/')[-1]}"
                        + (f" (tag {layout['tag']})" if layout.get("tag") else "")
                    )
                widgets = action.get("widgets", [])
                if widgets:
                    widget_descs = [
                        f"{w['name']}→{w['slot']}" if w.get("slot") else w["name"]
                        for w in widgets
                    ]
                    text_parts.append(f"Widgets: {', '.join(widget_descs)}")
            elif atype == "AddComponents":
                comps = action.get("components", [])
                if comps:
                    comp_descs = [f"{c['component']}→{c['actor']}" for c in comps]
                    text_parts.append(f"Components: {', '.join(comp_descs)}")
            elif atype == "AddInputContextMapping":
                mappings = action.get("mappings", [])
                if mappings:
                    text_parts.append(
                        f"Input: {', '.join(m['name'] for m in mappings)}"
                    )
            elif atype == "AddInputBinding":
                configs = action.get("configs", [])
                if configs:
                    text_parts.append(f"Input bindings: {', '.join(configs)}")
            elif atype == "DataRegistry":
                regs = action.get("registries", [])
                if regs:
                    text_parts.append(f"Registries: {', '.join(regs)}")

        # Add CDO-level refs to text
        action_set_refs = [
            r for r, t in typed_refs.items() if t == "includes_action_set"
        ]
        pawn_data_refs = [r for r, t in typed_refs.items() if t == "uses_pawn_data"]
        if action_set_refs:
            names = [r.split("/")[-1] for r in action_set_refs]
            text_parts.append(f"ActionSets: {', '.join(names)}")
        if pawn_data_refs:
            names = [r.split("/")[-1] for r in pawn_data_refs]
            text_parts.append(f"DefaultPawnData: {', '.join(names)}")

        # Additive generic tag walk over all exports
        walker_tags = _extract_gameplay_tags_from_data(exports)
        gameplay_tags = sorted(set(gameplay_tags) | set(walker_tags))

        if gameplay_tags:
            text_parts.append(f"Tags: {', '.join(gameplay_tags)}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "actions": actions_info,
            "gameplay_tags": gameplay_tags,
            "features_to_enable": features_to_enable,
            "action_sets": action_set_refs,
            "pawn_data": pawn_data_refs,
        }

        chunk = DocChunk(
            doc_id=f"asset:{game_path}",
            type="asset_summary",
            path=game_path,
            name=asset_name,
            text=text,
            metadata=metadata,
            references_out=all_refs,
            typed_references_out=typed_refs,
            module=game_path.split("/")[1] if "/" in game_path else "Unknown",
            asset_type=asset_type,
        )

        return [chunk]

    def _create_input_action_chunks(
        self,
        game_path: str,
        fs_path: Path,
        asset_name: str,
    ) -> list[DocChunk]:
        """Create chunks for InputAction assets.

        Uses ``inspect`` for trigger types and PlayerMappableKeySettings
        display name. Falls back to references for class-refs.
        """
        display_name = ""
        display_category = ""
        triggers: list[str] = []
        modifiers: list[str] = []

        # Try inspect first for structured property data
        output = self._run_parser("inspect", fs_path)
        if output:
            try:
                data = json.loads(output)
                for export in data.get("exports", []):
                    cls = export.get("class", "")
                    props = export.get("properties", [])

                    if cls == "PlayerMappableKeySettings":
                        for prop in props:
                            if prop.get("name") == "Name":
                                display_name = str(prop.get("value", ""))
                            elif prop.get("name") in ("DisplayName", "DisplayCategory"):
                                val = prop.get("value", "")
                                # Skip FText localization hashes (hex strings)
                                if (
                                    isinstance(val, str)
                                    and val
                                    and not re.fullmatch(r"[0-9A-Fa-f]+", val)
                                ):
                                    display_category = val

                    elif cls.startswith("InputTrigger"):
                        trigger_type = cls.replace("InputTrigger", "")
                        if trigger_type and trigger_type not in triggers:
                            triggers.append(trigger_type)

                    elif cls.startswith("InputModifier"):
                        mod_type = cls.replace("InputModifier", "")
                        if mod_type and mod_type not in modifiers:
                            modifiers.append(mod_type)
            except json.JSONDecodeError:
                pass

        # Get references for script-refs and class-refs
        refs = self._get_asset_references(fs_path)

        # Extract trigger/modifier info from class-refs if inspect missed them
        for ref in refs:
            if ref.startswith("/Script/InputTrigger"):
                t = ref.split("/")[-1].replace("InputTrigger", "")
                if t and t not in triggers:
                    triggers.append(t)
            elif ref.startswith("/Script/InputModifier"):
                m = ref.split("/")[-1].replace("InputModifier", "")
                if m and m not in modifiers:
                    modifiers.append(m)

        # Build text summary
        text_parts = [f"{asset_name} is an InputAction"]
        if display_name:
            text_parts.append(f"Display name: {display_name}")
        if display_category:
            text_parts.append(f"Category: {display_category}")
        if triggers:
            text_parts.append(f"Triggers: {', '.join(triggers)}")
        if modifiers:
            text_parts.append(f"Modifiers: {', '.join(modifiers)}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "display_name": display_name,
            "display_category": display_category,
            "triggers": triggers,
            "modifiers": modifiers,
        }

        chunk = DocChunk(
            doc_id=f"asset:{game_path}",
            type="asset_summary",
            path=game_path,
            name=asset_name,
            text=text,
            metadata=metadata,
            references_out=refs,
            module=game_path.split("/")[1] if "/" in game_path else "Unknown",
            asset_type="InputAction",
        )

        return [chunk]

    def _create_input_mapping_context_chunks(
        self,
        game_path: str,
        fs_path: Path,
        asset_name: str,
    ) -> list[DocChunk]:
        """Create chunks for InputMappingContext assets.

        IMC assets are always RawExport (no inspect properties available).
        Uses the ``references`` command to extract:
        - asset-refs: InputActions that the IMC maps (creates maps_input edges)
        - class-refs: Trigger/modifier classes used in the mapping
        """
        result = self._run_parser("references", fs_path)
        if not result:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "InputMappingContext", {}
                )
            ]

        all_refs: list[str] = []
        typed_refs: dict[str, str] = {}
        mapped_actions: list[str] = []
        trigger_classes: list[str] = []
        modifier_classes: list[str] = []

        try:
            root = ET.fromstring(result)

            # Asset refs → InputActions this IMC maps
            for ref in root.findall(".//asset-refs/ref"):
                if ref.text:
                    all_refs.append(ref.text)
                    action_name = ref.text.split("/")[-1]
                    # IA_ prefix indicates an InputAction
                    if action_name.startswith("IA_"):
                        typed_refs[ref.text] = "maps_input"
                        mapped_actions.append(action_name)
                    # Other asset refs (e.g., settings assets)

            # Class refs → triggers and modifiers used
            for ref in root.findall(".//class-refs/ref"):
                if ref.text:
                    all_refs.append(f"/Script/{ref.text}")
                    if ref.text.startswith("InputTrigger"):
                        trigger_classes.append(ref.text.replace("InputTrigger", ""))
                    elif ref.text.startswith("InputModifier") or "Modifier" in ref.text:
                        modifier_classes.append(ref.text)

            # Script refs
            for ref in root.findall(".//script-refs/ref"):
                if ref.text:
                    all_refs.append(ref.text)
        except ET.ParseError:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "InputMappingContext", {}
                )
            ]

        # Build text summary
        text_parts = [f"{asset_name} is an InputMappingContext"]
        if mapped_actions:
            text_parts.append(
                f"Maps {len(mapped_actions)} InputActions: "
                + ", ".join(sorted(mapped_actions))
            )
        if trigger_classes:
            text_parts.append(f"Triggers: {', '.join(sorted(set(trigger_classes)))}")
        if modifier_classes:
            text_parts.append(f"Modifiers: {', '.join(sorted(set(modifier_classes)))}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "mapped_actions": sorted(mapped_actions),
            "action_count": len(mapped_actions),
            "trigger_classes": sorted(set(trigger_classes)),
            "modifier_classes": sorted(set(modifier_classes)),
        }

        chunk = DocChunk(
            doc_id=f"asset:{game_path}",
            type="asset_summary",
            path=game_path,
            name=asset_name,
            text=text,
            metadata=metadata,
            references_out=all_refs,
            typed_references_out=typed_refs,
            module=game_path.split("/")[1] if "/" in game_path else "Unknown",
            asset_type="InputMappingContext",
        )

        return [chunk]

    # ------------------------------------------------------------------ #
    #  DataAsset handler + per-class extractors                           #
    # ------------------------------------------------------------------ #

    def _create_data_asset_chunks(
        self,
        game_path: str,
        fs_path: Path,
        asset_name: str,
    ) -> list[DocChunk]:
        """Create chunks for DataAsset assets using inspect JSON.

        Dispatches to per-class extractors based on the export's class name.
        Falls back to a generic extractor for unknown classes and to
        references-only for RawExport assets.
        """
        output = self._run_parser("inspect", fs_path)
        if not output:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "DataAsset", {}
                )
            ]

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "DataAsset", {}
                )
            ]

        exports = data.get("exports", [])

        # Find the main export (first non-metadata)
        main_export = None
        for export in exports:
            if (
                export.get("type") not in ("MetaDataExport",)
                and export.get("name") != "PackageMetaData"
            ):
                main_export = export
                break

        if main_export is None:
            return [
                self._create_generic_chunk(
                    game_path, fs_path, asset_name, "DataAsset", {}
                )
            ]

        class_name = main_export.get("class", "DataAsset")

        # RawExport — no property data available, fall back to refs
        if main_export.get("type") == "RawExport":
            refs = self._get_asset_references(fs_path)
            text = (
                f"{asset_name} is a {class_name} (raw data, properties not parseable)."
            )
            chunk = DocChunk(
                doc_id=f"asset:{game_path}",
                type="asset_summary",
                path=game_path,
                name=asset_name,
                text=text,
                metadata={"class": class_name, "raw_export": True},
                references_out=refs,
                module=game_path.split("/")[1] if "/" in game_path else "Unknown",
                asset_type="DataAsset",
            )
            return [chunk]

        props = main_export.get("properties", [])

        # Collect all refs from property values by walking recursively
        all_refs: list[str] = []
        self._collect_refs_from_value(props, all_refs)

        # Dispatch to per-class extractor (profile-driven registry)
        extractor = self._data_asset_extractors.get(
            class_name, self._extract_default_data_asset
        )
        text_parts, metadata, typed_refs = extractor(asset_name, class_name, props)

        # Centralized GameplayTag collection: walk all properties
        walker_tags = _extract_gameplay_tags_from_data(props)
        existing_tags = metadata.get("gameplay_tags", [])
        merged_tags = sorted(set(existing_tags) | set(walker_tags))
        if merged_tags:
            metadata["gameplay_tags"] = merged_tags
            # Append to text for FTS indexing if not already present
            if not any("Tags:" in p for p in text_parts):
                text_parts.append(f"Tags: {', '.join(merged_tags)}")

        text = ". ".join(text_parts) + "."
        metadata["class"] = class_name

        # Merge typed_ref paths into all_refs
        for ref_path in typed_refs:
            if ref_path not in all_refs:
                all_refs.append(ref_path)

        chunk = DocChunk(
            doc_id=f"asset:{game_path}",
            type="asset_summary",
            path=game_path,
            name=asset_name,
            text=text,
            metadata=metadata,
            references_out=all_refs,
            typed_references_out=typed_refs,
            module=game_path.split("/")[1] if "/" in game_path else "Unknown",
            asset_type="DataAsset",
        )
        return [chunk]

    def _collect_refs_from_value(self, value: object, refs: list[str]) -> None:
        """Recursively walk a parsed JSON value and extract asset paths."""
        if isinstance(value, str):
            path = self._extract_path_from_ref(value)
            if path and path not in refs:
                refs.append(path)
        elif isinstance(value, dict):
            for v in value.values():
                self._collect_refs_from_value(v, refs)
        elif isinstance(value, list):
            for item in value:
                self._collect_refs_from_value(item, refs)

    # -- Per-class extractors ------------------------------------------ #
    # Each returns (text_parts, metadata, typed_refs)

    @data_asset_extractor("LyraAbilitySet")
    def _extract_ability_set(
        self, asset_name: str, class_name: str, props: list[dict]
    ) -> tuple[list[str], dict, dict[str, str]]:
        abilities: list[dict] = []
        typed_refs: dict[str, str] = {}

        for prop in props:
            if prop.get("name") == "GrantedGameplayAbilities":
                for entry in prop.get("value", []):
                    if not isinstance(entry, dict):
                        continue
                    ability_ref = self._extract_path_from_ref(
                        str(entry.get("Ability", ""))
                    )
                    input_tag = _get_tag_name(entry, "InputTag")
                    ability_name = (
                        ability_ref.split("/")[-1]
                        if ability_ref
                        else str(entry.get("Ability", ""))
                    )
                    abilities.append(
                        {
                            "name": ability_name,
                            "path": ability_ref,
                            "input_tag": input_tag,
                        }
                    )
                    if ability_ref:
                        typed_refs[ability_ref] = "uses_asset"

        text_parts = [f"{asset_name} is a {class_name}"]
        if abilities:
            ability_descs = []
            for a in abilities:
                if a["input_tag"]:
                    ability_descs.append(f"{a['name']} ({a['input_tag']})")
                else:
                    ability_descs.append(a["name"])
            text_parts.append(f"Grants abilities: {', '.join(ability_descs)}")

        metadata = {"abilities": abilities}
        return text_parts, metadata, typed_refs

    @data_asset_extractor("LyraPawnData")
    def _extract_pawn_data(
        self, asset_name: str, class_name: str, props: list[dict]
    ) -> tuple[list[str], dict, dict[str, str]]:
        pawn_class = ""
        ability_sets: list[str] = []
        input_config = ""
        default_camera = ""
        tag_mapping = ""
        typed_refs: dict[str, str] = {}

        for prop in props:
            name = prop.get("name", "")
            val = prop.get("value", "")
            if name == "PawnClass":
                pawn_class = self._extract_path_from_ref(
                    str(val)
                ) or self._extract_class_name(str(val))
            elif name == "AbilitySets":
                if isinstance(val, list):
                    for item in val:
                        ref = self._extract_path_from_ref(str(item))
                        if ref:
                            ability_sets.append(ref)
                            typed_refs[ref] = "uses_asset"
            elif name == "InputConfig":
                input_config = self._extract_path_from_ref(str(val)) or ""
                if input_config:
                    typed_refs[input_config] = "uses_asset"
            elif name == "DefaultCameraMode":
                default_camera = self._extract_path_from_ref(
                    str(val)
                ) or self._extract_class_name(str(val))
            elif name == "TagRelationshipMapping":
                tag_mapping = self._extract_path_from_ref(str(val)) or ""
                if tag_mapping:
                    typed_refs[tag_mapping] = "uses_asset"

        text_parts = [f"{asset_name} is a {class_name}"]
        if pawn_class:
            text_parts.append(
                f"PawnClass: {pawn_class.split('/')[-1] if '/' in pawn_class else pawn_class}"
            )
        if ability_sets:
            text_parts.append(
                f"AbilitySets: {', '.join(s.split('/')[-1] for s in ability_sets)}"
            )
        if input_config:
            text_parts.append(f"InputConfig: {input_config.split('/')[-1]}")
        if default_camera:
            text_parts.append(
                f"DefaultCameraMode: {default_camera.split('/')[-1] if '/' in default_camera else default_camera}"
            )
        if tag_mapping:
            text_parts.append(f"TagRelationshipMapping: {tag_mapping.split('/')[-1]}")

        metadata = {
            "pawn_class": pawn_class,
            "ability_sets": ability_sets,
            "input_config": input_config,
            "default_camera": default_camera,
            "tag_mapping": tag_mapping,
        }
        return text_parts, metadata, typed_refs

    @data_asset_extractor("LyraInputConfig")
    def _extract_input_config(
        self, asset_name: str, class_name: str, props: list[dict]
    ) -> tuple[list[str], dict, dict[str, str]]:
        native_actions: list[dict] = []
        ability_actions: list[dict] = []
        typed_refs: dict[str, str] = {}

        for prop in props:
            name = prop.get("name", "")
            target_list = None
            if name == "NativeInputActions":
                target_list = native_actions
            elif name == "AbilityInputActions":
                target_list = ability_actions
            else:
                continue

            for entry in prop.get("value", []):
                if not isinstance(entry, dict):
                    continue
                action_ref = self._extract_path_from_ref(
                    str(entry.get("InputAction", ""))
                )
                input_tag = _get_tag_name(entry, "InputTag")
                action_name = (
                    action_ref.split("/")[-1]
                    if action_ref
                    else str(entry.get("InputAction", ""))
                )
                target_list.append(
                    {"action": action_name, "path": action_ref, "tag": input_tag}
                )
                if action_ref:
                    typed_refs[action_ref] = "uses_asset"

        text_parts = [f"{asset_name} is a {class_name}"]
        if native_actions:
            mappings = [
                f"{a['action']}->{a['tag']}" if a["tag"] else a["action"]
                for a in native_actions
            ]
            text_parts.append(f"NativeInputActions: {', '.join(mappings)}")
        if ability_actions:
            mappings = [
                f"{a['action']}->{a['tag']}" if a["tag"] else a["action"]
                for a in ability_actions
            ]
            text_parts.append(f"AbilityInputActions: {', '.join(mappings)}")

        metadata = {
            "native_actions": native_actions,
            "ability_actions": ability_actions,
        }
        return text_parts, metadata, typed_refs

    @data_asset_extractor("LyraUserFacingExperienceDefinition")
    def _extract_experience_def_playlist(
        self, asset_name: str, class_name: str, props: list[dict]
    ) -> tuple[list[str], dict, dict[str, str]]:
        map_id = ""
        experience_id = ""
        max_players = 0
        loading_widget = ""
        typed_refs: dict[str, str] = {}

        for prop in props:
            name = prop.get("name", "")
            val = prop.get("value", "")
            if name == "MapID" and isinstance(val, dict):
                map_id = val.get("PrimaryAssetName", "")
            elif name == "ExperienceID" and isinstance(val, dict):
                experience_id = val.get("PrimaryAssetName", "")
            elif name == "MaxPlayerCount":
                max_players = val if isinstance(val, int) else 0
            elif name == "LoadingScreenWidget":
                ref = self._extract_path_from_ref(str(val))
                if ref:
                    loading_widget = ref
                    typed_refs[ref] = "uses_asset"

        text_parts = [f"{asset_name} is a {class_name}"]
        if map_id:
            text_parts.append(
                f"Map: {map_id.split('/')[-1] if '/' in map_id else map_id}"
            )
        if experience_id:
            text_parts.append(f"Experience: {experience_id}")
        if max_players:
            text_parts.append(f"MaxPlayers: {max_players}")
        if loading_widget:
            text_parts.append(f"LoadingScreenWidget: {loading_widget.split('/')[-1]}")

        metadata = {
            "map_id": map_id,
            "experience_id": experience_id,
            "max_players": max_players,
            "loading_widget": loading_widget,
        }
        return text_parts, metadata, typed_refs

    @data_asset_extractor("LyraContextEffectsLibrary")
    def _extract_context_effects(
        self, asset_name: str, class_name: str, props: list[dict]
    ) -> tuple[list[str], dict, dict[str, str]]:
        # Group effects by tag -> set of contexts
        effects_by_tag: dict[str, list[str]] = {}

        for prop in props:
            if prop.get("name") != "ContextEffects":
                continue
            for entry in prop.get("value", []):
                if not isinstance(entry, dict):
                    continue
                effect_tag = _get_tag_name(entry, "EffectTag")
                # Context may be a GameplayTagContainer dict (new parser)
                # or a ToString string like "(SurfaceType.Concrete, )" (legacy)
                ctx_data = entry.get("Context")
                if isinstance(ctx_data, dict) and "tags" in ctx_data:
                    # New format: {"_type": "GameplayTagContainer", "tags": [...]}
                    context_tags = ctx_data.get("tags", [])
                    surface = context_tags[0].split(".")[-1] if context_tags else ""
                elif isinstance(ctx_data, dict):
                    context_str = ctx_data.get("Context", "")
                    surface = context_str.strip("() ")
                    if "." in surface:
                        surface = surface.split(".")[-1]
                else:
                    surface = str(ctx_data or "").strip("() ")
                    if "." in surface:
                        surface = surface.split(".")[-1]
                if effect_tag:
                    effects_by_tag.setdefault(effect_tag, [])
                    if surface and surface not in effects_by_tag[effect_tag]:
                        effects_by_tag[effect_tag].append(surface)

        text_parts = [f"{asset_name} is a {class_name}"]
        if effects_by_tag:
            effect_descs = []
            for tag, surfaces in effects_by_tag.items():
                if surfaces:
                    effect_descs.append(f"{tag} ({', '.join(surfaces)})")
                else:
                    effect_descs.append(tag)
            text_parts.append(f"ContextEffects: {'; '.join(effect_descs)}")

        metadata = {"effects_by_tag": effects_by_tag}
        typed_refs: dict[str, str] = {}
        return text_parts, metadata, typed_refs

    def _extract_default_data_asset(
        self, asset_name: str, class_name: str, props: list[dict]
    ) -> tuple[list[str], dict, dict[str, str]]:
        """Generic fallback extractor for unknown DataAsset classes."""
        prop_names = [p.get("name", "") for p in props if p.get("name")]
        all_refs: list[str] = []
        self._collect_refs_from_value(props, all_refs)

        text_parts = [f"{asset_name} is a {class_name}"]
        if prop_names:
            text_parts.append(f"Properties: {', '.join(prop_names[:15])}")
        if all_refs:
            ref_names = [r.split("/")[-1] for r in all_refs[:10]]
            text_parts.append(f"References: {', '.join(ref_names)}")

        metadata = {"properties": prop_names}
        typed_refs: dict[str, str] = {}
        return text_parts, metadata, typed_refs

    def _create_generic_chunk(
        self,
        game_path: str,
        fs_path: Path,
        asset_name: str,
        asset_type: str,
        summary: dict,
    ) -> DocChunk:
        """Create a generic asset summary chunk."""
        refs = self._get_asset_references(fs_path)

        return AssetSummary(
            path=game_path,
            name=asset_name,
            asset_type=asset_type,
            references_out=refs,
        )

    def _get_asset_summary(self, fs_path: Path) -> Optional[dict]:
        """Get asset summary using AssetParser."""
        result = self._run_parser("summary", fs_path)
        if result:
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                pass
        return None

    def _get_asset_references(self, fs_path: Path) -> list[str]:
        """Get asset references using AssetParser."""
        result = self._run_parser("references", fs_path)
        if not result:
            return []

        refs = []
        try:
            root = ET.fromstring(result)
            # Parse asset references (/Game/ paths)
            for ref in root.findall(".//asset-refs/ref"):
                if ref.text:
                    refs.append(ref.text)
            # Parse class references (C++ classes used by Blueprint)
            # These become /Script/ references for cross-referencing with C++ docs
            for ref in root.findall(".//class-refs/ref"):
                if ref.text:
                    # Class refs are just class names like "UCharacterMovementComponent"
                    # Store them as-is - they'll be resolved to C++ docs during edge creation
                    refs.append(f"/Script/{ref.text}")
            # Parse script references (already in /Script/Module format)
            for ref in root.findall(".//script-refs/ref"):
                if ref.text:
                    refs.append(ref.text)
        except ET.ParseError:
            pass

        return refs

    def _run_parser(self, command: str, fs_path: Path) -> Optional[str]:
        """Run AssetParser command."""
        if not self.parser_path or not self.parser_path.exists():
            return None

        try:
            result = subprocess.run(
                self._parser_cmd(command, str(fs_path)),
                capture_output=True,
                text=True,
                timeout=get_asset_timeout(),
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass

        return None

    def _extract_refs_from_inspect(self, fs_path: Path) -> list[str]:
        """Extract asset path references from `inspect` JSON output.

        The `inspect` command produces full property data including struct-embedded
        asset paths that the standard `references` command misses. This regex-extracts
        /Game/ and /PluginMount/ style paths from the JSON text.
        """
        output = self._run_parser("inspect", fs_path)
        if not output:
            return []

        # Regex to find asset paths in inspect JSON.
        # Matches patterns like:
        #   "/ShooterCore/UserInterface/W_ShooterHUDLayout.W_ShooterHUDLayout_C"
        #   "/Game/UI/Hud/W_Healthbar.W_Healthbar_C"
        # We strip the _C class suffix to get the clean asset path.
        path_pattern = re.compile(
            r"(/(?:Game|[A-Z][A-Za-z0-9_]+)/[A-Za-z0-9_/]+)"
            r"(?:\.[A-Za-z0-9_]+_C)?"
        )

        own_path = self._fs_to_game_path(fs_path)
        own_name = fs_path.stem

        refs = set()
        for match in path_pattern.finditer(output):
            path = match.group(1)
            # Skip /Script/ refs and very short paths
            if path.startswith("/Script/") or path.count("/") < 2:
                continue
            # Skip the asset's own path (both game path and potential fs path leaks)
            if path == own_path:
                continue
            # Filter out filesystem path fragments that leaked into JSON
            # (e.g., /Lyra/Plugins/... from the "path" field)
            if "/Plugins/" in path or "/Content/" in path or "/Source/" in path:
                continue
            refs.add(path)

        return sorted(refs)

    def _deep_ref_extraction(
        self,
        asset_summaries: dict,
        needs_refs_paths: list[str],
        batch_assets_stored: list[dict],
        stats: dict,
        timing_data: dict,
        progress_callback,
    ) -> int:
        """Phase 2c: Run inspect on zero-ref Unknown assets with high-value export classes.

        Returns number of assets updated.
        """
        import sys

        if not self.parser_path or not self.parser_path.exists():
            return 0

        # Find zero-ref non-OFPA Unknown assets that had interesting export classes
        candidates = []
        for p, s in asset_summaries.items():
            asset_type = s.get("asset_type", "Unknown")
            if asset_type not in ("Unknown", "DataAsset"):
                continue
            # Skip OFPA files
            if "__ExternalActors__" in p or "__ExternalObjects__" in p:
                continue
            # Check export classes for high-value types
            export_classes = s.get("export_classes", [])
            if any(cls in self._deep_ref_export_classes for cls in export_classes):
                candidates.append(p)
                continue
            # Also target by name pattern (profile-driven prefixes and candidates)
            name = Path(p).stem
            if name in self._deep_ref_candidates or any(
                name.startswith(prefix) for prefix in self._name_prefixes
            ):
                candidates.append(p)

        if not candidates:
            return 0

        print(
            f"Phase 2c: Deep ref extraction for {len(candidates)} high-value Unknown assets...",
            file=sys.stderr,
        )
        phase2c_start = time.perf_counter()
        updated = 0

        for i, fs_path_str in enumerate(candidates):
            fs_path = Path(fs_path_str)
            if progress_callback:
                progress_callback(
                    f"Deep inspect {i + 1}/{len(candidates)}", i, len(candidates)
                )

            timing_data["subprocess_calls"] += 1
            refs = self._extract_refs_from_inspect(fs_path)
            if not refs:
                continue

            game_path = self._fs_to_game_path(fs_path)
            summary = asset_summaries.get(fs_path_str, {})
            asset_entry = {
                "path": game_path,
                "name": fs_path.stem,
                "asset_type": summary.get("asset_type", "Unknown"),
                "references": refs,
            }

            written = self.store.upsert_lightweight_batch([asset_entry])
            if written > 0:
                timing_data["db_writes"] += written
                updated += 1

        if timing_data.get("enabled"):
            duration = time.perf_counter() - phase2c_start
            if "deep_ref_inspect" not in timing_data["phases"]:
                timing_data["phases"]["deep_ref_inspect"] = {"duration": 0, "items": 0}
            timing_data["phases"]["deep_ref_inspect"]["duration"] += duration
            timing_data["phases"]["deep_ref_inspect"]["items"] += len(candidates)

        print(
            f"Updated {updated}/{len(candidates)} assets with deep refs",
            file=sys.stderr,
        )
        return updated

    def _batch_semantic_index(
        self,
        paths: list[str],
        asset_type: str,
        batch_cmd: str,
        batch_size: int,
        progress_callback,
        progress_offset: int,
        progress_total: int,
        timing_data: dict = None,
    ) -> dict:
        """
        Batch index semantic assets using batch commands.

        Returns dict with 'indexed' and 'errors' counts.
        """
        import tempfile
        import sys

        stats = {"indexed": 0, "errors": 0}

        if not self.parser_path or not self.parser_path.exists():
            stats["errors"] = len(paths)
            return stats

        for batch_start in range(0, len(paths), batch_size):
            batch = paths[batch_start : batch_start + batch_size]

            if progress_callback:
                progress_callback(
                    f"Batch {asset_type} {batch_start // batch_size + 1}",
                    progress_offset + batch_start,
                    progress_total,
                )

            # Write batch to temp file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as f:
                for p in batch:
                    f.write(p + "\n")
                batch_file = f.name

            try:
                if timing_data:
                    timing_data["subprocess_calls"] += 1
                result = subprocess.run(
                    self._parser_cmd(batch_cmd, batch_file),
                    capture_output=True,
                    text=True,
                    timeout=get_batch_timeout(),
                )

                if result.returncode == 0:
                    # Collect all chunks for batch insert
                    all_chunks = []
                    all_embeddings = []
                    assets_processed = 0

                    for line in result.stdout.splitlines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            if "error" in data:
                                stats["errors"] += 1
                                continue

                            # Create chunks from JSON data
                            fs_path = Path(data.get("path", ""))
                            game_path = self._fs_to_game_path(fs_path)
                            asset_name = fs_path.stem
                            refs = data.get("refs") or []

                            chunks = self._create_chunks_from_json(
                                data, game_path, fs_path, asset_name, asset_type, refs
                            )

                            # Collect chunks and embeddings for batch insert
                            for chunk in chunks:
                                embedding = None
                                if self.embed_fn:
                                    try:
                                        embedding = self.embed_fn(chunk.text)
                                        chunk.embed_model = self.embed_model
                                        chunk.embed_version = self.embed_version
                                    except Exception:
                                        pass
                                all_chunks.append(chunk)
                                all_embeddings.append(embedding)

                            assets_processed += 1
                        except json.JSONDecodeError:
                            stats["errors"] += 1

                    # Batch insert all chunks at once (much faster than individual inserts)
                    if all_chunks:
                        batch_result = self.store.upsert_docs_batch(
                            all_chunks,
                            embeddings=all_embeddings if self.embed_fn else None,
                            force=self.force,
                        )
                        if batch_result.get("errors"):
                            stats["errors"] += int(batch_result.get("errors", 0))
                            err_msg = batch_result.get("last_error")
                            if err_msg:
                                print(
                                    f"\nWarning: DB batch write error for {asset_type}: {err_msg}",
                                    file=sys.stderr,
                                )
                        if timing_data:
                            timing_data["db_writes"] += batch_result.get("inserted", 0)
                        stats["indexed"] += assets_processed
                else:
                    stats["errors"] += len(batch)
            except subprocess.TimeoutExpired:
                print(f"\nWarning: Batch {batch_cmd} timed out", file=sys.stderr)
                stats["errors"] += len(batch)
            finally:
                os.unlink(batch_file)

        return stats

    def _create_chunks_from_json(
        self,
        data: dict,
        game_path: str,
        fs_path: Path,
        asset_name: str,
        asset_type: str,
        refs: list[str],
    ) -> list[DocChunk]:
        """Create doc chunks from batch JSON output."""
        chunks = []

        if asset_type == "Blueprint":
            # Blueprint parent redirects: delegate to game-feature handler for CDO extraction
            parent = data.get("parent", "")
            redirect_type = self._blueprint_parent_redirects.get(parent)
            if redirect_type:
                return self._create_game_feature_chunks(
                    game_path, fs_path, asset_name, redirect_type
                )
            chunks.extend(
                self._chunks_from_blueprint_json(data, game_path, asset_name, refs)
            )
        elif asset_type == "WidgetBlueprint":
            chunks.extend(
                self._chunks_from_widget_json(data, game_path, asset_name, refs)
            )
        elif asset_type in ("Material", "MaterialInstance", "MaterialFunction"):
            chunks.extend(
                self._chunks_from_material_json(
                    data, game_path, asset_name, asset_type, refs
                )
            )
        elif asset_type == "DataTable":
            chunks.extend(
                self._chunks_from_datatable_json(data, game_path, asset_name, refs)
            )
        else:
            # Generic fallback
            chunks.append(
                AssetSummary(
                    path=game_path,
                    name=asset_name,
                    asset_type=asset_type,
                    references_out=refs,
                )
            )

        return chunks

    def _chunks_from_blueprint_json(
        self, data: dict, game_path: str, asset_name: str, refs: list[str]
    ) -> list[DocChunk]:
        """Create chunks from batch-blueprint JSON output."""
        chunks = []

        parent = data.get("parent", "")
        events = data.get("events") or []
        components = data.get("components") or []
        variables = data.get("variables") or []
        interfaces = data.get("interfaces") or []
        functions_data = data.get("functions") or []

        # Extract function names
        functions = [
            f.get("name", "") if isinstance(f, dict) else str(f) for f in functions_data
        ]

        # Create asset summary
        chunks.append(
            AssetSummary(
                path=game_path,
                name=asset_name,
                asset_type="Blueprint",
                parent_class=parent,
                events=events,
                functions=functions,
                components=components,
                variables=variables,
                interfaces=interfaces,
                function_count=len(functions),
                references_out=refs,
            )
        )

        # Create function chunks
        for func_data in functions_data:
            if isinstance(func_data, dict):
                func_name = func_data.get("name", "")
                flags = (
                    func_data.get("flags", "").split(",")
                    if func_data.get("flags")
                    else []
                )
                calls = func_data.get("calls") or []
                control_flow = func_data.get("control_flow") or {}

                if func_name:
                    chunks.append(
                        BlueprintGraphDoc(
                            path=game_path,
                            asset_name=asset_name,
                            function_name=func_name,
                            flags=flags,
                            calls=calls,
                            variables=variables,
                            references_out=refs,
                            control_flow=control_flow,
                        )
                    )

        return chunks

    def _chunks_from_widget_json(
        self, data: dict, game_path: str, asset_name: str, refs: list[str]
    ) -> list[DocChunk]:
        """Create chunks from batch-widget JSON output."""
        chunks = []

        widget_count = data.get("widget_count", 0)
        widget_names = data.get("widget_names") or []

        # Extract blueprint metadata (new fields from enhanced batch-widget)
        parent = data.get("parent", "")
        interfaces = data.get("interfaces") or []
        events = data.get("events") or []
        variables = data.get("variables") or []
        functions_data = data.get("functions") or []
        functions = [
            f.get("name", "") if isinstance(f, dict) else str(f) for f in functions_data
        ]

        # Build hierarchy text from widgets list
        widgets = data.get("widgets") or []
        hierarchy_parts = []
        for w in widgets[:20]:  # Limit to first 20
            w_type = w.get("type", "Widget") if isinstance(w, dict) else "Widget"
            w_name = w.get("name", "") if isinstance(w, dict) else str(w)
            w_text = w.get("text") if isinstance(w, dict) else None
            if w_text:
                hierarchy_parts.append(f"{w_type}({w_name}) text='{w_text}'")
            else:
                hierarchy_parts.append(f"{w_type}({w_name})")
        hierarchy_text = "\n".join(hierarchy_parts)

        # Asset summary with blueprint metadata
        chunks.append(
            AssetSummary(
                path=game_path,
                name=asset_name,
                asset_type="WidgetBlueprint",
                parent_class=parent,
                interfaces=interfaces,
                events=events,
                functions=functions,
                variables=variables,
                widget_count=widget_count,
                references_out=refs,
            )
        )

        # Widget tree doc
        if widget_names:
            chunks.append(
                WidgetTreeDoc(
                    path=game_path,
                    name=asset_name,
                    root_widget=widget_names[0] if widget_names else "Unknown",
                    widget_names=widget_names,
                    widget_hierarchy=hierarchy_text[:500],
                    references_out=refs,
                )
            )

        return chunks

    def _chunks_from_material_json(
        self,
        data: dict,
        game_path: str,
        asset_name: str,
        asset_type: str,
        refs: list[str],
    ) -> list[DocChunk]:
        """Create chunks from batch-material JSON output."""
        is_instance = data.get("is_instance", asset_type == "MaterialInstance")
        parent = data.get("parent", "")
        domain = data.get("domain", "Surface")
        blend_mode = data.get("blend_mode", "Opaque")
        shading_model = data.get("shading_model", "DefaultLit")

        scalar_params = data.get("scalar_params", {})
        vector_params = data.get("vector_params", {})
        texture_params = data.get("texture_params", {})
        static_switches = data.get("static_switches", {})

        return [
            MaterialParamsDoc(
                path=game_path,
                name=asset_name,
                is_instance=is_instance,
                parent=parent,
                domain=domain,
                blend_mode=blend_mode,
                shading_model=shading_model,
                scalar_params=scalar_params,
                vector_params=vector_params,
                texture_params=texture_params,
                static_switches=static_switches,
                references_out=refs,
            )
        ]

    def _chunks_from_datatable_json(
        self, data: dict, game_path: str, asset_name: str, refs: list[str]
    ) -> list[DocChunk]:
        """Create chunks from batch-datatable JSON output."""
        row_struct = data.get("row_struct", "Unknown")
        row_count = data.get("row_count", 0)
        columns = data.get("columns") or []
        sample_keys = data.get("sample_keys") or []

        # Build text description
        text = f"DataTable {asset_name} with struct {row_struct}. {row_count} rows. "
        if columns:
            text += f"Columns: {', '.join(columns[:10])}. "
        if sample_keys:
            text += f"Sample rows: {', '.join(sample_keys[:5])}."

        return [
            DocChunk(
                doc_id=f"datatable:{game_path}",
                type="datatable",
                path=game_path,
                name=asset_name,
                text=text,
                metadata={
                    "row_struct": row_struct,
                    "row_count": row_count,
                    "columns": columns,
                    "sample_keys": sample_keys[:10],
                },
                references_out=refs,
                asset_type="DataTable",
            )
        ]

    def _game_path_to_fs(self, game_path: str) -> Path:
        """Convert game path to filesystem path."""
        # Check if it's a plugin path (e.g., /ShooterCore/UI/Widget)
        for mount_point, plugin_content in self.plugin_paths.items():
            prefix = f"/{mount_point}/"
            if game_path.startswith(prefix):
                path = game_path[len(prefix) :]
                fs_path = plugin_content / path
                if not fs_path.suffix and not fs_path.is_dir():
                    fs_path = fs_path.with_suffix(".uasset")
                return fs_path

        # Default: /Game/UI/Widget -> Content/UI/Widget.uasset
        path = game_path.replace("/Game/", "").replace("/Game", "")
        fs_path = self.content_path / path
        # Only add .uasset if it's not a directory and doesn't have a suffix
        if not fs_path.suffix and not fs_path.is_dir():
            fs_path = fs_path.with_suffix(".uasset")
        return fs_path

    def _fs_to_game_path(self, fs_path: Path) -> str:
        """Convert filesystem path to game path."""
        # Check if it's under a plugin content path
        for mount_point, plugin_content in self.plugin_paths.items():
            try:
                rel = fs_path.relative_to(plugin_content)
                game_path = f"/{mount_point}/" + to_game_path_sep(str(rel))
                if game_path.endswith(".uasset"):
                    game_path = game_path[:-7]
                return game_path
            except ValueError:
                continue

        # Default: Content/UI/Widget.uasset -> /Game/UI/Widget
        try:
            rel = fs_path.relative_to(self.content_path)
            game_path = "/Game/" + to_game_path_sep(str(rel))
            if game_path.endswith(".uasset"):
                game_path = game_path[:-7]
            return game_path
        except ValueError:
            return str(fs_path)

    def _widget_to_text(self, widget_elem: ET.Element, depth: int = 0) -> str:
        """Convert widget element to text representation."""
        name = widget_elem.get("name", "Unknown")
        widget_type = widget_elem.get("type", "Unknown")
        text = widget_elem.get("text", "")

        parts = [f"{'  ' * depth}{widget_type}({name})"]
        if text:
            parts[-1] += f" text='{text}'"

        for child in widget_elem.findall("widget"):
            parts.append(self._widget_to_text(child, depth + 1))

        return "\n".join(parts)


# Embedding provider helpers


def create_openai_embedder(api_key: str, model: str = "text-embedding-3-small"):
    """Create an OpenAI embedding function."""
    try:
        import openai

        client = openai.OpenAI(api_key=api_key)

        def embed(text: str) -> list[float]:
            response = client.embeddings.create(
                input=text[:8000],  # Truncate to fit token limit
                model=model,
            )
            return response.data[0].embedding

        return embed
    except ImportError:
        return None


def create_sentence_transformer_embedder(model_name: str = "all-MiniLM-L6-v2"):
    """Create a local sentence transformer embedding function."""
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_name)

        def embed(text: str) -> list[float]:
            return model.encode(text[:4000], convert_to_numpy=True).tolist()

        return embed
    except ImportError:
        return None
