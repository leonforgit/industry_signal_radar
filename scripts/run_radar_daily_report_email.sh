#!/usr/bin/env bash
set -euo pipefail

REMOTE_ROOT="${RADAR_RUNTIME_ROOT:-/opt/industry-signal-radar}"
REMOTE_VENV="${REMOTE_ROOT}/.venv"
BUILD_LOCK_PATH="${REMOTE_ROOT}/locks/industry-signal-radar.lock"
EMAIL_LOCK_PATH="${REMOTE_ROOT}/locks/industry-signal-radar-daily-email.lock"
RUN_TIMEOUT_SECONDS="${RADAR_DAILY_REPORT_TIMEOUT_SECONDS:-1800}"
SMTP_ENV_FILE="${RADAR_SMTP_ENV_FILE:-/opt/quant-runtime/config/paper_trade_daily.env}"
REPORT_TOOLKIT_ROOT="${REPORT_TOOLKIT_ROOT:-/opt/quant-runtime/report_toolkit}"
RADAR_SMTP_REQUIRED="${RADAR_SMTP_REQUIRED:-0}"
RADAR_DAILY_EMAIL_DRY_RUN="${RADAR_DAILY_EMAIL_DRY_RUN:-0}"
RADAR_DAILY_TIMER_UNIT="${RADAR_DAILY_TIMER_UNIT:-industry-signal-radar-daily-report.timer}"
RADAR_DAILY_TIMER_TRIGGER_WINDOW_SECONDS="${RADAR_DAILY_TIMER_TRIGGER_WINDOW_SECONDS:-900}"
RADAR_AGENT_TASK_AUTORUN="${RADAR_AGENT_TASK_AUTORUN:-1}"
RADAR_AGENT_TASK_AUTORUN_MAX="${RADAR_AGENT_TASK_AUTORUN_MAX:-1}"
RADAR_AGENT_TASK_AUTORUN_PRIORITIES="${RADAR_AGENT_TASK_AUTORUN_PRIORITIES:-P2,P3}"
DELIVERY_MANIFEST="${REMOTE_ROOT}/health/radar_daily_email_delivery_latest.json"
SCHEDULED_DELIVERY_MANIFEST="${REMOTE_ROOT}/health/radar_daily_email_delivery_scheduled_latest.json"
DELIVERY_HISTORY="${REMOTE_ROOT}/health/radar_daily_email_delivery_history.jsonl"
DELIVERY_INVOCATION_DIR="${REMOTE_ROOT}/health/delivery"
HARNESS_MANIFEST="${REMOTE_ROOT}/output/runs/radar_harness_manifest_latest.json"
PIPELINE_STAGE="bootstrap"
EXIT_HEALTH_RECORDED=0

mkdir -p \
  "${REMOTE_ROOT}/config" \
  "${REMOTE_ROOT}/state" \
  "${REMOTE_ROOT}/logs" \
  "${REMOTE_ROOT}/cache" \
  "${REMOTE_ROOT}/output" \
	  "${REMOTE_ROOT}/health" \
	  "${DELIVERY_INVOCATION_DIR}" \
	  "${REMOTE_ROOT}/locks"

cd "${REMOTE_ROOT}"
source "${REMOTE_VENV}/bin/activate"
export REPORT_TOOLKIT_ROOT

utc_now() {
  python - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).isoformat(timespec="seconds"))
PY
}

timer_last_trigger() {
  systemctl show "${RADAR_DAILY_TIMER_UNIT}" -p LastTriggerUSec --value 2>/dev/null || true
}

detect_invocation_source() {
  if [[ -n "${RADAR_DAILY_INVOCATION_SOURCE:-}" ]]; then
    printf '%s\n' "${RADAR_DAILY_INVOCATION_SOURCE}"
    return
  fi
  if [[ -n "${INVOCATION_ID:-}" ]]; then
    local trigger_text trigger_epoch now_epoch delta
    trigger_text="$(timer_last_trigger)"
    trigger_epoch="$(date -d "${trigger_text}" +%s 2>/dev/null || true)"
    now_epoch="$(date +%s)"
    if [[ -n "${trigger_epoch}" ]]; then
      delta=$((now_epoch - trigger_epoch))
      if [[ "${delta}" -ge 0 && "${delta}" -le "${RADAR_DAILY_TIMER_TRIGGER_WINDOW_SECONDS}" ]]; then
        printf '%s\n' "systemd_timer"
        return
      fi
    fi
    printf '%s\n' "systemd_service_manual"
    return
  fi
  printf '%s\n' "manual_shell"
}

INVOCATION_STARTED_AT="$(utc_now)"
INVOCATION_SOURCE="$(detect_invocation_source)"
SYSTEMD_INVOCATION_ID="${INVOCATION_ID:-}"
TIMER_LAST_TRIGGER="$(timer_last_trigger)"
INVOCATION_KEY="$(printf '%s_%s_%s' "${INVOCATION_SOURCE}" "${INVOCATION_STARTED_AT}" "${SYSTEMD_INVOCATION_ID:-no-systemd}" | tr ':+' '--' | tr -c 'A-Za-z0-9_.-' '_')"

