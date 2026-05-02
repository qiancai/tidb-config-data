#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/collect-scope.sh [options]

Options:
  --scope FILE          Scope JSON. Defaults to mvp-versions.json.
  --output-root DIR     Output root. Defaults to the repository root.
  --force               Overwrite each version output directory.
  --only-missing        Skip versions whose output directory already exists.
  --continue-on-error   Continue with later versions if one version fails.
  --dry-run             Print the versions and commands without running them.
  --workdir DIR         TiUP playground working directory.
  -h, --help            Show this help.

The script starts and stops one playground per version via collect-version.sh.
It intentionally does not reuse a running cluster across versions.
USAGE
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scope_file="${repo_root}/mvp-versions.json"
output_root="${repo_root}"
force=0
only_missing=0
continue_on_error=0
dry_run=0
workdir="${HOME}/Documents/for-testing"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)
      scope_file="$2"
      shift
      ;;
    --output-root)
      output_root="$2"
      shift
      ;;
    --force)
      force=1
      ;;
    --only-missing)
      only_missing=1
      ;;
    --continue-on-error)
      continue_on_error=1
      ;;
    --dry-run)
      dry_run=1
      ;;
    --workdir)
      workdir="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

if [[ ! -f "${scope_file}" ]]; then
  echo "scope file not found: ${scope_file}" >&2
  exit 1
fi

versions=()
while IFS= read -r version; do
  versions+=("${version}")
done < <(python3 - "${scope_file}" <<'PY'
import json
import sys
from pathlib import Path

scope = json.loads(Path(sys.argv[1]).read_text())
for item in scope["versions"]:
    print(item["version"])
PY
)

if [[ "${#versions[@]}" -eq 0 ]]; then
  echo "no versions in scope: ${scope_file}" >&2
  exit 1
fi

echo "scope: ${scope_file}"
echo "versions (${#versions[@]}): ${versions[*]}"

failed=()
for version in "${versions[@]}"; do
  out_dir="${output_root}/${version}"
  if [[ "${only_missing}" -eq 1 && -d "${out_dir}" ]]; then
    echo "skip existing: ${version}"
    continue
  fi

  cmd=("${repo_root}/scripts/collect-version.sh" "${version}" --output-root "${output_root}" --workdir "${workdir}")
  if [[ "${force}" -eq 1 ]]; then
    cmd+=(--force)
  fi

  if [[ "${dry_run}" -eq 1 ]]; then
    printf 'dry-run:'
    printf ' %q' "${cmd[@]}"
    printf '\n'
    continue
  fi

  echo "collecting ${version}"
  if ! "${cmd[@]}"; then
    failed+=("${version}")
    echo "failed: ${version}" >&2
    if [[ "${continue_on_error}" -eq 0 ]]; then
      break
    fi
  fi
done

if [[ "${#failed[@]}" -gt 0 ]]; then
  echo "failed versions: ${failed[*]}" >&2
  exit 1
fi
