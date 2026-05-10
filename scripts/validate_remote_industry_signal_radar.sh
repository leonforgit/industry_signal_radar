#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${CONFIG_PATH:-${PROJECT_ROOT}/config/runtime_defaults.json}"
ACTION="${1:-start}"
RUN_AT="${RUN_AT:-$(date -u +%Y-%m-%dT%H:%M:%S+00:00)}"
VALIDATION_ID="${VALIDATION_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
BODY_MODE_OVERRIDE="${BODY_MODE_OVERRIDE:-${RADAR_ALERT_BODY_MODE:-}}"

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
REMOTE_DB="${REMOTE_DB:-${REMOTE_ROOT}/state/industry_signal_radar.db}"
REMOTE_LOG="${REMOTE_ROOT}/logs/manual_validation_${VALIDATION_ID}.log"
REMOTE_STATUS_FILE="${REMOTE_ROOT}/health/manual_validation_${VALIDATION_ID}.json"
REMOTE_PID_FILE="${REMOTE_ROOT}/health/manual_validation_${VALIDATION_ID}.pid"
REMOTE_WRAPPER_STATUS_FILE="${REMOTE_ROOT}/health/manual_validation_${VALIDATION_ID}.wrapper.json"
REMOTE_WRAPPER_SCRIPT="${REMOTE_ROOT}/health/manual_validation_${VALIDATION_ID}.wrapper.sh"
REMOTE_UNIT_FILE="${REMOTE_ROOT}/health/manual_validation_${VALIDATION_ID}.unit"
REMOTE_LOCK_PATH="${REMOTE_ROOT}/locks/industry-signal-radar.lock"
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

start_validation() {
  local remote_script
  echo "[start] host=${REMOTE_HOST}"
  echo "[start] run_at=${RUN_AT}"
  echo "[start] remote_log=${REMOTE_LOG}"
  echo "[start] remote_status=${REMOTE_STATUS_FILE}"
  echo "[start] remote_pid_file=${REMOTE_PID_FILE}"
  echo "[start] remote_wrapper_status=${REMOTE_WRAPPER_STATUS_FILE}"
  echo "[start] remote_wrapper_script=${REMOTE_WRAPPER_SCRIPT}"
  echo "[start] remote_unit_file=${REMOTE_UNIT_FILE}"
  echo "[start] remote_lock_path=${REMOTE_LOCK_PATH}"
  echo "[start] body_mode_override=${BODY_MODE_OVERRIDE:-<config-default>}"
  remote_script="$(cat <<EOF
set -euo pipefail
VALIDATION_ID='${VALIDATION_ID}'
RUN_AT='${RUN_AT}'
REMOTE_ROOT='${REMOTE_ROOT}'
REMOTE_LOG='${REMOTE_LOG}'
REMOTE_STATUS_FILE='${REMOTE_STATUS_FILE}'
REMOTE_PID_FILE='${REMOTE_PID_FILE}'
REMOTE_WRAPPER_STATUS_FILE='${REMOTE_WRAPPER_STATUS_FILE}'
REMOTE_WRAPPER_SCRIPT='${REMOTE_WRAPPER_SCRIPT}'
REMOTE_UNIT_FILE='${REMOTE_UNIT_FILE}'
REMOTE_LOCK_PATH='${REMOTE_LOCK_PATH}'
BODY_MODE_OVERRIDE='${BODY_MODE_OVERRIDE}'
mkdir -p "\${REMOTE_ROOT}/logs" "\${REMOTE_ROOT}/health"
rm -f "\${REMOTE_STATUS_FILE}"
rm -f "\${REMOTE_WRAPPER_STATUS_FILE}"
rm -f "\${REMOTE_PID_FILE}"
rm -f "\${REMOTE_UNIT_FILE}"
cat > "\${REMOTE_WRAPPER_SCRIPT}" <<'INNER'
#!/usr/bin/env bash
set -u
export RADAR_DEBUG_PROGRESS=1
if [ -n "${BODY_MODE_OVERRIDE}" ]; then
  export RADAR_ALERT_BODY_MODE="${BODY_MODE_OVERRIDE}"
fi
set +e
'${REMOTE_ROOT}/.venv/bin/python' \
  '${REMOTE_ROOT}/scripts/radar_manual_validation.py' \
    --validation-id '${VALIDATION_ID}' \
    --run-at '${RUN_AT}' \
    --status-file '${REMOTE_STATUS_FILE}' \
    --log-file '${REMOTE_LOG}' \
    --lock-path '${REMOTE_LOCK_PATH}' \
    --skip-bark
rc=\$?
export WRAPPER_RC="\$rc"
python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

payload = {
    "validation_id": "${VALIDATION_ID}",
    "run_at": "${RUN_AT}",
    "body_mode_override": "${BODY_MODE_OVERRIDE}",
    "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "wrapper_return_code": int(os.environ.get("WRAPPER_RC", "-1")),
    "status_path": "${REMOTE_STATUS_FILE}",
    "log_path": "${REMOTE_LOG}",
}
Path("${REMOTE_WRAPPER_STATUS_FILE}").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
exit "\$rc"
INNER
chmod +x "\${REMOTE_WRAPPER_SCRIPT}"
if command -v systemd-run >/dev/null 2>&1; then
  unit_name="industry-signal-radar-validation-\${VALIDATION_ID}"
  systemd-run \
    --unit "\${unit_name}" \
    --description "Industry Signal Radar manual validation \${VALIDATION_ID}" \
    --property "WorkingDirectory=\${REMOTE_ROOT}" \
    --collect \
    --no-block \
    "\${REMOTE_WRAPPER_SCRIPT}" >/dev/null
  echo "\${unit_name}" > "\${REMOTE_UNIT_FILE}"
  : > "\${REMOTE_PID_FILE}"
  echo "\${unit_name}"
else
  nohup "\${REMOTE_WRAPPER_SCRIPT}" >/dev/null 2>&1 < /dev/null &
  pid=\$!
  echo "\${pid}" > "\${REMOTE_PID_FILE}"
  echo "\${pid}"
fi
EOF
)"
  ssh_remote "${remote_script}"
}

