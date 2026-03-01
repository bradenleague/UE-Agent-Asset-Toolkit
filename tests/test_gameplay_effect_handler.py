"""Tests for GameplayEffect data asset extractor."""

import json
from pathlib import Path
from unittest.mock import MagicMock


from unreal_agent.knowledge_index.indexer import AssetIndexer, _collect_and_merge_tags


# ---------------------------------------------------------------------------
# Fixtures: captured-style JSON for GE assets
# ---------------------------------------------------------------------------

GE_DAMAGE_INSTANT_INSPECT = {
    "path": "/fake/GE_Damage_Pistol.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "GE_Damage_Pistol",
            "type": "NormalExport",
            "class": "GameplayEffect",
            "properties": [
                {
                    "name": "DurationPolicy",
                    "type": "EnumProperty",
                    "value": "EGameplayEffectDurationType::Instant",
                },
                {
                    "name": "Modifiers",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "_type": "GameplayModifierInfo",
                            "Attribute": {
                                "_type": "GameplayAttribute",
                                "AttributeName": "Health",
                            },
                            "ModifierOp": "EGameplayModOp::Additive",
                            "ModifierMagnitude": {
                                "_type": "GameplayEffectModifierMagnitude",
                                "MagnitudeCalculationType": "EGameplayEffectMagnitudeCalculation::ScalableFloat",
                                "ScalableFloatMagnitude": {
                                    "_type": "ScalableFloat",
                                    "Value": -25.0,
                                },
                            },
                        }
                    ],
                },
                {
                    "name": "InheritableGameplayEffectTags",
                    "type": "StructProperty",
                    "value": {
                        "_type": "InheritedTagContainer",
                        "CombinedTags": {
                            "_type": "GameplayTagContainer",
                            "tags": ["Damage.Physical"],
                        },
                        "Added": {
                            "_type": "GameplayTagContainer",
                            "tags": ["Damage.Physical"],
                        },
                    },
                },
            ],
        },
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}

GE_DURATION_ONLY_INSPECT = {
    "path": "/fake/GE_Buff_Speed.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "GE_Buff_Speed",
            "type": "NormalExport",
            "class": "GameplayEffect",
            "properties": [
                {
                    "name": "DurationPolicy",
                    "type": "EnumProperty",
                    "value": "EGameplayEffectDurationType::HasDuration",
                },
                {
                    "name": "StackingType",
                    "type": "EnumProperty",
                    "value": "EGameplayEffectStackingType::AggregateByTarget",
                },
                {
                    "name": "StackLimitCount",
                    "type": "IntProperty",
                    "value": 3,
                },
            ],
        },
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}

GE_WITH_TAGS_INSPECT = {
    "path": "/fake/GE_Apply_Fire.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "GE_Apply_Fire",
            "type": "NormalExport",
            "class": "GameplayEffect",
            "properties": [
                {
                    "name": "DurationPolicy",
                    "type": "EnumProperty",
                    "value": "EGameplayEffectDurationType::Infinite",
                },
                {
                    "name": "ApplicationTagRequirements",
                    "type": "StructProperty",
                    "value": {
                        "_type": "GameplayTagRequirements",
                        "RequireTags": {
                            "_type": "GameplayTagContainer",
                            "tags": ["Status.Alive"],
                        },
                        "IgnoreTags": {
                            "_type": "GameplayTagContainer",
                            "tags": ["Status.Immune.Fire"],
                        },
                    },
                },
                {
                    "name": "RemovalTagRequirements",
                    "type": "StructProperty",
                    "value": {
                        "_type": "GameplayTagRequirements",
                        "RequireTags": {
                            "_type": "GameplayTagContainer",
                            "tags": ["Status.Dead"],
                        },
                    },
                },
            ],
        },
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}

GE_RAW_EXPORT_INSPECT = {
    "path": "/fake/GE_RawEffect.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "GE_RawEffect",
            "type": "RawExport",
            "class": "GameplayEffect",
        },
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}

GE_WITH_REFS_INSPECT = {
    "path": "/fake/GE_GrantAbility.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "GE_GrantAbility",
            "type": "NormalExport",
            "class": "GameplayEffect",
            "properties": [
                {
                    "name": "DurationPolicy",
                    "type": "EnumProperty",
                    "value": "EGameplayEffectDurationType::Instant",
                },
                {
                    "name": "GrantedAbilities",
                    "type": "ArrayProperty",
                    "value": ["/Game/Abilities/GA_FireBlast"],
                },
            ],
        },
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}

