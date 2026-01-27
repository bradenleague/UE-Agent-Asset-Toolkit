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
"""

import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable
import hashlib
import subprocess
import re

# Configurable timeouts (can be overridden via environment variables)
BATCH_TIMEOUT = int(os.environ.get("UE_INDEX_BATCH_TIMEOUT", "600"))  # 10 min default
ASSET_TIMEOUT = int(os.environ.get("UE_INDEX_ASSET_TIMEOUT", "60"))   # 1 min default

from .schemas import (
    DocChunk,
    AssetSummary,
    WidgetTreeDoc,
    BlueprintGraphDoc,
    MaterialParamsDoc,
    MaterialFunctionDoc,
)
from .store import KnowledgeStore


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

    # Asset types that benefit from full semantic indexing (embeddings + text chunks)
    # These produce rich searchable text (100-1000 chars)
    SEMANTIC_TYPES = {
        "WidgetBlueprint",  # 972 chars avg - widget tree hierarchy
        "Blueprint",        # 300+ chars with improvements - functions, components, variables
        "Material",         # 116 chars avg - domain, blend mode, shading model
        "MaterialInstance", # 430 chars avg - parameter values, inheritance
        "MaterialFunction", # Material graph inputs/outputs
        "DataTable",        # 414 chars avg - row/column structure
        "DataAsset",        # Custom data containers
    }

    # Everything NOT in SEMANTIC_TYPES gets lightweight indexing:
    # - Path + name + type + references (no embeddings)
    # - Includes: Animation, Texture, StaticMesh, SkeletalMesh, Sound, OFPA files, Unknown, etc.
    # - Enables: "where is BP_X used?", "what's in Main_Menu level?", path lookups

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
                base_dir.parent / "AssetParser" / "bin" / "Debug" / "net8.0" / "AssetParser.exe",
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

            except Exception as e:
                stats["errors"] += 1

        return stats

    def index_folder_batch(
        self,
        folder_path: str = "/Game",
        batch_size: int = 1000,
        progress_callback: Callable[[str, int, int], None] = None,
        profile: str = "hybrid",
    ) -> dict:
        """
        Index assets using batch API for 430x speedup.

        Two-tier strategy:
        - Lightweight: batch-summary + batch-refs for low-value types (~4 hours for 908k)
        - Semantic: Full parsing for high-value types (~24 hours for 70k)

        Args:
            folder_path: Asset path prefix (e.g., /Game/UI)
            batch_size: Assets per batch (default 1000, max 2000)
            progress_callback: Called with (status_msg, current, total)
            profile: "hybrid" (default), "lightweight-only", or "semantic-only"

        Returns:
            Dict with indexing statistics
        """
        import tempfile
        import sys

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

        # Collect assets from all content roots
        assets = []

        # Main content folder
        fs_path = self._game_path_to_fs(folder_path)
        if fs_path.exists():
            print(f"Scanning {fs_path}...", file=sys.stderr)
            main_assets = list(fs_path.rglob("*.uasset"))
            assets.extend(main_assets)
            print(f"Found {len(main_assets)} assets in main content", file=sys.stderr)

        # Plugin content folders
        for mount_point, plugin_content in self.plugin_paths.items():
            if plugin_content.exists():
                print(f"Scanning {plugin_content} ({mount_point})...", file=sys.stderr)
                plugin_assets = list(plugin_content.rglob("*.uasset"))
                assets.extend(plugin_assets)
                print(f"Found {len(plugin_assets)} assets in {mount_point}", file=sys.stderr)

        stats["total_found"] = len(assets)
        print(f"Total: {len(assets)} assets", file=sys.stderr)

        if not assets:
            return stats

        # Phase 1: Batch summary to classify all assets
        print("Phase 1: Classifying assets...", file=sys.stderr)
        asset_summaries = {}  # path -> {asset_type, name, ...}

        for batch_start in range(0, len(assets), batch_size):
            batch = assets[batch_start:batch_start + batch_size]

            if progress_callback:
                progress_callback(
                    f"Classifying batch {batch_start // batch_size + 1}",
                    batch_start,
                    len(assets)
                )

            # Write batch to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
                for p in batch:
                    f.write(str(p) + '\n')
                batch_file = f.name

            try:
                # Run batch-summary (longer timeout for network drives/OneDrive)
                result = subprocess.run(
                    [str(self.parser_path), "batch-summary", batch_file],
                    capture_output=True,
                    text=True,
                    timeout=BATCH_TIMEOUT,
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
                                stats["by_type"][asset_type] = stats["by_type"].get(asset_type, 0) + 1
                        except json.JSONDecodeError:
                            stats["errors"] += 1
            except subprocess.TimeoutExpired:
                print(f"\nWarning: Batch timed out, skipping {len(batch)} assets", file=sys.stderr)
                stats["errors"] += len(batch)
            finally:
                os.unlink(batch_file)

        print(f"Classified {len(asset_summaries)} assets", file=sys.stderr)

        # Phase 2: Batch references for lightweight assets (everything NOT semantic)
        # This includes: textures, meshes, animations, sounds, OFPA files, Unknown, etc.
        if profile in ("hybrid", "lightweight-only"):
            lightweight_paths = [
                p for p, s in asset_summaries.items()
                if s.get("asset_type", "Unknown") not in self.SEMANTIC_TYPES
            ]

            if lightweight_paths:
                print(f"Phase 2: Indexing {len(lightweight_paths)} lightweight assets...", file=sys.stderr)

                for batch_start in range(0, len(lightweight_paths), batch_size):
                    batch = lightweight_paths[batch_start:batch_start + batch_size]

                    if progress_callback:
                        progress_callback(
                            f"Lightweight batch {batch_start // batch_size + 1}",
                            batch_start,
                            len(lightweight_paths)
                        )

                    # Write batch to temp file
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
                        for p in batch:
                            f.write(p + '\n')
                        batch_file = f.name

                    try:
                        # Run batch-refs (longer timeout for network drives/OneDrive)
                        result = subprocess.run(
                            [str(self.parser_path), "batch-refs", batch_file],
                            capture_output=True,
                            text=True,
                            timeout=BATCH_TIMEOUT,
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
                                        summary = asset_summaries.get(path, {})
                                        game_path = self._fs_to_game_path(Path(path))

                                        batch_assets.append({
                                            "path": game_path,
                                            "name": Path(path).stem,
                                            "asset_type": summary.get("asset_type", "Unknown"),
                                            "references": refs_data.get("refs", []),
                                        })
                                except json.JSONDecodeError:
                                    stats["errors"] += 1

                            # Batch insert into store
                            if batch_assets:
                                self.store.upsert_lightweight_batch(batch_assets)
                                stats["lightweight_indexed"] += len(batch_assets)
                    finally:
                        os.unlink(batch_file)

                print(f"Indexed {stats['lightweight_indexed']} lightweight assets", file=sys.stderr)

        # Phase 3: Batch semantic indexing for high-value types
        # Uses batch-blueprint, batch-widget, batch-material, batch-datatable for ~100x speedup
        if profile in ("hybrid", "semantic-only"):
            # Group by type for batch processing
            type_groups = {
                "Blueprint": [],
                "WidgetBlueprint": [],
                "Material": [],
                "MaterialInstance": [],
                "MaterialFunction": [],
                "DataTable": [],
                "DataAsset": [],
            }

            for p, s in asset_summaries.items():
                asset_type = s.get("asset_type", "Unknown")
                if asset_type in type_groups:
                    type_groups[asset_type].append(p)

            total_semantic = sum(len(v) for v in type_groups.values())
            if total_semantic > 0:
                print(f"Phase 3: Batch indexing {total_semantic} semantic assets...", file=sys.stderr)

                # Process each type with its batch command
                batch_commands = {
                    "Blueprint": "batch-blueprint",
                    "WidgetBlueprint": "batch-widget",
                    "Material": "batch-material",
                    "MaterialInstance": "batch-material",
                    "MaterialFunction": "batch-material",
                    "DataTable": "batch-datatable",
                    "DataAsset": None,  # No batch command yet, will fall back
                }

                processed = 0
                for asset_type, paths in type_groups.items():
                    if not paths:
                        continue

                    batch_cmd = batch_commands.get(asset_type)
                    if batch_cmd:
                        # Use batch command
                        result = self._batch_semantic_index(
                            paths, asset_type, batch_cmd, batch_size,
                            progress_callback, processed, total_semantic
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

                print(f"Indexed {stats['semantic_indexed']} semantic assets", file=sys.stderr)

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
                chunks.extend(self._create_widget_chunks(game_path, fs_path, asset_name))
            elif asset_type == "Blueprint":
                chunks.extend(self._create_blueprint_chunks(game_path, fs_path, asset_name))
            elif asset_type in ("Material", "MaterialInstance"):
                chunks.extend(self._create_material_chunks(game_path, fs_path, asset_name, asset_type))
            elif asset_type == "MaterialFunction":
                chunks.extend(self._create_materialfunction_chunks(game_path, fs_path, asset_name))
            elif asset_type == "DataTable":
                chunks.extend(self._create_datatable_chunks(game_path, fs_path, asset_name))
            else:
                # Generic asset summary
                chunks.append(self._create_generic_chunk(game_path, fs_path, asset_name, asset_type, summary))

        except Exception as e:
            # Fall back to generic chunk on error
            chunks.append(self._create_generic_chunk(game_path, fs_path, asset_name, asset_type, summary))

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

    def _create_widget_chunks(self, game_path: str, fs_path: Path, asset_name: str) -> list[DocChunk]:
        """Create chunks for a WidgetBlueprint."""
        chunks = []

        # Get widget tree
        widget_xml = self._run_parser("widgets", fs_path)
        if not widget_xml:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, "WidgetBlueprint", {})]

        # Parse XML
        try:
            root = ET.fromstring(widget_xml)
        except ET.ParseError:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, "WidgetBlueprint", {})]

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
        chunks.append(AssetSummary(
            path=game_path,
            name=asset_name,
            asset_type="WidgetBlueprint",
            widget_count=widget_count,
            references_out=refs,
        ))

        # Create widget tree chunk
        if widget_names:
            chunks.append(WidgetTreeDoc(
                path=game_path,
                name=asset_name,
                root_widget=widget_names[0] if widget_names else "Unknown",
                widget_names=widget_names,
                widget_hierarchy=hierarchy_text[:500],  # Limit length
                references_out=refs,
            ))

        return chunks

    def _create_blueprint_chunks(self, game_path: str, fs_path: Path, asset_name: str) -> list[DocChunk]:
        """Create chunks for a Blueprint."""
        chunks = []

        # Get blueprint data
        bp_xml = self._run_parser("blueprint", fs_path)
        if not bp_xml:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, "Blueprint", {})]

        # Parse XML
        try:
            root = ET.fromstring(bp_xml)
        except ET.ParseError:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, "Blueprint", {})]

        # Extract info
        parent = root.findtext("parent", "")
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
                clean_name = func_name.replace("K2Node_", "").replace("ExecuteUbergraph_", "")
                if clean_name and clean_name not in functions:
                    functions.append(clean_name)

        # Get references
        refs = self._get_asset_references(fs_path)

        # Create asset summary with full details for better search
        chunks.append(AssetSummary(
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
        ))

        # Create chunks for each function
        for func_elem in root.findall(".//function"):
            func_name = func_elem.get("name") or func_elem.text
            if not func_name:
                continue

            flags = func_elem.get("flags", "").split(",") if func_elem.get("flags") else []
            calls_elem = func_elem.find("calls")
            calls = calls_elem.text.split(", ") if calls_elem is not None and calls_elem.text else []

            chunks.append(BlueprintGraphDoc(
                path=game_path,
                asset_name=asset_name,
                function_name=func_name,
                flags=flags,
                calls=calls,
                variables=variables,
                references_out=refs,
            ))

        return chunks

    def _create_material_chunks(self, game_path: str, fs_path: Path, asset_name: str, asset_type: str) -> list[DocChunk]:
        """Create chunks for Material/MaterialInstance."""
        # Get material data
        mat_xml = self._run_parser("material", fs_path)
        if not mat_xml:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, asset_type, {})]

        # Parse XML
        try:
            root = ET.fromstring(mat_xml)
        except ET.ParseError:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, asset_type, {})]

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

        return [MaterialParamsDoc(
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
        )]

    def _create_materialfunction_chunks(self, game_path: str, fs_path: Path, asset_name: str) -> list[DocChunk]:
        """Create chunks for MaterialFunction."""
        # Get material function data
        mf_xml = self._run_parser("materialfunction", fs_path)
        if not mf_xml:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, "MaterialFunction", {})]

        # Parse XML
        try:
            root = ET.fromstring(mf_xml)
        except ET.ParseError:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, "MaterialFunction", {})]

        # Extract inputs
        inputs = []
        inputs_elem = root.find("inputs")
        if inputs_elem is not None:
            for input_elem in inputs_elem.findall("input"):
                inputs.append({
                    "name": input_elem.get("name", ""),
                    "type": input_elem.get("type", ""),
                    "priority": int(input_elem.get("priority", "0")),
                })

        # Extract outputs
        outputs = []
        outputs_elem = root.find("outputs")
        if outputs_elem is not None:
            for output_elem in outputs_elem.findall("output"):
                outputs.append({
                    "name": output_elem.get("name", ""),
                    "priority": int(output_elem.get("priority", "0")),
                })

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

        return [MaterialFunctionDoc(
            path=game_path,
            name=asset_name,
            inputs=inputs,
            outputs=outputs,
            scalar_params=scalar_params,
            vector_params=vector_params,
            static_switches=static_switches,
            references_out=refs,
        )]

    def _create_datatable_chunks(self, game_path: str, fs_path: Path, asset_name: str) -> list[DocChunk]:
        """Create chunks for DataTable."""
        # Get datatable data
        dt_xml = self._run_parser("datatable", fs_path)
        if not dt_xml:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, "DataTable", {})]

        # Parse XML
        try:
            root = ET.fromstring(dt_xml)
        except ET.ParseError:
            return [self._create_generic_chunk(game_path, fs_path, asset_name, "DataTable", {})]

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

        return [DocChunk(
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
        )]

    def _create_generic_chunk(self, game_path: str, fs_path: Path, asset_name: str, asset_type: str, summary: dict) -> DocChunk:
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
            for ref in root.findall(".//asset-refs/ref"):
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
                [str(self.parser_path), command, str(fs_path)],
                capture_output=True,
                text=True,
                timeout=ASSET_TIMEOUT,
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass

        return None

    def _batch_semantic_index(
        self,
        paths: list[str],
        asset_type: str,
        batch_cmd: str,
        batch_size: int,
        progress_callback,
        progress_offset: int,
        progress_total: int,
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
            batch = paths[batch_start:batch_start + batch_size]

            if progress_callback:
                progress_callback(
                    f"Batch {asset_type} {batch_start // batch_size + 1}",
                    progress_offset + batch_start,
                    progress_total
                )

            # Write batch to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
                for p in batch:
                    f.write(p + '\n')
                batch_file = f.name

            try:
                result = subprocess.run(
                    [str(self.parser_path), batch_cmd, batch_file],
                    capture_output=True,
                    text=True,
                    timeout=BATCH_TIMEOUT,
                )

                if result.returncode == 0:
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
                            refs = data.get("refs", [])

                            chunks = self._create_chunks_from_json(
                                data, game_path, asset_name, asset_type, refs
                            )

                            # Store chunks
                            for chunk in chunks:
                                embedding = None
                                if self.embed_fn:
                                    try:
                                        embedding = self.embed_fn(chunk.text)
                                        chunk.embed_model = self.embed_model
                                        chunk.embed_version = self.embed_version
                                    except Exception:
                                        pass

                                self.store.upsert_doc(chunk, embedding, force=self.force)

                            stats["indexed"] += 1
                        except json.JSONDecodeError:
                            stats["errors"] += 1
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
        asset_name: str,
        asset_type: str,
        refs: list[str],
    ) -> list[DocChunk]:
        """Create doc chunks from batch JSON output."""
        chunks = []

        if asset_type == "Blueprint":
            chunks.extend(self._chunks_from_blueprint_json(data, game_path, asset_name, refs))
        elif asset_type == "WidgetBlueprint":
            chunks.extend(self._chunks_from_widget_json(data, game_path, asset_name, refs))
        elif asset_type in ("Material", "MaterialInstance", "MaterialFunction"):
            chunks.extend(self._chunks_from_material_json(data, game_path, asset_name, asset_type, refs))
        elif asset_type == "DataTable":
            chunks.extend(self._chunks_from_datatable_json(data, game_path, asset_name, refs))
        else:
            # Generic fallback
            chunks.append(AssetSummary(
                path=game_path,
                name=asset_name,
                asset_type=asset_type,
                references_out=refs,
            ))

        return chunks

    def _chunks_from_blueprint_json(
        self, data: dict, game_path: str, asset_name: str, refs: list[str]
    ) -> list[DocChunk]:
        """Create chunks from batch-blueprint JSON output."""
        chunks = []

        parent = data.get("parent", "")
        events = data.get("events", [])
        components = data.get("components", [])
        variables = data.get("variables", [])
        interfaces = data.get("interfaces", [])
        functions_data = data.get("functions", [])

        # Extract function names
        functions = [f.get("name", "") if isinstance(f, dict) else str(f) for f in functions_data]

        # Create asset summary
        chunks.append(AssetSummary(
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
        ))

        # Create function chunks
        for func_data in functions_data:
            if isinstance(func_data, dict):
                func_name = func_data.get("name", "")
                flags = func_data.get("flags", "").split(",") if func_data.get("flags") else []
                calls = func_data.get("calls", [])

                if func_name:
                    chunks.append(BlueprintGraphDoc(
                        path=game_path,
                        asset_name=asset_name,
                        function_name=func_name,
                        flags=flags,
                        calls=calls,
                        variables=variables,
                        references_out=refs,
                    ))

        return chunks

    def _chunks_from_widget_json(
        self, data: dict, game_path: str, asset_name: str, refs: list[str]
    ) -> list[DocChunk]:
        """Create chunks from batch-widget JSON output."""
        chunks = []

        widget_count = data.get("widget_count", 0)
        widget_names = data.get("widget_names", [])

        # Extract blueprint metadata (new fields from enhanced batch-widget)
        parent = data.get("parent", "")
        interfaces = data.get("interfaces", [])
        events = data.get("events", [])
        variables = data.get("variables", [])
        functions_data = data.get("functions", [])
        functions = [f.get("name", "") if isinstance(f, dict) else str(f) for f in functions_data]

        # Build hierarchy text from widgets list
        widgets = data.get("widgets", [])
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
        chunks.append(AssetSummary(
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
        ))

        # Widget tree doc
        if widget_names:
            chunks.append(WidgetTreeDoc(
                path=game_path,
                name=asset_name,
                root_widget=widget_names[0] if widget_names else "Unknown",
                widget_names=widget_names,
                widget_hierarchy=hierarchy_text[:500],
                references_out=refs,
            ))

        return chunks

    def _chunks_from_material_json(
        self, data: dict, game_path: str, asset_name: str, asset_type: str, refs: list[str]
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

        return [MaterialParamsDoc(
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
        )]

    def _chunks_from_datatable_json(
        self, data: dict, game_path: str, asset_name: str, refs: list[str]
    ) -> list[DocChunk]:
        """Create chunks from batch-datatable JSON output."""
        row_struct = data.get("row_struct", "Unknown")
        row_count = data.get("row_count", 0)
        columns = data.get("columns", [])
        sample_keys = data.get("sample_keys", [])

        # Build text description
        text = f"DataTable {asset_name} with struct {row_struct}. {row_count} rows. "
        if columns:
            text += f"Columns: {', '.join(columns[:10])}. "
        if sample_keys:
            text += f"Sample rows: {', '.join(sample_keys[:5])}."

        return [DocChunk(
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
        )]

    def _game_path_to_fs(self, game_path: str) -> Path:
        """Convert game path to filesystem path."""
        # Check if it's a plugin path (e.g., /ShooterCore/UI/Widget)
        for mount_point, plugin_content in self.plugin_paths.items():
            prefix = f"/{mount_point}/"
            if game_path.startswith(prefix):
                path = game_path[len(prefix):]
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
                game_path = f"/{mount_point}/" + str(rel).replace("\\", "/")
                if game_path.endswith(".uasset"):
                    game_path = game_path[:-7]
                return game_path
            except ValueError:
                continue

        # Default: Content/UI/Widget.uasset -> /Game/UI/Widget
        try:
            rel = fs_path.relative_to(self.content_path)
            game_path = "/Game/" + str(rel).replace("\\", "/")
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
