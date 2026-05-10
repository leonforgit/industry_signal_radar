from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

from build_radar_bark_summary import build_bark_summary, build_trigger, should_trigger_bark
from build_radar_candidate_pool import build_candidate_pool_payload
from build_radar_catalyst_inventory import build_inventory, load_optional_json
from build_radar_opportunity_snapshot import build_object as build_snapshot_object
from build_radar_opportunity_snapshot import build_snapshot
from build_radar_research_handoff import build_handoff_payload, render_handoff
from render_radar_daily_report import load_rules, render_report
from radar_bark import build_bark_config, build_bark_payload, send_bark_payload
from radar_announcements import build_announcement_overlay
from radar_config import load_config_section, load_source_manifest
from radar_event_db import DEFAULT_SCHEMA_PATH, initialize_event_db
from radar_flow_signals import build_flow_signal_overlay
from radar_fundamental_proxy import build_fundamental_proxy_overlay
from radar_industry_registry import (
    build_industry_overlay_context,
    load_industry_etf_primary,
    load_industry_registry,
    load_industry_stock_primary,
    load_theme_chain_overlay_members,
    load_theme_chain_overlays,
    parse_heat_keywords,
)
from radar_shared_news import build_shared_news_policy_overlay, load_json_file, shared_news_config
from radar_runtime_bootstrap import (
    build_summary as build_bootstrap_summary,
    materialize_runtime_dirs,
    resolve_runtime_paths,
    validate_runtime_guardrails,
)
from radar_runtime_bootstrap import record_bootstrap_health
from radar_runtime_health import ensure_event_db, record_runtime_health, record_source_health, write_json


ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_OUTPUT_LOCK = ROOT / "state" / "radar_workspace_outputs.lock"
INVESTMENT_SCRIPTS_DIR = ROOT.parent.parent / "scripts"
if str(INVESTMENT_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(INVESTMENT_SCRIPTS_DIR))

try:
    from openbb_adapter import OpenBBAdapterError, OpenBBClient
except ModuleNotFoundError:  # pragma: no cover - optional sidecar
    OpenBBAdapterError = RuntimeError
    OpenBBClient = None  # type: ignore[assignment]

