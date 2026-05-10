#!/usr/bin/env bash
set -euo pipefail

REMOTE_ROOT="${RADAR_RUNTIME_ROOT:-/opt/industry-signal-radar}"
REMOTE_VENV="${REMOTE_ROOT}/.venv"
LOCK_PATH="${REMOTE_ROOT}/locks/industry-signal-radar.lock"
RUN_TIMEOUT_SECONDS="${RADAR_RUN_TIMEOUT_SECONDS:-900}"

mkdir -p \
  "${REMOTE_ROOT}/config" \
  "${REMOTE_ROOT}/state" \
  "${REMOTE_ROOT}/logs" \
  "${REMOTE_ROOT}/cache" \
  "${REMOTE_ROOT}/output" \
  "${REMOTE_ROOT}/health" \
  "${REMOTE_ROOT}/locks"

cd "${REMOTE_ROOT}"
source "${REMOTE_VENV}/bin/activate"

exec flock -n "${LOCK_PATH}" \
  timeout --signal=TERM --kill-after=30 "${RUN_TIMEOUT_SECONDS}" \
  python "${REMOTE_ROOT}/scripts/radar_scan_runner.py" "$@"
