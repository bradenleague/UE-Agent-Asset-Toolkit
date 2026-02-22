"""Path utilities for cross-platform Unreal asset path handling."""


def to_game_path_sep(path: str) -> str:
    """Normalize path separators to forward slashes for Unreal game paths.

    Use at the filesystem→game-path boundary. Unreal game paths always use
    forward slashes (e.g. /Game/UI/Widget), but on Windows ``pathlib`` and
    ``os.path`` produce backslashes. Call this once when converting a
    filesystem-relative path into a game path string.
    """
    return path.replace("\\", "/")
