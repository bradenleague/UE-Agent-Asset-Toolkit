# AGENTS.md

You are working in the **UE Asset Toolkit** — an MCP server and indexer that helps AI agents understand Unreal Engine project codebases. It parses `.uasset` binary files, builds a semantic search index (SQLite + FTS5), and exposes two MCP tools: `unreal_search` and `inspect_asset`.

## Setup (Interactive)

If the user asks for help setting up, follow this flow. **The most important thing is to understand the user's situation before running any commands.** Do not assume anything about their Unreal projects, engine versions, or goals.

### 0. Understand What the User Needs

**Before touching anything**, have a conversation. Ask:

1. **What Unreal project(s) are you working on?** — Get the project name, what it is (game, prototype, sample project, etc.), and where it lives. Don't assume you know — the project may be on this machine, on a remote machine, on an external drive, or not cloned yet.
2. **What UE version?** — The toolkit works best with UE 5.0–5.4. UE 5.5+ has known parsing gaps. This affects expectations.
3. **What do you want to use this for?** — Understanding Blueprints? Searching for assets? Debugging a specific system? Onboarding onto an unfamiliar codebase? This shapes whether they need a full index, a quick index, source indexing, etc.
4. **Is the Editor available?** — The toolkit works *without* the Editor, but knowing whether they have it open or installed helps set context.

Do NOT skip this step. Do NOT silently search for `.uproject` files and assume that whatever you find is what the user wants. Ask them.

### 1. Check Prerequisites

```bash
dotnet --version    # Needs .NET 8+
python3 --version   # Needs 3.10+
```

If either is missing, help install them first:
- **Windows**: .NET SDK from the [official installer](https://dotnet.microsoft.com/download/dotnet/8.0). Python via python.org or winget.
- **macOS**: `brew install dotnet-sdk` and `brew install python` (or pyenv).
- **Linux**: Package manager or [official .NET instructions](https://learn.microsoft.com/dotnet/core/install/linux).

### 2. Clone and Build

```bash
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit
cd UE-Agent-Asset-Toolkit
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows
pip install -e .
```

Then run setup **with the user's project path** (from step 0):

```bash
python setup.py /path/to/Project.uproject
```

This builds UAssetAPI and AssetParser and registers the project. If the user hasn't given you a project path yet, **ask for it** — don't run `setup.py` without one unless the user explicitly just wants to build the parser.

### 3. Find the UE Project (only if the user doesn't know the path)

If the user says "I have a project but I'm not sure where it is", then search. Otherwise skip this — you should already have the path from step 0.

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

Present the results and let the user pick.

### 4. Run Initial Index

```bash
unreal-agent-toolkit --plugins
```

This indexes all assets with engine defaults. The `--plugins` flag includes Game Feature plugin content. For large projects this takes a few minutes.

If the user only cares about a specific area (e.g., "I'm working on the UI system"), consider a targeted index instead:

```bash
unreal-agent-toolkit --path UI --plugins
```

### 5. Check Results

```bash
unreal-agent-toolkit --status
```

Report the stats to the user: how many assets found, how many semantic docs, how many lightweight.

### 6. Set Up the MCP Client

The toolkit is only useful once it's connected to the user's AI tool. Ask which client they use and help configure it:

**Claude Code** — create `.mcp.json` in the working directory (or a parent directory):
```json
{
  "mcpServers": {
    "unreal": {
      "command": "unreal-agent-mcp"
    }
  }
}
```
The user needs to restart Claude Code for the MCP server to load. If the entry point isn't on PATH, use the full path: `/absolute/path/to/.venv/bin/unreal-agent-mcp`.

**Claude Desktop** — add to the app config:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "unreal": {
      "command": "unreal-agent-mcp"
    }
  }
}
```

### 7. Generate a Profile (Optional but Recommended)

After the initial index, analyze the database to create a project profile that improves classification of project-specific types. See [AGENT_PROFILE_GUIDE.md](AGENT_PROFILE_GUIDE.md) for the full reference.

Quick discovery queries against `unreal_agent/data/<project_name>.db`:

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

Use the results to create `unreal_agent/profiles/<project_name>.json`. Start from the template in AGENT_PROFILE_GUIDE.md. Then add `"profile": "<project_name>"` to the project entry in `unreal_agent/config.json` and re-index:

```bash
unreal-agent-toolkit --plugins --force
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
| `unreal-agent-toolkit add <path>` | Register a `.uproject` and set it active |
| `unreal-agent-toolkit use <name>` | Switch active project |
| `unreal-agent-toolkit list` | Show all configured projects |
| `unreal-agent-toolkit --plugins` | Full index with plugin content |
| `unreal-agent-toolkit --plugins --force` | Re-index everything (after profile changes) |
| `unreal-agent-toolkit --profile quick --plugins` | Fast index of high-value types only |
| `unreal-agent-toolkit --source` | Index C++ source files |
| `unreal-agent-toolkit --status` | Show index statistics |
| `unreal-agent-toolkit --embed` | Index with vector embeddings (needs `sentence-transformers`) |

