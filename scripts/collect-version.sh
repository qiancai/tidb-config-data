#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/collect-version.sh <version> [options]

Options:
  --reuse-running      Collect from the cluster already listening on local default ports.
  --output-root DIR    Output root. Defaults to the repository root.
  --no-sanitize        Keep local paths and 127.0.0.1 values in the capture.
  --force              Overwrite the version output directory.
  --stop-after         Stop the playground after collection. Default when this script starts it.
  --tag TAG            TiUP playground tag. Defaults to tidb-v<digits>.
  --workdir DIR        TiUP playground working directory.
  -h, --help           Show this help.
USAGE
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-}"
if [[ -z "${version}" || "${version}" == "-h" || "${version}" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

output_root="${repo_root}"
sanitize=1
force=0
reuse_running=0
stop_after=""
tag=""
workdir="${HOME}/Documents/for-testing"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reuse-running)
      reuse_running=1
      ;;
    --output-root)
      output_root="$2"
      shift
      ;;
    --no-sanitize)
      sanitize=0
      ;;
    --force)
      force=1
      ;;
    --stop-after)
      stop_after=1
      ;;
    --tag)
      tag="$2"
      shift
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

version_no_v="${version#v}"
version="v${version_no_v}"
if [[ -z "${tag}" ]]; then
  tag="tidb-v$(echo "${version_no_v}" | tr -d '.-')"
fi
if [[ -z "${stop_after}" ]]; then
  if [[ "${reuse_running}" -eq 1 ]]; then
    stop_after=0
  else
    stop_after=1
  fi
fi

wait_cmd() {
  local name="$1"
  local timeout="$2"
  shift 2
  local start
  start="$(date +%s)"
  while true; do
    if run_with_timeout 8 "$@" >/dev/null 2>&1; then
      echo "ready: ${name}"
      return 0
    fi
    if (( $(date +%s) - start > timeout )); then
      echo "timeout waiting for ${name}" >&2
      return 1
    fi
    sleep 2
  done
}

run_with_timeout() {
  local timeout="$1"
  shift
  perl -e 'alarm shift @ARGV; exec @ARGV' "${timeout}" "$@"
}

default_ports=(4000 10080 2379 20180 3930 20292)

port_lsof() {
  local args=()
  local port
  for port in "${default_ports[@]}"; do
    args+=("-iTCP:${port}")
  done
  lsof -nP "${args[@]}" -sTCP:LISTEN
}

wait_ports_free() {
  local timeout="$1"
  local start
  start="$(date +%s)"
  while true; do
    if ! port_lsof >/dev/null 2>&1; then
      return 0
    fi
    if (( $(date +%s) - start > timeout )); then
      echo "timeout waiting for default ports to be free: ${default_ports[*]}" >&2
      port_lsof >&2 || true
      return 1
    fi
    sleep 1
  done
}

tag_pids() {
  ps -axo pid=,comm=,command= | awk -v tag="${tag}" '
    {
      pid=$1
      comm=$2
      command=$0
      sub(/^[[:space:]]*[0-9]+[[:space:]]+[^[:space:]]+[[:space:]]+/, "", command)
      is_playground = comm ~ /^(tiup|tidb-server|tikv-server|pd-server|tiflash|tiflash-proxy)$/
      has_tag = index(command, "--tag " tag) || index(command, "--tag=" tag) || index(command, "/.tiup/data/" tag "/")
      if (is_playground && has_tag) {
        print pid
      }
    }
  ' || true
}

kill_tag_processes() {
  local signal="$1"
  local pids
  pids="$(tag_pids | tr '\n' ' ')"
  if [[ -n "${pids// }" ]]; then
    kill "-${signal}" ${pids} >/dev/null 2>&1 || true
  fi
}

ensure_components_installed() {
  echo "ensuring TiUP components for ${version}"
  tiup install "pd:${version}" "tikv:${version}" "tidb:${version}" "tiflash:${version}"
}

verify_component_binary() {
  local component="$1"
  local binary="$2"
  shift 2

  if [[ ! -x "${binary}" ]] || ! run_with_timeout 8 "${binary}" "$@" >/dev/null 2>&1; then
    echo "repairing TiUP component ${component}:${version}"
    tiup install --force "${component}:${version}"
  fi
}

repair_component_binaries() {
  verify_component_binary pd "${HOME}/.tiup/components/pd/${version}/pd-server" --version
  verify_component_binary tikv "${HOME}/.tiup/components/tikv/${version}/tikv-server" --version
  verify_component_binary tidb "${HOME}/.tiup/components/tidb/${version}/tidb-server" -V
  verify_component_binary tiflash "${HOME}/.tiup/components/tiflash/${version}/tiflash/tiflash" --version
}

