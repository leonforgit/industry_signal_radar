from __future__ import annotations

from io import BytesIO, StringIO
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
import requests
from bs4 import BeautifulSoup

from radar_industry_registry import (
    load_industry_etf_primary,
    load_industry_stock_primary,
    parse_heat_keywords,
)
from radar_config import load_config_section


MARKET_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parent.parent
NORTHBOUND_FETCH_TIMEOUT_SECONDS = 45


def debug_progress(message: str) -> None:
    if os.getenv("RADAR_DEBUG_PROGRESS") == "1":
        print(f"[radar-debug] flow:{message}", flush=True)


def configured_flow_cache_dir() -> Path:
    runtime_paths = load_config_section(None, "runtime_paths")
    cache_dir = runtime_paths.get("cache_dir")
    base = Path(str(cache_dir)) if cache_dir else ROOT / "cache"
    if base.is_absolute() and str(base).startswith("/root/"):
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            base = ROOT / "cache"
    target = base / "flow_snapshots"
    target.mkdir(parents=True, exist_ok=True)
    return target


def flow_cache_ttl_minutes() -> int:
    scan = load_config_section(None, "scan")
    return int(scan.get("flow_cache_ttl_minutes", 20))


def etf_spot_cache_ttl_minutes() -> int:
    scan = load_config_section(None, "scan")
    return int(scan.get("etf_spot_cache_ttl_minutes", flow_cache_ttl_minutes()))


def is_cache_fresh(path: Path, run_dt: datetime, ttl_minutes: int) -> bool:
    if not path.exists() or ttl_minutes <= 0:
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=MARKET_TZ)
    return (run_dt.astimezone(MARKET_TZ) - modified) <= timedelta(minutes=ttl_minutes)


