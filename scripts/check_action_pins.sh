#!/usr/bin/env bash
# Every third-party GitHub Action must be pinned to a full 40-character commit SHA, with the
# human-readable tag in a trailing comment: `uses: owner/action@<sha> # vX.Y.Z`.
# Mutable tags were the vector in real supply-chain incidents (V-016, V-021).
set -euo pipefail
cd "$(dirname "$0")/.."

status=0
while IFS= read -r line; do
  file="${line%%:*}"
  ref="$(sed -E 's/.*uses:[[:space:]]*["'"'"']?([^"'"'"'[:space:]]+).*/\1/' <<<"${line#*:*:}")"
  case "$ref" in
    ./*|docker://*) continue ;;
  esac
  if ! [[ "$ref" =~ @[0-9a-f]{40}$ ]]; then
    echo "not SHA-pinned: ${file}: ${ref}" >&2
    status=1
  fi
done < <(grep -rnE '^[[:space:]]*-?[[:space:]]*uses:' .github/workflows)

if (( status == 0 )); then
  echo "All actions are SHA-pinned."
fi
exit "$status"
