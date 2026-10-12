#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "${1:-}" == "-t" ]]; then shift; fi
if [[ $# -eq 0 ]]; then
  echo 'Usage: bash release.sh [-t] v1.2.3 [--dry-run]' >&2
  exit 2
fi
rtk uv run --no-project python tools/release.py "$@" --publish
