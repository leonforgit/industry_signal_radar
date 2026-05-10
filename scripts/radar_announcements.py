from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from radar_industry_registry import load_industry_stock_proxy_candidates


MARKET_TZ = ZoneInfo("Asia/Shanghai")
HIGH_WEIGHT_TYPES = {
    "签署重大合同": 0.5,
    "获得补贴（资助）": 0.35,
    "其他增发事项公告": 0.28,
    "调研活动": 0.12,
    "股东增持": 0.32,
    "股权激励": 0.28,
}
TITLE_KEYWORDS = {
    "中标": 0.45,
    "订单": 0.35,
    "签署": 0.28,
    "回购": 0.32,
    "增持": 0.32,
    "预增": 0.4,
    "扭亏": 0.32,
    "合作": 0.18,
    "扩产": 0.24,
    "涨价": 0.4,
}
SIGNAL_TAG_KEYWORDS = {
    "订单中标": ["中标", "订单", "合同", "签署"],
    "业绩改善": ["预增", "扭亏", "增长", "盈喜"],
    "资本开支": ["扩产", "投建", "开工", "技改"],
    "价格变化": ["涨价", "提价"],
    "股东回报": ["回购", "增持"],
    "合作进展": ["合作", "协议", "战略合作", "联合"],
    "补贴资助": ["补贴", "资助"],
}
NOTICE_SYMBOLS = ["重大事项", "持股变动", "财务报告", "融资公告"]
NOTICE_COLUMNS = ["代码", "名称", "公告标题", "公告类型", "公告日期", "网址"]
NOTICE_FETCH_TIMEOUT_SECONDS = 120


def market_date_str(run_dt: datetime) -> str:
    return run_dt.astimezone(MARKET_TZ).strftime("%Y%m%d")


def empty_notice_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=NOTICE_COLUMNS)


def fetch_notice_report_once(symbol: str, date_str: str, timeout_seconds: int = NOTICE_FETCH_TIMEOUT_SECONDS) -> tuple[pd.DataFrame, dict[str, Any]]:
    with tempfile.NamedTemporaryFile(prefix="radar_notice_", suffix=".json", delete=False) as handle:
        output_path = handle.name
    child_code = """
import json
import akshare as ak
from pathlib import Path

symbol = {symbol!r}
date_str = {date_str!r}
output_path = Path({output_path!r})
frame = ak.stock_notice_report(symbol=symbol, date=date_str)
if frame is None or frame.empty:
    output_path.write_text(json.dumps({{"rows": [], "row_count": 0}}, ensure_ascii=False), encoding="utf-8")
else:
    keep_columns = {columns!r}
    payload = frame.loc[:, [column for column in keep_columns if column in frame.columns]].copy()
    output_path.write_text(json.dumps({{
        "rows": payload.to_dict(orient="records"),
        "row_count": int(len(payload.index)),
    }}, ensure_ascii=False), encoding="utf-8")
print(json.dumps({{"status": "ok", "output_path": str(output_path)}}, ensure_ascii=False))
""".format(symbol=symbol, date_str=date_str, output_path=output_path, columns=NOTICE_COLUMNS)
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
        return empty_notice_frame(), {
            "status": "timeout",
            "note": f"stock_notice_report symbol={symbol} timed out after {timeout_seconds}s",
            "used_date": date_str,
            "symbol": symbol,
        }

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        Path(output_path).unlink(missing_ok=True)
        return empty_notice_frame(), {
            "status": "error",
            "note": f"stock_notice_report symbol={symbol} child rc={result.returncode}: {stderr[:240]}",
            "used_date": date_str,
            "symbol": symbol,
        }

    stdout = (result.stdout or "").strip()
    if not stdout:
        Path(output_path).unlink(missing_ok=True)
        return empty_notice_frame(), {
            "status": "warn",
            "note": f"stock_notice_report symbol={symbol} returned empty stdout",
            "used_date": date_str,
            "symbol": symbol,
        }
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        Path(output_path).unlink(missing_ok=True)
        return empty_notice_frame(), {
            "status": "error",
            "note": f"stock_notice_report symbol={symbol} returned invalid json: {exc}",
            "used_date": date_str,
            "symbol": symbol,
        }
    file_path = Path(str(payload.get("output_path", output_path)))
    if not file_path.exists():
        return empty_notice_frame(), {
            "status": "error",
            "note": f"stock_notice_report symbol={symbol} did not materialize output file",
            "used_date": date_str,
            "symbol": symbol,
        }
    try:
        file_payload = json.loads(file_path.read_text(encoding="utf-8"))
    finally:
        file_path.unlink(missing_ok=True)

    frame = pd.DataFrame(file_payload.get("rows", []))
    if frame.empty:
        return empty_notice_frame(), {
            "status": "warn",
            "note": f"stock_notice_report symbol={symbol} returned empty frame",
            "used_date": date_str,
            "symbol": symbol,
        }
    for column in NOTICE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame.loc[:, NOTICE_COLUMNS].copy()
    frame["代码"] = frame["代码"].astype(str).str.zfill(6)
    frame["公告标题"] = frame["公告标题"].astype(str)
    frame["公告类型"] = frame["公告类型"].astype(str)
    return frame, {
        "status": "pass",
        "note": f"stock_notice_report symbol={symbol} rows={len(frame.index)}",
        "used_date": date_str,
        "symbol": symbol,
    }


