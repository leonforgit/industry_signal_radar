#!/usr/bin/env python3
"""Ensure Radar local outputs are fresh enough by probing remote state, refreshing if needed, and syncing back."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo

from radar_freshness_utils import summarize_snapshot_freshness


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "runtime_defaults.json"
DEFAULT_LOCAL_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_freshness_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_freshness_latest.md"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="runtime_defaults.json path")
    parser.add_argument("--local-snapshot", type=Path, default=DEFAULT_LOCAL_SNAPSHOT, help="Local snapshot path")
    parser.add_argument(
        "--max-freshness-lag-days",
        type=int,
        default=0,
        help="Maximum allowed lag versus expected market sample date before refresh is required",
    )
    parser.add_argument(
        "--max-sample-age-days",
        type=int,
        default=None,
        help="Optional maximum allowed calendar-like sample age in market days",
    )
    parser.add_argument(
        "--refresh-timeout-seconds",
        type=int,
        default=1800,
        help="Timeout for remote manual refresh execution",
    )
    parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Only probe remote/local freshness and sync remote outputs; do not trigger a remote refresh",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Trigger a remote refresh even when current freshness already satisfies thresholds",
    )
    parser.add_argument(
        "--allow-bark",
        action="store_true",
        help="Allow Bark when triggering a remote refresh. Default is to skip Bark for freshness runs.",
    )
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT, help="Freshness JSON summary path")
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT, help="Freshness Markdown summary path")
    return parser.parse_args(argv)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: Path) -> dict[str, Any]:
    return load_json(path)


def build_ssh_command(host: str, ssh_options: str, remote_command: list[str]) -> list[str]:
    command = ["ssh"]
    if ssh_options.strip():
        command.extend(shlex.split(ssh_options))
    command.append(host)
    command.extend(remote_command)
    return command


def run_ssh_python(host: str, ssh_options: str, script: str) -> subprocess.CompletedProcess[str]:
    command = build_ssh_command(host, ssh_options, ["python3", "-"])
    return subprocess.run(command, input=script, check=False, text=True, capture_output=True)


def query_remote_freshness(config: dict[str, Any]) -> dict[str, Any]:
    deployment = config.get("deployment", {}) if isinstance(config.get("deployment"), dict) else {}
    runtime_paths = config.get("runtime_paths", {}) if isinstance(config.get("runtime_paths"), dict) else {}
    host = str(deployment.get("server_host") or "").strip()
    ssh_options = str(deployment.get("ssh_options") or "")
    output_dir = str(runtime_paths.get("output_dir") or "").strip()
    event_db_path = str(runtime_paths.get("event_db_path") or "").strip()
    if not host or not output_dir or not event_db_path:
        raise SystemExit("Missing remote deployment or runtime output settings for freshness check.")
    remote_script = f"""import json
import sqlite3
from pathlib import Path

