from __future__ import annotations

import json
from datetime import datetime, timedelta
import re
from pathlib import Path
from typing import Any

from radar_company_mapping import ensure_company_mapping_cache
from radar_industry_registry import load_industry_registry


ROOT = Path(__file__).resolve().parent.parent
OVERRIDES_PATH = ROOT / "data" / "company_mapping_overrides.json"

COMPANY_NORMALIZE_RE = re.compile(r"\s+")
INLINE_SYMBOL_RE = re.compile(r"(?:SH|SZ|BJ|HK)\d{4,6}|\d{4,6}\.(?:SH|SZ|SS|BJ|HK)")
NON_TRADING_KEYWORDS = ("停牌", "停牌核查", "继续停牌")


def normalize_company_name(value: Any) -> str:
    text = str(value or "").strip().strip("“”\"'")
    text = COMPANY_NORMALIZE_RE.sub("", text)
    text = re.sub(r"[（(].*?[）)]", "", text)
    return text.strip()


def stock_code_to_market_symbol(raw_value: Any) -> str:
    text = str(raw_value or "").strip().upper().replace(" ", "")
    if not text:
        return ""
    if re.fullmatch(r"\d{6}\.(SH|SZ|SS)", text):
        return text.replace(".SS", ".SH")
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", text):
        return f"{text[2:]}.{text[:2]}"
    if re.fullmatch(r"\d{6}\.BJ", text):
        return text
    if re.fullmatch(r"\d{6}", text):
        if text.startswith(("4", "8", "920")):
            return f"{text}.BJ"
        exchange = "SH" if text.startswith(("5", "6", "9")) else "SZ"
        return f"{text}.{exchange}"
    if re.fullmatch(r"HK\d{5}", text):
        return f"{text[2:]}.HK"
    if re.fullmatch(r"\d{5}\.HK", text):
        return text
    if re.fullmatch(r"\d{4}\.HK", text):
        return f"0{text}"
    if re.fullmatch(r"\d{5}", text) and text.startswith("0"):
        return f"{text}.HK"
    return ""


def extract_inline_market_symbol(text: Any) -> str:
    content = str(text or "").strip()
    if not content:
        return ""
    for raw in INLINE_SYMBOL_RE.findall(content):
        symbol = stock_code_to_market_symbol(raw)
        if symbol:
            return symbol
    return ""


def load_company_mapping_overrides() -> list[dict[str, Any]]:
    if not OVERRIDES_PATH.exists():
        return []
    try:
        payload = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [row for row in payload if isinstance(row, dict)]


def load_company_mapping_index() -> dict[str, dict[str, str]]:
    payload = ensure_company_mapping_cache(
        load_industry_registry(),
        build_if_missing=False,
        refresh_stale=False,
    )
    index: dict[str, dict[str, str]] = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        name = normalize_company_name(row.get("company_name"))
        if not name or name in index:
            continue
        stock_code = str(row.get("stock_code") or "").strip()
        index[name] = {
            "company_name": str(row.get("company_name") or "").strip(),
            "stock_code": stock_code,
            "market_symbol": stock_code_to_market_symbol(stock_code),
            "industry_id": str(row.get("industry_id") or "").strip(),
            "industry_label": str(row.get("industry_label") or "").strip(),
        }
    for row in load_company_mapping_overrides():
        company_name = str(row.get("company_name") or "").strip()
        stock_code = str(row.get("stock_code") or "").strip()
        aliases = [part.strip() for part in str(row.get("aliases") or "").split("|") if part.strip()]
        payload = {
            "company_name": company_name,
            "stock_code": stock_code,
            "market_symbol": stock_code_to_market_symbol(stock_code),
            "industry_id": str(row.get("industry_id") or "").strip(),
            "industry_label": str(row.get("industry_label") or "").strip(),
        }
        for raw_name in [company_name, *aliases]:
            name = normalize_company_name(raw_name)
            if name:
                index[name] = payload
    return index


