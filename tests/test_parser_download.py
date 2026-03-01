"""Tests for parser_download.py — GitHub Releases download fallback."""

import json
from unittest.mock import patch, MagicMock


from unreal_agent.parser_download import get_runtime_id, download_parser


class TestGetRuntimeId:
    """Platform detection for .NET RIDs."""

    @patch("unreal_agent.parser_download.platform")
    def test_linux_x64(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "x86_64"
        assert get_runtime_id() == "linux-x64"

    @patch("unreal_agent.parser_download.platform")
    def test_linux_arm64(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "aarch64"
        assert get_runtime_id() == "linux-arm64"

    @patch("unreal_agent.parser_download.platform")
    def test_macos_arm64(self, mock_platform):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "arm64"
        assert get_runtime_id() == "osx-arm64"

    @patch("unreal_agent.parser_download.platform")
    def test_windows(self, mock_platform):
        mock_platform.system.return_value = "Windows"
        mock_platform.machine.return_value = "AMD64"
        assert get_runtime_id() == "win-x64"


class TestDownloadParser:
    """Download with mocked network calls."""

    @patch("unreal_agent.parser_download.urllib.request.urlopen")
    def test_network_error_returns_none(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        result = download_parser(version="v0.1.0")
        assert result is None

    @patch("unreal_agent.parser_download.get_runtime_id", return_value="linux-x64")
    @patch("unreal_agent.parser_download.urllib.request.urlopen")
    def test_no_matching_asset_returns_none(self, mock_urlopen, mock_rid):
        release_data = json.dumps(
            {
                "assets": [
                    {
                        "name": "AssetParser-win-x64.zip",
                        "browser_download_url": "https://example.com/win",
                    },
                ]
            }
        ).encode()
        mock_response = MagicMock()
        mock_response.read.return_value = release_data
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = download_parser(version="v0.1.0")
        assert result is None

    def test_cached_binary_returned(self, tmp_path):
        """If binary is already cached, return it without downloading."""
        cache_dir = tmp_path / "AssetParser-v1.0.0"
        cache_dir.mkdir(parents=True)
        binary = cache_dir / "AssetParser"
        binary.touch()

        with patch("unreal_agent.parser_download._CACHE_DIR", tmp_path):
            with patch(
                "unreal_agent.parser_download.get_runtime_id", return_value="linux-x64"
            ):
                result = download_parser(version="v1.0.0")
                assert result == binary
