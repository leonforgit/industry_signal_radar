from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from radar_config import load_config_section
    from radar_sentiment_sidecar import instrument_code_suffix, normalize_lookup_name
except ModuleNotFoundError:
    from scripts.radar_config import load_config_section
    from scripts.radar_sentiment_sidecar import instrument_code_suffix, normalize_lookup_name


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_PRICE_CSV = ROOT / "output" / "sidecars" / "equity_prices" / "company_price_snapshot_latest.csv"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(numeric):
        return default
    return numeric


def resolve_repo_path(raw_path: Any, *, fallback: Path) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        return fallback
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        return candidate
    return ROOT / text


def build_company_price_snapshot_frame(
    *,
    db_path: Path,
    target_date: str,
    lookback_days: int = 45,
) -> pd.DataFrame:
    columns = [
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
    if not db_path.exists():
        return pd.DataFrame(columns=columns)
    conn = sqlite3.connect(db_path)
    try:
        frame = pd.read_sql_query(
            """
            SELECT market, instrument, company_name, trade_date, close, amount
            FROM equity_price_daily
            WHERE trade_date <= ?
              AND trade_date >= date(?, ?)
            ORDER BY market, instrument, trade_date
            """,
            conn,
            params=[target_date, target_date, f"-{int(lookback_days)} day"],
        )
    finally:
        conn.close()
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close"]).sort_values(["instrument", "trade_date"]).reset_index(drop=True)
    if frame.empty:
        return pd.DataFrame(columns=columns)

    parsed_target = pd.to_datetime(target_date, errors="coerce")
    rows: list[dict[str, Any]] = []
    for instrument, group in frame.groupby("instrument", sort=True):
        group = group.sort_values("trade_date").reset_index(drop=True)
        if len(group) < 2:
            continue
        group["prev_close"] = group["close"].shift(1)
        group["daily_return"] = group["close"].pct_change()
        group["amount_ratio_20"] = group["amount"] / group["amount"].rolling(20, min_periods=5).mean()
        positive = (group["daily_return"].fillna(0.0) > 0.0).astype(float)
        group["positive_days_5d"] = positive.rolling(5, min_periods=1).sum() / 5.0
        latest = group.iloc[-1]
        latest_date = pd.Timestamp(latest["trade_date"]).normalize()
        lag_days = 0
        if not pd.isna(parsed_target):
            lag_days = max(0, int((parsed_target.normalize() - latest_date).days))
        rows.append(
            {
                "as_of_date": latest_date.strftime("%Y-%m-%d"),
                "lag_days": lag_days,
                "instrument": str(instrument),
                "company_name": str(latest.get("company_name") or instrument),
                "close": safe_float(latest.get("close")),
                "prev_close": safe_float(latest.get("prev_close")),
                "daily_return": safe_float(latest.get("daily_return")),
                "amount_ratio_20": safe_float(latest.get("amount_ratio_20")),
                "positive_days_5d": safe_float(latest.get("positive_days_5d")),
                "price_sidecar_source": "equity_price_db",
            }
        )
    output = pd.DataFrame(rows)
    if output.empty:
        return pd.DataFrame(columns=columns)
    return output.sort_values("instrument").reset_index(drop=True)


def load_company_price_snapshot(
    *,
    target_date: str,
    config_path: Path | None = None,
) -> pd.DataFrame:
    cfg = load_config_section(config_path, "price_sidecar")
    if not bool(cfg.get("enabled", True)):
        return pd.DataFrame()
    csv_path = resolve_repo_path(
        cfg.get("company_price_csv_path"),
        fallback=DEFAULT_LOCAL_PRICE_CSV,
    )
    if csv_path.exists():
        frame = pd.read_csv(csv_path)
        if frame.empty:
            return frame
        if "as_of_date" in frame.columns:
            frame["as_of_date"] = pd.to_datetime(frame["as_of_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        return frame
    db_path = resolve_repo_path(
        cfg.get("equity_price_db_path"),
        fallback=Path(""),
    )
    if not str(db_path):
        return pd.DataFrame()
    return build_company_price_snapshot_frame(
        db_path=db_path,
        target_date=target_date,
        lookback_days=int(cfg.get("lookback_days") or 45),
    )


def load_company_price_index(
    *,
    target_date: str,
    config_path: Path | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    frame = load_company_price_snapshot(target_date=target_date, config_path=config_path)
    if frame.empty:
        return {"by_name": {}, "by_code": {}}
    by_name: dict[str, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        record = {
            "as_of_date": str(row.get("as_of_date") or target_date),
            "lag_days": int(row.get("lag_days") or 0),
            "instrument": str(row.get("instrument") or "").strip(),
            "company_name": str(row.get("company_name") or "").strip(),
            "close": safe_float(row.get("close")),
            "prev_close": safe_float(row.get("prev_close")),
            "daily_return": safe_float(row.get("daily_return")),
            "amount_ratio_20": safe_float(row.get("amount_ratio_20")),
            "positive_days_5d": safe_float(row.get("positive_days_5d")),
            "price_sidecar_source": str(row.get("price_sidecar_source") or "company_price_snapshot"),
        }
        code_key = instrument_code_suffix(record["instrument"])
        name_key = normalize_lookup_name(record["company_name"])
        if code_key and code_key not in by_code:
            by_code[code_key] = record
        if name_key and name_key not in by_name:
            by_name[name_key] = record
    return {"by_name": by_name, "by_code": by_code}


def lookup_company_price(
    index: dict[str, dict[str, dict[str, Any]]],
    *,
    subject: str,
    stock_code: str = "",
    company_name: str = "",
) -> dict[str, Any]:
    by_name = index.get("by_name") or {}
    by_code = index.get("by_code") or {}
    code_key = instrument_code_suffix(stock_code)
    if code_key and code_key in by_code:
        return dict(by_code[code_key])
    for candidate in (company_name, subject):
        name_key = normalize_lookup_name(candidate)
        if name_key and name_key in by_name:
            return dict(by_name[name_key])
    return {}