def fetch_notice_report(run_dt: datetime) -> tuple[pd.DataFrame, str, dict[str, Any]]:
    primary_date = market_date_str(run_dt)
    fallback_date = (run_dt.astimezone(MARKET_TZ) - timedelta(days=1)).strftime("%Y%m%d")
    attempts: list[dict[str, Any]] = []
    for date_str in [primary_date, fallback_date]:
        frames: list[pd.DataFrame] = []
        symbol_attempts: list[dict[str, Any]] = []
        for symbol in NOTICE_SYMBOLS:
            frame, meta = fetch_notice_report_once(symbol, date_str)
            attempts.append(meta)
            symbol_attempts.append(meta)
            if not frame.empty:
                frames.append(frame)
        if frames:
            merged = pd.concat(frames, ignore_index=True)
            merged = merged.drop_duplicates(subset=["代码", "公告标题", "公告类型", "公告日期"], keep="first").reset_index(drop=True)
            meta = {
                "status": "pass",
                "note": f"focused stock_notice_report rows={len(merged.index)} symbols={','.join(NOTICE_SYMBOLS)}",
                "used_date": date_str,
                "symbols": list(NOTICE_SYMBOLS),
                "attempts": symbol_attempts,
            }
            return merged, date_str, meta
    return empty_notice_frame(), primary_date, {
        "status": "warn",
        "note": f"focused stock_notice_report unavailable for primary and fallback dates; symbols={','.join(NOTICE_SYMBOLS)}",
        "used_date": primary_date,
        "attempts": attempts,
        "symbols": list(NOTICE_SYMBOLS),
    }


def announcement_weight(title: str, notice_type: str) -> float:
    score = HIGH_WEIGHT_TYPES.get(str(notice_type), 0.08)
    normalized_title = str(title)
    for keyword, weight in TITLE_KEYWORDS.items():
        if keyword in normalized_title:
            score = max(score, weight)
    return min(1.0, score)


def normalize_title(title: str) -> str:
    normalized = str(title)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"[：:;；,，。、“”\"'（）()【】\[\]\-—_]", "", normalized)
    normalized = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日", "", normalized)
    normalized = re.sub(r"\d+", "", normalized)
    return normalized[:48]


def extract_signal_tags(title: str, notice_type: str) -> list[str]:
    text = f"{notice_type} {title}"
    tags: list[str] = []
    for tag, keywords in SIGNAL_TAG_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    if not tags:
        tags.append("其他公告")
    return tags


def build_cluster_key(code: str, notice_type: str, title: str, signal_tags: list[str]) -> str:
    primary_tag = signal_tags[0] if signal_tags else "其他公告"
    title_stub = normalize_title(title)[:20]
    return "|".join([str(code).zfill(6), str(notice_type), primary_tag, title_stub])


