from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROXY_PATH = ROOT / "data" / "fundamental_proxy_definitions.json"
DEFAULT_PROXY_CACHE_DIR = ROOT / "output" / "sidecars" / "fundamental_proxy_cache"
MARKET_TZ = ZoneInfo("Asia/Shanghai")
ELECTRICITY_FETCH_TIMEOUT_SECONDS = 60


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_proxy_definitions(path: Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else DEFAULT_PROXY_PATH
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    proxy_sets = payload.get("proxy_sets", [])
    return proxy_sets if isinstance(proxy_sets, list) else []


def proxy_failure_health(source_id: str, note: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "status": "warn",
        "fetched_count": 0,
        "inserted_count": 0,
        "error_count": 1,
        "note": note,
    }


def proxy_cache_path(name: str) -> Path:
    DEFAULT_PROXY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_PROXY_CACHE_DIR / f"{name}.json"


def load_cached_proxy(name: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    path = proxy_cache_path(name)
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    overlay = payload.get("overlay")
    source_health = payload.get("source_health")
    if not isinstance(overlay, dict) or not isinstance(source_health, list):
        return None, None
    return overlay, source_health


def write_cached_proxy(name: str, *, overlay: dict[str, Any], source_health: list[dict[str, Any]]) -> None:
    payload = {
        "generated_at": datetime.now(MARKET_TZ).isoformat(timespec="seconds"),
        "overlay": overlay,
        "source_health": source_health,
    }
    proxy_cache_path(name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_daily_price_frame(frame: pd.DataFrame, price_col: str) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["日期"] = pd.to_datetime(normalized["日期"]).dt.date
    normalized[price_col] = pd.to_numeric(normalized[price_col], errors="coerce")
    normalized = normalized.dropna(subset=[price_col]).sort_values("日期").reset_index(drop=True)
    return normalized


def latest_on_or_before(frame: pd.DataFrame, run_dt: datetime) -> pd.DataFrame:
    local_date = run_dt.astimezone(MARKET_TZ).date()
    return frame[frame["日期"] <= local_date].copy()


def compute_return(frame: pd.DataFrame, price_col: str, lookback: int) -> float | None:
    if len(frame.index) <= lookback:
        return None
    latest = safe_float(frame.iloc[-1][price_col])
    base = safe_float(frame.iloc[-1 - lookback][price_col])
    if latest is None or base in {None, 0}:
        return None
    return (latest / base - 1.0) * 100


def fetch_society_electricity_series(timeout_seconds: int = ELECTRICITY_FETCH_TIMEOUT_SECONDS) -> tuple[pd.DataFrame, dict[str, Any]]:
    with tempfile.NamedTemporaryFile(prefix="radar_society_electricity_", suffix=".json", delete=False) as handle:
        output_path = handle.name
    child_code = """
import json
import akshare as ak
from pathlib import Path

output_path = Path({output_path!r})
frame = ak.macro_china_society_electricity()
if frame is None or frame.empty:
    output_path.write_text(json.dumps({{"rows": [], "row_count": 0}}, ensure_ascii=False), encoding="utf-8")
else:
    output_path.write_text(json.dumps({{
        "rows": frame.to_dict(orient="records"),
        "row_count": int(len(frame.index)),
    }}, ensure_ascii=False), encoding="utf-8")
print(json.dumps({{"status": "ok", "output_path": str(output_path)}}, ensure_ascii=False))
""".format(output_path=output_path)
    try:
        result = subprocess.run(
            [sys.executable, "-c", child_code],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        Path(output_path).unlink(missing_ok=True)
        return pd.DataFrame(), proxy_failure_health(
            "akshare:macro_china_society_electricity",
            f"TimeoutExpired after {timeout_seconds}s",
        )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        Path(output_path).unlink(missing_ok=True)
        return pd.DataFrame(), proxy_failure_health(
            "akshare:macro_china_society_electricity",
            f"child rc={result.returncode}: {stderr[:240]}",
        )

    stdout = (result.stdout or "").strip()
    if not stdout:
        Path(output_path).unlink(missing_ok=True)
        return pd.DataFrame(), proxy_failure_health(
            "akshare:macro_china_society_electricity",
            "empty stdout",
        )
    try:
        json.loads(stdout)
    except json.JSONDecodeError as exc:
        Path(output_path).unlink(missing_ok=True)
        return pd.DataFrame(), proxy_failure_health(
            "akshare:macro_china_society_electricity",
            f"invalid json: {exc}",
        )

    file_path = Path(output_path)
    if not file_path.exists():
        return pd.DataFrame(), proxy_failure_health(
            "akshare:macro_china_society_electricity",
            "did not materialize output file",
        )
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    finally:
        file_path.unlink(missing_ok=True)

    frame = pd.DataFrame(payload.get("rows", []))
    if frame.empty:
        return frame, {
            "source_id": "akshare:macro_china_society_electricity",
            "status": "warn",
            "fetched_count": 0,
            "inserted_count": 0,
            "error_count": 0,
            "note": "empty frame",
        }
    return frame, {
        "source_id": "akshare:macro_china_society_electricity",
        "status": "pass",
        "fetched_count": len(frame.index),
        "inserted_count": len(frame.index),
        "error_count": 0,
        "note": "society electricity series",
    }


def compute_hog_cycle_overlay(run_dt: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    overlay = {
        "fundamental_proxy_score": 0.0,
        "fundamental_proxy_evidence": {},
    }
    source_health: list[dict[str, Any]] = []

    hog_price = normalize_daily_price_frame(ak.spot_hog_lean_price_soozhu(), "价格")
    feed_price = normalize_daily_price_frame(ak.spot_mixed_feed_soozhu(), "价格")
    hog_index = ak.index_hog_spot_price().copy()
    hog_index["日期"] = pd.to_datetime(hog_index["日期"]).dt.date
    hog_index["指数"] = pd.to_numeric(hog_index["指数"], errors="coerce")
    hog_index["6个月均线"] = pd.to_numeric(hog_index["6个月均线"], errors="coerce")
    hog_index = hog_index.dropna(subset=["指数"]).sort_values("日期").reset_index(drop=True)

    hog_price = latest_on_or_before(hog_price, run_dt)
    feed_price = latest_on_or_before(feed_price, run_dt)
    hog_index = latest_on_or_before(hog_index, run_dt)

    source_health.extend(
        [
            {
                "source_id": "akshare:spot_hog_lean_price_soozhu",
                "status": "pass" if not hog_price.empty else "warn",
                "fetched_count": len(hog_price.index),
                "inserted_count": len(hog_price.tail(15).index),
                "error_count": 0,
                "note": "hog lean price series",
            },
            {
                "source_id": "akshare:spot_mixed_feed_soozhu",
                "status": "pass" if not feed_price.empty else "warn",
                "fetched_count": len(feed_price.index),
                "inserted_count": len(feed_price.tail(15).index),
                "error_count": 0,
                "note": "mixed feed price series",
            },
            {
                "source_id": "akshare:index_hog_spot_price",
                "status": "pass" if not hog_index.empty else "warn",
                "fetched_count": len(hog_index.index),
                "inserted_count": len(hog_index.tail(60).index),
                "error_count": 0,
                "note": "hog spot index series",
            },
        ]
    )

    if hog_price.empty or feed_price.empty or hog_index.empty:
        return overlay, source_health

    merged = pd.merge(
        hog_price.rename(columns={"价格": "hog_price"}),
        feed_price.rename(columns={"价格": "feed_price"}),
        on="日期",
        how="inner",
    )
    merged["hog_feed_ratio"] = merged["hog_price"] / merged["feed_price"]
    merged = merged.dropna(subset=["hog_feed_ratio"]).sort_values("日期").reset_index(drop=True)
    if merged.empty:
        return overlay, source_health

    hog_5d = compute_return(hog_price, "价格", 5)
    hog_10d = compute_return(hog_price, "价格", 10)
    feed_5d = compute_return(feed_price, "价格", 5)
    latest_ratio = safe_float(merged.iloc[-1]["hog_feed_ratio"])
    ratio_mean_5d = safe_float(merged["hog_feed_ratio"].tail(5).mean())
    latest_index = safe_float(hog_index.iloc[-1]["指数"])
    latest_ma6 = safe_float(hog_index.iloc[-1]["6个月均线"])
    index_above_ma = bool(latest_index is not None and latest_ma6 is not None and latest_index > latest_ma6)

    score = 0.0
    if hog_5d is not None and hog_5d > 0:
        score += 0.25
    if hog_10d is not None and hog_10d > 0:
        score += 0.2
    if latest_ratio is not None and ratio_mean_5d is not None and latest_ratio > ratio_mean_5d:
        score += 0.3
    if feed_5d is not None and feed_5d < 0:
        score += 0.1
    if index_above_ma:
        score += 0.15

    overlay["fundamental_proxy_score"] = min(1.0, round(score, 4))
    overlay["fundamental_proxy_evidence"] = {
        "proxy_family": "hog_cycle",
        "latest_date": str(merged.iloc[-1]["日期"]),
        "hog_price_latest": safe_float(hog_price.iloc[-1]["价格"]),
        "hog_price_5d_return": hog_5d,
        "hog_price_10d_return": hog_10d,
        "feed_price_latest": safe_float(feed_price.iloc[-1]["价格"]),
        "feed_price_5d_return": feed_5d,
        "hog_feed_ratio_latest": latest_ratio,
        "hog_feed_ratio_5d_mean": ratio_mean_5d,
        "hog_index_latest": latest_index,
        "hog_index_6m_ma": latest_ma6,
        "hog_index_above_6m_ma": index_above_ma,
        "summary_cn": "猪价 / 饲料 / 生猪指数代理",
    }
    return overlay, source_health


def compute_shipping_cycle_overlay(run_dt: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    overlay = {
        "fundamental_proxy_score": 0.0,
        "fundamental_proxy_evidence": {},
    }
    source_health: list[dict[str, Any]] = []
    bdi_error: Exception | None = None
    try:
        bdi = ak.macro_shipping_bdi().copy()
    except Exception as exc:
        bdi_error = exc
        bdi = pd.DataFrame(columns=["日期", "最新值", "涨跌幅", "近3月涨跌幅", "近6月涨跌幅"])
    if not bdi.empty:
        bdi["日期"] = pd.to_datetime(bdi["日期"], errors="coerce").dt.date
        bdi["最新值"] = pd.to_numeric(bdi["最新值"], errors="coerce")
        bdi["涨跌幅"] = pd.to_numeric(bdi["涨跌幅"], errors="coerce")
        bdi["近3月涨跌幅"] = pd.to_numeric(bdi["近3月涨跌幅"], errors="coerce")
        bdi["近6月涨跌幅"] = pd.to_numeric(bdi["近6月涨跌幅"], errors="coerce")
        bdi = bdi.dropna(subset=["日期", "最新值"]).sort_values("日期").reset_index(drop=True)

    latest = latest_on_or_before(bdi, run_dt) if not bdi.empty else pd.DataFrame()
    freight_error: Exception | None = None
    try:
        freight = ak.macro_china_freight_index().copy()
    except Exception as exc:
        freight_error = exc
        freight = pd.DataFrame(columns=["截止日期"])
    if freight.empty and bdi.empty:
        cached_overlay, cached_health = load_cached_proxy("shipping_cycle")
        if cached_overlay is not None and cached_health is not None:
            cache_notes = [str(item.get("note") or "") for item in cached_health if isinstance(item, dict)]
            cache_reason = " / ".join(item for item in cache_notes if item) or "upstream fetch failure"
            return cached_overlay, [
                {
                    "source_id": "akshare:shipping_cycle_proxy",
                    "status": "pass",
                    "fetched_count": 0,
                    "inserted_count": 0,
                    "error_count": 0,
                    "note": f"cache_fallback after {cache_reason}",
                }
            ]
        if bdi_error is not None:
            raise bdi_error
        if freight_error is not None:
            raise freight_error
    if not freight.empty:
        freight["日期"] = pd.to_datetime(freight["截止日期"], errors="coerce").dt.date
    for column in [
        "波罗的海综合运价指数BDI",
        "波罗的海好望角型船运价指数BCI",
        "波罗的海超级大灵便型船BSI指数",
        "油轮运价指数成品油运价指数BCTI",
        "油轮运价指数原油运价指数BDTI",
    ]:
        if column in freight.columns:
            freight[column] = pd.to_numeric(freight[column], errors="coerce")
    if not freight.empty:
        freight = freight.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)
        freight = latest_on_or_before(freight.rename(columns={"日期": "日期"}), run_dt)
    source_health.append(
        {
            "source_id": "akshare:macro_shipping_bdi",
            "status": "pass" if not latest.empty or (bdi_error is not None and not freight.empty) else "warn",
            "fetched_count": len(latest.index),
            "inserted_count": len(latest.tail(30).index) if not latest.empty else 0,
            "error_count": 0 if bdi_error is None else 1,
            "note": (
                "baltic dry index series"
                if bdi_error is None
                else f"freight_only_fallback after {type(bdi_error).__name__}: {bdi_error}"
            ),
        }
    )
    source_health.append(
        {
            "source_id": "akshare:macro_china_freight_index",
            "status": "pass" if not freight.empty or (freight_error is not None and not latest.empty) else "warn",
            "fetched_count": len(freight.index),
            "inserted_count": len(freight.tail(30).index),
            "error_count": 0 if freight_error is None else 1,
            "note": (
                "china freight composite series"
                if freight_error is None
                else f"bdi_only_fallback after {type(freight_error).__name__}: {freight_error}"
            ),
        }
    )
    if latest.empty and freight.empty:
        return overlay, source_health

    row = latest.iloc[-1] if not latest.empty else pd.Series(dtype="object")
    one_day = safe_float(row.get("涨跌幅"))
    three_month = safe_float(row.get("近3月涨跌幅"))
    six_month = safe_float(row.get("近6月涨跌幅"))
    latest_value = safe_float(row.get("最新值"))
    ma20 = safe_float(latest["最新值"].tail(20).mean()) if not latest.empty else None
    freight_row = freight.iloc[-1] if not freight.empty else pd.Series(dtype="object")
    freight_bdi = safe_float(freight_row.get("波罗的海综合运价指数BDI"))
    freight_bci = safe_float(freight_row.get("波罗的海好望角型船运价指数BCI"))
    freight_bsi = safe_float(freight_row.get("波罗的海超级大灵便型船BSI指数"))
    freight_bcti = safe_float(freight_row.get("油轮运价指数成品油运价指数BCTI"))
    freight_bdti = safe_float(freight_row.get("油轮运价指数原油运价指数BDTI"))
    freight_bdi_ma20 = safe_float(freight["波罗的海综合运价指数BDI"].tail(20).mean()) if not freight.empty else None
    freight_bcti_ma20 = safe_float(freight["油轮运价指数成品油运价指数BCTI"].tail(20).mean()) if not freight.empty else None

    score = 0.0
    if one_day is not None and one_day > 0:
        score += 0.14
    if three_month is not None and three_month > 0:
        score += 0.24
    if six_month is not None and six_month > 0:
        score += 0.14
    if latest_value is not None and ma20 is not None and latest_value > ma20:
        score += 0.18
    if freight_bdi is not None and freight_bdi_ma20 is not None and freight_bdi > freight_bdi_ma20:
        score += 0.14
    if freight_bcti is not None and freight_bcti_ma20 is not None and freight_bcti > freight_bcti_ma20:
        score += 0.08
    if freight_bci is not None and freight_bsi is not None and freight_bci > freight_bsi:
        score += 0.08

    overlay["fundamental_proxy_score"] = min(1.0, round(score, 4))
    overlay["fundamental_proxy_evidence"] = {
        "proxy_family": "shipping_cycle",
        "latest_date": str(row["日期"]) if "日期" in row else "",
        "bdi_latest": latest_value,
        "bdi_one_day_return": one_day,
        "bdi_three_month_return": three_month,
        "bdi_six_month_return": six_month,
        "bdi_20d_mean": ma20,
        "freight_bdi_latest": freight_bdi,
        "freight_bci_latest": freight_bci,
        "freight_bsi_latest": freight_bsi,
        "freight_bcti_latest": freight_bcti,
        "freight_bdti_latest": freight_bdti,
        "freight_bdi_20d_mean": freight_bdi_ma20,
        "freight_bcti_20d_mean": freight_bcti_ma20,
        "summary_cn": "BDI + 航贸运价指数代理",
    }
    write_cached_proxy("shipping_cycle", overlay=overlay, source_health=source_health)
    return overlay, source_health


def normalize_margin_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["日期"] = pd.to_datetime(normalized["日期"], errors="coerce").dt.date
    normalized["融资买入额"] = pd.to_numeric(normalized["融资买入额"], errors="coerce")
    normalized["融资余额"] = pd.to_numeric(normalized["融资余额"], errors="coerce")
    normalized["融资融券余额"] = pd.to_numeric(normalized["融资融券余额"], errors="coerce")
    normalized = normalized.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)
    return normalized


def compute_broker_cycle_overlay(run_dt: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    overlay = {
        "fundamental_proxy_score": 0.0,
        "fundamental_proxy_evidence": {},
    }
    source_health: list[dict[str, Any]] = []
    sh = normalize_margin_frame(ak.macro_china_market_margin_sh())
    sz = normalize_margin_frame(ak.macro_china_market_margin_sz())
    sh = latest_on_or_before(sh, run_dt)
    sz = latest_on_or_before(sz, run_dt)

    source_health.extend(
        [
            {
                "source_id": "akshare:macro_china_market_margin_sh",
                "status": "pass" if not sh.empty else "warn",
                "fetched_count": len(sh.index),
                "inserted_count": len(sh.tail(30).index),
                "error_count": 0,
                "note": "sh margin series",
            },
            {
                "source_id": "akshare:macro_china_market_margin_sz",
                "status": "pass" if not sz.empty else "warn",
                "fetched_count": len(sz.index),
                "inserted_count": len(sz.tail(30).index),
                "error_count": 0,
                "note": "sz margin series",
            },
        ]
    )
    if sh.empty or sz.empty:
        return overlay, source_health

    merged = pd.merge(
        sh[["日期", "融资买入额", "融资余额", "融资融券余额"]].rename(
            columns={
                "融资买入额": "sh_margin_buy",
                "融资余额": "sh_margin_balance",
                "融资融券余额": "sh_total_balance",
            }
        ),
        sz[["日期", "融资买入额", "融资余额", "融资融券余额"]].rename(
            columns={
                "融资买入额": "sz_margin_buy",
                "融资余额": "sz_margin_balance",
                "融资融券余额": "sz_total_balance",
            }
        ),
        on="日期",
        how="inner",
    ).sort_values("日期").reset_index(drop=True)
    if merged.empty:
        return overlay, source_health

    merged["total_margin_buy"] = merged["sh_margin_buy"] + merged["sz_margin_buy"]
    merged["total_margin_balance"] = merged["sh_margin_balance"] + merged["sz_margin_balance"]
    merged["total_balance"] = merged["sh_total_balance"] + merged["sz_total_balance"]
    latest = merged.iloc[-1]
    latest_date = str(latest["日期"])
    balance_5d = compute_return(merged.rename(columns={"total_balance": "value"}), "value", 5)
    balance_20d = compute_return(merged.rename(columns={"total_balance": "value"}), "value", 20)
    margin_buy_5d_mean = safe_float(merged["total_margin_buy"].tail(5).mean())
    margin_buy_20d_mean = safe_float(merged["total_margin_buy"].tail(20).mean())
    latest_margin_buy = safe_float(latest["total_margin_buy"])
    latest_balance = safe_float(latest["total_balance"])

    score = 0.0
    if balance_5d is not None and balance_5d > 0:
        score += 0.25
    if balance_20d is not None and balance_20d > 0:
        score += 0.3
    if latest_margin_buy is not None and margin_buy_20d_mean is not None and latest_margin_buy > margin_buy_20d_mean:
        score += 0.25
    if margin_buy_5d_mean is not None and margin_buy_20d_mean is not None and margin_buy_5d_mean > margin_buy_20d_mean:
        score += 0.2

    overlay["fundamental_proxy_score"] = min(1.0, round(score, 4))
    overlay["fundamental_proxy_evidence"] = {
        "proxy_family": "broker_cycle",
        "latest_date": latest_date,
        "market_total_balance": latest_balance,
        "market_total_balance_5d_return": balance_5d,
        "market_total_balance_20d_return": balance_20d,
        "market_margin_buy_latest": latest_margin_buy,
        "market_margin_buy_5d_mean": margin_buy_5d_mean,
        "market_margin_buy_20d_mean": margin_buy_20d_mean,
        "summary_cn": "两融总量 / 券商交易景气代理",
    }
    return overlay, source_health


def compute_utility_electricity_overlay(run_dt: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    overlay = {
        "fundamental_proxy_score": 0.0,
        "fundamental_proxy_evidence": {},
    }
    frame, health = fetch_society_electricity_series()
    source_health = [health]
    if frame.empty:
        return overlay, source_health

    frame = frame.copy()
    date_text = (
        frame["统计时间"]
        .astype(str)
        .str.replace(r"[^\d]", "-", regex=True)
        .str.replace(r"-{2,}", "-", regex=True)
        .str.strip("-")
    )
    parsed_date = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    month_mask = date_text.str.fullmatch(r"\d{4}-\d{1,2}")
    day_mask = date_text.str.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}")
    if month_mask.any():
        parsed_date.loc[month_mask] = pd.to_datetime(date_text.loc[month_mask], format="%Y-%m", errors="coerce")
    if day_mask.any():
        parsed_date.loc[day_mask] = pd.to_datetime(date_text.loc[day_mask], format="%Y-%m-%d", errors="coerce")
    frame["日期"] = parsed_date.dt.date
    numeric_columns = [
        "全社会用电量",
        "全社会用电量同比",
        "第二产业用电量",
        "第二产业用电量同比",
        "第三产业用电量",
        "第三产业用电量同比",
        "城乡居民生活用电量合计",
        "城乡居民生活用电量合计同比",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)
    frame = latest_on_or_before(frame, run_dt)
    source_health[0]["inserted_count"] = len(frame.index)
    if frame.empty:
        source_health[0]["status"] = "warn"
        source_health[0]["note"] = "no_recent_society_electricity_snapshot"
        return overlay, source_health

    latest = frame.iloc[-1]
    latest_date = str(latest["日期"])
    total_yoy = safe_float(latest.get("全社会用电量同比"))
    second_yoy = safe_float(latest.get("第二产业用电量同比"))
    third_yoy = safe_float(latest.get("第三产业用电量同比"))
    resident_yoy = safe_float(latest.get("城乡居民生活用电量合计同比"))
    total_level = safe_float(latest.get("全社会用电量"))
    three_month_total_yoy = safe_float(frame["全社会用电量同比"].tail(3).mean())
    three_month_second_yoy = safe_float(frame["第二产业用电量同比"].tail(3).mean())
    previous_three_month_total_yoy = (
        safe_float(frame["全社会用电量同比"].tail(6).head(3).mean()) if len(frame.index) >= 6 else None
    )
    previous_three_month_second_yoy = (
        safe_float(frame["第二产业用电量同比"].tail(6).head(3).mean()) if len(frame.index) >= 6 else None
    )

    score = 0.0
    if total_yoy is not None and total_yoy > 0:
        score += 0.22
    if second_yoy is not None and second_yoy > 0:
        score += 0.28
    if third_yoy is not None and third_yoy > 0:
        score += 0.12
    if (
        three_month_total_yoy is not None
        and previous_three_month_total_yoy is not None
        and three_month_total_yoy > previous_three_month_total_yoy
    ):
        score += 0.18
    if (
        three_month_second_yoy is not None
        and previous_three_month_second_yoy is not None
        and three_month_second_yoy > previous_three_month_second_yoy
    ):
        score += 0.2

    overlay["fundamental_proxy_score"] = min(1.0, round(score, 4))
    overlay["fundamental_proxy_evidence"] = {
        "proxy_family": "utility_electricity",
        "latest_date": latest_date,
        "society_electricity_latest": total_level,
        "society_electricity_yoy": total_yoy,
        "industry2_electricity_yoy": second_yoy,
        "industry3_electricity_yoy": third_yoy,
        "resident_electricity_yoy": resident_yoy,
        "society_electricity_3m_yoy_mean": three_month_total_yoy,
        "industry2_electricity_3m_yoy_mean": three_month_second_yoy,
        "society_electricity_prev_3m_yoy_mean": previous_three_month_total_yoy,
        "industry2_electricity_prev_3m_yoy_mean": previous_three_month_second_yoy,
        "summary_cn": "全社会用电量 / 第二产业用电量代理",
    }
    return overlay, source_health


def build_fundamental_proxy_overlay(registry: list[dict[str, str]], run_dt: datetime) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    overlay: dict[str, dict[str, Any]] = {
        item["industry_id"]: {
            "fundamental_proxy_score": 0.0,
            "fundamental_proxy_evidence": {},
        }
        for item in registry
    }
    source_health: list[dict[str, Any]] = []
    proxy_defs = load_proxy_definitions()
    active_ids = {str(item.get("industry_id", "")) for item in proxy_defs if str(item.get("status", "")) == "active"}

    if "sw_l1_801010" in active_ids:
        try:
            hog_overlay, hog_source_health = compute_hog_cycle_overlay(run_dt)
        except Exception as exc:
            hog_overlay = {"fundamental_proxy_score": 0.0, "fundamental_proxy_evidence": {}}
            hog_source_health = [
                proxy_failure_health(
                    "akshare:hog_cycle_proxy",
                    f"{type(exc).__name__}: {exc}",
                )
            ]
        overlay["sw_l1_801010"] = hog_overlay
        source_health.extend(hog_source_health)

    if "sw_l1_801170" in active_ids:
        try:
            shipping_overlay, shipping_source_health = compute_shipping_cycle_overlay(run_dt)
        except Exception as exc:
            shipping_overlay = {"fundamental_proxy_score": 0.0, "fundamental_proxy_evidence": {}}
            shipping_source_health = [
                proxy_failure_health(
                    "akshare:shipping_cycle_proxy",
                    f"{type(exc).__name__}: {exc}",
                )
            ]
        overlay["sw_l1_801170"] = shipping_overlay
        source_health.extend(shipping_source_health)

    if "sw_l1_801790" in active_ids:
        try:
            broker_overlay, broker_source_health = compute_broker_cycle_overlay(run_dt)
        except Exception as exc:
            broker_overlay = {"fundamental_proxy_score": 0.0, "fundamental_proxy_evidence": {}}
            broker_source_health = [
                proxy_failure_health(
                    "akshare:broker_cycle_proxy",
                    f"{type(exc).__name__}: {exc}",
                )
            ]
        overlay["sw_l1_801790"] = broker_overlay
        source_health.extend(broker_source_health)

    if "sw_l1_801160" in active_ids:
        try:
            utility_overlay, utility_source_health = compute_utility_electricity_overlay(run_dt)
        except Exception as exc:
            utility_overlay = {"fundamental_proxy_score": 0.0, "fundamental_proxy_evidence": {}}
            utility_source_health = [
                proxy_failure_health(
                    "akshare:utility_electricity_proxy",
                    f"{type(exc).__name__}: {exc}",
                )
            ]
        overlay["sw_l1_801160"] = utility_overlay
        source_health.extend(utility_source_health)

    return overlay, source_health
