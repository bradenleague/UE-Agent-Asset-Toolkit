"""Tests for C++ source bridge: _probe_class_name, resolve_cpp_sources, XML enrichment, search fallback."""

from unittest.mock import patch, MagicMock

import pytest

from UnrealAgent.knowledge_index.store import KnowledgeStore


# ---------------------------------------------------------------------------
# _probe_class_name
# ---------------------------------------------------------------------------


class TestProbeClassName:
    def test_bare_name_generates_prefixed_variants(self):
        """Bare name like LyraHealthComponent should produce U/A/F/... variants."""
        result = KnowledgeStore._probe_class_name("LyraHealthComponent")
        assert result[0] == "LyraHealthComponent"
        assert "ULyraHealthComponent" in result
        assert "ALyraHealthComponent" in result
        assert "FLyraHealthComponent" in result
        assert len(result) == 8  # bare + 7 prefixes

    def test_already_prefixed_returns_single(self):
        """Name like ULyraHealthComponent (prefix + uppercase) should not get extra prefixes."""
        result = KnowledgeStore._probe_class_name("ULyraHealthComponent")
        assert result == ["ULyraHealthComponent"]

    def test_bare_name_starting_with_prefix_letter(self):
        """Bare names like Actor, EnhancedInputComponent should still get prefixed variants.
        The heuristic checks that the second char is uppercase for already-prefixed."""
        result = KnowledgeStore._probe_class_name("Actor")
        assert "AActor" in result
        assert "UActor" in result

        result2 = KnowledgeStore._probe_class_name("EnhancedInputComponent")
        assert "UEnhancedInputComponent" in result2

        result3 = KnowledgeStore._probe_class_name("SkeletalMeshComponent")
        assert "USkeletalMeshComponent" in result3

    def test_strips_C_suffix(self):
        """_C suffix (Blueprint generated class) should be stripped first."""
        result = KnowledgeStore._probe_class_name("LyraCharacter_C")
        assert result[0] == "LyraCharacter"
        assert "ULyraCharacter" in result
        assert "ALyraCharacter" in result

    def test_prefixed_with_C_suffix(self):
        """Already-prefixed name with _C suffix."""
        result = KnowledgeStore._probe_class_name("AActor_C")
        assert result == ["AActor"]

    def test_empty_string(self):
        result = KnowledgeStore._probe_class_name("")
        assert result == [""]


# ---------------------------------------------------------------------------
# resolve_cpp_sources (batch) / resolve_cpp_source (single)
# ---------------------------------------------------------------------------


@pytest.fixture
def store_with_cpp(tmp_path):
    """Create a KnowledgeStore with cpp_class_index entries."""
    db = tmp_path / "test.db"
    store = KnowledgeStore(str(db), use_vector_search=False)
    store.upsert_cpp_classes_batch(
        [
            (
                "ULyraHealthComponent",
                "doc_health",
                "Source/LyraGame/Character/LyraHealthComponent.h",
            ),
            (
                "ULyraHeroComponent",
                "doc_hero",
                "Source/LyraGame/Character/LyraHeroComponent.h",
            ),
            ("ALyraCharacter", "doc_char", "Source/LyraGame/Character/LyraCharacter.h"),
        ]
    )
    return store