def compress_announcements(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    deduped: list[dict[str, Any]] = []
    seen_exact: set[tuple[str, str, str]] = set()
    for item in events:
        exact_key = (
            str(item.get("stock_code", "")),
            str(item.get("notice_type", "")),
            str(item.get("title", "")),
        )
        if exact_key in seen_exact:
            continue
        seen_exact.add(exact_key)
        deduped.append(item)

    cluster_map: dict[str, dict[str, Any]] = {}
    for item in deduped:
        cluster_key = str(item.get("cluster_key", ""))
        bucket = cluster_map.setdefault(
            cluster_key,
            {
                "source_id": "stock_notice_report",
                "stock_code": str(item.get("stock_code", "")),
                "stock_name": str(item.get("stock_name", "")),
                "notice_type": str(item.get("notice_type", "")),
                "cluster_key": cluster_key,
                "signal_tags": list(item.get("signal_tags", [])),
                "score": 0.0,
                "cluster_size": 0,
                "distinct_titles": 0,
                "titles": [],
                "urls": [],
                "date": str(item.get("date", "")),
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
            },
        )
        bucket["cluster_size"] += 1
        bucket["score"] = max(float(bucket.get("score", 0.0)), float(item.get("score", 0.0)))
        bucket["date"] = max(str(bucket.get("date", "")), str(item.get("date", "")))
        titles = bucket["titles"]
        title = str(item.get("title", ""))
        if title and title not in titles:
            titles.append(title)
        urls = bucket["urls"]
        url = str(item.get("url", ""))
        if url and url not in urls:
            urls.append(url)
        if float(item.get("score", 0.0)) >= float(bucket.get("score", 0.0)):
            bucket["title"] = title or bucket["title"]
            bucket["url"] = url or bucket["url"]
            bucket["stock_name"] = str(item.get("stock_name", "")) or bucket["stock_name"]

    compressed = list(cluster_map.values())
    for item in compressed:
        item["distinct_titles"] = len(item.get("titles", []))
        item["titles"] = item.get("titles", [])[:3]
        item["urls"] = item.get("urls", [])[:3]
    compressed.sort(
        key=lambda item: (
            -float(item.get("score", 0.0)),
            -int(item.get("cluster_size", 0)),
            str(item.get("stock_name", "")),
        )
    )
    distinct_stock_count = len({str(item.get("stock_code", "")) for item in compressed if str(item.get("stock_code", ""))})
    base = sum(float(item.get("score", 0.0)) for item in compressed[:3])
    diversity_bonus = min(0.3, max(0.0, (distinct_stock_count - 1) * 0.08))
    announcement_score = min(1.0, round(base + diversity_bonus, 4))
    return compressed[:5], announcement_score


def build_announcement_overlay(registry: list[dict[str, str]], run_dt: datetime) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    frame, used_date, fetch_meta = fetch_notice_report(run_dt)
    overlay = {
        item["industry_id"]: {
            "announcement_score": 0.0,
            "announcement_events": [],
        }
        for item in registry
    }
    candidates = load_industry_stock_proxy_candidates()
    tracked_codes = {
        str(item.get("stock_code", "")).zfill(6): item
        for item in candidates
        if str(item.get("stock_code", "")).strip()
    }

    matched_count = 0
    for _, row in frame.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        stock_item = tracked_codes.get(code)
        if stock_item is None:
            continue
        matched_count += 1
        industry_id = str(stock_item["industry_id"])
        score = announcement_weight(str(row.get("公告标题", "")), str(row.get("公告类型", "")))
        payload = {
            "source_id": "stock_notice_report",
            "stock_code": code,
            "stock_name": str(row.get("名称", "")),
            "title": str(row.get("公告标题", "")),
            "notice_type": str(row.get("公告类型", "")),
            "date": str(row.get("公告日期", used_date)),
            "url": str(row.get("网址", "")),
            "score": round(score, 4),
            "signal_tags": extract_signal_tags(str(row.get("公告标题", "")), str(row.get("公告类型", ""))),
        }
        payload["cluster_key"] = build_cluster_key(
            code=code,
            notice_type=payload["notice_type"],
            title=payload["title"],
            signal_tags=payload["signal_tags"],
        )
        bucket = overlay[industry_id]["announcement_events"]
        if payload not in bucket:
            bucket.append(payload)

    for value in overlay.values():
        compressed, score = compress_announcements(value["announcement_events"])
        value["announcement_events"] = compressed
        value["announcement_score"] = score

    meta = {
        "source_id": "stock_notice_report",
        "used_date": used_date,
        "fetched_count": len(frame.index),
        "matched_count": matched_count,
        "fetch_status": str(fetch_meta.get("status", "warn")),
        "fetch_note": str(fetch_meta.get("note", "")),
    }
    source_health = [
        {
            "source_id": "akshare:stock_notice_report",
            "status": "pass" if str(fetch_meta.get("status", "warn")) == "pass" and not frame.empty else str(fetch_meta.get("status", "warn")),
            "fetched_count": len(frame.index),
            "inserted_count": matched_count,
            "error_count": 0,
            "note": f"used_date={used_date}; {fetch_meta.get('note', '')}",
        }
    ]
    return overlay, meta, source_health
