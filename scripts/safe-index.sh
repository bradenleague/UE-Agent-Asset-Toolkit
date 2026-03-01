#!/usr/bin/env bash
set -euo pipefail

# Low-impact index wrapper for Unix-like development machines.
# - Low CPU priority via nice
# - Idle IO priority via ionice (if available)
# - Small default batch size unless overridden
#
# Examples:
#   scripts/safe-index.sh --quick --path UI
#   scripts/safe-index.sh --quick --quick-profile analysis --path Characters --batch-size 5
#   scripts/safe-index.sh --all --path /Game/UI --batch-size 10
#   UE_SAFE_MAX_ASSETS=200 scripts/safe-index.sh --quick --path /Game/Characters --types Blueprint
#   UE_SAFE_NON_RECURSIVE=1 scripts/safe-index.sh --quick --path /Game/UI

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

SAFE_BATCH_SIZE="${UE_SAFE_BATCH_SIZE:-5}"
SAFE_NICE="${UE_SAFE_NICE:-15}"
SAFE_IO_CLASS="${UE_SAFE_IO_CLASS:-3}" # 3 = idle
SAFE_PARSER_PARALLELISM="${UE_SAFE_PARSER_PARALLELISM:-2}"
PARSER_PARALLELISM="${UE_ASSETPARSER_MAX_PARALLELISM:-$SAFE_PARSER_PARALLELISM}"
SAFE_BATCH_TIMEOUT="${UE_SAFE_BATCH_TIMEOUT:-${UE_INDEX_BATCH_TIMEOUT:-600}}"
SAFE_ASSET_TIMEOUT="${UE_SAFE_ASSET_TIMEOUT:-${UE_INDEX_ASSET_TIMEOUT:-60}}"
SAFE_MAX_ASSETS="${UE_SAFE_MAX_ASSETS:-}"
SAFE_NON_RECURSIVE="${UE_SAFE_NON_RECURSIVE:-0}"

has_batch_size=false
has_timing=false
has_max_assets=false
has_recursive_flag=false
has_parser_parallelism=false
has_batch_timeout=false
has_asset_timeout=false

for arg in "$@"; do
  case "$arg" in
    --batch-size)
      has_batch_size=true
      ;;
    --timing)
      has_timing=true
      ;;
    --max-assets)
      has_max_assets=true
      ;;
    --non-recursive)
      has_recursive_flag=true
      ;;
    --parser-parallelism)
      has_parser_parallelism=true
      ;;
    --batch-timeout)
      has_batch_timeout=true
      ;;
    --asset-timeout)
      has_asset_timeout=true
      ;;
  esac
done

cmd=(python3 index.py "$@")
if [[ "$has_batch_size" == "false" ]]; then
  cmd+=(--batch-size "$SAFE_BATCH_SIZE")
fi
if [[ "$has_timing" == "false" ]]; then
  cmd+=(--timing)
fi
if [[ "$has_max_assets" == "false" && -n "$SAFE_MAX_ASSETS" ]]; then
  cmd+=(--max-assets "$SAFE_MAX_ASSETS")
fi
if [[ "$has_recursive_flag" == "false" && "$SAFE_NON_RECURSIVE" == "1" ]]; then
  cmd+=(--non-recursive)
fi
if [[ "$has_parser_parallelism" == "false" ]]; then
  cmd+=(--parser-parallelism "$PARSER_PARALLELISM")
fi
if [[ "$has_batch_timeout" == "false" ]]; then
  cmd+=(--batch-timeout "$SAFE_BATCH_TIMEOUT")
fi
if [[ "$has_asset_timeout" == "false" ]]; then
  cmd+=(--asset-timeout "$SAFE_ASSET_TIMEOUT")
fi

echo "Running low-impact index command:"
echo "  ionice class: $SAFE_IO_CLASS, nice: $SAFE_NICE"
echo "  parser parallelism: $PARSER_PARALLELISM"
echo "  batch timeout: $SAFE_BATCH_TIMEOUT s"
echo "  asset timeout: $SAFE_ASSET_TIMEOUT s"
printf '  %q ' "${cmd[@]}"
echo

if command -v ionice >/dev/null 2>&1; then
  exec ionice -c "$SAFE_IO_CLASS" nice -n "$SAFE_NICE" "${cmd[@]}"
else
  exec nice -n "$SAFE_NICE" "${cmd[@]}"
fi
