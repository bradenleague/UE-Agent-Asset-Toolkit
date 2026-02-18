"""Tests for the path utility module."""

import pytest

from UnrealAgent.pathutil import to_game_path_sep


# ---------------------------------------------------------------------------
# Unit tests — to_game_path_sep
# ---------------------------------------------------------------------------


class TestToGamePathSep:
    def test_forward_slashes_unchanged(self):
        assert to_game_path_sep("/Game/UI/Widget") == "/Game/UI/Widget"

    def test_backslashes_converted(self):
        assert to_game_path_sep("UI\\HUD\\Widget") == "UI/HUD/Widget"

    def test_mixed_separators(self):
        assert to_game_path_sep("UI/HUD\\Widget") == "UI/HUD/Widget"

    def test_empty_string(self):
        assert to_game_path_sep("") == ""

    def test_windows_absolute_path(self):
        assert (
            to_game_path_sep("D:\\Projects\\MyGame\\Content\\UI")
            == "D:/Projects/MyGame/Content/UI"
        )

    def test_single_component(self):
        assert to_game_path_sep("Widget") == "Widget"

    def test_trailing_backslash(self):
        assert to_game_path_sep("UI\\HUD\\") == "UI/HUD/"


# ---------------------------------------------------------------------------
# Integration test — _fs_to_game_path pipeline
# ---------------------------------------------------------------------------


class TestFsToGamePathIntegration:
    """Verify the full filesystem→game-path pipeline produces forward slashes."""

    @pytest.fixture()
    def indexer(self, tmp_path):
        """Create a minimal AssetIndexer with a fake content path."""
        from UnrealAgent.knowledge_index.indexer import AssetIndexer
        from UnrealAgent.knowledge_index.store import KnowledgeStore

        content = tmp_path / "Content"
        content.mkdir()

        store = KnowledgeStore(":memory:")
        return AssetIndexer(
            store=store,
            content_path=content,
            parser_path="/dev/null",
        )

    @pytest.mark.parametrize(
        "rel_parts, expected",
        [
            (["UI", "Widget.uasset"], "/Game/UI/Widget"),
            (["UI", "HUD", "Health.uasset"], "/Game/UI/HUD/Health"),
            (["Maps", "MainMenu.uasset"], "/Game/Maps/MainMenu"),
        ],
    )
    def test_game_path_has_forward_slashes(self, indexer, rel_parts, expected):
        # Build a filesystem path under the indexer's content_path
        fs_path = indexer.content_path.joinpath(*rel_parts)
        result = indexer._fs_to_game_path(fs_path)
        assert result == expected
        assert "\\" not in result