def candidate_primary_titles(candidate: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for event in candidate.get("shared_feed_events") or []:
        if not isinstance(event, dict):
            continue
        for key in ("event_title", "title", "headline", "summary"):
            title = str(event.get(key) or "").strip()
            if title and title not in titles:
                titles.append(title)
    return titles


def candidate_event_dates(candidate: dict[str, Any]) -> list[str]:
    dates: list[str] = []
    for event in candidate.get("shared_feed_events") or []:
        if not isinstance(event, dict):
            continue
        for key in ("published_at", "event_sample_date", "as_of_date"):
            value = str(event.get(key) or "").strip()
            if value:
                date_text = value[:10]
                if date_text and date_text not in dates:
                    dates.append(date_text)
    return dates


def target_date_tokens(target_date: str) -> set[str]:
    text = str(target_date or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return set()
    _, month, day = text.split("-")
    month_int = int(month)
    day_int = int(day)
    return {
        text,
        f"{month_int}月{day_int}日",
        f"{month_int}月{day_int}日起",
        f"{month_int}月{day_int}日开市",
        f"{month_int}月{day_int}日开市起",
        f"{month_int}月{day_int}日起停牌",
    }


def candidate_non_trading_context(candidate: dict[str, Any], *, target_date: str) -> dict[str, Any]:
    titles = candidate_primary_titles(candidate)
    if not titles:
        return {"expected_non_trading_on_target_date": False, "non_trading_reason": ""}
    target_tokens = target_date_tokens(target_date)
    event_dates = candidate_event_dates(candidate)
    previous_calendar_date = ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(target_date or "").strip()[:10]):
        previous_calendar_date = str((datetime.fromisoformat(str(target_date)[:10]).date() - timedelta(days=1)).isoformat())
    for title in titles:
        text = str(title or "").strip()
        if not text or not any(keyword in text for keyword in NON_TRADING_KEYWORDS):
            continue
        if any(token and token in text for token in target_tokens):
            return {
                "expected_non_trading_on_target_date": True,
                "non_trading_reason": clean_non_trading_reason(text),
            }
        if "明起停牌" in text or "明日停牌" in text:
            if previous_calendar_date and previous_calendar_date in event_dates:
                return {
                    "expected_non_trading_on_target_date": True,
                    "non_trading_reason": clean_non_trading_reason(text),
                }
    return {"expected_non_trading_on_target_date": False, "non_trading_reason": ""}


def clean_non_trading_reason(text: Any) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value[:120]


def resolve_candidate_market_symbol(
    candidate: dict[str, Any],
    *,
    mapping_index: dict[str, dict[str, str]],
) -> dict[str, str]:
    company_name = str(candidate.get("radar_object_name") or "").strip()
    normalized_name = normalize_company_name(company_name)
    direct_stock_code = str(candidate.get("stock_code") or "").strip()
    direct_symbol = stock_code_to_market_symbol(direct_stock_code)
    mapping_hit = mapping_index.get(normalized_name) if normalized_name else None
    mapped_stock_code = str((mapping_hit or {}).get("stock_code") or "").strip()
    mapped_symbol = str((mapping_hit or {}).get("market_symbol") or "").strip()
    inline_symbol = ""
    for title in candidate_primary_titles(candidate):
        inline_symbol = extract_inline_market_symbol(title)
        if inline_symbol:
            break
    market_symbol = direct_symbol or mapped_symbol or inline_symbol
    stock_code = direct_stock_code or mapped_stock_code
    return {
        "company_name": company_name,
        "normalized_name": normalized_name,
        "stock_code": stock_code,
        "market_symbol": market_symbol,
        "industry_id": str((mapping_hit or {}).get("industry_id") or "").strip(),
        "industry_label": str((mapping_hit or {}).get("industry_label") or "").strip(),
    }


def extract_company_targets(candidate_pool_payload: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    mapping_index = load_company_mapping_index()
    resolved: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    seen_symbols: set[str] = set()
    seen_names: set[str] = set()
    target_date = str(
        candidate_pool_payload.get("market_sample_date")
        or candidate_pool_payload.get("expected_sample_date")
        or candidate_pool_payload.get("as_of_date")
        or ""
    )[:10]
    for candidate in candidate_pool_payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("radar_object_type") or "").strip() != "company":
            continue
        item = resolve_candidate_market_symbol(candidate, mapping_index=mapping_index)
        company_name = item.get("company_name") or ""
        market_symbol = item.get("market_symbol") or ""
        if market_symbol:
            if market_symbol in seen_symbols:
                continue
            seen_symbols.add(market_symbol)
            non_trading = candidate_non_trading_context(candidate, target_date=target_date)
            resolved.append(
                {
                    "candidate_id": str(candidate.get("candidate_id") or ""),
                    "radar_object_name": company_name,
                    "stock_code": item.get("stock_code") or "",
                    "market_symbol": market_symbol,
                    "industry_id": item.get("industry_id") or "",
                    "industry_label": item.get("industry_label") or "",
                    "expected_non_trading_on_target_date": bool(non_trading.get("expected_non_trading_on_target_date")),
                    "non_trading_reason": str(non_trading.get("non_trading_reason") or ""),
                }
            )
            continue
        normalized_name = item.get("normalized_name") or ""
        if normalized_name and normalized_name not in seen_names:
            seen_names.add(normalized_name)
            unresolved.append(
                {
                    "candidate_id": str(candidate.get("candidate_id") or ""),
                    "radar_object_name": company_name,
                    "stock_code": item.get("stock_code") or "",
                    "reason": "unresolved_market_symbol",
                }
            )
    return resolved, unresolved
