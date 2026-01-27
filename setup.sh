#!/bin/bash
# UE Asset Toolkit Setup - Unix wrapper
# This script just calls setup.py with Python
#
# Usage:
#   ./setup.sh                              Build only
#   ./setup.sh /path/to/Project.uproject    Build + configure project
#   ./setup.sh /path/to/Project.uproject --index  Build + configure + index
#   ./setup.sh --help                       Show help

# Find Python
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "ERROR: Python not found. Install Python 3.10+"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
$PYTHON_CMD "$SCRIPT_DIR/setup.py" "$@"
