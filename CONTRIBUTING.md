# Contributing

## Dev Setup

```bash
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit
cd UE-Agent-Asset-Toolkit
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
pip install -e ".[dev,embeddings]"
```

## Running Tests

```bash
pytest tests/ -v
```

227 tests covering search, indexing, profiles, data asset handlers, GameplayTag extraction, CLI option resolution, parser download, and inspector path resolution.

## Building the C# Parser Locally

```bash
# Build UAssetAPI first (submodule)
dotnet build UAssetAPI/UAssetAPI.sln -c Release

# Build AssetParser
dotnet build AssetParser/AssetParser.csproj -c Release
```

The parser binary ends up at `AssetParser/bin/Release/net8.0/<rid>/publish/AssetParser` (or `AssetParser.exe` on Windows). The `setup.py` script automates this.

## Adding Profiles and Data Asset Handlers

See [AGENT_PROFILE_GUIDE.md](AGENT_PROFILE_GUIDE.md) for the profile field reference.

To add a custom data asset extractor:

```python
@data_asset_extractor("MyClassName")
def _extract_my_class(self, asset_name, class_name, props):
    # Return (text_parts, metadata_dict, typed_refs_dict)
    ...
```

Register the class name in the project profile's `data_asset_extractors` list.

## Architecture Notes

### Mutable-Global Config Pattern

Several modules use function-level re-imports like:

```python
def some_function():
    from unreal_agent.core.config import PROJECT
    # use PROJECT...
```

This is intentional. `PROJECT` is a mutable global that changes when the user switches projects via `tools.set_active_project()`. A top-level import would capture the initial value and miss subsequent changes. Do not "optimize" these into top-level imports.

### Known Performance Notes

**N+1 subprocess in `_list_assets_filesystem`**: The filesystem asset discovery calls the parser once per directory. A future optimization would batch these into a single subprocess call.

**DB connection-per-query**: `KnowledgeStore` opens a new SQLite connection per query in the MCP server. This is simple and correct (each request is independent), but could be optimized with connection pooling for high-throughput scenarios.

## PR Process

1. Fork the repo and create a feature branch
2. Make your changes
3. Run `pytest tests/ -v` to verify all tests pass
4. Open a PR against `main`
