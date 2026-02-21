"""Tests for inherits_from edge emission and inheritance queries."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from UnrealAgent.knowledge_index.indexer import AssetIndexer
from UnrealAgent.knowledge_index.store import KnowledgeStore
from UnrealAgent.knowledge_index.schemas import DocChunk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_store(tmp_path):
    """Create a temporary KnowledgeStore for testing."""
    db_path = tmp_path / "test.db"
    return KnowledgeStore(db_path, use_vector_search=False)


def _make_indexer(store, profile_name="lyra"):
    """Create an AssetIndexer with mocked parser for testing."""
    from UnrealAgent.project_profile import load_profile

    indexer = AssetIndexer.__new__(AssetIndexer)
    indexer.store = store
    indexer.parser_path = Path("/fake/AssetParser")
    indexer.embed_fn = None
    indexer.embed_model = None
    indexer.embed_version = None
    indexer.force = False
    indexer.plugin_paths = {}

    profile = load_profile(profile_name)
    indexer._apply_profile(profile)
    return indexer


def _make_indexer_with_mock(
    inspect_data: dict,
    refs: list[str] | None = None,
    profile_name: str = "lyra",
    store=None,
):
    """Create an AssetIndexer with mocked parser for DataAsset testing."""
    from UnrealAgent.project_profile import load_profile

    indexer = AssetIndexer.__new__(AssetIndexer)
    indexer.store = store or MagicMock()
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
# Tests: _resolve_parent_to_edge_target
# ---------------------------------------------------------------------------


class TestResolveParentToEdgeTarget:
    def test_empty_parent_returns_none(self, tmp_store):
        indexer = _make_indexer(tmp_store)
        assert indexer._resolve_parent_to_edge_target("") is None
        assert indexer._resolve_parent_to_edge_target("Unknown") is None
        assert indexer._resolve_parent_to_edge_target("None") is None

    def test_engine_class_returns_class_prefix(self, tmp_store):
        """Known engine classes should return class:<name> directly."""
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("GameplayEffect")
        assert result == "class:GameplayEffect"

    def test_engine_class_actor(self, tmp_store):
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("Actor")
        assert result == "class:Actor"

    def test_resolved_to_semantic_doc(self, tmp_store):
        """Parent resolved via docs table → asset:<path>."""
        # Insert a doc that can be found
        doc = DocChunk(
            doc_id="asset:/Game/BP/BP_ParentClass",
            type="asset_summary",
            path="/Game/BP/BP_ParentClass",
            name="BP_ParentClass",
            text="A parent blueprint",
            asset_type="Blueprint",
        )
        tmp_store.upsert_doc(doc, force=True)

        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("BP_ParentClass")
        assert result == "asset:/Game/BP/BP_ParentClass"

    def test_resolved_to_lightweight_asset(self, tmp_store):
        """Parent resolved via lightweight_assets table → asset:<path>."""
        tmp_store.upsert_lightweight_asset(
            path="/Game/BP/BP_LightweightParent",
            name="BP_LightweightParent",
            asset_type="Blueprint",
            references=[],
        )

        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("BP_LightweightParent")
        assert result == "asset:/Game/BP/BP_LightweightParent"

    def test_unresolved_returns_class_fallback(self, tmp_store):
        """Unresolved parent → class:<name> fallback."""
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("SomeCustomParentClass")
        assert result == "class:SomeCustomParentClass"

    def test_strips_c_suffix(self, tmp_store):
        """Blueprint _C suffix should be stripped."""
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("MyBlueprint_C")
        assert result == "class:MyBlueprint"

    def test_game_path_used_directly(self, tmp_store):
        """Explicit /Game/ path should be used as-is, not resolved by name."""
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target(
            "/Game/GE/Damage/GE_DamageParent"
        )
        assert result == "asset:/Game/GE/Damage/GE_DamageParent"

    def test_game_path_not_ambiguous(self, tmp_store):
        """/Game/ path must not fall through to a name-based DB lookup."""
        # Insert two assets with the same name in different folders
        tmp_store.upsert_lightweight_asset(
            path="/Game/FolderA/BP_Shared",
            name="BP_Shared",
            asset_type="Blueprint",
            references=[],
        )
        tmp_store.upsert_lightweight_asset(
            path="/Game/FolderB/BP_Shared",
            name="BP_Shared",
            asset_type="Blueprint",
            references=[],
        )
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("/Game/FolderB/BP_Shared")
        assert result == "asset:/Game/FolderB/BP_Shared"

    def test_game_path_strips_c_suffix(self, tmp_store):
        """/Game/ path with _C suffix should be cleaned."""
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("/Game/GE/GE_Parent_C")
        assert result == "asset:/Game/GE/GE_Parent"

    def test_script_module_class_returns_class_prefix(self, tmp_store):
        """/Script/Module.Class should extract the class name, not the module."""
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target(
            "/Script/GameplayAbilities.GameplayEffect"
        )
        assert result == "class:GameplayEffect"

    def test_script_engine_actor_returns_class_prefix(self, tmp_store):
        """/Script/Engine.Actor should resolve to class:Actor."""
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("/Script/Engine.Actor")
        assert result == "class:Actor"

    def test_script_module_without_dot(self, tmp_store):
        """/Script/Engine → bare module name used as class fallback."""
        indexer = _make_indexer(tmp_store)
        result = indexer._resolve_parent_to_edge_target("/Script/Engine")
        assert result == "class:Engine"


# ---------------------------------------------------------------------------
# Tests: Blueprint inherits_from edge emission
# ---------------------------------------------------------------------------

_BLUEPRINT_XML = """<blueprint>
  <parent>BP_ParentClass</parent>
  <components></components>
  <events></events>
  <variables></variables>
  <interfaces></interfaces>
