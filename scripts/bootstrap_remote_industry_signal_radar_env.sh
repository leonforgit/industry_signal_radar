#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${CONFIG_PATH:-${PROJECT_ROOT}/config/runtime_defaults.json}"
LOCK_PATH="${LOCK_PATH:-${PROJECT_ROOT}/config/remote_requirements.lock.txt}"

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
  shift
fi

if [[ $# -gt 0 ]]; then
  echo "Unsupported arguments: $*" >&2
  exit 1
fi

config_value() {
  python3 - "$CONFIG_PATH" "$1" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
dot_path = sys.argv[2].split(".")
payload = json.loads(config_path.read_text(encoding="utf-8"))
value = payload
for part in dot_path:
    if not isinstance(value, dict):
        value = ""
        break
    value = value.get(part, "")
print(value if value is not None else "")
PY
}

REMOTE_HOST="${RADAR_REMOTE_HOST:-${REMOTE_HOST:-$(config_value deployment.server_host)}}"
REMOTE_ROOT="${REMOTE_ROOT:-$(config_value deployment.remote_root)}"
REMOTE_VENV="${REMOTE_VENV:-$(config_value deployment.remote_venv)}"
REMOTE_LOCK_PATH="${REMOTE_ROOT}/config/remote_requirements.lock.txt"
SSH_OPTIONS="${RADAR_SSH_OPTIONS:-${SSH_OPTIONS:-$(config_value deployment.ssh_options)}}"
if [[ -z "${REMOTE_HOST}" ]]; then
  echo "Missing remote host. Set RADAR_REMOTE_HOST or REMOTE_HOST in a private, untracked environment." >&2
  exit 2
fi
SSH_OPTS_ARR=()
if [[ -n "${SSH_OPTIONS}" ]]; then
  read -r -a SSH_OPTS_ARR <<< "${SSH_OPTIONS}"
fi

ssh_remote() {
  local remote_script="$1"
  if [[ ${#SSH_OPTS_ARR[@]} -gt 0 ]]; then
    printf '%s\n' "${remote_script}" | ssh "${SSH_OPTS_ARR[@]}" "${REMOTE_HOST}" bash -s
  else
    printf '%s\n' "${remote_script}" | ssh "${REMOTE_HOST}" bash -s
  fi
}

copy_file_remote() {
  local source_path="$1"
  local remote_path="$2"
  local remote_command
  remote_command="$(
    python3 - "${source_path}" "${remote_path}" <<'PY'
import base64
import shlex
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
remote_path = sys.argv[2]
payload = base64.b64encode(source_path.read_bytes()).decode("ascii")
script = (
    "import base64; "
    "from pathlib import Path; "
    f"path = Path({remote_path!r}); "
    "path.parent.mkdir(parents=True, exist_ok=True); "
    f"path.write_bytes(base64.b64decode({payload!r}))"
)
print(f"python3 -c {shlex.quote(script)}")
PY
  )"
  ssh_remote "${remote_command}"
}

if [[ ! -f "${LOCK_PATH}" ]]; then
  echo "Missing lock file: ${LOCK_PATH}" >&2
  exit 1
fi

echo "[0/4] Checking remote prerequisites on ${REMOTE_HOST}"
ssh_remote "command -v python3 >/dev/null && python3 -V && mkdir -p '${REMOTE_ROOT}/config' '${REMOTE_ROOT}/state' '${REMOTE_ROOT}/logs' '${REMOTE_ROOT}/cache' '${REMOTE_ROOT}/output' '${REMOTE_ROOT}/health' '${REMOTE_ROOT}/locks' '${REMOTE_ROOT}/scripts'"

echo "[1/4] Syncing locked requirements"
copy_file_remote "${LOCK_PATH}" "${REMOTE_LOCK_PATH}"

echo "[2/4] Checking current venv state"
ssh_remote "if [ -x '${REMOTE_VENV}/bin/python' ]; then '${REMOTE_VENV}/bin/python' -V; else echo 'REMOTE_VENV_MISSING'; fi"

if [[ "${CHECK_ONLY}" == "1" ]]; then
  echo "[3/4] Check-only mode enabled; skipping venv creation and pip install"
  echo "Remote Industry Signal Radar environment preflight OK on ${REMOTE_HOST}"
  exit 0
fi

echo "[3/4] Creating or reusing remote venv"
ssh_remote "if [ ! -x '${REMOTE_VENV}/bin/python' ]; then python3 -m venv '${REMOTE_VENV}'; fi"

if grep -Eq '^[[:space:]]*[^#[:space:]].*$' "${LOCK_PATH}"; then
  echo "[4/4] Installing locked Python dependencies"
  ssh_remote "source '${REMOTE_VENV}/bin/activate' && python -m pip install --upgrade pip setuptools wheel && pip install -r '${REMOTE_LOCK_PATH}'"
else
  echo "[4/4] No pinned third-party dependencies yet; skipping pip install"
fi

echo "Remote Industry Signal Radar Python environment bootstrapped on ${REMOTE_HOST}"