def read_cached_frame(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def write_cached_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def cache_key_path(name: str, run_dt: datetime) -> Path:
    cache_day = run_dt.astimezone(MARKET_TZ).strftime("%Y%m%d")
    return configured_flow_cache_dir() / f"{name}_{cache_day}.csv"


def load_recent_cached_frame(name: str) -> tuple[pd.DataFrame | None, Path | None]:
    cache_dir = configured_flow_cache_dir()
    candidates = sorted(cache_dir.glob(f"{name}_*.csv"), reverse=True)
    for path in candidates:
        frame = read_cached_frame(path)
        if frame is not None and not frame.empty:
            return frame, path
    return None, None


def request_with_retries(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    attempts: int = 3,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as exc:  # pragma: no cover - network behavior
            last_exc = exc
            if attempt >= attempts:
                break
            time.sleep(min(0.5 * attempt, 2.0))
    if last_exc is None:  # pragma: no cover
        raise RuntimeError(f"request_with_retries exhausted without response for {url}")
    raise last_exc


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def market_date(run_dt: datetime) -> datetime.date:
    return run_dt.astimezone(MARKET_TZ).date()


def date_candidates(run_dt: datetime, lookback_days: int) -> list[str]:
    base = market_date(run_dt)
    return [(base - timedelta(days=offset)).strftime("%Y%m%d") for offset in range(lookback_days + 1)]


def percentile(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).rank(pct=True, method="average")


def industry_keywords(item: dict[str, str]) -> list[str]:
    display_name = str(item.get("display_name_cn", "")).strip()
    keywords = [display_name] if display_name else []
    for keyword in parse_heat_keywords(item.get("heat_keywords", "")):
        if keyword not in keywords:
            keywords.append(keyword)
    return keywords


def first_non_empty_margin_frame(run_dt: datetime) -> tuple[pd.DataFrame, str, str]:
    for date_str in date_candidates(run_dt, 7):
        try:
            sz_frame = ak.stock_margin_detail_szse(date=date_str).copy()
        except Exception:
            sz_frame = pd.DataFrame(columns=["证券代码", "证券简称", "融资买入额", "融资余额", "融资融券余额"])
        try:
            sh_frame = ak.stock_margin_detail_sse(date=date_str).copy()
        except Exception:
            sh_frame = pd.DataFrame(columns=["标的证券代码", "标的证券简称", "融资买入额", "融资余额"])
        normalized_frames: list[pd.DataFrame] = []
        if not sz_frame.empty:
            item = sz_frame.rename(columns={"证券代码": "代码", "证券简称": "名称"})
            normalized_frames.append(item[["代码", "名称", "融资买入额", "融资余额"]].copy())
        if not sh_frame.empty:
            item = sh_frame.rename(columns={"标的证券代码": "代码", "标的证券简称": "名称"})
            normalized_frames.append(item[["代码", "名称", "融资买入额", "融资余额"]].copy())
        if normalized_frames:
            merged = pd.concat(normalized_frames, ignore_index=True)
            merged["代码"] = merged["代码"].astype(str).str.zfill(6)
            merged["融资买入额"] = pd.to_numeric(merged["融资买入额"], errors="coerce")
            merged["融资余额"] = pd.to_numeric(merged["融资余额"], errors="coerce")
            return merged, date_str, "pass"
    return pd.DataFrame(columns=["代码", "名称", "融资买入额", "融资余额"]), "", "warn"


def fetch_lhb_frame(run_dt: datetime) -> tuple[pd.DataFrame, str, str]:
    trade_date = market_date(run_dt).strftime("%Y%m%d")
    try:
        frame = ak.stock_lhb_detail_daily_sina(date=trade_date).copy()
    except Exception:
        frame = pd.DataFrame()
    if frame.empty:
        url = "https://vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/lhb/index.phtml"
        params = {"tradedate": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"}
        try:
            headers = {
                "Referer": "https://vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/lhb/index.phtml",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            }
            response = request_with_retries(url, params=params, headers=headers, timeout=20, attempts=3)
            soup = BeautifulSoup(response.text, features="lxml")
            root = soup.find(name="div", attrs={"class": "list"})
            table_nodes = root.find_all(name="table", attrs={"class": "list_table"}) if root else []
        except Exception as exc:
            return pd.DataFrame(columns=["股票代码", "股票名称", "指标"]), "warn", f"latest={market_date(run_dt).isoformat()}; error={type(exc).__name__}: {exc}"
        rows: list[pd.DataFrame] = []
        for table_node in table_nodes:
            table_html = table_node.prettify()
            try:
                main = pd.read_html(StringIO(table_html), header=0, skiprows=1)[0]
                raw_tables = pd.read_html(StringIO(table_html))
            except ValueError:
                continue
            if main.empty:
                continue
            metric_text = ""
            if raw_tables and not raw_tables[0].empty:
                metric_text = str(raw_tables[0].iat[0, 0]).strip()
            if "股票代码" not in main.columns or "股票名称" not in main.columns:
                continue
            body = main.copy()
            body["指标"] = metric_text
            rows.append(body)
        frame = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if frame.empty:
        return pd.DataFrame(columns=["股票代码", "股票名称", "指标"]), "pass", f"latest={market_date(run_dt).isoformat()}; empty"
    code_col = next((item for item in ["股票代码", "证券代码", "代码"] if item in frame.columns), "")
    name_col = next((item for item in ["股票名称", "证券简称", "名称"] if item in frame.columns), "")
    metric_col = next((item for item in ["指标", "上榜原因", "原因"] if item in frame.columns), "")
    if not code_col or not name_col:
        return pd.DataFrame(columns=["股票代码", "股票名称", "指标"]), "warn", f"latest={market_date(run_dt).isoformat()}; missing_columns={','.join(map(str, frame.columns.tolist()))}"
    normalized = pd.DataFrame(
        {
            "股票代码": frame[code_col].astype(str).str.extract(r"(\d+)")[0].fillna("").str.zfill(6),
            "股票名称": frame[name_col].astype(str),
            "指标": frame[metric_col].astype(str) if metric_col else "",
        }
    )
    normalized = normalized[normalized["股票代码"].str.len() == 6].drop_duplicates(subset=["股票代码", "指标"], keep="first")
    return normalized.reset_index(drop=True), "pass", f"latest={market_date(run_dt).isoformat()}; rows={len(normalized.index)}"


def fetch_northbound_frame_once(
    market: str,
    timeout_seconds: int = NORTHBOUND_FETCH_TIMEOUT_SECONDS,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    with tempfile.NamedTemporaryFile(prefix="radar_northbound_", suffix=".json", delete=False) as handle:
        output_path = handle.name
    child_code = """
import json
import akshare as ak
from pathlib import Path

market = {market!r}
output_path = Path({output_path!r})
frame = ak.stock_hsgt_hold_stock_em(market=market, indicator="3日排行")
if frame is None or frame.empty:
    output_path.write_text(json.dumps({{"rows": [], "row_count": 0}}, ensure_ascii=False), encoding="utf-8")
else:
    payload_rows = json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))
    output_path.write_text(json.dumps({{
        "rows": payload_rows,
        "row_count": int(len(frame.index)),
    }}, ensure_ascii=False), encoding="utf-8")
print(json.dumps({{"status": "ok", "output_path": str(output_path), "market": market}}, ensure_ascii=False))
""".format(market=market, output_path=output_path)
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
        return pd.DataFrame(), {
            "status": "timeout",
            "note": f"{market}:TimeoutExpired after {timeout_seconds}s",
            "fetched_count": 0,
        }

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        Path(output_path).unlink(missing_ok=True)
        return pd.DataFrame(), {
            "status": "error",
            "note": f"{market}:child rc={result.returncode}: {stderr[:240]}",
            "fetched_count": 0,
        }

    stdout = (result.stdout or "").strip()
    if not stdout:
        Path(output_path).unlink(missing_ok=True)
        return pd.DataFrame(), {
            "status": "warn",
            "note": f"{market}:empty stdout",
            "fetched_count": 0,
        }
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        Path(output_path).unlink(missing_ok=True)
        return pd.DataFrame(), {
            "status": "error",
            "note": f"{market}:invalid json: {exc}",
            "fetched_count": 0,
        }

    file_path = Path(str(payload.get("output_path", output_path)))
    if not file_path.exists():
        return pd.DataFrame(), {
            "status": "error",
            "note": f"{market}:did not materialize output file",
            "fetched_count": 0,
        }
    try:
        file_payload = json.loads(file_path.read_text(encoding="utf-8"))
    finally:
        file_path.unlink(missing_ok=True)

    frame = pd.DataFrame(file_payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(), {
            "status": "warn",
            "note": f"{market}:empty frame",
            "fetched_count": 0,
        }
    return frame, {
        "status": "pass",
        "note": f"{market}:rows={len(frame.index)}",
        "fetched_count": len(frame.index),
    }


def fetch_northbound_frames(
    run_dt: datetime,
    timeout_seconds: int = NORTHBOUND_FETCH_TIMEOUT_SECONDS,
) -> tuple[pd.DataFrame, bool, str, dict[str, Any]]:
    northbound_frames: list[pd.DataFrame] = []
    northbound_dates: list[datetime.date] = []
    northbound_delta_col = ""
    northbound_ratio_col = ""
    errors: list[str] = []
    fetched_count = 0
    for market in ["沪股通", "深股通"]:
        frame, fetch_meta = fetch_northbound_frame_once(market=market, timeout_seconds=timeout_seconds)
        if fetch_meta.get("status") != "pass":
            errors.append(str(fetch_meta.get("note", f"{market}:unknown")))
            continue
        if frame.empty:
            continue
        frame = frame.copy()
        fetched_count += int(fetch_meta.get("fetched_count", len(frame.index)))
        frame["代码"] = frame["代码"].astype(str).str.zfill(6)
        if not northbound_delta_col:
            for candidate in ["3日增持估计-市值", "今日增持估计-市值"]:
                if candidate in frame.columns:
                    northbound_delta_col = candidate
                    break
        if not northbound_ratio_col:
            for candidate in ["3日增持估计-市值增幅", "今日增持估计-市值增幅"]:
                if candidate in frame.columns:
                    northbound_ratio_col = candidate
                    break
        if northbound_delta_col and northbound_delta_col in frame.columns:
            frame[northbound_delta_col] = pd.to_numeric(frame[northbound_delta_col], errors="coerce")
        if northbound_ratio_col and northbound_ratio_col in frame.columns:
            frame[northbound_ratio_col] = pd.to_numeric(frame[northbound_ratio_col], errors="coerce")
        frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.date
        if pd.notna(frame["日期"]).any():
            northbound_dates.append(max(item for item in frame["日期"].dropna().tolist()))
        northbound_frames.append(frame)

    northbound = pd.concat(northbound_frames, ignore_index=True) if northbound_frames else pd.DataFrame()
    northbound_stale = True
    northbound_latest = ""
    if northbound_dates:
        latest_dt = max(northbound_dates)
        northbound_latest = latest_dt.isoformat()
        northbound_stale = (market_date(run_dt) - latest_dt).days > 30

    status = "warn"
    if not northbound.empty and not northbound_stale:
        status = "pass"
    note_parts = [f"latest={northbound_latest or 'N/A'}"]
    if errors:
        note_parts.append(f"errors={';'.join(errors)}")
    if northbound_stale:
        note_parts.append("stale=true")
    return northbound, northbound_stale, northbound_latest, {
        "status": status,
        "note": "; ".join(note_parts),
        "fetched_count": fetched_count,
    }


def fetch_etf_fund_daily_frame() -> tuple[pd.DataFrame, str, str]:
    cache_path = cache_key_path("etf_daily", datetime.now(MARKET_TZ))
    ttl_minutes = flow_cache_ttl_minutes()
    if is_cache_fresh(cache_path, datetime.now(MARKET_TZ), ttl_minutes):
        cached = read_cached_frame(cache_path)
        if cached is not None and not cached.empty:
            return cached, "pass", f"cache rows={len(cached.index)} ttl={ttl_minutes}m"
    try:
        frame = ak.fund_etf_fund_daily_em().copy()
    except Exception as exc:
        return pd.DataFrame(columns=["基金代码", "基金简称", "增长率", "市价", "折价率"]), "warn", f"error={type(exc).__name__}: {exc}"
    if frame.empty:
        return pd.DataFrame(columns=["基金代码", "基金简称", "增长率", "市价", "折价率"]), "warn", "empty"
    frame["基金代码"] = frame["基金代码"].astype(str)
    for column in ["增长率", "市价", "折价率"]:
        if column in frame.columns:
            frame[column] = (
                frame[column]
                .astype(str)
                .str.replace("%", "", regex=False)
                .str.replace(",", "", regex=False)
            )
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    write_cached_frame(cache_path, frame)
    return frame, "pass", f"rows={len(frame.index)}"


def fetch_sector_flow_frame() -> tuple[pd.DataFrame, str, str]:
    empty = pd.DataFrame(
        columns=["名称", "今日主力净流入-净额", "今日主力净流入-净占比", "今日主力净流入最大股", "sector_net_rank", "sector_ratio_rank"]
    )
    cache_path = cache_key_path("sector_flow", datetime.now(MARKET_TZ))
    ttl_minutes = flow_cache_ttl_minutes()
    if is_cache_fresh(cache_path, datetime.now(MARKET_TZ), ttl_minutes):
        cached = read_cached_frame(cache_path)
        if cached is not None and not cached.empty:
            return cached, "pass", f"cache rows={len(cached.index)} ttl={ttl_minutes}m"
    try:
        frame = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流").copy()
    except Exception as exc:
        fallback_exc = exc
        try:
            fallback = ak.stock_fund_flow_industry(symbol="即时").copy()
        except Exception as fallback_error:
            return empty, "warn", f"error={type(fallback_exc).__name__}: {fallback_exc}; fallback_error={type(fallback_error).__name__}: {fallback_error}"
        if fallback.empty:
            return empty, "warn", f"error={type(fallback_exc).__name__}: {fallback_exc}; fallback_empty"
        fallback["行业"] = fallback["行业"].astype(str)
        fallback["净额"] = pd.to_numeric(fallback["净额"], errors="coerce")
        normalized = pd.DataFrame(
            {
                "名称": fallback["行业"].astype(str),
                "今日主力净流入-净额": fallback["净额"],
                "今日主力净流入-净占比": pd.NA,
                "今日主力净流入最大股": fallback.get("领涨股", pd.Series([""] * len(fallback.index))).astype(str),
            }
        )
        normalized["sector_net_rank"] = percentile(normalized["今日主力净流入-净额"])
        normalized["sector_ratio_rank"] = normalized["sector_net_rank"]
        write_cached_frame(cache_path, normalized)
        return normalized, "pass", f"fallback=stock_fund_flow_industry:即时 rows={len(normalized.index)} primary_error={type(fallback_exc).__name__}"
    if frame.empty:
        return empty, "warn", "empty"
    frame["名称"] = frame["名称"].astype(str)
    frame["今日主力净流入-净额"] = pd.to_numeric(frame["今日主力净流入-净额"], errors="coerce")
    frame["今日主力净流入-净占比"] = pd.to_numeric(frame["今日主力净流入-净占比"], errors="coerce")
    frame["sector_net_rank"] = percentile(frame["今日主力净流入-净额"])
    frame["sector_ratio_rank"] = percentile(frame["今日主力净流入-净占比"])
    write_cached_frame(cache_path, frame)
    return frame, "pass", f"rows={len(frame.index)}"


def fetch_subindustry_flow_frame() -> tuple[pd.DataFrame, str, str]:
    empty = pd.DataFrame(columns=["行业", "净额", "净额_rank", "领涨股"])
    cache_path = cache_key_path("subindustry_flow", datetime.now(MARKET_TZ))
    ttl_minutes = flow_cache_ttl_minutes()
    if is_cache_fresh(cache_path, datetime.now(MARKET_TZ), ttl_minutes):
        cached = read_cached_frame(cache_path)
        if cached is not None and not cached.empty:
            return cached, "pass", f"cache rows={len(cached.index)} ttl={ttl_minutes}m"
    try:
        frame = ak.stock_fund_flow_industry(symbol="即时").copy()
    except Exception as exc:
        return empty, "warn", f"error={type(exc).__name__}: {exc}"
    if frame.empty:
        return empty, "warn", "empty"
    frame["行业"] = frame["行业"].astype(str)
    frame["净额"] = pd.to_numeric(frame["净额"], errors="coerce")
    frame["净额_rank"] = percentile(frame["净额"])
    write_cached_frame(cache_path, frame)
    return frame, "pass", f"rows={len(frame.index)}"


def fetch_stock_connect_market_summary(run_dt: datetime) -> tuple[dict[str, Any], str, str]:
    cache_path = cache_key_path("stock_connect_market", run_dt)
    ttl_minutes = flow_cache_ttl_minutes()
    if is_cache_fresh(cache_path, run_dt, ttl_minutes):
        cached = read_cached_frame(cache_path)
        if cached is not None and not cached.empty:
            payload = {
                str(row.get("source_symbol") or ""): {
                    "latest_date": str(row.get("latest_date") or ""),
                    "net_buy": safe_float(row.get("net_buy")),
                    "buy_turnover": safe_float(row.get("buy_turnover")),
                    "sell_turnover": safe_float(row.get("sell_turnover")),
                }
                for row in cached.to_dict("records")
            }
            return payload, "pass", f"cache rows={len(cached.index)} ttl={ttl_minutes}m"

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for symbol in ["北向资金", "南向资金", "沪股通", "深股通"]:
        try:
            frame = ak.stock_hsgt_hist_em(symbol=symbol).copy()
        except Exception as exc:
            errors.append(f"{symbol}:{type(exc).__name__}")
            continue
        if frame.empty:
            errors.append(f"{symbol}:empty")
            continue
        frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.date
        frame = frame.dropna(subset=["日期"]).sort_values("日期")
        if frame.empty:
            errors.append(f"{symbol}:invalid_dates")
            continue
        latest = frame.iloc[-1]
        rows.append(
            {
                "source_symbol": symbol,
                "latest_date": str(latest.get("日期") or ""),
                "net_buy": safe_float(latest.get("当日成交净买额")),
                "buy_turnover": safe_float(latest.get("买入成交额")),
                "sell_turnover": safe_float(latest.get("卖出成交额")),
            }
        )
    if not rows:
        return {}, "warn", "; ".join(errors) if errors else "empty"
    summary_frame = pd.DataFrame(rows)
    write_cached_frame(cache_path, summary_frame)
    latest_dates = [str(item.get("latest_date") or "") for item in rows if str(item.get("latest_date") or "").strip()]
    latest_date = max(latest_dates) if latest_dates else ""
    note = f"latest={latest_date or 'N/A'}; rows={len(rows)}"
    if errors:
        note = f"{note}; errors={';'.join(errors)}"
    payload = {
        str(row.get("source_symbol") or ""): {
            "latest_date": str(row.get("latest_date") or ""),
            "net_buy": safe_float(row.get("net_buy")),
            "buy_turnover": safe_float(row.get("buy_turnover")),
            "sell_turnover": safe_float(row.get("sell_turnover")),
        }
        for row in rows
    }
    return payload, "pass", note


def _fetch_etf_spot_em_fast() -> pd.DataFrame:
    """
    Fast-path: fetch ETF spot data from East Money in a single large-page request.

    This preserves exact signal semantics because it uses the same data source
    (East Money) and the same fields as ak.fund_etf_spot_em(). The only
    difference is a larger page size, which avoids ~10 sequential paginated
    HTTP requests and cuts latency by an order of magnitude.
    """
    url = "https://88.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "5000",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "wbp2u": "|0|0|0|web",
        "fid": "f12",
        "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
        "fields": "f12,f6,f21",
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data_json = response.json()
    if not data_json.get("data") or not data_json["data"].get("diff"):
        return pd.DataFrame(columns=["代码", "成交额", "流通市值"])
    rows = list(data_json["data"]["diff"].values())
    total = data_json["data"].get("total")
    if total is not None and len(rows) < total:
        raise ValueError(f"Incomplete ETF spot response: expected {total}, got {len(rows)}")
    temp_df = pd.DataFrame(rows)
    temp_df = temp_df.rename(columns={"f12": "代码", "f6": "成交额", "f21": "流通市值"})
    temp_df["代码"] = temp_df["代码"].astype(str)
    temp_df["成交额"] = pd.to_numeric(temp_df["成交额"], errors="coerce")
    temp_df["流通市值"] = pd.to_numeric(temp_df["流通市值"], errors="coerce")
    return temp_df


def _eastmoney_secid_for_etf(code: str) -> str:
    normalized = str(code).strip()
    market = "1" if normalized.startswith(("5", "6")) else "0"
    return f"{market}.{normalized}"


def _fetch_primary_etf_spot_batch() -> pd.DataFrame:
    primary_codes = sorted(
        {
            str(item.get("etf_code", "")).strip()
            for item in load_industry_etf_primary()
            if str(item.get("etf_code", "")).strip()
        }
    )
    if not primary_codes:
        return pd.DataFrame(columns=["代码", "名称", "成交额", "流通市值"])

    url = "https://88.push2.eastmoney.com/api/qt/ulist.np/get"
    rows: list[dict[str, Any]] = []
    for start in range(0, len(primary_codes), 50):
        chunk = primary_codes[start : start + 50]
        params = {
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fields": "f12,f14,f6,f21",
            "secids": ",".join(_eastmoney_secid_for_etf(code) for code in chunk),
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        diff = data.get("diff", []) if isinstance(data, dict) else []
        if isinstance(diff, dict):
            diff = list(diff.values())
        for item in diff:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "代码": str(item.get("f12", "")).strip(),
                    "名称": str(item.get("f14", "")).strip(),
                    "成交额": pd.to_numeric(item.get("f6"), errors="coerce"),
                    "流通市值": pd.to_numeric(item.get("f21"), errors="coerce"),
                }
            )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["代码", "名称", "成交额", "流通市值"])
    frame["代码"] = frame["代码"].astype(str)
    frame = frame.drop_duplicates(subset=["代码"], keep="first").reset_index(drop=True)
    return frame


def fetch_etf_spot_frame() -> tuple[pd.DataFrame, str, str]:
    empty = pd.DataFrame(columns=["代码", "成交额", "流通市值", "etf_turnover_rank"])
    cache_path = cache_key_path("etf_spot", datetime.now(MARKET_TZ))
    ttl_minutes = etf_spot_cache_ttl_minutes()
    if is_cache_fresh(cache_path, datetime.now(MARKET_TZ), ttl_minutes):
        cached = read_cached_frame(cache_path)
        if cached is not None and not cached.empty:
            return cached, "pass", f"cache rows={len(cached.index)} ttl={ttl_minutes}m"
    try:
        frame = _fetch_primary_etf_spot_batch()
        note = f"primary batch rows={len(frame.index)}"
    except Exception:
        try:
            frame = _fetch_etf_spot_em_fast()
            note = f"fast_path rows={len(frame.index)}"
        except Exception:
            try:
                frame = ak.fund_etf_spot_em().copy()
                note = f"fallback rows={len(frame.index)}"
            except Exception as exc:
                cached, cached_path = load_recent_cached_frame("etf_spot")
                if cached is not None and not cached.empty:
                    cached["代码"] = cached["代码"].astype(str)
                    cached["成交额"] = pd.to_numeric(cached["成交额"], errors="coerce")
                    cached["流通市值"] = pd.to_numeric(cached["流通市值"], errors="coerce")
                    cached["etf_turnover_rank"] = percentile(cached["成交额"])
                    return cached, "pass", f"fallback_cache={cached_path.name if cached_path else 'unknown'}; primary_error={type(exc).__name__}"
                return empty, "warn", f"error={type(exc).__name__}: {exc}"
    if frame.empty:
        return empty, "warn", "empty"
    frame["代码"] = frame["代码"].astype(str)
    frame["成交额"] = pd.to_numeric(frame["成交额"], errors="coerce")
    frame["流通市值"] = pd.to_numeric(frame["流通市值"], errors="coerce")
    frame["etf_turnover_rank"] = percentile(frame["成交额"])
    write_cached_frame(cache_path, frame)
    return frame, "pass", note


def fetch_etf_scale_context(run_dt: datetime) -> tuple[pd.DataFrame, str, dict[str, dict[str, Any]]]:
    cache_path = cache_key_path("etf_scale", run_dt)
    ttl_minutes = flow_cache_ttl_minutes()
    if is_cache_fresh(cache_path, run_dt, ttl_minutes):
        cached = read_cached_frame(cache_path)
        if cached is not None and not cached.empty:
            health = {
                "akshare:fund_etf_scale_sse": {
                    "source_id": "akshare:fund_etf_scale_sse",
                    "status": "pass",
                    "fetched_count": len(cached.index),
                    "inserted_count": len(cached.index),
                    "error_count": 0,
                    "note": f"cache ttl={ttl_minutes}m",
                },
                "akshare:fund_etf_scale_szse": {
                    "source_id": "akshare:fund_etf_scale_szse",
                    "status": "pass",
                    "fetched_count": len(cached.index),
                    "inserted_count": len(cached.index),
                    "error_count": 0,
                    "note": f"cache ttl={ttl_minutes}m",
                },
            }
            return cached, "pass", health
    frames: list[pd.DataFrame] = []
    health: dict[str, dict[str, Any]] = {}

    latest_sse = pd.DataFrame(columns=["基金代码", "基金份额", "统计日期"])
    latest_sse_date = ""
    prev_sse = pd.DataFrame(columns=["基金代码", "基金份额", "统计日期"])
    prev_sse_date = ""
    for date_str in date_candidates(run_dt, 7):
        try:
            frame = ak.fund_etf_scale_sse(date=date_str).copy()
        except Exception as exc:
            health["akshare:fund_etf_scale_sse"] = {
                "source_id": "akshare:fund_etf_scale_sse",
                "status": "warn",
                "fetched_count": 0,
                "inserted_count": 0,
                "error_count": 1,
                "note": f"date={date_str}; error={type(exc).__name__}: {exc}",
            }
            continue
        if frame.empty or "基金代码" not in frame.columns:
            continue
        frame["基金代码"] = frame["基金代码"].astype(str)
        frame["基金份额"] = pd.to_numeric(frame["基金份额"], errors="coerce")
        frame["统计日期"] = pd.to_datetime(frame["统计日期"], errors="coerce").dt.date
        frame = frame.dropna(subset=["基金份额"]).copy()
        if frame.empty:
            continue
        if latest_sse.empty:
            latest_sse = frame[["基金代码", "基金份额", "统计日期"]].copy()
            latest_sse_date = date_str
        elif prev_sse.empty:
            prev_sse = frame[["基金代码", "基金份额", "统计日期"]].copy()
            prev_sse_date = date_str
            break
    if not latest_sse.empty:
        sse_latest = latest_sse.rename(columns={"基金份额": "最新基金份额", "统计日期": "最新统计日期"})
        if not prev_sse.empty:
            sse_prev = prev_sse.rename(columns={"基金份额": "前次基金份额", "统计日期": "前次统计日期"})
            sse_latest = sse_latest.merge(sse_prev, on="基金代码", how="left")
            sse_latest["基金份额变化率"] = (
                (sse_latest["最新基金份额"] / sse_latest["前次基金份额"] - 1.0) * 100.0
            )
        else:
            sse_latest["前次基金份额"] = pd.NA
            sse_latest["前次统计日期"] = pd.NaT
            sse_latest["基金份额变化率"] = pd.NA
        sse_latest["交易所"] = "SSE"
        frames.append(sse_latest)
        health["akshare:fund_etf_scale_sse"] = {
            "source_id": "akshare:fund_etf_scale_sse",
            "status": "pass",
            "fetched_count": len(latest_sse.index) + len(prev_sse.index),
            "inserted_count": len(sse_latest.index),
            "error_count": 0,
            "note": f"latest={latest_sse_date or 'N/A'}; prev={prev_sse_date or 'N/A'}",
        }
    elif "akshare:fund_etf_scale_sse" not in health:
        health["akshare:fund_etf_scale_sse"] = {
            "source_id": "akshare:fund_etf_scale_sse",
            "status": "warn",
            "fetched_count": 0,
            "inserted_count": 0,
            "error_count": 0,
            "note": "no_recent_sse_scale_snapshot",
        }

    try:
        url = "https://fund.szse.cn/api/report/ShowReport"
        params = {
            "SHOWTYPE": "xlsx",
            "CATALOGID": "1000_lf",
            "TABKEY": "tab1",
            "random": "0.07610353191740105",
        }
        headers = {
            "Referer": "https://fund.szse.cn/marketdata/fundslist/index.html",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/88.0.4324.150 Safari/537.36"
            ),
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive",
        }
        response = request_with_retries(url, params=params, headers=headers, timeout=20, attempts=3)
        szse = pd.read_excel(BytesIO(response.content), engine="openpyxl", dtype={"基金代码": str})
        szse = szse.rename(columns={"当前规模(份)": "基金份额"})
    except Exception as exc:
        cached_frame, cached_path = load_recent_cached_frame("etf_scale")
        cached_szse = None
        if cached_frame is not None and not cached_frame.empty and "交易所" in cached_frame.columns:
            cached_szse = cached_frame[cached_frame["交易所"].astype(str) == "SZSE"].copy()
        if cached_szse is not None and not cached_szse.empty:
            frames.append(cached_szse)
            health["akshare:fund_etf_scale_szse"] = {
                "source_id": "akshare:fund_etf_scale_szse",
                "status": "pass",
                "fetched_count": len(cached_szse.index),
                "inserted_count": len(cached_szse.index),
                "error_count": 0,
                "note": f"fallback_cache={cached_path.name if cached_path else 'unknown'}; primary_error={type(exc).__name__}",
            }
        else:
            health["akshare:fund_etf_scale_szse"] = {
                "source_id": "akshare:fund_etf_scale_szse",
                "status": "warn",
                "fetched_count": 0,
                "inserted_count": 0,
                "error_count": 1,
                "note": f"error={type(exc).__name__}: {exc}",
            }
    else:
        if not szse.empty and "基金代码" in szse.columns:
            szse["基金代码"] = szse["基金代码"].astype(str)
            szse["基金份额"] = szse["基金份额"].astype(str).str.replace(",", "", regex=False)
            szse["基金份额"] = pd.to_numeric(szse["基金份额"], errors="coerce")
            szse = szse.dropna(subset=["基金份额"]).copy()
            szse = szse.rename(columns={"基金份额": "最新基金份额"})
            szse["最新统计日期"] = market_date(run_dt)
            szse["前次基金份额"] = pd.NA
            szse["前次统计日期"] = pd.NaT
            szse["基金份额变化率"] = pd.NA
            szse["交易所"] = "SZSE"
            frames.append(szse[["基金代码", "最新基金份额", "最新统计日期", "前次基金份额", "前次统计日期", "基金份额变化率", "交易所"]].copy())
            health["akshare:fund_etf_scale_szse"] = {
                "source_id": "akshare:fund_etf_scale_szse",
                "status": "pass",
                "fetched_count": len(szse.index),
                "inserted_count": len(szse.index),
                "error_count": 0,
                "note": f"latest={market_date(run_dt).isoformat()}",
            }
        else:
            health["akshare:fund_etf_scale_szse"] = {
                "source_id": "akshare:fund_etf_scale_szse",
                "status": "warn",
                "fetched_count": 0,
                "inserted_count": 0,
                "error_count": 0,
                "note": "empty",
            }

    if not frames:
        return pd.DataFrame(columns=["基金代码", "最新基金份额", "最新统计日期", "前次基金份额", "前次统计日期", "基金份额变化率", "交易所"]), "warn", health

    merged = pd.concat(frames, ignore_index=True)
    merged["基金份额规模分位"] = percentile(merged["最新基金份额"])
    write_cached_frame(cache_path, merged)
    return merged, "pass", health


def build_flow_signal_overlay(registry: list[dict[str, str]], run_dt: datetime) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    overlay = {
        item["industry_id"]: {
            "aux_flow_score": 0.0,
            "sector_flow_score": 0.0,
            "northbound_score": 0.0,
            "margin_score": 0.0,
            "lhb_score": 0.0,
            "etf_flow_score": 0.0,
            "flow_signal_evidence": {},
        }
        for item in registry
    }
    source_health: list[dict[str, Any]] = []

    debug_progress("sector-flow:start")
    sector_flow, sector_flow_status, sector_flow_note = fetch_sector_flow_frame()
    sector_flow_source_id = "akshare:stock_fund_flow_industry" if str(sector_flow_note).startswith("fallback=stock_fund_flow_industry") else "akshare:stock_sector_fund_flow_rank"
    debug_progress(f"sector-flow:done status={sector_flow_status} rows={len(sector_flow.index)}")
    debug_progress("subindustry-flow:start")
    subindustry_flow, subindustry_flow_status, subindustry_flow_note = fetch_subindustry_flow_frame()
    debug_progress(f"subindustry-flow:done status={subindustry_flow_status} rows={len(subindustry_flow.index)}")
    debug_progress("etf-spot:start")
    etf_spot, etf_spot_status, etf_spot_note = fetch_etf_spot_frame()
    debug_progress(f"etf-spot:done status={etf_spot_status} rows={len(etf_spot.index)}")
    debug_progress("etf-daily:start")
    etf_daily, etf_daily_status, etf_daily_note = fetch_etf_fund_daily_frame()
    debug_progress(f"etf-daily:done status={etf_daily_status} rows={len(etf_daily.index)}")
    debug_progress("etf-scale:start")
    etf_scale, etf_scale_status, etf_scale_health = fetch_etf_scale_context(run_dt)
    debug_progress(f"etf-scale:done status={etf_scale_status} rows={len(etf_scale.index)}")

    debug_progress("stock-connect-market:start")
    stock_connect_summary, stock_connect_status, stock_connect_note = fetch_stock_connect_market_summary(run_dt)
    debug_progress(f"stock-connect-market:done status={stock_connect_status} rows={len(stock_connect_summary)}")

    debug_progress("margin:start")
    margin_frame, margin_date, margin_status = first_non_empty_margin_frame(run_dt)
    debug_progress(f"margin:done status={margin_status} rows={len(margin_frame.index)} date={margin_date or 'N/A'}")
    debug_progress("lhb:start")
    lhb_frame, lhb_status, lhb_note = fetch_lhb_frame(run_dt)
    debug_progress(f"lhb:done status={lhb_status} rows={len(lhb_frame.index)}")
    lhb_codes = set(lhb_frame["股票代码"].astype(str).tolist())

    stock_primary_map = {item["industry_id"]: item for item in load_industry_stock_primary()}
    etf_primary_map = {item["industry_id"]: item for item in load_industry_etf_primary()}

    sector_exact_map = {str(item["名称"]): item for item in sector_flow.to_dict("records")}

    source_health.extend(
        [
            {
                "source_id": sector_flow_source_id,
                "status": sector_flow_status,
                "fetched_count": len(sector_flow.index),
                "inserted_count": len(sector_flow.index),
                "error_count": 0 if sector_flow_status == "pass" else 1,
                "note": sector_flow_note,
            },
            {
                "source_id": "akshare:stock_fund_flow_industry",
                "status": subindustry_flow_status,
                "fetched_count": len(subindustry_flow.index),
                "inserted_count": len(subindustry_flow.index),
                "error_count": 0 if subindustry_flow_status == "pass" else 1,
                "note": subindustry_flow_note,
            },
            {
                "source_id": "akshare:fund_etf_spot_em",
                "status": etf_spot_status,
                "fetched_count": len(etf_spot.index),
                "inserted_count": len(etf_spot.index),
                "error_count": 0 if etf_spot_status == "pass" else 1,
                "note": etf_spot_note,
            },
            {
                "source_id": "akshare:fund_etf_fund_daily_em",
                "status": etf_daily_status,
                "fetched_count": len(etf_daily.index),
                "inserted_count": len(etf_daily.index),
                "error_count": 0 if etf_daily_status == "pass" else 1,
                "note": etf_daily_note,
            },
            {
                "source_id": "akshare:stock_hsgt_hist_em",
                "status": stock_connect_status,
                "fetched_count": len(stock_connect_summary),
                "inserted_count": len(stock_connect_summary),
                "error_count": 0 if stock_connect_status == "pass" else 1,
                "note": stock_connect_note,
            },
            {
                "source_id": "akshare:stock_margin_detail",
                "status": margin_status,
                "fetched_count": len(margin_frame.index),
                "inserted_count": len(margin_frame.index),
                "error_count": 0,
                "note": f"latest={margin_date or 'N/A'}",
            },
            {
                "source_id": "akshare:stock_lhb_detail_daily_sina",
                "status": lhb_status,
                "fetched_count": len(lhb_frame.index),
                "inserted_count": len(lhb_frame.index),
                "error_count": 0,
                "note": lhb_note,
            },
        ]
    )
    source_health.extend(etf_scale_health.values())

    debug_progress(f"registry-loop:start count={len(registry)}")
    for item in registry:
        industry_id = item["industry_id"]
        keywords = industry_keywords(item)
        sector_item = sector_exact_map.get(str(item["display_name_cn"]))
        sector_flow_score = 0.0
        sector_net = None
        sector_ratio = None
        sector_leader = ""
        if sector_item:
            sector_flow_score = min(
                1.0,
                (
                    safe_float(sector_item.get("sector_net_rank")) or 0.0
                ) * 0.6
                + (
                    safe_float(sector_item.get("sector_ratio_rank")) or 0.0
                ) * 0.4,
            )
            sector_net = safe_float(sector_item.get("今日主力净流入-净额"))
            sector_ratio = safe_float(sector_item.get("今日主力净流入-净占比"))
            sector_leader = str(sector_item.get("今日主力净流入最大股", ""))
        else:
            sub = subindustry_flow[subindustry_flow["行业"].astype(str).apply(lambda x: any(keyword in x for keyword in keywords))]
            if not sub.empty:
                sector_flow_score = min(1.0, float(pd.to_numeric(sub["净额_rank"], errors="coerce").fillna(0).max()))
                sector_net = safe_float(pd.to_numeric(sub["净额"], errors="coerce").sum())
                leader_row = sub.sort_values(by="净额", ascending=False).iloc[0]
                sector_leader = str(leader_row.get("领涨股", ""))

        stock_item = stock_primary_map.get(industry_id, {})
        stock_code = str(stock_item.get("stock_code", "")).zfill(6) if stock_item.get("stock_code") else ""
        etf_item = etf_primary_map.get(industry_id, {})
        etf_code = str(etf_item.get("etf_code", "")) if etf_item.get("etf_code") else ""

        etf_flow_score = 0.0
        etf_turnover_amount = None
        etf_share_score = 0.0
        etf_share_latest = None
        etf_share_change_pct = None
        etf_discount_rate = None
        if etf_code:
            etf_match = etf_spot[etf_spot["代码"] == etf_code]
            if not etf_match.empty:
                etf_row = etf_match.iloc[0]
                etf_turnover_amount = safe_float(etf_row.get("成交额"))
                etf_flow_score = safe_float(etf_row.get("etf_turnover_rank")) or 0.0
            if not etf_daily.empty:
                etf_daily_match = etf_daily[etf_daily["基金代码"] == etf_code]
                if not etf_daily_match.empty:
                    etf_discount_rate = safe_float(etf_daily_match.iloc[0].get("折价率"))
            if not etf_scale.empty:
                etf_scale_match = etf_scale[etf_scale["基金代码"] == etf_code]
                if not etf_scale_match.empty:
                    etf_scale_row = etf_scale_match.iloc[0]
                    etf_share_latest = safe_float(etf_scale_row.get("最新基金份额"))
                    etf_share_change_pct = safe_float(etf_scale_row.get("基金份额变化率"))
                    share_rank = safe_float(etf_scale_row.get("基金份额规模分位")) or 0.0
                    if etf_share_change_pct is not None and etf_share_change_pct > 0:
                        etf_share_score = min(1.0, share_rank * 0.4 + min(etf_share_change_pct / 5.0, 1.0) * 0.6)
                    else:
                        etf_share_score = share_rank * 0.2

        northbound_score = 0.0
        northbound_delta_value = None

        margin_score = 0.0
        margin_buy_amount = None
        if stock_code and not margin_frame.empty:
            margin_match = margin_frame[margin_frame["代码"] == stock_code]
            if not margin_match.empty:
                margin_buy_amount = safe_float(margin_match.iloc[0].get("融资买入额"))
                margin_balance = safe_float(margin_match.iloc[0].get("融资余额"))
                if margin_buy_amount is not None and margin_balance is not None and margin_balance > 0:
                    margin_score = min(1.0, (margin_buy_amount / margin_balance) * 20.0)

        lhb_score = 0.3 if stock_code and stock_code in lhb_codes else 0.0
        aux_flow_score = round(
            min(
                1.0,
                sector_flow_score * 0.45
                + etf_flow_score * 0.16
                + etf_share_score * 0.08
                + northbound_score * 0.1
                + margin_score * 0.13
                + lhb_score * 0.08,
            ),
            4,
        )

        overlay[industry_id] = {
            "aux_flow_score": aux_flow_score,
            "sector_flow_score": round(sector_flow_score, 4),
            "northbound_score": round(northbound_score, 4),
            "margin_score": round(margin_score, 4),
            "lhb_score": round(lhb_score, 4),
            "etf_flow_score": round(etf_flow_score, 4),
            "etf_share_score": round(etf_share_score, 4),
            "flow_signal_evidence": {
                "sector_main_net_inflow": sector_net,
                "sector_main_net_ratio": sector_ratio,
                "sector_main_flow_leader": sector_leader,
                "etf_proxy_code": etf_code,
                "etf_proxy_turnover_amount": etf_turnover_amount,
                "etf_share_latest": etf_share_latest,
                "etf_share_change_pct": etf_share_change_pct,
                "etf_discount_rate": etf_discount_rate,
                "northbound_latest_date": str((stock_connect_summary.get("北向资金") or {}).get("latest_date") or ""),
                "northbound_stale": False if stock_connect_summary else True,
                "northbound_delta_value": northbound_delta_value,
                "northbound_market_net_buy": safe_float((stock_connect_summary.get("北向资金") or {}).get("net_buy")),
                "southbound_market_net_buy": safe_float((stock_connect_summary.get("南向资金") or {}).get("net_buy")),
                "margin_latest_date": margin_date,
                "margin_buy_amount": margin_buy_amount,
                "lhb_hit": bool(lhb_score > 0),
            },
        }

    debug_progress("registry-loop:done")
    return overlay, source_health