output_dir = Path({output_dir!r})
event_db = Path({event_db_path!r})
payload = {{
    "snapshot_latest_exists": False,
    "latest_output": {{}},
    "latest_success_run": None,
}}
snapshot_latest = output_dir / "snapshots" / "radar_opportunity_snapshot_latest.json"
if snapshot_latest.exists():
    try:
        latest_output = json.loads(snapshot_latest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        latest_output = {{}}
    if isinstance(latest_output, dict):
        payload["snapshot_latest_exists"] = True
        payload["latest_output"] = {{
            "run_id": str(latest_output.get("run_id") or latest_output.get("radar_run_id") or ""),
            "as_of_date": str(latest_output.get("as_of_date") or ""),
            "generated_at": str(latest_output.get("generated_at") or ""),
            "candidate_count": int((latest_output.get("summary") or {{}}).get("candidate_count") or 0),
        }}
if event_db.exists():
    conn = sqlite3.connect(str(event_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        '''
        SELECT run_id, started_at, completed_at, status, note
        FROM radar_runs
        WHERE mode = 'scan' AND status = 'success'
        ORDER BY started_at DESC
        LIMIT 1
        '''
    ).fetchone()
    if row is not None:
        payload["latest_success_run"] = dict(row)
print(json.dumps(payload, ensure_ascii=False))
"""
    result = run_ssh_python(host, ssh_options, remote_script)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"ssh exited with {result.returncode}"
        raise SystemExit(f"Failed to query remote freshness: {message}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Remote freshness probe returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Remote freshness probe did not return a JSON object.")
    return payload


def summarize_snapshot(snapshot: dict[str, Any], *, market_tz: str, market_open_time: str) -> dict[str, Any]:
    freshness = summarize_snapshot_freshness(snapshot, market_tz=market_tz, market_open_time=market_open_time)
    return {
        "run_id": str(snapshot.get("run_id") or snapshot.get("radar_run_id") or ""),
        "as_of_date": str(snapshot.get("as_of_date") or ""),
        "generated_at": str(snapshot.get("generated_at") or ""),
        "sample_age_days": freshness.get("sample_age_days"),
        "expected_sample_date": freshness.get("expected_sample_date"),
        "freshness_lag_days": freshness.get("freshness_lag_days"),
        "candidate_count": int((snapshot.get("summary") or {}).get("candidate_count") or snapshot.get("candidate_count") or 0),
    }


def load_local_snapshot(path: Path, *, market_tz: str, market_open_time: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "run_id": "",
            "as_of_date": "",
            "generated_at": "",
            "sample_age_days": None,
            "expected_sample_date": "",
            "freshness_lag_days": None,
            "candidate_count": 0,
        }
    payload = load_json(path)
    summary = summarize_snapshot(payload, market_tz=market_tz, market_open_time=market_open_time)
    summary["exists"] = True
    return summary


def default_run_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def trigger_remote_refresh(config: dict[str, Any], *, timeout_seconds: int, skip_bark: bool) -> dict[str, Any]:
    deployment = config.get("deployment", {}) if isinstance(config.get("deployment"), dict) else {}
    runtime_paths = config.get("runtime_paths", {}) if isinstance(config.get("runtime_paths"), dict) else {}
    host = str(deployment.get("server_host") or "").strip()
    ssh_options = str(deployment.get("ssh_options") or "")
    remote_root = str(deployment.get("remote_root") or "").strip()
    remote_venv = str(deployment.get("remote_venv") or "").strip()
    health_dir = str(runtime_paths.get("health_dir") or f"{remote_root.rstrip('/')}/health").strip()
    lock_dir = str(runtime_paths.get("lock_dir") or f"{remote_root.rstrip('/')}/locks").strip()
    if not host or not remote_root or not remote_venv:
        raise SystemExit("Missing remote deployment settings for freshness refresh.")
    validation_id = f"freshness_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_at = default_run_at()
    remote_status = f"{health_dir.rstrip('/')}/manual_validation_{validation_id}.json"
    remote_log = f"{remote_root.rstrip('/')}/logs/manual_validation_{validation_id}.log"
    remote_lock = f"{lock_dir.rstrip('/')}/industry-signal-radar.lock"
    remote_cmd = [
        f"{remote_venv.rstrip('/')}/bin/python",
        f"{remote_root.rstrip('/')}/scripts/radar_manual_validation.py",
        "--validation-id",
        validation_id,
        "--run-at",
        run_at,
        "--status-file",
        remote_status,
        "--log-file",
        remote_log,
        "--lock-path",
        remote_lock,
    ]
    if skip_bark:
        remote_cmd.append("--skip-bark")
    result = subprocess.run(
        build_ssh_command(host, ssh_options, remote_cmd),
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 60),
    )
    status_payload: dict[str, Any] = {
        "validation_id": validation_id,
        "run_at": run_at,
        "status_file": remote_status,
        "log_file": remote_log,
        "return_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"ssh exited with {result.returncode}"
        raise SystemExit(f"Remote freshness refresh failed: {message}")
    remote_status_result = subprocess.run(
        build_ssh_command(host, ssh_options, ["cat", remote_status]),
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        last_payload = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        last_payload = {}
    if remote_status_result.returncode == 0 and remote_status_result.stdout.strip():
        try:
            last_payload = json.loads(remote_status_result.stdout)
        except json.JSONDecodeError:
            pass
    if isinstance(last_payload, dict):
        status_payload["remote_status"] = last_payload
    return status_payload


def run_sync(build_workspace_outputs: bool = True) -> dict[str, Any]:
    command = [sys.executable, str(ROOT / "scripts" / "sync_latest_radar_payload.py")]
    if build_workspace_outputs:
        command.append("--build-workspace-outputs")
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"sync exited with {result.returncode}"
        raise SystemExit(f"Failed to sync refreshed radar payload: {message}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"raw_stdout": result.stdout.strip()}
    if not isinstance(payload, dict):
        payload = {"raw_stdout": result.stdout.strip()}
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_freshness"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Freshness {payload.get("market_today") or "unknown"}"',
        "---",
        "",
        "# Radar Freshness",
        "",
        f"- 市场日期：`{payload.get('market_today')}`",
        f"- 市场时区：`{payload.get('market_tz')}`",
        f"- 期望样本日期：`{payload.get('expected_sample_date')}`",
        f"- 允许最大 freshness lag：`{payload.get('max_freshness_lag_days')}` 个市场日",
        f"- 允许最大样本年龄：`{payload.get('max_sample_age_days') if payload.get('max_sample_age_days') is not None else 'disabled'}` 天",
        f"- 动作：`{payload.get('action_taken')}`",
        f"- freshness_ok：`{payload.get('freshness_ok')}`",
        "",
        "## Local",
        "",
        f"- run_id：`{((payload.get('local_after') or {}).get('run_id') or (payload.get('local_before') or {}).get('run_id') or '')}`",
        f"- sample_date：`{((payload.get('local_after') or {}).get('as_of_date') or (payload.get('local_before') or {}).get('as_of_date') or '')}`",
        f"- sample_age_days：`{((payload.get('local_after') or {}).get('sample_age_days') if payload.get('local_after') else (payload.get('local_before') or {}).get('sample_age_days'))}`",
        f"- freshness_lag_days：`{((payload.get('local_after') or {}).get('freshness_lag_days') if payload.get('local_after') else (payload.get('local_before') or {}).get('freshness_lag_days'))}`",
        "",
        "## Remote",
        "",
        f"- latest_output_run_id：`{((payload.get('remote_probe_after') or {}).get('latest_output') or {}).get('run_id', '')}`",
        f"- latest_output_sample_date：`{((payload.get('remote_probe_after') or {}).get('latest_output') or {}).get('as_of_date', '')}`",
        f"- latest_output_freshness_lag_days：`{((payload.get('remote_probe_after') or {}).get('latest_output') or {}).get('freshness_lag_days', '')}`",
        f"- latest_success_run：`{((payload.get('remote_probe_after') or {}).get('latest_success_run') or {}).get('run_id', '')}`",
        "",
        "## Refresh",
        "",
    ]
    refresh = payload.get("refresh") or {}
    if refresh:
        remote_status = refresh.get("remote_status") or {}
        lines.extend(
            [
                f"- validation_id：`{refresh.get('validation_id', '')}`",
                f"- run_at：`{refresh.get('run_at', '')}`",
                f"- return_code：`{refresh.get('return_code', '')}`",
                f"- remote_status：`{remote_status.get('status', '')}`",
                f"- target_run_id：`{remote_status.get('target_run_id', '')}`",
                f"- execution_mode：`{remote_status.get('execution_mode', '')}`",
            ]
        )
    else:
        lines.append("- 本轮未触发远端刷新。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = load_config(args.config)
    polling = config.get("polling", {}) if isinstance(config.get("polling"), dict) else {}
    market_tz = str(polling.get("market_timezone") or "Asia/Shanghai")
    market_open_time = str(polling.get("morning_session") or "09:30")
    local_before = load_local_snapshot(args.local_snapshot, market_tz=market_tz, market_open_time=market_open_time)
    remote_probe_before_raw = query_remote_freshness(config)
    remote_output_before = (
        summarize_snapshot(
            remote_probe_before_raw.get("latest_output") or {},
            market_tz=market_tz,
            market_open_time=market_open_time,
        )
        if remote_probe_before_raw.get("latest_output")
        else {}
    )
    remote_probe_before = dict(remote_probe_before_raw)
    if remote_output_before:
        remote_probe_before["latest_output"] = remote_output_before
    action_taken = "sync_only"
    refresh_result: dict[str, Any] | None = None
    remote_lag_before = remote_output_before.get("freshness_lag_days")
    local_lag_before = local_before.get("freshness_lag_days")
    remote_age_before = remote_output_before.get("sample_age_days")
    local_age_before = local_before.get("sample_age_days")
    need_refresh = (
        args.force_refresh
        or not local_before.get("exists")
        or local_lag_before is None
        or int(local_lag_before) > args.max_freshness_lag_days
        or not remote_output_before
        or remote_lag_before is None
        or int(remote_lag_before) > args.max_freshness_lag_days
        or (
            args.max_sample_age_days is not None
            and (
                local_age_before is None
                or int(local_age_before) > args.max_sample_age_days
                or remote_age_before is None
                or int(remote_age_before) > args.max_sample_age_days
            )
        )
    )
    if need_refresh and not args.skip_refresh:
        refresh_result = trigger_remote_refresh(config, timeout_seconds=args.refresh_timeout_seconds, skip_bark=not args.allow_bark)
        action_taken = "refresh_and_sync"
    sync_result = run_sync(build_workspace_outputs=True)
    remote_probe_after = query_remote_freshness(config)
    remote_probe_after = dict(remote_probe_after)
    if remote_probe_after.get("latest_output"):
        remote_probe_after["latest_output"] = summarize_snapshot(
            remote_probe_after.get("latest_output") or {},
            market_tz=market_tz,
            market_open_time=market_open_time,
        )
    local_after = load_local_snapshot(args.local_snapshot, market_tz=market_tz, market_open_time=market_open_time)
    local_age_after = local_after.get("sample_age_days")
    local_lag_after = local_after.get("freshness_lag_days")
    freshness_ok = (
        local_after.get("exists")
        and local_lag_after is not None
        and int(local_lag_after) <= args.max_freshness_lag_days
        and (
            args.max_sample_age_days is None
            or (local_age_after is not None and int(local_age_after) <= args.max_sample_age_days)
        )
    )
    payload = {
        "generated_at": utc_now_iso(),
        "market_tz": market_tz,
        "market_today": datetime.now(ZoneInfo(market_tz)).date().isoformat(),
        "expected_sample_date": str(local_after.get("expected_sample_date") or local_before.get("expected_sample_date") or ""),
        "max_freshness_lag_days": args.max_freshness_lag_days,
        "max_sample_age_days": args.max_sample_age_days,
        "action_taken": action_taken,
        "freshness_ok": bool(freshness_ok),
        "local_before": local_before,
        "remote_probe_before": remote_probe_before,
        "refresh": refresh_result or {},
        "sync": sync_result,
        "remote_probe_after": remote_probe_after,
        "local_after": local_after,
    }
    write_json(args.json_output, payload)
    write_text(args.md_output, render_markdown(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if freshness_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
