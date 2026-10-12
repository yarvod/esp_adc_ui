#!/usr/bin/env bash
set -euo pipefail
echo 'Release builds are Windows-only. Run build-win.bat on Windows with x86 or x64 Python, or use GitHub Actions.' >&2
exit 1