class TestResolveCppSources:
    def test_batch_resolve_with_prefix_probing(self, store_with_cpp):
        """Bare names should resolve via U/A prefix probing."""
        result = store_with_cpp.resolve_cpp_sources(
            [
                "LyraHealthComponent",
                "LyraHeroComponent",
                "LyraCharacter",
            ]
        )
        assert "LyraHealthComponent" in result
        assert result["LyraHealthComponent"]["class_name"] == "ULyraHealthComponent"
        assert (
            result["LyraHealthComponent"]["source_path"]
            == "Source/LyraGame/Character/LyraHealthComponent.h"
        )

        assert "LyraHeroComponent" in result
        assert "LyraCharacter" in result
        assert result["LyraCharacter"]["class_name"] == "ALyraCharacter"

    def test_batch_resolve_already_prefixed(self, store_with_cpp):
        """Already-prefixed names should resolve directly."""
        result = store_with_cpp.resolve_cpp_sources(["ULyraHealthComponent"])
        assert "ULyraHealthComponent" in result
        assert result["ULyraHealthComponent"]["doc_id"] == "doc_health"

    def test_resolve_missing_returns_empty(self, store_with_cpp):
        """Non-existent class should not appear in results."""
        result = store_with_cpp.resolve_cpp_sources(["NonExistentClass"])
        assert result == {}

    def test_resolve_single_convenience(self, store_with_cpp):
        """resolve_cpp_source() wraps the batch version for single lookups."""
        info = store_with_cpp.resolve_cpp_source("LyraHealthComponent")
        assert info is not None
        assert info["class_name"] == "ULyraHealthComponent"

    def test_resolve_single_missing(self, store_with_cpp):
        assert store_with_cpp.resolve_cpp_source("NoSuchClass") is None

    def test_empty_input(self, store_with_cpp):
        assert store_with_cpp.resolve_cpp_sources([]) == {}

    def test_shared_candidate_resolves_both_inputs(self, store_with_cpp):
        """When bare 'LyraCharacter' and prefixed 'ALyraCharacter' share a candidate,
        both inputs should resolve."""
        result = store_with_cpp.resolve_cpp_sources(["LyraCharacter", "ALyraCharacter"])
        assert "LyraCharacter" in result
        assert "ALyraCharacter" in result
        assert result["LyraCharacter"]["class_name"] == "ALyraCharacter"
        assert result["ALyraCharacter"]["class_name"] == "ALyraCharacter"


# ---------------------------------------------------------------------------
# _enrich_blueprint_xml
# ---------------------------------------------------------------------------

from UnrealAgent.mcp_server import _enrich_blueprint_xml


_BLUEPRINT_XML = """<blueprint>
  <name>B_Hero_ShooterMannequin</name>
  <parent>LyraCharacter</parent>
  <components>
    <component type="LyraHealthComponent">HealthComponent</component>
    <component type="LyraHeroComponent">HeroComponent</component>
    <component type="CapsuleComponent">CollisionCylinder</component>
  </components>
  <events></events>
  <variables></variables>
</blueprint>"""

_WIDGET_XML = """<widget-blueprint>
  <parent-class>LyraActivatableWidget</parent-class>
  <hierarchy>
    <widget name="Root" type="Overlay" />
  </hierarchy>
</widget-blueprint>"""


