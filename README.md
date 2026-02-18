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

## Platform Notes

**Windows** (primary platform):
- PowerShell may require `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` to run `.ps1` wrapper scripts.
- Install .NET 8 SDK from the [official installer](https://dotnet.microsoft.com/download/dotnet/8.0).

**macOS**:
- Install .NET SDK via Homebrew: `brew install dotnet-sdk`
- Apple Silicon (M1/M2/M3/M4) is auto-detected — `dotnet run` uses the native `osx-arm64` runtime.

**Linux**:
- Install .NET SDK via your package manager or the [official instructions](https://learn.microsoft.com/dotnet/core/install/linux).

**Virtual environment** (all platforms):
```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r UnrealAgent/requirements.txt
```

## Quick Start

```bash
# 1. Clone the repository
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit
cd UE-Agent-Asset-Toolkit

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# 3. Run setup — builds the parser, installs Python deps, configures your project
python setup.py /path/to/YourProject.uproject

# 4. Build the search index
python index.py --all --plugins

# 5. Connect to your MCP client (see below)
```

`setup.py` handles building the C# parser (UAssetAPI + AssetParser), installing Python packages, and registering your `.uproject`. Add `--index` to combine steps 3 and 4.

You can also clone into your UE project as a subfolder:
```bash
cd YourProject
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit Tools
cd Tools && python setup.py ../YourProject.uproject --index
```

## MCP Client Setup

The toolkit runs as an MCP server. After building and indexing, connect it to your AI tool:

### Claude Code

Create a `.mcp.json` file in your working directory (or any parent directory):

```json
{
  "mcpServers": {
    "unreal": {
      "command": "/path/to/UE-Agent-Asset-Toolkit/.venv/bin/python",
      "args": ["/path/to/UE-Agent-Asset-Toolkit/UnrealAgent/mcp_server.py"]
    }
  }
}
```

Use absolute paths. Restart Claude Code after adding the config.

### Claude Desktop

Add to your Claude Desktop config:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "unreal": {
      "command": "python",
      "args": ["/path/to/UE-Agent-Asset-Toolkit/UnrealAgent/mcp_server.py"]
    }
  }
}
```

### Other MCP Clients

Any MCP-compatible client can connect — just point it at `UnrealAgent/mcp_server.py` via stdio transport.

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
| `--path UI` | Only index assets under a specific path (see note below) |
| `--force` | Re-index everything (ignore fingerprint cache) |
| `--status` | Show detailed index statistics |

**Path convention:** The `--path` option uses Unreal's virtual path convention where `/Game/` maps to your `Content/` folder. Use the folder name relative to Content:
```bash
python index.py --all --path UI          # indexes Content/UI/
python index.py --all --path UI/HUD      # indexes Content/UI/HUD/
python index.py --all --path /Game/UI    # same as above (explicit form)
```
Do NOT use filesystem paths like `C:\Projects\MyGame\Content\UI` - use the virtual path instead.

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
python index.py --all --path UI --force

# Rebuild just blueprints folder
python index.py --all --path Blueprints --force

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

## Project Profiles

Profiles configure project-specific asset types so the toolkit can classify and extract them correctly. Without a profile, the toolkit uses engine defaults — standard UE types like Blueprint, Widget, Material, DataTable work out of the box.

For project-specific types (custom DataAsset subclasses, experience definitions, ability sets, etc.), create a profile JSON:

```bash
# 1. Index with defaults first
python index.py --all --plugins

# 2. Create a profile based on what you find
#    See AGENT_PROFILE_GUIDE.md for field reference
cp UnrealAgent/profiles/_defaults.json UnrealAgent/profiles/mygame.json
#    Edit mygame.json with your project's types...

# 3. Link it in config.json (add "profile": "mygame" to your project entry)

# 4. Re-index with the profile
python index.py --all --plugins --force
```

Profiles live in `UnrealAgent/profiles/`. See:
- [AGENT_PROFILE_GUIDE.md](AGENT_PROFILE_GUIDE.md) — Profile field reference and discovery queries
- [AGENT_ONBOARDING.md](AGENT_ONBOARDING.md) — Full onboarding walkthrough
- [AGENTS.md](AGENTS.md) — Agent instructions (read automatically by AI coding agents)

## Known Limitations

- **Read-only**: AssetParser cannot modify assets
- **UE5.5+ compatibility**: Some newer assets may fail to parse
- **Type detection**: Uses naming conventions (BP_, WBP_, DT_) which may miss non-standard names — create a [profile](#project-profiles) to handle project-specific naming

## License

- **AssetParser/UnrealAgent**: MIT
- **UAssetAPI**: See [UAssetAPI/LICENSE](UAssetAPI/LICENSE)
