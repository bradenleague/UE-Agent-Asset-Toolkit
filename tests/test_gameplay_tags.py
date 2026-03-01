"""Tests for P3: GameplayTag extraction across all property types."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from unreal_agent.knowledge_index.indexer import (
    AssetIndexer,
    _extract_gameplay_tags_from_data,
    _get_tag_name,
)
from unreal_agent.knowledge_index.schemas import DocChunk
from unreal_agent.knowledge_index.store import KnowledgeStore


# =========================================================================
# TestGetTagName
# =========================================================================


class TestGetTagName:
    def test_valid_tag_dict(self):
        data = {
            "InputTag": {"_type": "GameplayTag", "TagName": "InputTag.Ability.Dash"}
        }
        assert _get_tag_name(data, "InputTag") == "InputTag.Ability.Dash"

    def test_none_tag_returns_empty(self):
        data = {"InputTag": {"_type": "GameplayTag", "TagName": "None"}}
        assert _get_tag_name(data, "InputTag") == ""

    def test_missing_key_returns_empty(self):
        assert _get_tag_name({}, "InputTag") == ""

    def test_non_dict_value_returns_empty(self):
        data = {"InputTag": "some_string"}
        assert _get_tag_name(data, "InputTag") == ""


# =========================================================================
# TestTagWalker
# =========================================================================


class TestTagWalker:
    def test_single_gameplay_tag(self):
        data = {"_type": "GameplayTag", "TagName": "InputTag.Ability.Dash"}
        assert _extract_gameplay_tags_from_data(data) == ["InputTag.Ability.Dash"]

    def test_gameplay_tag_container_new_format(self):
        data = {
            "_type": "GameplayTagContainer",
            "tags": ["SurfaceType.Concrete", "SurfaceType.Metal"],
        }
        result = _extract_gameplay_tags_from_data(data)
        assert result == ["SurfaceType.Concrete", "SurfaceType.Metal"]

    def test_nested_in_array(self):
        data = [
            {
                "name": "GrantedAbilities",
                "value": [
                    {
                        "InputTag": {
                            "_type": "GameplayTag",
                            "TagName": "InputTag.Ability.Dash",
                        }
                    },
                    {
                        "InputTag": {
                            "_type": "GameplayTag",
                            "TagName": "InputTag.Ability.Jump",
                        }
                    },
                ],
            }
        ]
        result = _extract_gameplay_tags_from_data(data)
        assert "InputTag.Ability.Dash" in result
        assert "InputTag.Ability.Jump" in result

    def test_deeply_nested(self):
        data = {"a": {"b": {"c": {"_type": "GameplayTag", "TagName": "Deep.Tag"}}}}
        assert _extract_gameplay_tags_from_data(data) == ["Deep.Tag"]

    def test_none_and_empty_filtered(self):
        data = [
            {"_type": "GameplayTag", "TagName": "None"},
            {"_type": "GameplayTag", "TagName": ""},
            {"_type": "GameplayTag", "TagName": "Valid.Tag"},
        ]
        assert _extract_gameplay_tags_from_data(data) == ["Valid.Tag"]

    def test_deduplication(self):
        data = [
            {"_type": "GameplayTag", "TagName": "Tag.A"},
            {"_type": "GameplayTag", "TagName": "Tag.A"},
            {"_type": "GameplayTag", "TagName": "Tag.B"},
        ]
        result = _extract_gameplay_tags_from_data(data)
        assert result == ["Tag.A", "Tag.B"]

    def test_mixed_types_ignored(self):
        data = [
            42,
            True,
            None,
            "just a string",
            {"_type": "GameplayTag", "TagName": "Found.Tag"},
        ]
        assert _extract_gameplay_tags_from_data(data) == ["Found.Tag"]

    def test_depth_limit(self):
        # Build a deeply nested structure exceeding depth 10
        data = {"_type": "GameplayTag", "TagName": "Deep.Tag"}
        for _ in range(15):
            data = {"nested": data}
        # Tag is at depth 15, beyond limit of 10 — should not be found
        assert _extract_gameplay_tags_from_data(data) == []

    def test_tag_container_filters_none(self):
        data = {
            "_type": "GameplayTagContainer",
            "tags": ["Valid.Tag", "None", "", "Another.Tag"],
        }
        result = _extract_gameplay_tags_from_data(data)
        assert result == ["Another.Tag", "Valid.Tag"]

    def test_nested_tag_container(self):
        """Real parser output: Context wraps another GameplayTagContainer."""
        data = {
            "_type": "GameplayTagContainer",
            "Context": {
                "_type": "GameplayTagContainer",
                "tags": ["SurfaceType.Concrete"],
            },
        }
        result = _extract_gameplay_tags_from_data(data)
        assert "SurfaceType.Concrete" in result


# =========================================================================
# TestCentralizedExtraction (via indexer)
# =========================================================================


# Minimal fixtures for testing centralized extraction

ABILITY_SET_INSPECT = {
    "exports": [
        {
            "name": "AbilitySet_Test",
            "type": "NormalExport",
            "class": "LyraAbilitySet",
            "properties": [
                {
                    "name": "GrantedGameplayAbilities",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "_type": "LyraAbilitySet_GameplayAbility",
                            "Ability": "/Game/GA_Dash",
                            "InputTag": {
                                "_type": "GameplayTag",
                                "TagName": "InputTag.Ability.Dash",
                            },
                        }
                    ],
                }
            ],
        }
    ]
}

INPUT_CONFIG_INSPECT = {
    "exports": [
        {
            "name": "InputConfig_Test",
            "type": "NormalExport",
            "class": "LyraInputConfig",
            "properties": [
                {
                    "name": "NativeInputActions",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "InputAction": "/Game/IA_Move",
                            "InputTag": {
                                "_type": "GameplayTag",
                                "TagName": "InputTag.Move",
                            },
                        }
                    ],
                },
                {
                    "name": "AbilityInputActions",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "InputAction": "/Game/IA_Jump",
                            "InputTag": {
                                "_type": "GameplayTag",
                                "TagName": "InputTag.Ability.Jump",
                            },
                        }
                    ],
                },
            ],
        }
    ]
}

CONTEXT_EFFECTS_INSPECT = {
    "exports": [
        {
            "name": "CFX_Test",
            "type": "NormalExport",
            "class": "LyraContextEffectsLibrary",
            "properties": [
                {
                    "name": "ContextEffects",
                    "type": "ArrayProperty",
                    "value": [
                        {
                            "EffectTag": {
                                "_type": "GameplayTag",
                                "TagName": "Effect.Footstep",
                            },
                            "Context": {
                                "_type": "GameplayTagContainer",
                                "tags": ["SurfaceType.Concrete"],
                            },
                        }
                    ],
                }
            ],
        }
    ]
}

DEFAULT_ASSET_INSPECT = {
    "exports": [
        {
            "name": "DA_Unknown",
            "type": "NormalExport",
            "class": "SomeUnknownAsset",
            "properties": [
                {
                    "name": "SomeTag",
                    "type": "StructProperty",
                    "value": {
                        "_type": "GameplayTag",
                        "TagName": "Custom.Tag.Found",
                    },
                }
            ],
        }
    ]
}


def _make_indexer():
    """Create a minimal AssetIndexer with mocked parser."""
    indexer = AssetIndexer.__new__(AssetIndexer)
    indexer.store = MagicMock()
    indexer.parser_path = Path("/fake/parser")
    indexer.content_root = Path("/fake/content")
    indexer.SEMANTIC_TYPES = set()
    indexer.BATCH_SKIP_TYPES = set()
    indexer.SKIP_REFS_TYPES = set()
    indexer.game_feature_types = set()
    indexer.input_action_dirs = []
    indexer.imc_dirs = []
    indexer.data_asset_types = set()
    # Build extractor dispatch table from registry
    from unreal_agent.knowledge_index.indexer import _EXTRACTOR_REGISTRY

    indexer._data_asset_extractors = {}
    for cls_name, method_name in _EXTRACTOR_REGISTRY.items():
        fn = getattr(indexer, method_name, None)
        if fn:
            indexer._data_asset_extractors[cls_name] = fn
    return indexer


class TestCentralizedExtraction:
    def _run_data_asset(self, inspect_data: dict) -> DocChunk:
        indexer = _make_indexer()
        indexer._run_parser = MagicMock(return_value=json.dumps(inspect_data))
        chunks = indexer._create_data_asset_chunks(
            "/Game/Test/Asset", Path("/fake/Asset.uasset"), "Asset"
        )
        assert len(chunks) == 1
        return chunks[0]

    def test_ability_set_gets_gameplay_tags(self):
        chunk = self._run_data_asset(ABILITY_SET_INSPECT)
        assert "InputTag.Ability.Dash" in chunk.metadata.get("gameplay_tags", [])

    def test_input_config_gets_gameplay_tags(self):
        chunk = self._run_data_asset(INPUT_CONFIG_INSPECT)
        tags = chunk.metadata.get("gameplay_tags", [])
        assert "InputTag.Move" in tags
        assert "InputTag.Ability.Jump" in tags

    def test_context_effects_gets_gameplay_tags(self):
        chunk = self._run_data_asset(CONTEXT_EFFECTS_INSPECT)
        tags = chunk.metadata.get("gameplay_tags", [])
        assert "Effect.Footstep" in tags
        assert "SurfaceType.Concrete" in tags

    def test_default_extractor_gets_gameplay_tags(self):
        chunk = self._run_data_asset(DEFAULT_ASSET_INSPECT)
        tags = chunk.metadata.get("gameplay_tags", [])
        assert "Custom.Tag.Found" in tags

    def test_tags_in_text_for_fts(self):
        chunk = self._run_data_asset(DEFAULT_ASSET_INSPECT)
        assert "Tags:" in chunk.text
        assert "Custom.Tag.Found" in chunk.text

    def test_extractor_tags_preserved_on_merge(self):
        """When an extractor sets gameplay_tags, the walker shouldn't remove them."""
        chunk = self._run_data_asset(ABILITY_SET_INSPECT)
        tags = chunk.metadata.get("gameplay_tags", [])
        # The InputTag from the extractor should still be there
        assert "InputTag.Ability.Dash" in tags


