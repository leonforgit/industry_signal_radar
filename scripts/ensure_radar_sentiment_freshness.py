#!/usr/bin/env python3
"""Ensure Radar sentiment sidecars are refreshed from the upstream sentiment subsystem when stale."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any

import pandas as pd

from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_freshness_utils import DEFAULT_MARKET_OPEN_TIME, DEFAULT_MARKET_SAMPLE_READY_TIME, DEFAULT_MARKET_TZ, expected_sample_date
from radar_sentiment_sidecar import resolve_sidecar_path
from radar_upstream_bridge import build_scp_command, build_ssh_command, load_deployment_config, run_local_python, run_remote_python


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_sentiment_freshness_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_sentiment_freshness_latest.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--force-refresh", action="store_true")
    return parser.parse_args()


def latest_csv_date(path: Path) -> str:
    if not path.exists():
        return ""
    frame = pd.read_csv(path, usecols=["datetime"])
    if "datetime" not in frame.columns or frame.empty:
        return ""
    parsed = pd.to_datetime(frame["datetime"], errors="coerce").dropna()
    if parsed.empty:
        return ""
    return parsed.max().date().isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def deployment_host_is_local(host: str) -> bool:
    target = str(host or "").strip()
    if not target:
        return False
    if "@" in target:
        target = target.rsplit("@", 1)[-1]
    target = target.split(":", 1)[0].strip("[]").lower()
    if target in {"localhost", "127.0.0.1", "::1"}:
        return True
    local_names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    if target in {name for name in local_names if name}:
        return True
    try:
        result = subprocess.run(["hostname", "-I"], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    local_ips = {item.strip() for item in result.stdout.split() if item.strip()}
    return target in local_ips


def copy_local_file(*, source_path: str, local_path: Path) -> tuple[bool, str]:
    source = Path(source_path)
    if not source.exists():
        return False, f"missing local source file: {source}"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == local_path.resolve():
        return True, "same_path"
    shutil.copyfile(source, local_path)
    return True, ""


def copy_remote_file(*, host: str, ssh_options: str, remote_path: str, local_path: Path, timeout_seconds: int) -> tuple[bool, str]:
    local_path.parent.mkdir(parents=True, exist_ok=True)
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


def fetch_remote_text(*, host: str, ssh_options: str, remote_path: str, timeout_seconds: int) -> tuple[str, str]:
    command = build_ssh_command(host, ssh_options, ["cat", remote_path])
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"ssh cat exited with {result.returncode}"
        return "", message
    return result.stdout, ""


def render_markdown(payload: dict[str, Any]) -> str:
    files = payload.get("files") or []
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_sentiment_freshness"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Sentiment Freshness {payload.get("expected_sample_date") or "unknown"}"',
        "---",
        "",
        "# Radar Sentiment Freshness",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 期望样本日期：`{payload.get('expected_sample_date')}`",
        f"- 使用路径：`{payload.get('used_lane')}`",
        "",
        "## Files",
        "",
    ]
    for item in files:
        lines.append(
            "- {name}: latest_date `{latest_date}` | lag_days `{lag_days}` | path `{path}`".format(
                name=item.get("name"),
                latest_date=item.get("latest_date") or "",
                lag_days=item.get("lag_days"),
                path=item.get("path") or "",
            )
        )
    lines.extend(["", "## Attempts", ""])
    attempts = payload.get("attempts") or []
    if attempts:
        for item in attempts:
            lines.append(f"- {item.get('lane')}: `{item.get('status')}` | {item.get('note') or ''}")
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    source_cfg = load_config_section(args.config, "source_readiness")
    sentiment_cfg = load_config_section(args.config, "sentiment_sidecar")
    refresh_cfg = load_config_section(args.config, "upstream_sentiment_refresh")
    deployment = load_deployment_config(args.config)

    market_tz = str(source_cfg.get("market_tz") or DEFAULT_MARKET_TZ)
    market_sample_ready_time = str(
        source_cfg.get("market_sample_ready_time")
        or source_cfg.get("market_open_time")
        or DEFAULT_MARKET_SAMPLE_READY_TIME
    )
    expected_date = expected_sample_date(
        {"market_tz": market_tz, "market_sample_ready_time": market_sample_ready_time},
        market_tz=market_tz,
        market_open_time=market_sample_ready_time,
    ).isoformat()
    max_lag_days = int(source_cfg.get("max_sentiment_lag_days") or 0)
    warning_lag_days = max(int(source_cfg.get("max_sentiment_warning_lag_days") or 3), max_lag_days)

    mirror_dir = ROOT / str(sentiment_cfg.get("mirror_dir") or "output/sidecars/sentiment")
    market_path = resolve_sidecar_path(sentiment_cfg.get("market_csv_path"), mirror_dir=mirror_dir)
    company_path = resolve_sidecar_path(sentiment_cfg.get("company_csv_path"), mirror_dir=mirror_dir)
    timeout_seconds = max(int(refresh_cfg.get("timeout_seconds") or 900), 60)
    attempts: list[dict[str, Any]] = []

    def collect_status() -> list[dict[str, Any]]:
        expected = datetime.fromisoformat(expected_date).date()
        rows = []
        for name, path in (("market_sentiment", market_path), ("company_sentiment", company_path)):
            latest_date_text = latest_csv_date(path)
            lag_days = None
            if latest_date_text:
                latest_date = datetime.fromisoformat(latest_date_text).date()
                lag_days = max(0, (expected - latest_date).days)
            rows.append(
                {
                    "name": name,
                    "path": str(path),
                    "latest_date": latest_date_text,
                    "lag_days": lag_days,
                }
            )
        return rows

    def is_fresh(files: list[dict[str, Any]]) -> bool:
        if not files:
            return False
        return all(item.get("latest_date") and int(item.get("lag_days") or 0) <= max_lag_days for item in files)

    files = collect_status()
    if is_fresh(files) and not args.force_refresh:
        payload = {
            "status": "pass",
            "used_lane": "existing_local_inputs",
            "expected_sample_date": expected_date,
            "files": files,
            "attempts": attempts,
            "warning_lag_days": warning_lag_days,
        }
        write_json(args.json_output, payload)
        write_text(args.md_output, render_markdown(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    max_existing_lag = max(int(item.get("lag_days") or 0) for item in files if item.get("lag_days") is not None) if files else 999
    if files and max_existing_lag <= warning_lag_days and not args.force_refresh:
        payload = {
            "status": "warn",
            "used_lane": "existing_local_warning",
            "expected_sample_date": expected_date,
            "files": files,
            "attempts": attempts,
            "warning_lag_days": warning_lag_days,
        }
        write_json(args.json_output, payload)
        write_text(args.md_output, render_markdown(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    remote_host = str(deployment.get("server_host") or "").strip()
    remote_root = str(refresh_cfg.get("remote_runtime_root") or "/opt/quant-runtime/qlib_paper_trading").strip().rstrip("/")
    remote_python = str(refresh_cfg.get("remote_python_bin") or f"{remote_root}/.venv/bin/python").strip()
    remote_config = str(refresh_cfg.get("remote_config_path") or f"{remote_root}/config/runtime_defaults.json").strip()
    ssh_options = str(deployment.get("ssh_options") or "")
    remote_market_csv = str(refresh_cfg.get("remote_market_csv_path") or "").strip()
    remote_company_csv = str(refresh_cfg.get("remote_company_csv_path") or "").strip()
    remote_price_build_args = [str(item) for item in (refresh_cfg.get("remote_price_build_args") or ["--skip-hk"])]
    use_local_refresh = deployment_host_is_local(remote_host)

    if remote_host and remote_root:
        def run_upstream(script_path: str, script_args: list[str]) -> tuple[dict[str, Any] | None, str]:
            if use_local_refresh:
                return run_local_python(Path(script_path), script_args, timeout_seconds=timeout_seconds, python_bin=remote_python)
            return run_remote_python(
                host=remote_host,
                ssh_options=ssh_options,
                python_bin=remote_python,
                script_path=script_path,
                script_args=script_args,
                timeout_seconds=timeout_seconds,
            )

        lane_prefix = "local" if use_local_refresh else "remote"
        price_result, price_error = run_upstream(
            str(refresh_cfg.get("remote_price_build_script") or f"{remote_root}/scripts/build_equity_price_substrate.py"),
            ["--config", remote_config, *remote_price_build_args],
        )
        attempts.append(
            {
                "lane": f"{lane_prefix}_build_equity_price_substrate",
                "status": "pass" if price_error == "" and price_result is not None else "fail",
                "note": price_error or "",
            }
        )
        stock_connect_result, stock_connect_error = run_upstream(
            str(refresh_cfg.get("remote_stock_connect_build_script") or f"{remote_root}/scripts/build_stock_connect_substrate.py"),
            ["--config", remote_config],
        )
        attempts.append(
            {
                "lane": f"{lane_prefix}_build_stock_connect_substrate",
                "status": "pass" if stock_connect_error == "" and stock_connect_result is not None else "fail",
                "note": stock_connect_error or "",
            }
        )
        build_result, build_error = run_upstream(
            str(refresh_cfg.get("remote_build_script") or f"{remote_root}/scripts/build_sentiment_factor_substrate.py"),
            ["--config", remote_config],
        )
        attempts.append(
            {
                "lane": f"{lane_prefix}_build_sentiment_factor_substrate",
                "status": "pass" if build_error == "" and build_result is not None else "fail",
                "note": build_error or "",
            }
        )
        export_result, export_error = run_upstream(
            str(refresh_cfg.get("remote_export_script") or f"{remote_root}/scripts/export_sentiment_subsystem_snapshot.py"),
            ["--config", remote_config],
        )
        attempts.append(
            {
                "lane": f"{lane_prefix}_export_sentiment_snapshot",
                "status": "pass" if export_error == "" and export_result is not None else "fail",
                "note": export_error or "",
            }
        )
        for remote_path, local_path, label in (
            (remote_market_csv, market_path, "sync_market_sentiment_csv"),
            (remote_company_csv, company_path, "sync_company_sentiment_csv"),
        ):
            if use_local_refresh:
                copied, copy_error = copy_local_file(source_path=remote_path, local_path=local_path)
                attempts.append({"lane": label, "status": "pass" if copied else "fail", "note": copy_error})
                continue
            copied, copy_error = copy_remote_file(
                host=remote_host,
                ssh_options=ssh_options,
                remote_path=remote_path,
                local_path=local_path,
                timeout_seconds=timeout_seconds,
            )
            if copied:
                attempts.append({"lane": label, "status": "pass", "note": ""})
                continue
            remote_text, fetch_error = fetch_remote_text(
                host=remote_host,
                ssh_options=ssh_options,
                remote_path=remote_path,
                timeout_seconds=timeout_seconds,
            )
            if remote_text:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_text(remote_text, encoding="utf-8")
                attempts.append({"lane": label, "status": "pass", "note": "ssh_cat_fallback"})
            else:
                attempts.append({"lane": label, "status": "fail", "note": copy_error or fetch_error})

    files = collect_status()
    payload = {
        "status": "pass" if is_fresh(files) else "fail",
        "used_lane": "remote_sentiment_refresh" if attempts else "bridge_unavailable",
        "expected_sample_date": expected_date,
        "files": files,
        "attempts": attempts,
        "warning_lag_days": warning_lag_days,
    }
    write_json(args.json_output, payload)
    write_text(args.md_output, render_markdown(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
