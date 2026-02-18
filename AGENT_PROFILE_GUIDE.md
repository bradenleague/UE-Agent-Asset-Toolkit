# Agent Profile Guide

How to generate a project profile JSON for the UE Asset Toolkit. A profile tells the indexer, parser, and MCP server about your project's custom asset types so they get classified and extracted correctly.

## How Profiles Work

Profiles use a two-layer merge system:

1. **`_defaults.json`** — Engine-level types (GameFeatureData, DataAsset, PrimaryDataAsset, etc.)
2. **`<project>.json`** — Project-specific overrides

When a profile key appears in the project JSON, it **replaces** the default entirely (per-key override, not deep merge). If your project has no custom types for a given key, omit it to inherit the default.

The merged result is consumed by:
- **Python indexer** — asset classification, semantic extraction, data asset routing
- **C# parser** — type detection via `--type-config` (gets `export_class_reclassify` + `name_prefixes`)
- **MCP server** — widget trace ranking, search features

## Profile Keys Reference

### `export_class_reclassify`

**What it does:** Maps C++ export class names to the asset type used in the index. When the parser finds an export with class `LyraAbilitySet`, this tells it to classify the asset as `DataAsset`.

**How to discover values:** After an initial index (no profile), query for Unknown assets and inspect a few to find their export class:

```bash
# Inspect an Unknown asset to see its export class
cd UnrealAgent && python -c "
import tools
result = tools.inspect_asset('/Game/Path/To/UnknownAsset')
print(result)
"
```

Or query the DB for classes already discovered during batch-summary:

```sql
SELECT json_extract(metadata, '$.class') as cls, COUNT(*) as cnt
FROM docs
WHERE json_extract(metadata, '$.class') IS NOT NULL
GROUP BY cls ORDER BY cnt DESC;
```

**Example:**
```json
"export_class_reclassify": {
  "GameFeatureData": "GameFeatureData",
  "MyGameAbilitySet": "DataAsset",
  "MyGamePawnData": "DataAsset",
  "MyGameInputConfig": "DataAsset",
  "MyGameExperienceDefinition": "MyGameExperienceDefinition"
}
```

**Rules:**
- Map to `"DataAsset"` for generic DataAsset subclasses (abilities, pawns, configs)
- Map to `"GameFeatureData"` for game feature data assets
- Map to the class name itself (e.g., `"MyGameExperienceDefinition"`) if the type needs its own semantic extraction pipeline
- Include `"GameFeatureData": "GameFeatureData"` unless your project doesn't use Game Features

---

### `name_prefixes`

**What it does:** Maps asset name prefixes to types. If an asset is named `MG_SomeAbility`, and `MG_` maps to `DataAsset`, the parser classifies it as DataAsset even before inspecting its export class.

**How to discover values:**

```sql
-- Find common prefixes in unknown or unclassified assets
SELECT
  SUBSTR(name, 1, INSTR(name, '_')) as prefix,
  COUNT(*) as cnt,
  GROUP_CONCAT(DISTINCT asset_type) as types
FROM lightweight_assets
WHERE INSTR(name, '_') > 1
GROUP BY prefix
HAVING cnt >= 3
ORDER BY cnt DESC
LIMIT 20;
```

Look for prefixes that consistently map to one type. Standard UE prefixes (BP_, WBP_, DT_, MI_, SM_, T_) are already in `_defaults.json` — only add project-specific ones.

**Example:**
```json
"name_prefixes": {
  "LAS_": "LyraExperienceActionSet",
  "EAS_": "LyraExperienceActionSet",
  "TEAMDA_": "DataAsset",
  "CFX_": "DataAsset"
}
```

**Rules:**
- Include the trailing underscore
- Only add prefixes that are specific to your project (engine prefixes are in defaults)
- The mapped type must also appear in `export_class_reclassify` if it's a custom type

---

### `semantic_types`

**What it does:** Additional asset types (beyond the built-in list) that get full semantic indexing — meaning the indexer inspects them individually and creates rich text documents with metadata.

