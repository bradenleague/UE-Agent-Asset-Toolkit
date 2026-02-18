"""Project profile system for extracting project-specific config from the engine code.

Profiles define project-specific asset types, class mappings, and extraction rules
so the indexer/parser/MCP server stay project-agnostic.

Usage:
    from project_profile import load_profile
    profile = load_profile("lyra")        # explicit name
    profile = load_profile(None)          # resolve from config.json active project
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_PROFILES_DIR = Path(__file__).parent / "profiles"


@dataclass
class ProjectProfile:
    """All project-specific configuration consumed by the indexer, parser, and MCP server."""

    profile_name: str = ""

    # Export class name -> reclassified asset type (Python indexer + C# parser)
    export_class_reclassify: dict[str, str] = field(default_factory=dict)

    # Asset name prefix -> type (Python indexer + C# parser)
    name_prefixes: dict[str, str] = field(default_factory=dict)

    # Additional types that get full semantic indexing
    semantic_types: list[str] = field(default_factory=list)

    # Types routed to game-feature extraction handler
    game_feature_types: list[str] = field(default_factory=list)

    # Blueprint parent -> redirect to game feature handler
    blueprint_parent_redirects: dict[str, str] = field(default_factory=dict)

    # DataAsset classes with custom extractors
    data_asset_extractors: list[str] = field(default_factory=list)

    # Deep-ref export classes and candidate asset names
    deep_ref_export_classes: list[str] = field(default_factory=list)
    deep_ref_candidates: list[str] = field(default_factory=list)

    # MCP: widget trace additions
    widget_rank_terms: list[str] = field(default_factory=list)
    widget_fallback_patterns: list[str] = field(default_factory=list)


# Module-level cache: profile_name -> ProjectProfile
_cache: dict[str, ProjectProfile] = {}


def _resolve_profile_name() -> Optional[str]:
    """Resolve profile name from config.json active project entry."""
    config_path = Path(__file__).parent / "config.json"
    if not config_path.exists():
        return None
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        active = config.get("active_project", "")
        projects = config.get("projects", {})
        if active and active in projects:
            return projects[active].get("profile", None)
    except (json.JSONDecodeError, IOError):
        pass
    return None


def _load_json_profile(name: str) -> dict:
    """Load a profile JSON file by name. Raises FileNotFoundError if missing."""
    path = _PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Profile '{name}' not found at {path}. "
            f"Available profiles: {', '.join(p.stem for p in _PROFILES_DIR.glob('*.json') if not p.stem.startswith('.'))}"
        )
    with open(path, "r") as f:
        return json.load(f)


def _merge_profiles(defaults: dict, overlay: dict) -> dict:
    """Merge overlay on top of defaults with per-key override semantics."""
    merged = dict(defaults)
    for key, value in overlay.items():
        merged[key] = value  # Per-key override — overlay replaces entirely
    return merged


def load_profile(
    profile_name: Optional[str] = None, emit_info: bool = True
) -> ProjectProfile:
    """Load and merge a project profile.

    Args:
        profile_name: Explicit profile name (e.g., "lyra"). If None, resolves
                      from config.json active project's "profile" field.
        emit_info: If True, print an informational message when no project
                   profile is configured and engine defaults are used.

    Returns:
        ProjectProfile with defaults merged with the named profile.
        If no profile is configured, returns defaults only and optionally
        prints an informational message to stderr.
    """
    # Resolve name
    if profile_name is None:
        profile_name = _resolve_profile_name()

    cache_key = profile_name or "__defaults_only__"

    if cache_key in _cache:
        return _cache[cache_key]

    # Load defaults
    defaults = _load_json_profile("_defaults")

    if profile_name and profile_name != "_defaults":
        overlay = _load_json_profile(profile_name)
        merged = _merge_profiles(defaults, overlay)
    else:
        if profile_name is None and emit_info:
            print(
                "INFO: Using engine defaults. "
                'Set "profile" in config.json project entry to enable project-specific types.',
                file=sys.stderr,
            )
        merged = defaults

    profile = ProjectProfile(
        profile_name=merged.get("profile_name", cache_key),
        export_class_reclassify=merged.get("export_class_reclassify", {}),
        name_prefixes=merged.get("name_prefixes", {}),
        semantic_types=merged.get("semantic_types", []),
        game_feature_types=merged.get("game_feature_types", []),
        blueprint_parent_redirects=merged.get("blueprint_parent_redirects", {}),
        data_asset_extractors=merged.get("data_asset_extractors", []),
        deep_ref_export_classes=merged.get("deep_ref_export_classes", []),
        deep_ref_candidates=merged.get("deep_ref_candidates", []),
        widget_rank_terms=merged.get("widget_rank_terms", []),
        widget_fallback_patterns=merged.get("widget_fallback_patterns", []),
    )

    _cache[cache_key] = profile
    return profile


def get_parser_type_config(profile: ProjectProfile) -> dict:
    """Extract the subset of profile config needed by the C# parser.

    Returns dict with 'export_class_reclassify' and 'name_prefixes' keys.
    """
    return {
        "export_class_reclassify": profile.export_class_reclassify,
        "name_prefixes": profile.name_prefixes,
    }


def clear_cache() -> None:
    """Clear the profile cache (useful for tests)."""
    _cache.clear()
