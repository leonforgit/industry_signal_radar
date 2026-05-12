#!/usr/bin/env python3
"""Mirror the latest remote radar payload into the local repo output directory."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from radar_upstream_bridge import build_scp_command, quote_remote_command


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "runtime_defaults.json"
DEFAULT_LOCAL_OUTPUT_PATH = ROOT / "output" / "industry_signal_scan_latest.json"
DEFAULT_LOCAL_SUMMARY_PATH = ROOT / "output" / "industry_signal_scan_bridge_latest.json"


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_remote_payload_path(config: dict[str, Any]) -> str:
    runtime_paths = config.get("runtime_paths", {}) if isinstance(config.get("runtime_paths"), dict) else {}
    output_dir = str(runtime_paths.get("output_dir") or "").strip()
    if output_dir:
        return f"{output_dir.rstrip('/')}/industry_signal_scan_latest.json"
    deployment = config.get("deployment", {}) if isinstance(config.get("deployment"), dict) else {}
    remote_root = str(deployment.get("remote_root") or "").strip()
    if remote_root:
        return f"{remote_root.rstrip('/')}/output/industry_signal_scan_latest.json"
    return "/opt/industry-signal-radar/output/industry_signal_scan_latest.json"


def build_ssh_command(host: str, ssh_options: str, remote_path: str) -> list[str]:
    command = ["ssh"]
    if ssh_options.strip():
        command.extend(shlex.split(ssh_options))
    command.extend([host, quote_remote_command(["cat", remote_path])])
    return command


def run_remote_python(host: str, ssh_options: str, remote_script: str) -> subprocess.CompletedProcess[str]:
    command = ["ssh"]
    if ssh_options.strip():
        command.extend(shlex.split(ssh_options))
    command.extend([host, quote_remote_command(["python3", "-"])])
    return subprocess.run(command, input=remote_script, check=False, capture_output=True, text=True)


def fetch_all_industries(
    *,
    host: str,
    ssh_options: str,
    event_db_path: str,
    run_id: str | None,
) -> list[dict[str, Any]]:
    if not run_id:
        return []
    remote_script = f"""import json
import sqlite3
from pathlib import Path

db_path = Path({event_db_path!r})
run_id = {run_id!r}
if not db_path.exists():
    raise SystemExit("missing_event_db")

conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row
rows = conn.execute(
    '''
    SELECT raw_snapshot_json
    FROM signal_snapshots
    WHERE run_id = ?
    ORDER BY total_score DESC, industry_id ASC
    ''',
    (run_id,),
).fetchall()
payload = []
for row in rows:
    raw_snapshot = row["raw_snapshot_json"]
    if not raw_snapshot:
        continue
    try:
        snapshot = json.loads(raw_snapshot)
    except json.JSONDecodeError:
        continue
    if not isinstance(snapshot, dict):
        continue
    if not snapshot.get("industry_label") and snapshot.get("display_name_cn"):
        snapshot["industry_label"] = snapshot.get("display_name_cn")
    payload.append(snapshot)
