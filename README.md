# UE Asset Toolkit

[![Tests](https://github.com/bradenleague/UE-Agent-Asset-Toolkit/actions/workflows/test.yml/badge.svg)](https://github.com/bradenleague/UE-Agent-Asset-Toolkit/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/unreal-agent-toolkit)](https://pypi.org/project/unreal-agent-toolkit/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

AI-powered Unreal Engine asset inspection toolkit. Analyze Blueprints, Materials, Widgets, DataTables, and more without launching the Editor.

## Features

- **Zero-config setup** - auto-detects your `.uproject` and engine path
- **Fast binary parsing** (~100ms per asset vs 15-30s UE Python startup)
- **Works without Unreal Editor** for read operations
- **Semantic search** - natural language queries across assets AND C++ source code
- **MCP server** for Claude Desktop, Claude Code, and other MCP clients

## Requirements

- .NET 8 SDK or later (for AssetParser)
- Python 3.10+ (for indexer and MCP server)

## Installation

### Recommended: Clone + editable install

```bash
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit
cd UE-Agent-Asset-Toolkit
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows
pip install -e .
```

This installs two CLI entry points:
- `unreal-agent-toolkit` — index management CLI
- `unreal-agent-mcp` — MCP server

### From PyPI

```bash
pip install unreal-agent-toolkit
```

Note: the C# AssetParser binary is downloaded automatically from GitHub Releases on first use. For manual builds, see [CONTRIBUTING.md](CONTRIBUTING.md).

### Optional dependencies

```bash
pip install -e ".[embeddings]"  # vector embeddings (sentence-transformers)
pip install -e ".[dev]"         # pytest + coverage
```

## Platform Notes

**Windows** (primary platform):
- PowerShell may require `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` to run `.ps1` wrapper scripts.
- Install .NET 8 SDK or later from the [official installer](https://dotnet.microsoft.com/download/dotnet/8.0).

**macOS**:
- Install .NET SDK via Homebrew: `brew install dotnet-sdk`
- Apple Silicon (M1/M2/M3/M4) is auto-detected — `dotnet run` uses the native `osx-arm64` runtime.

**Linux**:
- Install .NET SDK via your package manager or the [official instructions](https://learn.microsoft.com/dotnet/core/install/linux).

## Quick Start

```bash
# 1. Clone and install (see Installation above)

# 2. Run setup — builds the parser, configures your project
python setup.py /path/to/YourProject.uproject

# 3. Build the search index
unreal-agent-toolkit
# Or with plugins: unreal-agent-toolkit --plugins

# 4. Connect to your MCP client (see below)
```

Note: the `UAssetAPI` submodule is about 70MB, so the initial clone can take a minute on slower connections.

`setup.py` handles building the C# parser (UAssetAPI + AssetParser), installing Python packages, and registering your `.uproject`. Add `--index` to combine steps 2 and 3.

You can also clone into your UE project as a subfolder:
```bash
cd YourProject
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit Tools
cd Tools && python setup.py ../YourProject.uproject --index
```

## What Can I Ask?

Once indexed and connected via MCP, try queries like:

| Query | What happens |
|-------|-------------|
| `"BP_Player"` | Exact name match — finds the Blueprint |
| `"player health widget"` | Semantic search across all indexed assets |
| `"where is BP_Enemy used"` | Find all references and placements |
| `"damage calculation"` | Find relevant Blueprints and C++ code |
| `"inherits:LyraGameplayAbility"` | Find all children of a class |
| `"tag:Ability.Melee.*"` | Find assets with matching GameplayTags |

Use `inspect_asset` on any result to get full structured data (components, functions, variables, widget trees, material parameters, etc.).

## MCP Client Setup

The toolkit runs as an MCP server over stdio. After building and indexing, register it in your MCP client config.

### After `pip install -e .` (recommended)

```json
{
  "mcpServers": {
    "unreal": {
      "command": "unreal-agent-mcp"
    }
  }
}
```

### From source (without pip install)

```json
{
  "mcpServers": {
    "unreal": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/UE-Agent-Asset-Toolkit/unreal_agent/mcp_server.py"]
    }
  }
}
```

Windows form:

```json
{
  "mcpServers": {
    "unreal": {
      "command": "C:\\absolute\\path\\to\\.venv\\Scripts\\python.exe",
      "args": ["C:\\absolute\\path\\to\\UE-Agent-Asset-Toolkit\\unreal_agent\\mcp_server.py"]
    }
  }
}
```

Client configs differ, so always verify in your client's MCP docs:
- config file location
- exact JSON key names/shape
- whether a restart is required to load new MCP servers

See [`mcp-config.example.json`](mcp-config.example.json) for a copy-paste template.

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

## Project Structure

```
UE-Agent-Asset-Toolkit/
├── pyproject.toml            # Python package config
├── setup.py                  # Cross-platform setup script
├── index.py                  # Backwards-compatible CLI shim
│
├── AssetParser/              # C# binary parser
│   └── bin/Release/net8.0/AssetParser
│
├── unreal_agent/             # Python package
│   ├── __init__.py           # Package version
│   ├── mcp_server.py         # MCP server (2 tools)
│   ├── cli.py                # Index management CLI
│   ├── tools.py              # Backend implementations
│   ├── parser_resolver.py    # AssetParser binary resolution
│   ├── parser_download.py    # GitHub Releases download fallback
│   ├── project_profile.py    # Profile loading and merging
│   ├── config.json           # Project config (multi-project)
│   │
│   ├── knowledge_index/      # Semantic search index
│   │   ├── indexer.py        # Asset indexing pipeline
│   │   ├── store.py          # SQLite schema and queries
│   │   └── schemas.py        # DocChunk types
│   │
│   ├── search/               # Search engine
│   │   ├── engine.py         # FTS5 + vector search
│   │   ├── retriever.py      # Result enrichment
│   │   └── trace.py          # System trace builder
│   │
│   ├── profiles/             # Project profiles
│   │   ├── _defaults.json    # Engine-level types
│   │   └── <project>.json    # Project-specific overrides
│   │
│   └── data/                 # Per-project databases
│       └── <project>.db
│
├── tests/                    # 227 tests
└── UAssetAPI/                # .uasset parsing library (submodule)
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
│  - inspect_asset: calls AssetParser                         │
└─────────────────────────────────────────────────────────────┘
                     │                    │
                     ▼                    ▼
┌─────────────────────────┐    ┌──────────────────────────────┐
│  data/{project}.db      │    │  AssetParser (C#)             │
│  (SQLite + FTS5)        │    │  Parses .uasset binaries      │
└─────────────────────────┘    └──────────────────────────────┘
```

## Indexing

Build the semantic index using the CLI.

### Commands Reference

| Command | Description |
|---------|-------------|
| `unreal-agent-toolkit` | Full hybrid index (default) |
| `unreal-agent-toolkit --profile quick` | Only WidgetBlueprint, DataTable, MaterialInstance |
| `unreal-agent-toolkit --plugins` | Full index including plugin content |
| `unreal-agent-toolkit --plugins --embed` | Full index + vector embeddings |
| `unreal-agent-toolkit --source` | C++ source files (UCLASS, UPROPERTY macros) |
| `unreal-agent-toolkit --path UI` | Only index assets under a specific path |
| `unreal-agent-toolkit --force` | Re-index everything (ignore fingerprint cache) |
| `unreal-agent-toolkit --status` | Show detailed index statistics |

**Path convention:** The `--path` option uses Unreal's virtual path convention where `/Game/` maps to your `Content/` folder:
```bash
unreal-agent-toolkit --path UI          # indexes Content/UI/
unreal-agent-toolkit --path UI/HUD      # indexes Content/UI/HUD/
unreal-agent-toolkit --path /Game/UI    # same as above (explicit form)
```
Do NOT use filesystem paths — use the virtual path instead.

**Backwards compatibility:** `python index.py` still works as a shim for `unreal-agent-toolkit`.

### Recommended Workflows

**Comprehensive (slowest):**
```bash
unreal-agent-toolkit --plugins --embed --source
```
Full coverage: all assets, all plugins, vector embeddings for semantic search, and C++ source.

**Standard (recommended for most projects):**
```bash
unreal-agent-toolkit
```
Full coverage without embeddings. FTS5 full-text search works well for most queries.

**Quick (fastest):**
```bash
unreal-agent-toolkit --profile quick --plugins
```
Just the high-value types you search most often.

## Multi-Project Setup

The toolkit supports multiple projects with isolated per-project databases.

```bash
# Add a project (sets it as active)
unreal-agent-toolkit add "/path/to/MyGame/MyGame.uproject"

# List all projects
unreal-agent-toolkit list

# Switch active project
unreal-agent-toolkit use lyra

# Index a specific project without switching
unreal-agent-toolkit --project shootergame
```

Each project gets its own isolated database at `unreal_agent/data/<project_name>.db`.

## Project Profiles

Profiles configure project-specific asset types so the toolkit can classify and extract them correctly. Without a profile, the toolkit uses engine defaults — standard UE types like Blueprint, Widget, Material, DataTable work out of the box.

For project-specific types (custom DataAsset subclasses, experience definitions, ability sets, etc.), create a profile JSON:

```bash
# 1. Index with defaults first
unreal-agent-toolkit --plugins

# 2. Create a profile based on what you find
cp unreal_agent/profiles/_defaults.json unreal_agent/profiles/mygame.json

# 3. Link it in config.json (add "profile": "mygame" to your project entry)

# 4. Re-index with the profile
unreal-agent-toolkit --plugins --force
```

Profiles live in `unreal_agent/profiles/`. See:
- [AGENT_PROFILE_GUIDE.md](AGENT_PROFILE_GUIDE.md) — Profile field reference and discovery queries
- [AGENT_ONBOARDING.md](AGENT_ONBOARDING.md) — Full onboarding walkthrough
- [AGENTS.md](AGENTS.md) — Agent instructions (read automatically by AI coding agents)

## Known Limitations

- **Read-only**: AssetParser cannot modify assets
- **UE5.5+ compatibility**: Some newer assets may fail to parse
- **Type detection**: Uses naming conventions (BP_, WBP_, DT_) which may miss non-standard names — create a [profile](#project-profiles) to handle project-specific naming
- **Windows console encoding**: Some terminals using cp1252 can raise `UnicodeEncodeError` at the final completion line. Workaround: set UTF-8 before indexing (`$env:PYTHONUTF8=1` in PowerShell or `set PYTHONUTF8=1` in cmd.exe).
- **RTX 50-series + torch on Windows**: Verified working with `torch==2.8.0+cu128` (CUDA) on RTX 5080. If embedding setup pulls an incompatible torch build, install:
  `pip install "torch==2.8.0+cu128" --index-url https://download.pytorch.org/whl/cu128`

## License

- **AssetParser/unreal_agent**: MIT
- **UAssetAPI**: See [UAssetAPI/LICENSE](UAssetAPI/LICENSE)
