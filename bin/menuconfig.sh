#!/usr/bin/env bash
set -u

script_dir="$(cd "$(dirname "$0")" && pwd)"
framework_dir="$(cd "$script_dir/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPYCACHEPREFIX
exec python3 "$script_dir/ui.py" "$@"
