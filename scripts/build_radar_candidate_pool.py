#!/usr/bin/env python3
"""Build the live Radar candidate pool from canonical news feeds plus canonical market evidence."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

from radar_company_mapping import ensure_company_mapping_cache
from radar_company_targets import extract_inline_market_symbol
from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_freshness_utils import (
    DEFAULT_MARKET_OPEN_TIME,
    DEFAULT_MARKET_SAMPLE_READY_TIME,
    DEFAULT_MARKET_TZ,
    expected_sample_date as compute_expected_sample_date,
)
from radar_industry_registry import load_industry_etf_primary, load_industry_registry, load_industry_stock_primary
from radar_market_sidecar import load_canonical_industry_market_index, resolve_market_path
from radar_shared_news import build_registry_alias_map, load_json_file, resolve_industry_id, shared_news_config


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "output" / "snapshots"
DEFAULT_LATEST_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_candidate_pool_latest.json"
DEFAULT_SHARED_NEWS_MIRROR_DIR = ROOT / "output" / "sidecars" / "news_event_hub" / "consumer_exports"
DEFAULT_SOURCE_READINESS_JSON = ROOT / "output" / "reports" / "radar_source_readiness_latest.json"
DEFAULT_MARKET_MIRROR_DIR = ROOT / "output" / "sidecars" / "market"

SUPPORTED_OBJECT_TYPES = {
    "industry",
    "macro",
    "company",
    "special_situation",
    "watchlist_priority_change",
}
HARD_EVENT_TYPES = {
    "earnings_guidance",
    "deal_mna",
    "earnings_preannounce",
    "earnings_report",
    "capital_markets",
    "shareholder_support",
    "product_launch",
    "order_project",
    "approval_registration",
    "litigation_regulatory",
}
MARKET_PROXY_THEME_MAP = {
    "hog_cycle": "猪周期",
    "broker_cycle": "券商交易景气",
    "utility_electricity": "用电景气",
    "shipping_cycle": "航运景气",
}
LOW_QUALITY_SOURCE_IDS = {
    "serpstack_company_discovery_optional",
    "reddit_tracked_search",
}
LOW_QUALITY_SOURCE_FAMILIES = {
    "search:serpstack",
    "forum:reddit",
}
INSTITUTION_ENTITY_KEYWORDS = (
    "人民银行",
    "证监会",
    "财政部",
    "发改委",
    "国务院",
    "世界银行",
    "开发银行",
    "国际货币基金组织",
    "基金组织",
    "监管局",
    "工信部",
    "商务部",
    "海关",
    "国家能源局",
    "市场监管总局",
    "联合声明",
    "署长",
    "委员会",
    "政府",
)
TRUSTED_CN_SOURCE_FAMILIES = {
    "social:xueqiu",
    "social:weibo",
}
TRUSTED_CN_DOMAINS = (
    "xueqiu.com",
    "eastmoney.com",
    "10jqka.com.cn",
    "cls.cn",
    "cnstock.com",
    "stcn.com",
    "caixin.com",
    "thepaper.cn",
    "sina.com.cn",
    "qq.com",
    "163.com",
)
OBVIOUS_NOISE_TITLE_PATTERNS = (
    "vendor list",
    "personal new record",
    "tap me +1",
    "labubuswap",
    "[h]",
    "[w]",
)
COMPANY_SUFFIX_HINTS = (
    "科技",
    "股份",
    "集团",
    "药业",
    "证券",
    "银行",
    "能源",
    "电气",
    "电子",
    "通信",
    "材料",
    "医疗",
    "生物",
    "控股",
    "汽车",
    "公司",
)
COMPANY_NAME_NOISE_TOKENS = (
    "这家公司",
    "被曝",
    "欲与",
    "携手",
    "签署",
    "关于",
    "本次",
    "资产注入",
    "零售服务商",
    "成立合资",
    "战略合作",
    "董事长",
    "CEO",
    "布局",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Optional dated candidate pool output path.")
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT, help="Latest candidate pool output path.")
    return parser.parse_args()


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def event_title(event: dict[str, Any]) -> str:
    return str(event.get("event_title") or event.get("title") or "").strip()


def normalize_text_key(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return text


def event_dedupe_key(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "").strip()
    if event_id:
        return f"id:{event_id}"
    title = normalize_text_key(event_title(event))
    entity = normalize_text_key(event.get("primary_entity") or "")
    published = str(event.get("published_at") or "").strip()[:10]
    return f"title:{title}|entity:{entity}|date:{published}"


def event_sort_tuple(event: dict[str, Any]) -> tuple[float, str]:
    published = str(event.get("published_at") or "").strip()
    return (float(event.get("score") or 0.0), published)


def dedupe_feed_events(events: list[dict[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        key = event_dedupe_key(event)
        existing = deduped.get(key)
        if existing is None or event_sort_tuple(event) > event_sort_tuple(existing):
            deduped[key] = event
    ordered = sorted(deduped.values(), key=event_sort_tuple, reverse=True)
    if limit is not None:
        ordered = ordered[:limit]
    return ordered


def load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def configured_market_manifest_path() -> Path:
    cfg = load_config_section(DEFAULT_CONFIG_PATH, "canonical_market_substrate")
    mirror_dir = ROOT / str(cfg.get("mirror_dir") or DEFAULT_MARKET_MIRROR_DIR.relative_to(ROOT))
    return resolve_market_path(cfg.get("manifest_path"), mirror_dir=mirror_dir)


def resolve_price_path(raw_path: Any, *, mirror_dir: Path | None = None) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        return Path("")
    candidate = Path(text).expanduser()
    if candidate.exists():
        return candidate
    if candidate.is_absolute() and text.startswith("/opt/quant-runtime/data_substrate/equity_prices/"):
        active_mirror_dir = mirror_dir or ROOT / "output" / "sidecars" / "equity_prices"
        return active_mirror_dir / candidate.name
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def latest_price_db_trade_date() -> str:
    cfg = load_config_section(DEFAULT_CONFIG_PATH, "price_sidecar")
    path = resolve_price_path(cfg.get("equity_price_db_path"))
    if not path.exists():
        return ""
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT MAX(trade_date) FROM equity_price_daily").fetchone()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    return str((row or [""])[0] or "").strip()[:10]


def latest_price_csv_date() -> str:
    cfg = load_config_section(DEFAULT_CONFIG_PATH, "price_sidecar")
    path = resolve_price_path(
        cfg.get("company_price_csv_path"),
        mirror_dir=ROOT / "output" / "sidecars" / "equity_prices",
    )
    if not path.exists():
        return ""
    latest = ""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                value = str(row.get("as_of_date") or "").strip()[:10]
                if value and value > latest:
                    latest = value
    except OSError:
        return ""
    return latest


def latest_price_sample_date() -> str:
    return latest_price_db_trade_date() or latest_price_csv_date()


def infer_market_sample_dates(
    *,
    run_dt: datetime,
    market_index: dict[str, dict[str, Any]],
) -> tuple[str, str, str]:
    index_dates = {
        str(item.get("as_of_date") or "").strip()
        for item in market_index.values()
        if isinstance(item, dict) and str(item.get("as_of_date") or "").strip()
    }

    readiness = load_json_dict(DEFAULT_SOURCE_READINESS_JSON)
    manifest = load_json_dict(configured_market_manifest_path())
    source_cfg = load_config_section(DEFAULT_CONFIG_PATH, "source_readiness")
    market_tz = str(source_cfg.get("market_tz") or DEFAULT_MARKET_TZ)
    market_sample_ready_time = str(
        source_cfg.get("market_sample_ready_time")
        or source_cfg.get("market_open_time")
        or DEFAULT_MARKET_SAMPLE_READY_TIME
    )
    computed_expected = compute_expected_sample_date(
        {"market_tz": market_tz, "market_sample_ready_time": market_sample_ready_time},
        market_tz=market_tz,
        market_open_time=market_sample_ready_time,
        now=run_dt,
    ).isoformat()
    expected_sample_date = (
        computed_expected
        or str(readiness.get("expected_sample_date") or "").strip()
    )
    manifest_latest_trade_date = str(manifest.get("latest_trade_date") or "").strip()
    latest_index_date = max(index_dates) if index_dates else ""
    latest_price_date = latest_price_sample_date()
    market_sample_date = (
        latest_price_date
        or manifest_latest_trade_date
        or latest_index_date
        or str((readiness.get("market") or {}).get("latest_trade_date") or "").strip()
        or expected_sample_date
    )
    market_payload = readiness.get("market") or {}
    latest_trade_date = str((market_payload.get("latest_trade_date") if isinstance(market_payload, dict) else "") or "").strip()
    if not market_sample_date and latest_trade_date:
        market_sample_date = latest_trade_date
    if not market_sample_date:
        market_sample_date = run_dt.date().isoformat()
    return market_sample_date, expected_sample_date or market_sample_date, market_sample_ready_time


def supporting_articles(event: dict[str, Any]) -> list[dict[str, Any]]:
    rows = event.get("supporting_articles") or []
    return [row for row in rows if isinstance(row, dict)]


def source_families(event: dict[str, Any]) -> set[str]:
    return {
        str(article.get("source_family") or "").strip()
        for article in supporting_articles(event)
        if str(article.get("source_family") or "").strip()
    }


def source_ids(event: dict[str, Any]) -> set[str]:
    return {
        str(article.get("source_id") or "").strip()
        for article in supporting_articles(event)
        if str(article.get("source_id") or "").strip()
    }


def source_domains(event: dict[str, Any]) -> set[str]:
    domains: set[str] = set()
    for article in supporting_articles(event):
        url = str(article.get("canonical_url") or "").strip()
        if not url:
            continue
        host = urlparse(url).hostname or ""
        host = host.lower().strip()
        if host.startswith("www."):
            host = host[4:]
        if host:
            domains.add(host)
    return domains


def entity_rows(event: dict[str, Any], entity_type: str) -> list[dict[str, Any]]:
    payload = event.get("entities_by_type") or {}
    rows = payload.get(entity_type) or []
    return [row for row in rows if isinstance(row, dict)]


def entity_names(event: dict[str, Any], entity_type: str) -> list[str]:
    ordered: list[str] = []
    for row in entity_rows(event, entity_type):
        name = str(row.get("entity_name") or "").strip()
        if name and name not in ordered:
            ordered.append(name)
    if entity_type == "company":
        for raw in [str(event.get("primary_entity") or "").strip(), *(str(x).strip() for x in (event.get("companies") or []) if str(x).strip())]:
            if raw and raw not in ordered:
                ordered.append(raw)
    if entity_type == "industry":
        for raw in [str(event.get("primary_industry") or "").strip(), *(str(x).strip() for x in (event.get("industries") or []) if str(x).strip())]:
            if raw and raw not in ordered:
                ordered.append(raw)
    if entity_type == "macro_theme":
        for raw in [str(x).strip() for x in (event.get("macro_themes") or []) if str(x).strip()]:
            if raw and raw not in ordered:
                ordered.append(raw)
    return ordered


def normalize_company_candidate_name(name: Any) -> str:
    text = str(name or "").strip().strip("“”\"'")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[（(].*?[）)]", "", text)
    return text.strip()


def extract_company_names_from_title(title: str) -> list[str]:
    matches = re.findall(r"\$([\u4e00-\u9fffA-Za-z0-9]+?)\((?:SH|SZ|BJ|HK)\d{4,6}\)\$", str(title or ""))
    names: list[str] = []
    for raw in matches:
        normalized = normalize_company_candidate_name(raw)
        if normalized and normalized not in names:
            names.append(normalized)
    return names


def load_company_name_index(registry: list[dict[str, str]]) -> set[str]:
    payload = ensure_company_mapping_cache(
        registry,
        build_if_missing=False,
        refresh_stale=False,
    )
    names: set[str] = set()
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        normalized = normalize_company_candidate_name(row.get("company_name"))
        if normalized:
            names.add(normalized)
    return names


def is_usable_company_candidate_name(
    name: str,
    *,
    known_company_names: set[str],
) -> bool:
    normalized = normalize_company_candidate_name(name)
    if not normalized or not contains_cjk(normalized):
        return False
    if normalized in known_company_names:
        return True
    if any(token in normalized for token in COMPANY_NAME_NOISE_TOKENS):
        return False
    if "与" in normalized:
        return False
    if len(normalized) < 3 or len(normalized) > 8:
        return False
    return normalized.endswith(COMPANY_SUFFIX_HINTS)


def top_entity_mapping_confidence(event: dict[str, Any], entity_type: str) -> float:
    confidence = 0.0
    for row in entity_rows(event, entity_type):
        try:
            confidence = max(confidence, float(row.get("mapping_confidence") or 0.0))
        except (TypeError, ValueError):
            continue
    return confidence


def obvious_noise_title(title: str) -> bool:
    lowered = str(title or "").lower()
    if not lowered:
        return False
    if any(token in lowered for token in OBVIOUS_NOISE_TITLE_PATTERNS):
        return True
    if re.search(r"^\[[a-z]{2,}-[a-z]{2,}\]\s*\[h\]", lowered):
        return True
    return False


def has_trusted_market_source(event: dict[str, Any]) -> bool:
    families = source_families(event)
    domains = source_domains(event)
    if any(family in TRUSTED_CN_SOURCE_FAMILIES for family in families):
        return True
    for domain in domains:
        if any(domain == trusted or domain.endswith(f".{trusted}") for trusted in TRUSTED_CN_DOMAINS):
            return True
    if families:
        return any(family not in LOW_QUALITY_SOURCE_FAMILIES for family in families)
    return False


def only_low_quality_sources(event: dict[str, Any]) -> bool:
    families = source_families(event)
    ids = source_ids(event)
    if not families and not ids:
        return True
    return (
        (not families or all(family in LOW_QUALITY_SOURCE_FAMILIES for family in families))
        and (not ids or all(source_id in LOW_QUALITY_SOURCE_IDS for source_id in ids))
    )


def has_cjk_anchor(event: dict[str, Any]) -> bool:
    if contains_cjk(event_title(event)):
        return True
    for key in ("primary_industry", "primary_entity"):
        if contains_cjk(str(event.get(key) or "")):
            return True
    for key in ("industries", "companies", "themes", "macro_themes"):
        for item in event.get(key) or []:
            if contains_cjk(str(item or "")):
                return True
    for entity_type in ("company", "industry", "macro_theme"):
        for name in entity_names(event, entity_type):
            if contains_cjk(name):
                return True
    for article in supporting_articles(event):
        if contains_cjk(str(article.get("title") or "")):
            return True
    return False


def is_institution_entity(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    if any(keyword in text for keyword in INSTITUTION_ENTITY_KEYWORDS):
        return True
    return len(text) >= 10 and any(keyword in text for keyword in ("银行", "基金组织", "开发银行", "委员会", "政府", "声明"))


def classify_source_quality(event: dict[str, Any]) -> str:
    if has_trusted_market_source(event):
        return "trusted"
    if only_low_quality_sources(event):
        return "low"
    return "mixed"


def is_relevant_news_event(event: dict[str, Any]) -> bool:
    title = event_title(event)
    cjk_anchor = has_cjk_anchor(event)
    trusted_source = has_trusted_market_source(event)
    low_quality_only = only_low_quality_sources(event)
    event_type = str(event.get("event_type") or "").strip()
    event_state = str(event.get("event_state") or "").strip()
    confirmation_count = int(event.get("confirmation_count") or 0)
    source_family_count = int(event.get("source_family_count") or 0)
    company_names = [name for name in entity_names(event, "company") if contains_cjk(name)]
    industry_names = [name for name in entity_names(event, "industry") if contains_cjk(name)]
    company_confidence = top_entity_mapping_confidence(event, "company")

    if obvious_noise_title(title) and low_quality_only:
        return False
    if low_quality_only and not cjk_anchor and event_type not in HARD_EVENT_TYPES:
        return False
    if company_names:
        if trusted_source and cjk_anchor:
            return True
        if event_type in HARD_EVENT_TYPES and company_confidence >= 0.85:
            return True
        if confirmation_count >= 2 and source_family_count >= 2:
            return True
        return False
    if industry_names:
        if trusted_source and (cjk_anchor or confirmation_count >= 1):
            return True
        if confirmation_count >= 2 and source_family_count >= 2:
            return True
        return False
    if cjk_anchor and trusted_source:
        return True
    if event_type in HARD_EVENT_TYPES and event_state == "confirmed" and confirmation_count >= 2:
        return True
    return False


def is_expandable_industry_event(event: dict[str, Any], *, seeded_industry: bool = False) -> bool:
    event_type = str(event.get("event_type") or "").strip()
    confirmation_count = int(event.get("confirmation_count") or 0)
    source_family_count = int(event.get("source_family_count") or 0)
    trusted_source = has_trusted_market_source(event)
    cjk_anchor = has_cjk_anchor(event)
    score = float(event.get("score") or 0.0)
    primary_entity = str(event.get("primary_entity") or "").strip()
    company_confidence = top_entity_mapping_confidence(event, "company")
    industry_confidence = top_entity_mapping_confidence(event, "industry")
    if industry_confidence >= 0.9 and trusted_source and confirmation_count >= 1:
        return True
    if seeded_industry and trusted_source and cjk_anchor and score >= 40.0:
        return True
    if event_type == "commodity_disruption" and trusted_source and confirmation_count >= 2:
        return True
    if is_institution_entity(primary_entity):
        return False
    if company_confidence >= 0.75:
        return True
    if event_type in {"policy", "regulation"}:
        return trusted_source and confirmation_count >= 2 and source_family_count >= 2 and industry_confidence >= 0.85
    return False


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_sidecar_inputs() -> list[dict[str, str]]:
    return [
        {"source_name": "canonical_market_substrate", "signal_family": "industry_flow"},
        {"source_name": "canonical_market_substrate", "signal_family": "industry_proxy"},
    ]


def market_sidecar_evidence(market_context: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    aux_flow_score = market_context.get("aux_flow_score")
    if aux_flow_score is not None:
        items.append({"source_name": "canonical_market_substrate.industry_flow", "signal_value": float(aux_flow_score)})
    proxy_score = market_context.get("fundamental_proxy_score")
    if proxy_score is not None:
        items.append({"source_name": "canonical_market_substrate.industry_proxy", "signal_value": float(proxy_score)})
    return items


def normalize_object_type(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if value in SUPPORTED_OBJECT_TYPES:
        return value
    aliases = {
        "industry_event": "industry",
        "macro_event": "macro",
        "company_event": "company",
        "institution": "special_situation",
    }
    mapped = aliases.get(value)
    return mapped if mapped in SUPPORTED_OBJECT_TYPES else None


def event_record(item: dict[str, Any], *, source_name: str) -> dict[str, Any]:
    title = str(item.get("title") or "").strip()
    return {
        "source": source_name,
        "source_id": str(item.get("source_id") or source_name).strip() or source_name,
        "event_id": str(item.get("event_id") or "").strip(),
        "event_type": str(item.get("event_type") or "").strip(),
        "event_title": title,
        "event_state": str(item.get("event_state") or "").strip(),
        "novelty_state": str(item.get("novelty_state") or "").strip(),
        "published_at": str(item.get("last_seen_at") or item.get("published_at") or "").strip(),
        "score": float(item.get("score") or 0.0),
        "confirmation_count": int(item.get("confirmation_count") or 0),
        "calibrated_confirmation": float(item.get("calibrated_confirmation") or 0.0),
        "source_family_count": int(item.get("source_family_count") or 0),
        "signal_platform_count": int(item.get("signal_platform_count") or 0),
        "source_count": int(item.get("source_count") or 0),
        "uncertainty": float(item.get("uncertainty") or 0.0),
        "primary_industry": str(item.get("primary_industry") or "").strip(),
        "primary_entity": str(item.get("primary_entity") or "").strip(),
        "industries": [str(x).strip() for x in (item.get("industries") or []) if str(x).strip()],
        "companies": [str(x).strip() for x in (item.get("companies") or []) if str(x).strip()],
        "themes": [str(x).strip() for x in (item.get("themes") or []) if str(x).strip()],
        "macro_themes": [str(x).strip() for x in (item.get("macro_themes") or []) if str(x).strip()],
        "entities_by_type": item.get("entities_by_type") or {},
        "supporting_articles": item.get("supporting_articles") or [],
        "followup_path": item.get("followup_path") or [],
        "opportunity_bucket": str(item.get("opportunity_bucket") or "").strip(),
        "portfolio_relevance": str(item.get("portfolio_relevance") or "").strip(),
        "watchlist_relevance": str(item.get("watchlist_relevance") or "").strip(),
        "thesis_impact": str(item.get("thesis_impact") or "").strip(),
    }


def iter_event_payloads(feed_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("top_events", "macro_events", "recent_events"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        for event in raw:
            if isinstance(event, dict):
                items.append(event_record(event, source_name=feed_name))
    buckets = payload.get("opportunity_buckets")
    if isinstance(buckets, dict):
        for bucket_items in buckets.values():
            if not isinstance(bucket_items, list):
                continue
            for event in bucket_items:
                if isinstance(event, dict):
                    items.append(event_record(event, source_name=feed_name))
    return items


def build_industry_seed_map(
    registry: list[dict[str, str]],
    industry_feed: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    alias_map = build_registry_alias_map(registry, configured_aliases=shared_news_config().get("industry_aliases"))
    seeds: dict[str, dict[str, Any]] = {}
    for item in industry_feed.get("industries", []) or []:
        if not isinstance(item, dict):
            continue
        feed_industry = str(item.get("industry") or "").strip()
        industry_id = resolve_industry_id(feed_industry, registry, alias_map)
        if not industry_id:
            continue
        bucket = seeds.setdefault(
            industry_id,
            {
                "feed_industry_name": feed_industry,
                "shared_news_score": 0.0,
                "event_count": 0,
                "shared_feed_events": [],
                "theme_overlays": [],
            },
        )
        bucket["shared_news_score"] = max(float(bucket.get("shared_news_score") or 0.0), float(item.get("shared_news_score") or 0.0))
        bucket["event_count"] = max(int(bucket.get("event_count") or 0), int(item.get("event_count") or 0))
        for raw_event in (item.get("shared_events") or []):
            if not isinstance(raw_event, dict):
                continue
            event = event_record(raw_event, source_name="news_event_hub.industry_radar_feed_latest")
            if is_relevant_news_event(event) and is_expandable_industry_event(event, seeded_industry=True):
                bucket["shared_feed_events"].append(event)
        for raw_event in (item.get("policy_articles") or []):
            if not isinstance(raw_event, dict):
                continue
            event = event_record(raw_event, source_name="news_event_hub.industry_radar_feed_latest")
            if is_relevant_news_event(event) and is_expandable_industry_event(event, seeded_industry=True):
                bucket["shared_feed_events"].append(event)
    return seeds


def enrich_industry_seeds_from_extra_feeds(
    seeds: dict[str, dict[str, Any]],
    registry: list[dict[str, str]],
    extra_feed_payloads: list[tuple[str, dict[str, Any]]],
    *,
    known_company_names: set[str],
) -> list[dict[str, Any]]:
    alias_map = build_registry_alias_map(registry, configured_aliases=shared_news_config().get("industry_aliases"))
    generic_candidates: list[dict[str, Any]] = []
    for feed_name, payload in extra_feed_payloads:
        for event in iter_event_payloads(feed_name, payload):
            if not is_relevant_news_event(event):
                continue
            matched_ids: set[str] = set()
            for raw_name in [event.get("primary_industry"), *(event.get("industries") or [])]:
                industry_id = resolve_industry_id(str(raw_name or ""), registry, alias_map)
                if industry_id:
                    matched_ids.add(industry_id)
            industry_seed_allowed = is_expandable_industry_event(event, seeded_industry=bool(matched_ids))
            for industry_id in matched_ids:
                if not industry_seed_allowed:
                    continue
                bucket = seeds.setdefault(
                    industry_id,
                    {
                        "feed_industry_name": "",
                        "shared_news_score": 0.0,
                        "event_count": 0,
                        "shared_feed_events": [],
                        "theme_overlays": [],
                    },
                )
                bucket["shared_news_score"] = max(float(bucket.get("shared_news_score") or 0.0), min(1.0, float(event.get("score") or 0.0) / 100.0))
                bucket["event_count"] = int(bucket.get("event_count") or 0) + 1
                bucket["shared_feed_events"].append(event)
                for theme in [*(event.get("themes") or []), *(event.get("macro_themes") or [])]:
                    if theme and theme not in bucket["theme_overlays"]:
                        bucket["theme_overlays"].append(theme)
            topic_key = str(event.get("event_id") or "").strip()
            if matched_ids:
                continue
            companies = [name for name in entity_names(event, "company") if contains_cjk(name)]
            for title_name in extract_company_names_from_title(event_title(event)):
                if title_name not in companies:
                    companies.append(title_name)
            primary_entity = str(event.get("primary_entity") or "").strip()
            event_type = str(event.get("event_type") or "").strip()
            source_quality = classify_source_quality(event)
            company_mapping_confidence = top_entity_mapping_confidence(event, "company")
            if companies or primary_entity:
                company_name = primary_entity or companies[0]
                company_name = normalize_company_candidate_name(company_name)
                if not is_usable_company_candidate_name(company_name, known_company_names=known_company_names):
                    continue
                inline_symbol = extract_inline_market_symbol(event_title(event))
                # Radar 的 company lane 只接当前可交易标的。没有映射、也没有明确 ticker 的
                # 主体，宁可留在事件层/行业层，也不把 foreign/source/generic entity 混进个股层。
                if company_name not in known_company_names and not inline_symbol:
                    continue
                generic_candidates.append(
                    {
                        "candidate_id": f"company:{company_name}",
                        "radar_object_type": "company",
                        "radar_object_id": f"company:{company_name}",
                        "radar_object_name": company_name,
                        "radar_object_scope": "shared_feed/company",
                        "candidate_origin": ["shared_feed"],
                        "shared_feed_events": [event],
                        "sidecar_evidence": [],
                        "market_context": {},
                        "source_quality": source_quality,
                        "entity_mapping_confidence": round(company_mapping_confidence, 4),
                        "source_priority": float(event.get("score") or 0.0),
                    }
                )
            elif event_type.startswith("macro"):
                name = primary_entity or str(event.get("event_title") or event.get("event_type") or topic_key).strip()
                if not contains_cjk(name):
                    continue
                generic_candidates.append(
                    {
                        "candidate_id": f"macro:{topic_key or name}",
                        "radar_object_type": "macro",
                        "radar_object_id": f"macro:{topic_key or name}",
                        "radar_object_name": name,
                        "radar_object_scope": "shared_feed/macro",
                        "candidate_origin": ["shared_feed"],
                        "shared_feed_events": [event],
                        "sidecar_evidence": [],
                        "market_context": {},
                        "source_quality": source_quality,
                        "source_priority": float(event.get("score") or 0.0),
                    }
                )
    return generic_candidates


def build_industry_market_context(
    *,
    row: dict[str, Any],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    flow_evidence = dict(market_context.get("flow_signal_evidence") or {})
    proxy_evidence = dict(market_context.get("fundamental_proxy_evidence") or {})
    return {
        "as_of_date": str(market_context.get("as_of_date") or ""),
        "shared_news_score": float(row.get("shared_news_score") or 0.0),
        "event_count": int(row.get("event_count") or 0),
        "aux_flow_score": float(market_context.get("aux_flow_score") or 0.0),
        "sector_flow_score": float(market_context.get("sector_flow_score") or 0.0),
        "northbound_score": float(market_context.get("northbound_score") or 0.0),
        "margin_score": float(market_context.get("margin_score") or 0.0),
        "lhb_score": float(market_context.get("lhb_score") or 0.0),
        "etf_flow_score": float(market_context.get("etf_flow_score") or 0.0),
        "etf_share_score": float(market_context.get("etf_share_score") or 0.0),
        "flow_signal_evidence": flow_evidence,
        "fundamental_proxy_score": float(market_context.get("fundamental_proxy_score") or 0.0),
        "fundamental_proxy_evidence": proxy_evidence,
    }


def has_material_market_signal(market_context: dict[str, Any]) -> bool:
    aux_flow_score = float(market_context.get("aux_flow_score") or 0.0)
    sector_flow_score = float(market_context.get("sector_flow_score") or 0.0)
    northbound_score = float(market_context.get("northbound_score") or 0.0)
    margin_score = float(market_context.get("margin_score") or 0.0)
    lhb_score = float(market_context.get("lhb_score") or 0.0)
    etf_flow_score = float(market_context.get("etf_flow_score") or 0.0)
    etf_share_score = float(market_context.get("etf_share_score") or 0.0)
    proxy_score = float(market_context.get("fundamental_proxy_score") or 0.0)
    proxy_evidence = dict(market_context.get("fundamental_proxy_evidence") or {})
    if proxy_evidence and proxy_score >= 0.4:
        return True
    if aux_flow_score >= 0.6:
        return True
    if sector_flow_score >= 0.75 and (etf_flow_score >= 0.2 or northbound_score >= 0.15 or margin_score >= 0.1 or lhb_score >= 0.25):
        return True
    if proxy_score >= 0.3 and aux_flow_score >= 0.35:
        return True
    if etf_flow_score >= 0.45 and etf_share_score >= 0.25:
        return True
    return False


def extend_industry_seeds_with_market_signals(
    seeds: dict[str, dict[str, Any]],
    registry: list[dict[str, str]],
    market_index: dict[str, dict[str, Any]],
) -> None:
    display_lookup = {str(item.get("industry_id") or "").strip(): str(item.get("display_name_cn") or "").strip() for item in registry}
    for industry_id, market_context in market_index.items():
        if not has_material_market_signal(market_context):
            continue
        bucket = seeds.setdefault(
            industry_id,
            {
                "feed_industry_name": display_lookup.get(industry_id) or industry_id,
                "shared_news_score": 0.0,
                "event_count": 0,
                "shared_feed_events": [],
                "theme_overlays": [],
            },
        )
        proxy_evidence = dict(market_context.get("fundamental_proxy_evidence") or {})
        proxy_family = str(proxy_evidence.get("proxy_family") or "").strip()
        overlay_text = str(proxy_evidence.get("summary_cn") or MARKET_PROXY_THEME_MAP.get(proxy_family) or "").strip()
        if overlay_text and overlay_text not in bucket["theme_overlays"]:
            bucket["theme_overlays"].append(overlay_text)
        if proxy_family:
            theme_text = MARKET_PROXY_THEME_MAP.get(proxy_family)
            if theme_text and theme_text not in bucket["theme_overlays"]:
                bucket["theme_overlays"].append(theme_text)


def build_industry_candidates(
    registry: list[dict[str, str]],
    industry_seeds: dict[str, dict[str, Any]],
    market_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    display_lookup = {str(item.get("industry_id") or "").strip(): str(item.get("display_name_cn") or "").strip() for item in registry}
    rep_lookup = {str(item.get("industry_id") or "").strip(): item for item in load_industry_stock_primary()}
    etf_lookup = {str(item.get("industry_id") or "").strip(): item for item in load_industry_etf_primary()}
    candidates: list[dict[str, Any]] = []
    for industry_id, row in industry_seeds.items():
        market_context = dict(market_index.get(industry_id) or {})
        has_feed_events = bool(row.get("shared_feed_events"))
        has_market_signal = has_material_market_signal(market_context)
        origin: list[str] = []
        if has_feed_events:
            origin.append("shared_feed")
        if has_market_signal:
            origin.append("canonical_market")
        if not origin:
            continue
        rep = rep_lookup.get(industry_id) or {}
        etf = etf_lookup.get(industry_id) or {}
        candidates.append(
            {
                "candidate_id": f"industry:{industry_id}",
                "radar_object_type": "industry",
                "radar_object_id": industry_id,
                "radar_object_name": display_lookup.get(industry_id) or str(row.get("feed_industry_name") or industry_id),
                "radar_object_scope": "CN A-share industry",
                "candidate_origin": origin,
                "shared_feed_events": dedupe_feed_events(list(row.get("shared_feed_events") or []), limit=10),
                "sidecar_evidence": market_sidecar_evidence(market_context),
                "market_context": build_industry_market_context(row=row, market_context=market_context),
                "theme_overlays": list(row.get("theme_overlays") or []),
                "representative_stock_code": str(rep.get("stock_code") or "").strip(),
                "representative_stock_name": str(rep.get("stock_name") or "").strip(),
                "etf_proxy_code": str(etf.get("etf_code") or "").strip(),
                "etf_proxy_name": str(etf.get("etf_name") or "").strip(),
            }
        )
    return candidates


def dedupe_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        existing = deduped.get(candidate_id)
        if existing is None:
            deduped[candidate_id] = item
            continue
        existing_events = list(existing.get("shared_feed_events") or []) + list(item.get("shared_feed_events") or [])
        existing["shared_feed_events"] = dedupe_feed_events(existing_events, limit=10)
        if item.get("source_quality") == "trusted":
            existing["source_quality"] = "trusted"
        elif item.get("source_quality") == "mixed" and existing.get("source_quality") != "trusted":
            existing["source_quality"] = "mixed"
        existing["entity_mapping_confidence"] = max(
            float(existing.get("entity_mapping_confidence") or 0.0),
            float(item.get("entity_mapping_confidence") or 0.0),
        )
        if float(item.get("source_priority") or 0.0) > float(existing.get("source_priority") or 0.0):
            deduped[candidate_id] = {**existing, **item, "shared_feed_events": existing["shared_feed_events"]}
    return list(deduped.values())


def build_candidate_pool_payload(
    *,
    run_dt: datetime,
    rows: list[dict[str, Any]] | None = None,
    industry_feed_payload: dict[str, Any] | None = None,
    extra_feed_payloads: list[tuple[str, dict[str, Any]]] | None = None,
    market_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    registry = load_industry_registry()
    known_company_names = load_company_name_index(registry)
    industry_feed = industry_feed_payload if isinstance(industry_feed_payload, dict) else {}
    extra_payloads = list(extra_feed_payloads or [])
    canonical_market = dict(market_index or {})
    rows_by_industry = {str(row.get("industry_id") or "").strip(): row for row in (rows or []) if isinstance(row, dict)}
    industry_seeds = build_industry_seed_map(registry, industry_feed)
    generic_candidates = enrich_industry_seeds_from_extra_feeds(
        industry_seeds,
        registry,
        extra_payloads,
        known_company_names=known_company_names,
    )
    extend_industry_seeds_with_market_signals(industry_seeds, registry, canonical_market)
    for industry_id, row in rows_by_industry.items():
        market_bucket = canonical_market.setdefault(industry_id, {})
        for field in (
            "aux_flow_score",
            "sector_flow_score",
            "northbound_score",
            "margin_score",
            "lhb_score",
            "etf_flow_score",
            "etf_share_score",
            "fundamental_proxy_score",
        ):
            if field not in market_bucket and row.get(field) is not None:
                market_bucket[field] = row.get(field)
        if "flow_signal_evidence" not in market_bucket and row.get("flow_signal_evidence"):
            market_bucket["flow_signal_evidence"] = row.get("flow_signal_evidence")
        if "fundamental_proxy_evidence" not in market_bucket and row.get("fundamental_proxy_evidence"):
            market_bucket["fundamental_proxy_evidence"] = row.get("fundamental_proxy_evidence")
    generated_at = str(industry_feed.get("generated_at") or run_dt.astimezone(timezone.utc).isoformat(timespec="seconds"))
    industry_candidates = build_industry_candidates(registry, industry_seeds, canonical_market)
    candidates = dedupe_candidates([*industry_candidates, *generic_candidates])
    market_sample_date, expected_sample_date, market_sample_ready_time = infer_market_sample_dates(run_dt=run_dt, market_index=canonical_market)
    report_date = run_dt.date().isoformat()
    return {
        "generated_at": generated_at,
        "run_id": f"candidate-pool:{run_dt.isoformat(timespec='seconds')}",
        "candidate_pool_run_id": f"candidate-pool:{run_dt.isoformat(timespec='seconds')}",
        "as_of_date": report_date,
        "report_date": report_date,
        "event_window_end_date": report_date,
        "market_sample_date": market_sample_date,
        "expected_sample_date": expected_sample_date,
        "market_sample_ready_time": market_sample_ready_time,
        "source_contract_version": "v2",
        "shared_feed_input": {
            "feed_name": "news_event_hub.consumer_feeds",
            "feed_generated_at": generated_at,
            "consumer_name": "radar",
        },
        "sidecar_inputs": build_sidecar_inputs(),
        "candidates": candidates,
    }


def default_dated_output(run_dt: datetime) -> Path:
    return DEFAULT_OUTPUT_DIR / f"radar_candidate_pool_{run_dt.strftime('%Y%m%dT%H%M%SZ')}.json"


def load_optional_payload(path_value: Any) -> dict[str, Any] | None:
    path_text = str(path_value or "").strip()
    if not path_text:
        return None
    candidate = Path(path_text).expanduser()
    local_fallbacks: list[Path] = []
    mirror_fallback = DEFAULT_SHARED_NEWS_MIRROR_DIR / candidate.name
    local_fallbacks.append(mirror_fallback)
    if path_text.startswith("/opt/news-event-hub/"):
        local_fallbacks.append(ROOT.parent / "news_event_hub" / "state" / "consumer_exports" / candidate.name)
    if candidate.exists():
        path = candidate
    else:
        path = next((item for item in local_fallbacks if item.exists()), None)
        if path is None:
            return None
    try:
        return load_json_file(path)
    except json.JSONDecodeError:
        return None


def main() -> int:
    args = parse_args()
    run_dt = datetime.now(timezone.utc)
    shared_cfg = shared_news_config()
    market_index = load_canonical_industry_market_index(target_date=run_dt.date().isoformat())
    industry_payload = load_optional_payload(shared_cfg.get("industry_radar_feed_path")) or {}
    extra_feed_payloads: list[tuple[str, dict[str, Any]]] = []
    for key, feed_name in (
        ("opportunity_report_feed_path", "news_event_hub.opportunity_report_feed_latest"),
        ("research_feed_path", "news_event_hub.research_feed_latest"),
    ):
        payload = load_optional_payload(shared_cfg.get(key))
        if payload:
            extra_feed_payloads.append((feed_name, payload))
    payload = build_candidate_pool_payload(
        run_dt=run_dt,
        rows=[],
        industry_feed_payload=industry_payload,
        extra_feed_payloads=extra_feed_payloads,
        market_index=market_index,
    )
    latest_output = args.latest_output
    dated_output = args.output or default_dated_output(run_dt)
    write_json(latest_output, payload)
    write_json(dated_output, payload)
    print(
        json.dumps(
            {
                "latest_output": str(latest_output),
                "dated_output": str(dated_output),
                "candidate_count": len(payload.get("candidates", [])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