check_status() {
  echo "[status] host=${REMOTE_HOST}"
  ssh_remote "python3 - '${REMOTE_DB}' '${REMOTE_ROOT}' '${VALIDATION_ID}' '${RUN_AT}' '${REMOTE_PID_FILE}' '${REMOTE_UNIT_FILE}' <<'PY'
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

db_path = sys.argv[1]
remote_root = Path(sys.argv[2])
validation_id = sys.argv[3]
run_at = sys.argv[4]
pid_file = Path(sys.argv[5])
unit_file = Path(sys.argv[6])
run_id = f'scan:{run_at}'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

latest_run = conn.execute(
    '''
    SELECT run_id, started_at, completed_at, status, note
    FROM radar_runs
    WHERE mode = 'scan'
    ORDER BY started_at DESC
    LIMIT 1
    '''
).fetchone()

target_run = conn.execute(
    '''
    SELECT run_id, started_at, completed_at, status, note
    FROM radar_runs
    WHERE run_id = ?
    LIMIT 1
    ''',
    (run_id,),
).fetchone()
selected_run_id = str(target_run['run_id']) if target_run else ''

source_ids = [
    'akshare:stock_notice_report',
    'akshare:stock_info_global_cls',
    'akshare:news_cctv',
    'shared:news_event_hub:industry_radar_feed',
    'openbb:yfinance',
    'akshare:stock_sector_fund_flow_rank',
    'akshare:stock_fund_flow_industry',
    'akshare:fund_etf_spot_em',
    'akshare:fund_etf_fund_daily_em',
    'akshare:fund_etf_scale_sse',
    'akshare:fund_etf_scale_szse',
    'akshare:stock_hsgt_hold_stock_em',
    'akshare:stock_margin_detail',
    'akshare:stock_lhb_detail_daily_sina',
    'akshare:macro_shipping_bdi',
    'akshare:macro_china_freight_index',
    'akshare:macro_china_society_electricity',
]

if selected_run_id:
    health_rows = conn.execute(
        f'''
        SELECT source_id, checked_at, status, fetched_count, inserted_count, error_count, note
        FROM source_health_checks
        WHERE run_id = ? AND source_id IN ({','.join('?' for _ in source_ids)})
        ORDER BY checked_at DESC
        LIMIT 20
        ''',
        [selected_run_id, *source_ids],
    ).fetchall()
    snapshot = conn.execute(
        '''
        SELECT industry_id, industry_label, industry_state, total_score, evidence_json
        FROM signal_snapshots
        WHERE run_id = ?
        ORDER BY total_score DESC, snapshot_at DESC
        LIMIT 3
        ''',
        (selected_run_id,),
    ).fetchall()
    recent_alerts = conn.execute(
        '''
        SELECT alert_id, created_at, industry_label, alert_level, state, title, body
        FROM alert_events
        WHERE run_id = ?
        ORDER BY created_at DESC
        LIMIT 3
        ''',
        (selected_run_id,),
    ).fetchall()
else:
    health_rows = conn.execute(
        f'''
        SELECT source_id, checked_at, status, fetched_count, inserted_count, error_count, note
        FROM source_health_checks
        WHERE source_id IN ({','.join('?' for _ in source_ids)})
        ORDER BY checked_at DESC
        LIMIT 20
        ''',
        source_ids,
    ).fetchall()
    snapshot = conn.execute(
        '''
        SELECT industry_id, industry_label, industry_state, total_score, evidence_json
        FROM signal_snapshots
        ORDER BY snapshot_at DESC, total_score DESC
        LIMIT 3
        '''
    ).fetchall()
    recent_alerts = conn.execute(
        '''
        SELECT alert_id, created_at, industry_label, alert_level, state, title, body
        FROM alert_events
        ORDER BY created_at DESC
        LIMIT 3
        '''
    ).fetchall()

payload = {
    'latest_run': dict(latest_run) if latest_run else None,
    'target_run': dict(target_run) if target_run else None,
    'source_health': [dict(item) for item in health_rows],
    'top_snapshots': [],
    'recent_alerts': [],
}
for item in snapshot:
    row = dict(item)
    evidence = json.loads(row.pop('evidence_json') or '{}')
    row['has_announcement_events'] = bool(evidence.get('announcement_events'))
    row['has_flow_signal_evidence'] = bool(evidence.get('flow_signal_evidence'))
    row['proxy_family'] = str(evidence.get('fundamental_proxy_evidence', {}).get('proxy_family', ''))
    row['overlay_names'] = [entry.get('display_name_cn') for entry in evidence.get('overlay_context', [])[:3]]
    payload['top_snapshots'].append(row)

for item in recent_alerts:
    row = dict(item)
    body = str(row.get('body', '') or '')
    body_lines = body.splitlines()
    row['body_preview'] = body_lines[:12]
    row['body_line_count'] = len(body_lines)
    row['has_trigger_line'] = any(line.startswith('触发：') for line in body_lines)
    row['has_evidence_line'] = any(line.startswith('证据：') for line in body_lines)
    row['has_catalyst_line'] = any(line.startswith('催化：') for line in body_lines)
    row['has_policy_line'] = any(line.startswith('政策/舆情：') for line in body_lines)
    row['has_proxy_detail_line'] = any(line.startswith('代理细节：') for line in body_lines)
    row['has_announcement_line'] = any(line.startswith('公告硬信息：') for line in body_lines)
    row['has_market_context_line'] = any(line.startswith('外围环境：') for line in body_lines)
    if row['has_trigger_line'] and row['has_evidence_line']:
        row['body_mode_hint'] = 'brief'
    elif row['has_policy_line'] or row['has_proxy_detail_line'] or row['has_announcement_line'] or row['has_market_context_line']:
        row['body_mode_hint'] = 'full'
    else:
        row['body_mode_hint'] = 'unknown'
    payload['recent_alerts'].append(row)

if unit_file.exists():
    unit_name = unit_file.read_text(encoding='utf-8').strip()
    unit_result = subprocess.run(
        ['systemctl', 'show', unit_name, '--property=ActiveState,SubState,Result,ExecMainStatus,MainPID'],
        capture_output=True,
        text=True,
        check=False,
    )
    payload['manual_validation_unit'] = {
        'unit_file': str(unit_file),
        'unit_name': unit_name,
        'show_rc': unit_result.returncode,
        'show_output': unit_result.stdout.strip().splitlines(),
    }

status_file: Path | None = None
if validation_id:
    candidate = remote_root / 'health' / f'manual_validation_{validation_id}.json'
    if candidate.exists():
        status_file = candidate
if status_file is None:
    matches = sorted((remote_root / 'health').glob('manual_validation_*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
    if matches:
        status_file = matches[0]
if status_file is not None and status_file.exists():
    payload['manual_validation_status'] = json.loads(status_file.read_text(encoding='utf-8'))
    target_run_id = str(payload['manual_validation_status'].get('target_run_id', '') or '')
    if target_run_id and not payload.get('target_run'):
        payload['target_run'] = conn.execute(
            '''
            SELECT run_id, started_at, completed_at, status, note
            FROM radar_runs
            WHERE run_id = ?
            LIMIT 1
            ''',
            (target_run_id,),
        ).fetchone()
        if payload['target_run'] is not None:
            payload['target_run'] = dict(payload['target_run'])
        run_health_rows = conn.execute(
            f'''
            SELECT source_id, checked_at, status, fetched_count, inserted_count, error_count, note
            FROM source_health_checks
            WHERE run_id = ? AND source_id IN ({','.join('?' for _ in source_ids)})
            ORDER BY checked_at DESC
            LIMIT 20
            ''',
            [target_run_id, *source_ids],
        ).fetchall()
        payload['source_health'] = [dict(item) for item in run_health_rows]
        run_snapshots = conn.execute(
            '''
            SELECT industry_id, industry_label, industry_state, total_score, evidence_json
            FROM signal_snapshots
            WHERE run_id = ?
            ORDER BY total_score DESC, snapshot_at DESC
            LIMIT 3
            ''',
            (target_run_id,),
        ).fetchall()
        payload['top_snapshots'] = []
        for item in run_snapshots:
            row = dict(item)
            evidence = json.loads(row.pop('evidence_json') or '{}')
            row['has_announcement_events'] = bool(evidence.get('announcement_events'))
            row['has_flow_signal_evidence'] = bool(evidence.get('flow_signal_evidence'))
            row['proxy_family'] = str(evidence.get('fundamental_proxy_evidence', {}).get('proxy_family', ''))
            row['overlay_names'] = [entry.get('display_name_cn') for entry in evidence.get('overlay_context', [])[:3]]
            payload['top_snapshots'].append(row)
        run_alerts = conn.execute(
            '''
            SELECT alert_id, created_at, industry_label, alert_level, state, title, body
            FROM alert_events
            WHERE run_id = ?
            ORDER BY created_at DESC
            LIMIT 3
            ''',
            (target_run_id,),
        ).fetchall()
        payload['recent_alerts'] = []
        for item in run_alerts:
            row = dict(item)
            body = str(row.get('body', '') or '')
            body_lines = body.splitlines()
            row['body_preview'] = body_lines[:12]
            row['body_line_count'] = len(body_lines)
            row['has_trigger_line'] = any(line.startswith('触发：') for line in body_lines)
            row['has_evidence_line'] = any(line.startswith('证据：') for line in body_lines)
            row['has_catalyst_line'] = any(line.startswith('催化：') for line in body_lines)
            row['has_policy_line'] = any(line.startswith('政策/舆情：') for line in body_lines)
            row['has_proxy_detail_line'] = any(line.startswith('代理细节：') for line in body_lines)
            row['has_announcement_line'] = any(line.startswith('公告硬信息：') for line in body_lines)
            row['has_market_context_line'] = any(line.startswith('外围环境：') for line in body_lines)
            if row['has_trigger_line'] and row['has_evidence_line']:
                row['body_mode_hint'] = 'brief'
            elif row['has_policy_line'] or row['has_proxy_detail_line'] or row['has_announcement_line'] or row['has_market_context_line']:
                row['body_mode_hint'] = 'full'
            else:
                row['body_mode_hint'] = 'unknown'
            payload['recent_alerts'].append(row)
    log_path = Path(str(payload['manual_validation_status'].get('log_path', '')))
    if log_path.exists():
        payload['manual_validation_log_tail'] = log_path.read_text(encoding='utf-8', errors='ignore').splitlines()[-20:]
    wrapper_status_path = remote_root / 'health' / f'manual_validation_{validation_id}.wrapper.json'
    if wrapper_status_path.exists():
        payload['manual_validation_wrapper'] = json.loads(wrapper_status_path.read_text(encoding='utf-8'))
else:
    pid = None
    if pid_file.exists():
        raw = pid_file.read_text(encoding='utf-8').strip()
        pid = int(raw) if raw else None
    ps_result = None
    if pid is not None:
        ps_result = subprocess.run(
            ['ps', '-p', str(pid), '-o', 'pid=,etime=,stat=,cmd='],
            capture_output=True,
            text=True,
            check=False,
        )
    payload['manual_validation_process'] = {
        'pid_file': str(pid_file),
        'pid': pid,
        'ps_rc': None if ps_result is None else ps_result.returncode,
        'ps_output': [] if ps_result is None else ps_result.stdout.strip().splitlines(),
    }
if 'manual_validation_status' in payload:
    current_boot_id_path = Path('/proc/sys/kernel/random/boot_id')
    current_boot_id = current_boot_id_path.read_text(encoding='utf-8').strip() if current_boot_id_path.exists() else ''
    payload['manual_validation_runtime'] = {
        'current_boot_id': current_boot_id,
    }
    if 'manual_validation_process' not in payload:
        pid = None
        if pid_file.exists():
            raw = pid_file.read_text(encoding='utf-8').strip()
            pid = int(raw) if raw else None
        ps_result = None
        if pid is not None:
            ps_result = subprocess.run(
                ['ps', '-p', str(pid), '-o', 'pid=,etime=,stat=,cmd='],
                capture_output=True,
                text=True,
                check=False,
            )
        payload['manual_validation_process'] = {
            'pid_file': str(pid_file),
            'pid': pid,
            'ps_rc': None if ps_result is None else ps_result.returncode,
            'ps_output': [] if ps_result is None else ps_result.stdout.strip().splitlines(),
        }
    status_value = str(payload['manual_validation_status'].get('status', ''))
    started_boot_id = str(payload['manual_validation_status'].get('boot_id', ''))
    process_alive = bool(payload['manual_validation_process'].get('ps_output'))
    if not process_alive and 'manual_validation_unit' in payload:
        unit_lines = payload['manual_validation_unit'].get('show_output', [])
        unit_map = {}
        for line in unit_lines:
            if '=' in line:
                key, value = line.split('=', 1)
                unit_map[key] = value
        payload['manual_validation_unit']['state'] = unit_map
        active_state = str(unit_map.get('ActiveState', ''))
        sub_state = str(unit_map.get('SubState', ''))
        process_alive = active_state in {'active', 'activating'} or sub_state in {'running', 'start', 'start-pre', 'start-post'}
    if status_value == 'running' and not process_alive:
        derived = 'interrupted'
        reason = 'process_not_found'
        if started_boot_id and current_boot_id and started_boot_id != current_boot_id:
            derived = 'interrupted_after_reboot'
            reason = 'boot_id_changed'
        payload['manual_validation_runtime']['derived_status'] = derived
        payload['manual_validation_runtime']['derived_reason'] = reason
    wrapper_status_path = remote_root / 'health' / f'manual_validation_{validation_id}.wrapper.json'
    if wrapper_status_path.exists():
        payload['manual_validation_wrapper'] = json.loads(wrapper_status_path.read_text(encoding='utf-8'))
        wrapper_rc = payload['manual_validation_wrapper'].get('wrapper_return_code')
        if status_value == 'running' and not process_alive and isinstance(wrapper_rc, int):
            if wrapper_rc != 0:
                payload['manual_validation_runtime']['derived_status'] = 'failed_wrapper'
                payload['manual_validation_runtime']['derived_reason'] = f'wrapper_return_code={wrapper_rc}'

print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
PY"
}

case "${ACTION}" in
  start)
    start_validation
    ;;
  status)
    check_status
    ;;
  *)
    echo "Usage: $(basename "$0") [start|status]" >&2
    exit 1
    ;;
esac
