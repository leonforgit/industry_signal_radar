#!/usr/bin/env python3
"""Ensure Radar price inputs are fresh by calling the canonical price system when needed."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from radar_canonical_writeback_queue import DEFAULT_PRICE_QUEUE
from radar_company_targets import extract_company_targets, normalize_company_name, stock_code_to_market_symbol
from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_freshness_utils import previous_market_weekday_text
from radar_price_sidecar import resolve_repo_path
from radar_upstream_bridge import (
    build_scp_command,
    build_ssh_command,
    load_deployment_config,
    run_local_python,
    run_remote_python,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_price_freshness_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_price_freshness_latest.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL)
    parser.add_argument("--target-date", default="", help="Optional explicit target date (YYYY-MM-DD).")
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--audit-only", action="store_true", help="Only rewrite freshness status from current local inputs; do not call upstream providers.")
    return parser.parse_args()


def load_target_date(args: argparse.Namespace) -> str:
    if str(args.target_date or "").strip():
        return str(args.target_date).strip()[:10]
    payload = json.loads(args.input_candidate_pool.read_text(encoding="utf-8"))
    for key in ("market_sample_date", "expected_sample_date", "as_of_date", "run_at", "generated_at"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value[:10]
    raise SystemExit(f"Unable to infer target date from {args.input_candidate_pool}")


def load_company_target_payload(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return extract_company_targets(payload)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def local_queue_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(payload) if isinstance(payload, list) else 0


def prune_local_price_writeback_queue(*, target_date: str, company_targets: list[dict[str, str]], queue_path: Path) -> int:
    if not queue_path.exists():
        return 0
    try:
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, list):
        return 0
    target_symbols = {
        str(item.get("market_symbol") or "").strip().upper()
        for item in company_targets
        if str(item.get("market_symbol") or "").strip()
    }
    if not target_symbols:
        return 0
    kept: list[dict[str, Any]] = []
    pruned = 0
    for record in payload:
        if not isinstance(record, dict):
            kept.append(record)
            continue
        symbol = str(record.get("symbol") or "").strip().upper()
        record_target = str(record.get("target_date") or record.get("last_trade_date") or "").strip()[:10]
        if symbol in target_symbols and record_target and record_target <= target_date:
            pruned += 1
            continue
        kept.append(record)
    if pruned:
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pruned


def overwrite_local_queue(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bridge_needed_names(csv_status: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in csv_status.get("stale_targets") or []:
        if isinstance(item, dict):
            name = normalize_company_name(item.get("radar_object_name"))
            if name:
                names.add(name)
    for item in csv_status.get("missing_targets") or []:
        name = normalize_company_name(item)
        if name:
            names.add(name)
    return names


def write_scoped_bridge_candidate_pool(
    *,
    input_candidate_pool: Path,
    output_candidate_pool: Path,
    csv_status: dict[str, Any],
    max_candidates: int = 0,
) -> tuple[Path, int]:
    needed_names = bridge_needed_names(csv_status)
    if not needed_names:
        return input_candidate_pool, 0
    try:
        payload = json.loads(input_candidate_pool.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return input_candidate_pool, 0
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list):
        return input_candidate_pool, 0
    scoped_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and normalize_company_name(candidate.get("radar_object_name")) in needed_names
    ]
    if max_candidates > 0:
        scoped_candidates = scoped_candidates[:max_candidates]
    if not scoped_candidates:
        return input_candidate_pool, 0
    scoped_payload = dict(payload)
    scoped_payload["candidates"] = scoped_candidates
    scoped_payload["bridge_scope"] = {
        "source_candidate_count": len(candidates),
        "scoped_candidate_count": len(scoped_candidates),
        "needed_company_count": len(needed_names),
        "max_candidates": max_candidates,
    }
    output_candidate_pool.parent.mkdir(parents=True, exist_ok=True)
    output_candidate_pool.write_text(json.dumps(scoped_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_candidate_pool, len(scoped_candidates)


def query_price_db(db_path: Path, *, target_date: str) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "exists": False,
            "latest_trade_date": "",
            "target_row_count": 0,
        }
    conn = sqlite3.connect(db_path)
    try:
        latest = conn.execute("SELECT MAX(trade_date) FROM equity_price_daily").fetchone()
        previous_trade = conn.execute(
            "SELECT MAX(trade_date) FROM equity_price_daily WHERE trade_date < ?",
            (target_date,),
        ).fetchone()
        target_count = conn.execute(
            "SELECT COUNT(*) FROM equity_price_daily WHERE trade_date = ?",
            (target_date,),
        ).fetchone()
    except sqlite3.Error:
        return {
            "exists": True,
            "latest_trade_date": "",
            "previous_trade_date": "",
            "target_row_count": 0,
        }
    finally:
        conn.close()
    return {
        "exists": True,
        "latest_trade_date": str((latest or [""])[0] or ""),
        "previous_trade_date": str((previous_trade or [""])[0] or ""),
        "target_row_count": int((target_count or [0])[0] or 0),
    }


def db_instruments_on_target(db_path: Path, *, target_date: str) -> set[str]:
    if not db_path.exists() or not target_date:
        return set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT instrument FROM equity_price_daily WHERE trade_date = ?",
            (target_date,),
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        conn.close()
    return {str(row[0] or "").strip().upper() for row in rows if str(row[0] or "").strip()}


def query_price_csv(
    csv_path: Path,
    *,
    target_date: str = "",
    company_targets: list[dict[str, str]] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if not csv_path.exists():
        return {
            "exists": False,
            "latest_as_of_date": "",
            "row_count": 0,
            "stale_targets": [],
            "non_trading_targets": [],
            "missing_targets": [],
        }
    try:
        frame = pd.read_csv(csv_path)
    except Exception:
        return {
            "exists": True,
            "latest_as_of_date": "",
            "row_count": 0,
            "stale_targets": [],
            "non_trading_targets": [],
            "missing_targets": [],
        }
    if frame.empty or "as_of_date" not in frame.columns:
        return {
            "exists": True,
            "latest_as_of_date": "",
            "row_count": int(len(frame)),
            "stale_targets": [],
            "non_trading_targets": [],
            "missing_targets": [],
        }
    parsed = pd.to_datetime(frame["as_of_date"], errors="coerce").dropna()
    latest = parsed.max().date().isoformat() if not parsed.empty else ""
    stale_targets: list[dict[str, Any]] = []
    non_trading_targets: list[dict[str, Any]] = []
    missing_targets: list[str] = []
    targets = company_targets or []
    previous_trade_date = ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(target_date or "").strip()):
        previous_trade_date = previous_market_weekday_text(target_date)
    db_target_instruments = db_instruments_on_target(db_path, target_date=target_date) if db_path is not None else set()
    if targets and "company_name" in frame.columns and target_date:
        frame = frame.copy()
        frame["_company_key"] = frame["company_name"].astype(str).str.strip()
        frame["_code_key"] = (
            frame["instrument"].astype(str).map(stock_code_to_market_symbol)
            if "instrument" in frame.columns
            else ""
        )
        parsed_target = pd.to_datetime(target_date, errors="coerce")
        for item in targets:
            name = str(item.get("radar_object_name") or "").strip()
            market_symbol = str(item.get("market_symbol") or "").strip()
            subset = frame.loc[frame["_company_key"] == name]
            if subset.empty and market_symbol:
                subset = frame.loc[frame["_code_key"] == market_symbol]
            if subset.empty:
                missing_targets.append(name)
                continue
            subset_dates = pd.to_datetime(subset["as_of_date"], errors="coerce")
            subset = subset.assign(_parsed_date=subset_dates).dropna(subset=["_parsed_date"])
            if subset.empty:
                missing_targets.append(name)
                continue
            latest_row = subset.sort_values("_parsed_date").iloc[-1]
            lag_days = 0
            if not pd.isna(parsed_target):
                lag_days = max(0, int((parsed_target.normalize() - latest_row["_parsed_date"].normalize()).days))
            if lag_days > 0:
                instrument = str(latest_row.get("instrument") or market_symbol).strip().upper()
                target_record = {
                    "radar_object_name": name,
                    "instrument": instrument,
                    "as_of_date": str(latest_row.get("as_of_date") or ""),
                    "lag_days": lag_days,
                }
                expected_non_trading = bool(item.get("expected_non_trading_on_target_date"))
                reason = str(item.get("non_trading_reason") or "").strip()
                source_has_no_target_record = bool(db_target_instruments) and instrument and instrument not in db_target_instruments
                if (
                    (expected_non_trading or source_has_no_target_record)
                    and previous_trade_date
                    and str(latest_row.get("as_of_date") or "") == previous_trade_date
                ):
                    target_record["reason"] = reason or "目标交易日数据底座无该股记录，按非交易/供应商不可得处理"
                    non_trading_targets.append(target_record)
                else:
                    stale_targets.append(target_record)
    return {
        "exists": True,
        "latest_as_of_date": latest,
        "row_count": int(len(frame)),
        "stale_targets": stale_targets,
        "non_trading_targets": non_trading_targets,
        "missing_targets": missing_targets,
    }


def copy_remote_file(*, host: str, ssh_options: str, remote_path: str, local_path: Path, timeout_seconds: int) -> tuple[bool, str]:
    command = build_scp_command(ssh_options, f"{host}:{remote_path}", str(local_path))
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"scp exited with {result.returncode}"
        return False, message
    return True, ""


def push_local_file_to_remote(*, host: str, ssh_options: str, local_path: Path, remote_path: str, timeout_seconds: int) -> tuple[bool, str]:
    mkdir_command = build_ssh_command(host, ssh_options, ["mkdir", "-p", str(Path(remote_path).parent)])
    mkdir_result = subprocess.run(
        mkdir_command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if mkdir_result.returncode != 0:
        message = mkdir_result.stderr.strip() or mkdir_result.stdout.strip() or f"mkdir exited with {mkdir_result.returncode}"
        return False, message
    command = build_scp_command(ssh_options, str(local_path), f"{host}:{remote_path}")
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"scp exited with {result.returncode}"
        return False, message
    return True, ""


def render_markdown(payload: dict[str, Any]) -> str:
    attempts = payload.get("attempts") or []
    csv_status = payload.get("csv_status") or {}
    stale_targets = csv_status.get("stale_targets") or []
    non_trading_targets = csv_status.get("non_trading_targets") or []
    missing_targets = csv_status.get("missing_targets") or []
    unresolved_targets = payload.get("unresolved_targets") or []
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_price_freshness"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Price Freshness {payload.get("target_date") or "unknown"}"',
        "---",
        "",
        "# Radar Price Freshness",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 目标交易日：`{payload.get('target_date')}`",
        f"- DB 最新日期：`{payload.get('db_status', {}).get('latest_trade_date')}`",
        f"- DB 目标行数：`{payload.get('db_status', {}).get('target_row_count')}`",
        f"- CSV 最新日期：`{payload.get('csv_status', {}).get('latest_as_of_date')}`",
        f"- 使用路径：`{payload.get('used_lane')}`",
        "",
        "## Candidate Coverage",
        "",
    ]
    if stale_targets:
        lines.append(
            "- stale targets: "
            + " / ".join(
                f"{str(item.get('radar_object_name') or '')}({str(item.get('as_of_date') or 'unknown')}, lag {int(item.get('lag_days') or 0)}d)"
                for item in stale_targets[:8]
            )
        )
    else:
        lines.append("- stale targets: none")
    if non_trading_targets:
        lines.append(
            "- non-trading targets: "
            + " / ".join(
                f"{str(item.get('radar_object_name') or '')}({str(item.get('as_of_date') or 'unknown')}; {str(item.get('reason') or '停牌/非交易')})"
                for item in non_trading_targets[:8]
            )
        )
    else:
        lines.append("- non-trading targets: none")
    if missing_targets:
        lines.append("- missing targets: " + " / ".join(str(name) for name in missing_targets[:8]))
    else:
        lines.append("- missing targets: none")
    if unresolved_targets:
        lines.append("- unresolved targets: " + " / ".join(str(item.get("radar_object_name") or "") for item in unresolved_targets[:8]))
    lines.extend([
        "",
        "## Attempts",
        "",
    ])
    if attempts:
        for item in attempts:
            lines.append(
                "- {lane}: `{status}` | {note}".format(
                    lane=item.get("lane"),
                    status=item.get("status"),
                    note=item.get("note") or "",
                )
            )
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    target_date = load_target_date(args)
    price_cfg = load_config_section(args.config, "price_sidecar")
    bridge_cfg = load_config_section(args.config, "canonical_price_bridge")
    refresh_cfg = load_config_section(args.config, "upstream_price_refresh")
    deployment = load_deployment_config(args.config)

    output_csv = resolve_repo_path(
        price_cfg.get("company_price_csv_path"),
        fallback=ROOT / "output" / "sidecars" / "equity_prices" / "company_price_snapshot_latest.csv",
    )
    bridge_output_json = resolve_repo_path(
        bridge_cfg.get("output_path"),
        fallback=ROOT / "output" / "sidecars" / "equity_prices" / "company_price_backfill_latest.json",
    )
    queue_path = resolve_repo_path(
        bridge_cfg.get("writeback_queue_path"),
        fallback=DEFAULT_PRICE_QUEUE,
    )
    company_targets, unresolved_targets = load_company_target_payload(args.input_candidate_pool)
    db_path = resolve_repo_path(price_cfg.get("equity_price_db_path"), fallback=Path(""))
    lookback_days = int(price_cfg.get("lookback_days") or 45)
    timeout_seconds = max(int(refresh_cfg.get("timeout_seconds") or 900), 60)
    attempts: list[dict[str, Any]] = []
    host = str(deployment.get("server_host") or "").strip()
    ssh_options = str(deployment.get("ssh_options") or "").strip()
    remote_root = str(deployment.get("remote_root") or "").strip().rstrip("/")
    remote_venv = str(deployment.get("remote_venv") or "").strip().rstrip("/")
    remote_bridge_script = str(refresh_cfg.get("remote_bridge_script") or f"{remote_root}/scripts/build_radar_canonical_price_bridge.py").strip()
    remote_bridge_output = str(
        refresh_cfg.get("remote_price_bridge_json_path")
        or bridge_cfg.get("remote_output_path")
        or f"{remote_root}/output/sidecars/equity_prices/company_price_backfill_latest.json"
    ).strip()
    remote_candidate_pool_path = str(
        refresh_cfg.get("remote_candidate_pool_path")
        or f"{remote_root}/output/snapshots/radar_candidate_pool_latest.json"
    ).strip()
    remote_bridge_candidate_pool_path = str(Path(remote_candidate_pool_path).with_name("company_price_bridge_targets_latest.json"))
    remote_queue_path = str(
        refresh_cfg.get("remote_price_writeback_queue_path")
        or f"{remote_root}/output/sidecars/canonical_writeback/price_writeback_queue.json"
    ).strip()
    local_remote_bridge_script = Path(remote_bridge_script) if remote_bridge_script else Path()

    db_status = query_price_db(db_path, target_date=target_date) if str(db_path) else {"exists": False, "latest_trade_date": "", "previous_trade_date": "", "target_row_count": 0}
    csv_status = query_price_csv(output_csv, target_date=target_date, company_targets=company_targets, db_path=db_path)
    bridge_attempted = False

    def has_fresh_price() -> bool:
        nonlocal db_status, csv_status
        db_status = query_price_db(db_path, target_date=target_date) if str(db_path) else {"exists": False, "latest_trade_date": "", "previous_trade_date": "", "target_row_count": 0}
        csv_status = query_price_csv(output_csv, target_date=target_date, company_targets=company_targets, db_path=db_path)
        csv_has_target_date = str(csv_status.get("latest_as_of_date") or "") == target_date
        if company_targets:
            return (
                csv_has_target_date
                and not csv_status.get("stale_targets")
                and not csv_status.get("missing_targets")
            )
        return int(db_status.get("target_row_count") or 0) > 0 or csv_has_target_date

    def has_fresh_substrate() -> bool:
        nonlocal db_status, csv_status
        db_status = query_price_db(db_path, target_date=target_date) if str(db_path) else {"exists": False, "latest_trade_date": "", "previous_trade_date": "", "target_row_count": 0}
        csv_status = query_price_csv(output_csv, target_date=target_date, company_targets=company_targets, db_path=db_path)
        return int(db_status.get("target_row_count") or 0) > 0 and str(csv_status.get("latest_as_of_date") or "") == target_date

    def build_payload(status: str, used_lane: str) -> dict[str, Any]:
        return {
            "status": status,
            "target_date": target_date,
            "used_lane": used_lane,
            "db_path": str(db_path),
            "csv_path": str(output_csv),
            "bridge_json_path": str(bridge_output_json),
            "db_status": db_status,
            "csv_status": csv_status,
            "attempts": attempts,
            "unresolved_targets": unresolved_targets,
        }

    def write_and_exit(payload: dict[str, Any], exit_code: int) -> int:
        write_json(args.json_output, payload)
        write_text(args.md_output, render_markdown(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exit_code

    if args.audit_only:
        audit_status = "pass" if has_fresh_price() else ("warn" if csv_status.get("exists") else "fail")
        attempts.append(
            {
                "lane": "audit_only_existing_inputs",
                "status": audit_status,
                "note": "upstream refresh skipped by --audit-only",
            }
        )
        return write_and_exit(build_payload(audit_status, "audit_only_existing_inputs"), 0 if audit_status in {"pass", "warn"} else 1)

    def company_targets_need_bridge() -> bool:
        return bool(
            args.force_refresh
            or csv_status.get("stale_targets")
            or csv_status.get("missing_targets")
        )

    def attempt_remote_queue_flush() -> None:
        if local_queue_count(queue_path) <= 0:
            return
        if local_remote_bridge_script.exists():
            local_result, local_message = run_local_python(
                local_remote_bridge_script,
                [
                    "--config",
                    str(args.config),
                    "--input-candidate-pool",
                    str(args.input_candidate_pool),
                    "--target-date",
                    target_date,
                    "--output",
                    str(bridge_output_json),
                    "--flush-queue-only",
                ],
                timeout_seconds=timeout_seconds,
                python_bin=f"{remote_venv}/bin/python" if remote_venv else None,
            )
            flush_summary = dict((local_result or {}).get("writeback_queue_flush") or {})
            remaining_count = int(flush_summary.get("remaining_count") or 0)
            attempts.append(
                {
                    "lane": "local_remote_price_writeback_flush",
                    "status": "pass" if local_message == "" and remaining_count == 0 else ("warn" if local_message == "" else "fail"),
                    "note": local_message
                    or f"flushed={int(flush_summary.get('flushed_count') or 0)} remaining={remaining_count}",
                }
            )
            if local_message == "" and remaining_count == 0:
                overwrite_local_queue(queue_path, [])
            return
        if not (host and remote_root and remote_venv):
            attempts.append(
                {
                    "lane": "remote_price_writeback_flush",
                    "status": "skip",
                    "note": "remote deployment unavailable",
                }
            )
            return
        pushed_queue, queue_message = push_local_file_to_remote(
            host=host,
            ssh_options=ssh_options,
            local_path=queue_path,
            remote_path=remote_queue_path,
            timeout_seconds=timeout_seconds,
        )
        attempts.append(
            {
                "lane": "sync_local_price_writeback_queue",
                "status": "pass" if pushed_queue else "fail",
                "note": queue_message or f"synced_to={remote_queue_path}",
            }
        )
        if not pushed_queue:
            return
        pushed_pool, pool_message = push_local_file_to_remote(
            host=host,
            ssh_options=ssh_options,
            local_path=args.input_candidate_pool,
            remote_path=remote_candidate_pool_path,
            timeout_seconds=timeout_seconds,
        )
        attempts.append(
            {
                "lane": "sync_local_candidate_pool_for_writeback_flush",
                "status": "pass" if pushed_pool else "fail",
                "note": pool_message or f"synced_to={remote_candidate_pool_path}",
            }
        )
        if not pushed_pool:
            return
        remote_result, remote_message = run_remote_python(
            host=host,
            ssh_options=ssh_options,
            python_bin=f"{remote_venv}/bin/python",
            script_path=remote_bridge_script,
            script_args=[
                "--config",
                f"{remote_root}/config/runtime_defaults.json",
                "--input-candidate-pool",
                remote_candidate_pool_path,
                "--target-date",
                target_date,
                "--output",
                remote_bridge_output,
                "--flush-queue-only",
            ],
            timeout_seconds=timeout_seconds,
        )
        flush_summary = dict((remote_result or {}).get("writeback_queue_flush") or {})
        remaining_count = int(flush_summary.get("remaining_count") or 0)
        attempts.append(
            {
                "lane": "remote_price_writeback_flush",
                "status": "pass" if remote_message == "" and remaining_count == 0 else ("warn" if remote_message == "" else "fail"),
                "note": remote_message
                or f"flushed={int(flush_summary.get('flushed_count') or 0)} remaining={remaining_count}",
            }
        )
        if remote_message == "" and remaining_count == 0:
            overwrite_local_queue(queue_path, [])
            return
        copied, copy_message = copy_remote_file(
            host=host,
            ssh_options=ssh_options,
            remote_path=remote_queue_path,
            local_path=queue_path,
            timeout_seconds=timeout_seconds,
        )
        attempts.append(
            {
                "lane": "sync_remote_price_writeback_queue_back",
                "status": "pass" if copied else "fail",
                "note": copy_message or f"synced_to={queue_path}",
            }
        )

    if has_fresh_price() and not company_targets_need_bridge() and not args.force_refresh:
        pruned_count = prune_local_price_writeback_queue(
            target_date=target_date,
            company_targets=company_targets,
            queue_path=queue_path,
        )
        if pruned_count:
            attempts.append(
                {
                    "lane": "prune_local_price_writeback_queue",
                    "status": "pass",
                    "note": f"pruned={pruned_count}",
                }
            )
        if local_queue_count(queue_path) > 0:
            attempt_remote_queue_flush()
    if has_fresh_price() and not company_targets_need_bridge() and not args.force_refresh:
        return write_and_exit(
            build_payload("pass", "existing_local_inputs"),
            0,
        )

    if has_fresh_price() and company_targets_need_bridge() and not args.force_refresh and not bool(bridge_cfg.get("auto_bridge_stale_targets", False)):
        attempts.append(
            {
                "lane": "targeted_price_bridge_deferred",
                "status": "warn",
                "note": "fresh market price substrate exists; stale/missing company targets deferred to manual --force-refresh",
            }
        )
        return write_and_exit(
            build_payload("warn", "existing_local_inputs_with_target_warnings"),
            0,
        )

    bridge_candidate_pool = args.input_candidate_pool
    bridge_candidate_count = len(company_targets)
    if company_targets_need_bridge() and not args.force_refresh:
        bridge_candidate_pool, scoped_count = write_scoped_bridge_candidate_pool(
            input_candidate_pool=args.input_candidate_pool,
            output_candidate_pool=bridge_output_json.with_name("company_price_bridge_targets_latest.json"),
            csv_status=csv_status,
            max_candidates=int(bridge_cfg.get("max_bridge_candidates") or 0),
        )
        if scoped_count > 0:
            bridge_candidate_count = scoped_count
            attempts.append(
                {
                    "lane": "scope_price_bridge_targets",
                    "status": "pass",
                    "note": f"scoped_bridge_candidates={scoped_count} full_candidate_targets={len(company_targets)}",
                }
            )

    if bool(bridge_cfg.get("enabled", True)) and company_targets and company_targets_need_bridge():
        bridge_attempted = True
        bridge_result: dict[str, Any] | None = None
        bridge_message = ""
        bridge_lane = ""
        bridge_ran_on_remote = False
        if local_remote_bridge_script.exists():
            bridge_result, bridge_message = run_local_python(
                local_remote_bridge_script,
                [
                    "--config",
                    str(args.config),
                    "--input-candidate-pool",
                    str(bridge_candidate_pool),
                    "--target-date",
                    target_date,
                    "--output",
                    str(bridge_output_json),
                    *(["--force-fetch"] if args.force_refresh else []),
                ],
                timeout_seconds=timeout_seconds,
                python_bin=f"{remote_venv}/bin/python" if remote_venv else None,
            )
            bridge_lane = "local_remote_canonical_price_bridge"
            attempts.append(
                {
                    "lane": bridge_lane,
                    "status": "pass" if bridge_message == "" and bridge_result is not None else "fail",
                    "note": bridge_message
                    or f"candidate_count={int((bridge_result or {}).get('candidate_count') or 0)} requested={bridge_candidate_count}",
                }
            )
        elif host and remote_root and remote_venv:
            pushed, push_message = push_local_file_to_remote(
                host=host,
                ssh_options=ssh_options,
                local_path=bridge_candidate_pool,
                remote_path=remote_bridge_candidate_pool_path,
                timeout_seconds=timeout_seconds,
            )
            attempts.append(
                {
                    "lane": "sync_local_bridge_candidate_pool",
                    "status": "pass" if pushed else "fail",
                    "note": push_message or f"synced_to={remote_bridge_candidate_pool_path}",
                }
            )
            if pushed:
                bridge_result, bridge_message = run_remote_python(
                    host=host,
                    ssh_options=ssh_options,
                    python_bin=f"{remote_venv}/bin/python",
                    script_path=remote_bridge_script,
                    script_args=[
                        "--config",
                        f"{remote_root}/config/runtime_defaults.json",
                        "--input-candidate-pool",
                        remote_bridge_candidate_pool_path,
                        "--target-date",
                        target_date,
                        "--output",
                        remote_bridge_output,
                        *(["--force-fetch"] if args.force_refresh else []),
                    ],
                    timeout_seconds=timeout_seconds,
                )
                attempts.append(
                    {
                        "lane": "remote_canonical_price_bridge",
                        "status": "pass" if bridge_message == "" and bridge_result is not None else "fail",
                        "note": bridge_message
                        or f"candidate_count={int((bridge_result or {}).get('candidate_count') or 0)} requested={bridge_candidate_count}",
                    }
                )
                bridge_ran_on_remote = bridge_message == "" and bridge_result is not None
                if bridge_message == "":
                    bridge_output_json.parent.mkdir(parents=True, exist_ok=True)
                    copied, copy_message = copy_remote_file(
                        host=host,
                        ssh_options=ssh_options,
                        remote_path=remote_bridge_output,
                        local_path=bridge_output_json,
                        timeout_seconds=timeout_seconds,
                    )
                    attempts.append(
                        {
                            "lane": "remote_bridge_sync",
                            "status": "pass" if copied else "fail",
                            "note": copy_message or f"synced_to={bridge_output_json}",
                        }
                    )
        if bridge_message == "":
            if bridge_ran_on_remote and host and remote_root and remote_venv:
                pushed_pool, pool_message = push_local_file_to_remote(
                    host=host,
                    ssh_options=ssh_options,
                    local_path=args.input_candidate_pool,
                    remote_path=remote_candidate_pool_path,
                    timeout_seconds=timeout_seconds,
                )
                attempts.append(
                    {
                        "lane": "sync_local_candidate_pool_for_remote_price_sidecar",
                        "status": "pass" if pushed_pool else "fail",
                        "note": pool_message or f"synced_to={remote_candidate_pool_path}",
                    }
                )
                remote_output_csv = str(refresh_cfg.get("remote_price_csv_path") or f"{remote_root}/output/sidecars/equity_prices/company_price_snapshot_latest.csv").strip()
                remote_radar_sidecar_script = str(refresh_cfg.get("remote_radar_sidecar_script") or f"{remote_root}/scripts/build_radar_company_price_sidecar.py").strip()
                if pushed_pool:
                    sidecar_result, sidecar_message = run_remote_python(
                        host=host,
                        ssh_options=ssh_options,
                        python_bin=f"{remote_venv}/bin/python",
                        script_path=remote_radar_sidecar_script,
                        script_args=[
                            "--input-candidate-pool",
                            remote_candidate_pool_path,
                            "--target-date",
                            target_date,
                            "--config",
                            f"{remote_root}/config/runtime_defaults.json",
                            "--output",
                            remote_output_csv,
                        ],
                        timeout_seconds=timeout_seconds,
                    )
                    attempts.append(
                        {
                            "lane": "remote_price_sidecar_from_bridge",
                            "status": "pass" if sidecar_message == "" and sidecar_result is not None else "fail",
                            "note": sidecar_message or f"row_count={int((sidecar_result or {}).get('row_count') or 0)}",
                        }
                    )
                    if sidecar_message == "":
                        copied_csv, csv_message = copy_remote_file(
                            host=host,
                            ssh_options=ssh_options,
                            remote_path=remote_output_csv,
                            local_path=output_csv,
                            timeout_seconds=timeout_seconds,
                        )
                        attempts.append(
                            {
                                "lane": "remote_price_sidecar_sync",
                                "status": "pass" if copied_csv else "fail",
                                "note": csv_message or f"synced_to={output_csv}",
                            }
                        )
            else:
                local_sidecar_result, local_sidecar_message = run_local_python(
                    ROOT / "scripts" / "build_radar_company_price_sidecar.py",
                    [
                        "--input-candidate-pool",
                        str(args.input_candidate_pool),
                        "--target-date",
                        target_date,
                        "--config",
                        str(args.config),
                        "--output",
                        str(output_csv),
                    ],
                    timeout_seconds=timeout_seconds,
                )
                attempts.append(
                    {
                        "lane": "local_price_sidecar_from_bridge",
                        "status": "pass" if local_sidecar_message == "" and local_sidecar_result is not None else "fail",
                        "note": local_sidecar_message or f"row_count={int((local_sidecar_result or {}).get('row_count') or 0)}",
                    }
                )
            pruned_count = prune_local_price_writeback_queue(
                target_date=target_date,
                company_targets=company_targets,
                queue_path=queue_path,
            )
            if pruned_count:
                attempts.append(
                    {
                        "lane": "prune_local_price_writeback_queue",
                        "status": "pass",
                        "note": f"pruned={pruned_count}",
                    }
                )
            has_fresh_price()
            if not args.force_refresh and not csv_status.get("stale_targets") and not csv_status.get("missing_targets"):
                return write_and_exit(
                    build_payload("pass", bridge_lane or "remote_canonical_price_bridge"),
                    0,
                )

    if has_fresh_price() and not company_targets_need_bridge() and not args.force_refresh:
        return write_and_exit(
            build_payload("pass", "existing_local_inputs"),
            0,
        )

    if has_fresh_price() and not args.force_refresh and not unresolved_targets and not csv_status.get("stale_targets") and not csv_status.get("missing_targets"):
        return write_and_exit(
            build_payload("pass", "post_bridge_local_inputs"),
            0,
        )

    if bridge_attempted and not args.force_refresh:
        attempts.append(
            {
                "lane": "post_bridge_partial",
                "status": "warn",
                "note": "targeted bridge left stale/missing company targets; continuing to whole-db refresh",
            }
        )

    if has_fresh_price() and not args.force_refresh and (csv_status.get("stale_targets") or csv_status.get("missing_targets")):
        attempts.append(
            {
                "lane": "targeted_bridge_insufficient",
                "status": "warn",
                "note": "falling back to whole-db refresh",
            }
        )

    local_builder = Path(str(refresh_cfg.get("local_builder_script") or (ROOT.parent / "qlib_paper_trading" / "scripts" / "build_equity_price_substrate.py"))).expanduser()
    local_builder_config = Path(str(refresh_cfg.get("local_builder_config") or (ROOT.parent / "qlib_paper_trading" / "config" / "runtime_defaults.json"))).expanduser()
    local_builder_args = [str(item) for item in (refresh_cfg.get("local_builder_args") or [])]
    if local_builder.exists() and (db_path.exists() or not str(db_path).startswith("/root/")):
        result, message = run_local_python(local_builder, ["--config", str(local_builder_config), *local_builder_args], timeout_seconds=timeout_seconds)
        attempts.append(
            {
                "lane": "local_builder",
                "status": "pass" if message == "" and result is not None else "fail",
                "note": message or f"latest_trade_date={str((result or {}).get('latest_trade_date') or '')}",
            }
        )
        if has_fresh_price():
            return write_and_exit(
                build_payload("warn" if (csv_status.get("stale_targets") or csv_status.get("missing_targets")) else "pass", "local_builder"),
                0,
            )

    remote_builder = str(refresh_cfg.get("remote_builder_script") or "/opt/quant-runtime/scripts/build_equity_price_substrate.py").strip()
    remote_builder_config = str(refresh_cfg.get("remote_builder_config") or "/opt/quant-runtime/config/runtime_defaults.json").strip()
    remote_builder_args = [str(item) for item in (refresh_cfg.get("remote_builder_args") or [])]
    remote_radar_sidecar_script = str(refresh_cfg.get("remote_radar_sidecar_script") or f"{remote_root}/scripts/build_radar_company_price_sidecar.py").strip()
    remote_output_csv = str(refresh_cfg.get("remote_price_csv_path") or f"{remote_root}/output/sidecars/equity_prices/company_price_snapshot_latest.csv").strip()
    remote_builder_python = str(refresh_cfg.get("remote_builder_python_bin") or "/opt/quant-runtime/.venv/bin/python").strip()
    local_remote_builder = Path(remote_builder) if remote_builder else Path()
    local_remote_radar_sidecar = Path(remote_radar_sidecar_script) if remote_radar_sidecar_script else Path()

    if local_remote_builder.exists() and local_remote_radar_sidecar.exists():
        result, message = run_local_python(
            local_remote_builder,
            ["--config", remote_builder_config, *remote_builder_args],
            timeout_seconds=timeout_seconds,
            python_bin=remote_builder_python,
        )
        attempts.append(
            {
                "lane": "local_remote_builder",
                "status": "pass" if message == "" and result is not None else "fail",
                "note": message or f"latest_trade_date={str((result or {}).get('latest_trade_date') or '')}",
            }
        )
        if message == "":
            sidecar_result, sidecar_message = run_local_python(
                local_remote_radar_sidecar,
                [
                    "--target-date",
                    target_date,
                    "--output",
                    str(output_csv),
                    "--config",
                    str(args.config),
                ],
                timeout_seconds=timeout_seconds,
                python_bin=f"{remote_venv}/bin/python" if remote_venv else None,
            )
            attempts.append(
                {
                    "lane": "local_remote_price_sidecar",
                    "status": "pass" if sidecar_message == "" and sidecar_result is not None else "fail",
                    "note": sidecar_message or f"row_count={int((sidecar_result or {}).get('row_count') or 0)}",
                }
            )
        if has_fresh_price():
            return write_and_exit(
                build_payload("warn" if (csv_status.get("stale_targets") or csv_status.get("missing_targets")) else "pass", "local_remote_builder"),
                0,
            )

    elif host and remote_root and remote_venv:
        result, message = run_remote_python(
            host=host,
            ssh_options=ssh_options,
            python_bin=remote_builder_python,
            script_path=remote_builder,
            script_args=["--config", remote_builder_config, *remote_builder_args],
            timeout_seconds=timeout_seconds,
        )
        attempts.append(
            {
                "lane": "remote_builder",
                "status": "pass" if message == "" and result is not None else "fail",
                "note": message or f"latest_trade_date={str((result or {}).get('latest_trade_date') or '')}",
            }
        )
        if message == "":
            sidecar_result, sidecar_message = run_remote_python(
                host=host,
                ssh_options=ssh_options,
                python_bin=f"{remote_venv}/bin/python",
                script_path=remote_radar_sidecar_script,
                script_args=[
                    "--target-date",
                    target_date,
                    "--output",
                    remote_output_csv,
                    "--config",
                    f"{remote_root}/config/runtime_defaults.json",
                ],
                timeout_seconds=timeout_seconds,
            )
            attempts.append(
                {
                    "lane": "remote_price_sidecar",
                    "status": "pass" if sidecar_message == "" and sidecar_result is not None else "fail",
                    "note": sidecar_message or f"row_count={int((sidecar_result or {}).get('row_count') or 0)}",
                }
            )
            if sidecar_message == "":
                output_csv.parent.mkdir(parents=True, exist_ok=True)
                copied, copy_message = copy_remote_file(
                    host=host,
                    ssh_options=ssh_options,
                    remote_path=remote_output_csv,
                    local_path=output_csv,
                    timeout_seconds=timeout_seconds,
                )
                attempts.append(
                    {
                        "lane": "remote_csv_sync",
                        "status": "pass" if copied else "fail",
                        "note": copy_message or f"synced_to={output_csv}",
                    }
                )
        if has_fresh_price():
            return write_and_exit(
                build_payload("warn" if (csv_status.get("stale_targets") or csv_status.get("missing_targets")) else "pass", "remote_builder_and_sync"),
                0,
            )

    if has_fresh_substrate():
        attempts.append(
            {
                "lane": "fresh_substrate_with_target_warnings",
                "status": "warn",
                "note": "target trade date exists in canonical DB and CSV; stale/missing long-tail company targets are delegated to report quality gating",
            }
        )
        return write_and_exit(
            build_payload("warn", "fresh_substrate_with_target_warnings"),
            0,
        )

    return write_and_exit(build_payload("fail", "none"), 1)


if __name__ == "__main__":
    raise SystemExit(main())
