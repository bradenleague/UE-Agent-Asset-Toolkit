# AGENTS.md

You are working in the **UE Asset Toolkit** — an MCP server and indexer that helps AI agents understand Unreal Engine project codebases. It parses `.uasset` binary files, builds a semantic search index (SQLite + FTS5), and exposes two MCP tools: `unreal_search` and `inspect_asset`.

## Setup (Interactive)

If the user asks for help setting up, follow this flow:

### 1. Check Prerequisites

```bash
dotnet --version    # Needs .NET 8+
python3 --version   # Needs 3.10+
```

If either is missing, help install them first. On Windows, .NET is typically installed via the SDK installer. Python via python.org or winget.

### 2. Build the Toolkit

```bash
python setup.py
```

This builds UAssetAPI and AssetParser for the current platform. Cross-platform — works on Windows, macOS, and Linux.

### 3. Find the UE Project

Ask the user where their Unreal project is. If they don't know, search common locations:

**Windows:**
```powershell
Get-ChildItem -Path "D:\", "C:\Users\$env:USERNAME\Documents", "C:\Projects", "D:\Projects", "C:\Dev", "D:\Dev" -Filter "*.uproject" -Recurse -Depth 3 -ErrorAction SilentlyContinue
```

**macOS:**
```bash
find ~/Documents ~/Projects ~/Dev /Users/Shared/Epic\ Games -name "*.uproject" -maxdepth 4 2>/dev/null
```

**Linux:**
```bash
find ~/dev ~/projects ~/Documents -name "*.uproject" -maxdepth 4 2>/dev/null
```

Present the results and let the user pick, or accept a path directly.

### 4. Add the Project

```bash
python index.py add "/path/to/MyGame/MyGame.uproject"
```

This registers the project in `UnrealAgent/config.json` and sets it as active.

### 5. Run Initial Index

```bash
python index.py --all --plugins
```

This indexes all assets with engine defaults. It takes a few minutes depending on project size. The `--plugins` flag includes Game Feature plugin content.

### 6. Check Results

```bash
python index.py --status
```

Report the stats to the user: how many assets found, how many semantic docs, how many lightweight.

### 7. Generate a Profile (Optional but Recommended)

After the initial index, analyze the database to create a project profile that improves classification of project-specific types. See [AGENT_PROFILE_GUIDE.md](AGENT_PROFILE_GUIDE.md) for the full reference.

Quick discovery queries against `UnrealAgent/data/<project_name>.db`:

```sql
-- What types were found?
SELECT asset_type, COUNT(*) as cnt FROM lightweight_assets GROUP BY asset_type ORDER BY cnt DESC;

-- What export classes exist in DataAsset docs?
SELECT json_extract(metadata, '$.class') as cls, COUNT(*) as cnt
FROM docs WHERE json_extract(metadata, '$.class') IS NOT NULL
GROUP BY cls ORDER BY cnt DESC;

-- What name prefixes are common?
SELECT SUBSTR(name, 1, INSTR(name, '_')) as prefix, COUNT(*) as cnt
FROM lightweight_assets WHERE INSTR(name, '_') > 1
GROUP BY prefix HAVING cnt >= 3 ORDER BY cnt DESC LIMIT 20;
```

Use the results to create `UnrealAgent/profiles/<project_name>.json`. Start from the template in AGENT_PROFILE_GUIDE.md. Then add `"profile": "<project_name>"` to the project entry in `UnrealAgent/config.json` and re-index:

```bash
python index.py --all --plugins --force
```

## Build Commands

| Command | Description |
|---------|-------------|
| `python setup.py` | Build AssetParser and install dependencies |
| `python setup.py /path/to/Game.uproject` | Build + register a project |
| `python setup.py /path/to/Game.uproject --index` | Build + register + index |

## Index Commands

| Command | Description |
|---------|-------------|
| `python index.py add <path>` | Register a `.uproject` and set it active |
| `python index.py use <name>` | Switch active project |
| `python index.py list` | Show all configured projects |
| `python index.py --all --plugins` | Full index with plugin content |
| `python index.py --all --plugins --force` | Re-index everything (after profile changes) |
| `python index.py --quick --plugins` | Fast index of high-value types only |
| `python index.py --source` | Index C++ source files |
| `python index.py --status` | Show index statistics |
| `python index.py --all --embed` | Index with vector embeddings (needs `sentence-transformers`) |

## Testing

```bash
cd UnrealAgent && python -m pytest ../tests/ -v
```

45 tests covering the profile system, data asset handlers, GameplayTag extraction, and fuzzy matching.

## Project Structure

```
setup.py / setup.bat / setup.sh     # Build toolkit
index.py / index.bat / index.sh     # Index management CLI

AssetParser/                         # C# binary .uasset parser
  Program.cs                         # Supports --type-config for project-specific types

UnrealAgent/
  mcp_server.py                      # MCP server (unreal_search, inspect_asset)
  tools.py                           # Backend implementations
  project_profile.py                 # Profile loading and merging
  config.json                        # Active project config (user-edited)
  config.example.json                # Template

  profiles/
    _defaults.json                   # Engine-level type config (generic)
    <project>.json                   # Project-specific overrides
    .resolved/                       # Auto-generated merged configs (gitignored)

  knowledge_index/
    indexer.py                       # Asset indexing pipeline
    store.py                         # SQLite schema and queries
    retriever.py                     # FTS5 + vector search
    schemas.py                       # DocChunk types

  data/
    <project_name>.db                # Per-project SQLite database
```

## Key Concepts

- **Profile system**: Project-specific types are configured in JSON profiles, not hardcoded. `_defaults.json` provides engine-level types, `<project>.json` overrides per-key. See [AGENT_PROFILE_GUIDE.md](AGENT_PROFILE_GUIDE.md).
- **Two-tier parsing**: C# AssetParser does fast binary parsing (~100ms/asset). Python indexer builds the semantic index on top.
- **Multi-project**: Each project gets its own isolated database. Switch with `python index.py use <name>`.
- **Typed edges**: The index stores reference edges with types: `uses_asset`, `registers_widget`, `adds_component`, `maps_input`, `uses_layout`, `targets_actor`.
- **`references` is a reserved SQL word**: When querying `lightweight_assets`, use `la."references"` (quoted).

## Platform Notes

The toolkit is cross-platform (Windows, macOS, Linux). Core Python code uses `pathlib` and `os.path` throughout. The C# parser builds as a self-contained binary for the current platform via `setup.py`.

- **Windows**: Use `setup.bat`, `index.bat`, or call `python` directly
- **macOS/Linux**: Use `setup.sh`, `index.sh`, or call `python3` directly

## Code Style

- Python: no strict linter enforced, but follow existing patterns
- Prefer `pathlib.Path` over string path manipulation
- Use `os.path.join()` when mixing with `os.walk()` results
- Subprocess calls use argument lists, never `shell=True`