</blueprint>"""


class TestBlueprintInheritsFrom:
    def test_blueprint_emits_inherits_from_edge(self, tmp_store):
        """Blueprint with parent → typed_references_out has inherits_from."""
        indexer = _make_indexer(tmp_store)

        def mock_run_parser(command, fs_path):
            if command == "blueprint":
                return _BLUEPRINT_XML
            if command == "references":
                return "<asset-analysis><asset-refs></asset-refs></asset-analysis>"
            return None

        indexer._run_parser = mock_run_parser
        indexer._get_asset_references = MagicMock(return_value=[])

        chunks = indexer._create_blueprint_chunks(
            "/Game/BP/BP_Child", Path("/fake/BP_Child.uasset"), "BP_Child"
        )
        summary = chunks[0]

        # The parent should produce an inherits_from edge
        inherits_refs = {
            k: v
            for k, v in summary.typed_references_out.items()
            if v == "inherits_from"
        }
        assert len(inherits_refs) > 0
        # Should target class:BP_ParentClass (unresolved)
        target = list(inherits_refs.keys())[0]
        assert "BP_ParentClass" in target

    def test_batch_blueprint_emits_inherits_from_edge(self, tmp_store):
        """Batch blueprint path also emits inherits_from edges."""
        indexer = _make_indexer(tmp_store)
        data = {
            "parent": "BP_ParentClass",
            "events": [],
            "components": [],
            "variables": [],
            "interfaces": [],
            "functions": [],
        }
        chunks = indexer._chunks_from_blueprint_json(
            data, "/Game/BP/BP_Child", "BP_Child", []
        )
        summary = chunks[0]

        inherits_refs = {
            k: v
            for k, v in summary.typed_references_out.items()
            if v == "inherits_from"
        }
        assert len(inherits_refs) > 0
        target = list(inherits_refs.keys())[0]
        assert "BP_ParentClass" in target

    def test_batch_blueprint_no_parent_no_edge(self, tmp_store):
        """Batch blueprint with no parent emits no inherits_from edge."""
        indexer = _make_indexer(tmp_store)
        data = {
            "parent": "",
            "events": [],
            "components": [],
            "variables": [],
            "interfaces": [],
            "functions": [],
        }
        chunks = indexer._chunks_from_blueprint_json(
            data, "/Game/BP/BP_Root", "BP_Root", []
        )
        summary = chunks[0]
        inherits_refs = {
            k: v
            for k, v in summary.typed_references_out.items()
            if v == "inherits_from"
        }
        assert len(inherits_refs) == 0


# ---------------------------------------------------------------------------
# Tests: find_children_of (store)
# ---------------------------------------------------------------------------


class TestFindChildrenOf:
    def _setup_inheritance_tree(self, store):
        """Set up: GE_Base → GE_Damage, GE_Damage → GE_Damage_Pistol."""
        # GE_Base (root)
        doc_base = DocChunk(
            doc_id="asset:/Game/GE/GE_Base",
            type="asset_summary",
            path="/Game/GE/GE_Base",
            name="GE_Base",
            text="Base GE",
            asset_type="GameplayEffect",
        )
        store.upsert_doc(doc_base, force=True)

        # GE_Damage inherits from GE_Base
        doc_damage = DocChunk(
            doc_id="asset:/Game/GE/GE_Damage",
            type="asset_summary",
            path="/Game/GE/GE_Damage",
            name="GE_Damage",
            text="Damage GE",
            references_out=["asset:/Game/GE/GE_Base"],
            typed_references_out={"asset:/Game/GE/GE_Base": "inherits_from"},
            asset_type="GameplayEffect",
        )
        store.upsert_doc(doc_damage, force=True)

        # GE_Damage_Pistol inherits from GE_Damage
        doc_pistol = DocChunk(
            doc_id="asset:/Game/GE/GE_Damage_Pistol",
            type="asset_summary",
            path="/Game/GE/GE_Damage_Pistol",
            name="GE_Damage_Pistol",
            text="Pistol damage GE",
            references_out=["asset:/Game/GE/GE_Damage"],
            typed_references_out={"asset:/Game/GE/GE_Damage": "inherits_from"},
            asset_type="GameplayEffect",
        )
        store.upsert_doc(doc_pistol, force=True)

        # GE_Heal inherits from class:GameplayEffect (unresolved engine class)
        doc_heal = DocChunk(
            doc_id="asset:/Game/GE/GE_Heal",
            type="asset_summary",
            path="/Game/GE/GE_Heal",
            name="GE_Heal",
            text="Heal GE",
            references_out=["class:GameplayEffect"],
            typed_references_out={"class:GameplayEffect": "inherits_from"},
            asset_type="GameplayEffect",
        )
        store.upsert_doc(doc_heal, force=True)

    def test_direct_children(self, tmp_store):
        """find_children_of returns direct children."""
        self._setup_inheritance_tree(tmp_store)
        children = tmp_store.find_children_of(["asset:/Game/GE/GE_Base"])
        names = {c["name"] for c in children}
        assert "GE_Damage" in names

    def test_transitive_descendants(self, tmp_store):
        """find_children_of returns transitive descendants."""
        self._setup_inheritance_tree(tmp_store)
        children = tmp_store.find_children_of(["asset:/Game/GE/GE_Base"], max_depth=4)
        names = {c["name"] for c in children}
        assert "GE_Damage" in names
        assert "GE_Damage_Pistol" in names

    def test_depth_annotation(self, tmp_store):
        """Depth is correctly annotated on results."""
        self._setup_inheritance_tree(tmp_store)
        children = tmp_store.find_children_of(["asset:/Game/GE/GE_Base"], max_depth=4)
        by_name = {c["name"]: c for c in children}
        assert by_name["GE_Damage"]["depth"] == 1
        assert by_name["GE_Damage_Pistol"]["depth"] == 2

    def test_class_prefix_targets(self, tmp_store):
        """find_children_of with class: targets finds unresolved parents."""
        self._setup_inheritance_tree(tmp_store)
        children = tmp_store.find_children_of(["class:GameplayEffect"])
        names = {c["name"] for c in children}
        assert "GE_Heal" in names

    def test_empty_parent_ids(self, tmp_store):
        """Empty parent_ids returns empty list."""
        assert tmp_store.find_children_of([]) == []


# ---------------------------------------------------------------------------
# Tests: _normalize_ue_path (MCP server)
# ---------------------------------------------------------------------------


class TestNormalizeUePath:
    def test_object_style_path(self):
        from UnrealAgent.mcp_server import _normalize_ue_path

        assert _normalize_ue_path("/Game/GE/GE_Base.GE_Base_C") == "/Game/GE/GE_Base"

    def test_path_with_c_suffix_only(self):
        from UnrealAgent.mcp_server import _normalize_ue_path

        assert _normalize_ue_path("/Game/GE/GE_Base_C") == "/Game/GE/GE_Base"

    def test_clean_path_unchanged(self):
        from UnrealAgent.mcp_server import _normalize_ue_path

        assert _normalize_ue_path("/Game/GE/GE_Base") == "/Game/GE/GE_Base"

    def test_non_game_mount_path(self):
        from UnrealAgent.mcp_server import _normalize_ue_path

        assert (
            _normalize_ue_path("/ShooterCore/GE/GE_Damage.GE_Damage_C")
            == "/ShooterCore/GE/GE_Damage"
        )

    def test_non_path_string_unchanged(self):
        from UnrealAgent.mcp_server import _normalize_ue_path

        assert _normalize_ue_path("GameplayEffect") == "GameplayEffect"

    def test_empty_string(self):
        from UnrealAgent.mcp_server import _normalize_ue_path

        assert _normalize_ue_path("") == ""

    def test_script_path_preserved(self):
        """Dot in /Script/ paths is module.class separator — must not be stripped."""
        from UnrealAgent.mcp_server import _normalize_ue_path

        assert (
            _normalize_ue_path("/Script/GameplayAbilities.GameplayEffect")
            == "/Script/GameplayAbilities.GameplayEffect"
        )


# ---------------------------------------------------------------------------
# Tests: inherits query prefix handling (integration)
# ---------------------------------------------------------------------------


class TestInheritsQueryPrefixHandling:
    """Regression: class: prefix on query should not double up to class:class:..."""

    def _setup_inheritance_tree(self, store):
        """Insert GE_Heal inheriting from class:GameplayEffect."""
        doc_heal = DocChunk(
            doc_id="asset:/Game/GE/GE_Heal",
            type="asset_summary",
            path="/Game/GE/GE_Heal",
            name="GE_Heal",
            text="Heal GE",
            references_out=["class:GameplayEffect"],
            typed_references_out={"class:GameplayEffect": "inherits_from"},
            asset_type="GameplayEffect",
        )
        store.upsert_doc(doc_heal, force=True)

    def test_class_prefix_input_finds_children(self, tmp_store):
        """find_children_of with double-prefixed ID returns nothing (proves bug path)."""
        self._setup_inheritance_tree(tmp_store)
        # Double prefix should find nothing
        bad = tmp_store.find_children_of(["class:class:GameplayEffect"])
        assert len(bad) == 0
        # Correct prefix should find GE_Heal
        good = tmp_store.find_children_of(["class:GameplayEffect"])
        names = {c["name"] for c in good}
        assert "GE_Heal" in names

    def test_script_path_bare_name_extracts_class(self, tmp_store):
        """Regression: /Script/Module.Class in inherits mode should use Class, not Module."""
        self._setup_inheritance_tree(tmp_store)
        # class:GameplayAbilities (module name) should find nothing
        bad = tmp_store.find_children_of(["class:GameplayAbilities"])
        assert len(bad) == 0
        # class:GameplayEffect (class name) should find GE_Heal
        good = tmp_store.find_children_of(["class:GameplayEffect"])
        names = {c["name"] for c in good}
        assert "GE_Heal" in names

    def test_unreal_search_normalizes_embedded_class_prefix(self, tmp_store, monkeypatch):
        """Natural-language inherits query with class: token should find children."""
        from UnrealAgent import mcp_server

        self._setup_inheritance_tree(tmp_store)
        monkeypatch.setattr(mcp_server, "_get_store", lambda: tmp_store)

        result = mcp_server.unreal_search(
            "inherits from class:GameplayEffect", search_type="inherits", limit=10
        )

        names = {r["name"] for r in result["results"]}
        assert "GE_Heal" in names

    def test_unreal_search_script_target_uses_class_segment(self, tmp_store, monkeypatch):
        """Inherits query with /Script/Module.Class should use the class segment."""
        from UnrealAgent import mcp_server

        self._setup_inheritance_tree(tmp_store)
        monkeypatch.setattr(mcp_server, "_get_store", lambda: tmp_store)

        result = mcp_server.unreal_search(
            "children of /Script/GameplayAbilities.GameplayEffect",
            search_type="inherits",
            limit=10,
        )

        names = {r["name"] for r in result["results"]}
        assert "GE_Heal" in names
