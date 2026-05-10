#!/usr/bin/env python3
"""Backfill missing/stale company prices through the canonical market bridge."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from radar_canonical_retry import call_with_canonical_retries
from radar_canonical_writeback_queue import DEFAULT_PRICE_QUEUE, enqueue_record, flush_queue
from radar_company_targets import extract_company_targets
from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_market_access import RadarMarketAccessFacade
from radar_price_sidecar import resolve_repo_path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_OUTPUT = ROOT / "output" / "sidecars" / "equity_prices" / "company_price_backfill_latest.json"
DEFAULT_LOCAL_EQUITY_PRICE_DB = ROOT / "output" / "sidecars" / "equity_prices" / "equity_prices.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL)
    parser.add_argument("--target-date", default="", help="Optional explicit target market sample date (YYYY-MM-DD).")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--flush-queue-only", action="store_true")
    return parser.parse_args()


def infer_target_date(candidate_pool_path: Path, explicit_target_date: str) -> str:
    text = str(explicit_target_date or "").strip()
    if text:
        return text[:10]
    payload = json.loads(candidate_pool_path.read_text(encoding="utf-8"))
    for key in ("market_sample_date", "expected_sample_date", "as_of_date", "run_at", "generated_at"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value[:10]
    raise SystemExit(f"Unable to infer target date from {candidate_pool_path}")


def load_candidate_targets(path: Path) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets, unresolved = extract_company_targets(payload)
    return payload, targets, unresolved


def localize_writable_db_path(path: Path) -> Path:
    text = str(path).strip()
    if not text:
        return DEFAULT_LOCAL_EQUITY_PRICE_DB
    if text.startswith("/root/"):
        return DEFAULT_LOCAL_EQUITY_PRICE_DB
    return path


def safe_float(value: Any) -> float | None:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    if output != output or output in {float("inf"), float("-inf")}:
        return None
    return output


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [row for row in rows if isinstance(row, dict) and str(row.get("date") or "").strip()],
        key=lambda row: str(row.get("date") or ""),
    )


def latest_date(rows: list[dict[str, Any]]) -> str:
    ordered = sort_rows(rows)
    if not ordered:
        return ""
    return str(ordered[-1].get("date") or "")


def row_amount_value(row: dict[str, Any]) -> float | None:
    amount = safe_float(row.get("amount"))
    if amount is not None and amount > 0:
        return amount
    close = safe_float(row.get("close"))
    volume = safe_float(row.get("volume"))
    if close is not None and close > 0 and volume is not None and volume > 0:
        return close * volume
    return None


def amount_ratio_from_rows(rows: list[dict[str, Any]], *, window: int = 20, min_points: int = 5) -> float | None:
    ordered = sort_rows(rows)
    if not ordered:
        return None
    latest_amount = row_amount_value(ordered[-1])
    if latest_amount is None or latest_amount <= 0:
        return None
    history = [value for row in ordered[-window:] if (value := row_amount_value(row)) is not None and value > 0]
    if len(history) < min_points:
        return None
    average_amount = sum(history) / len(history)
    if average_amount <= 0:
        return None
    return latest_amount / average_amount


def append_retry_note(note: str, label: str, retry_count: int) -> str:
    if retry_count <= 0:
        return note
    suffix = f"{label}_retries={retry_count}"
    return f"{note}; {suffix}" if note else suffix


def build_price_snapshot_row(
    *,
    rows: list[dict[str, Any]],
    target_date: str,
    company_name: str,
    instrument_id: str,
    stock_code: str,
    provider_used: str,
    source_name: str,
    note: str = "",
) -> dict[str, Any]:
    ordered = sort_rows(rows)
    if not ordered:
        return {
            "radar_object_name": company_name,
            "instrument": instrument_id,
            "stock_code": stock_code,
            "as_of_date": "",
            "lag_days": 999,
            "close": None,
            "prev_close": None,
            "daily_return": None,
            "amount_ratio_20": None,
            "positive_days_5d": None,
            "price_sidecar_source": source_name,
            "provider_used": provider_used,
            "note": note or "no_rows",
            "status": "missing",
        }
    latest = ordered[-1]
    previous = ordered[-2] if len(ordered) >= 2 else None
    latest_date_text = str(latest.get("date") or "")
    lag_days = 0
    try:
        lag_days = max(
            0,
            (datetime.fromisoformat(target_date) - datetime.fromisoformat(latest_date_text)).days,
        )
    except ValueError:
        lag_days = 999
    latest_close = safe_float(latest.get("close"))
    prev_close = safe_float((previous or {}).get("close"))
    daily_return = None
    if latest_close is not None and prev_close not in {None, 0.0}:
        daily_return = (latest_close - prev_close) / prev_close
    positive_window = ordered[-5:]
    positive_flags = 0
    for idx in range(1, len(positive_window)):
        current_close = safe_float(positive_window[idx].get("close"))
        prior_close = safe_float(positive_window[idx - 1].get("close"))
        if current_close is not None and prior_close not in {None, 0.0} and current_close > prior_close:
            positive_flags += 1
    return {
        "radar_object_name": company_name,
        "instrument": instrument_id,
        "stock_code": stock_code,
        "as_of_date": latest_date_text,
        "lag_days": lag_days,
        "close": latest_close,
        "prev_close": prev_close,
        "daily_return": daily_return,
        "amount_ratio_20": amount_ratio_from_rows(ordered),
        "positive_days_5d": positive_flags / 5.0 if positive_window else None,
        "price_sidecar_source": source_name,
        "provider_used": provider_used,
        "note": note,
        "status": "pass" if lag_days == 0 else "warn",
    }


def fetch_or_load_rows(
    *,
    facade: RadarMarketAccessFacade,
    symbol: str,
    target_date: str,
    lookback_days: int,
    force_fetch: bool,
) -> tuple[list[dict[str, Any]], str, str]:
    result = facade.get_equity_price_history(
        symbol=symbol,
        target_date=target_date,
        lookback_days=lookback_days,
        force_fetch=force_fetch,
    )
    return result.rows, result.provider_used, result.note


def build_price_queue_record(
    *,
    symbol: str,
    company_name: str,
    stock_code: str,
    target_date: str,
    provider_used: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sort_rows(rows)
    return {
        "kind": "price_historical",
        "symbol": symbol,
        "company_name": company_name,
        "stock_code": stock_code,
        "target_date": target_date,
        "provider_used": provider_used,
        "source_provider": "radar_price_backfill",
        "last_trade_date": latest_date(ordered),
        "rows": ordered,
    }


def bridge_result_key(row: dict[str, Any]) -> str:
    for key in ("market_symbol", "instrument", "stock_code", "radar_object_name"):
        value = str(row.get(key) or "").strip()
        if value:
            return f"{key}:{value.upper()}"
    return ""


def load_previous_bridge_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("results") or []
    return [row for row in rows if isinstance(row, dict)]


def merge_previous_bridge_results(
    *,
    previous_results: list[dict[str, Any]],
    current_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    current_keys = {bridge_result_key(row) for row in current_results}
    current_keys.discard("")
    preserved: list[dict[str, Any]] = []
    for row in previous_results:
        key = bridge_result_key(row)
        if key and key in current_keys:
            continue
        preserved.append(row)
    return [*preserved, *current_results], len(preserved)


def ensure_equity_price_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS equity_price_daily (
            market TEXT NOT NULL,
            instrument TEXT NOT NULL,
            company_name TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            currency TEXT,
            provider TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (instrument, trade_date)
        );
        """
    )
    conn.commit()


