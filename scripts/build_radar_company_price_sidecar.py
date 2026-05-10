#!/usr/bin/env python3
"""Build a lightweight company price sidecar for Radar reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from radar_company_targets import normalize_company_name, stock_code_to_market_symbol
from radar_config import load_config_section
from radar_price_sidecar import (
    DEFAULT_LOCAL_PRICE_CSV,
    build_company_price_snapshot_frame,
    resolve_repo_path,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL, help="Radar candidate pool path.")
    parser.add_argument("--target-date", default="", help="Optional explicit target market sample date (YYYY-MM-DD).")
    parser.add_argument("--output", type=Path, default=None, help="Output CSV path.")
    parser.add_argument("--config", type=Path, default=None, help="Optional runtime config path.")
    return parser.parse_args()


def load_target_date(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(
        payload.get("market_sample_date")
        or payload.get("expected_sample_date")
        or payload.get("as_of_date")
        or payload.get("run_at")
        or payload.get("generated_at")
        or ""
    )[:10]


def load_bridge_overlay(config_path: Path | None) -> list[dict[str, Any]]:
    cfg = load_config_section(config_path, "canonical_price_bridge")
    path = resolve_repo_path(
        cfg.get("output_path"),
        fallback=ROOT / "output" / "sidecars" / "equity_prices" / "company_price_backfill_latest.json",
    )
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("results") or []
    return [row for row in rows if isinstance(row, dict)]


def overlay_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def merge_bridge_overlay(frame: pd.DataFrame, overlay_rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not overlay_rows:
        return frame
    working = frame.copy() if not frame.empty else pd.DataFrame()
    if working.empty:
        working = pd.DataFrame(
            columns=[
                "as_of_date",
                "lag_days",
                "instrument",
                "company_name",
                "close",
                "prev_close",
                "daily_return",
                "amount_ratio_20",
                "positive_days_5d",
                "price_sidecar_source",
            ]
        )
    working["_code_key"] = working["instrument"].astype(str).map(stock_code_to_market_symbol)
    working["_name_key"] = working["company_name"].astype(str).map(normalize_company_name)
    for row in overlay_rows:
        if not str(row.get("as_of_date") or "").strip():
            continue
        market_symbol = stock_code_to_market_symbol(row.get("market_symbol") or row.get("stock_code") or row.get("instrument"))
        name_key = normalize_company_name(row.get("radar_object_name") or row.get("company_name"))
        existing_index = None
        if market_symbol and "_code_key" in working.columns:
            matches = working.index[working["_code_key"] == market_symbol].tolist()
            if matches:
                existing_index = matches[0]
        if existing_index is None and name_key and "_name_key" in working.columns:
            matches = working.index[working["_name_key"] == name_key].tolist()
            if matches:
                existing_index = matches[0]
        record = {
            "as_of_date": str(row.get("as_of_date") or ""),
            "lag_days": int(row.get("lag_days") or 0),
            "instrument": str(row.get("instrument") or market_symbol or "").strip(),
            "company_name": str(row.get("radar_object_name") or row.get("company_name") or "").strip(),
            "close": row.get("close"),
            "prev_close": row.get("prev_close"),
            "daily_return": row.get("daily_return"),
            "amount_ratio_20": row.get("amount_ratio_20"),
            "positive_days_5d": row.get("positive_days_5d"),
            "price_sidecar_source": str(row.get("price_sidecar_source") or "canonical_price_bridge"),
            "_code_key": market_symbol,
            "_name_key": name_key,
        }
        if existing_index is None:
            if working.empty:
                working = pd.DataFrame([record])
            else:
                working = pd.concat([working, pd.DataFrame([record])], ignore_index=True)
            continue
        existing_date = pd.to_datetime(working.at[existing_index, "as_of_date"], errors="coerce")
        overlay_date = pd.to_datetime(record["as_of_date"], errors="coerce")
        if pd.isna(existing_date) or (not pd.isna(overlay_date) and overlay_date >= existing_date):
            for key, value in record.items():
                if key in {"close", "prev_close", "daily_return", "amount_ratio_20", "positive_days_5d"} and overlay_missing_value(value):
                    continue
                working.at[existing_index, key] = value
    if "_code_key" in working.columns:
        working = working.drop(columns=["_code_key", "_name_key"], errors="ignore")
    return working


def main() -> int:
    args = parse_args()
    cfg = load_config_section(args.config, "price_sidecar")
    output_path = args.output or resolve_repo_path(
        cfg.get("company_price_csv_path"),
        fallback=DEFAULT_LOCAL_PRICE_CSV,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_date = str(args.target_date or "").strip() or load_target_date(args.input_candidate_pool)
    db_path = resolve_repo_path(cfg.get("equity_price_db_path"), fallback=Path(""))
    overlay_rows = load_bridge_overlay(args.config)
    if not str(db_path) or not db_path.exists():
        if overlay_rows:
            frame = merge_bridge_overlay(pd.DataFrame(), overlay_rows)
            frame.to_csv(output_path, index=False)
            print(
                json.dumps(
                    {
                        "status": "built_from_bridge",
                        "output": str(output_path),
                        "target_date": target_date,
                        "row_count": int(len(frame)),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if output_path.exists():
            print(
                json.dumps(
                    {
                        "status": "skip_existing_csv",
                        "reason": f"missing_db:{db_path}",
                        "output": str(output_path),
                        "target_date": target_date,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        print(
            json.dumps(
                {
                    "status": "skip_missing_db",
                    "reason": f"missing_db:{db_path}",
                    "output": str(output_path),
                    "target_date": target_date,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    frame = build_company_price_snapshot_frame(
        db_path=db_path,
        target_date=target_date,
        lookback_days=int(cfg.get("lookback_days") or 45),
    )
    frame = merge_bridge_overlay(frame, overlay_rows)
    frame.to_csv(output_path, index=False)
    print(
        json.dumps(
            {
                "status": "built",
                "output": str(output_path),
                "target_date": target_date,
                "row_count": int(len(frame)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