LOCAL_TZ = ZoneInfo("Europe/London")
DEFAULT_MARKET_TZ = ZoneInfo("Asia/Shanghai")
DEBUG_PROGRESS = os.environ.get("RADAR_DEBUG_PROGRESS", "") == "1"
DEFAULT_OPENBB_MARKET_CONTEXT = {
    "enabled": True,
    "lookback_days": 30,
    "symbols": [
        {"symbol": "SPY", "name": "SPY", "label": "美股大盘", "provider": "yfinance"},
        {"symbol": "QQQ", "name": "QQQ", "label": "美股科技", "provider": "yfinance"},
        {"symbol": "KWEB", "name": "KWEB", "label": "中概互联网", "provider": "yfinance"},
    ],
}
DEFAULT_OPENBB_MACRO_CONTEXT = {
    "enabled": True,
    "series": [
        {"symbol": "DCOILWTICO", "label": "WTI油价", "transform": "pct_change", "lookback_points": 20, "start_days": 90},
        {"symbol": "DGS10", "label": "美债10Y", "transform": "delta_bps", "lookback_points": 20, "start_days": 90},
        {"symbol": "CPIAUCSL", "label": "美国CPI", "transform": "yoy_pct", "lookback_points": 12, "start_days": 450},
    ],
}
DEFAULT_OPENBB_SHIPPING_CONTEXT = {
    "enabled": True,
    "lookback_days": 60,
    "change_window_days": 7,
    "chokepoints": [
        {"id": "strait_of_hormuz", "label": "霍尔木兹"},
        {"id": "suez_canal", "label": "苏伊士"},
        {"id": "bab_el_mandeb_strait", "label": "曼德海峡"},
        {"id": "taiwan_strait", "label": "台湾海峡"},
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Industry Signal Radar market scan.")
    parser.add_argument("--check-only", action="store_true", help="Validate runtime paths and configs without scanning.")
    parser.add_argument("--bootstrap-only", action="store_true", help="Only materialize runtime and initialize state.")
    parser.add_argument("--skip-bark", action="store_true", help="Skip Bark sending even if device keys exist.")
    parser.add_argument("--run-at", default=None, help="ISO 8601 timestamp override.")
    parser.add_argument("--runtime-root-override", type=Path, default=None, help="Optional runtime root override.")
    return parser.parse_args()


def debug_progress(message: str) -> None:
    if DEBUG_PROGRESS:
        print(f"[radar-debug] {message}", flush=True)


def try_acquire_workspace_output_lock(label: str) -> Any | None:
    WORKSPACE_OUTPUT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = WORKSPACE_OUTPUT_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({"owner": label, "pid": os.getpid(), "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
    handle.flush()
    return handle


def release_workspace_output_lock(handle: Any | None) -> None:
    if handle is None:
        return
    try:
        handle.seek(0)
        handle.truncate()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_run_dt(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_sw_symbol(sw_code: str) -> str:
    return sw_code.replace(".SI", "").strip()


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def percent_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def ratio_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}x"


def percentile_score(series: pd.Series) -> pd.Series:
    return series.rank(pct=True, method="average")


def inverse_percentile_score(series: pd.Series) -> pd.Series:
    return 1.0 - percentile_score(series.fillna(series.max()))


def current_market_phase(run_dt: datetime) -> str:
    polling = load_config_section(None, "polling")
    market_tz = ZoneInfo(str(polling.get("market_timezone", "Asia/Shanghai")))
    local_dt = run_dt.astimezone(market_tz)
    hhmm = local_dt.strftime("%H:%M")
    morning = str(polling.get("morning_session", "09:30-11:30"))
    afternoon = str(polling.get("afternoon_session", "13:00-15:00"))
    if morning.split("-")[0] <= hhmm <= morning.split("-")[1]:
        return "market_session_morning"
    if afternoon.split("-")[0] <= hhmm <= afternoon.split("-")[1]:
        return "market_session_afternoon"
    return "off_session"


def current_market_local_dt(run_dt: datetime) -> datetime:
    polling = load_config_section(None, "polling")
    market_tz = ZoneInfo(str(polling.get("market_timezone", "Asia/Shanghai")))
    return run_dt.astimezone(market_tz)


def configured_cache_dir() -> Path:
    runtime_paths = load_config_section(None, "runtime_paths")
    cache_dir = runtime_paths.get("cache_dir")
    base = Path(str(cache_dir)) if cache_dir else ROOT / "cache"
    if base.is_absolute() and str(base).startswith("/root/"):
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            base = ROOT / "cache"
    target = base / "index_hist_sw"
    target.mkdir(parents=True, exist_ok=True)
    return target


def normalize_market_context_specs(raw: Any) -> list[dict[str, str]]:
    source = raw if isinstance(raw, list) else DEFAULT_OPENBB_MARKET_CONTEXT["symbols"]
    items: list[dict[str, str]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        provider = str(item.get("provider") or "yfinance").strip() or "yfinance"
        name = str(item.get("name") or symbol).strip() or symbol
        label = str(item.get("label") or name).strip() or name
        items.append(
            {
                "symbol": symbol,
                "provider": provider,
                "name": name,
                "label": label,
            }
        )
    return items


def resolve_openbb_market_context_config() -> dict[str, Any]:
    scan_cfg = load_config_section(None, "scan")
    raw = scan_cfg.get("openbb_market_context", {})
    payload = raw if isinstance(raw, dict) else {}
    enabled = bool(payload.get("enabled", DEFAULT_OPENBB_MARKET_CONTEXT["enabled"]))
    try:
        lookback_days = int(payload.get("lookback_days", DEFAULT_OPENBB_MARKET_CONTEXT["lookback_days"]))
    except (TypeError, ValueError):
        lookback_days = int(DEFAULT_OPENBB_MARKET_CONTEXT["lookback_days"])
    specs = normalize_market_context_specs(payload.get("symbols"))
    return {
        "enabled": enabled,
        "lookback_days": max(10, lookback_days),
        "symbols": specs,
    }


def configure_openbb_base_url_from_config() -> None:
    if str(os.environ.get("OPENBB_BASE_URL") or "").strip():
        return
    for section_name in ("market_access_facade", "canonical_fundamental_bridge"):
        section = load_config_section(None, section_name)
        candidates = section.get("openbb_base_url_candidates") or []
        if not isinstance(candidates, list):
            continue
        for raw in candidates:
            base_url = str(raw or "").strip().rstrip("/")
            if base_url:
                os.environ["OPENBB_BASE_URL"] = base_url
                return


def normalize_openbb_history(rows: list[dict[str, Any]]) -> pd.DataFrame:
    data: list[dict[str, Any]] = []
    for row in rows:
        raw_date = row.get("date")
        raw_close = row.get("close")
        parsed_date = pd.to_datetime(raw_date, errors="coerce")
        close = pd.to_numeric(raw_close, errors="coerce")
        if pd.isna(parsed_date) or pd.isna(close):
            continue
        data.append(
            {
                "date": parsed_date.date(),
                "close": float(close),
            }
        )
    if not data:
        return pd.DataFrame(columns=["date", "close"])
    frame = pd.DataFrame(data)
    return frame.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def normalize_openbb_series_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    data: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed_date = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(parsed_date):
            continue
        numeric_items = [
            (str(key), pd.to_numeric(value, errors="coerce"))
            for key, value in row.items()
            if str(key) != "date"
        ]
        numeric_items = [(key, value) for key, value in numeric_items if not pd.isna(value)]
        if not numeric_items:
            continue
        key, value = numeric_items[0]
        data.append(
            {
                "date": parsed_date.date(),
                "series_key": key,
                "value": float(value),
            }
        )
    if not data:
        return pd.DataFrame(columns=["date", "series_key", "value"])
    frame = pd.DataFrame(data)
    return frame.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def normalize_openbb_shipping_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    data: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed_date = pd.to_datetime(row.get("date"), errors="coerce")
        capacity_total = pd.to_numeric(row.get("capacity_total"), errors="coerce")
        vessels_total = pd.to_numeric(row.get("vessels_total"), errors="coerce")
        if pd.isna(parsed_date) or (pd.isna(capacity_total) and pd.isna(vessels_total)):
            continue
        data.append(
            {
                "date": parsed_date.date(),
                "chokepoint": str(row.get("chokepoint", "")).strip(),
                "capacity_total": None if pd.isna(capacity_total) else float(capacity_total),
                "vessels_total": None if pd.isna(vessels_total) else float(vessels_total),
            }
        )
    if not data:
        return pd.DataFrame(columns=["date", "chokepoint", "capacity_total", "vessels_total"])
    frame = pd.DataFrame(data)
    return frame.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def compute_price_drawdown_pct(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak <= 0:
            continue
        drawdown = value / peak - 1.0
        max_drawdown = min(max_drawdown, drawdown)
    return abs(max_drawdown) * 100


def summarize_market_context_window(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        raise RuntimeError("Market context data is empty.")
    end_close = float(frame.iloc[-1]["close"])
    prev_close = float(frame.iloc[-2]["close"]) if len(frame) >= 2 else None
    five_back_close = float(frame.iloc[-6]["close"]) if len(frame) >= 6 else None
    twenty_back_close = float(frame.iloc[-21]["close"]) if len(frame) >= 21 else None
    recent_values = frame["close"].astype(float).tail(21).tolist()
    return {
        "close": end_close,
        "last_date": frame.iloc[-1]["date"].isoformat(),
        "observation_count": int(len(frame)),
        "one_day_return": ((end_close / prev_close) - 1.0) * 100 if prev_close not in {None, 0} else None,
        "five_day_return": ((end_close / five_back_close) - 1.0) * 100 if five_back_close not in {None, 0} else None,
        "twenty_day_return": ((end_close / twenty_back_close) - 1.0) * 100 if twenty_back_close not in {None, 0} else None,
        "max_drawdown_20d": compute_price_drawdown_pct(recent_values),
    }


def normalize_openbb_macro_series_specs(raw: Any) -> list[dict[str, Any]]:
    source = raw if isinstance(raw, list) else DEFAULT_OPENBB_MACRO_CONTEXT["series"]
    items: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        items.append(
            {
                "symbol": symbol,
                "label": str(item.get("label") or symbol).strip() or symbol,
                "transform": str(item.get("transform") or "pct_change").strip() or "pct_change",
                "lookback_points": max(2, int(item.get("lookback_points") or 20)),
                "start_days": max(30, int(item.get("start_days") or 365)),
            }
        )
    return items


def normalize_openbb_shipping_specs(raw: Any) -> list[dict[str, str]]:
    source = raw if isinstance(raw, list) else DEFAULT_OPENBB_SHIPPING_CONTEXT["chokepoints"]
    items: list[dict[str, str]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or "").strip()
        if not identifier:
            continue
        label = str(item.get("label") or identifier).strip() or identifier
        items.append({"id": identifier, "label": label})
    return items


def resolve_openbb_macro_context_config() -> dict[str, Any]:
    scan_cfg = load_config_section(None, "scan")
    raw_all = scan_cfg.get("openbb_sidecar_context", {})
    raw = raw_all.get("fred", {}) if isinstance(raw_all, dict) and isinstance(raw_all.get("fred", {}), dict) else {}
    enabled = bool(raw.get("enabled", DEFAULT_OPENBB_MACRO_CONTEXT["enabled"]))
    return {
        "enabled": enabled,
        "series": normalize_openbb_macro_series_specs(raw.get("series")),
    }


def resolve_openbb_shipping_context_config() -> dict[str, Any]:
    scan_cfg = load_config_section(None, "scan")
    raw_all = scan_cfg.get("openbb_sidecar_context", {})
    raw = raw_all.get("shipping", {}) if isinstance(raw_all, dict) and isinstance(raw_all.get("shipping", {}), dict) else {}
    enabled = bool(raw.get("enabled", DEFAULT_OPENBB_SHIPPING_CONTEXT["enabled"]))
    lookback_days = max(14, int(raw.get("lookback_days") or DEFAULT_OPENBB_SHIPPING_CONTEXT["lookback_days"]))
    change_window_days = max(3, int(raw.get("change_window_days") or DEFAULT_OPENBB_SHIPPING_CONTEXT["change_window_days"]))
    return {
        "enabled": enabled,
        "lookback_days": lookback_days,
        "change_window_days": change_window_days,
        "chokepoints": normalize_openbb_shipping_specs(raw.get("chokepoints")),
    }


def summarize_openbb_macro_series(frame: pd.DataFrame, spec: dict[str, Any]) -> dict[str, Any]:
    if frame.empty:
        raise RuntimeError(f"No macro rows returned for {spec['symbol']}.")
    values = frame["value"].astype(float).tolist()
    latest = float(values[-1])
    last_date = frame.iloc[-1]["date"].isoformat()
    transform = str(spec.get("transform", "pct_change"))
    lookback_points = int(spec.get("lookback_points", 20))
    summary_value: float | None = None
    summary_unit = "%"
    if transform == "delta_bps":
        previous = float(values[-lookback_points]) if len(values) >= lookback_points else float(values[0])
        summary_value = (latest - previous) * 100
        summary_unit = "bp"
    elif transform == "yoy_pct":
        previous = float(values[-(lookback_points + 1)]) if len(values) >= (lookback_points + 1) else None
        if previous not in {None, 0}:
            summary_value = (latest / previous - 1.0) * 100
    else:
        previous = float(values[-lookback_points]) if len(values) >= lookback_points else float(values[0])
        if previous != 0:
            summary_value = (latest / previous - 1.0) * 100
    return {
        "symbol": spec["symbol"],
        "label": spec["label"],
        "transform": transform,
        "last_date": last_date,
        "latest_value": latest,
        "summary_value": summary_value,
        "summary_unit": summary_unit,
        "lookback_points": lookback_points,
    }


def summarize_openbb_shipping_series(frame: pd.DataFrame, spec: dict[str, Any], change_window_days: int, info_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if frame.empty:
        raise RuntimeError(f"No shipping rows returned for {spec['id']}.")
    last_date = frame.iloc[-1]["date"].isoformat()
    latest_capacity = safe_float(frame.iloc[-1]["capacity_total"])
    latest_vessels = safe_float(frame.iloc[-1]["vessels_total"])
    recent = frame.tail(change_window_days)
    previous = frame.iloc[-(change_window_days * 2):-change_window_days] if len(frame.index) > change_window_days else frame.iloc[0:0]
    capacity_change_pct = None
    vessels_change_pct = None
    recent_capacity = pd.to_numeric(recent["capacity_total"], errors="coerce").dropna()
    previous_capacity = pd.to_numeric(previous["capacity_total"], errors="coerce").dropna()
    if not recent_capacity.empty and not previous_capacity.empty and float(previous_capacity.mean()) != 0:
        capacity_change_pct = (float(recent_capacity.mean()) / float(previous_capacity.mean()) - 1.0) * 100
    recent_vessels = pd.to_numeric(recent["vessels_total"], errors="coerce").dropna()
    previous_vessels = pd.to_numeric(previous["vessels_total"], errors="coerce").dropna()
    if not recent_vessels.empty and not previous_vessels.empty and float(previous_vessels.mean()) != 0:
        vessels_change_pct = (float(recent_vessels.mean()) / float(previous_vessels.mean()) - 1.0) * 100
    info = info_map.get(str(frame.iloc[-1].get("chokepoint", "")).strip(), {})
    return {
        "id": spec["id"],
        "label": spec["label"],
        "last_date": last_date,
        "latest_capacity_total": latest_capacity,
        "latest_vessels_total": latest_vessels,
        "capacity_change_pct": capacity_change_pct,
        "vessels_change_pct": vessels_change_pct,
        "industry_top1": str(info.get("industry_top1", "")).strip(),
        "industry_top2": str(info.get("industry_top2", "")).strip(),
    }


def fetch_openbb_market_context(run_dt: datetime) -> dict[str, Any]:
    config = resolve_openbb_market_context_config()
    if not config["enabled"]:
        return {
            "enabled": False,
            "source_id": "openbb:market_context",
            "items": [],
            "errors": [],
            "note": "disabled by scan.openbb_market_context.enabled",
        }
    if not config["symbols"]:
        return {
            "enabled": False,
            "source_id": "openbb:market_context",
            "items": [],
            "errors": ["No OpenBB market-context symbols configured."],
            "note": "no symbols configured",
        }
    if OpenBBClient is None:
        return {
            "enabled": False,
            "source_id": "openbb:market_context",
            "items": [],
            "errors": ["OpenBB adapter unavailable in radar runtime."],
            "note": "adapter unavailable",
        }

    configure_openbb_base_url_from_config()
    client = OpenBBClient()
    request_end = run_dt.date()
    request_start = request_end - timedelta(days=int(config["lookback_days"]))
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    provider_ids: set[str] = set()

    for spec in config["symbols"]:
        provider_ids.add(spec["provider"])
        try:
            rows = client.get_equity_price_historical(
                spec["symbol"],
                provider=spec["provider"],
                start_date=request_start,
                end_date=request_end,
            )
            frame = normalize_openbb_history(rows)
            summary = summarize_market_context_window(frame)
            items.append(
                {
                    **spec,
                    "source": f"openbb:{spec['provider']}",
                    **summary,
                }
            )
        except (OpenBBAdapterError, RuntimeError, ValueError) as exc:
            errors.append(f"{spec['symbol']}: {exc}")

    source_suffix = "+".join(sorted(provider_ids)) if provider_ids else "market_context"
    latest_dates = sorted({str(item.get("last_date", "")) for item in items if str(item.get("last_date", "")).strip()})
    note_parts: list[str] = []
    if latest_dates:
        note_parts.append(f"latest_dates={','.join(latest_dates)}")
    if errors:
        note_parts.append(f"errors={len(errors)}")
    return {
        "enabled": bool(items),
        "source_id": f"openbb:{source_suffix}",
        "items": items,
        "errors": errors,
        "note": "; ".join(note_parts),
    }


def fetch_openbb_macro_context(run_dt: datetime) -> dict[str, Any]:
    config = resolve_openbb_macro_context_config()
    if not config["enabled"]:
        return {
            "enabled": False,
            "source_id": "openbb:fred_context",
            "items": [],
            "errors": [],
            "note": "disabled by scan.openbb_sidecar_context.fred.enabled",
        }
    if OpenBBClient is None:
        return {
            "enabled": False,
            "source_id": "openbb:fred_context",
            "items": [],
            "errors": ["OpenBB adapter unavailable in radar runtime."],
            "note": "adapter unavailable",
        }
    configure_openbb_base_url_from_config()
    client = OpenBBClient()
    if not hasattr(client, "get_fred_series"):
        return {
            "enabled": False,
            "source_id": "openbb:fred_context",
            "items": [],
            "errors": [],
            "note": "disabled: adapter missing get_fred_series",
        }
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for spec in config["series"]:
        try:
            rows = client.get_fred_series(
                spec["symbol"],
                provider="fred",
                start_date=(run_dt.date() - timedelta(days=int(spec["start_days"]))),
                end_date=run_dt.date(),
            )
            frame = normalize_openbb_series_rows(rows)
            items.append(summarize_openbb_macro_series(frame, spec))
        except (OpenBBAdapterError, RuntimeError, ValueError) as exc:
            errors.append(f"{spec['symbol']}: {exc}")
    latest_dates = sorted({str(item.get("last_date", "")).strip() for item in items if str(item.get("last_date", "")).strip()})
    note_parts: list[str] = []
    if latest_dates:
        note_parts.append(f"latest_dates={','.join(latest_dates)}")
    if errors:
        note_parts.append(f"errors={len(errors)}")
    return {
        "enabled": bool(items),
        "source_id": "openbb:fred_context",
        "items": items,
        "errors": errors,
        "note": "; ".join(note_parts),
    }


def fetch_openbb_shipping_context(run_dt: datetime) -> dict[str, Any]:
    config = resolve_openbb_shipping_context_config()
    if not config["enabled"]:
        return {
            "enabled": False,
            "source_id": "openbb:shipping_context",
            "items": [],
            "errors": [],
            "note": "disabled by scan.openbb_sidecar_context.shipping.enabled",
        }
    if OpenBBClient is None:
        return {
            "enabled": False,
            "source_id": "openbb:shipping_context",
            "items": [],
            "errors": ["OpenBB adapter unavailable in radar runtime."],
            "note": "adapter unavailable",
        }
    configure_openbb_base_url_from_config()
    client = OpenBBClient()
    required_methods = ("get_shipping_chokepoint_info", "get_shipping_chokepoint_volume")
    missing_methods = [method for method in required_methods if not hasattr(client, method)]
    if missing_methods:
        return {
            "enabled": False,
            "source_id": "openbb:shipping_context",
            "items": [],
            "errors": [],
            "note": f"disabled: adapter missing {', '.join(missing_methods)}",
        }
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    info_map: dict[str, dict[str, Any]] = {}
    try:
        info_rows = client.get_shipping_chokepoint_info(provider="imf")
        info_map = {str(item.get("name", "")).strip(): item for item in info_rows if isinstance(item, dict)}
    except (OpenBBAdapterError, RuntimeError, ValueError) as exc:
        errors.append(f"info: {exc}")
    request_start = run_dt.date() - timedelta(days=int(config["lookback_days"]))
    for spec in config["chokepoints"]:
        try:
            rows = client.get_shipping_chokepoint_volume(
                provider="imf",
                chokepoint=spec["id"],
                start_date=request_start,
                end_date=run_dt.date(),
            )
            frame = normalize_openbb_shipping_rows(rows)
            items.append(summarize_openbb_shipping_series(frame, spec, int(config["change_window_days"]), info_map))
        except (OpenBBAdapterError, RuntimeError, ValueError) as exc:
            errors.append(f"{spec['id']}: {exc}")
    latest_dates = sorted({str(item.get("last_date", "")).strip() for item in items if str(item.get("last_date", "")).strip()})
    note_parts: list[str] = []
    if latest_dates:
        note_parts.append(f"latest_dates={','.join(latest_dates)}")
    if errors:
        note_parts.append(f"errors={len(errors)}")
    return {
        "enabled": bool(items),
        "source_id": "openbb:shipping_context",
        "items": items,
        "errors": errors,
        "note": "; ".join(note_parts),
    }


def read_cached_history(cache_path: Path) -> pd.DataFrame | None:
    if not cache_path.exists():
        return None
    try:
        frame = pd.read_csv(cache_path)
        frame["日期"] = pd.to_datetime(frame["日期"])
        return frame
    except Exception:
        return None


def write_cached_history(cache_path: Path, frame: pd.DataFrame) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(cache_path, index=False)


def should_execute_scan(run_dt: datetime) -> tuple[bool, str]:
    polling = load_config_section(None, "polling")
    local_dt = current_market_local_dt(run_dt)
    market_phase = current_market_phase(run_dt)
    after_close_full_rebuild_time = str(polling.get("after_close_full_rebuild_time", "15:20"))
    if local_dt.strftime("%H:%M") == after_close_full_rebuild_time:
        return True, "after_close_rebuild"
    if market_phase in {"market_session_morning", "market_session_afternoon"}:
        return True, "market_tick"
    off_session_poll_minutes = int(polling.get("off_session_poll_minutes", 30))
    if off_session_poll_minutes > 0 and (local_dt.hour * 60 + local_dt.minute) % off_session_poll_minutes == 0:
        return True, "off_session_tick"
    return False, "skipped_off_session_base_tick"


def fetch_sw_valuation_frame() -> pd.DataFrame:
    frame = ak.sw_index_first_info().copy()
    frame["代码"] = frame["行业代码"].astype(str).str.replace(".SI", "", regex=False)
    return frame


def fetch_heat_board_frame(limit: int) -> pd.DataFrame:
    frame = ak.stock_board_industry_name_em().copy()
    return frame.reset_index(drop=True)


def fetch_industry_history(sw_symbol: str, lookback_days: int) -> pd.DataFrame:
    cache_day = datetime.now(DEFAULT_MARKET_TZ).strftime("%Y%m%d")
    cache_path = configured_cache_dir() / f"{sw_symbol}_{cache_day}.csv"
    cached = read_cached_history(cache_path)
    if cached is not None:
        frame = cached.copy()
    else:
        frame = ak.index_hist_sw(symbol=sw_symbol, period="day").copy()
        write_cached_history(cache_path, frame)
    frame["日期"] = pd.to_datetime(frame["日期"])
    frame = frame.sort_values("日期").tail(lookback_days).reset_index(drop=True)
    return frame


def build_market_feature_rows(registry: list[dict[str, str]], lookback_days: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = len(registry)
    for idx, item in enumerate(registry, start=1):
        sw_symbol = normalize_sw_symbol(item["sw_code"])
        if DEBUG_PROGRESS and (idx == 1 or idx % 5 == 0 or idx == total):
            debug_progress(f"market:history-fetch-start idx={idx}/{total} industry={item['display_name_cn']} symbol={sw_symbol}")
        history = fetch_industry_history(sw_symbol, lookback_days)
        if history.empty or len(history) < 21:
            if DEBUG_PROGRESS:
                debug_progress(f"market:history-fetch-skip idx={idx}/{total} industry={item['display_name_cn']} rows={len(history)}")
            continue
        latest = history.iloc[-1]
        prev = history.iloc[-2]
        five_back = history.iloc[-6] if len(history) >= 6 else history.iloc[0]
        twenty_back = history.iloc[-21] if len(history) >= 21 else history.iloc[0]
        prev_amount_mean = safe_float(history.iloc[:-1]["成交额"].tail(20).mean())
        latest_amount = safe_float(latest["成交额"])
        amount_ratio = latest_amount / prev_amount_mean if latest_amount and prev_amount_mean else None
        rows.append(
            {
                "industry_id": item["industry_id"],
                "display_name_cn": item["display_name_cn"],
                "sw_code": item["sw_code"],
                "latest_date": latest["日期"].date().isoformat(),
                "close": safe_float(latest["收盘"]),
                "one_day_return": ((safe_float(latest["收盘"]) / safe_float(prev["收盘"]) - 1) * 100) if safe_float(prev["收盘"]) else None,
                "five_day_return": ((safe_float(latest["收盘"]) / safe_float(five_back["收盘"]) - 1) * 100) if safe_float(five_back["收盘"]) else None,
                "twenty_day_return": ((safe_float(latest["收盘"]) / safe_float(twenty_back["收盘"]) - 1) * 100) if safe_float(twenty_back["收盘"]) else None,
                "latest_amount": latest_amount,
                "amount_ratio": amount_ratio,
            }
        )
        if DEBUG_PROGRESS and (idx == 1 or idx % 5 == 0 or idx == total):
            debug_progress(f"market:history-fetch-done idx={idx}/{total} industry={item['display_name_cn']}")
    return rows


def enrich_trading_proxy_context(
    rows: list[dict[str, Any]],
    stock_primary_map: dict[str, dict[str, str]],
    etf_primary_map: dict[str, dict[str, str]],
) -> None:
    for row in rows:
        stock_item = stock_primary_map.get(row["industry_id"], {})
        etf_item = etf_primary_map.get(row["industry_id"], {})
        row["representative_stock_code"] = str(stock_item.get("stock_code", ""))
        row["representative_stock_name"] = str(stock_item.get("stock_name", ""))
        row["representative_stock_score"] = safe_float(stock_item.get("representative_score"))
        row["representative_board_name"] = str(stock_item.get("board_name", ""))
        row["etf_proxy_code"] = str(etf_item.get("etf_code", ""))
        row["etf_proxy_name"] = str(etf_item.get("etf_name", ""))


def enrich_overlay_context(rows: list[dict[str, Any]]) -> None:
    overlay_context = build_industry_overlay_context(
        overlays=load_theme_chain_overlays(),
        members=load_theme_chain_overlay_members(),
    )
    for row in rows:
        overlays = overlay_context.get(row["industry_id"], [])
        row["overlay_context"] = overlays[:4]
        row["overlay_names"] = [
            str(item.get("display_name_cn", ""))
            for item in overlays[:3]
            if str(item.get("display_name_cn", "")).strip()
        ]


def enrich_market_structure_context(rows: list[dict[str, Any]], board_frame: pd.DataFrame) -> None:
    board_map = {str(item["板块名称"]): item for item in board_frame.to_dict("records")}
    total_amount = sum((safe_float(row.get("latest_amount")) or 0.0) for row in rows)
    for row in rows:
        board = board_map.get(str(row["display_name_cn"]), {})
        up_count = int(safe_float(board.get("上涨家数")) or 0)
        down_count = int(safe_float(board.get("下跌家数")) or 0)
        total_count = up_count + down_count
        row["industry_amount_share"] = (safe_float(row.get("latest_amount")) or 0.0) / total_amount if total_amount else None
        row["up_count"] = up_count
        row["down_count"] = down_count
        row["breadth_up_ratio"] = up_count / total_count if total_count else None
        row["leader_stock_name"] = str(board.get("领涨股票", ""))
        row["leader_stock_pct_change"] = safe_float(board.get("领涨股票-涨跌幅"))


def enrich_heat_scores(rows: list[dict[str, Any]], registry_map: dict[str, dict[str, str]], heat_frame: pd.DataFrame, limit: int) -> None:
    for row in rows:
        item = registry_map[row["industry_id"]]
        keywords = parse_heat_keywords(item.get("heat_keywords", ""))
        matched_boards: list[dict[str, Any]] = []
        score = 0.0
        for _, board in heat_frame.iterrows():
            board_name = str(board["板块名称"])
            if not any(keyword in board_name for keyword in keywords):
                continue
            rank_value = safe_float(board["排名"]) or float(limit)
            if rank_value > limit:
                continue
            pct_value = safe_float(board["涨跌幅"]) or 0.0
            contribution = max(0.0, (limit + 1 - rank_value) / limit) * 0.6 + max(0.0, min(pct_value, 10.0)) / 10.0 * 0.4
            score += contribution
            matched_boards.append(
                {
                    "board_name": board_name,
                    "rank": int(rank_value),
                    "pct_change": pct_value,
                    "leader": str(board.get("领涨股票", "")),
                }
            )
        row["heat_score"] = min(1.0, score / 2.5)
        row["matched_boards"] = matched_boards[:5]


def enrich_fundamental_scores(rows: list[dict[str, Any]], valuation_frame: pd.DataFrame) -> None:
    mapping = valuation_frame.rename(
        columns={
            "行业代码": "sw_code",
            "行业名称": "display_name_cn",
            "静态市盈率": "pe_static",
            "TTM(滚动)市盈率": "pe_ttm",
            "静态股息率": "dividend_yield",
            "市净率": "pb",
        }
    )
    metrics = mapping[["sw_code", "pe_ttm", "dividend_yield", "pb"]].copy()
    metrics["pe_ttm"] = pd.to_numeric(metrics["pe_ttm"], errors="coerce")
    metrics["dividend_yield"] = pd.to_numeric(metrics["dividend_yield"], errors="coerce")
    metrics["pb"] = pd.to_numeric(metrics["pb"], errors="coerce")
    metrics["pe_score"] = inverse_percentile_score(metrics["pe_ttm"].fillna(metrics["pe_ttm"].max()))
    metrics["dividend_score"] = percentile_score(metrics["dividend_yield"].fillna(metrics["dividend_yield"].min()))
    metrics["fundamental_score"] = (metrics["pe_score"] * 0.6 + metrics["dividend_score"] * 0.4).round(4)
    metric_map = {str(item["sw_code"]): item for item in metrics.to_dict("records")}

    for row in rows:
        item = metric_map.get(row["sw_code"], {})
        row["pe_ttm"] = safe_float(item.get("pe_ttm"))
        row["pb"] = safe_float(item.get("pb"))
        row["dividend_yield"] = safe_float(item.get("dividend_yield"))
        row["fundamental_score"] = safe_float(item.get("fundamental_score")) or 0.0


def enrich_money_flow_scores(rows: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(rows)
    weights = load_config_section(None, "scan").get("money_flow_weights", {})
    composite = load_config_section(None, "scan").get("composite_weights", {})
    one_day = percentile_score(pd.to_numeric(frame["one_day_return"], errors="coerce").fillna(-999))
    five_day = percentile_score(pd.to_numeric(frame["five_day_return"], errors="coerce").fillna(-999))
    twenty_day = percentile_score(pd.to_numeric(frame["twenty_day_return"], errors="coerce").fillna(-999))
    amount_ratio = percentile_score(pd.to_numeric(frame["amount_ratio"], errors="coerce").fillna(0))
    amount_share = percentile_score(pd.to_numeric(frame.get("industry_amount_share"), errors="coerce").fillna(0))
    breadth_up_ratio = percentile_score(pd.to_numeric(frame.get("breadth_up_ratio"), errors="coerce").fillna(0))
    leader_stock_pct_change = percentile_score(pd.to_numeric(frame.get("leader_stock_pct_change"), errors="coerce").fillna(-999))
    aux_flow_score = percentile_score(pd.to_numeric(frame.get("aux_flow_score"), errors="coerce").fillna(0))
    frame["money_flow_score"] = (
        one_day * float(weights.get("one_day_return", 0.2))
        + five_day * float(weights.get("five_day_return", 0.3))
        + twenty_day * float(weights.get("twenty_day_return", 0.3))
        + amount_ratio * float(weights.get("amount_ratio", 0.2))
        + amount_share * float(weights.get("amount_share", 0.0))
        + breadth_up_ratio * float(weights.get("breadth_up_ratio", 0.0))
        + leader_stock_pct_change * float(weights.get("leader_stock_pct_change", 0.0))
        + aux_flow_score * float(weights.get("aux_flow_score", 0.0))
    )
    frame["total_score"] = (
        frame["money_flow_score"] * float(composite.get("money_flow", 0.6))
        + pd.to_numeric(frame["heat_score"], errors="coerce").fillna(0) * float(composite.get("heat", 0.15))
        + pd.to_numeric(frame["fundamental_score"], errors="coerce").fillna(0) * float(composite.get("fundamental", 0.1))
        + pd.to_numeric(frame.get("policy_score", 0), errors="coerce").fillna(0) * float(composite.get("policy", 0.15))
    )
    frame["rank_desc"] = frame["total_score"].rank(method="first", ascending=False).astype(int)
    updated_rows = frame.to_dict("records")
    rows.clear()
    rows.extend(updated_rows)


def determine_state(row: dict[str, Any]) -> str:
    scan_cfg = load_config_section(None, "scan")
    total_score = safe_float(row.get("total_score")) or 0.0
    heat_score = safe_float(row.get("heat_score")) or 0.0
    candidate_threshold = float(scan_cfg.get("candidate_threshold", 0.74))
    strong_threshold = float(scan_cfg.get("strong_threshold", 0.84))
    heat_min_for_candidate = float(scan_cfg.get("heat_min_for_candidate", 0.18))
    heat_min_for_strong = float(scan_cfg.get("heat_min_for_strong", 0.32))
    if total_score >= strong_threshold and heat_score >= heat_min_for_strong:
        return "strong_alert"
    if total_score >= candidate_threshold and heat_score >= heat_min_for_candidate:
        return "candidate"
    if total_score >= 0.6:
        return "warming"
    return "cold"


def deployment_host_label() -> str:
    deployment = load_config_section(None, "deployment")
    return str(deployment.get("server_host", "remote-runner")).strip() or "remote-runner"


def connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_event_db(conn, DEFAULT_SCHEMA_PATH)
    return conn


def register_run(conn: sqlite3.Connection, run_id: str, run_at: str, run_label: str, note: str) -> None:
    conn.execute(
        """
        INSERT INTO radar_runs (run_id, run_label, mode, started_at, status, host, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            run_label=excluded.run_label,
            started_at=excluded.started_at,
            status=excluded.status,
            note=excluded.note
        """,
        (run_id, run_label, "scan", run_at, "running", deployment_host_label(), note),
    )


def complete_run(conn: sqlite3.Connection, run_id: str, run_at: str, status: str, note: str) -> None:
    conn.execute(
        """
        UPDATE radar_runs
        SET completed_at = ?, status = ?, note = ?
        WHERE run_id = ?
        """,
        (run_at, status, note, run_id),
    )


def insert_signal_snapshot(conn: sqlite3.Connection, run_id: str, run_at: str, row: dict[str, Any]) -> str:
    snapshot_id = f"{row['industry_id']}:{run_at}"
    evidence = {
        "one_day_return": row.get("one_day_return"),
        "five_day_return": row.get("five_day_return"),
        "twenty_day_return": row.get("twenty_day_return"),
        "amount_ratio": row.get("amount_ratio"),
        "matched_boards": row.get("matched_boards", []),
        "pe_ttm": row.get("pe_ttm"),
        "pb": row.get("pb"),
        "dividend_yield": row.get("dividend_yield"),
        "valuation_score": row.get("valuation_score"),
        "fundamental_proxy_score": row.get("fundamental_proxy_score"),
        "fundamental_proxy_evidence": row.get("fundamental_proxy_evidence", {}),
        "rank_desc": row.get("rank_desc"),
        "policy_articles": row.get("policy_articles", []),
        "announcement_events": row.get("announcement_events", []),
        "representative_stock_code": row.get("representative_stock_code"),
        "representative_stock_name": row.get("representative_stock_name"),
        "etf_proxy_code": row.get("etf_proxy_code"),
        "etf_proxy_name": row.get("etf_proxy_name"),
        "industry_amount_share": row.get("industry_amount_share"),
        "up_count": row.get("up_count"),
        "down_count": row.get("down_count"),
        "breadth_up_ratio": row.get("breadth_up_ratio"),
        "leader_stock_name": row.get("leader_stock_name"),
        "leader_stock_pct_change": row.get("leader_stock_pct_change"),
        "aux_flow_score": row.get("aux_flow_score"),
        "flow_signal_evidence": row.get("flow_signal_evidence", {}),
        "overlay_context": row.get("overlay_context", []),
    }
    source_ids = [
        "akshare:sw_index_first_info",
        "akshare:index_hist_sw",
        "akshare:stock_board_industry_name_em",
    ]
    if row.get("policy_articles"):
        source_ids.extend(
            sorted(
                {
                    str(item.get("source_id", "")).strip()
                    for item in row.get("policy_articles", [])
                    if str(item.get("source_id", "")).strip()
                }
            )
        )
    if row.get("announcement_events"):
        source_ids.append("akshare:stock_notice_report")
    if row.get("fundamental_proxy_evidence"):
        proxy_family = str(row.get("fundamental_proxy_evidence", {}).get("proxy_family", ""))
        if proxy_family == "hog_cycle":
            source_ids.extend(
                [
                    "akshare:index_hog_spot_price",
                    "akshare:spot_hog_lean_price_soozhu",
                    "akshare:spot_mixed_feed_soozhu",
                ]
            )
        if proxy_family == "shipping_cycle":
            source_ids.extend(
                [
                    "akshare:macro_shipping_bdi",
                    "akshare:macro_china_freight_index",
                ]
            )
        if proxy_family == "broker_cycle":
            source_ids.extend(
                [
                    "akshare:macro_china_market_margin_sh",
                    "akshare:macro_china_market_margin_sz",
                ]
            )
        if proxy_family == "utility_electricity":
            source_ids.append("akshare:macro_china_society_electricity")
    if row.get("flow_signal_evidence"):
        source_ids.extend(
            [
                "akshare:stock_sector_fund_flow_rank",
                "akshare:stock_fund_flow_industry",
                "akshare:fund_etf_spot_em",
                "akshare:stock_hsgt_hold_stock_em",
                "akshare:stock_margin_detail",
                "akshare:stock_lhb_detail_daily_sina",
            ]
        )
    conn.execute(
        """
        INSERT OR REPLACE INTO signal_snapshots (
            snapshot_id, run_id, snapshot_at, industry_id, industry_label, industry_state,
            total_score, money_flow_score, news_score, fundamental_score, policy_score,
            signal_summary_json, evidence_json, source_ids_json, raw_snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            run_id,
            run_at,
            row["industry_id"],
            row["display_name_cn"],
            row["industry_state"],
            safe_float(row.get("total_score")),
            safe_float(row.get("money_flow_score")),
            safe_float(row.get("heat_score")),
            safe_float(row.get("fundamental_score")),
            safe_float(row.get("policy_score")),
            json.dumps(
                {
                    "state": row["industry_state"],
                    "rank": row.get("rank_desc"),
                    "close": row.get("close"),
                },
                ensure_ascii=False,
            ),
            json.dumps(evidence, ensure_ascii=False),
            json.dumps(source_ids, ensure_ascii=False),
            json.dumps(row, ensure_ascii=False),
        ),
    )
    return snapshot_id


def recent_sent_alert_exists(conn: sqlite3.Connection, dedup_key: str, run_dt: datetime) -> bool:
    dedup_minutes = int(load_config_section(None, "alerting").get("default_dedup_window_minutes", 180))
    cutoff = (run_dt - timedelta(minutes=dedup_minutes)).isoformat(timespec="seconds")
    row = conn.execute(
        """
        SELECT alert_id
        FROM alert_events
        WHERE dedup_key = ? AND created_at >= ? AND state IN ('sent', 'partial_success')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (dedup_key, cutoff),
    ).fetchone()
    return row is not None


def latest_alert_level_for_industry(conn: sqlite3.Connection, industry_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT alert_level
        FROM alert_events
        WHERE industry_id = ? AND state IN ('sent', 'partial_success', 'pending', 'bark_candidate')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (industry_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["alert_level"] or "").strip() or None


def build_alert_title(row: dict[str, Any]) -> str:
    level = "强提醒" if row["industry_state"] == "strong_alert" else "候选"
    return f"{level} | {row['display_name_cn']}"


def build_proxy_metric_text(proxy_evidence: dict[str, Any]) -> str:
    if not proxy_evidence:
        return "无关键代理指标"
    family = str(proxy_evidence.get("proxy_family", ""))
    if family == "hog_cycle":
        hog_5d = percent_text(safe_float(proxy_evidence.get("hog_price_5d_return")))
        ratio = safe_float(proxy_evidence.get("hog_feed_ratio_latest"))
        ratio_text_value = "N/A" if ratio is None else f"{ratio:.2f}"
        index_above_ma = "是" if proxy_evidence.get("hog_index_above_6m_ma") else "否"
        return f"猪价5日 {hog_5d} | 猪粮比 {ratio_text_value} | 指数站上6月均线 {index_above_ma}"
    if family == "shipping_cycle":
        bdi_3m = percent_text(safe_float(proxy_evidence.get("bdi_three_month_return")))
        freight_bdi = safe_float(proxy_evidence.get("freight_bdi_latest"))
        freight_bcti = safe_float(proxy_evidence.get("freight_bcti_latest"))
        freight_bdi_text = "N/A" if freight_bdi is None else f"{freight_bdi:.0f}"
        freight_bcti_text = "N/A" if freight_bcti is None else f"{freight_bcti:.0f}"
        return f"BDI近3月 {bdi_3m} | 综合运价 {freight_bdi_text} | 成品油运价 {freight_bcti_text}"
    if family == "broker_cycle":
        balance_20d = percent_text(safe_float(proxy_evidence.get("market_total_balance_20d_return")))
        margin_buy = safe_float(proxy_evidence.get("market_margin_buy_latest"))
        margin_buy_text = "N/A" if margin_buy is None else f"{margin_buy / 1e8:.2f}亿"
        return f"两融余额20日 {balance_20d} | 融资买入额 {margin_buy_text}"
    if family == "utility_electricity":
        total_yoy = percent_text(safe_float(proxy_evidence.get("society_electricity_yoy")))
        industry2_yoy = percent_text(safe_float(proxy_evidence.get("industry2_electricity_yoy")))
        total_3m = percent_text(safe_float(proxy_evidence.get("society_electricity_3m_yoy_mean")))
        return f"全社会用电同比 {total_yoy} | 第二产业用电同比 {industry2_yoy} | 3月均值 {total_3m}"
    return "无关键代理指标"


def build_policy_text(policy_articles: list[dict[str, Any]]) -> str:
    if not policy_articles:
        return "无政策/舆情催化"
    snippets: list[str] = []
    for item in policy_articles[:2]:
        title = str(item.get("title", "")).strip()
        source_id = str(item.get("source_id", "")).strip()
        date_text = str(item.get("date", "")).strip()
        source_count = int(safe_float(item.get("source_count")) or 0)
        if not title:
            continue
        source_text = source_id
        if source_count >= 2:
            source_text = f"{source_id}+{source_count - 1}源"
        suffix = " / ".join(part for part in [source_text, date_text] if part)
        snippets.append(f"{title} ({suffix})" if suffix else title)
    return "；".join(snippets) if snippets else "无政策/舆情催化"


def build_market_context_text(openbb_context: dict[str, Any] | None) -> str:
    if not openbb_context:
        return ""
    market_context = openbb_context.get("market", {}) if isinstance(openbb_context, dict) else {}
    items = market_context.get("items", [])
    if not isinstance(items, list) or not items:
        return ""
    snippets: list[str] = []
    for item in items[:3]:
        label = str(item.get("label") or item.get("name") or item.get("symbol") or "").strip()
        if not label:
            continue
        daily_move = safe_float(item.get("one_day_return"))
        last_date = str(item.get("last_date", "")).strip()
        snippet = f"{label} {percent_text(daily_move)}" if daily_move is not None else label
        if last_date:
            snippet = f"{snippet} ({last_date})"
        snippets.append(snippet)
    return " | ".join(snippets)


def build_macro_context_text(openbb_context: dict[str, Any] | None) -> str:
    if not openbb_context:
        return ""
    macro_context = openbb_context.get("macro", {}) if isinstance(openbb_context, dict) else {}
    items = macro_context.get("items", [])
    if not isinstance(items, list) or not items:
        return ""
    snippets: list[str] = []
    for item in items[:3]:
        label = str(item.get("label", "")).strip()
        if not label:
            continue
        transform = str(item.get("transform", ""))
        summary_value = safe_float(item.get("summary_value"))
        latest_value = safe_float(item.get("latest_value"))
        if transform == "delta_bps" and summary_value is not None:
            sign = "+" if summary_value >= 0 else ""
            snippets.append(f"{label} {sign}{summary_value:.0f}bp")
        elif summary_value is not None:
            snippets.append(f"{label} {percent_text(summary_value)}")
        elif latest_value is not None:
            snippets.append(f"{label} {latest_value:.2f}")
    return " | ".join(snippets)


def build_shipping_context_text(openbb_context: dict[str, Any] | None) -> str:
    if not openbb_context:
        return ""
    shipping_context = openbb_context.get("shipping", {}) if isinstance(openbb_context, dict) else {}
    items = shipping_context.get("items", [])
    if not isinstance(items, list) or not items:
        return ""
    snippets: list[str] = []
    for item in items[:2]:
        label = str(item.get("label", "")).strip()
        if not label:
            continue
        capacity_change = safe_float(item.get("capacity_change_pct"))
        vessels_change = safe_float(item.get("vessels_change_pct"))
        if capacity_change is not None:
            snippets.append(f"{label}运力7日 {percent_text(capacity_change)}")
        elif vessels_change is not None:
            snippets.append(f"{label}船次7日 {percent_text(vessels_change)}")
    return " | ".join(snippets)


def alert_body_mode() -> str:
    override = str(os.environ.get("RADAR_ALERT_BODY_MODE", "")).strip().lower()
    if override in {"brief", "full"}:
        return override
    alerting = load_config_section(None, "alerting")
    bark = alerting.get("bark", {}) if isinstance(alerting.get("bark", {}), dict) else {}
    return str(bark.get("body_mode", "brief")).strip().lower()


def truncate_text(text: str, limit: int = 36) -> str:
    cleaned = " ".join(str(text).strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(0, limit - 1)]}…"


def build_trigger_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    amount_ratio = safe_float(row.get("amount_ratio")) or 0.0
    breadth_ratio = safe_float(row.get("breadth_up_ratio")) or 0.0
    heat_score = safe_float(row.get("heat_score")) or 0.0
    money_flow_score = safe_float(row.get("money_flow_score")) or 0.0
    flow_evidence = row.get("flow_signal_evidence", {})

    if money_flow_score >= 0.75 or amount_ratio >= 1.4 or breadth_ratio >= 0.65:
        reasons.append("资金走强")
    if heat_score >= 0.3 or row.get("matched_boards"):
        reasons.append("热度升温")
    if row.get("policy_articles"):
        reasons.append("政策/舆情催化")
    if row.get("announcement_events"):
        reasons.append("公告催化")
    if row.get("fundamental_proxy_evidence"):
        reasons.append("行业代理改善")
    if (
        safe_float(flow_evidence.get("etf_share_change_pct")) is not None
        or safe_float(flow_evidence.get("margin_buy_amount")) is not None
        or flow_evidence.get("lhb_hit")
    ):
        reasons.append("显性资金痕迹")

    deduped: list[str] = []
    for item in reasons:
        if item not in deduped:
            deduped.append(item)
    return deduped[:3]


def build_brief_evidence_line(row: dict[str, Any]) -> str:
    evidence_parts: list[str] = []
    five_day_return = safe_float(row.get("five_day_return"))
    amount_ratio = safe_float(row.get("amount_ratio"))
    breadth_ratio = safe_float(row.get("breadth_up_ratio"))
    leader_pct = safe_float(row.get("leader_stock_pct_change"))

    if five_day_return is not None:
        evidence_parts.append(f"5日 {percent_text(five_day_return)}")
    if amount_ratio is not None:
        evidence_parts.append(f"放量 {ratio_text(amount_ratio)}")
    if breadth_ratio is not None:
        evidence_parts.append(f"上涨占比 {(breadth_ratio * 100):.1f}%")
    elif leader_pct is not None:
        evidence_parts.append(f"龙头 {percent_text(leader_pct)}")

    return " | ".join(evidence_parts[:3]) if evidence_parts else "暂无高置信交易证据"


def build_brief_catalyst_line(row: dict[str, Any]) -> str:
    announcement_events = row.get("announcement_events", [])
    if announcement_events:
        item = announcement_events[0]
        stock_name = str(item.get("stock_name", "")).strip()
        tag = "/".join(item.get("signal_tags", [])[:2]) or str(item.get("notice_type", "")).strip()
        catalyst = f"{stock_name}:{tag}".strip(":")
        return f"催化：{truncate_text(catalyst, 40)}"

    policy_articles = row.get("policy_articles", [])
    if policy_articles:
        title = str(policy_articles[0].get("title", "")).strip()
        if title:
            source_count = int(safe_float(policy_articles[0].get("source_count")) or 0)
            prefix = "多源:" if source_count >= 2 else ""
            return f"催化：{truncate_text(prefix + title, 40)}"

    proxy_evidence = row.get("fundamental_proxy_evidence", {})
    if proxy_evidence:
        return f"催化：{truncate_text(build_proxy_metric_text(proxy_evidence), 40)}"

    matched = row.get("matched_boards", [])
    if matched:
        board_name = str(matched[0].get("board_name", "")).strip()
        if board_name:
            return f"催化：热板匹配 {truncate_text(board_name, 24)}"

    return ""


def build_brief_alert_body(row: dict[str, Any], openbb_context: dict[str, Any] | None = None) -> str:
    reasons = "；".join(build_trigger_reasons(row)) or "信号共振"
    lines = [
        f"行业：{row['display_name_cn']}",
        f"触发：{reasons}",
        f"证据：{build_brief_evidence_line(row)}",
    ]
    catalyst_line = build_brief_catalyst_line(row)
    if catalyst_line:
        lines.append(catalyst_line)
    return "\n".join(lines)


def build_full_alert_body(row: dict[str, Any], openbb_context: dict[str, Any] | None = None) -> str:
    matched = row.get("matched_boards", [])
    matched_text = "、".join(item["board_name"] for item in matched[:3]) if matched else "无明显热板匹配"
    proxy_evidence = row.get("fundamental_proxy_evidence", {})
    proxy_summary = str(proxy_evidence.get("summary_cn", "无行业代理")) if proxy_evidence else "无行业代理"
    proxy_metric_text = build_proxy_metric_text(proxy_evidence)
    policy_text = build_policy_text(row.get("policy_articles", []))
    announcement_events = row.get("announcement_events", [])
    announcement_text = "无公告硬信息"
    if announcement_events:
        announcement_text = "；".join(
            f"{item.get('stock_name', '')}:{'/'.join(item.get('signal_tags', [])[:2]) or item.get('notice_type', '')}"
            for item in announcement_events[:2]
        )
    flow_evidence = row.get("flow_signal_evidence", {})
    representative_stock_name = str(row.get("representative_stock_name", "")).strip()
    representative_stock_code = str(row.get("representative_stock_code", "")).strip()
    representative_text = "无代表股"
    if representative_stock_name or representative_stock_code:
        representative_text = f"{representative_stock_name} {representative_stock_code}".strip()
    etf_proxy_name = str(row.get("etf_proxy_name", "")).strip()
    etf_proxy_code = str(row.get("etf_proxy_code", "")).strip()
    etf_text = "无 ETF 代理"
    if etf_proxy_name or etf_proxy_code:
        etf_text = f"{etf_proxy_name} {etf_proxy_code}".strip()
    breadth_ratio = safe_float(row.get("breadth_up_ratio"))
    breadth_text = f"{(breadth_ratio * 100):.1f}%" if breadth_ratio is not None else "N/A"
    amount_share = safe_float(row.get("industry_amount_share"))
    amount_share_text = f"{(amount_share * 100):.1f}%" if amount_share is not None else "N/A"
    leader_text = str(row.get("leader_stock_name", "")).strip() or "无龙头牵引"
    leader_pct = safe_float(row.get("leader_stock_pct_change"))
    if leader_pct is not None:
        leader_text = f"{leader_text} {percent_text(leader_pct)}"
    sector_net = safe_float(flow_evidence.get("sector_main_net_inflow"))
    sector_flow_text = "N/A" if sector_net is None else f"{sector_net / 1e8:.2f}亿"
    northbound_text = "无北向显性痕迹"
    if flow_evidence:
        if flow_evidence.get("northbound_stale"):
            northbound_text = "北向公开榜单口径已过期，已降权"
        elif safe_float(flow_evidence.get("northbound_delta_value")):
            northbound_text = f"代表股北向增持估计 {safe_float(flow_evidence.get('northbound_delta_value')) / 1e4:.1f}万"
    margin_text = "无两融痕迹"
    margin_buy = safe_float(flow_evidence.get("margin_buy_amount"))
    if margin_buy is not None:
        margin_text = f"融资买入额 {margin_buy / 1e8:.2f}亿"
    etf_flow_text = "无 ETF 资金痕迹"
    etf_turnover = safe_float(flow_evidence.get("etf_proxy_turnover_amount"))
    etf_share_change = safe_float(flow_evidence.get("etf_share_change_pct"))
    etf_discount = safe_float(flow_evidence.get("etf_discount_rate"))
    etf_flow_parts: list[str] = []
    if etf_turnover is not None:
        etf_flow_parts.append(f"成交额 {etf_turnover / 1e8:.2f}亿")
    if etf_share_change is not None:
        etf_flow_parts.append(f"份额变化 {etf_share_change:.2f}%")
    if etf_discount is not None:
        etf_flow_parts.append(f"折价率 {etf_discount:.2f}%")
    if etf_flow_parts:
        etf_flow_text = " / ".join(etf_flow_parts)
    lhb_text = "否"
    if flow_evidence.get("lhb_hit"):
        lhb_text = "是"
    overlay_context = row.get("overlay_context", [])
    overlay_text = "无主题/产业链映射"
    if overlay_context:
        overlay_text = "、".join(
            f"{item.get('display_name_cn', '')}({'主' if str(item.get('role', '')) == 'primary' else '辅'})"
            for item in overlay_context[:3]
        )
    lines = [
        f"行业：{row['display_name_cn']}",
        f"主题/产业链：{overlay_text}",
        f"总分：{(safe_float(row.get('total_score')) or 0):.2f} | 资金：{(safe_float(row.get('money_flow_score')) or 0):.2f} | 热度：{(safe_float(row.get('heat_score')) or 0):.2f} | 基本面上下文：{(safe_float(row.get('fundamental_score')) or 0):.2f}",
        f"1日 {percent_text(safe_float(row.get('one_day_return')))} | 5日 {percent_text(safe_float(row.get('five_day_return')))} | 20日 {percent_text(safe_float(row.get('twenty_day_return')))}",
        f"成交额放大：{ratio_text(safe_float(row.get('amount_ratio')))} | 成交占比：{amount_share_text} | 内部上涨占比：{breadth_text}",
        f"龙头牵引：{leader_text} | TTM PE：{safe_float(row.get('pe_ttm')) or 'N/A'} | 股息率：{safe_float(row.get('dividend_yield')) or 'N/A'}",
        f"行业资金净额：{sector_flow_text} | ETF 资金代理：{etf_flow_text}",
        f"北向：{northbound_text} | 两融：{margin_text} | 龙虎榜：{lhb_text}",
        f"代表股：{representative_text} | ETF：{etf_text}",
        f"行业代理：{proxy_summary}",
        f"代理细节：{proxy_metric_text}",
        f"政策/舆情：{policy_text}",
        f"公告硬信息：{announcement_text}",
        f"热板匹配：{matched_text}",
    ]
    market_context_text = build_market_context_text(openbb_context)
    if market_context_text:
        lines.append(f"外围环境：{market_context_text}")
    macro_context_text = build_macro_context_text(openbb_context)
    if macro_context_text:
        lines.append(f"宏观环境：{macro_context_text}")
    shipping_context_text = build_shipping_context_text(openbb_context)
    if shipping_context_text:
        lines.append(f"航运扰动：{shipping_context_text}")
    return "\n".join(lines)


def build_alert_body(row: dict[str, Any], openbb_context: dict[str, Any] | None = None) -> str:
    if alert_body_mode() == "full":
        return build_full_alert_body(row, openbb_context)
    return build_brief_alert_body(row, openbb_context)


def insert_alert_event(
    conn: sqlite3.Connection,
    run_id: str,
    run_at: str,
    snapshot_id: str,
    row: dict[str, Any],
    openbb_context: dict[str, Any] | None = None,
) -> str:
    alert_id = f"{row['industry_id']}:{row['industry_state']}:{run_at}"
    dedup_key = f"{row['industry_id']}:{row['industry_state']}"
    payload_preview = {
        "title": build_alert_title(row),
        "body": build_alert_body(row, openbb_context),
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO alert_events (
            alert_id, run_id, snapshot_id, industry_id, industry_label, alert_level,
            dedup_key, state, total_score, title, body, bark_group, bark_payload_json,
            evidence_json, source_ids_json, suppress_until, escalated_from, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            run_id,
            snapshot_id,
            row["industry_id"],
            row["display_name_cn"],
            row["industry_state"],
            dedup_key,
            "pending",
            safe_float(row.get("total_score")),
            payload_preview["title"],
            payload_preview["body"],
            build_bark_config().group,
            json.dumps(payload_preview, ensure_ascii=False),
            json.dumps(
                {
                    "matched_boards": row.get("matched_boards", []),
                    "rank_desc": row.get("rank_desc"),
                },
                ensure_ascii=False,
            ),
            json.dumps(
                [
                    "akshare:sw_index_first_info",
                    "akshare:index_hist_sw",
                    "akshare:stock_board_industry_name_em",
                ],
                ensure_ascii=False,
            ),
            None,
            None,
            run_at,
        ),
    )
    return alert_id


def insert_snapshot_alert_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    run_at: str,
    snapshot_object: dict[str, Any],
    bark_trigger: dict[str, Any],
) -> str:
    alert_id = f"{snapshot_object['radar_object_id']}:{snapshot_object['alert_level']}:{run_at}"
    conn.execute(
        """
        INSERT OR REPLACE INTO alert_events (
            alert_id, run_id, snapshot_id, industry_id, industry_label, alert_level,
            dedup_key, state, total_score, title, body, bark_group, bark_payload_json,
            evidence_json, source_ids_json, suppress_until, escalated_from, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            run_id,
            f"{snapshot_object['radar_object_id']}:{run_at}",
            snapshot_object["radar_object_id"],
            snapshot_object["radar_object_name"],
            snapshot_object["alert_level"],
            snapshot_object["dedup_key"],
            "pending",
            float(snapshot_object.get("radar_score") or 0.0),
            bark_trigger["title"],
            bark_trigger["body"],
            build_bark_config().group,
            json.dumps(bark_trigger, ensure_ascii=False),
            json.dumps(
                {
                    "radar_bucket": snapshot_object.get("radar_bucket"),
                    "runtime_state": snapshot_object.get("runtime_state"),
                    "key_evidence": snapshot_object.get("key_evidence", []),
                },
                ensure_ascii=False,
            ),
            json.dumps([event.get("source", "") for event in snapshot_object.get("supporting_events", [])], ensure_ascii=False),
            None,
            snapshot_object.get("previous_bucket"),
            run_at,
        ),
    )
    return alert_id


def build_live_status_context(conn: sqlite3.Connection, row: dict[str, Any], run_dt: datetime) -> dict[str, Any]:
    industry_id = str(row.get("industry_id") or "").strip()
    current_state = str(row.get("industry_state") or "").strip()
    previous_level = latest_alert_level_for_industry(conn, industry_id)
    same_level_sent_recently = recent_sent_alert_exists(conn, f"{industry_id}:{current_state}", run_dt)
    previous_bucket = (
        "strong_alert"
        if previous_level == "strong_alert"
        else "strong_candidate"
        if previous_level == "candidate"
        else "research_candidate"
        if previous_level == "watch"
        else "observe"
    )
    is_upgraded = current_state == "strong_alert" and previous_level == "candidate" and not same_level_sent_recently
    is_new = current_state == "strong_alert" and previous_level is None and not same_level_sent_recently
    trigger_state = "suppressed" if same_level_sent_recently else "bark_candidate" if current_state == "strong_alert" else "report_only"
    return {
        "is_new": is_new,
        "is_upgraded": is_upgraded,
        "previous_bucket": previous_bucket,
        "trigger_state": trigger_state,
    }


def insert_dispatch_attempt(conn: sqlite3.Connection, alert_id: str, run_at: str, result: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO dispatch_attempts (
            alert_id, channel, attempted_at, status, target_count, success_count, failure_count,
            response_summary_json, error_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            "bark",
            run_at,
            str(result.get("status", "")),
            int(result.get("target_count", 0)),
            int(result.get("success_count", 0)),
            int(result.get("failure_count", 0)),
            json.dumps(result, ensure_ascii=False),
            "; ".join(result.get("failures", [])),
        ),
    )


def update_alert_state(conn: sqlite3.Connection, alert_id: str, state: str, run_at: str) -> None:
    conn.execute(
        """
        UPDATE alert_events
        SET state = ?, sent_at = CASE WHEN ? IN ('sent', 'partial_success') THEN ? ELSE sent_at END
        WHERE alert_id = ?
        """,
        (state, state, run_at, alert_id),
    )


def process_alerts(
    conn: sqlite3.Connection,
    run_dt: datetime,
    rows: list[dict[str, Any]],
    output_payload: dict[str, Any],
    skip_bark: bool,
    openbb_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    bark_config = build_bark_config()
    max_alerts = int(load_config_section(None, "scan").get("max_alerts_per_run", 6))
    alerts: list[dict[str, Any]] = []
    run_at = run_dt.isoformat(timespec="seconds")

    for row in rows:
        if len(alerts) >= max_alerts:
            break
        status_context = build_live_status_context(conn, row, run_dt)
        snapshot_object = build_snapshot_object(row, set(), status_context=status_context)
        if not should_trigger_bark(snapshot_object):
            continue
        bark_trigger = build_trigger(snapshot_object)
        alert_id = insert_snapshot_alert_event(
            conn,
            run_id=output_payload["run_id"],
            run_at=run_at,
            snapshot_object=snapshot_object,
            bark_trigger=bark_trigger,
        )
        if skip_bark:
            result = {
                "sent": False,
                "status": "skipped_by_flag",
                "target_count": len(bark_config.device_keys),
                "success_count": 0,
                "failure_count": 0,
                "failures": [],
            }
        else:
            result = send_bark_payload(
                build_bark_payload(
                    title=bark_trigger["title"],
                    body=bark_trigger["body"],
                    bark_config=bark_config,
                ),
                bark_config,
            )
            result["target_count"] = len(bark_config.device_keys)
        insert_dispatch_attempt(conn, alert_id, run_at, result)
        update_alert_state(conn, alert_id, str(result.get("status", "pending")), run_at)
        alerts.append(
            {
                "alert_id": alert_id,
                "industry_id": snapshot_object["radar_object_id"],
                "display_name_cn": snapshot_object["radar_object_name"],
                "industry_state": snapshot_object["runtime_state"],
                "radar_bucket": snapshot_object["radar_bucket"],
                "total_score": snapshot_object["radar_score"],
                "dispatch_status": result.get("status"),
            }
        )
    return alerts


def write_radar_outputs(runtime_paths: dict[str, Path], run_dt: datetime, payload: dict[str, Any], candidate_pool_payload: dict[str, Any]) -> None:
    output_dir = runtime_paths["output_dir"]
    snapshot_dir = output_dir / "snapshots"
    inventory_dir = output_dir / "inventory"
    handoff_dir = output_dir / "handoffs"
    report_dir = output_dir / "reports"
    snapshot = build_snapshot(payload)
    previous_inventory = load_optional_json(inventory_dir / "radar_catalyst_inventory_latest.json")
    inventory = build_inventory(snapshot, previous_inventory)
    rules = load_rules(ROOT / "config" / "radar_report_rules_v1.json")
    bark_summary = build_bark_summary(snapshot)
    handoff_payload = build_handoff_payload(snapshot, inventory)
    handoff_text = render_handoff(snapshot, inventory)
    report_text = render_report(
        snapshot,
        rules=rules,
        inventory=inventory,
        handoff=handoff_payload,
        kimi_editorial={},
        kimi_research={},
        price_freshness={},
        news_verification={},
        ipo_watchlist={},
        hk_ipo_watchlist={},
        structural_signal={},
    )
    write_json(snapshot_dir / "radar_candidate_pool_latest.json", candidate_pool_payload)
    write_json(snapshot_dir / f"radar_candidate_pool_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.json", candidate_pool_payload)
    write_json(snapshot_dir / "radar_opportunity_snapshot_latest.json", snapshot)
    write_json(snapshot_dir / f"radar_opportunity_snapshot_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.json", snapshot)
    write_json(snapshot_dir / "radar_bark_summary_latest.json", bark_summary)
    write_json(snapshot_dir / f"radar_bark_summary_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.json", bark_summary)
    inventory_dir.mkdir(parents=True, exist_ok=True)
    write_json(inventory_dir / "radar_catalyst_inventory_latest.json", inventory)
    write_json(inventory_dir / f"radar_catalyst_inventory_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.json", inventory)
    handoff_dir.mkdir(parents=True, exist_ok=True)
    write_json(handoff_dir / "radar_research_handoff_latest.json", handoff_payload)
    write_json(handoff_dir / f"radar_research_handoff_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.json", handoff_payload)
    (handoff_dir / "radar_research_handoff_latest.md").write_text(handoff_text, encoding="utf-8")
    (handoff_dir / f"radar_research_handoff_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.md").write_text(handoff_text, encoding="utf-8")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "radar_intraday_scan_report_latest.md").write_text(report_text, encoding="utf-8")
    (report_dir / f"radar_intraday_scan_report_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.md").write_text(report_text, encoding="utf-8")


def write_scan_outputs(runtime_paths: dict[str, Path], payload: dict[str, Any], run_dt: datetime) -> None:
    latest_path = runtime_paths["output_dir"] / "industry_signal_scan_latest.json"
    dated_path = runtime_paths["output_dir"] / f"industry_signal_scan_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(latest_path, payload)
    write_json(dated_path, payload)


def build_output_payload(
    run_id: str,
    run_dt: datetime,
    market_phase: str,
    rows: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    execution_mode: str,
    openbb_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    top_rows = sorted(rows, key=lambda item: safe_float(item.get("total_score")) or 0.0, reverse=True)
    return {
        "run_id": run_id,
        "run_at": run_dt.isoformat(timespec="seconds"),
        "generated_at": utc_now_iso(),
        "market_phase": market_phase,
        "execution_mode": execution_mode,
        "industry_count": len(rows),
        "alert_count": len(alerts),
        "openbb_market_context": (openbb_context or {}).get("market", {"enabled": False, "items": [], "errors": []}),
        "openbb_fred_context": (openbb_context or {}).get("macro", {"enabled": False, "items": [], "errors": []}),
        "openbb_shipping_context": (openbb_context or {}).get("shipping", {"enabled": False, "items": [], "errors": []}),
        "all_industries": top_rows,
        "top_industries": top_rows[:10],
        "alerts": alerts,
    }


def run_scan(args: argparse.Namespace) -> dict[str, Any]:
    run_dt = parse_run_dt(args.run_at)
    run_at = run_dt.isoformat(timespec="seconds")
    debug_progress(f"run_scan:start run_at={run_at}")
    runtime_paths = resolve_runtime_paths(args.runtime_root_override)
    guardrails = validate_runtime_guardrails(runtime_paths)
    source_manifest = load_source_manifest()
    if guardrails["status"] != "pass":
        return build_bootstrap_summary(run_at, args.check_only, runtime_paths, guardrails, source_manifest, [])
    if args.check_only:
        return build_bootstrap_summary(run_at, args.check_only, runtime_paths, guardrails, source_manifest, [])

    materialize_runtime_dirs(runtime_paths)
    initialize_event_db(runtime_paths["event_db_path"], DEFAULT_SCHEMA_PATH, run_at)
    conn = connect_db(runtime_paths["event_db_path"])
    market_phase = current_market_phase(run_dt)
    run_id = f"scan:{run_at}"
    register_run(conn, run_id, run_at, market_phase, "industry radar scan started")
    should_run, execution_mode = should_execute_scan(run_dt)
    if not should_run:
        skip_note = f"scan skipped by business schedule: {execution_mode}"
        complete_run(conn, run_id, run_at, "skipped_schedule", skip_note)
        record_runtime_health(conn, run_id, run_at, "scan_runner", "skip", 0.0, skip_note)
        conn.commit()
        conn.close()
        return {
            "run_id": run_id,
            "run_at": run_at,
            "generated_at": utc_now_iso(),
            "market_phase": market_phase,
            "execution_mode": execution_mode,
            "status": "skipped_schedule",
            "industry_count": 0,
            "alert_count": 0,
            "alerts": [],
        }

    registry = load_industry_registry()
    debug_progress("registry:loaded")
    registry_map = {item["industry_id"]: item for item in registry}
    stock_primary_map = {item["industry_id"]: item for item in load_industry_stock_primary()}
    etf_primary_map = {item["industry_id"]: item for item in load_industry_etf_primary()}
    debug_progress("registry:proxy-maps-loaded")
    valuation_frame = fetch_sw_valuation_frame()
    debug_progress("market:valuation-frame-loaded")
    heat_frame = fetch_heat_board_frame(limit=int(load_config_section(None, "scan").get("heat_board_limit", 120)))
    debug_progress("market:heat-frame-loaded")
    rows = build_market_feature_rows(registry, lookback_days=int(load_config_section(None, "scan").get("history_lookback_days", 40)))
    debug_progress(f"market:feature-rows-built count={len(rows)}")
    enrich_trading_proxy_context(rows, stock_primary_map, etf_primary_map)
    enrich_overlay_context(rows)
    enrich_market_structure_context(rows, heat_frame)
    enrich_heat_scores(rows, registry_map, heat_frame, limit=int(load_config_section(None, "scan").get("heat_board_limit", 120)))
    enrich_fundamental_scores(rows, valuation_frame)
    debug_progress("market:enrichment-complete")
    news_overlay, news_source_health = build_shared_news_policy_overlay(registry, run_dt)
    debug_progress("overlay:news-policy-complete")
    announcement_overlay: dict[str, dict[str, Any]] = {item["industry_id"]: {"announcement_score": 0.0, "announcement_events": []} for item in registry}
    announcement_meta = {"source_id": "stock_notice_report", "used_date": "", "fetched_count": 0, "matched_count": 0}
    announcement_source_health: list[dict[str, Any]] = []
    if execution_mode in {"off_session_tick", "after_close_rebuild"}:
        announcement_overlay, announcement_meta, announcement_source_health = build_announcement_overlay(registry, run_dt)
        debug_progress("overlay:announcements-complete")
    flow_overlay, flow_source_health = build_flow_signal_overlay(registry, run_dt)
    debug_progress("overlay:flow-complete")
    fundamental_overlay, fundamental_source_health = build_fundamental_proxy_overlay(registry, run_dt)
    debug_progress("overlay:fundamental-proxy-complete")
    fundamental_blend = load_config_section(None, "scan").get("fundamental_blend", {})
    valuation_weight = float(fundamental_blend.get("valuation_weight", 0.4))
    proxy_weight = float(fundamental_blend.get("proxy_weight", 0.6))
    for row in rows:
        overlay = news_overlay.get(row["industry_id"], {})
        row["policy_score"] = safe_float(overlay.get("policy_score")) or 0.0
        row["policy_articles"] = overlay.get("policy_articles", [])
        announcement_item = announcement_overlay.get(row["industry_id"], {})
        row["announcement_score"] = safe_float(announcement_item.get("announcement_score")) or 0.0
        row["announcement_events"] = announcement_item.get("announcement_events", [])
        row["policy_score"] = min(1.0, row["policy_score"] + row["announcement_score"] * 0.8)
        row["valuation_score"] = safe_float(row.get("fundamental_score")) or 0.0
        proxy_overlay = fundamental_overlay.get(row["industry_id"], {})
        row["fundamental_proxy_score"] = safe_float(proxy_overlay.get("fundamental_proxy_score")) or 0.0
        row["fundamental_proxy_evidence"] = proxy_overlay.get("fundamental_proxy_evidence", {})
        if row["fundamental_proxy_evidence"]:
            row["fundamental_score"] = round(
                row["valuation_score"] * valuation_weight + row["fundamental_proxy_score"] * proxy_weight,
                4,
            )
        flow_item = flow_overlay.get(row["industry_id"], {})
        row["aux_flow_score"] = safe_float(flow_item.get("aux_flow_score")) or 0.0
        row["flow_signal_evidence"] = flow_item.get("flow_signal_evidence", {})
    enrich_money_flow_scores(rows)
    debug_progress("scoring:money-flow-complete")
    for item in news_source_health:
        record_source_health(
            conn,
            run_id=run_id,
            recorded_at=run_at,
            source_id=str(item.get("source_id", "akshare:news_cctv")),
            status=str(item.get("status", "pass")),
            lag_seconds=None,
            fetched_count=int(item.get("fetched_count", 0)),
            inserted_count=int(item.get("matched_article_count", 0)),
            error_count=0,
            note=str(item.get("note", "")),
        )
    for item in fundamental_source_health:
        record_source_health(
            conn,
            run_id=run_id,
            recorded_at=run_at,
            source_id=str(item.get("source_id", "akshare_hog_cycle_proxy")),
            status=str(item.get("status", "pass")),
            lag_seconds=None,
            fetched_count=int(item.get("fetched_count", 0)),
            inserted_count=int(item.get("inserted_count", 0)),
            error_count=int(item.get("error_count", 0)),
            note=str(item.get("note", "")),
        )
    for item in flow_source_health + announcement_source_health:
        record_source_health(
            conn,
            run_id=run_id,
            recorded_at=run_at,
            source_id=str(item.get("source_id", "")),
            status=str(item.get("status", "pass")),
            lag_seconds=None,
            fetched_count=int(item.get("fetched_count", 0)),
            inserted_count=int(item.get("inserted_count", 0)),
            error_count=int(item.get("error_count", 0)),
            note=str(item.get("note", "")),
        )
    openbb_context = {
        "market": fetch_openbb_market_context(run_dt),
        "macro": fetch_openbb_macro_context(run_dt),
        "shipping": fetch_openbb_shipping_context(run_dt),
    }
    for context in [openbb_context["market"], openbb_context["macro"], openbb_context["shipping"]]:
        source_status = "pass" if context.get("items") else ("warn" if context.get("errors") else "skip")
        record_source_health(
            conn,
            run_id=run_id,
            recorded_at=run_at,
            source_id=str(context.get("source_id", "openbb:context")),
            status=source_status,
            lag_seconds=None,
            fetched_count=int(len(context.get("items", []))),
            inserted_count=int(len(context.get("items", []))),
            error_count=int(len(context.get("errors", []))),
            note=str(context.get("note", "")),
        )
    if announcement_meta.get("fetched_count", 0) > 0 or execution_mode in {"off_session_tick", "after_close_rebuild"}:
        record_source_health(
            conn,
            run_id=run_id,
            recorded_at=run_at,
            source_id=str(announcement_meta.get("source_id", "stock_notice_report")),
            status="pass" if int(announcement_meta.get("fetched_count", 0)) > 0 else "warn",
            lag_seconds=None,
            fetched_count=int(announcement_meta.get("fetched_count", 0)),
            inserted_count=int(announcement_meta.get("matched_count", 0)),
            error_count=0,
            note=f"used_date={announcement_meta.get('used_date', '')}",
        )

    for row in rows:
        row["industry_state"] = determine_state(row)
        insert_signal_snapshot(conn, run_id, run_at, row)
    debug_progress("persistence:snapshots-complete")

    shared_cfg = shared_news_config()
    industry_feed_payload: dict[str, Any] | None = None
    feed_path_text = str(shared_cfg.get("industry_radar_feed_path") or "").strip()
    if feed_path_text:
        feed_path = Path(feed_path_text).expanduser()
        if feed_path.exists():
            try:
                industry_feed_payload = load_json_file(feed_path)
            except json.JSONDecodeError:
                industry_feed_payload = None
    extra_feed_payloads: list[tuple[str, dict[str, Any]]] = []
    for key, feed_name in (
        ("opportunity_report_feed_path", "news_event_hub.opportunity_report_feed_latest"),
        ("research_feed_path", "news_event_hub.research_feed_latest"),
    ):
        path_text = str(shared_cfg.get(key) or "").strip()
        if not path_text:
            continue
        path = Path(path_text).expanduser()
        if not path.exists():
            continue
        try:
            payload = load_json_file(path)
        except json.JSONDecodeError:
            continue
        extra_feed_payloads.append((feed_name, payload))
    candidate_pool_payload = build_candidate_pool_payload(
        run_dt=run_dt,
        rows=rows,
        industry_feed_payload=industry_feed_payload,
        extra_feed_payloads=extra_feed_payloads,
    )

    provisional_payload = {
        "run_id": run_id,
        "run_at": run_at,
        "market_phase": market_phase,
        "execution_mode": execution_mode,
    }
    alerts = process_alerts(
        conn,
        run_dt,
        sorted(rows, key=lambda item: safe_float(item.get("total_score")) or 0.0, reverse=True),
        provisional_payload,
        args.skip_bark,
        openbb_context,
    )
    debug_progress(f"alerts:processed count={len(alerts)}")
    payload = build_output_payload(run_id, run_dt, market_phase, rows, alerts, execution_mode, openbb_context)
    write_scan_outputs(runtime_paths, payload, run_dt)
    write_radar_outputs(runtime_paths, run_dt, payload, candidate_pool_payload)
    debug_progress("outputs:written")
    complete_run(conn, run_id, run_at, "success", f"scan finished with {len(alerts)} alerts")
    record_runtime_health(conn, run_id, run_at, "scan_runner", "pass", float(len(alerts)), "scan completed successfully")
    conn.commit()
    conn.close()
    debug_progress("run_scan:success")
    return payload


def main() -> None:
    args = parse_args()
    if args.bootstrap_only:
        run_dt = parse_run_dt(args.run_at)
        run_at = run_dt.isoformat(timespec="seconds")
        runtime_paths = resolve_runtime_paths(args.runtime_root_override)
        guardrails = validate_runtime_guardrails(runtime_paths)
        created_paths: list[str] = []
        if guardrails["status"] == "pass":
            created_paths = materialize_runtime_dirs(runtime_paths)
            initialize_event_db(runtime_paths["event_db_path"], DEFAULT_SCHEMA_PATH, run_at)
            record_bootstrap_health(runtime_paths, f"bootstrap:{run_at}", run_at, "runtime bootstrap completed")
        payload = build_bootstrap_summary(run_at, False, runtime_paths, guardrails, load_source_manifest(), created_paths)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    lock_handle = try_acquire_workspace_output_lock("intraday_scan")
    if lock_handle is None:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": "workspace_output_lock_held",
                    "lock_path": str(WORKSPACE_OUTPUT_LOCK),
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    try:
        payload = run_scan(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        release_workspace_output_lock(lock_handle)


if __name__ == "__main__":
    main()
