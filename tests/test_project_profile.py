"""Tests for the project profile system."""

import pytest
from unittest.mock import patch

from UnrealAgent.project_profile import (
    ProjectProfile,
    load_profile,
    get_parser_type_config,
    clear_cache,
    _load_json_profile,
    _merge_profiles,
)


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    """Ensure a clean profile cache for every test."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# _load_json_profile
# ---------------------------------------------------------------------------


class TestLoadJsonProfile:
    def test_loads_defaults(self):
        data = _load_json_profile("_defaults")
        assert data["profile_name"] == "_defaults"
        assert "GameFeatureData" in data["export_class_reclassify"]

    def test_loads_lyra(self):
        data = _load_json_profile("lyra")
        assert data["profile_name"] == "lyra"
        assert "LyraExperienceActionSet" in data["export_class_reclassify"]

    def test_missing_profile_raises(self):
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            _load_json_profile("nonexistent")


# ---------------------------------------------------------------------------
# _merge_profiles
# ---------------------------------------------------------------------------


class TestMergeProfiles:
    def test_list_concatenate_dedupe(self):
        defaults = {"a": [1, 2], "b": {"x": 1}}
        overlay = {"a": [2, 3]}
        merged = _merge_profiles(defaults, overlay)
        # Lists: concatenate + dedupe, preserving order
        assert merged["a"] == [1, 2, 3]
        assert merged["b"] == {"x": 1}

    def test_dict_deep_merge(self):
        defaults = {"d": {"x": 1, "y": 2}}
        overlay = {"d": {"y": 99, "z": 3}}
        merged = _merge_profiles(defaults, overlay)
        # Dicts: deep merge, overlay wins on conflicts
        assert merged["d"] == {"x": 1, "y": 99, "z": 3}

    def test_scalar_override(self):
        defaults = {"a": 1}
        overlay = {"a": 2}
        merged = _merge_profiles(defaults, overlay)
        assert merged["a"] == 2

    def test_overlay_adds_new_keys(self):
        defaults = {"a": 1}
        overlay = {"b": 2}
        merged = _merge_profiles(defaults, overlay)
        assert merged["a"] == 1
        assert merged["b"] == 2


# ---------------------------------------------------------------------------
# load_profile
# ---------------------------------------------------------------------------


class TestLoadProfile:
    def test_defaults_only(self):
        profile = load_profile("_defaults")
        assert profile.profile_name == "_defaults"
        assert "GameFeatureData" in profile.export_class_reclassify
        assert profile.name_prefixes == {"GE_": "GameplayEffect"}
        assert profile.semantic_types == ["GameplayEffect"]

    def test_lyra_profile(self):
        profile = load_profile("lyra")
        assert profile.profile_name == "lyra"
        assert profile.export_class_reclassify["LyraAbilitySet"] == "DataAsset"
        assert "LAS_" in profile.name_prefixes
        assert "LyraExperienceActionSet" in profile.semantic_types
        assert "LyraExperienceDefinition" in profile.game_feature_types
        assert "LyraExperienceDefinition" in profile.blueprint_parent_redirects
        assert "LyraAbilitySet" in profile.data_asset_extractors
        assert "ShooterCore" in profile.deep_ref_candidates
        assert "LyraHUDLayout" in profile.widget_rank_terms

    def test_lyra_overrides_defaults(self):
        """Lyra profile should override default export_class_reclassify entirely."""
        profile = load_profile("lyra")
        # Lyra profile has all the entries (including engine-level ones it overrides)
        assert "LyraExperienceActionSet" in profile.export_class_reclassify
        # And the engine defaults that lyra includes
        assert "GameFeatureData" in profile.export_class_reclassify

    def test_caching(self):
        p1 = load_profile("lyra")
        p2 = load_profile("lyra")
        assert p1 is p2

    def test_missing_profile_error(self):
        with pytest.raises(FileNotFoundError):
            load_profile("does_not_exist")

    def test_none_profile_uses_defaults(self):
        """When no profile is configured, load_profile(None) returns defaults."""
        # Patch _resolve_profile_name to return None (no config)
        with patch(
            "UnrealAgent.project_profile._resolve_profile_name", return_value=None
        ):
            profile = load_profile(None)
        assert profile.profile_name == "_defaults"
        assert len(profile.data_asset_extractors) == 1
        assert "GameplayEffect" in profile.data_asset_extractors


# ---------------------------------------------------------------------------
# get_parser_type_config
# ---------------------------------------------------------------------------


class TestGetParserTypeConfig:
    def test_contains_expected_keys(self):
        profile = load_profile("lyra")
        config = get_parser_type_config(profile)
        assert "export_class_reclassify" in config
        assert "name_prefixes" in config
        assert config["export_class_reclassify"]["LyraAbilitySet"] == "DataAsset"
        assert config["name_prefixes"]["LAS_"] == "LyraExperienceActionSet"

    def test_defaults_only_config(self):
        profile = load_profile("_defaults")
        config = get_parser_type_config(profile)
        assert "export_class_reclassify" in config
        assert "name_prefixes" in config
        assert config["name_prefixes"] == {"GE_": "GameplayEffect"}


# ---------------------------------------------------------------------------
# ProjectProfile dataclass
# ---------------------------------------------------------------------------


class TestProjectProfile:
    def test_default_construction(self):
        p = ProjectProfile()
        assert p.profile_name == ""
        assert p.export_class_reclassify == {}
        assert p.semantic_types == []

    def test_field_assignment(self):
        p = ProjectProfile(
            profile_name="test",
            export_class_reclassify={"Foo": "Bar"},
            semantic_types=["CustomType"],
        )
        assert p.profile_name == "test"
        assert p.export_class_reclassify["Foo"] == "Bar"
        assert "CustomType" in p.semantic_types