# =========================================================================
# TestAssetTagsStore
# =========================================================================


class TestAssetTagsStore:
    @pytest.fixture
    def store(self, tmp_path):
        db_path = tmp_path / "test.db"
        return KnowledgeStore(db_path, use_vector_search=False)

    def test_upsert_and_exact_search(self, store):
        store.upsert_asset_tags("/Game/Test/A", ["InputTag.Ability.Dash", "Tag.Other"])
        results = store.search_by_tag("InputTag.Ability.Dash")
        assert len(results) == 1
        assert results[0]["path"] == "/Game/Test/A"
        assert results[0]["tag"] == "InputTag.Ability.Dash"

    def test_prefix_wildcard(self, store):
        store.upsert_asset_tags("/Game/A", ["InputTag.Ability.Dash"])
        store.upsert_asset_tags("/Game/B", ["InputTag.Ability.Jump"])
        store.upsert_asset_tags("/Game/C", ["Effect.Footstep"])
        results = store.search_by_tag("InputTag.*")
        assert len(results) == 2
        paths = {r["path"] for r in results}
        assert paths == {"/Game/A", "/Game/B"}

    def test_empty_result(self, store):
        results = store.search_by_tag("Nonexistent.Tag")
        assert results == []

    def test_replace_on_reupsert(self, store):
        store.upsert_asset_tags("/Game/A", ["Tag.Old"])
        results = store.search_by_tag("Tag.Old")
        assert len(results) == 1

        store.upsert_asset_tags("/Game/A", ["Tag.New"])
        assert store.search_by_tag("Tag.Old") == []
        results = store.search_by_tag("Tag.New")
        assert len(results) == 1

    def test_integration_upsert_doc_populates_tags(self, store):
        """DocChunk with gameplay_tags in metadata → upsert_doc → searchable."""
        doc = DocChunk(
            doc_id="asset:/Game/Test/TagAsset",
            type="asset_summary",
            path="/Game/Test/TagAsset",
            name="TagAsset",
            text="Test asset with tags",
            metadata={"gameplay_tags": ["InputTag.Ability.Dash", "Effect.Hit"]},
            references_out=[],
        )
        store.upsert_doc(doc, force=True)
        results = store.search_by_tag("InputTag.Ability.Dash")
        assert len(results) == 1
        assert results[0]["path"] == "/Game/Test/TagAsset"

    def test_get_tag_stats(self, store):
        store.upsert_asset_tags("/Game/A", ["InputTag.Ability.Dash"])
        store.upsert_asset_tags("/Game/B", ["InputTag.Ability.Dash", "Effect.Hit"])
        stats = store.get_tag_stats()
        tag_map = {s["tag"]: s["asset_count"] for s in stats}
        assert tag_map["InputTag.Ability.Dash"] == 2
        assert tag_map["Effect.Hit"] == 1


