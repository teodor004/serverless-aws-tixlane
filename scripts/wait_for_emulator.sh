#!/usr/bin/env bash
# Poll the emulator's health endpoint until it answers (health paths: V-002, V-004).
set -euo pipefail

emulator="${EMULATOR:-floci}"
endpoint="${EMULATOR_ENDPOINT:-http://localhost:4566}"
timeout="${EMULATOR_WAIT_SECONDS:-120}"

case "$emulator" in
  floci) path="/_floci/health" ;;
  ministack) path="/_ministack/health" ;;
  *) echo "unknown EMULATOR '$emulator' (floci|ministack)" >&2; exit 2 ;;
esac

deadline=$((SECONDS + timeout))
until curl -fsS --max-time 2 "${endpoint}${path}" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "${emulator} not healthy at ${endpoint}${path} after ${timeout}s" >&2
    exit 1
  fi
  sleep 2
done
echo "${emulator} healthy at ${endpoint}${path}"
