"""Tests for assets/inspector.py — path resolution and parser invocation."""

import json
from unittest.mock import patch


from unreal_agent.assets.inspector import (
    _asset_path_to_file,
    _run_asset_parser,
)


class TestAssetPathToFile:
    """_asset_path_to_file() path resolution."""

    @patch(
        "unreal_agent.core.config.PROJECT",
        "/projects/Lyra/LyraStarterGame.uproject",
    )
    def test_game_path(self):
        result = _asset_path_to_file("/Game/UI/HUD/Widget")
        assert result.endswith("Content/UI/HUD/Widget.uasset")
        assert "/projects/Lyra/" in result

    @patch(
        "unreal_agent.core.config.PROJECT",
        "/projects/Lyra/LyraStarterGame.uproject",
    )
    @patch("unreal_agent.assets.inspector._discover_plugins")
    @patch(
        "unreal_agent.assets.inspector._plugin_paths",
        {"ShooterCore": "/projects/Lyra/Plugins/ShooterCore/Content"},
    )
    def test_plugin_path(self, mock_discover):
        result = _asset_path_to_file("/ShooterCore/Weapons/Rifle")
        assert result.endswith("Weapons/Rifle.uasset")
        assert "ShooterCore" in result

    @patch(
        "unreal_agent.core.config.PROJECT",
        "/projects/Lyra/LyraStarterGame.uproject",
    )
    @patch("unreal_agent.assets.inspector._discover_plugins")
    @patch("unreal_agent.assets.inspector._plugin_paths", {})
    def test_unknown_mount_returns_input(self, mock_discover):
        result = _asset_path_to_file("/Unknown/Path")
        assert result == "/Unknown/Path"

    def test_non_game_path_passthrough(self):
        result = _asset_path_to_file("/Script/Engine.Actor")
        assert result == "/Script/Engine.Actor"


class TestRunAssetParser:
    """_run_asset_parser() error handling."""

    def test_missing_file_returns_error(self, tmp_path):
        result = _run_asset_parser("inspect", str(tmp_path / "missing.uasset"))
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"]

    @patch(
        "unreal_agent.assets.inspector._get_asset_parser_path",
        return_value="/nonexistent/AssetParser",
    )
    def test_missing_parser_returns_error(self, mock_path, tmp_path):
        fake_asset = tmp_path / "test.uasset"
        fake_asset.touch()

        result = _run_asset_parser("inspect", str(fake_asset))
        data = json.loads(result)
        assert "error" in data
        assert (
            "not built" in data["error"].lower() or "not found" in data["error"].lower()
        )

    @patch("unreal_agent.assets.inspector._get_asset_parser_path")
    @patch("unreal_agent.assets.inspector.subprocess.run")
    def test_timeout_returns_error(self, mock_run, mock_path, tmp_path):
        import subprocess

        fake_asset = tmp_path / "test.uasset"
        fake_asset.touch()
        mock_path.return_value = "/usr/bin/true"  # exists on Linux
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=30)

        with patch("unreal_agent.assets.inspector.os.path.exists", return_value=True):
            result = _run_asset_parser("inspect", str(fake_asset))
            data = json.loads(result)
            assert "error" in data
            assert "timed out" in data["error"].lower()
