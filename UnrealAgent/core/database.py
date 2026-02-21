import os

_CORE_DIR = os.path.dirname(__file__)
_TOOL_DIR = os.path.dirname(_CORE_DIR)

def get_project_db_path(project_name: str = None) -> str:
    from .config import get_active_project_name
    
    if not project_name:
        project_name = get_active_project_name()

    if not project_name:
        # Fallback for backwards compatibility
        return os.path.join(_TOOL_DIR, "data", "knowledge_index.db")

    return os.path.join(_TOOL_DIR, "data", f"{project_name}.db")