def mirror_rows_to_equity_price_substrate(
    *,
    db_path: Path,
    instrument_id: str,
    company_name: str,
    rows: list[dict[str, Any]],
    provider_used: str,
) -> int:
    if not str(db_path) or not rows:
        return 0
    db_path.parent.mkdir(parents=True, exist_ok=True)
    market = "hk" if instrument_id.upper().startswith("HK") else "a_share"
    updated_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload_rows: list[tuple[Any, ...]] = []
    for row in sort_rows(rows):
        trade_date = str(row.get("date") or "").strip()
        close_value = safe_float(row.get("close"))
        if not trade_date or close_value is None:
            continue
        volume_value = safe_float(row.get("volume"))
        amount_value = safe_float(row.get("amount"))
        if amount_value is None and volume_value is not None:
            amount_value = close_value * volume_value
        payload_rows.append(
            (
                market,
                instrument_id,
                company_name or instrument_id,
                trade_date,
                safe_float(row.get("open")),
                safe_float(row.get("high")),
                safe_float(row.get("low")),
                close_value,
                volume_value,
                amount_value,
                str(row.get("currency") or "").strip() or None,
                f"radar_price_backfill:{provider_used or 'provider_fetch'}",
                updated_at_utc,
            )
        )
    if not payload_rows:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        ensure_equity_price_schema(conn)
        conn.executemany(
            """
            INSERT OR REPLACE INTO equity_price_daily (
                market, instrument, company_name, trade_date, open, high, low, close,
                volume, amount, currency, provider, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload_rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(payload_rows)


def main() -> int:
    args = parse_args()
    bridge_cfg = load_config_section(args.config, "canonical_price_bridge")
    price_cfg = load_config_section(args.config, "price_sidecar")
    payload, targets, unresolved = load_candidate_targets(args.input_candidate_pool)
    target_date = infer_target_date(args.input_candidate_pool, args.target_date)
    output_path = args.output or resolve_repo_path(
        bridge_cfg.get("output_path"),
        fallback=DEFAULT_OUTPUT,
    )
    facade = RadarMarketAccessFacade.from_config(args.config)
    client = facade.client
    lookback_days = int(bridge_cfg.get("lookback_days") or 45)
    queue_path = resolve_repo_path(
        bridge_cfg.get("writeback_queue_path"),
        fallback=DEFAULT_PRICE_QUEUE,
    )
    equity_price_db_path = resolve_repo_path(
        bridge_cfg.get("equity_price_db_path") or price_cfg.get("equity_price_db_path"),
        fallback=Path(""),
    )
    equity_price_db_path = localize_writable_db_path(equity_price_db_path)
    queue_flush_summary = {
        "queue_path": str(queue_path),
        "before_count": 0,
        "flushed_count": 0,
        "remaining_count": 0,
        "status": "skip_empty",
        "errors": [],
    }
    if client.canonical_db.enabled():
        queue_flush_summary = flush_queue(
            queue_path,
            lambda record: call_with_canonical_retries(
                lambda: client.canonical_db.persist_equity_price_historical(
                    str(record.get("symbol") or ""),
                    list(record.get("rows") or []),
                    provider_used=str(record.get("provider_used") or ""),
                    source_provider=str(record.get("source_provider") or "radar_price_backfill"),
                )
            ),
        )
    if args.flush_queue_only:
        output_payload = {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "target_date": target_date,
            "market_sample_date": str(payload.get("market_sample_date") or "")[:10],
            "expected_sample_date": str(payload.get("expected_sample_date") or "")[:10],
            "candidate_count": len(targets),
            "unresolved_count": len(unresolved),
            "binding_failure_count": 0,
            "equity_price_db_path": str(equity_price_db_path) if str(equity_price_db_path) else "",
            "substrate_writeback_rows": 0,
            "writeback_queue_path": str(queue_path),
            "writeback_queue_flush": queue_flush_summary,
            "queued_writeback_count": 0,
            "results": [],
            "unresolved_targets": unresolved,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(output_payload, ensure_ascii=False, indent=2))
        return 0
    binding_failures = 0
    fetch_failure_count = 0
    target_error_count = 0
    substrate_writeback_rows = 0
    queued_writeback_count = 0
    results: list[dict[str, Any]] = []
    for target in targets:
        symbol = str(target.get("market_symbol") or "").strip()
        company_name = str(target.get("radar_object_name") or "").strip()
        stock_code = str(target.get("stock_code") or "").strip()
        binding = None
        if client.canonical_db.enabled():
            try:
                binding, _ = call_with_canonical_retries(
                    lambda: client.canonical_db._resolve_symbol_binding(symbol),
                )
            except Exception:  # pragma: no cover - upstream lock / env dependent
                binding = None
        if client.canonical_db.enabled() and not binding:
            binding_failures += 1
        instrument_id = str((binding or {}).get("instrument_id") or symbol).strip()
        provider_used = ""
        note = ""
        rows: list[dict[str, Any]] = []
        try:
            try:
                rows, provider_used, note = fetch_or_load_rows(
                    facade=facade,
                    symbol=symbol,
                    target_date=target_date,
                    lookback_days=lookback_days,
                    force_fetch=bool(args.force_fetch),
                )
            except Exception as exc:  # pragma: no cover - upstream network / provider dependent
                fetch_failure_count += 1
                note = f"fetch_error={type(exc).__name__}: {exc}"
            snapshot_row = build_price_snapshot_row(
                rows=rows,
                target_date=target_date,
                company_name=company_name,
                instrument_id=instrument_id,
                stock_code=stock_code,
                provider_used=provider_used,
                source_name="canonical_price_bridge",
                note=note,
            )
            snapshot_row["market_symbol"] = symbol
            if "canonical_persist_error=" in note and rows:
                queue_size, _ = enqueue_record(
                    queue_path,
                    build_price_queue_record(
                        symbol=symbol,
                        company_name=company_name,
                        stock_code=stock_code,
                        target_date=target_date,
                        provider_used=provider_used,
                        rows=rows,
                    ),
                )
                queued_writeback_count += 1
                snapshot_row["note"] = f"{note}; deferred_writeback_queue={queue_size}"
            substrate_writeback_rows += mirror_rows_to_equity_price_substrate(
                db_path=equity_price_db_path,
                instrument_id=instrument_id,
                company_name=company_name,
                rows=rows,
                provider_used=provider_used,
            )
        except Exception as exc:  # pragma: no cover - queue/sqlite/writeback dependent
            target_error_count += 1
            snapshot_row = build_price_snapshot_row(
                rows=[],
                target_date=target_date,
                company_name=company_name,
                instrument_id=instrument_id,
                stock_code=stock_code,
                provider_used=provider_used,
                source_name="canonical_price_bridge",
                note=f"target_error={type(exc).__name__}: {exc}",
            )
            snapshot_row["market_symbol"] = symbol
        results.append(snapshot_row)

    merged_results, preserved_result_count = merge_previous_bridge_results(
        previous_results=load_previous_bridge_results(output_path),
        current_results=results,
    )
    output_payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "target_date": target_date,
        "market_sample_date": str(payload.get("market_sample_date") or "")[:10],
        "expected_sample_date": str(payload.get("expected_sample_date") or "")[:10],
        "candidate_count": len(targets),
        "unresolved_count": len(unresolved),
        "binding_failure_count": binding_failures,
        "fetch_failure_count": fetch_failure_count,
        "target_error_count": target_error_count,
        "equity_price_db_path": str(equity_price_db_path) if str(equity_price_db_path) else "",
        "substrate_writeback_rows": substrate_writeback_rows,
        "writeback_queue_path": str(queue_path),
        "writeback_queue_flush": queue_flush_summary,
        "queued_writeback_count": queued_writeback_count,
        "result_count": len(merged_results),
        "preserved_result_count": preserved_result_count,
        "results": merged_results,
        "unresolved_targets": unresolved,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