# Real GE structure: main export is Blueprint, properties in CDO
GE_BLUEPRINT_SUBCLASS_INSPECT = {
    "path": "/fake/GE_Damage_Real.uasset",
    "exports_count": 4,
    "exports": [
        {
            "name": "GE_Damage_Real",
            "type": "NormalExport",
            "class": "Blueprint",
            "properties": [
                {
                    "name": "ParentClass",
                    "type": "ObjectProperty",
                    "value": "/Game/GE/GE_DamageParent",
                },
                {"name": "BlueprintSystemVersion", "type": "IntProperty", "value": 2},
            ],
        },
        {
            "name": "GE_Damage_Real_C",
            "type": "NormalExport",
            "class": "BlueprintGeneratedClass",
            "properties": [],
        },
        {
            "name": "Default__GE_Damage_Real_C",
            "type": "NormalExport",
            "class": "2",
            "properties": [
                {
                    "name": "DurationPolicy",
                    "type": "EnumProperty",
                    "value": "EGameplayEffectDurationType::Instant",
                },
                {
                    "name": "InheritableGameplayEffectTags",
                    "type": "StructProperty",
                    "value": {
                        "_type": "InheritedTagContainer",
                        "CombinedTags": {
                            "_type": "GameplayTagContainer",
                            "tags": ["Damage.Type.Basic"],
                        },
                        "Added": {
                            "_type": "GameplayTagContainer",
                            "tags": ["Damage.Type.Basic"],
                        },
                    },
                },
            ],
        },
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}

# GE with /Script/ tuple parent (e.g., root GameplayEffect)
GE_TUPLE_PARENT_INSPECT = {
    "path": "/fake/GE_RootEffect.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "GE_RootEffect",
            "type": "NormalExport",
            "class": "GameplayEffect",
            "properties": [
                {
                    "name": "DurationPolicy",
                    "type": "EnumProperty",
                    "value": "EGameplayEffectDurationType::Instant",
                },
                {
                    "name": "ParentClass",
                    "type": "ObjectProperty",
                    "value": "(/Script/GameplayAbilities, GameplayEffect, )",
                },
            ],
        },
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}

# GE with object-ref style /Script parent including module.class in one token
GE_SCRIPT_OBJECT_PARENT_INSPECT = {
    "path": "/fake/GE_ScriptRefParent.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "GE_ScriptRefParent",
            "type": "NormalExport",
            "class": "GameplayEffect",
            "properties": [
                {
                    "name": "DurationPolicy",
                    "type": "EnumProperty",
                    "value": "EGameplayEffectDurationType::Instant",
                },
                {
                    "name": "ParentClass",
                    "type": "ObjectProperty",
                    "value": "(, /Script/GameplayAbilities.GameplayEffect, )",
                },
            ],
        },
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_indexer_with_mock(inspect_data, refs=None, profile_name="_defaults"):
    from unreal_agent.project_profile import load_profile

    indexer = AssetIndexer.__new__(AssetIndexer)
    indexer.store = MagicMock()
    indexer.parser_path = Path("/fake/AssetParser")
    indexer.embed_fn = None
    indexer.embed_model = None
    indexer.embed_version = None
    indexer.force = False
    indexer.plugin_paths = {}

    profile = load_profile(profile_name)
    indexer._apply_profile(profile)

    def mock_run_parser(command, fs_path):
        if command == "inspect":
            return json.dumps(inspect_data)
        if command == "references":
            ref_xml = "<asset-analysis><asset-refs>"
            for r in refs or []:
                ref_xml += f"<ref>{r}</ref>"
            ref_xml += "</asset-refs></asset-analysis>"
            return ref_xml
        return None

    indexer._run_parser = mock_run_parser
    indexer._get_asset_references = MagicMock(return_value=refs or [])
    return indexer


# ---------------------------------------------------------------------------
# Tests: GameplayEffect extractor
# ---------------------------------------------------------------------------


