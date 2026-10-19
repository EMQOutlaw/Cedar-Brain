#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export QT_QUICK_CONTROLS_STYLE="${QT_QUICK_CONTROLS_STYLE:-Basic}"
exec qs -n -p "$repo_dir/cockpit" "$@"