record_delivery_health() {
  local status="$1"
  local detail="$2"
  local rc="$3"
  python "${REMOTE_ROOT}/scripts/radar_runtime_health.py" \
    --health-key radar_daily_email_delivery \
    --status "${status}" \
    --metric-value "${rc}" \
    --detail "${detail}" >/dev/null 2>&1 || true
  python - "${DELIVERY_MANIFEST}" "${SCHEDULED_DELIVERY_MANIFEST}" "${DELIVERY_HISTORY}" "${DELIVERY_INVOCATION_DIR}" "${status}" "${detail}" "${rc}" "${PIPELINE_STAGE}" "${INVOCATION_SOURCE}" "${INVOCATION_STARTED_AT}" "${SYSTEMD_INVOCATION_ID}" "${RADAR_DAILY_TIMER_UNIT}" "${TIMER_LAST_TRIGGER}" "${RADAR_DAILY_EMAIL_DRY_RUN}" "${RADAR_SMTP_REQUIRED}" "${INVOCATION_KEY}" <<'PY' || true
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

latest_path = Path(sys.argv[1])
scheduled_path = Path(sys.argv[2])
history_path = Path(sys.argv[3])
invocation_dir = Path(sys.argv[4])
status = sys.argv[5]
detail = sys.argv[6]
return_code = int(sys.argv[7])
pipeline_stage = sys.argv[8]
invocation_source = sys.argv[9]
invocation_started_at = sys.argv[10]
systemd_invocation_id = sys.argv[11]
timer_unit = sys.argv[12]
timer_last_trigger = sys.argv[13]
dry_run = sys.argv[14] == "1"
smtp_required = sys.argv[15] == "1"
invocation_key = sys.argv[16]
generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
payload = {
    "generated_at": generated_at,
    "status": status,
    "detail": detail,
    "return_code": return_code,
    "pipeline_stage": pipeline_stage,
    "invocation_source": invocation_source,
    "invocation_started_at": invocation_started_at,
    "systemd_invocation_id": systemd_invocation_id,
    "timer_unit": timer_unit,
    "timer_last_trigger": timer_last_trigger,
    "dry_run": dry_run,
    "smtp_required": smtp_required,
    "invocation_key": invocation_key,
}

root = latest_path.parent.parent

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def read_json(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}

def extract_report_run_id(text):
    for pattern in (r'codex_output_run_id:\s*"([^"]+)"', r"codex_output_run_id:\s*'([^']+)'", r"运行批次[：:]\s*`([^`]+)`"):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""

def artifact_meta(relative, *, json_artifact=True):
    path = root / relative
    row = {"path": relative, "exists": path.exists()}
    if not path.exists():
        return row
    stat = path.stat()
    row.update({"bytes": stat.st_size, "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"), "sha256": sha256_file(path)})
    if json_artifact:
        data = read_json(path)
        row.update(
            {
                "status": str(data.get("status") or ""),
                "generated_at": str(data.get("generated_at") or ""),
                "run_id": str(data.get("run_id") or data.get("radar_run_id") or ""),
            }
        )
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        row["run_id"] = extract_report_run_id(text)
    return row