class TestGameplayEffectExtractor:
    def test_standard_ge_with_modifiers(self):
        """GE with modifiers → correct text, metadata, typed_refs."""
        indexer = _make_indexer_with_mock(GE_DAMAGE_INSTANT_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_Damage_Pistol",
            Path("/fake/GE_Damage_Pistol.uasset"),
            "GE_Damage_Pistol",
        )
        assert len(chunks) == 1
        chunk = chunks[0]

        # Check text
        assert "GameplayEffect" in chunk.text
        assert "Instant" in chunk.text
        assert "Health" in chunk.text
        assert "-25" in chunk.text

        # Check metadata
        meta = chunk.metadata
        assert meta["duration_policy"] == "Instant"
        assert len(meta["modifiers"]) == 1
        assert meta["modifiers"][0]["attribute"] == "Health"
        assert meta["modifiers"][0]["operation"] == "Additive"
        assert meta["modifiers"][0]["magnitude"] == -25.0

    def test_ge_duration_only_no_modifiers(self):
        """GE with duration + stacking but no modifiers → graceful handling."""
        indexer = _make_indexer_with_mock(GE_DURATION_ONLY_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_Buff_Speed",
            Path("/fake/GE_Buff_Speed.uasset"),
            "GE_Buff_Speed",
        )
        assert len(chunks) == 1
        chunk = chunks[0]

        assert "HasDuration" in chunk.text
        meta = chunk.metadata
        assert meta["duration_policy"] == "HasDuration"
        assert meta["stacking"]["type"] == "AggregateByTarget"
        assert meta["stacking"]["limit"] == 3
        assert meta["modifiers"] == []

    def test_ge_with_tag_requirements(self):
        """GE with tag requirements → tags in metadata."""
        indexer = _make_indexer_with_mock(GE_WITH_TAGS_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_Apply_Fire",
            Path("/fake/GE_Apply_Fire.uasset"),
            "GE_Apply_Fire",
        )
        chunk = chunks[0]
        meta = chunk.metadata

        # The tag walker + extractor should find all tags
        tags = meta.get("gameplay_tags", [])
        assert "Status.Alive" in tags
        assert "Status.Dead" in tags
        assert "Status.Immune.Fire" in tags

    def test_ge_with_granted_abilities_uses_asset_refs(self):
        """GE referencing abilities → uses_asset refs."""
        indexer = _make_indexer_with_mock(GE_WITH_REFS_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_GrantAbility",
            Path("/fake/GE_GrantAbility.uasset"),
            "GE_GrantAbility",
        )
        chunk = chunks[0]
        assert "/Game/Abilities/GA_FireBlast" in chunk.references_out

    def test_raw_export_ge_falls_through(self):
        """RawExport GE → falls through to default extractor cleanly."""
        indexer = _make_indexer_with_mock(GE_RAW_EXPORT_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_RawEffect",
            Path("/fake/GE_RawEffect.uasset"),
            "GE_RawEffect",
        )
        assert len(chunks) == 1
        chunk = chunks[0]
        assert "raw" in chunk.text.lower() or "GameplayEffect" in chunk.text
        assert chunk.metadata.get("raw_export") is True

    def test_ge_asset_type_is_gameplay_effect(self):
        """GE chunk should have asset_type='GameplayEffect', not 'DataAsset'."""
        indexer = _make_indexer_with_mock(GE_DAMAGE_INSTANT_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_Damage_Pistol",
            Path("/fake/GE_Damage_Pistol.uasset"),
            "GE_Damage_Pistol",
        )
        assert chunks[0].asset_type == "GameplayEffect"


# ---------------------------------------------------------------------------
# Tests: _collect_and_merge_tags helper
# ---------------------------------------------------------------------------


class TestCollectAndMergeTags:
    def test_merges_walker_tags_with_existing(self):
        """Helper merges walker tags with existing metadata tags."""
        props = [
            {
                "name": "Tags",
                "value": {
                    "_type": "GameplayTagContainer",
                    "tags": ["Effect.Damage", "Effect.Physical"],
                },
            }
        ]
        metadata = {"gameplay_tags": ["Effect.Existing"]}
        text_parts = ["Some text"]

        _collect_and_merge_tags(props, metadata, text_parts)

        assert "Effect.Damage" in metadata["gameplay_tags"]
        assert "Effect.Existing" in metadata["gameplay_tags"]
        assert "Effect.Physical" in metadata["gameplay_tags"]
        assert any("Tags:" in p for p in text_parts)

    def test_no_tags_no_mutation(self):
        """No tags found → metadata and text_parts unchanged."""
        props = [{"name": "Foo", "value": 42}]
        metadata = {}
        text_parts = ["Some text"]

        _collect_and_merge_tags(props, metadata, text_parts)

        assert "gameplay_tags" not in metadata
        assert len(text_parts) == 1

    def test_skips_tags_line_if_already_present(self):
        """Don't add duplicate Tags: line."""
        props = [
            {
                "name": "T",
                "value": {"_type": "GameplayTag", "TagName": "Foo.Bar"},
            }
        ]
        metadata = {}
        text_parts = ["Tags: Already.Here"]

        _collect_and_merge_tags(props, metadata, text_parts)

        # Should NOT add another Tags: line
        tags_lines = [p for p in text_parts if p.startswith("Tags:")]
        assert len(tags_lines) == 1


# ---------------------------------------------------------------------------
# Tests: GameplayEffect in defaults profile
# ---------------------------------------------------------------------------


