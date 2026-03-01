"""Tests for C++ source bridge: _probe_class_name, resolve_cpp_sources, XML enrichment, search fallback, scan_cpp_classes."""

from unittest.mock import patch, MagicMock

import pytest

from unreal_agent.knowledge_index.store import KnowledgeStore


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
                "Source/LyraGame/Character/LyraHealthComponent.h",
            ),
            (
                "ULyraHeroComponent",
                "Source/LyraGame/Character/LyraHeroComponent.h",
            ),
            ("ALyraCharacter", "Source/LyraGame/Character/LyraCharacter.h"),
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
        assert result["ULyraHealthComponent"]["class_name"] == "ULyraHealthComponent"
        assert "source_path" in result["ULyraHealthComponent"]

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

from unreal_agent.mcp_server import _enrich_blueprint_xml


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
                "source_path": "Source/LyraGame/Character/LyraCharacter.h",
            },
            "LyraHealthComponent": {
                "class_name": "ULyraHealthComponent",
                "source_path": "Source/LyraGame/Character/LyraHealthComponent.h",
            },
        }

        with patch("unreal_agent.mcp_server.get_store", return_value=mock_store):
            result = _enrich_blueprint_xml(_BLUEPRINT_XML)

        assert "<cpp-sources" in result
        assert 'class="LyraCharacter"' in result
        assert 'path="Source/LyraGame/Character/LyraCharacter.h"' in result
        assert 'class="LyraHealthComponent"' in result
        # CapsuleComponent not in mock -> should not appear
        assert 'class="CapsuleComponent"' not in result
        # Original content preserved
        assert "<parent>LyraCharacter</parent>" in result
        assert "</blueprint>" in result

    def test_no_resolvable_classes_unchanged(self):
        """All classes unresolvable -> XML returned unchanged."""
        mock_store = MagicMock()
        mock_store.resolve_cpp_sources.return_value = {}

        with patch("unreal_agent.mcp_server.get_store", return_value=mock_store):
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
            "unreal_agent.mcp_server.get_store", side_effect=Exception("DB error")
        ):
            result = _enrich_blueprint_xml(_BLUEPRINT_XML)
        assert result == _BLUEPRINT_XML

    def test_null_source_path_skipped(self):
        """Entries with None source_path should be silently skipped."""
        mock_store = MagicMock()
        mock_store.resolve_cpp_sources.return_value = {
            "LyraCharacter": {
                "class_name": "ALyraCharacter",
                "source_path": None,
            },
            "LyraHealthComponent": {
                "class_name": "ULyraHealthComponent",
                "source_path": "Source/LyraGame/Character/LyraHealthComponent.h",
            },
        }

        with patch("unreal_agent.mcp_server.get_store", return_value=mock_store):
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
                "source_path": 'Source/A&B/Lyra "Special".h',
            },
        }

        with (
            patch("unreal_agent.mcp_server.get_store", return_value=mock_store),
            patch(
                "unreal_agent.mcp_server._get_project_root",
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
        mock_store = MagicMock()
        # FTS returns empty (simulating the FTS miss)
        mock_retriever = MagicMock()
        mock_retriever.search_exact.return_value = []

        # lightweight_assets returns no rows
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_store._get_connection.return_value = mock_conn

        # cpp_class_index fallback — no doc_id, just class_name + source_path
        mock_store.resolve_cpp_source.return_value = {
            "class_name": "ULyraHealthComponent",
            "source_path": "Source/LyraGame/Character/LyraHealthComponent.h",
        }

        with (
            patch("unreal_agent.search.engine.get_store", return_value=mock_store),
            patch(
                "unreal_agent.search.engine.get_retriever_instance",
                return_value=mock_retriever,
            ),
        ):
            from unreal_agent.search.engine import unreal_search

            result = unreal_search("LyraHealthComponent", search_type="name", limit=5)

        # Should have found the C++ class
        cpp_results = [r for r in result["results"] if r.get("type") == "CppClass"]
        assert len(cpp_results) == 1
        assert cpp_results[0]["name"] == "ULyraHealthComponent"
        assert "LyraHealthComponent.h" in cpp_results[0]["path"]


# ---------------------------------------------------------------------------
# scan_cpp_classes
# ---------------------------------------------------------------------------


class TestScanCppClasses:
    def test_happy_path_uclass_and_ustruct(self, tmp_path):
        """Header with UCLASS + USTRUCT should produce both entries."""
        source_dir = tmp_path / "Source" / "MyGame" / "Public"
        source_dir.mkdir(parents=True)
        header = source_dir / "MyActor.h"
        header.write_text(
            "UCLASS(Blueprintable)\n"
            "class MYGAME_API AMyActor : public AActor\n"
            "{\n"
            "};\n"
            "\n"
            "USTRUCT(BlueprintType)\n"
            "struct FMyStruct\n"
            "{\n"
            "};\n"
        )

        db = tmp_path / "test.db"
        store = KnowledgeStore(str(db), use_vector_search=False)
        count = store.scan_cpp_classes(tmp_path)

        assert count == 2
        info = store.resolve_cpp_source("AMyActor")
        assert info is not None
        assert info["class_name"] == "AMyActor"
        assert "MyActor.h" in info["source_path"]

        info2 = store.resolve_cpp_source("FMyStruct")
        assert info2 is not None

    def test_intermediate_dir_excluded(self, tmp_path):
        """Headers in Intermediate/ subdirectory should be skipped."""
        inter_dir = tmp_path / "Source" / "MyGame" / "Intermediate" / "Build"
        inter_dir.mkdir(parents=True)
        header = inter_dir / "Generated.h"
        header.write_text("UCLASS()\nclass UGenerated : public UObject\n{\n};\n")

        db = tmp_path / "test.db"
        store = KnowledgeStore(str(db), use_vector_search=False)
        count = store.scan_cpp_classes(tmp_path)

        assert count == 0

    def test_nested_paren_specifiers(self, tmp_path):
        """UCLASS(Meta=(BlueprintSpawnableComponent)) should extract class name."""
        source_dir = tmp_path / "Source" / "MyGame"
        source_dir.mkdir(parents=True)
        header = source_dir / "MyComp.h"
        header.write_text(
            "UCLASS(Meta=(BlueprintSpawnableComponent))\n"
            "class UMyComponent : public UActorComponent\n"
            "{\n"
            "};\n"
        )

        db = tmp_path / "test.db"
        store = KnowledgeStore(str(db), use_vector_search=False)
        count = store.scan_cpp_classes(tmp_path)

        assert count == 1
        info = store.resolve_cpp_source("UMyComponent")
        assert info is not None

    def test_empty_header_no_classes(self, tmp_path):
        """Header with no UCLASS/USTRUCT should produce 0 classes."""
        source_dir = tmp_path / "Source" / "MyGame"
        source_dir.mkdir(parents=True)
        header = source_dir / "Utils.h"
        header.write_text("#pragma once\n\nint add(int a, int b);\n")

        db = tmp_path / "test.db"
        store = KnowledgeStore(str(db), use_vector_search=False)
        count = store.scan_cpp_classes(tmp_path)

        assert count == 0

    def test_missing_source_dir_returns_zero(self, tmp_path):
        """Project root with no Source/ or Plugins/ should return 0."""
        db = tmp_path / "test.db"
        store = KnowledgeStore(str(db), use_vector_search=False)
        count = store.scan_cpp_classes(tmp_path)

        assert count == 0

    def test_generated_h_excluded(self, tmp_path):
        """Files ending in .generated.h should be skipped."""
        source_dir = tmp_path / "Source" / "MyGame"
        source_dir.mkdir(parents=True)
        header = source_dir / "MyActor.generated.h"
        header.write_text("UCLASS()\nclass AMyActor : public AActor\n{};\n")

        db = tmp_path / "test.db"
        store = KnowledgeStore(str(db), use_vector_search=False)
        count = store.scan_cpp_classes(tmp_path)

        assert count == 0

    def test_rescan_clears_stale_entries(self, tmp_path):
        """Rescan should remove classes from deleted/renamed headers."""
        source_dir = tmp_path / "Source" / "MyGame"
        source_dir.mkdir(parents=True)
        header = source_dir / "OldActor.h"
        header.write_text("UCLASS()\nclass AOldActor : public AActor\n{\n};\n")

        db = tmp_path / "test.db"
        store = KnowledgeStore(str(db), use_vector_search=False)

        # First scan: AOldActor present
        count1 = store.scan_cpp_classes(tmp_path)
        assert count1 == 1
        assert store.resolve_cpp_source("AOldActor") is not None

        # Delete old header, add new one
        header.unlink()
        new_header = source_dir / "NewActor.h"
        new_header.write_text("UCLASS()\nclass ANewActor : public AActor\n{\n};\n")

        # Second scan: AOldActor gone, ANewActor present
        count2 = store.scan_cpp_classes(tmp_path)
        assert count2 == 1
        assert store.resolve_cpp_source("AOldActor") is None
        assert store.resolve_cpp_source("ANewActor") is not None
