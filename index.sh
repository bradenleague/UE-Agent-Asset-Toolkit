#!/bin/bash
# UE Asset Toolkit - Index Management wrapper
# Usage:
#   ./index.sh              Show index status
#   ./index.sh --all        Full hybrid index
#   ./index.sh --quick      Quick index
#   ./index.sh --source     Index C++ source
#   ./index.sh --status     Detailed statistics

if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "ERROR: Python not found"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
$PYTHON_CMD "$SCRIPT_DIR/index.py" "$@"
