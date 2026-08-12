#!/usr/bin/env bash

set -u
set -o pipefail

usage() {
    cat <<'EOF'
Usage: ./pack-all-plugins.sh [--with-deps]

Pack every Datus plugin in this repository into plugin-build/.

Options:
  --with-deps  Include all dependency wheels for offline installation.
  -h, --help   Show this help message.
EOF
}

with_deps=false
case "${1:-}" in
    "") ;;
    --with-deps) with_deps=true ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

if (( $# > 1 )); then
    usage >&2
    exit 2
fi

if ! command -v datus >/dev/null 2>&1; then
    echo "Error: datus is not installed or is not in PATH." >&2
    exit 127
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${repo_root}/plugin-build"
mkdir -p "$output_dir"

plugin_dirs=()
while IFS= read -r -d '' pyproject; do
    if grep -Fq '[project.entry-points."datus.plugins"]' "$pyproject"; then
        plugin_dirs+=("$(dirname "$pyproject")")
    fi
done < <(
    find "$repo_root" \
        \( -path "$repo_root/.git" \
        -o -path "$repo_root/.codex" \
        -o -path "$repo_root/.datus-e2e" \
        -o -path "$repo_root/.venv" \
        -o -path "$output_dir" \
        -o -name build \
        -o -name dist \) -prune \
        -o -name pyproject.toml -type f -print0
)

if (( ${#plugin_dirs[@]} == 0 )); then
    echo "Error: no Datus plugin projects found under $repo_root." >&2
    exit 1
fi

failed=()
for plugin_dir in "${plugin_dirs[@]}"; do
    relative_dir="${plugin_dir#"$repo_root"/}"
    echo "==> Packing ${relative_dir}"
    if [[ "$with_deps" == true ]]; then
        datus plugin pack "$plugin_dir" -o "$output_dir" --with-deps
    else
        datus plugin pack "$plugin_dir" -o "$output_dir"
    fi
    if (( $? != 0 )); then
        failed+=("$relative_dir")
    fi
done

echo
if (( ${#failed[@]} > 0 )); then
    echo "Failed to pack ${#failed[@]} of ${#plugin_dirs[@]} plugin(s):" >&2
    printf '  - %s\n' "${failed[@]}" >&2
    exit 1
fi

echo "Packed ${#plugin_dirs[@]} plugin(s) into $output_dir"
