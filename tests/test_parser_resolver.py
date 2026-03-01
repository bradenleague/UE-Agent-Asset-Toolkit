"""Tests for parser_resolver.py — AssetParser binary path resolution."""

import json
from unittest.mock import patch


from unreal_agent.parser_resolver import resolve_parser_path


class TestLocalConfig:
    """Resolution via local_config.json."""

    def test_valid_local_config(self, tmp_path):
        fake_parser = tmp_path / "AssetParser"
        fake_parser.touch()

        config = {"asset_parser_path": str(fake_parser)}
        config_file = tmp_path / "local_config.json"
        config_file.write_text(json.dumps(config))

        result = resolve_parser_path(local_config_dir=tmp_path)
        assert result == str(fake_parser)

    def test_missing_local_config(self, tmp_path):
        # No local_config.json — should fall through to in-tree detection
        result = resolve_parser_path(local_config_dir=tmp_path)
        assert result is not None  # returns a candidate path (may not exist)

    def test_malformed_local_config(self, tmp_path):
        config_file = tmp_path / "local_config.json"
        config_file.write_text("not valid json {{{")

        result = resolve_parser_path(local_config_dir=tmp_path)
        assert result is not None  # falls through gracefully

    def test_local_config_missing_binary(self, tmp_path):
        config = {"asset_parser_path": "/nonexistent/AssetParser"}
        config_file = tmp_path / "local_config.json"
        config_file.write_text(json.dumps(config))

        # Binary doesn't exist, should fall through to in-tree
        result = resolve_parser_path(local_config_dir=tmp_path)
        assert result is not None


class TestPlatformDetection:
    """In-tree platform-specific path resolution."""

    @patch("unreal_agent.parser_resolver.platform")
    def test_linux_x64(self, mock_platform, tmp_path):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "x86_64"

        result = resolve_parser_path(local_config_dir=tmp_path)
        assert "linux-x64" in result or "AssetParser" in result

    @patch("unreal_agent.parser_resolver.platform")
    def test_macos_arm64(self, mock_platform, tmp_path):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "arm64"

        result = resolve_parser_path(local_config_dir=tmp_path)
        assert "osx-arm64" in result or "AssetParser" in result

    @patch("unreal_agent.parser_resolver.platform")
    def test_windows(self, mock_platform, tmp_path):
        mock_platform.system.return_value = "Windows"
        mock_platform.machine.return_value = "AMD64"

        result = resolve_parser_path(local_config_dir=tmp_path)
        assert "AssetParser.exe" in result