class TestEnrichBlueprintXml:
    def test_enriches_with_resolved_sources(self):
        """Blueprint XML should get <cpp-sources> appended for resolvable classes."""
        mock_store = MagicMock()
        mock_store.resolve_cpp_sources.return_value = {
            "LyraCharacter": {
                "class_name": "ALyraCharacter",
                "doc_id": "doc_char",
                "source_path": "Source/LyraGame/Character/LyraCharacter.h",
            },
            "LyraHealthComponent": {
                "class_name": "ULyraHealthComponent",
                "doc_id": "doc_health",
                "source_path": "Source/LyraGame/Character/LyraHealthComponent.h",
            },
        }

        with patch("UnrealAgent.mcp_server.get_store", return_value=mock_store):
            result = _enrich_blueprint_xml(_BLUEPRINT_XML)

        assert "<cpp-sources" in result
        assert 'class="LyraCharacter"' in result
        assert 'path="Source/LyraGame/Character/LyraCharacter.h"' in result
        assert 'class="LyraHealthComponent"' in result
        # CapsuleComponent not in mock → should not appear
        assert 'class="CapsuleComponent"' not in result
        # Original content preserved
        assert "<parent>LyraCharacter</parent>" in result
        assert "</blueprint>" in result

    def test_no_resolvable_classes_unchanged(self):
        """All classes unresolvable → XML returned unchanged."""
        mock_store = MagicMock()
        mock_store.resolve_cpp_sources.return_value = {}

        with patch("UnrealAgent.mcp_server.get_store", return_value=mock_store):
            result = _enrich_blueprint_xml(_BLUEPRINT_XML)

        assert result == _BLUEPRINT_XML

    def test_nonblueprint_xml_unchanged(self):
        """Widget XML should not be modified."""
        result = _enrich_blueprint_xml(_WIDGET_XML)
        assert result == _WIDGET_XML
        assert "<cpp-sources" not in result

    def test_store_exception_returns_original(self):
        """If store raises, return original XML gracefully."""
        with patch(
            "UnrealAgent.mcp_server.get_store", side_effect=Exception("DB error")
        ):
            result = _enrich_blueprint_xml(_BLUEPRINT_XML)
        assert result == _BLUEPRINT_XML

    def test_null_source_path_skipped(self):
        """Entries with None source_path should be silently skipped."""
        mock_store = MagicMock()
        mock_store.resolve_cpp_sources.return_value = {
            "LyraCharacter": {
                "class_name": "ALyraCharacter",
                "doc_id": "doc_char",
                "source_path": None,
            },
            "LyraHealthComponent": {
                "class_name": "ULyraHealthComponent",
                "doc_id": "doc_health",
                "source_path": "Source/LyraGame/Character/LyraHealthComponent.h",
            },
        }

        with patch("UnrealAgent.mcp_server.get_store", return_value=mock_store):
            result = _enrich_blueprint_xml(_BLUEPRINT_XML)

        assert 'class="LyraHealthComponent"' in result
        assert (
            "LyraCharacter"
            not in result.split("<cpp-sources")[1].split("</cpp-sources>")[0]
        )

    def test_xml_attributes_escaped(self):
        """Paths with XML-significant characters should be escaped."""
        mock_store = MagicMock()
        mock_store.resolve_cpp_sources.return_value = {
            "LyraCharacter": {
                "class_name": "ALyraCharacter",
                "doc_id": "doc_char",
                "source_path": 'Source/A&B/Lyra "Special".h',
            },
        }

        with (
            patch("UnrealAgent.mcp_server.get_store", return_value=mock_store),
            patch(
                "UnrealAgent.mcp_server._get_project_root",
                return_value="C:\\Users\\A&B",
            ),
        ):
            result = _enrich_blueprint_xml(_BLUEPRINT_XML)

        assert "&amp;" in result
        # quoteattr uses single-quote delimiters when value contains "
        # so the path attr becomes: path='Source/A&amp;B/Lyra "Special".h'
        assert "A&amp;B" in result
        # Raw & should not appear in the cpp-sources block attributes
        cpp_block = result[
            result.index("<cpp-sources") : result.index("</cpp-sources>")
        ]
        assert "A&B" not in cpp_block  # only A&amp;B
        assert "<cpp-sources" in result
        assert "</blueprint>" in result


# ---------------------------------------------------------------------------
# Search fallback: cpp_class_index probe in name mode
# ---------------------------------------------------------------------------


class TestSearchCppFallback:
    def test_name_search_finds_cpp_via_index(self):
        """Name search for 'LyraHealthComponent' should find it via cpp_class_index fallback."""
        from UnrealAgent.knowledge_index.schemas import DocChunk

        mock_doc = DocChunk(
            doc_id="doc_health",
            path="Source/LyraGame/Character/LyraHealthComponent.h",
            name="ULyraHealthComponent",
            type="source_file",
            asset_type="CppClass",
            text="UCLASS() class ULyraHealthComponent : public UGameplayAbilityComponent",
        )

        mock_store = MagicMock()
        # FTS returns empty (simulating the FTS miss)
        mock_retriever = MagicMock()
        mock_retriever.search_exact.return_value = []

        # lightweight_assets returns no rows
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_store._get_connection.return_value = mock_conn

        # cpp_class_index fallback
        mock_store.resolve_cpp_source.return_value = {
            "class_name": "ULyraHealthComponent",
            "doc_id": "doc_health",
            "source_path": "Source/LyraGame/Character/LyraHealthComponent.h",
        }
        mock_store.get_doc.return_value = mock_doc

        with (
            patch("UnrealAgent.search.engine.get_store", return_value=mock_store),
            patch(
                "UnrealAgent.search.engine.get_retriever_instance",
                return_value=mock_retriever,
            ),
        ):
            from UnrealAgent.search.engine import unreal_search

            result = unreal_search("LyraHealthComponent", search_type="name", limit=5)

        # Should have found the C++ class
        cpp_results = [r for r in result["results"] if r.get("type") == "CppClass"]
        assert len(cpp_results) == 1
        assert cpp_results[0]["name"] == "ULyraHealthComponent"
        assert "LyraHealthComponent.h" in cpp_results[0]["path"]
