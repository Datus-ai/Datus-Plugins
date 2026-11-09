#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./pack-plugin.sh <plugin-directory> [-o output-directory] [--without-deps]

Build a plugin ZIP with all dependency wheels for offline installation.
The default output directory is plugin-build/ at the repository root.

Options:
  -o, --output    Override the output directory.
  --with-deps     Include dependency wheels (the default).
  --without-deps  Build a plugin-only ZIP; installation requires network access.
  -h, --help      Show this help message.

Dependency wheels target the build environment's OS, CPU, and Python version.
Build in the deployment environment when preparing a server release.
EOF
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
output_dir="$repo_root/plugin-build"
plugin_dir=""
with_deps=true

while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --with-deps) with_deps=true; shift ;;
        --without-deps) with_deps=false; shift ;;
        -o|--output)
            if (( $# < 2 )) || [[ -z "$2" ]]; then
                echo "Error: $1 requires an output directory." >&2
                exit 2
            fi
            output_dir="$2"
            shift 2
            ;;
        -*) usage >&2; exit 2 ;;
        *)
            if [[ -n "$plugin_dir" ]]; then
                usage >&2
                exit 2
            fi
            plugin_dir="$1"
            shift
            ;;
    esac
done

if [[ -z "$plugin_dir" || ! -f "$plugin_dir/pyproject.toml" ]]; then
    echo "Error: specify a plugin directory containing pyproject.toml." >&2
    exit 2
fi
if ! grep -Fq '[project.entry-points."datus.plugins"]' "$plugin_dir/pyproject.toml"; then
    echo "Error: $plugin_dir does not declare a Datus plugin entry point." >&2
    exit 2
fi
if ! command -v datus >/dev/null 2>&1; then
    echo "Error: datus is not installed or is not in PATH." >&2
    exit 127
fi

if [[ "$with_deps" == true ]]; then
    datus plugin pack "$plugin_dir" -o "$output_dir" --with-deps
else
    datus plugin pack "$plugin_dir" -o "$output_dir"
fi
