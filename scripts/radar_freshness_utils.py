#!/usr/bin/env python3
"""Shared freshness helpers for Radar market-sample outputs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_MARKET_TZ = "Asia/Shanghai"
DEFAULT_MARKET_OPEN_TIME = "09:30"
DEFAULT_MARKET_SAMPLE_READY_TIME = "18:00"

# Keep this small and explicit: Radar needs the expected A-share sample date for
# freshness gates, not a full exchange calendar engine. Extend this list when a
# new production holiday window is observed or when a generated calendar is wired in.
CN_A_SHARE_HOLIDAYS = frozenset(
    {
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
    }
)


def parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def snapshot_market_sample_date(snapshot: dict[str, Any]) -> date | None:
    if not isinstance(snapshot, dict):
        return None
    return parse_iso_date(snapshot.get("market_sample_date")) or parse_iso_date(snapshot.get("as_of_date"))


def parse_market_open_time(value: str | None) -> tuple[int, int]:
    text = str(value or DEFAULT_MARKET_OPEN_TIME).strip()
    if "-" in text:
        text = text.split("-", 1)[0].strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = max(0, min(23, int(hour_text)))
        minute = max(0, min(59, int(minute_text)))
    except (TypeError, ValueError):
        return (9, 30)
    return (hour, minute)


def is_market_trading_day(value: date) -> bool:
    return value.weekday() < 5 and value not in CN_A_SHARE_HOLIDAYS


def previous_market_weekday(current_date: date) -> date:
    cursor = current_date
    while True:
        cursor = cursor.fromordinal(cursor.toordinal() - 1)
        if is_market_trading_day(cursor):
            return cursor


def previous_market_weekday_text(value: Any) -> str:
    parsed = parse_iso_date(value)
    if parsed is None:
        return ""
    return previous_market_weekday(parsed).isoformat()


def market_now(market_tz: str = DEFAULT_MARKET_TZ, *, now: datetime | None = None) -> datetime:
    zone = ZoneInfo(str(market_tz or DEFAULT_MARKET_TZ))
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def expected_sample_date(
    snapshot: dict[str, Any] | None = None,
    *,
    market_tz: str | None = None,
    market_open_time: str | None = None,
    now: datetime | None = None,
) -> date:
    resolved_market_tz = str(
        market_tz or ((snapshot or {}).get("market_tz") if isinstance(snapshot, dict) else "") or DEFAULT_MARKET_TZ
    ).strip() or DEFAULT_MARKET_TZ
    sample_ready_time = market_open_time
    if sample_ready_time is None and isinstance(snapshot, dict):
        sample_ready_time = str(snapshot.get("market_sample_ready_time") or snapshot.get("market_open_time") or "").strip()
    open_hour, open_minute = parse_market_open_time(sample_ready_time or DEFAULT_MARKET_SAMPLE_READY_TIME)
    current = market_now(resolved_market_tz, now=now)
    if is_market_trading_day(current.date()) and (current.hour, current.minute) >= (open_hour, open_minute):
        return current.date()
    return previous_market_weekday(current.date())


def compute_sample_age_days(
    snapshot: dict[str, Any],
    *,
    market_tz: str | None = None,
    now: datetime | None = None,
) -> int | None:
    sample_date = snapshot_market_sample_date(snapshot)
    if sample_date is None:
        return None
    resolved_market_tz = str(market_tz or snapshot.get("market_tz") or DEFAULT_MARKET_TZ).strip() or DEFAULT_MARKET_TZ
    current_date = market_now(resolved_market_tz, now=now).date()
    return max(0, (current_date - sample_date).days)


def compute_freshness_lag_days(
    snapshot: dict[str, Any],
    *,
    market_tz: str | None = None,
    market_open_time: str | None = None,
    now: datetime | None = None,
) -> int | None:
    sample_date = snapshot_market_sample_date(snapshot)
    if sample_date is None:
        return None
    expected_date = expected_sample_date(
        snapshot,
        market_tz=market_tz,
        market_open_time=market_open_time,
        now=now,
    )
    return max(0, (expected_date - sample_date).days)


def summarize_snapshot_freshness(
    snapshot: dict[str, Any],
    *,
    market_tz: str | None = None,
    market_open_time: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_market_tz = str(market_tz or snapshot.get("market_tz") or DEFAULT_MARKET_TZ).strip() or DEFAULT_MARKET_TZ
    expected_date = expected_sample_date(
        snapshot,
        market_tz=resolved_market_tz,
        market_open_time=market_open_time,
        now=now,
    )
    return {
        "sample_age_days": compute_sample_age_days(snapshot, market_tz=resolved_market_tz, now=now),
        "expected_sample_date": expected_date.isoformat(),
        "freshness_lag_days": compute_freshness_lag_days(
            snapshot,
            market_tz=resolved_market_tz,
            market_open_time=market_open_time,
            now=now,
        ),
    }
