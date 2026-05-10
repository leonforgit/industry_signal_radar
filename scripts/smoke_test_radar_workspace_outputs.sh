#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 "$ROOT_DIR/scripts/build_radar_workspace_outputs.py"
python3 "$ROOT_DIR/scripts/check_radar_contract_cases.py"

test -f "$ROOT_DIR/output/snapshots/radar_opportunity_snapshot_latest.json"
test -f "$ROOT_DIR/output/snapshots/radar_bark_summary_latest.json"
test -f "$ROOT_DIR/output/inventory/radar_catalyst_inventory_latest.json"
test -f "$ROOT_DIR/output/inventory/radar_catalyst_inventory_latest.md"
test -f "$ROOT_DIR/output/handoffs/radar_research_handoff_latest.md"
test -f "$ROOT_DIR/output/handoffs/radar_research_handoff_latest.json"
test -f "$ROOT_DIR/output/reports/radar_daily_report_latest.md"
test -f "$ROOT_DIR/output/reports/radar_daily_battlecard_latest.md"
test -f "$ROOT_DIR/output/reports/radar_daily_battlecard_latest.pdf"
test -f "$ROOT_DIR/output/reports/radar_calibration_latest.md"
test -f "$ROOT_DIR/output/reports/radar_report_quality_latest.json"
test -f "$ROOT_DIR/output/reports/radar_daily_battlecard_quality_latest.json"
test -f "$ROOT_DIR/output/runs/radar_daily_battlecard_manifest_latest.json"

echo "radar workspace outputs smoke test passed"
