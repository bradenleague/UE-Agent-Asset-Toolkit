# Contributing

## Dev Setup

```bash
git clone --recursive https://github.com/bradenleague/UE-Agent-Asset-Toolkit
cd UE-Agent-Asset-Toolkit
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
pip install -e ".[dev]"
```

## Building the C# Parser

```bash
dotnet build UAssetAPI/UAssetAPI.sln -c Release
dotnet build AssetParser/AssetParser.csproj -c Release
```

Or use `setup.py` which handles both automatically.

## Running Tests

```bash
pytest tests/ -v
```

## PR Process

1. Fork the repo and create a feature branch
2. Run `pytest tests/ -v` to verify all tests pass
3. Open a PR against `main`
