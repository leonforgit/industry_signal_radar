from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import akshare as ak
import pandas as pd

from radar_industry_registry import load_industry_registry
from radar_runtime_bootstrap import resolve_runtime_paths
from radar_runtime_health import write_json


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a validation summary for Industry Signal Radar alerts.")
    parser.add_argument("--runtime-root-override", type=Path, default=None, help="Optional runtime root override.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Optional output dir override.")
    parser.add_argument("--horizons", default="1,3,5", help="Comma-separated trading-day horizons, for example 1,3,5.")
    parser.add_argument("--limit", type=int, default=200, help="Maximum alerts to evaluate.")
    return parser.parse_args()


def parse_horizons(raw: str) -> list[int]:
    horizons = sorted({int(part.strip()) for part in str(raw).split(",") if part.strip()})
    return [value for value in horizons if value > 0] or [1, 3, 5]


def connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_alert_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT alert_id, industry_id, industry_label, alert_level, state, total_score, created_at
            FROM alert_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


def normalize_sw_symbol(sw_code: str) -> str:
    return str(sw_code).replace(".SI", "").strip()


def load_history_cache(registry: list[dict[str, str]]) -> dict[str, pd.DataFrame]:
    cache: dict[str, pd.DataFrame] = {}
    for item in registry:
        sw_symbol = normalize_sw_symbol(item.get("sw_code", ""))
        if not sw_symbol:
            continue
        frame = ak.index_hist_sw(symbol=sw_symbol, period="day").copy()
        if frame.empty:
            continue
        frame["日期"] = pd.to_datetime(frame["日期"]).dt.date
        frame["收盘"] = pd.to_numeric(frame["收盘"], errors="coerce")
        frame = frame.dropna(subset=["收盘"]).sort_values("日期").reset_index(drop=True)
        cache[item["industry_id"]] = frame
    return cache


def compute_forward_returns(frame: pd.DataFrame, event_date: datetime.date, horizons: list[int]) -> tuple[dict[int, float | None], str]:
    if frame.empty:
        return {horizon: None for horizon in horizons}, "missing_history"
    history = frame[frame["日期"] <= event_date].copy()
    if history.empty:
        return {horizon: None for horizon in horizons}, "pre_history"
    entry_idx = history.index[-1]
    entry_close = float(frame.loc[entry_idx, "收盘"])
    returns: dict[int, float | None] = {}
    status = "evaluated"
    for horizon in horizons:
        target_idx = entry_idx + horizon
        if target_idx >= len(frame.index):
            returns[horizon] = None
            status = "pending_future"
            continue
        target_close = float(frame.loc[target_idx, "收盘"])
        returns[horizon] = (target_close / entry_close - 1.0) * 100
    return returns, status


def build_validation_rows(
    alert_rows: list[sqlite3.Row],
    registry: list[dict[str, str]],
    horizons: list[int],
) -> list[dict[str, Any]]:
    registry_map = {item["industry_id"]: item for item in registry}
    history_cache = load_history_cache(registry)
    validation_rows: list[dict[str, Any]] = []
    for row in alert_rows:
        created_at = datetime.fromisoformat(str(row["created_at"]))
        event_date = created_at.date()
        history = history_cache.get(str(row["industry_id"]), pd.DataFrame())
        forward_returns, evaluation_status = compute_forward_returns(history, event_date, horizons)
        validation_rows.append(
            {
                "alert_id": str(row["alert_id"]),
                "industry_id": str(row["industry_id"]),
                "industry_label": str(row["industry_label"] or registry_map.get(str(row["industry_id"]), {}).get("display_name_cn", "")),
                "alert_level": str(row["alert_level"]),
                "state": str(row["state"]),
                "total_score": float(row["total_score"]) if row["total_score"] is not None else None,
                "created_at": str(row["created_at"]),
                "event_date": event_date.isoformat(),
                "sw_code": str(registry_map.get(str(row["industry_id"]), {}).get("sw_code", "")),
                "evaluation_status": evaluation_status,
                "forward_returns_pct": {str(horizon): forward_returns[horizon] for horizon in horizons},
            }
        )
    return validation_rows


