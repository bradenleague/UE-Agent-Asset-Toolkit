# Agent Onboarding Guide

Step-by-step instructions for an AI agent to onboard a new Unreal Engine project into the UE Asset Toolkit. This guide assumes you have CLI access and the toolkit is already built (via `setup.py`).

## Prerequisites

- .NET 8 SDK installed
- Python 3.10+ with dependencies installed (`pip install -e .`)
- AssetParser built (run `python setup.py` if not)
- The target `.uproject` file path

## Phase 1: Add the Project

```bash
# Add the project and set it as active
unreal-agent-toolkit add "/path/to/MyGame/MyGame.uproject"

# Verify it was added
unreal-agent-toolkit list
```

This creates an entry in `unreal_agent/config.json` with a derived name (e.g., `mygame`). The name is lowercase, derived from the `.uproject` filename.

You can override the name:

```bash
unreal-agent-toolkit add "/path/to/MyGame/MyGame.uproject" --name mygame
```

## Phase 2: Initial Index (No Profile)

Run the first index **without a profile**. This uses engine defaults only, which is enough to classify most standard UE asset types and discover what's project-specific.

```bash
unreal-agent-toolkit --plugins
```

This will:
1. Discover all `.uasset` files in `Content/` and `Plugins/`
2. Classify them by type using naming conventions and export class detection
3. Build the SQLite database at `unreal_agent/data/<project_name>.db`
4. Index semantic docs for known types (Blueprint, Widget, Material, DataTable, etc.)
5. Store lightweight records (path + refs) for everything else

Check what was indexed:

```bash
unreal-agent-toolkit --status
```

## Phase 3: Analyze the Index

Now query the database to understand what project-specific types exist. Open the DB directly:

```bash
sqlite3 unreal_agent/data/<project_name>.db
```

### 3.1 Find Unknown/Unclassified Assets

These are assets the engine defaults couldn't classify — they're your candidates for profile configuration.

```sql
-- Count assets by type, focusing on Unknown
SELECT asset_type, COUNT(*) as cnt
FROM lightweight_assets
GROUP BY asset_type
ORDER BY cnt DESC;
```

```sql
-- Find distinct export classes in Unknown assets
-- (requires re-running with inspect, or checking batch-summary output)
SELECT DISTINCT json_extract(metadata, '$.class') as class_name, COUNT(*) as cnt
FROM docs
WHERE asset_type = 'DataAsset' OR json_extract(metadata, '$.class') IS NOT NULL
GROUP BY class_name
ORDER BY cnt DESC;
```

### 3.2 Find Project-Specific Class Names

Look for classes that follow the project's naming pattern:

```sql
-- Find asset names with non-standard prefixes
SELECT name, asset_type, path
FROM lightweight_assets
WHERE asset_type = 'Unknown'
ORDER BY name
LIMIT 50;
```

```sql
-- Find semantic docs with class metadata
SELECT json_extract(metadata, '$.class') as class_name, COUNT(*) as cnt
FROM docs
WHERE json_extract(metadata, '$.class') IS NOT NULL
GROUP BY class_name
ORDER BY cnt DESC;
```

### 3.3 Identify Naming Conventions

```sql
-- Find common name prefixes in the project
SELECT
  SUBSTR(name, 1, INSTR(name, '_')) as prefix,
  COUNT(*) as cnt
FROM lightweight_assets
WHERE INSTR(name, '_') > 0
GROUP BY prefix
HAVING cnt >= 3
ORDER BY cnt DESC;
```

### 3.4 Find High-Value Data Assets

```sql
-- Find DataAsset subclasses with many references (these are worth custom extractors)
SELECT name, path,
  json_array_length(la."references") as ref_count
FROM lightweight_assets la
WHERE asset_type = 'DataAsset'
  AND json_array_length(la."references") > 3
ORDER BY ref_count DESC
LIMIT 20;
```

## Phase 4: Generate a Profile

