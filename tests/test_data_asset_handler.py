"""Tests for DataAsset handler and per-class extractors in the indexer."""

import json
from pathlib import Path
from unittest.mock import MagicMock


from UnrealAgent.knowledge_index.indexer import AssetIndexer


# ---------------------------------------------------------------------------
# Fixtures: captured JSON from actual AssetParser inspect output
# ---------------------------------------------------------------------------

ABILITY_SET_INSPECT = {
    "path": "/fake/AbilitySet_Elimination.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "AbilitySet_Elimination",
            "type": "NormalExport",
            "class": "LyraAbilitySet",
            "properties": [
                {
                    "name": "GrantedGameplayAbilities",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "_type": "LyraAbilitySet_GameplayAbility",
                            "Ability": "/ShooterCore/Elimination/GA_ShowLeaderboard_TDM",
                            "AbilityLevel": 1,
                            "InputTag": {
                                "_type": "GameplayTag",
                                "TagName": "InputTag.Ability.ShowLeaderboard",
                            },
                        },
                        {
                            "_type": "LyraAbilitySet_GameplayAbility",
                            "Ability": "/ShooterCore/Game/Respawn/GA_AutoRespawn",
                            "AbilityLevel": 1,
                            "InputTag": {"_type": "GameplayTag", "TagName": "None"},
                        },
                    ],
                }
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