print(json.dumps(payload, ensure_ascii=False))
"""
    result = run_remote_python(host, ssh_options, remote_script)
    if result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def summarize_payload(payload: dict[str, Any], source_path: str) -> dict[str, Any]:
    top_industries = payload.get("top_industries", [])
    alerts = payload.get("alerts", [])
    summary = {
        "synced_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_remote_path": source_path,
        "run_id": payload.get("run_id"),
        "run_at": payload.get("run_at"),
        "generated_at": payload.get("generated_at"),
        "market_phase": payload.get("market_phase"),
        "execution_mode": payload.get("execution_mode"),
        "industry_count": payload.get("industry_count"),
        "alert_count": payload.get("alert_count"),
        "top_industries": top_industries[:5] if isinstance(top_industries, list) else [],
        "alerts": alerts[:5] if isinstance(alerts, list) else [],
        "openbb_market_context": payload.get("openbb_market_context", {}),
        "openbb_fred_context": payload.get("openbb_fred_context", {}),
        "openbb_shipping_context": payload.get("openbb_shipping_context", {}),
    }
    return summary


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_remote_text(host: str, ssh_options: str, remote_path: str) -> str | None:
    command = build_ssh_command(host, ssh_options, remote_path)
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout


def write_remote_json_mirror(host: str, ssh_options: str, remote_path: str, local_path: Path) -> bool:
    raw = fetch_remote_text(host, ssh_options, remote_path)
    if raw is None:
        return False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    write_json(local_path, payload)
    return True


def write_remote_text_mirror(host: str, ssh_options: str, remote_path: str, local_path: Path) -> bool:
    raw = fetch_remote_text(host, ssh_options, remote_path)
    if raw is None:
        return False
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(raw, encoding="utf-8")
    return True


def copy_remote_file(host: str, ssh_options: str, remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_scp_command(ssh_options, f"{host}:{remote_path}", str(local_path))
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"Failed to copy {remote_path}")


def copy_remote_mirror(host: str, ssh_options: str, remote_path: str, local_path: Path) -> bool:
    try:
        copy_remote_file(host, ssh_options, remote_path, local_path)
    except RuntimeError:
        return False
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="runtime_defaults.json path")
    parser.add_argument("--host", default=None, help="Override remote SSH host")
    parser.add_argument("--ssh-options", default=None, help="Override SSH options")
    parser.add_argument("--remote-path", default=None, help="Override remote latest payload path")
    parser.add_argument("--output", type=Path, default=DEFAULT_LOCAL_OUTPUT_PATH, help="Local mirrored payload path")
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=DEFAULT_LOCAL_SUMMARY_PATH,
        help="Local bridge summary payload path",
    )
    parser.add_argument(
        "--build-workspace-outputs",
        action="store_true",
        help="After sync, rebuild snapshot, Bark summary, and daily report locally.",
    )
    return parser.parse_args(argv)


def run_local_workspace_builder() -> None:
    command = [sys.executable, str(ROOT / "scripts" / "build_radar_workspace_outputs.py")]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"builder exited with {result.returncode}"
        raise SystemExit(f"Failed to rebuild local Radar outputs: {message}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = load_config(args.config)
    deployment = config.get("deployment", {}) if isinstance(config.get("deployment"), dict) else {}
    runtime_paths = config.get("runtime_paths", {}) if isinstance(config.get("runtime_paths"), dict) else {}
    sentiment_sidecar = config.get("sentiment_sidecar", {}) if isinstance(config.get("sentiment_sidecar"), dict) else {}
    price_sidecar = config.get("price_sidecar", {}) if isinstance(config.get("price_sidecar"), dict) else {}
    host = args.host or str(deployment.get("server_host") or "").strip()
    ssh_options = args.ssh_options if args.ssh_options is not None else str(deployment.get("ssh_options") or "")
    remote_path = args.remote_path or default_remote_payload_path(config)
    event_db_path = str(runtime_paths.get("event_db_path") or "").strip()

    if not host:
        raise SystemExit("No remote host configured for radar payload sync.")

    command = build_ssh_command(host, ssh_options, remote_path)
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"ssh exited with {result.returncode}"
        raise SystemExit(f"Failed to fetch remote radar payload: {message}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Remote radar payload is not valid JSON: {exc}") from exc

    if isinstance(payload, dict) and not isinstance(payload.get("all_industries"), list) and event_db_path:
        all_industries = fetch_all_industries(
            host=host,
            ssh_options=ssh_options,
            event_db_path=event_db_path,
            run_id=str(payload.get("run_id") or "").strip() or None,
        )
        if all_industries:
            payload["all_industries"] = all_industries

    summary = summarize_payload(payload, remote_path)
    write_json(args.output, payload)
    write_json(args.summary_output, summary)
    mirrored_outputs = {
        "candidate_pool": write_remote_json_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/snapshots/radar_candidate_pool_latest.json",
            ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "opportunity_snapshot": write_remote_json_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/snapshots/radar_opportunity_snapshot_latest.json",
            ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "bark_summary": write_remote_json_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/snapshots/radar_bark_summary_latest.json",
            ROOT / "output" / "snapshots" / "radar_bark_summary_latest.json",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "catalyst_inventory": write_remote_json_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/inventory/radar_catalyst_inventory_latest.json",
            ROOT / "output" / "inventory" / "radar_catalyst_inventory_latest.json",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "catalyst_inventory_md": write_remote_text_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/inventory/radar_catalyst_inventory_latest.md",
            ROOT / "output" / "inventory" / "radar_catalyst_inventory_latest.md",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "research_handoff": write_remote_text_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/handoffs/radar_research_handoff_latest.md",
            ROOT / "output" / "handoffs" / "radar_research_handoff_latest.md",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "research_handoff_json": write_remote_json_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/handoffs/radar_research_handoff_latest.json",
            ROOT / "output" / "handoffs" / "radar_research_handoff_latest.json",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "daily_report": write_remote_text_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/reports/radar_daily_report_latest.md",
            ROOT / "output" / "reports" / "radar_daily_report_latest.md",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "daily_battlecard": write_remote_text_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/reports/radar_daily_battlecard_latest.md",
            ROOT / "output" / "reports" / "radar_daily_battlecard_latest.md",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "report_quality_json": write_remote_json_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/reports/radar_report_quality_latest.json",
            ROOT / "output" / "reports" / "radar_report_quality_latest.json",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "report_quality_md": write_remote_text_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/reports/radar_report_quality_latest.md",
            ROOT / "output" / "reports" / "radar_report_quality_latest.md",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "battlecard_quality_json": write_remote_json_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/reports/radar_daily_battlecard_quality_latest.json",
            ROOT / "output" / "reports" / "radar_daily_battlecard_quality_latest.json",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "battlecard_quality_md": write_remote_text_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/reports/radar_daily_battlecard_quality_latest.md",
            ROOT / "output" / "reports" / "radar_daily_battlecard_quality_latest.md",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "daily_report_pdf": copy_remote_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/reports/radar_daily_report_latest.pdf",
            ROOT / "output" / "reports" / "radar_daily_report_latest.pdf",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "daily_battlecard_pdf": copy_remote_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/reports/radar_daily_battlecard_latest.pdf",
            ROOT / "output" / "reports" / "radar_daily_battlecard_latest.pdf",
        )
        if str(runtime_paths.get("output_dir") or "").strip()
        else False,
        "sentiment_market_csv": write_remote_text_mirror(
            host,
            ssh_options,
            str(sentiment_sidecar.get("market_csv_path") or ""),
            ROOT / "output" / "sidecars" / "sentiment" / "market_sentiment_factor_daily.csv",
        )
        if str(sentiment_sidecar.get("market_csv_path") or "").strip()
        else False,
        "sentiment_company_csv": write_remote_text_mirror(
            host,
            ssh_options,
            str(sentiment_sidecar.get("company_csv_path") or ""),
            ROOT / "output" / "sidecars" / "sentiment" / "company_sentiment_score_daily.csv",
        )
        if str(sentiment_sidecar.get("company_csv_path") or "").strip()
        else False,
        "company_price_csv": write_remote_text_mirror(
            host,
            ssh_options,
            f"{str(runtime_paths.get('output_dir') or '').rstrip('/')}/sidecars/equity_prices/company_price_snapshot_latest.csv",
            ROOT / "output" / "sidecars" / "equity_prices" / Path(
                str(price_sidecar.get("company_price_csv_path") or "output/sidecars/equity_prices/company_price_snapshot_latest.csv")
            ).name,
        )
        if bool(price_sidecar.get("enabled", True)) and str(runtime_paths.get("output_dir") or "").strip()
        else False,
    }
    if args.build_workspace_outputs:
        run_local_workspace_builder()
    print(
        json.dumps(
            {
                "output_path": str(args.output),
                "summary_output_path": str(args.summary_output),
                "run_id": payload.get("run_id"),
                "generated_at": payload.get("generated_at"),
                "alert_count": payload.get("alert_count"),
                "workspace_outputs_built": bool(args.build_workspace_outputs),
                "mirrored_outputs": mirrored_outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
