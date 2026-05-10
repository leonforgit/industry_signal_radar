from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from radar_config import load_config_section
except ModuleNotFoundError:
    from scripts.radar_config import load_config_section


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_MIRROR_DIR = ROOT / "output" / "sidecars" / "sentiment"
REMOTE_SENTIMENT_PREFIX = "/opt/quant-runtime/data_substrate/sentiment/"
LOCAL_QLIB_SENTIMENT_DIR = ROOT.parent / "qlib_paper_trading" / "output" / "sidecars" / "sentiment"

NAME_NORMALIZE_RE = re.compile(r"[\s\u3000·・.\\-—_()（）【】\\[\\]<>《》,:：;；/\\\\]+")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(output):
        return default
    return output


def normalize_lookup_name(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    return NAME_NORMALIZE_RE.sub("", text)


def instrument_code_suffix(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def sentiment_score_100(value: Any) -> int:
    raw = max(-1.0, min(1.0, safe_float(value)))
    return max(0, min(100, int(round((raw + 1.0) * 50.0))))


def sentiment_label(score_100: int) -> str:
    if score_100 >= 72:
        return "偏强"
    if score_100 >= 58:
        return "中性偏强"
    if score_100 >= 43:
        return "中性"
    if score_100 >= 30:
        return "中性偏弱"
    return "偏弱"


def resolve_sidecar_path(raw_path: Any, *, mirror_dir: Path | None = None) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        return Path("")
    candidate = Path(text).expanduser()
    if candidate.exists():
        return candidate
    if text.startswith(REMOTE_SENTIMENT_PREFIX):
        filename = Path(text).name
        active_mirror_dir = mirror_dir or DEFAULT_LOCAL_MIRROR_DIR
        mirror_candidate = active_mirror_dir / filename
        if mirror_candidate.exists():
            return mirror_candidate
        qlib_candidate = LOCAL_QLIB_SENTIMENT_DIR / filename
        if qlib_candidate.exists():
            return qlib_candidate
    return candidate


def latest_row_on_or_before(frame: pd.DataFrame, target_date: str) -> pd.Series | None:
    if frame.empty:
        return None
    parsed_target = pd.to_datetime(target_date, errors="coerce")
    if pd.isna(parsed_target):
        return None
    scoped = frame[pd.to_datetime(frame["datetime"], errors="coerce") <= parsed_target.normalize()].copy()
    if scoped.empty:
        return None
    scoped["datetime"] = pd.to_datetime(scoped["datetime"], errors="coerce").dt.normalize()
    scoped = scoped.dropna(subset=["datetime"]).sort_values("datetime")
    if scoped.empty:
        return None
    return scoped.iloc[-1]


def build_market_sentiment_context(*, target_date: str, config_path: Path | None = None) -> dict[str, Any]:
    cfg = load_config_section(config_path, "sentiment_sidecar")
    if not bool(cfg.get("enabled", True)):
        return {}
    mirror_dir = ROOT / str(cfg.get("mirror_dir") or "output/sidecars/sentiment")
    market_path = resolve_sidecar_path(cfg.get("market_csv_path"), mirror_dir=mirror_dir)
    if not market_path.exists():
        return {}
    frame = pd.read_csv(market_path)
    if "datetime" not in frame.columns:
        return {}
    row = latest_row_on_or_before(frame, target_date)
    if row is None:
        return {}
    source_date = pd.to_datetime(row.get("datetime"), errors="coerce")
    lag_days = 0
    parsed_target = pd.to_datetime(target_date, errors="coerce")
    if not pd.isna(parsed_target) and not pd.isna(source_date):
        lag_days = max(0, int((parsed_target.normalize() - source_date.normalize()).days))
    flow_raw = safe_float(row.get("market_flow_sentiment"))
    event_raw = safe_float(row.get("market_event_sentiment"))
    composite_raw = safe_float(row.get("market_composite_sentiment"))
    flow_score = sentiment_score_100(flow_raw)
    event_score = sentiment_score_100(event_raw)
    composite_score = sentiment_score_100(composite_raw)
    return {
        "as_of_date": source_date.strftime("%Y-%m-%d") if not pd.isna(source_date) else str(target_date),
        "lag_days": lag_days,
        "market_flow_sentiment": flow_raw,
        "market_flow_score": flow_score,
        "market_flow_label": sentiment_label(flow_score),
        "market_event_sentiment": event_raw,
        "market_event_score": event_score,
        "market_event_label": sentiment_label(event_score),
        "market_composite_sentiment": composite_raw,
        "market_composite_score": composite_score,
        "market_composite_label": sentiment_label(composite_score),
    }


def load_company_sentiment_index(*, target_date: str, config_path: Path | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    cfg = load_config_section(config_path, "sentiment_sidecar")
    if not bool(cfg.get("enabled", True)):
        return {"by_name": {}, "by_code": {}}
    mirror_dir = ROOT / str(cfg.get("mirror_dir") or "output/sidecars/sentiment")
    company_path = resolve_sidecar_path(cfg.get("company_csv_path"), mirror_dir=mirror_dir)
    if not company_path.exists():
        return {"by_name": {}, "by_code": {}}
    frame = pd.read_csv(company_path)
    required_columns = {"datetime", "instrument", "company_name"}
    if not required_columns.issubset(frame.columns):
        return {"by_name": {}, "by_code": {}}
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce").dt.normalize()
    parsed_target = pd.to_datetime(target_date, errors="coerce")
    if pd.isna(parsed_target):
        return {"by_name": {}, "by_code": {}}
    scoped = frame[frame["datetime"] <= parsed_target.normalize()].copy()
    if scoped.empty:
        return {"by_name": {}, "by_code": {}}
    latest_date = scoped["datetime"].max()
    scoped = scoped[scoped["datetime"] == latest_date].copy()
    by_name: dict[str, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    for _, row in scoped.iterrows():
        market_raw = safe_float(row.get("company_market_sentiment"))
        event_raw = safe_float(row.get("company_event_sentiment"))
        composite_raw = safe_float(row.get("company_composite_sentiment"))
        market_score = sentiment_score_100(market_raw)
        event_score = sentiment_score_100(event_raw)
        composite_score = sentiment_score_100(composite_raw)
        record = {
            "as_of_date": latest_date.strftime("%Y-%m-%d"),
            "lag_days": max(0, int((parsed_target.normalize() - latest_date).days)),
            "instrument": str(row.get("instrument") or "").strip(),
            "company_name": str(row.get("company_name") or "").strip(),
            "company_market_sentiment": market_raw,
            "company_market_score": market_score,
            "company_market_label": sentiment_label(market_score),
            "company_event_sentiment": event_raw,
            "company_event_score": event_score,
            "company_event_label": sentiment_label(event_score),
            "company_composite_sentiment": composite_raw,
            "company_composite_score": composite_score,
            "company_composite_label": sentiment_label(composite_score),
            "daily_return": safe_float(row.get("daily_return")),
            "amount_ratio_20": safe_float(row.get("amount_ratio_20")),
            "positive_days_5d": safe_float(row.get("positive_days_5d")),
            "fact_event_count_1d": safe_float(row.get("fact_event_count_1d")),
        }
        name_key = normalize_lookup_name(record["company_name"])
        code_key = instrument_code_suffix(record["instrument"])
        if name_key and name_key not in by_name:
            by_name[name_key] = record
        if code_key and code_key not in by_code:
            by_code[code_key] = record
    return {"by_name": by_name, "by_code": by_code}


def lookup_company_sentiment(
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


def compact_company_sentiment_text(context: dict[str, Any]) -> str:
    if not context:
        return ""
    if not any(
        context.get(key) not in (None, "")
        for key in (
            "company_market_sentiment",
            "company_event_sentiment",
            "company_composite_sentiment",
            "company_market_score",
            "company_event_score",
            "company_composite_score",
        )
    ):
        return ""
    return (
        "情绪侧车：事件 {event_score}（{event_label}） / 行情 {market_score}（{market_label}） / 综合 {composite_score}（{composite_label}）"
    ).format(
        event_score=int(context.get("company_event_score") or 0),
        event_label=str(context.get("company_event_label") or "未标注"),
        market_score=int(context.get("company_market_score") or 0),
        market_label=str(context.get("company_market_label") or "未标注"),
        composite_score=int(context.get("company_composite_score") or 0),
        composite_label=str(context.get("company_composite_label") or "未标注"),
    )


def compact_market_sentiment_text(context: dict[str, Any]) -> str:
    if not context:
        return ""
    return (
        "资金流情绪 {flow_score}（{flow_label}） / 市场事件情绪 {event_score}（{event_label}） / 综合情绪 {composite_score}（{composite_label}）"
    ).format(
        flow_score=int(context.get("market_flow_score") or 0),
        flow_label=str(context.get("market_flow_label") or "未标注"),
        event_score=int(context.get("market_event_score") or 0),
        event_label=str(context.get("market_event_label") or "未标注"),
        composite_score=int(context.get("market_composite_score") or 0),
        composite_label=str(context.get("market_composite_label") or "未标注"),
    )


def sentiment_bias_text(raw_value: Any) -> str:
    value = safe_float(raw_value)
    if value >= 0.35:
        return "明显偏强"
    if value >= 0.12:
        return "温和偏强"
    if value <= -0.35:
        return "明显偏弱"
    if value <= -0.12:
        return "温和偏弱"
    return "大致中性"


def market_sentiment_date(context: dict[str, Any]) -> date | None:
    raw = str(context.get("as_of_date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None
