from typing import Optional

def _guess_asset_type_from_name(asset_name: str, file_path: str) -> Optional[str]:
    """Fast heuristic to guess asset type from naming conventions.

    Returns None if uncertain - caller should use AssetParser for definitive answer.
    Note: Heuristics are imperfect but provide ~10-100x speedup over parsing every file.
    """
    name_lower = asset_name.lower()
    path_lower = file_path.lower()

    if "_builtdata" in name_lower:
        return "_BuiltData"

    if name_lower.startswith("bp_"):
        return "Blueprint"
    if name_lower.startswith("wbp_") or name_lower.startswith("wb_"):
        return "WidgetBlueprint"
    if name_lower.startswith("dt_"):
        return "DataTable"
    if name_lower.startswith("da_"):
        return "DataAsset"
    if name_lower.startswith("mi_"):
        return "MaterialInstance"
    if name_lower.startswith("mf_"):
        return "MaterialFunction"
    if name_lower.startswith("m_"):
        return "Material"
    if name_lower.startswith("t_"):
        return "Texture2D"
    if name_lower.startswith("sm_"):
        return "StaticMesh"
    if name_lower.startswith("sk_") or name_lower.startswith("skm_"):
        return "SkeletalMesh"
    if name_lower.startswith("abp_"):
        return "AnimBlueprint"
    if name_lower.startswith("am_"):
        return "AnimMontage"
    if name_lower.startswith("gc_"):
        return "GameplayCue"
    if name_lower.startswith("ga_"):
        return "GameplayAbility"
    if name_lower.startswith("ge_"):
        return "GameplayEffect"

    if name_lower.startswith("w_") and ("/ui/" in path_lower or "widget" in name_lower):
        return "WidgetBlueprint"
    if name_lower.startswith("b_") and "/experiences/" in path_lower:
        return "Blueprint"

    if "/ui/" in path_lower or "/widgets/" in path_lower:
        if "widget" in name_lower or name_lower.startswith("w_"):
            return "WidgetBlueprint"
    if "/datatables/" in path_lower:
        return "DataTable"

    return None