class TestGameplayEffectProfile:
    def test_defaults_profile_has_gameplay_effect(self):
        from unreal_agent.project_profile import load_profile

        profile = load_profile("_defaults")
        assert "GameplayEffect" in profile.export_class_reclassify
        assert "GameplayEffect" in profile.semantic_types
        assert "GameplayEffect" in profile.data_asset_extractors

    def test_lyra_overlay_preserves_ge_defaults(self):
        """Lyra overlay must not wipe engine-level GameplayEffect config."""
        from unreal_agent.project_profile import load_profile, clear_cache

        clear_cache()
        profile = load_profile("lyra", emit_info=False)
        assert "GameplayEffect" in profile.export_class_reclassify
        assert "GE_" in profile.name_prefixes
        assert "GameplayEffect" in profile.semantic_types
        assert "GameplayEffect" in profile.data_asset_extractors
        # Lyra-specific entries should also be present
        assert "LyraExperienceActionSet" in profile.export_class_reclassify
        assert "LAS_" in profile.name_prefixes


# ---------------------------------------------------------------------------
# Tests: Blueprint-subclass GE (CDO export path)
# ---------------------------------------------------------------------------


class TestBlueprintSubclassGE:
    def test_cdo_properties_used_for_blueprint_ge(self):
        """When main export is Blueprint but asset_type is GameplayEffect,
        properties should come from the CDO export."""
        indexer = _make_indexer_with_mock(GE_BLUEPRINT_SUBCLASS_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_Damage_Real",
            Path("/fake/GE_Damage_Real.uasset"),
            "GE_Damage_Real",
            asset_type="GameplayEffect",
        )
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.asset_type == "GameplayEffect"
        assert "GameplayEffect" in chunk.text
        assert "Instant" in chunk.text
        # Tags from CDO should be extracted
        tags = chunk.metadata.get("gameplay_tags", [])
        assert "Damage.Type.Basic" in tags
        # ParentClass from main export should be captured
        assert chunk.metadata.get("parent_class") is not None

    def test_empty_cdo_still_dispatches_as_ge(self):
        """CDO with empty properties list should still dispatch as GameplayEffect."""
        inspect_data = {
            "path": "/fake/GE_EmptyCDO.uasset",
            "exports_count": 3,
            "exports": [
                {
                    "name": "GE_EmptyCDO",
                    "type": "NormalExport",
                    "class": "Blueprint",
                    "properties": [
                        {
                            "name": "ParentClass",
                            "type": "ObjectProperty",
                            "value": "/Game/GE/GE_BaseParent",
                        },
                    ],
                },
                {
                    "name": "Default__GE_EmptyCDO_C",
                    "type": "NormalExport",
                    "class": "2",
                    "properties": [],
                },
                {
                    "name": "PackageMetaData",
                    "type": "MetaDataExport",
                    "class": "MetaData",
                    "properties": [],
                },
            ],
        }
        indexer = _make_indexer_with_mock(inspect_data)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_EmptyCDO",
            Path("/fake/GE_EmptyCDO.uasset"),
            "GE_EmptyCDO",
            asset_type="GameplayEffect",
        )
        assert len(chunks) == 1
        assert chunks[0].asset_type == "GameplayEffect"
        assert "GameplayEffect" in chunks[0].text

    def test_tuple_parent_resolves_to_class_name(self):
        """Tuple ref (/Script/GameplayAbilities, GameplayEffect, ) should
        resolve to 'GameplayEffect', not '/Script/GameplayAbilities'."""
        indexer = _make_indexer_with_mock(GE_TUPLE_PARENT_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_RootEffect",
            Path("/fake/GE_RootEffect.uasset"),
            "GE_RootEffect",
            asset_type="GameplayEffect",
        )
        chunk = chunks[0]
        parent = chunk.metadata.get("parent_class", "")
        assert parent == "GameplayEffect", f"Expected 'GameplayEffect', got '{parent}'"
        # inherits_from edge should target class:GameplayEffect
        assert any("GameplayEffect" in k for k in chunk.typed_references_out), (
            f"Expected inherits_from edge with GameplayEffect, got {chunk.typed_references_out}"
        )

    def test_script_object_ref_parent_resolves_to_class_name(self):
        """Object-ref style /Script parent should resolve to the class segment."""
        indexer = _make_indexer_with_mock(GE_SCRIPT_OBJECT_PARENT_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/GE/GE_ScriptRefParent",
            Path("/fake/GE_ScriptRefParent.uasset"),
            "GE_ScriptRefParent",
            asset_type="GameplayEffect",
        )
        chunk = chunks[0]
        parent = chunk.metadata.get("parent_class", "")
        assert parent == "GameplayEffect", f"Expected 'GameplayEffect', got '{parent}'"
        assert any("GameplayEffect" in k for k in chunk.typed_references_out), (
            f"Expected inherits_from edge with GameplayEffect, got {chunk.typed_references_out}"
        )