Based on your analysis, create a profile JSON. See [AGENT_PROFILE_GUIDE.md](AGENT_PROFILE_GUIDE.md) for detailed instructions on each field.

Create the file at `unreal_agent/profiles/<project_name>.json`:

```json
{
  "profile_name": "mygame",
  "export_class_reclassify": {
    "GameFeatureData": "GameFeatureData",
    "MyGameAbilitySet": "DataAsset",
    "MyGamePawnData": "DataAsset"
  },
  "name_prefixes": {
    "MG_": "DataAsset"
  },
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

Then link it in `config.json`:

```json
{
  "active_project": "mygame",
  "projects": {
    "mygame": {
      "project_path": "/path/to/MyGame/MyGame.uproject",
      "profile": "mygame"
    }
  }
}
```

## Phase 5: Re-Index with Profile

```bash
# Force re-index to apply the new profile
unreal-agent-toolkit --plugins --force
```

The profile changes how assets are classified and which get semantic extraction. Force is needed to re-process already-indexed assets.

## Phase 6: Validate Coverage

```bash
unreal-agent-toolkit --status
```

Run these validation queries:

```sql
-- Check that Unknown count decreased
SELECT asset_type, COUNT(*) as cnt
FROM lightweight_assets
GROUP BY asset_type
ORDER BY cnt DESC;

-- Verify reclassified types have semantic docs
SELECT type, asset_type, COUNT(*) as cnt
FROM docs
GROUP BY type, asset_type
ORDER BY cnt DESC;

-- Check edge graph is populated
SELECT edge_type, COUNT(*) as cnt
FROM edges
GROUP BY edge_type
ORDER BY cnt DESC;
```

If Unknown assets remain that have identifiable classes, update the profile and re-index.

## Phase 7: Optional Enhancements

### Add Custom Data Asset Extractors

If the project has DataAsset subclasses with rich property data (abilities, pawns, configs), you can add custom extractors. See the existing Lyra extractors in `unreal_agent/knowledge_index/indexer.py` for the pattern:

```python
@data_asset_extractor("MyGameAbilitySet")
def _extract_my_ability_set(
    self, asset_name: str, class_name: str, props: list[dict]
) -> tuple[list[str], dict, dict[str, str]]:
    # Extract structured info from props list
    # Return (text_parts, metadata_dict, typed_refs_dict)
    ...
```

Then add the class name to `data_asset_extractors` in your profile JSON.

Without a custom extractor, the generic fallback still indexes property names and references — it just doesn't produce structured metadata.

### Add Vector Embeddings

For better semantic search quality:

```bash
pip install sentence-transformers
unreal-agent-toolkit --plugins --embed --force
```

### Index C++ Source

```bash
unreal-agent-toolkit --source
```

This indexes `UCLASS`, `UPROPERTY`, `UFUNCTION` macros from `Source/` and plugin C++ files.

## Quick Reference

| Command | Purpose |
|---------|---------|
| `unreal-agent-toolkit add <path>` | Register project |
| `unreal-agent-toolkit list` | Show all projects |
| `unreal-agent-toolkit use <name>` | Switch active project |
| `unreal-agent-toolkit --plugins` | Full index with plugins |
| `unreal-agent-toolkit --plugins --force` | Re-index everything |
| `unreal-agent-toolkit --profile quick` | Index high-value types only |
| `unreal-agent-toolkit --status` | Show index stats |
| `unreal-agent-toolkit --source` | Index C++ source |

## Troubleshooting

**"No project configured"** — Run `unreal-agent-toolkit add <path>` first.

**"Profile not found"** — Check that the profile JSON exists at `unreal_agent/profiles/<name>.json` and that `config.json` has `"profile": "<name>"` in the project entry.

**Many Unknown assets after indexing** — Normal on first pass without a profile. Analyze with Phase 3 queries and create a profile.

**FTS5 corruption errors** — Run `unreal-agent-toolkit --rebuild-fts`.