artifacts = {
    "snapshot": artifact_meta("output/snapshots/radar_daily_battlecard_snapshot_latest.json"),
    "quality": artifact_meta("output/reports/radar_daily_battlecard_quality_latest.json"),
    "source_readiness": artifact_meta("output/reports/radar_source_readiness_latest.json"),
    "report_md": artifact_meta("output/reports/radar_daily_battlecard_latest.md", json_artifact=False),
    "report_pdf": artifact_meta("output/reports/radar_daily_battlecard_latest.pdf", json_artifact=False),
}
payload["artifacts"] = artifacts
payload["run_id"] = str(artifacts["snapshot"].get("run_id") or artifacts["quality"].get("run_id") or artifacts["report_md"].get("run_id") or "")
payload["quality_status"] = str(artifacts["quality"].get("status") or "")
payload["quality_generated_at"] = str(artifacts["quality"].get("generated_at") or "")
payload["source_readiness_status"] = str(artifacts["source_readiness"].get("status") or "")
payload["source_readiness_generated_at"] = str(artifacts["source_readiness"].get("generated_at") or "")
latest_path.parent.mkdir(parents=True, exist_ok=True)
latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
invocation_dir.mkdir(parents=True, exist_ok=True)
(invocation_dir / f"{invocation_key}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
history_path.parent.mkdir(parents=True, exist_ok=True)
with history_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
if invocation_source == "systemd_timer":
    scheduled_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  python - "${HARNESS_MANIFEST}" "${DELIVERY_MANIFEST}" "${status}" "${detail}" "${rc}" "${PIPELINE_STAGE}" "${INVOCATION_SOURCE}" "${INVOCATION_STARTED_AT}" "${SYSTEMD_INVOCATION_ID}" "${RADAR_DAILY_TIMER_UNIT}" "${TIMER_LAST_TRIGGER}" "${RADAR_DAILY_EMAIL_DRY_RUN}" "${INVOCATION_KEY}" <<'PY' || true
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
delivery_path = Path(sys.argv[2])
if not path.exists():
    raise SystemExit(0)
payload = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(payload, dict) or payload.get("schema_version") != "radar_harness_manifest.v2":
    raise SystemExit(0)
payload["delivery_status"] = sys.argv[3]
payload["delivery_detail"] = sys.argv[4]
payload["delivery_return_code"] = int(sys.argv[5])
payload["delivery_pipeline_stage"] = sys.argv[6]
payload["delivery_invocation_source"] = sys.argv[7]
payload["delivery_invocation_started_at"] = sys.argv[8]
payload["delivery_systemd_invocation_id"] = sys.argv[9]
payload["delivery_timer_unit"] = sys.argv[10]
payload["delivery_timer_last_trigger"] = sys.argv[11]
payload["delivery_dry_run"] = sys.argv[12] == "1"
payload["delivery_invocation_key"] = sys.argv[13]
try:
    delivery_payload = json.loads(delivery_path.read_text(encoding="utf-8"))
except Exception:
    delivery_payload = {}
if isinstance(delivery_payload, dict):
    payload["delivery_artifacts"] = delivery_payload.get("artifacts") or {}
    payload["delivery_run_id"] = delivery_payload.get("run_id") or ""
    payload["delivery_quality_status"] = delivery_payload.get("quality_status") or ""
    payload["delivery_quality_generated_at"] = delivery_payload.get("quality_generated_at") or ""
    payload["delivery_source_readiness_status"] = delivery_payload.get("source_readiness_status") or ""
    payload["delivery_source_readiness_generated_at"] = delivery_payload.get("source_readiness_generated_at") or ""
payload["delivery_updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

refresh_agent_task_queue() {
  python "${REMOTE_ROOT}/scripts/build_radar_agent_task_queue.py" >/dev/null 2>&1 || true
  if [[ "${RADAR_AGENT_TASK_AUTORUN}" == "1" ]]; then
    python "${REMOTE_ROOT}/scripts/build_radar_agent_task_queue.py" \
      --run-ready \
      --agent "radar-auto-worker" \
      --max-tasks "${RADAR_AGENT_TASK_AUTORUN_MAX}" \
      --auto-priorities "${RADAR_AGENT_TASK_AUTORUN_PRIORITIES}" >/dev/null 2>&1 || true
  fi
}

on_exit() {
  local rc=$?
  if [[ "${rc}" -ne 0 && "${EXIT_HEALTH_RECORDED}" != "1" ]]; then
    record_delivery_health "fail" "daily report pipeline failed at ${PIPELINE_STAGE}" "${rc}"
    refresh_agent_task_queue
  fi
}

on_signal() {
  local signal_name="$1"
  EXIT_HEALTH_RECORDED=1
  record_delivery_health "fail" "daily report pipeline terminated by ${signal_name} at ${PIPELINE_STAGE}" 143
  refresh_agent_task_queue
  exit 143
}
trap on_exit EXIT
trap 'on_signal SIGTERM' TERM
trap 'on_signal SIGINT' INT

exec 9>"${BUILD_LOCK_PATH}"
flock -w 120 9

PIPELINE_STAGE="build_workspace_outputs"
timeout --signal=TERM --kill-after=30 "${RUN_TIMEOUT_SECONDS}" \
  python "${REMOTE_ROOT}/scripts/build_radar_workspace_outputs.py"
echo "RADAR_DAILY_EMAIL_OUTPUTS_DONE"

if [[ ! -f "${SMTP_ENV_FILE}" ]]; then
  echo "RADAR_DAILY_EMAIL_SKIP missing_smtp_env=${SMTP_ENV_FILE}"
  PIPELINE_STAGE="smtp_env"
  record_delivery_health "warn" "missing_smtp_env=${SMTP_ENV_FILE}" 0
  refresh_agent_task_queue
  exit 0
fi

set +e
PIPELINE_STAGE="send_email"
email_command=(
  python "${REMOTE_ROOT}/scripts/send_radar_daily_report_email.py"
  --config "${REMOTE_ROOT}/config/runtime_defaults.json"
  --report-transport local
  --smtp-env-file "${SMTP_ENV_FILE}"
)
if [[ "${RADAR_DAILY_EMAIL_DRY_RUN}" == "1" ]]; then
  email_command+=(--dry-run)
fi
flock -w 120 "${EMAIL_LOCK_PATH}" \
  timeout --signal=TERM --kill-after=30 900 \
  "${email_command[@]}"
send_status=$?
set -e
if [[ "${send_status}" -ne 0 ]]; then
  echo "RADAR_DAILY_EMAIL_SEND_WARN exit_code=${send_status}"
  record_delivery_health "warn" "send_email_failed exit_code=${send_status}" "${send_status}"
  refresh_agent_task_queue
  if [[ "${RADAR_SMTP_REQUIRED}" == "1" ]]; then
    exit "${send_status}"
  fi
else
  if [[ "${RADAR_DAILY_EMAIL_DRY_RUN}" == "1" ]]; then
    record_delivery_health "pass" "email dry-run rendered successfully" 0
  else
    record_delivery_health "pass" "email sent successfully" 0
  fi
  refresh_agent_task_queue
fi