HERO_DATA_INSPECT = {
    "path": "/fake/HeroData_ShooterGame.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "HeroData_ShooterGame",
            "type": "NormalExport",
            "class": "LyraPawnData",
            "properties": [
                {
                    "name": "PawnClass",
                    "type": "ObjectProperty",
                    "value": "/ShooterCore/Game/B_Hero_ShooterMannequin",
                },
                {
                    "name": "AbilitySets",
                    "type": "ArrayProperty",
                    "value": ["/ShooterCore/Game/AbilitySet_ShooterHero"],
                },
                {
                    "name": "TagRelationshipMapping",
                    "type": "ObjectProperty",
                    "value": "/ShooterCore/Game/TagRelationships_ShooterHero",
                },
                {
                    "name": "InputConfig",
                    "type": "ObjectProperty",
                    "value": "/Game/Input/InputData_Hero",
                },
                {
                    "name": "DefaultCameraMode",
                    "type": "ObjectProperty",
                    "value": "/Game/Characters/Cameras/CM_ThirdPerson",
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

INPUT_CONFIG_INSPECT = {
    "path": "/fake/InputData_Hero.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "InputData_Hero",
            "type": "NormalExport",
            "class": "LyraInputConfig",
            "properties": [
                {
                    "name": "NativeInputActions",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "_type": "LyraInputAction",
                            "InputAction": "/Game/Input/Actions/IA_Move",
                            "InputTag": {
                                "_type": "GameplayTag",
                                "TagName": "InputTag.Move",
                            },
                        },
                        {
                            "_type": "LyraInputAction",
                            "InputAction": "/Game/Input/Actions/IA_Look_Mouse",
                            "InputTag": {
                                "_type": "GameplayTag",
                                "TagName": "InputTag.Look.Mouse",
                            },
                        },
                    ],
                },
                {
                    "name": "AbilityInputActions",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "_type": "LyraInputAction",
                            "InputAction": "/Game/Input/Actions/IA_Jump",
                            "InputTag": {
                                "_type": "GameplayTag",
                                "TagName": "InputTag.Jump",
                            },
                        },
                    ],
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

PLAYLIST_INSPECT = {
    "path": "/fake/DA_ExamplePlaylist.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "DA_ExamplePlaylist",
            "type": "NormalExport",
            "class": "LyraUserFacingExperienceDefinition",
            "properties": [
                {
                    "name": "MapID",
                    "type": "StructProperty",
                    "value": {
                        "_type": "PrimaryAssetId",
                        "PrimaryAssetType": {
                            "_type": "PrimaryAssetType",
                            "Name": "Map",
                        },
                        "PrimaryAssetName": "/Game/System/DefaultEditorMap/L_DefaultEditorOverview",
                    },
                },
                {
                    "name": "ExperienceID",
                    "type": "StructProperty",
                    "value": {
                        "_type": "PrimaryAssetId",
                        "PrimaryAssetType": {
                            "_type": "PrimaryAssetType",
                            "Name": "LyraExperienceDefinition",
                        },
                        "PrimaryAssetName": "B_LyraDefaultExperience",
                    },
                },
                {"name": "MaxPlayerCount", "type": "IntProperty", "value": 4},
                {
                    "name": "LoadingScreenWidget",
                    "type": "SoftObjectProperty",
                    "value": "(, /Game/UI/Foundation/LoadingScreen/W_LoadingScreen_DefaultContent.W_LoadingScreen_DefaultContent_C, )",
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

CONTEXT_EFFECTS_INSPECT = {
    "path": "/fake/CFX_DefaultSkin.uasset",
    "exports_count": 2,
    "exports": [
        {
            "name": "CFX_DefaultSkin",
            "type": "NormalExport",
            "class": "LyraContextEffectsLibrary",
            "properties": [
                {
                    "name": "ContextEffects",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "_type": "LyraContextEffects",
                            "EffectTag": {
                                "_type": "GameplayTag",
                                "TagName": "AnimEffect.Footstep.Walk",
                            },
                            "Context": {
                                "_type": "GameplayTagContainer",
                                "Context": "(SurfaceType.Concrete)",
                            },
                            "Effects": [
                                {"_type": "SoftObjectPath", "Effects": "[max depth]"}
                            ],
                        },
                        {
                            "_type": "LyraContextEffects",
                            "EffectTag": {
                                "_type": "GameplayTag",
                                "TagName": "AnimEffect.Footstep.Walk",
                            },
                            "Context": {
                                "_type": "GameplayTagContainer",
                                "Context": "(SurfaceType.Glass)",
                            },
                            "Effects": [
                                {"_type": "SoftObjectPath", "Effects": "[max depth]"}
                            ],
                        },
                        {
                            "_type": "LyraContextEffects",
                            "EffectTag": {
                                "_type": "GameplayTag",
                                "TagName": "AnimEffect.Footstep.Land",
                            },
                            "Context": {
                                "_type": "GameplayTagContainer",
                                "Context": "(SurfaceType.Default)",
                            },
                            "Effects": [
                                {"_type": "SoftObjectPath", "Effects": "[max depth]"}
                            ],
                        },
                    ],
                }
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

TEAM_DA_RAW_INSPECT = {
    "path": "/fake/TeamDA_Blue.uasset",
    "exports_count": 2,
    "exports": [
        {"name": "TeamDA_Blue", "type": "RawExport", "class": "LyraTeamDisplayAsset"},
        {
            "name": "PackageMetaData",
            "type": "MetaDataExport",
            "class": "MetaData",
            "properties": [],
        },
    ],
}

EXPERIENCE_CDO_INSPECT = {
    "path": "/fake/B_ShooterGame_Elimination.uasset",
    "exports_count": 8,
    "exports": [
        {
            "name": "Default__B_ShooterGame_Elimination_C",
            "type": "NormalExport",
            "class": "3",
            "properties": [
                {
                    "name": "GameFeaturesToEnable",
                    "type": "ArrayProperty",
                    "value": ["ShooterCore"],
                },
                {
                    "name": "DefaultPawnData",
                    "type": "ObjectProperty",
                    "value": "/ShooterCore/Game/HeroData_ShooterGame",
                },
                {
                    "name": "ActionSets",
                    "type": "ArrayProperty",
                    "value": [
                        "/ShooterCore/Experiences/LAS_ShooterGame_SharedInput",
                        "/ShooterCore/Experiences/LAS_ShooterGame_StandardComponents",
                        "/ShooterCore/Experiences/LAS_ShooterGame_StandardHUD",
                        "/ShooterCore/Accolades/EAS_BasicShooterAcolades",
                    ],
                },
            ],
        },
        {
            "name": "B_ShooterGame_Elimination",
            "type": "NormalExport",
            "class": "Blueprint",
            "properties": [
                {
                    "name": "ParentClass",
                    "type": "ObjectProperty",
                    "value": "(, /Script/LyraGame.LyraExperienceDefinition, )",
                },
            ],
        },
        {
            "name": "GameFeatureAction_AddWidgets_1",
            "type": "NormalExport",
            "class": "GameFeatureAction_AddWidgets",
            "properties": [
                {
                    "name": "Widgets",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "_type": "LyraHUDElementEntry",
                            "WidgetClass": "/ShooterCore/UI/W_EliminationFeed",
                            "SlotID": {
                                "_type": "GameplayTag",
                                "TagName": "HUD.Slot.EliminationFeed",
                            },
                        }
                    ],
                }
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Helper: create an indexer with mocked parser
# ---------------------------------------------------------------------------


def _make_indexer_with_mock(
    inspect_data: dict,
    refs: list[str] | None = None,
    profile_name: str = "lyra",
):
    """Create an AssetIndexer with a mocked _run_parser and _get_asset_references."""
    from UnrealAgent.project_profile import load_profile

    indexer = AssetIndexer.__new__(AssetIndexer)
    indexer.store = MagicMock()
    indexer.parser_path = Path("/fake/AssetParser")
    indexer.embed_fn = None
    indexer.embed_model = None
    indexer.embed_version = None
    indexer.force = False

    # Apply profile so extractor dispatch works
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
# Tests: AbilitySet extractor
# ---------------------------------------------------------------------------


class TestAbilitySetExtractor:
    def test_text_contains_abilities(self):
        indexer = _make_indexer_with_mock(ABILITY_SET_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/ShooterCore/Elimination/AbilitySet_Elimination",
            Path("/fake/AbilitySet_Elimination.uasset"),
            "AbilitySet_Elimination",
        )
        assert len(chunks) == 1
        text = chunks[0].text
        assert "GA_ShowLeaderboard_TDM" in text
        assert "GA_AutoRespawn" in text
        assert "InputTag.Ability.ShowLeaderboard" in text

    def test_refs_contain_ability_paths(self):
        indexer = _make_indexer_with_mock(ABILITY_SET_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/ShooterCore/Elimination/AbilitySet_Elimination",
            Path("/fake/AbilitySet_Elimination.uasset"),
            "AbilitySet_Elimination",
        )
        refs = chunks[0].references_out
        assert "/ShooterCore/Elimination/GA_ShowLeaderboard_TDM" in refs
        assert "/ShooterCore/Game/Respawn/GA_AutoRespawn" in refs

    def test_metadata_has_abilities(self):
        indexer = _make_indexer_with_mock(ABILITY_SET_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/ShooterCore/Elimination/AbilitySet_Elimination",
            Path("/fake/AbilitySet_Elimination.uasset"),
            "AbilitySet_Elimination",
        )
        meta = chunks[0].metadata
        assert "abilities" in meta
        assert len(meta["abilities"]) == 2
        assert meta["abilities"][0]["input_tag"] == "InputTag.Ability.ShowLeaderboard"
        # "None" tag should be stripped
        assert meta["abilities"][1]["input_tag"] == ""


# ---------------------------------------------------------------------------
# Tests: PawnData extractor
# ---------------------------------------------------------------------------


class TestPawnDataExtractor:
    def test_text_contains_pawn_class(self):
        indexer = _make_indexer_with_mock(HERO_DATA_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/ShooterCore/Game/HeroData_ShooterGame",
            Path("/fake/HeroData_ShooterGame.uasset"),
            "HeroData_ShooterGame",
        )
        text = chunks[0].text
        assert "B_Hero_ShooterMannequin" in text
        assert "AbilitySet_ShooterHero" in text
        assert "InputData_Hero" in text
        assert "CM_ThirdPerson" in text

    def test_refs_include_ability_sets(self):
        indexer = _make_indexer_with_mock(HERO_DATA_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/ShooterCore/Game/HeroData_ShooterGame",
            Path("/fake/HeroData_ShooterGame.uasset"),
            "HeroData_ShooterGame",
        )
        refs = chunks[0].references_out
        assert "/ShooterCore/Game/AbilitySet_ShooterHero" in refs
        assert "/Game/Input/InputData_Hero" in refs
        assert "/ShooterCore/Game/TagRelationships_ShooterHero" in refs

    def test_metadata_keys(self):
        indexer = _make_indexer_with_mock(HERO_DATA_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/ShooterCore/Game/HeroData_ShooterGame",
            Path("/fake/HeroData_ShooterGame.uasset"),
            "HeroData_ShooterGame",
        )
        meta = chunks[0].metadata
        assert meta["pawn_class"] == "/ShooterCore/Game/B_Hero_ShooterMannequin"
        assert meta["input_config"] == "/Game/Input/InputData_Hero"
        assert len(meta["ability_sets"]) == 1


# ---------------------------------------------------------------------------
# Tests: InputConfig extractor
# ---------------------------------------------------------------------------


class TestInputConfigExtractor:
    def test_text_contains_action_mappings(self):
        indexer = _make_indexer_with_mock(INPUT_CONFIG_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/Input/InputData_Hero",
            Path("/fake/InputData_Hero.uasset"),
            "InputData_Hero",
        )
        text = chunks[0].text
        assert "IA_Move->InputTag.Move" in text
        assert "IA_Jump->InputTag.Jump" in text
        assert "NativeInputActions" in text
        assert "AbilityInputActions" in text

    def test_refs_include_input_actions(self):
        indexer = _make_indexer_with_mock(INPUT_CONFIG_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/Input/InputData_Hero",
            Path("/fake/InputData_Hero.uasset"),
            "InputData_Hero",
        )
        refs = chunks[0].references_out
        assert "/Game/Input/Actions/IA_Move" in refs
        assert "/Game/Input/Actions/IA_Jump" in refs


# ---------------------------------------------------------------------------
# Tests: ExperienceDefPlaylist extractor
# ---------------------------------------------------------------------------


class TestPlaylistExtractor:
    def test_text_contains_map_and_experience(self):
        indexer = _make_indexer_with_mock(PLAYLIST_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/System/Playlists/DA_ExamplePlaylist",
            Path("/fake/DA_ExamplePlaylist.uasset"),
            "DA_ExamplePlaylist",
        )
        text = chunks[0].text
        assert "L_DefaultEditorOverview" in text
        assert "B_LyraDefaultExperience" in text
        assert "MaxPlayers: 4" in text

    def test_loading_widget_ref(self):
        indexer = _make_indexer_with_mock(PLAYLIST_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/System/Playlists/DA_ExamplePlaylist",
            Path("/fake/DA_ExamplePlaylist.uasset"),
            "DA_ExamplePlaylist",
        )
        refs = chunks[0].references_out
        assert (
            "/Game/UI/Foundation/LoadingScreen/W_LoadingScreen_DefaultContent" in refs
        )


# ---------------------------------------------------------------------------
# Tests: ContextEffects extractor
# ---------------------------------------------------------------------------


class TestContextEffectsExtractor:
    def test_text_contains_effects(self):
        indexer = _make_indexer_with_mock(CONTEXT_EFFECTS_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/ContextEffects/CFX_DefaultSkin",
            Path("/fake/CFX_DefaultSkin.uasset"),
            "CFX_DefaultSkin",
        )
        text = chunks[0].text
        assert "AnimEffect.Footstep.Walk" in text
        assert "Concrete" in text
        assert "Glass" in text
        assert "AnimEffect.Footstep.Land" in text

    def test_metadata_effects_by_tag(self):
        indexer = _make_indexer_with_mock(CONTEXT_EFFECTS_INSPECT)
        chunks = indexer._create_data_asset_chunks(
            "/Game/ContextEffects/CFX_DefaultSkin",
            Path("/fake/CFX_DefaultSkin.uasset"),
            "CFX_DefaultSkin",
        )
        effects = chunks[0].metadata["effects_by_tag"]
        assert "AnimEffect.Footstep.Walk" in effects
        assert "Concrete" in effects["AnimEffect.Footstep.Walk"]
        assert "Glass" in effects["AnimEffect.Footstep.Walk"]
        assert "AnimEffect.Footstep.Land" in effects


# ---------------------------------------------------------------------------
# Tests: RawExport fallback (TeamDA)
# ---------------------------------------------------------------------------


class TestRawExportFallback:
    def test_raw_export_produces_text(self):
        indexer = _make_indexer_with_mock(
            TEAM_DA_RAW_INSPECT, refs=["/Game/SomeTexture"]
        )
        chunks = indexer._create_data_asset_chunks(
            "/Game/System/Teams/TeamDA_Blue",
            Path("/fake/TeamDA_Blue.uasset"),
            "TeamDA_Blue",
        )
        assert len(chunks) == 1
        text = chunks[0].text
        assert "LyraTeamDisplayAsset" in text
        assert "raw data" in text

    def test_raw_export_uses_refs(self):
        indexer = _make_indexer_with_mock(
            TEAM_DA_RAW_INSPECT, refs=["/Game/SomeTexture"]
        )
        chunks = indexer._create_data_asset_chunks(
            "/Game/System/Teams/TeamDA_Blue",
            Path("/fake/TeamDA_Blue.uasset"),
            "TeamDA_Blue",
        )
        assert "/Game/SomeTexture" in chunks[0].references_out


# ---------------------------------------------------------------------------
# Tests: ExperienceDefinition CDO extraction
# ---------------------------------------------------------------------------


class TestExperienceDefinitionCDO:
    def test_cdo_action_sets_edges(self):
        indexer = _make_indexer_with_mock(EXPERIENCE_CDO_INSPECT)
        chunks = indexer._create_game_feature_chunks(
            "/ShooterCore/Experiences/B_ShooterGame_Elimination",
            Path("/fake/B_ShooterGame_Elimination.uasset"),
            "B_ShooterGame_Elimination",
            "LyraExperienceDefinition",
        )
        assert len(chunks) == 1
        typed = chunks[0].typed_references_out
        assert (
            typed.get("/ShooterCore/Experiences/LAS_ShooterGame_SharedInput")
            == "includes_action_set"
        )
        assert (
            typed.get("/ShooterCore/Experiences/LAS_ShooterGame_StandardHUD")
            == "includes_action_set"
        )
        assert (
            typed.get("/ShooterCore/Accolades/EAS_BasicShooterAcolades")
            == "includes_action_set"
        )

    def test_cdo_pawn_data_edge(self):
        indexer = _make_indexer_with_mock(EXPERIENCE_CDO_INSPECT)
        chunks = indexer._create_game_feature_chunks(
            "/ShooterCore/Experiences/B_ShooterGame_Elimination",
            Path("/fake/B_ShooterGame_Elimination.uasset"),
            "B_ShooterGame_Elimination",
            "LyraExperienceDefinition",
        )
        typed = chunks[0].typed_references_out
        assert typed.get("/ShooterCore/Game/HeroData_ShooterGame") == "uses_pawn_data"

    def test_cdo_text_includes_action_sets(self):
        indexer = _make_indexer_with_mock(EXPERIENCE_CDO_INSPECT)
        chunks = indexer._create_game_feature_chunks(
            "/ShooterCore/Experiences/B_ShooterGame_Elimination",
            Path("/fake/B_ShooterGame_Elimination.uasset"),
            "B_ShooterGame_Elimination",
            "LyraExperienceDefinition",
        )
        text = chunks[0].text
        assert "ActionSets:" in text
        assert "LAS_ShooterGame_SharedInput" in text
        assert "DefaultPawnData:" in text
        assert "HeroData_ShooterGame" in text

    def test_cdo_features_to_enable(self):
        indexer = _make_indexer_with_mock(EXPERIENCE_CDO_INSPECT)
        chunks = indexer._create_game_feature_chunks(
            "/ShooterCore/Experiences/B_ShooterGame_Elimination",
            Path("/fake/B_ShooterGame_Elimination.uasset"),
            "B_ShooterGame_Elimination",
            "LyraExperienceDefinition",
        )
        text = chunks[0].text
        assert "ShooterCore" in text

    def test_cdo_widget_actions_still_work(self):
        indexer = _make_indexer_with_mock(EXPERIENCE_CDO_INSPECT)
        chunks = indexer._create_game_feature_chunks(
            "/ShooterCore/Experiences/B_ShooterGame_Elimination",
            Path("/fake/B_ShooterGame_Elimination.uasset"),
            "B_ShooterGame_Elimination",
            "LyraExperienceDefinition",
        )
        typed = chunks[0].typed_references_out
        assert typed.get("/ShooterCore/UI/W_EliminationFeed") == "registers_widget"


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_properties(self):
        data = {
            "exports": [
                {
                    "name": "Test",
                    "type": "NormalExport",
                    "class": "SomeDataAsset",
                    "properties": [],
                },
                {
                    "name": "PackageMetaData",
                    "type": "MetaDataExport",
                    "class": "MetaData",
                    "properties": [],
                },
            ]
        }
        indexer = _make_indexer_with_mock(data)
        chunks = indexer._create_data_asset_chunks(
            "/Game/Test", Path("/fake/Test.uasset"), "Test"
        )
        assert len(chunks) == 1
        assert "SomeDataAsset" in chunks[0].text

    def test_malformed_json(self):
        indexer = _make_indexer_with_mock({"exports": []})
        indexer._run_parser = lambda cmd, path: "not json at all"
        indexer._get_asset_references = MagicMock(return_value=[])
        chunks = indexer._create_data_asset_chunks(
            "/Game/Broken", Path("/fake/Broken.uasset"), "Broken"
        )
        assert len(chunks) == 1
        assert chunks[0].type == "asset_summary"

    def test_no_parser_output(self):
        indexer = _make_indexer_with_mock({"exports": []})
        indexer._run_parser = lambda cmd, path: None
        indexer._get_asset_references = MagicMock(return_value=[])
        chunks = indexer._create_data_asset_chunks(
            "/Game/Missing", Path("/fake/Missing.uasset"), "Missing"
        )
        assert len(chunks) == 1

    def test_default_extractor_for_unknown_class(self):
        data = {
            "exports": [
                {
                    "name": "MyCustomAsset",
                    "type": "NormalExport",
                    "class": "CustomGameDataAsset",
                    "properties": [
                        {
                            "name": "SomeRef",
                            "type": "ObjectProperty",
                            "value": "/Game/Some/Asset",
                        },
                        {"name": "SomeValue", "type": "IntProperty", "value": 42},
                    ],
                },
                {
                    "name": "PackageMetaData",
                    "type": "MetaDataExport",
                    "class": "MetaData",
                    "properties": [],
                },
            ]
        }
        indexer = _make_indexer_with_mock(data)
        chunks = indexer._create_data_asset_chunks(
            "/Game/MyCustomAsset", Path("/fake/MyCustomAsset.uasset"), "MyCustomAsset"
        )
        assert len(chunks) == 1
        text = chunks[0].text
        assert "CustomGameDataAsset" in text
        assert "Properties:" in text
        assert "SomeRef" in text
        assert "/Game/Some/Asset" in chunks[0].references_out


# ---------------------------------------------------------------------------
# Tests: Extractor registry
# ---------------------------------------------------------------------------


class TestExtractorRegistry:
    def test_all_lyra_classes_registered(self):
        """Every class in lyra.json data_asset_extractors has a registered handler."""
        from UnrealAgent.knowledge_index.indexer import _EXTRACTOR_REGISTRY
        from UnrealAgent.project_profile import load_profile

        profile = load_profile("lyra")
        for cls in profile.data_asset_extractors:
            assert cls in _EXTRACTOR_REGISTRY, (
                f"{cls} listed in lyra.json but has no @data_asset_extractor"
            )

    def test_registry_method_names_valid(self):
        """All registered method names exist on AssetIndexer."""
        from UnrealAgent.knowledge_index.indexer import _EXTRACTOR_REGISTRY

        for cls, method_name in _EXTRACTOR_REGISTRY.items():
            assert hasattr(AssetIndexer, method_name), (
                f"Registry points {cls} -> {method_name} but method not found"
            )

    def test_apply_profile_builds_dispatch(self):
        """_apply_profile should populate _data_asset_extractors from registry."""

        indexer = _make_indexer_with_mock(ABILITY_SET_INSPECT, profile_name="lyra")
        assert "LyraAbilitySet" in indexer._data_asset_extractors
        assert callable(indexer._data_asset_extractors["LyraAbilitySet"])


# ---------------------------------------------------------------------------
# Tests: Generic mode (defaults-only profile)
# ---------------------------------------------------------------------------


class TestGenericMode:
    def test_defaults_profile_has_engine_extractors(self):
        """With _defaults profile, only engine-level extractors are loaded."""
        indexer = _make_indexer_with_mock(ABILITY_SET_INSPECT, profile_name="_defaults")
        assert len(indexer._data_asset_extractors) == 1
        assert "GameplayEffect" in indexer._data_asset_extractors

    def test_defaults_profile_uses_default_extractor(self):
        """Unknown class falls through to default extractor with any profile."""
        data = {
            "exports": [
                {
                    "name": "Test",
                    "type": "NormalExport",
                    "class": "SomeUnknownClass",
                    "properties": [
                        {"name": "Val", "type": "IntProperty", "value": 1},
                    ],
                },
                {
                    "name": "PackageMetaData",
                    "type": "MetaDataExport",
                    "class": "MetaData",
                    "properties": [],
                },
            ]
        }
        indexer = _make_indexer_with_mock(data, profile_name="_defaults")
        chunks = indexer._create_data_asset_chunks(
            "/Game/Test", Path("/fake/Test.uasset"), "Test"
        )
        assert len(chunks) == 1
        assert "SomeUnknownClass" in chunks[0].text

    def test_defaults_semantic_types_are_engine_only(self):
        """Defaults profile should only have base engine semantic types."""
        indexer = _make_indexer_with_mock({"exports": []}, profile_name="_defaults")
        assert "WidgetBlueprint" in indexer.SEMANTIC_TYPES
        assert "LyraExperienceActionSet" not in indexer.SEMANTIC_TYPES

    def test_lyra_semantic_types_include_lyra(self):
        """Lyra profile adds project-specific semantic types."""
        indexer = _make_indexer_with_mock({"exports": []}, profile_name="lyra")
        assert "LyraExperienceActionSet" in indexer.SEMANTIC_TYPES
        assert "LyraExperienceDefinition" in indexer.SEMANTIC_TYPES