Built-in semantic types (always indexed): `Blueprint`, `WidgetBlueprint`, `Material`, `MaterialInstance`, `MaterialFunction`, `DataTable`, `GameFeatureData`, `DataAsset`, `InputAction`, `InputMappingContext`

**When to add types:** If you created a custom type in `export_class_reclassify` that maps to itself (not to `DataAsset` or another built-in), add it here so the indexer knows to do deep extraction.

**Example:**
```json
"semantic_types": [
  "LyraExperienceActionSet",
  "LyraExperienceDefinition"
]
```

**Rules:**
- Don't add types that map to `DataAsset` in `export_class_reclassify` — those already get semantic indexing through the DataAsset pipeline
- Only add types that have their own identity distinct from the built-in types

---

### `game_feature_types`

**What it does:** Types routed to the Game Feature extraction handler, which parses `AddWidgets`, `AddComponents`, `AddInputMapping` actions and creates typed edges.

**Default:** `["GameFeatureData"]`

**When to customize:** If your project has custom types that contain Game Feature action lists (like Lyra's `LyraExperienceActionSet` and `LyraExperienceDefinition`).

**Example:**
```json
"game_feature_types": [
  "GameFeatureData",
  "LyraExperienceActionSet",
  "LyraExperienceDefinition"
]
```

**How to identify:** Inspect a few of your Game Feature assets and look for action lists:

```bash
# Check if an asset has AddWidgets/AddComponents actions
cd UnrealAgent && python -c "
import tools
result = tools.inspect_asset('/Game/System/Experiences/B_DefaultExperience')
print(result)
"
```

If the inspect output shows `GameFeatureAction_AddWidgets`, `GameFeatureAction_AddComponents`, etc., the type belongs here.

---

### `blueprint_parent_redirects`

**What it does:** When a Blueprint's parent class matches a key, it gets rerouted to the Game Feature handler instead of the normal Blueprint handler.

**When to use:** If you have Blueprint assets that inherit from a custom class (e.g., `LyraExperienceDefinition`) and should be treated as Game Feature definitions rather than normal Blueprints.

**Example:**
```json
"blueprint_parent_redirects": {
  "LyraExperienceDefinition": "LyraExperienceDefinition"
}
```

**Most projects won't need this.** Only add it if you've verified that Blueprint assets with this parent contain Game Feature action data.

---

### `data_asset_extractors`

**What it does:** Lists DataAsset class names that have custom extractor functions registered in `indexer.py` via the `@data_asset_extractor("ClassName")` decorator.

**How it works:** When the indexer encounters a DataAsset with a class in this list, it dispatches to the registered handler instead of the generic fallback. The generic fallback still works — it just produces less structured output (property names + references vs. parsed abilities, pawn configs, etc.).

**Example:**
```json
"data_asset_extractors": [
  "LyraAbilitySet",
  "LyraPawnData",
  "LyraInputConfig"
]
```

**For a new project:** Start with an empty list. The generic fallback extractor handles all DataAsset types by indexing property names and references. Only add entries here after you've:

1. Identified high-value DataAsset classes (lots of instances, rich property data)
2. Written a custom extractor function in `indexer.py`
3. Tested it against sample assets

**To write a custom extractor:**

```python
# In UnrealAgent/knowledge_index/indexer.py

@data_asset_extractor("MyGameAbilitySet")
def _extract_my_ability_set(
    self, asset_name: str, class_name: str, props: list[dict]
) -> tuple[list[str], dict, dict[str, str]]:
    """
    Args:
        asset_name: e.g., "AbilitySet_Warrior"
        class_name: e.g., "MyGameAbilitySet"
        props: List of property dicts from the parser inspect output.
               Each has {"name": "PropName", "type": "TypeName", "value": ...}

    Returns:
        text_parts: List of strings joined with ". " for the FTS text field
        metadata: Dict stored as JSON in the docs table
        typed_refs: Dict of {asset_path: edge_type} for typed edges
    """
    text_parts = [f"{asset_name} is a {class_name}"]
    metadata = {}
    typed_refs = {}

    for prop in props:
        name = prop.get("name", "")
        value = prop.get("value", "")
        # Extract structured data from properties...

    return text_parts, metadata, typed_refs
```

**Tip:** Inspect a representative asset to see what properties are available:

```bash
cd UnrealAgent && python -c "
import tools, json
result = tools.inspect_asset('/Game/Path/To/MyAbilitySet')
data = json.loads(result)
for prop in data.get('properties', data.get('exports', [{}])[0].get('properties', [])):
    print(f\"{prop.get('name')}: {prop.get('type')} = {str(prop.get('value', ''))[:80]}\")
"
```

---

### `deep_ref_export_classes`

**What it does:** Export classes that trigger deep-reference inspection during indexing. Assets with these classes get their full reference graph extracted even if they have zero references from the initial batch-refs pass.

**Default:** `["GameFeatureData", "DataRegistrySource_DataTable", "DataRegistry"]`

**When to customize:** Add your project's equivalent of Game Feature Data — types that are reference hubs connecting many other assets.

**Example:**
```json
"deep_ref_export_classes": [
  "GameFeatureData",
  "LyraExperienceActionSet",
  "LyraExperienceDefinition"
]
```

---

### `deep_ref_candidates`

**What it does:** Asset *names* (not paths, not classes) that should always get deep-reference inspection. Used for specific high-value assets that might not be caught by class-based rules.

**Example:**
```json
"deep_ref_candidates": ["ShooterCore", "TopDownArena"]
```

**When to use:** If you know specific plugin or experience assets by name that are important reference hubs.

---

### `widget_rank_terms`

**What it does:** Extra terms used by the MCP server's widget trace system to rank widget assets by relevance. Higher-ranked widgets appear first in trace results.

**Default:** `["HUD", "Widget", "UIExtension", "AddWidget", "ExtensionPoint"]`

**When to customize:** Add your project's HUD layout class names or UI-specific terms.

**Example:**
```json
"widget_rank_terms": ["LyraHUDLayout", "MyGameMainHUD"]
```

---

### `widget_fallback_patterns`

**What it does:** SQL LIKE patterns used as fallback queries when the widget trace doesn't find results through the normal edge graph. These search the text field of docs.

**Default:** `["%GameFeatureAction_AddWidget%", "%UIExtensionPointWidget%", "%PrimaryGameLayout%"]`

**When to customize:** Add patterns matching your project's widget registration or layout class names.

**Example:**
```json
"widget_fallback_patterns": [
  "%LyraHUDLayout%",
  "%LyraHUD%",
  "%MyGameMainHUD%"
]
```

---

## Complete Profile Template

```json
{
  "profile_name": "mygame",

  "export_class_reclassify": {
    "GameFeatureData": "GameFeatureData"
  },

  "name_prefixes": {},

  "semantic_types": [],

  "game_feature_types": ["GameFeatureData"],

  "blueprint_parent_redirects": {},

  "data_asset_extractors": [],

  "deep_ref_export_classes": ["GameFeatureData"],

  "deep_ref_candidates": [],

  "widget_rank_terms": [],

  "widget_fallback_patterns": []
}
```

Start with this minimal template. After an initial index, use the discovery queries to fill in project-specific values.

## Iterative Workflow

1. **Index with defaults** → `python index.py --all --plugins`
2. **Analyze** → Run discovery queries from [AGENT_ONBOARDING.md](AGENT_ONBOARDING.md) Phase 3
3. **Create profile** → Fill in `export_class_reclassify` and `name_prefixes` first
4. **Re-index** → `python index.py --all --plugins --force`
5. **Validate** → Check Unknown count decreased, semantic docs increased
6. **Refine** → Add `game_feature_types`, `deep_ref_*`, `widget_*` based on results
7. **Re-index** → Repeat until coverage is satisfactory

Each iteration improves classification. The most impactful keys to fill first:

1. `export_class_reclassify` — biggest impact on Unknown reduction
2. `name_prefixes` — catches assets missed by class detection
3. `game_feature_types` — unlocks typed edges (registers_widget, adds_component, etc.)
4. `deep_ref_export_classes` — ensures reference hubs are fully resolved
5. `data_asset_extractors` — optional, improves search quality for specific types
