#!/usr/bin/env bash
# Fail while any tool version is still unverified (PIN_ME). See docs/VERIFICATION_LOG.md.
set -euo pipefail
cd "$(dirname "$0")/.."

if matches=$(grep -nE '^[^#]*PIN_ME' versions.env .tflint.hcl); then
  echo "Unverified versions remain - verify, pin, and log them in docs/VERIFICATION_LOG.md:" >&2
  echo "$matches" >&2
  exit 1
fi
echo "All tool versions pinned."
