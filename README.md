# UE Asset Toolkit

AI-powered Unreal Engine asset inspection toolkit. Analyze Blueprints, Materials, Widgets, DataTables, and more without launching the Editor.

## Features

- **Zero-config setup** - auto-detects your `.uproject` and engine path
- **Fast binary parsing** (~100ms per asset vs 15-30s UE Python startup)
- **Works without Unreal Editor** for read operations
- **Semantic search** - natural language queries across assets AND C++ source code
- **MCP server** for Claude Desktop, Claude Code, and other MCP clients

## Requirements

- .NET 8 SDK (for AssetParser)
- Python 3.10+ (for indexer and MCP server)

## Quick Start

```bash
# Clone the repository
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit

# Or clone into your UE project as a subfolder
cd YourProject
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit Tools

# Run setup (from inside the cloned directory)
python setup.py                                    # Build tools only
python setup.py /path/to/Project.uproject          # Build + configure project
python setup.py /path/to/Project.uproject --index  # Build + configure + index

# Or use the wrapper scripts:
.\setup.bat C:\Projects\MyGame\MyGame.uproject     # Windows
./setup.sh ~/Projects/MyGame/MyGame.uproject       # macOS/Linux
```

## MCP Tools

The toolkit provides 2 MCP tools for use with Claude Desktop, Claude Code, and other MCP clients:

### `unreal_search`

Search assets and C++ source code with auto-detecting search modes:

```
- "BP_Player" → exact name match
- "player health widget" → semantic search
- "where is BP_Enemy used" → find references/placements
- "damage calculation" → find relevant code
```

### `inspect_asset`

Get detailed structured data about a specific asset:

- **Blueprint**: parent class, components, functions, variables, events
- **WidgetBlueprint**: widget tree hierarchy, bindings
- **Material**: parameters (scalar, vector, texture), domain
- **DataTable**: row structure, columns, sample data

## Claude Desktop Setup

Add to your Claude Desktop config:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "unreal": {
      "command": "python",
      "args": ["/path/to/YourProject/Tools/UnrealAgent/mcp_server.py"]
    }
  }
}
```

## Project Structure

```
YourProject/
├── YourProject.uproject
├── Content/
└── Tools/
    ├── setup.py              # Cross-platform setup script
    ├── setup.bat / setup.sh  # Wrapper scripts
    ├── index.py              # Indexing commands
    ├── index.bat / index.sh  # Wrapper scripts
    │
    ├── AssetParser/          # C# binary parser
    │   └── bin/Release/net8.0/AssetParser.exe
    │
    ├── UnrealAgent/          # Python indexer + MCP server
    │   ├── mcp_server.py     # MCP server (2 tools)
    │   ├── tools.py          # Backend implementations
    │   ├── knowledge_index/  # Semantic search index
    │   ├── config.json       # Project config (multi-project)
    │   └── data/             # Per-project databases
    │       ├── lyra.db
    │       └── mygame.db
    │
    └── UAssetAPI/            # .uasset parsing library (submodule)
```

## Architecture

Two-tier architecture:

1. **C# AssetParser** - Fast binary parsing of .uasset files
2. **Python MCP Server** - Runtime server with semantic search index

```
┌─────────────────────────────────────────────────────────────┐
│  MCP Client (Claude Desktop, Claude Code, etc.)             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  MCP Server (Python)                                        │
│  - unreal_search: FTS + vector search on {project}.db       │
│  - inspect_asset: calls AssetParser.exe                     │
└─────────────────────────────────────────────────────────────┘
                     │                    │
                     ▼                    ▼
┌─────────────────────────┐    ┌──────────────────────────────┐
│  data/{project}.db      │    │  AssetParser.exe (C#)        │
│  (SQLite + FTS5)        │    │  Parses .uasset binaries     │
└─────────────────────────┘    └──────────────────────────────┘
```

## Indexing

Build the semantic index from the repo root.

### Commands Reference

| Command | Description |
|---------|-------------|
| `--all` | Full hybrid index (semantic + lightweight for all assets) |
| `--all --plugins` | Full index including game feature plugins |
| `--all --plugins --embed` | Full index + vector embeddings (best quality) |
| `--quick` | Only WidgetBlueprint, DataTable, MaterialInstance |
| `--source` | C++ source files (UCLASS, UPROPERTY macros) |
| `--path /Game/UI` | Only index assets under a specific path |
| `--force` | Re-index everything (ignore fingerprint cache) |
| `--status` | Show detailed index statistics |

### Recommended Workflows

**Comprehensive (slowest):**
```bash
python index.py --all --plugins --embed --source
```
Full coverage: all assets, all plugins, vector embeddings for semantic search, and C++ source.

**Standard:**
```bash
python index.py --all --plugins
```
Full coverage without embeddings. FTS5 full-text search works well for most queries.

**Quick (fastest):**
```bash
python index.py --quick --plugins
```
Just the high-value types you search most often.

### Rebuilding Specific Sections

```bash
# Rebuild just UI assets
python index.py --all --path /Game/UI --force

# Rebuild just blueprints folder
python index.py --all --path /Game/Blueprints --force

# Rebuild C++ source only
python index.py --source --force
```

The `--force` flag bypasses the fingerprint cache that normally skips unchanged files.

**Note:** Incremental indexing is built-in. Without `--force`, unchanged files are skipped based on file hash.

### Wrapper Scripts
```bash
.\index.bat --all    # Windows
./index.sh --all     # macOS/Linux
```

## Manual Setup

If you prefer manual setup:

```bash
# Build UAssetAPI
cd Tools/UAssetAPI/UAssetAPI && dotnet build -c Release

# Build AssetParser
cd Tools/AssetParser && dotnet build -c Release

# Install Python dependencies
cd Tools/UnrealAgent && pip install -r requirements.txt

# Build index
cd Tools && python index.py --all
```

## Multi-Project Setup

The toolkit supports multiple projects with isolated per-project databases.

### Project Management Commands

```bash
# Add a project (sets it as active)
python index.py add "C:\Projects\MyGame\MyGame.uproject"
python index.py add ~/Projects/Lyra/Lyra.uproject --name lyra

# List all projects
python index.py list

# Switch active project
python index.py use lyra

# Index a specific project without switching
python index.py --all --project shootergame
```

### Per-Project Databases

Each project gets its own isolated database:
```
UnrealAgent/data/
├── lyra.db           # Lyra project index
├── mygame.db         # MyGame project index
└── shootergame.db    # ShooterGame project index
```

### Manual Configuration

You can also edit `Tools/UnrealAgent/config.json` directly:

```json
{
  "active_project": "lyra",
  "projects": {
    "lyra": {
      "project_path": "C:\\Projects\\Lyra\\Lyra.uproject",
      "engine_path": "C:\\Program Files\\Epic Games\\UE_5.4\\..."
    },
    "mygame": {
      "project_path": "D:\\Projects\\MyGame\\MyGame.uproject"
    }
  }
}
```

## Known Limitations

- **Read-only**: AssetParser cannot modify assets
- **UE5.5+ compatibility**: Some newer assets may fail to parse
- **Type detection**: Uses naming conventions (BP_, WBP_, DT_) which may miss non-standard names

## License

- **AssetParser/UnrealAgent**: MIT
- **UAssetAPI**: See [UAssetAPI/LICENSE](UAssetAPI/LICENSE)