## Testing

```bash
pytest tests/ -v
```

227 tests covering search, indexing, profiles, data asset handlers, GameplayTag extraction, CLI, and more.

## Project Structure

```
pyproject.toml                       # Python package config
setup.py / setup.bat / setup.sh      # Build toolkit
index.py                             # Backwards-compatible CLI shim

AssetParser/                         # C# binary .uasset parser
  Program.cs                         # Supports --type-config for project-specific types

unreal_agent/                        # Python package
  __init__.py                        # Package version
  mcp_server.py                      # MCP server (unreal_search, inspect_asset)
  cli.py                             # Index management CLI
  tools.py                           # Backend implementations
  parser_resolver.py                 # AssetParser binary resolution
  parser_download.py                 # GitHub Releases download fallback
  project_profile.py                 # Profile loading and merging
  config.json                        # Active project config (user-edited)

  profiles/
    _defaults.json                   # Engine-level type config (generic)
    <project>.json                   # Project-specific overrides
    .resolved/                       # Auto-generated merged configs (gitignored)

  knowledge_index/
    indexer.py                       # Asset indexing pipeline
    store.py                         # SQLite schema and queries
    schemas.py                       # DocChunk types

  search/
    engine.py                        # FTS5 + vector search
    retriever.py                     # Result enrichment
    trace.py                         # System trace builder

  data/
    <project_name>.db                # Per-project SQLite database
```

## Key Concepts

- **Profile system**: Project-specific types are configured in JSON profiles, not hardcoded. `_defaults.json` provides engine-level types, `<project>.json` overrides per-key. See [AGENT_PROFILE_GUIDE.md](AGENT_PROFILE_GUIDE.md).
- **Two-tier parsing**: C# AssetParser does fast binary parsing (~100ms/asset). Python indexer builds the semantic index on top.
- **Multi-project**: Each project gets its own isolated database. Switch with `unreal-agent-toolkit use <name>`.
- **Typed edges**: The index stores reference edges with types: `uses_asset`, `registers_widget`, `adds_component`, `maps_input`, `uses_layout`, `targets_actor`.
- **`references` is a reserved SQL word**: When querying `lightweight_assets`, use `la."references"` (quoted).
- **Mutable-global config pattern**: Function-level re-imports like `from unreal_agent.core.config import PROJECT` exist because `PROJECT` changes at runtime when switching projects. Do not move these to top-level imports.

## Platform Notes

The toolkit is cross-platform (Windows, macOS, Linux). Core Python code uses `pathlib` and `os.path` throughout. The C# parser builds as a self-contained binary for the current platform via `setup.py`.

- **Windows**: Use `setup.bat`, `index.bat`, or call `python` directly
- **macOS/Linux**: Use `setup.sh`, `index.sh`, or call `python3` directly

## Code Style

- Python: no strict linter enforced, but follow existing patterns
- Prefer `pathlib.Path` over string path manipulation
- Use `os.path.join()` when mixing with `os.walk()` results
- Subprocess calls use argument lists, never `shell=True`
