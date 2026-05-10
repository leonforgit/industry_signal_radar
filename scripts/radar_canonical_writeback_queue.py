#!/usr/bin/env python3
"""Durable queue for deferred canonical writeback when DuckDB is temporarily locked."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE_DIR = ROOT / "output" / "sidecars" / "canonical_writeback"
DEFAULT_PRICE_QUEUE = DEFAULT_QUEUE_DIR / "price_writeback_queue.json"
DEFAULT_FUNDAMENTAL_QUEUE = DEFAULT_QUEUE_DIR / "fundamental_writeback_queue.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dedupe_key(record: dict[str, Any]) -> str:
    kind = str(record.get("kind") or "").strip()
    symbol = str(record.get("symbol") or "").strip().upper()
    if kind == "price_historical":
        last_date = str(record.get("last_trade_date") or "").strip()
        return f"{kind}:{symbol}:{last_date}"
    statement = str(record.get("statement") or "").strip()
    period = str(record.get("period") or "").strip()
    latest_period = str(record.get("latest_period_ending") or "").strip()
    return f"{kind}:{symbol}:{statement}:{period}:{latest_period}"


def enqueue_record(path: Path, record: dict[str, Any]) -> tuple[int, int]:
    records = _load_records(path)
    record = dict(record)
    record["queued_at"] = str(record.get("queued_at") or utc_now_iso())
    record_key = _dedupe_key(record)
    deduped: list[dict[str, Any]] = []
    replaced = 0
    for existing in records:
        if _dedupe_key(existing) == record_key:
            replaced += 1
            continue
        deduped.append(existing)
    deduped.append(record)
    _write_records(path, deduped)
    return len(deduped), replaced


def flush_queue(
    path: Path,
    handler: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    records = _load_records(path)
    if not records:
        return {
            "queue_path": str(path),
            "before_count": 0,
            "flushed_count": 0,
            "remaining_count": 0,
            "status": "skip_empty",
            "errors": [],
        }
    remaining: list[dict[str, Any]] = []
    errors: list[str] = []
    flushed_count = 0
    for record in records:
        try:
            handler(record)
        except Exception as exc:
            remaining.append(record)
            errors.append(f"{_dedupe_key(record)} -> {type(exc).__name__}")
        else:
            flushed_count += 1
    _write_records(path, remaining)
    return {
        "queue_path": str(path),
        "before_count": len(records),
        "flushed_count": flushed_count,
        "remaining_count": len(remaining),
        "status": "pass" if not remaining else ("partial" if flushed_count else "deferred"),
        "errors": errors[:12],
    }