def summarize_validation(rows: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    evaluated_rows = [row for row in rows if row["evaluation_status"] == "evaluated"]
    pending_rows = [row for row in rows if row["evaluation_status"] != "evaluated"]
    by_level: dict[str, int] = {}
    for row in rows:
        by_level[row["alert_level"]] = by_level.get(row["alert_level"], 0) + 1

    horizon_summary: dict[str, dict[str, Any]] = {}
    for horizon in horizons:
        key = str(horizon)
        values = [
            row["forward_returns_pct"].get(key)
            for row in evaluated_rows
            if row["forward_returns_pct"].get(key) is not None
        ]
        if not values:
            horizon_summary[key] = {
                "evaluated_count": 0,
                "average_return_pct": None,
                "positive_rate": None,
            }
            continue
        positive_count = sum(1 for value in values if value > 0)
        horizon_summary[key] = {
            "evaluated_count": len(values),
            "average_return_pct": round(sum(values) / len(values), 4),
            "positive_rate": round(positive_count / len(values), 4),
        }

    return {
        "total_alerts": len(rows),
        "evaluated_alerts": len(evaluated_rows),
        "pending_alerts": len(pending_rows),
        "alerts_by_level": by_level,
        "horizon_summary": horizon_summary,
    }


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]], horizons: list[int], generated_at: str) -> str:
    lines = [
        "# Industry Signal Radar Validation Summary",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Total alerts: `{summary['total_alerts']}`",
        f"- Evaluated alerts: `{summary['evaluated_alerts']}`",
        f"- Pending alerts: `{summary['pending_alerts']}`",
        "",
        "## Horizon Summary",
        "",
        "| Horizon | Evaluated | Avg Return % | Positive Rate |",
        "| --- | ---: | ---: | ---: |",
    ]
    for horizon in horizons:
        item = summary["horizon_summary"][str(horizon)]
        avg_value = item["average_return_pct"]
        pos_value = item["positive_rate"]
        lines.append(
            f"| {horizon}d | {item['evaluated_count']} | {avg_value if avg_value is not None else 'N/A'} | {pos_value if pos_value is not None else 'N/A'} |"
        )

    lines.extend(
        [
            "",
            "## Recent Alerts",
            "",
            "| Created At | Industry | Level | Eval Status | " + " | ".join(f"{h}d" for h in horizons) + " |",
            "| --- | --- | --- | --- | " + " | ".join(["---:" for _ in horizons]) + " |",
        ]
    )
    for row in rows[:20]:
        values = []
        for horizon in horizons:
            value = row["forward_returns_pct"].get(str(horizon))
            values.append("N/A" if value is None else f"{value:.2f}")
        lines.append(
            f"| {row['created_at']} | {row['industry_label']} | {row['alert_level']} | {row['evaluation_status']} | "
            + " | ".join(values)
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    horizons = parse_horizons(args.horizons)
    runtime_paths = resolve_runtime_paths(args.runtime_root_override)
    db_path = runtime_paths["event_db_path"]
    output_dir = args.output_dir or runtime_paths["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect_db(db_path)
    alert_rows = load_alert_rows(conn, args.limit)
    conn.close()

    registry = load_industry_registry()
    validation_rows = build_validation_rows(alert_rows, registry, horizons)
    summary = summarize_validation(validation_rows, horizons)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = {
        "generated_at": generated_at,
        "horizons": horizons,
        "summary": summary,
        "rows": validation_rows,
    }

    latest_json_path = output_dir / "industry_signal_validation_latest.json"
    latest_md_path = output_dir / "industry_signal_validation_latest.md"
    dated_json_path = output_dir / f"industry_signal_validation_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(latest_json_path, payload)
    write_json(dated_json_path, payload)
    latest_md_path.write_text(render_markdown(summary, validation_rows, horizons, generated_at), encoding="utf-8")
    print(json.dumps({"status": "ok", "output_json": str(latest_json_path), "output_md": str(latest_md_path), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
