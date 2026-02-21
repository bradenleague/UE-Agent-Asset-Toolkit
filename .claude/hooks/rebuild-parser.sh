#!/bin/bash
# PostToolUse hook: rebuild AssetParser when C# sources change
# Runs the full chain: UAssetAPI → AssetParser → self-contained publish

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only trigger on .cs or .csproj files in AssetParser/ or UAssetAPI/
if [[ "$FILE_PATH" =~ (AssetParser|UAssetAPI)/.+\.(cs|csproj)$ ]]; then
    DOTNET="$HOME/.dotnet/dotnet"
    PROJECT_DIR="/home/bradenleague/dev/UE-Agent-Asset-Toolkit"

    echo "Rebuilding AssetParser (triggered by $FILE_PATH)..." >&2

    # Build UAssetAPI first (dependency)
    "$DOTNET" build "$PROJECT_DIR/UAssetAPI/UAssetAPI/UAssetAPI.csproj" -c Release --nologo -v q 2>&1 | tail -1 >&2
    if [ $? -ne 0 ]; then
        echo "UAssetAPI build failed" >&2
        exit 0
    fi

    # Publish self-contained AssetParser (what the MCP server actually uses)
    "$DOTNET" publish "$PROJECT_DIR/AssetParser/AssetParser.csproj" -c Release -r linux-x64 --self-contained --nologo -v q 2>&1 | tail -1 >&2
    if [ $? -ne 0 ]; then
        echo "AssetParser publish failed" >&2
        exit 0
    fi

    echo "AssetParser rebuilt successfully" >&2
fi

exit 0
