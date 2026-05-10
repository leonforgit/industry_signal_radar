#!/usr/bin/env python3
"""Backfill missing company fundamentals through the canonical market bridge."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from radar_canonical_retry import call_with_canonical_retries
from radar_canonical_writeback_queue import DEFAULT_FUNDAMENTAL_QUEUE, enqueue_record, flush_queue
from radar_company_targets import extract_company_targets
from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_market_access import RadarMarketAccessFacade
from radar_price_sidecar import resolve_repo_path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_OUTPUT = ROOT / "output" / "sidecars" / "fundamentals" / "company_fundamental_backfill_latest.json"
STATEMENT_TYPES = ("income", "balance", "cash")
PERIOD_TYPES = ("quarter", "annual")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--limit", type=int, default=4)
    return parser.parse_args()


def load_candidate_targets(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return extract_company_targets(payload)


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "row_count": 0,
            "latest_period_ending": "",
            "currency": "",
            "metrics": [],
        }
    first = rows[0]
    metrics = sorted(
        key for key in first.keys() if key not in {"period_ending", "currency"} and first.get(key) is not None
    )
    return {
        "row_count": len(rows),
        "latest_period_ending": str(first.get("period_ending") or ""),
        "currency": str(first.get("currency") or ""),
        "metrics": metrics[:12],
    }


def build_fundamental_queue_record(
    *,
    symbol: str,
    company_name: str,
    stock_code: str,
    statement: str,
    period: str,
    provider_used: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: str(row.get("period_ending") or ""),
        reverse=True,
    )
    latest_period = str((ordered[0] or {}).get("period_ending") or "") if ordered else ""
    return {
        "kind": "fundamental_statement",
        "symbol": symbol,
        "company_name": company_name,
        "stock_code": stock_code,
        "statement": statement,
        "period": period,
        "provider_used": provider_used,
        "source_provider": "radar_fundamental_backfill",
        "latest_period_ending": latest_period,
        "rows": ordered,
    }


def load_financial_snapshot_index(snapshot_paths: list[Path]) -> tuple[dict[str, dict[str, str]], str]:
    for path in snapshot_paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            index: dict[str, dict[str, str]] = {}
            for row in reader:
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                index[symbol] = {str(key): str(value or "") for key, value in row.items()}
        return index, str(path)
    return {}, ""


def build_snapshot_summary(row: dict[str, str]) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "source": "a_share_financial_snapshot",
        "company_name": row.get("company_name") or row.get("\ufeffcompany_name") or "",
        "report_period": row.get("报告期") or "",
        "report_type": row.get("报告类型") or "",
        "announcement_date": row.get("公告日期") or "",
        "revenue": row.get("营业总收入") or "",
        "net_profit": row.get("归母净利润") or "",
        "net_profit_excl_nonrecurring": row.get("扣非净利润") or "",
        "eps": row.get("基本每股收益") or "",
        "book_value_per_share": row.get("每股净资产") or "",
        "roe_weighted": row.get("加权ROE") or "",
        "gross_margin": row.get("销售毛利率") or "",
        "net_margin": row.get("销售净利率") or "",
        "operating_cashflow_over_revenue": row.get("经营现金流/营收") or "",
        "debt_ratio": row.get("资产负债率") or "",
        "total_assets": row.get("总资产") or "",
        "total_liabilities": row.get("总负债") or "",
        "currency": row.get("货币") or "",
        "industry": row.get("行业") or "",
        "source_note": row.get("notes") or "",
        "data_status": row.get("data_status") or "",
    }


def load_or_fetch_statement(
    *,
    facade: RadarMarketAccessFacade,
    symbol: str,
    statement: str,
    period: str,
    limit: int,
    force_fetch: bool,
) -> tuple[list[dict[str, Any]], str, str]:
    result = facade.get_equity_fundamental_statement(
        symbol=symbol,
        statement=statement,
        period=period,
        limit=limit,
        force_fetch=force_fetch,
    )
    return result.rows, result.provider_used, result.note


def main() -> int:
    args = parse_args()
    bridge_cfg = load_config_section(args.config, "canonical_fundamental_bridge")
    targets, unresolved = load_candidate_targets(args.input_candidate_pool)
    source_candidate_count = len(targets)
    max_target_count = max(int(bridge_cfg.get("max_target_count") or 0), 0)
    if max_target_count > 0:
        targets = targets[:max_target_count]
    output_path = args.output or resolve_repo_path(
        bridge_cfg.get("output_path"),
        fallback=DEFAULT_OUTPUT,
    )
    snapshot_paths = [
        resolve_repo_path(path, fallback=Path(""))
        for path in (bridge_cfg.get("a_share_financial_snapshot_paths") or [])
        if str(path).strip()
    ]
    snapshot_index, snapshot_source_path = load_financial_snapshot_index(snapshot_paths)
    queue_path = resolve_repo_path(
        bridge_cfg.get("writeback_queue_path"),
        fallback=DEFAULT_FUNDAMENTAL_QUEUE,
    )
    facade = RadarMarketAccessFacade.from_config(args.config)
    client = facade.client
    limit = max(1, int(args.limit or bridge_cfg.get("limit") or 4))
    prefer_summary_snapshot = bool(bridge_cfg.get("prefer_summary_snapshot", True))
    auto_fetch_missing_statements = bool(bridge_cfg.get("auto_fetch_missing_statements", False))
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
                lambda: client.canonical_db.persist_equity_fundamental_statement_results(
                    str(record.get("symbol") or ""),
                    statement=str(record.get("statement") or ""),
                    period=str(record.get("period") or ""),
                    rows=list(record.get("rows") or []),
                    provider_used=str(record.get("provider_used") or ""),
                    source_provider=str(record.get("source_provider") or "radar_fundamental_backfill"),
                )
            ),
        )

    binding_failure_count = 0
    summary_fallback_count = 0
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
                    lambda: client.canonical_db._resolve_symbol_binding(symbol),  # noqa: SLF001
                )
            except Exception:  # pragma: no cover - upstream lock / env dependent
                binding = None
        if client.canonical_db.enabled() and not binding:
            binding_failure_count += 1
        item: dict[str, Any] = {
            "candidate_id": str(target.get("candidate_id") or ""),
            "radar_object_name": company_name,
            "market_symbol": symbol,
            "stock_code": stock_code,
            "instrument_id": str((binding or {}).get("instrument_id") or symbol).strip(),
            "market": str((binding or {}).get("market") or "").strip(),
            "statements": {},
            "missing_required": [],
            "coverage_mode": "full_statements",
            "summary_fallback": {},
        }
        snapshot_summary = build_snapshot_summary(snapshot_index.get(symbol.upper(), {}))
        if snapshot_summary and prefer_summary_snapshot and not args.force_fetch:
            item["coverage_mode"] = "summary_snapshot"
            item["summary_fallback"] = snapshot_summary
            item["coverage_status"] = "pass"
            summary_fallback_count += 1
            results.append(item)
            continue
        if not args.force_fetch and not auto_fetch_missing_statements:
            item["coverage_mode"] = "deferred_full_statements"
            item["missing_required"] = [f"{period}_{statement}" for period in PERIOD_TYPES for statement in STATEMENT_TYPES]
            item["coverage_status"] = "fail"
            results.append(item)
            continue
        for period in PERIOD_TYPES:
            for statement in STATEMENT_TYPES:
                key = f"{period}_{statement}"
                rows, provider_used, note = load_or_fetch_statement(
                    facade=facade,
                    symbol=symbol,
                    statement=statement,
                    period=period,
                    limit=limit,
                    force_fetch=bool(args.force_fetch),
                )
                summary = summarize_results(rows)
                status = "pass" if summary["row_count"] > 0 else "fail"
                if status != "pass":
                    item["missing_required"].append(key)
                if "canonical_persist_error=" in note and rows:
                    queue_size, _ = enqueue_record(
                        queue_path,
                        build_fundamental_queue_record(
                            symbol=symbol,
                            company_name=company_name,
                            stock_code=stock_code,
                            statement=statement,
                            period=period,
                            provider_used=provider_used,
                            rows=rows,
                        ),
                    )
                    queued_writeback_count += 1
                    note = f"{note}; deferred_writeback_queue={queue_size}"
                item["statements"][key] = {
                    "status": status,
                    "provider_used": provider_used,
                    "note": note,
                    **summary,
                }
        if item["missing_required"] and snapshot_summary:
            item["coverage_mode"] = "summary_fallback"
            item["summary_fallback"] = snapshot_summary
            summary_fallback_count += 1
        item["coverage_status"] = "pass" if (not item["missing_required"] or item["coverage_mode"] == "summary_fallback") else "fail"
        results.append(item)

    status = (
        "pass"
        if targets
        and not any(item.get("missing_required") and str(item.get("coverage_mode") or "") != "summary_fallback" for item in results)
        else "warn"
    )
    if not targets:
        status = "skip"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_candidate_count": source_candidate_count,
        "candidate_count": len(targets),
        "max_target_count": max_target_count,
        "resolved_count": len(results),
        "unresolved_count": len(unresolved),
        "binding_failure_count": binding_failure_count,
        "status": status,
        "openbb_service_status": str(facade.discovery.get("status") or ""),
        "openbb_base_url": str(facade.discovery.get("base_url") or ""),
        "openbb_note": str(facade.discovery.get("note") or ""),
        "openbb_checked_urls": facade.discovery.get("checked_urls") or [],
        "summary_fallback_count": summary_fallback_count,
        "summary_fallback_source_path": snapshot_source_path,
        "writeback_queue_path": str(queue_path),
        "writeback_queue_flush": queue_flush_summary,
        "queued_writeback_count": queued_writeback_count,
        "results": results,
        "unresolved_targets": unresolved,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