# =========================================================================
# TestTagAutoDetect
# =========================================================================


class TestTagAutoDetect:
    """Test _should_try_tag_search regex."""

    def test_dotted_pascal_case_matches(self):
        from unreal_agent.search.trace import should_try_tag_search as _should_try_tag_search

        assert _should_try_tag_search("InputTag.Ability.Dash") is True

    def test_bp_prefix_does_not_match(self):
        from unreal_agent.search.trace import should_try_tag_search as _should_try_tag_search

        assert _should_try_tag_search("BP_PlayerCharacter") is False

    def test_tag_prefix_always_matches(self):
        from unreal_agent.search.trace import should_try_tag_search as _should_try_tag_search

        assert _should_try_tag_search("tag:Footstep") is True

    def test_single_word_does_not_match(self):
        from unreal_agent.search.trace import should_try_tag_search as _should_try_tag_search

        assert _should_try_tag_search("player") is False

    def test_wildcard_suffix_matches(self):
        from unreal_agent.search.trace import should_try_tag_search as _should_try_tag_search

        assert _should_try_tag_search("InputTag.Ability.*") is True

    def test_lowercase_does_not_match(self):
        from unreal_agent.search.trace import should_try_tag_search as _should_try_tag_search

        assert _should_try_tag_search("input.tag.ability") is False