sign_darwin_binaries() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi

  local component_dir file
  for component_dir in \
    "${HOME}/.tiup/components/pd/${version}" \
    "${HOME}/.tiup/components/tikv/${version}" \
    "${HOME}/.tiup/components/tidb/${version}" \
    "${HOME}/.tiup/components/tiflash/${version}"; do
    [[ -d "${component_dir}" ]] || continue
    while IFS= read -r file; do
      if file "${file}" | grep -q 'Mach-O'; then
        if command -v upx >/dev/null 2>&1; then
          upx -q -d "${file}" >/dev/null 2>&1 || true
        fi
        codesign -s - --force "${file}" >/dev/null 2>&1 || true
      fi
    done < <(find "${component_dir}" -type f -perm -111)
  done
}

clear_existing_playground() {
  screen -S "${tag}" -X quit >/dev/null 2>&1 || true
  kill_tag_processes TERM

  local i
  for i in {1..10}; do
    if [[ -z "$(tag_pids)" ]]; then
      break
    fi
    sleep 1
  done

  if [[ -n "$(tag_pids)" ]]; then
    kill_tag_processes KILL
  fi

  tiup clean "${tag}" >/dev/null 2>&1 || true
}

start_playground() {
  mkdir -p "${workdir}" "${workdir}/tidb-config-capture-logs"
  local log_file="${workdir}/tidb-config-capture-logs/${tag}.log"
  ensure_components_installed
  repair_component_binaries
  sign_darwin_binaries
  clear_existing_playground
  wait_ports_free 60
  screen -dmS "${tag}" /bin/sh -c "cd '${workdir}'; export NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1; tiup playground '${version}' --tag '${tag}' --host 127.0.0.1 --db 1 --pd 1 --kv 1 --tiflash 1 --without-monitor >> '${log_file}' 2>&1"
  echo "started playground screen ${tag}; log ${log_file}"
}

stop_playground() {
  screen -S "${tag}" -X quit >/dev/null 2>&1 || true
  sleep 2
  kill_tag_processes TERM

  local i
  for i in {1..30}; do
    if [[ -z "$(tag_pids)" ]]; then
      break
    fi
    sleep 1
  done

  if [[ -n "$(tag_pids)" ]]; then
    kill_tag_processes KILL
  fi

  wait_ports_free 60
  tiup clean "${tag}" >/dev/null 2>&1 || true
}

started_playground=0
cleanup() {
  local status=$?
  trap - EXIT
  if [[ "${started_playground}" -eq 1 && "${stop_after}" -eq 1 ]]; then
    stop_playground || true
  fi
  exit "${status}"
}
trap cleanup EXIT

if [[ "${reuse_running}" -eq 0 ]]; then
  start_playground
  started_playground=1
fi

wait_cmd "TiDB SQL" 240 mysql --connect-timeout=5 --comments --host 127.0.0.1 --port 4000 -u root -N -e "SELECT 1"
actual_version="$(mysql --connect-timeout=5 --comments --host 127.0.0.1 --port 4000 -u root -N -e "SELECT VERSION()")"
if [[ "${actual_version}" != *"TiDB-${version}"* ]]; then
  echo "version mismatch: expected TiDB-${version}, got ${actual_version}" >&2
  echo "A different playground might already be using the default ports. Stop it or rerun with --reuse-running for the matching version." >&2
  exit 1
fi
wait_cmd "TiDB status" 120 curl --max-time 5 -fsS http://127.0.0.1:10080/config
wait_cmd "PD config" 120 curl --max-time 5 -fsS http://127.0.0.1:2379/pd/api/v1/config
wait_cmd "TiKV config" 120 curl --max-time 5 -fsS 'http://127.0.0.1:20180/config?full=true'
wait_cmd "TiFlash config" 240 curl --max-time 5 -fsS 'http://127.0.0.1:20292/config?full=true'

collect_args=("${version}" --output-root "${output_root}" --cluster-tag "${tag}")
if [[ "${sanitize}" -eq 1 ]]; then
  collect_args+=(--sanitize)
fi
if [[ "${force}" -eq 1 ]]; then
  collect_args+=(--force)
fi

"${repo_root}/scripts/collect-configs.py" "${collect_args[@]}"
validate_args=("${output_root}/${version}")
if [[ "${sanitize}" -eq 1 ]]; then
  validate_args+=(--require-sanitized)
fi
"${repo_root}/scripts/validate-capture.py" "${validate_args[@]}"

if [[ "${stop_after}" -eq 1 ]]; then
  stop_playground
  started_playground=0
fi
