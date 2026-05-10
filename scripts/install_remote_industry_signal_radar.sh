#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${CONFIG_PATH:-${PROJECT_ROOT}/config/runtime_defaults.json}"

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
SERVICE_NAME="${SERVICE_NAME:-$(config_value deployment.service_name)}"
TIMER_NAME="${TIMER_NAME:-$(config_value deployment.timer_name)}"
DAILY_REPORT_SERVICE_NAME="${DAILY_REPORT_SERVICE_NAME:-$(config_value deployment.daily_report_service_name)}"
DAILY_REPORT_TIMER_NAME="${DAILY_REPORT_TIMER_NAME:-$(config_value deployment.daily_report_timer_name)}"
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
    python3 - "${remote_path}" <<'PY'
import shlex
import sys
from pathlib import Path

remote_path = Path(sys.argv[1])
print(f"mkdir -p {shlex.quote(str(remote_path.parent))} && cat > {shlex.quote(str(remote_path))}")
PY
  )"
  if [[ ${#SSH_OPTS_ARR[@]} -gt 0 ]]; then
    cat "${source_path}" | ssh "${SSH_OPTS_ARR[@]}" "${REMOTE_HOST}" "${remote_command}"
  else
    cat "${source_path}" | ssh "${REMOTE_HOST}" "${remote_command}"
  fi
}

echo "[0/6] Ensuring remote directories"
ssh_remote "mkdir -p '${REMOTE_ROOT}/scripts' '${REMOTE_ROOT}/config' '${REMOTE_ROOT}/data' '${REMOTE_ROOT}/state' '${REMOTE_ROOT}/logs' '${REMOTE_ROOT}/cache' '${REMOTE_ROOT}/output' '${REMOTE_ROOT}/output/snapshots' '${REMOTE_ROOT}/output/reports' '${REMOTE_ROOT}/output/inventory' '${REMOTE_ROOT}/output/sidecars/equity_prices' '${REMOTE_ROOT}/output/sidecars/fundamentals' '${REMOTE_ROOT}/output/sidecars/market' '${REMOTE_ROOT}/health' '${REMOTE_ROOT}/locks' '/opt/quant-runtime/data_substrate/radar_market'"

echo "[1/6] Syncing runtime scripts"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_config.py" "${REMOTE_ROOT}/scripts/radar_config.py"
copy_file_remote "${PROJECT_ROOT}/scripts/_report_toolkit.py" "${REMOTE_ROOT}/scripts/_report_toolkit.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_bark.py" "${REMOTE_ROOT}/scripts/radar_bark.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_company_mapping.py" "${REMOTE_ROOT}/scripts/radar_company_mapping.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_company_targets.py" "${REMOTE_ROOT}/scripts/radar_company_targets.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_announcements.py" "${REMOTE_ROOT}/scripts/radar_announcements.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_event_db.py" "${REMOTE_ROOT}/scripts/radar_event_db.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_flow_signals.py" "${REMOTE_ROOT}/scripts/radar_flow_signals.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_industry_registry.py" "${REMOTE_ROOT}/scripts/radar_industry_registry.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_news_overlay_routing.py" "${REMOTE_ROOT}/scripts/radar_news_overlay_routing.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_news_policy.py" "${REMOTE_ROOT}/scripts/radar_news_policy.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_shared_news.py" "${REMOTE_ROOT}/scripts/radar_shared_news.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_fundamental_proxy.py" "${REMOTE_ROOT}/scripts/radar_fundamental_proxy.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_manual_validation.py" "${REMOTE_ROOT}/scripts/radar_manual_validation.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_runtime_health.py" "${REMOTE_ROOT}/scripts/radar_runtime_health.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_runtime_bootstrap.py" "${REMOTE_ROOT}/scripts/radar_runtime_bootstrap.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_freshness_utils.py" "${REMOTE_ROOT}/scripts/radar_freshness_utils.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_market_sidecar.py" "${REMOTE_ROOT}/scripts/radar_market_sidecar.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_market_access.py" "${REMOTE_ROOT}/scripts/radar_market_access.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_price_sidecar.py" "${REMOTE_ROOT}/scripts/radar_price_sidecar.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_signal_access.py" "${REMOTE_ROOT}/scripts/radar_signal_access.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_sentiment_sidecar.py" "${REMOTE_ROOT}/scripts/radar_sentiment_sidecar.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_upstream_bridge.py" "${REMOTE_ROOT}/scripts/radar_upstream_bridge.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_scan_runner.py" "${REMOTE_ROOT}/scripts/radar_scan_runner.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_validation_summary.py" "${REMOTE_ROOT}/scripts/radar_validation_summary.py"
copy_file_remote "${PROJECT_ROOT}/scripts/evaluate_news_source_overlap.py" "${REMOTE_ROOT}/scripts/evaluate_news_source_overlap.py"
copy_file_remote "${PROJECT_ROOT}/scripts/evaluate_news_industry_mapping.py" "${REMOTE_ROOT}/scripts/evaluate_news_industry_mapping.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_news_keyword_candidate_report.py" "${REMOTE_ROOT}/scripts/build_news_keyword_candidate_report.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_company_mapping_cache.py" "${REMOTE_ROOT}/scripts/build_company_mapping_cache.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_representative_stock_candidates.py" "${REMOTE_ROOT}/scripts/build_representative_stock_candidates.py"
copy_file_remote "${PROJECT_ROOT}/scripts/sync_shared_news_event_hub_exports.py" "${REMOTE_ROOT}/scripts/sync_shared_news_event_hub_exports.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_market_substrate.py" "${REMOTE_ROOT}/scripts/build_radar_market_substrate.py"
copy_file_remote "${PROJECT_ROOT}/scripts/ensure_radar_sentiment_freshness.py" "${REMOTE_ROOT}/scripts/ensure_radar_sentiment_freshness.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_source_readiness.py" "${REMOTE_ROOT}/scripts/build_radar_source_readiness.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_candidate_pool.py" "${REMOTE_ROOT}/scripts/build_radar_candidate_pool.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_company_enrichment_sidecar.py" "${REMOTE_ROOT}/scripts/build_radar_company_enrichment_sidecar.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_canonical_price_bridge.py" "${REMOTE_ROOT}/scripts/build_radar_canonical_price_bridge.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_canonical_fundamental_bridge.py" "${REMOTE_ROOT}/scripts/build_radar_canonical_fundamental_bridge.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_quant_signal_sidecar.py" "${REMOTE_ROOT}/scripts/build_radar_quant_signal_sidecar.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_canonical_retry.py" "${REMOTE_ROOT}/scripts/radar_canonical_retry.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_canonical_writeback_queue.py" "${REMOTE_ROOT}/scripts/radar_canonical_writeback_queue.py"
copy_file_remote "${PROJECT_ROOT}/scripts/radar_openbb_service.py" "${REMOTE_ROOT}/scripts/radar_openbb_service.py"
copy_file_remote "${PROJECT_ROOT}/scripts/ensure_radar_price_freshness.py" "${REMOTE_ROOT}/scripts/ensure_radar_price_freshness.py"
copy_file_remote "${PROJECT_ROOT}/scripts/ensure_radar_fundamental_coverage.py" "${REMOTE_ROOT}/scripts/ensure_radar_fundamental_coverage.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_company_price_sidecar.py" "${REMOTE_ROOT}/scripts/build_radar_company_price_sidecar.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_opportunity_snapshot.py" "${REMOTE_ROOT}/scripts/build_radar_opportunity_snapshot.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_news_verification_sidecar.py" "${REMOTE_ROOT}/scripts/build_radar_news_verification_sidecar.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_catalyst_inventory.py" "${REMOTE_ROOT}/scripts/build_radar_catalyst_inventory.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_bark_summary.py" "${REMOTE_ROOT}/scripts/build_radar_bark_summary.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_research_handoff.py" "${REMOTE_ROOT}/scripts/build_radar_research_handoff.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_ipo_watchlist.py" "${REMOTE_ROOT}/scripts/build_radar_ipo_watchlist.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_hk_ipo_watchlist.py" "${REMOTE_ROOT}/scripts/build_radar_hk_ipo_watchlist.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_kimi_editorial.py" "${REMOTE_ROOT}/scripts/build_radar_kimi_editorial.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_kimi_research_harness.py" "${REMOTE_ROOT}/scripts/build_radar_kimi_research_harness.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_structural_signal_sidecar.py" "${REMOTE_ROOT}/scripts/build_radar_structural_signal_sidecar.py"
copy_file_remote "${PROJECT_ROOT}/scripts/render_radar_daily_report.py" "${REMOTE_ROOT}/scripts/render_radar_daily_report.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_calibration_summary.py" "${REMOTE_ROOT}/scripts/build_radar_calibration_summary.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_data_substrate_audit.py" "${REMOTE_ROOT}/scripts/build_radar_data_substrate_audit.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_agent_task_queue.py" "${REMOTE_ROOT}/scripts/build_radar_agent_task_queue.py"
copy_file_remote "${PROJECT_ROOT}/scripts/validate_radar_opportunity_snapshot.py" "${REMOTE_ROOT}/scripts/validate_radar_opportunity_snapshot.py"
copy_file_remote "${PROJECT_ROOT}/scripts/validate_radar_catalyst_inventory.py" "${REMOTE_ROOT}/scripts/validate_radar_catalyst_inventory.py"
copy_file_remote "${PROJECT_ROOT}/scripts/validate_radar_research_handoff.py" "${REMOTE_ROOT}/scripts/validate_radar_research_handoff.py"
copy_file_remote "${PROJECT_ROOT}/scripts/validate_radar_kimi_research.py" "${REMOTE_ROOT}/scripts/validate_radar_kimi_research.py"
copy_file_remote "${PROJECT_ROOT}/scripts/build_radar_workspace_outputs.py" "${REMOTE_ROOT}/scripts/build_radar_workspace_outputs.py"
copy_file_remote "${PROJECT_ROOT}/scripts/check_radar_report_quality.py" "${REMOTE_ROOT}/scripts/check_radar_report_quality.py"
copy_file_remote "${PROJECT_ROOT}/scripts/send_radar_daily_report_email.py" "${REMOTE_ROOT}/scripts/send_radar_daily_report_email.py"

echo "[2/6] Syncing runtime config"
copy_file_remote "${PROJECT_ROOT}/config/runtime_defaults.json" "${REMOTE_ROOT}/config/runtime_defaults.json"
copy_file_remote "${PROJECT_ROOT}/config/source_manifest.json" "${REMOTE_ROOT}/config/source_manifest.json"
copy_file_remote "${PROJECT_ROOT}/config/event_db_schema.sql" "${REMOTE_ROOT}/config/event_db_schema.sql"
copy_file_remote "${PROJECT_ROOT}/config/radar_candidate_pool_schema_v1.json" "${REMOTE_ROOT}/config/radar_candidate_pool_schema_v1.json"
copy_file_remote "${PROJECT_ROOT}/config/radar_opportunity_snapshot_schema_v1.json" "${REMOTE_ROOT}/config/radar_opportunity_snapshot_schema_v1.json"
copy_file_remote "${PROJECT_ROOT}/config/radar_catalyst_inventory_schema_v1.json" "${REMOTE_ROOT}/config/radar_catalyst_inventory_schema_v1.json"
copy_file_remote "${PROJECT_ROOT}/config/radar_research_handoff_schema_v1.json" "${REMOTE_ROOT}/config/radar_research_handoff_schema_v1.json"
copy_file_remote "${PROJECT_ROOT}/config/radar_kimi_research_schema_v1.json" "${REMOTE_ROOT}/config/radar_kimi_research_schema_v1.json"
copy_file_remote "${PROJECT_ROOT}/config/radar_report_rules_v1.json" "${REMOTE_ROOT}/config/radar_report_rules_v1.json"
copy_file_remote "${PROJECT_ROOT}/config/remote_requirements.lock.txt" "${REMOTE_ROOT}/config/remote_requirements.lock.txt"
copy_file_remote "${PROJECT_ROOT}/config/industry_signal_radar.env.example" "${REMOTE_ROOT}/config/industry_signal_radar.env.example"
copy_file_remote "${PROJECT_ROOT}/data/industry_registry_sw_level1.csv" "${REMOTE_ROOT}/data/industry_registry_sw_level1.csv"
copy_file_remote "${PROJECT_ROOT}/data/fundamental_proxy_definitions.json" "${REMOTE_ROOT}/data/fundamental_proxy_definitions.json"
copy_file_remote "${PROJECT_ROOT}/data/industry_etf_proxy_candidates.csv" "${REMOTE_ROOT}/data/industry_etf_proxy_candidates.csv"
copy_file_remote "${PROJECT_ROOT}/data/industry_etf_proxies_primary.csv" "${REMOTE_ROOT}/data/industry_etf_proxies_primary.csv"
copy_file_remote "${PROJECT_ROOT}/data/industry_representative_stock_candidates.csv" "${REMOTE_ROOT}/data/industry_representative_stock_candidates.csv"
copy_file_remote "${PROJECT_ROOT}/data/industry_representative_stocks_primary.csv" "${REMOTE_ROOT}/data/industry_representative_stocks_primary.csv"
copy_file_remote "${PROJECT_ROOT}/data/theme_chain_overlays.csv" "${REMOTE_ROOT}/data/theme_chain_overlays.csv"
copy_file_remote "${PROJECT_ROOT}/data/theme_chain_overlay_members.csv" "${REMOTE_ROOT}/data/theme_chain_overlay_members.csv"
copy_file_remote "${PROJECT_ROOT}/data/non_industry_news_overlays.csv" "${REMOTE_ROOT}/data/non_industry_news_overlays.csv"
copy_file_remote "${PROJECT_ROOT}/data/radar_candidate_pool_fixture_v1.json" "${REMOTE_ROOT}/data/radar_candidate_pool_fixture_v1.json"
copy_file_remote "${PROJECT_ROOT}/data/radar_contract_validation_cases_v1.json" "${REMOTE_ROOT}/data/radar_contract_validation_cases_v1.json"
copy_file_remote "${PROJECT_ROOT}/data/radar_opportunity_snapshot_fixture_v1.json" "${REMOTE_ROOT}/data/radar_opportunity_snapshot_fixture_v1.json"
LOCAL_COMPANY_CACHE="${PROJECT_ROOT}/cache/company_name_index/industry_company_name_index.json"
if [[ -f "${LOCAL_COMPANY_CACHE}" ]]; then
  echo "[2a/6] Seeding remote company-name cache from local cache"
  copy_file_remote "${LOCAL_COMPANY_CACHE}" "${REMOTE_ROOT}/cache/company_name_index/industry_company_name_index.json"
fi

echo "[3/6] Writing remote wrapper"
copy_file_remote "${PROJECT_ROOT}/scripts/run_industry_signal_radar.sh" "${REMOTE_ROOT}/run_industry_signal_radar.sh"
copy_file_remote "${PROJECT_ROOT}/scripts/run_radar_daily_report_email.sh" "${REMOTE_ROOT}/run_radar_daily_report_email.sh"
copy_file_remote "${PROJECT_ROOT}/scripts/run_radar_daily_report_email.sh" "${REMOTE_ROOT}/scripts/run_radar_daily_report_email.sh"
ssh_remote "chmod +x '${REMOTE_ROOT}/run_industry_signal_radar.sh' '${REMOTE_ROOT}/run_radar_daily_report_email.sh' '${REMOTE_ROOT}/scripts/run_radar_daily_report_email.sh' && if [ ! -f '${REMOTE_ROOT}/config/industry_signal_radar.env' ]; then cp '${REMOTE_ROOT}/config/industry_signal_radar.env.example' '${REMOTE_ROOT}/config/industry_signal_radar.env'; fi"

echo "[4/6] Writing systemd units"
ssh_remote "cat > /etc/systemd/system/${SERVICE_NAME} <<'EOF'
$(cat "${PROJECT_ROOT}/config/systemd/industry-signal-radar.service")
EOF
cat > /etc/systemd/system/${TIMER_NAME} <<'EOF'
$(cat "${PROJECT_ROOT}/config/systemd/industry-signal-radar.timer")
EOF
cat > /etc/systemd/system/${DAILY_REPORT_SERVICE_NAME} <<'EOF'
$(cat "${PROJECT_ROOT}/config/systemd/industry-signal-radar-daily-report.service")
EOF
cat > /etc/systemd/system/${DAILY_REPORT_TIMER_NAME} <<'EOF'
$(cat "${PROJECT_ROOT}/config/systemd/industry-signal-radar-daily-report.timer")
EOF
systemctl daemon-reload"

echo "[5/6] Running remote bootstrap check-only"
ssh_remote "source '${REMOTE_VENV}/bin/activate' && python '${REMOTE_ROOT}/scripts/radar_runtime_bootstrap.py' --check-only"

echo "[6/6] Enabling timer"
ssh_remote "systemctl enable --now '${TIMER_NAME}' '${DAILY_REPORT_TIMER_NAME}'; systemctl status '${TIMER_NAME}' --no-pager; systemctl status '${DAILY_REPORT_TIMER_NAME}' --no-pager"

echo "Remote Industry Signal Radar installed on ${REMOTE_HOST}"
