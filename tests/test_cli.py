"""Tests for cli.py — option resolution and CLI entry point."""

import argparse
import os
from unittest.mock import patch, MagicMock

from unreal_agent.cli import _resolve_index_options, main, QUICK_TYPE_PROFILES


def _make_args(**overrides):
    """Build a minimal argparse.Namespace matching cli.py expectations."""
    defaults = {
        "profile": None,
        "plugins": False,
        "batch_size": None,
        "max_assets": None,
        "max_batch_memory": None,
        "non_recursive": False,
        "path": None,
        "no_ofpa": False,
        "quick_profile": "default",
        "types": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestResolveIndexOptions:
    """_resolve_index_options() cascade logic: CLI > saved > env > default."""

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_defaults(self, mock_opts):
        """No CLI args, no saved opts -> hybrid profile, batch_size=500."""
        args = _make_args()
        result = _resolve_index_options(args)

        assert result["profile"] == "hybrid"
        assert result["batch_size"] == 500
        assert result["include_plugins"] is False
        assert result["max_assets"] is None
        assert result["recursive"] is True
        assert result["index_path"] == "/Game"
        assert result["exclude_patterns"] is None
        assert result["selected_types"] is None

    @patch(
        "unreal_agent.tools.get_project_index_options",
        return_value={"default_profile": "hybrid"},
    )
    def test_cli_profile_overrides_saved(self, mock_opts):
        """Explicit --profile quick overrides saved default_profile."""
        args = _make_args(profile="quick")
        result = _resolve_index_options(args)

        assert result["profile"] == "quick"
        assert result["selected_types"] == QUICK_TYPE_PROFILES["default"]

    @patch(
        "unreal_agent.tools.get_project_index_options",
        return_value={"default_profile": "quick"},
    )
    def test_saved_profile_used_when_no_cli(self, mock_opts):
        """Saved default_profile used when --profile not passed."""
        args = _make_args()
        result = _resolve_index_options(args)
        assert result["profile"] == "quick"

    @patch(
        "unreal_agent.tools.get_project_index_options",
        return_value={"batch_size": 200},
    )
    def test_cli_batch_size_overrides_saved(self, mock_opts):
        """Explicit --batch-size overrides saved and env."""
        args = _make_args(batch_size=100)
        result = _resolve_index_options(args)
        assert result["batch_size"] == 100

    @patch(
        "unreal_agent.tools.get_project_index_options",
        return_value={"batch_size": 300},
    )
    def test_saved_batch_size_overrides_env(self, mock_opts):
        """Saved batch_size takes priority over env var."""
        args = _make_args()
        with patch.dict(os.environ, {"UE_INDEX_BATCH_SIZE": "999"}):
            result = _resolve_index_options(args)
        assert result["batch_size"] == 300

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_env_batch_size_fallback(self, mock_opts):
        """Env var UE_INDEX_BATCH_SIZE used when no CLI or saved value."""
        args = _make_args()
        with patch.dict(os.environ, {"UE_INDEX_BATCH_SIZE": "750"}):
            result = _resolve_index_options(args)
        assert result["batch_size"] == 750

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_batch_size_clamped(self, mock_opts):
        """Batch size clamped to [1, 2000]."""
        # Over max
        args = _make_args(batch_size=5000)
        result = _resolve_index_options(args)
        assert result["batch_size"] == 2000

        # Under min
        args = _make_args(batch_size=0)
        result = _resolve_index_options(args)
        assert result["batch_size"] == 1

    @patch(
        "unreal_agent.tools.get_project_index_options",
        return_value={"include_plugins": True},
    )
    def test_plugins_from_saved(self, mock_opts):
        """Saved include_plugins used when CLI --plugins not set."""
        args = _make_args()
        result = _resolve_index_options(args)
        assert result["include_plugins"] is True

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_non_recursive(self, mock_opts):
        """--non-recursive sets recursive=False."""
        args = _make_args(non_recursive=True)
        result = _resolve_index_options(args)
        assert result["recursive"] is False

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_path_normalization(self, mock_opts):
        """Path without /Game prefix gets normalized."""
        args = _make_args(path="UI/HUD")
        result = _resolve_index_options(args)
        assert result["index_path"] == "/Game/UI/HUD"

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_windows_path_sanitized(self, mock_opts):
        """Windows-style paths with /Game/ inside get extracted."""
        args = _make_args(path="C:/Program Files/Game/Content/Game/UI")
        result = _resolve_index_options(args)
        assert result["index_path"].startswith("/Game")

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_no_ofpa_sets_exclude(self, mock_opts):
        """--no-ofpa sets default OFPA exclusion patterns."""
        args = _make_args(no_ofpa=True)
        result = _resolve_index_options(args)
        assert result["exclude_patterns"] is not None
        assert "__ExternalActors__" in result["exclude_patterns"]
        assert "__ExternalObjects__" in result["exclude_patterns"]

    @patch(
        "unreal_agent.tools.get_project_index_options",
        return_value={"exclude_paths": ["__ExternalActors__"]},
    )
    def test_saved_exclude_paths(self, mock_opts):
        """Saved exclude_paths used without --no-ofpa."""
        args = _make_args()
        result = _resolve_index_options(args)
        assert result["exclude_patterns"] == ["__ExternalActors__"]

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_quick_custom_types(self, mock_opts):
        """--profile quick --types overrides default type list."""
        args = _make_args(profile="quick", types="Blueprint,DataTable")
        result = _resolve_index_options(args)
        assert result["selected_types"] == ["Blueprint", "DataTable"]

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_quick_analysis_profile(self, mock_opts):
        """--quick-profile analysis uses expanded type list."""
        args = _make_args(profile="quick", quick_profile="analysis")
        result = _resolve_index_options(args)
        assert result["selected_types"] == QUICK_TYPE_PROFILES["analysis"]

    @patch("unreal_agent.tools.get_project_index_options", return_value={})
    def test_max_assets_positive(self, mock_opts):
        """max_assets is clamped to >= 1."""
        args = _make_args(max_assets=0)
        result = _resolve_index_options(args)
        # 0 is not None, so max(1, 0) = 1
        assert result["max_assets"] == 1


class TestMainHelpExit:
    """Verify --help exits 0."""

    def test_help_exits_zero(self, capsys):
        """--help should print help and exit 0."""
        import sys

        try:
            with patch.object(sys, "argv", ["unreal-agent-toolkit", "--help"]):
                main()
        except SystemExit as e:
            assert e.code == 0
        else:
            assert False, "--help should raise SystemExit"


class TestSourceRouting:
    """Verify --source calls store.scan_cpp_classes()."""

    @patch("unreal_agent.tools.PROJECT", "/tmp/Test.uproject")
    @patch("unreal_agent.tools.get_active_project_name", return_value="test")
    @patch("unreal_agent.tools.get_project_db_path")
    def test_source_calls_scan_cpp_classes(self, mock_db_path, mock_name, tmp_path):
        """--source should route to store.scan_cpp_classes()."""
        mock_db_path.return_value = str(tmp_path / "test.db")

        mock_store = MagicMock()
        mock_store.scan_cpp_classes.return_value = 42

        with patch(
            "unreal_agent.knowledge_index.KnowledgeStore",
            return_value=mock_store,
        ):
            args = argparse.Namespace(
                command=None,
                source=True,
                project=None,
                rebuild_fts=False,
                dry_run=False,
                status=False,
                log_file=None,
                timing=False,
            )
            from unreal_agent.cli import cmd_index

            cmd_index(args)

        mock_store.scan_cpp_classes.assert_called_once()
