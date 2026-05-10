#!/usr/bin/env python3
"""Build the Radar opportunity snapshot from canonical candidate pool inputs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from radar_company_mapping import ensure_company_mapping_cache
from radar_freshness_utils import previous_market_weekday_text
from radar_industry_registry import load_industry_etf_primary, load_industry_registry, load_industry_stock_primary
from radar_price_sidecar import load_company_price_index, lookup_company_price
from radar_sentiment_sidecar import (
    build_market_sentiment_context,
    compact_company_sentiment_text,
    load_company_sentiment_index,
    lookup_company_sentiment,
    sentiment_bias_text,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_INPUT_COMPANY_ENRICHMENT = ROOT / "output" / "reports" / "radar_company_enrichment_latest.json"
DEFAULT_INPUT_QUANT_SIGNALS = ROOT / "output" / "sidecars" / "quant" / "radar_quant_signal_sidecar_latest.json"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "snapshots"
DEFAULT_LATEST_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_opportunity_snapshot_latest.json"
DEFAULT_COMPANY_MAPPING_OVERRIDES = ROOT / "data" / "company_mapping_overrides.json"

BUCKET_ORDER = {
    "strong_alert": 0,
    "strong_candidate": 1,
    "research_candidate": 2,
    "observe": 3,
}

RUNTIME_STATE_ORDER = {
    "strong_alert": 0,
    "candidate": 1,
    "warming": 2,
    "cold": 3,
}
BUCKET_SEQUENCE = ["observe", "research_candidate", "strong_candidate", "strong_alert"]

GENERIC_EVENT_SUBJECTS = {
    "中泰证券",
    "国金证券",
    "银河证券",
    "中信证券",
    "中国银河证券",
    "央视快评",
    "乘联分会崔东树",
    "中国人民银行",
    "中越联合声明",
}
POSITIVE_EVENT_KEYWORDS = ("增长", "预增", "获批", "批准", "订单", "中标", "合作", "上车", "充足", "发布", "回购", "增持")
NEGATIVE_EVENT_KEYWORDS = ("下降", "下修", "亏损", "终止上市", "摘牌", "风险警示", "停牌", "调查", "承压")
NEGATIVE_COMPANY_EVENT_KEYWORDS = (
    "减持",
    "拟减持",
    "终止收购",
    "终止购买",
    "终止筹划",
    "终止重大资产重组",
    "补缴税款",
    "滞纳金",
    "尚存不确定性",
    "无开展",
    "无相关业务计划",
)
LOW_SIGNAL_COMPANY_EVENT_KEYWORDS = (
    "经营情况正常",
    "不存在未披露重大事项",
    "正常履职",
    "续聘会计师事务所",
    "会计师事务所",
    "独立董事提名人声明",
    "年度报告摘要",
)
MATERIAL_RISK_COMPANY_EVENT_KEYWORDS = (
    "监管函",
    "问询函",
    "立案调查",
    "行政处罚",
    "处罚决定",
    "纪律处分",
)
LOW_SIGNAL_COMPANY_ENRICHMENT_TOKENS = (
    "千问",
    "预测",
    "股民",
    "一地鸡毛",
    "看了一些人的发言",
    "挺有意思",
    "这个标的",
    "专栏",
    "社交舆情发酵",
    "赔我牙齿",
    "任何茅",
)
SOCIAL_SIGNAL_PREFIX_RE = re.compile(
    r"^[^$]{0,80}(?:\d{2}-\d{2}\s+\d{2}:\d{2}|修改于\d{2}-\d{2}\s+\d{2}:\d{2}|[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}).*?来自[^$]{1,40}\s*",
)
GENERIC_EVENT_KEYWORDS = ("证券：", "市场重心", "风险资产", "关注A股", "市场或缩圈", "快评", "新闻发布会", "结束大陆参访")
NON_INDUSTRY_EVENT_KEYWORDS = (
    "副总统",
    "特朗普",
    "佩斯科夫",
    "郑丽文",
    "伊斯兰堡",
    "结束大陆参访",
    "停火",
    "沙特能源部",
    "二手房",
    "以军空袭",
    "黎巴嫩",
    "地方政府债券",
    "航空燃油",
)
PROXY_NAME_MAP = {
    "shipping_cycle": "BDI / 航运运价",
    "utility_electricity": "全社会用电量",
    "hog_cycle": "猪价 / 生猪指数",
    "broker_cycle": "两融总量",
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
LOW_QUALITY_SOURCE_FAMILIES = {
    "search:serpstack",
    "forum:reddit",
}
FEED_EVENT_TYPE_LABELS = {
    "earnings_guidance": "业绩指引线索",
    "social_signal": "舆情发酵线索",
    "commodity_disruption": "商品/供给扰动",
    "policy_regulation": "政策监管线索",
    "deal_mna": "并购重组线索",
    "order_project": "订单项目线索",
    "approval_registration": "审批注册线索",
}
COMPANY_EVENT_KEYWORDS = ("净利润", "预增", "增长", "获批", "批准", "订单", "中标", "回购", "增持", "交付", "停牌", "摘牌", "风险警示", "合作")
NON_COMPANY_SUBJECT_KEYWORDS = (
    "副总统",
    "快评",
    "分会",
    "能源部",
    "政府",
    "二手房",
    "发布会",
    "停火",
    "伊斯兰堡",
    "佩斯科夫",
    "郑丽文",
)
CURRENCY_WINDOW_SHORT = "days_1_3"
CURRENCY_WINDOW_MEDIUM = "days_3_10"
CURRENCY_WINDOW_LONG = "weeks_2_6"
CURRENCY_WINDOW_OPEN = "open_ended"
HEDGE_DIFFICULTY_LOW = "low"
HEDGE_DIFFICULTY_MEDIUM = "medium"
HEDGE_DIFFICULTY_HIGH = "high"
EVIDENCE_QUALITY_STRUCTURED = "structured_confirmed"
EVIDENCE_QUALITY_PROXY = "proxy_confirmed"
EVIDENCE_QUALITY_EARLY = "early_thematic"
EVIDENCE_QUALITY_NARRATIVE = "narrative_only"
EVIDENCE_QUALITY_RISK = "risk_signal"
TRIAGE_IMMEDIATE = "immediate_research"
TRIAGE_WATCH = "thesis_watch"
TRIAGE_RISK = "risk_review"
TRIAGE_BACKGROUND = "background_only"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL, help="Radar candidate pool JSON.")
    parser.add_argument("--input-company-enrichment", type=Path, default=DEFAULT_INPUT_COMPANY_ENRICHMENT, help="Pre-snapshot company enrichment sidecar JSON.")
    parser.add_argument("--input-quant-signals", type=Path, default=DEFAULT_INPUT_QUANT_SIGNALS, help="Quant research sidecar JSON.")
    parser.add_argument("--output", type=Path, default=None, help="Optional dated snapshot output path.")
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT, help="Latest snapshot output path.")
    return parser.parse_args()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slugify_text(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", str(text or "").strip().lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    return cleaned.strip("-") or "item"


def build_research_links(object_id: str, object_name: str) -> list[str]:
    slug = slugify_text(object_name)
    safe_id = str(object_id or "").strip() or slug
    return [
        f"inventory_json:output/inventory/radar_catalyst_inventory_latest.json#{safe_id}",
        f"inventory_md:output/inventory/radar_catalyst_inventory_latest.md#{slug}",
        f"handoff_json:output/handoffs/radar_research_handoff_latest.json#{safe_id}",
        f"handoff_md:output/handoffs/radar_research_handoff_latest.md#{slug}",
        f"daily_report_md:output/reports/radar_daily_report_latest.md#{slug}",
    ]


def load_company_mapping_overrides(path: Path = DEFAULT_COMPANY_MAPPING_OVERRIDES) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        rows.append({str(key): str(value) if value is not None else "" for key, value in item.items()})
    return rows


def load_payload(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return data


def load_optional_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def normalize_quant_instrument(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    compact = raw.replace(".", "").replace("-", "").upper()
    match = re.search(r"(?i)(SH|SZ|HK)(\d{5,6})", compact)
    if match:
        return f"{match.group(1).upper()}{match.group(2)}"
    match = re.search(r"(?i)(\d{6})(SH|SZ)", compact)
    if match:
        return f"{match.group(2).upper()}{match.group(1)}"
    match = re.search(r"(?i)(\d{5,6})HK", compact)
    if match:
        return f"HK{match.group(1).zfill(5)}"
    match = re.search(r"(\d{5,6})", compact)
    if match:
        code = match.group(1)
        if len(code) == 5:
            return f"HK{code}"
        prefix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        return f"{prefix}{code}"
    return compact


def build_quant_signal_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in payload.get("instrument_results") or []:
        if not isinstance(item, dict):
            continue
        symbol = normalize_quant_instrument(item.get("symbol"))
        query_payload = dict(item.get("payload") or {})
        bundle_summary = dict(query_payload.get("bundle_summary") or {})
        if not symbol or not bundle_summary:
            continue
        record = {
            "status": str(item.get("status") or ""),
            "note": str(item.get("note") or ""),
            "symbol": symbol,
            "source_dates": dict(query_payload.get("source_dates") or {}),
            "availability": dict(query_payload.get("availability") or {}),
            "date_alignment_status": str(query_payload.get("date_alignment_status") or ""),
            "bundle_summary": bundle_summary,
        }
        index[symbol] = record
    return index


def lookup_quant_signal_context(
    index: dict[str, dict[str, Any]],
    *,
    stock_code: str = "",
    market_symbol: str = "",
) -> dict[str, Any]:
    for value in (stock_code, market_symbol):
        key = normalize_quant_instrument(value)
        if key and key in index:
            return dict(index[key])
    return {}


def event_sort_score(event: dict[str, Any]) -> float:
    return float(event.get("score") or 0.0)


def normalized_event_title(event: dict[str, Any]) -> str:
    return clean_display_text(event.get("event_title") or event.get("title") or event.get("event_type") or "")


def parse_iso_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def supporting_event_display_key(event: dict[str, Any]) -> str:
    title = normalized_event_title(event)
    if title:
        published = str(event.get("published_at") or "").strip()[:10]
        text = re.sub(r"\s+", " ", title.lower())
        return f"text:{text}|date:{published}"
    event_id = str(event.get("event_id") or "").strip()
    if event_id:
        return f"id:{event_id}"
    text = re.sub(r"\s+", " ", str(event.get("event_type") or "").strip().lower())
    if text:
        return f"type:{text}"
    return "unknown"


def supporting_event_priority(event: dict[str, Any]) -> tuple[int, str]:
    source = str(event.get("source") or "").strip()
    event_id = str(event.get("event_id") or "").strip()
    if source == "news_event_hub:structured_event":
        rank = 0
    elif source.startswith("proxy:"):
        rank = 1
    elif source.startswith("news_event_hub"):
        rank = 2
    else:
        rank = 3
    title = normalized_event_title(event)
    return (rank, event_id or title)


def dedupe_supporting_events(events: list[dict[str, str]], *, limit: int | None = None) -> list[dict[str, str]]:
    deduped: dict[str, dict[str, str]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        key = supporting_event_display_key(event)
        existing = deduped.get(key)
        if existing is None or supporting_event_priority(event) < supporting_event_priority(existing):
            deduped[key] = dict(event)
    ordered = sorted(deduped.values(), key=supporting_event_priority)
    if limit is not None:
        ordered = ordered[:limit]
    return ordered


def event_sample_date(event: dict[str, Any]) -> str:
    parsed = parse_iso_date(event.get("published_at"))
    return parsed.date().isoformat() if parsed else ""


def price_context_sample_date(context: dict[str, Any]) -> str:
    return str(context.get("as_of_date") or "").strip()[:10]


def shared_feed_event_dedupe_key(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "").strip()
    if event_id:
        return f"id:{event_id}"
    title = re.sub(r"\s+", " ", normalized_event_title(event).lower())
    published = str(event.get("published_at") or "").strip()[:10]
    entity = re.sub(r"\s+", " ", str(event.get("primary_entity") or "").strip().lower())
    return f"title:{title}|entity:{entity}|date:{published}"


def dedupe_shared_feed_events(events: list[dict[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        key = shared_feed_event_dedupe_key(event)
        existing = deduped.get(key)
        if existing is None or event_sort_score(event) > event_sort_score(existing):
            deduped[key] = event
    ordered = sorted(deduped.values(), key=event_sort_score, reverse=True)
    if limit is not None:
        ordered = ordered[:limit]
    return ordered


def is_structured_feed_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("event_type") or "").strip()
    event_state = str(event.get("event_state") or "").strip()
    if event_type in HARD_EVENT_TYPES:
        return True
    return event_state == "confirmed"


def build_rows_from_candidate_pool(payload: dict[str, Any]) -> list[dict[str, Any]]:
    registry = load_industry_registry()
    stock_primary = {str(item.get("industry_id") or "").strip(): item for item in load_industry_stock_primary()}
    etf_primary = {str(item.get("industry_id") or "").strip(): item for item in load_industry_etf_primary()}
    registry_lookup = {str(item.get("industry_id") or "").strip(): item for item in registry}
    rows: list[dict[str, Any]] = []
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("radar_object_type") or "") != "industry":
            continue
        industry_id = str(candidate.get("radar_object_id") or "").strip()
        if not industry_id:
            continue
        market_context = dict(candidate.get("market_context") or {})
        shared_events = [dict(item) for item in (candidate.get("shared_feed_events") or []) if isinstance(item, dict)]
        shared_events.sort(key=event_sort_score, reverse=True)
        shared_events = dedupe_shared_feed_events(shared_events, limit=10)
        structured_events = [item for item in shared_events if is_structured_feed_event(item)]
        proxy_evidence = dict(market_context.get("fundamental_proxy_evidence") or {})
        policy_score = float(market_context.get("shared_news_score") or 0.0)
        heat_score = min(1.0, policy_score * 0.72 + min(int(market_context.get("event_count") or 0) / 8.0, 1.0) * 0.28)
        money_flow_score = max(
            float(market_context.get("aux_flow_score") or 0.0),
            float(market_context.get("sector_flow_score") or 0.0),
        )
        fundamental_proxy_score = float(market_context.get("fundamental_proxy_score") or 0.0)
        announcement_score = min(1.0, max((event_sort_score(item) / 100.0 for item in structured_events), default=0.0))
        total_score = min(
            1.0,
            policy_score * 0.42
            + money_flow_score * 0.28
            + fundamental_proxy_score * 0.20
            + announcement_score * 0.10,
        )
        state = "strong_alert" if total_score >= 0.86 else "candidate" if total_score >= 0.72 else "warming" if total_score >= 0.58 else "cold"
        display_name = str(candidate.get("radar_object_name") or registry_lookup.get(industry_id, {}).get("display_name_cn") or industry_id)
        policy_articles: list[dict[str, Any]] = []
        announcement_events: list[dict[str, Any]] = []
        theme_overlays = [str(item).strip() for item in (candidate.get("theme_overlays") or []) if str(item).strip()]
        for event in shared_events:
            title = normalized_event_title(event)
            payload_event = {
                "event_id": str(event.get("event_id") or "").strip(),
                "source_id": str(event.get("source_id") or event.get("source") or "news_event_hub").strip() or "news_event_hub",
                "title": title,
                "headline": title,
                "event_type": str(event.get("event_type") or "").strip(),
                "published_at": str(event.get("published_at") or "").strip(),
                "score": event_sort_score(event),
            }
            if is_structured_feed_event(event):
                announcement_events.append(
                    {
                        "title": title,
                        "signal_tag": str(event.get("event_type") or "").strip(),
                        "event_state": str(event.get("event_state") or "").strip(),
                        "score": event_sort_score(event),
                        "published_at": str(event.get("published_at") or "").strip(),
                    }
                )
            else:
                policy_articles.append(payload_event)
            for theme in [*(event.get("themes") or []), *(event.get("macro_themes") or [])]:
                theme_text = str(theme).strip()
                if theme_text and theme_text not in theme_overlays:
                    theme_overlays.append(theme_text)
        rep = stock_primary.get(industry_id) or {}
        etf = etf_primary.get(industry_id) or {}
        row = {
            "industry_id": industry_id,
            "display_name_cn": display_name,
            "industry_state": state,
            "total_score": round(total_score, 4),
            "heat_score": round(heat_score, 4),
            "money_flow_score": round(money_flow_score, 4),
            "fundamental_score": round(max(fundamental_proxy_score, announcement_score), 4),
            "policy_score": round(policy_score, 4),
            "announcement_score": round(announcement_score, 4),
            "fundamental_proxy_score": round(fundamental_proxy_score, 4),
            "aux_flow_score": round(float(market_context.get("aux_flow_score") or 0.0), 4),
            "announcement_events": announcement_events[:6],
            "policy_articles": policy_articles[:8],
            "shared_events": shared_events[:10],
            "flow_signal_evidence": dict(market_context.get("flow_signal_evidence") or {}),
            "fundamental_proxy_evidence": proxy_evidence,
            "overlay_names": theme_overlays[:5],
            "representative_stock_code": str(rep.get("stock_code") or "").strip(),
            "representative_stock_name": str(rep.get("stock_name") or "").strip(),
            "etf_proxy_code": str(etf.get("etf_code") or "").strip(),
            "etf_proxy_name": str(etf.get("etf_name") or "").strip(),
            "rank_desc": 0,
        }
        rows.append(row)
    rows.sort(key=lambda item: float(item.get("total_score") or 0.0), reverse=True)
    for idx, row in enumerate(rows, start=1):
        row["rank_desc"] = idx
    return rows


def clamp_score(value: Any) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, int(round(numeric * 100 if numeric <= 1.0 else numeric))))


def stringify_symbol(code: Any, name: Any | None = None) -> str | None:
    code_text = str(code or "").strip()
    name_text = str(name or "").strip()
    if not code_text and not name_text:
        return None
    if code_text and name_text:
        return f"{name_text} {code_text}"
    return name_text or code_text


def clean_display_text(text: Any) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"\[[^\]]*\]\s*", "", cleaned)
    cleaned = re.sub(r"【[^】]*】\s*", "", cleaned)
    cleaned = re.sub(r"（[^）]*）", "", cleaned)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def optional_number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def pct_text(value: Any) -> str:
    numeric = optional_number(value)
    if numeric is None:
        return "N/A"
    return f"{numeric * 100:+.1f}%"


def ratio_x_text(value: Any) -> str:
    numeric = optional_number(value)
    if numeric is None or numeric <= 0:
        return "N/A"
    return f"{numeric:.2f}x"


def amount_yi_text(value: Any) -> str:
    numeric = optional_number(value)
    if numeric is None:
        return "N/A"
    return f"{numeric / 1e8:.2f}亿"


def extract_event_subject(text: str) -> str:
    cleaned = clean_display_text(text)
    if "：" in cleaned:
        subject = cleaned.split("：", 1)[0].strip()
    elif ":" in cleaned:
        subject = cleaned.split(":", 1)[0].strip()
    else:
        subject = ""
    if not subject or len(subject) > 18:
        return ""
    return subject


def normalize_company_subject(subject: str) -> str:
    cleaned = clean_display_text(subject).strip("“”\"'")
    cleaned = re.sub(r"^[一二三四五六七八九十0-9]+连板", "", cleaned)
    for prefix in ("两连板", "三连板", "四连板", "五连板", "六连板", "*ST", "ST"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned.strip()


def is_generic_subject(subject: str) -> bool:
    if not subject:
        return True
    if subject in GENERIC_EVENT_SUBJECTS:
        return True
    return any(keyword in subject for keyword in ("证券", "政府", "副总统", "快评", "分会", "人民银行", "联合声明", "署长"))


def looks_like_company_subject(subject: str, event_text: str) -> bool:
    normalized = normalize_company_subject(subject)
    if not normalized:
        return False
    if any(keyword in normalized for keyword in NON_COMPANY_SUBJECT_KEYWORDS):
        return False
    if normalized.endswith(("LOF", "ETF")):
        return False
    if re.search(r"\d{3,}", normalized):
        return False
    if is_generic_subject(normalized):
        return False
    cleaned_event = clean_display_text(event_text)
    if any(keyword in cleaned_event for keyword in COMPANY_EVENT_KEYWORDS):
        return True
    return 3 <= len(normalized) <= 8


def candidate_events(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    events = [dict(item) for item in (candidate.get("shared_feed_events") or []) if isinstance(item, dict)]
    events.sort(key=event_sort_score, reverse=True)
    return events


def candidate_primary_event(candidate: dict[str, Any]) -> dict[str, Any]:
    events = candidate_events(candidate)
    return events[0] if events else {}


def candidate_source_families(event: dict[str, Any]) -> set[str]:
    families: set[str] = set()
    for article in event.get("supporting_articles") or []:
        if not isinstance(article, dict):
            continue
        family = str(article.get("source_family") or "").strip()
        if family:
            families.add(family)
    return families


def candidate_source_quality(candidate: dict[str, Any], event: dict[str, Any]) -> str:
    quality = str(candidate.get("source_quality") or "").strip()
    if quality:
        return quality
    families = candidate_source_families(event)
    if not families:
        return "low"
    if any(family not in LOW_QUALITY_SOURCE_FAMILIES for family in families):
        return "trusted"
    return "low"


def candidate_entity_name(event: dict[str, Any], candidate: dict[str, Any]) -> str:
    for raw in [
        str(candidate.get("radar_object_name") or "").strip(),
        str(event.get("primary_entity") or "").strip(),
        *(str(item).strip() for item in (event.get("companies") or []) if str(item).strip()),
    ]:
        if raw:
            return raw
    return ""


def direct_candidate_signal_text(candidate: dict[str, Any], event: dict[str, Any]) -> str:
    subject = normalize_company_subject(candidate_entity_name(event, candidate))
    title = normalized_event_title(event)
    event_type = str(event.get("event_type") or "").strip()
    type_label = FEED_EVENT_TYPE_LABELS.get(event_type, "")
    if event_type == "social_signal":
        article_signal = candidate_article_signal_text(candidate, event)
        if article_signal:
            return article_signal
        social_text = title
        if subject:
            social_text = re.sub(
                rf"^.*?\${re.escape(subject)}(?:\((?:SH|SZ|BJ|HK)\d{{4,6}}\))?\$\s*",
                "",
                social_text,
                count=1,
            ).strip()
        social_text = clean_display_text(social_text)
        if subject and social_text and any(keyword in social_text for keyword in COMPANY_EVENT_KEYWORDS):
            return f"{subject}：{social_text[:90]}"
        if subject:
            return f"{subject}：社交舆情发酵，待补核"
        return "社交舆情发酵，待补核"
    if subject:
        if title and contains_cjk(title):
            if title.startswith(subject):
                return title
            return f"{subject}：{title}"
        if type_label:
            return f"{subject}：{type_label}"
        if title:
            return f"{subject}：{title}"
        return subject
    return title or type_label or event_type


def sanitize_company_signal_title(subject: str, title: str) -> str:
    cleaned = str(title or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"\[[^\]]*\]\s*", "", cleaned)
    cleaned = re.sub(r"【[^】]*】\s*", "", cleaned)
    cleaned = clean_display_text(cleaned)
    if subject:
        cleaned = re.sub(
            rf"^.*?\${re.escape(subject)}(?:\((?:SH|SZ|BJ|HK)\d{{4,6}}\))?\$\s*",
            "",
            cleaned,
            count=1,
        ).strip()
    cleaned = SOCIAL_SIGNAL_PREFIX_RE.sub("", cleaned).strip()
    cleaned = cleaned.lstrip(".：:;；- ").strip()
    if not cleaned:
        return ""
    if subject and not cleaned.startswith(subject):
        cleaned = f"{subject}：{cleaned}"
    return cleaned[:120]


def score_company_signal_title(subject: str, title: str) -> tuple[int, str]:
    cleaned = sanitize_company_signal_title(subject, title)
    if not cleaned:
        return (-99, "")
    body = cleaned
    if subject and body.startswith(subject):
        body = body[len(subject) :].lstrip("：: ").strip()
    score = 0
    if any(keyword in body for keyword in ("公告", "拟收购", "拟增持", "净利润", "同比增长", "同比下降", "停牌", "订单", "中标", "重大资产重组", "员工持股", "增持", "回购", "收购", "分红", "派息", "获批", "临床试验")):
        score += 5
    if any(keyword in body for keyword in ("亏损", "减持", "风险警示", "摘牌", "退市", "问询函", "立案", "处罚")):
        score += 4
    if len(body) <= 42:
        score += 1
    if any(token in body for token in LOW_SIGNAL_COMPANY_ENRICHMENT_TOKENS):
        score -= 8
    return score, cleaned


def best_material_company_title(subject: str, titles: list[str]) -> str:
    best = ""
    best_score = -99
    for raw in titles:
        score, cleaned = score_company_signal_title(subject, raw)
        if score > best_score:
            best = cleaned
            best_score = score
    if best_score < 4:
        return ""
    return best


def candidate_article_signal_text(candidate: dict[str, Any], event: dict[str, Any]) -> str:
    subject = normalize_company_subject(candidate_entity_name(event, candidate))
    titles: list[str] = []
    for article in event.get("supporting_articles") or []:
        if not isinstance(article, dict):
            continue
        source_family = str(article.get("source_family") or "").strip().lower()
        if source_family.startswith(("social:", "forum:", "search:")):
            continue
        title = str(article.get("title") or article.get("headline") or article.get("event_title") or "").strip()
        if title:
            titles.append(title)
    return best_material_company_title(subject, titles)


def company_signal_is_placeholder(subject: str, text: str) -> bool:
    cleaned = clean_display_text(text)
    if not cleaned:
        return True
    if "社交舆情发酵，待补核" in cleaned:
        return True
    score, _ = score_company_signal_title(subject, cleaned)
    return score < 4


def candidate_supporting_events(
    candidate: dict[str, Any],
    *,
    enrichment: dict[str, Any] | None = None,
    limit: int = 6,
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    subject = normalize_company_subject(str(candidate.get("radar_object_name") or ""))
    for idx, event in enumerate(candidate_events(candidate)[:limit], start=1):
        text = direct_candidate_signal_text(candidate, event)
        if not text:
            continue
        events.append(
            {
                "source": str(event.get("source_id") or event.get("source") or "news_event_hub").strip() or "news_event_hub",
                "event_id": str(event.get("event_id") or f"{candidate.get('candidate_id') or 'candidate'}:{idx}"),
                "event_type": str(event.get("event_type") or "").strip(),
                "title": text[:120],
                "headline": text[:120],
                "summary": text[:120],
                "published_at": str(event.get("published_at") or "").strip(),
            }
        )
    if enrichment:
        events.extend(company_enrichment_supporting_events(subject, enrichment, limit=min(2, limit)))
    return dedupe_supporting_events(events, limit=limit)


def candidate_theme_overlays(candidate: dict[str, Any], event: dict[str, Any]) -> list[str]:
    overlays: list[str] = []
    for raw in [*(candidate.get("theme_overlays") or []), *(event.get("themes") or []), *(event.get("macro_themes") or [])]:
        text = str(raw or "").strip()
        if text and text not in overlays:
            overlays.append(text)
    return overlays


def build_company_enrichment_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        object_id = str(item.get("radar_object_id") or "").strip()
        object_name = normalize_company_subject(str(item.get("radar_object_name") or ""))
        if object_id:
            index[object_id] = item
        if object_name and f"name:{object_name}" not in index:
            index[f"name:{object_name}"] = item
    return index


def lookup_company_enrichment(candidate: dict[str, Any], enrichment_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not enrichment_index:
        return {}
    object_id = str(candidate.get("radar_object_id") or candidate.get("candidate_id") or "").strip()
    if object_id and object_id in enrichment_index:
        return dict(enrichment_index[object_id])
    object_name = normalize_company_subject(str(candidate.get("radar_object_name") or ""))
    if object_name:
        item = enrichment_index.get(f"name:{object_name}")
        if isinstance(item, dict):
            return dict(item)
    return {}


def company_enrichment_counts(enrichment: dict[str, Any]) -> tuple[int, int]:
    coverage = enrichment.get("coverage_after") or {}
    return (
        int(coverage.get("matching_events") or 0),
        int(coverage.get("matching_articles") or 0),
    )


def company_enrichment_signal_text(subject: str, enrichment: dict[str, Any]) -> str:
    if str(enrichment.get("status") or "") != "pass":
        return ""
    trusted_count = int(enrichment.get("trusted_evidence_count") or 0)
    if trusted_count <= 0:
        return ""
    titles = [str(raw or "") for raw in (enrichment.get("trusted_titles") or enrichment.get("top_titles") or [])]
    return best_material_company_title(subject, titles)


def company_enrichment_rank_floor(
    *,
    enrichment: dict[str, Any],
    signal_text: str,
) -> int:
    if str(enrichment.get("status") or "") != "pass" or not signal_text:
        return 0
    events, articles = company_enrichment_counts(enrichment)
    if events <= 0 and articles <= 0:
        return 0
    catalyst_type = classify_catalyst_type(signal_text, object_type="company")
    base = 0
    if events >= 2:
        base += 20
    elif events == 1:
        base += 14
    base += min(articles, 4) * 2
    if catalyst_type in {
        "approval_registration",
        "order_project",
        "earnings_guidance",
        "capital_markets",
        "merger_restructuring",
        "buyback_shareholder_support",
        "financing_dilution",
        "litigation_regulatory",
        "dividend_capital_return",
    }:
        base += 6
    if str(enrichment.get("lane") or "") not in {"research_feed_fastpath", "research_feed_fastpath_fallback"}:
        base += 4
    return min(42, base)


def company_enrichment_bonus(enrichment: dict[str, Any]) -> tuple[int, int, int]:
    signal_text = company_enrichment_signal_text("", enrichment)
    if str(enrichment.get("status") or "") != "pass" or not signal_text:
        return (0, 0, 0)
    events, articles = company_enrichment_counts(enrichment)
    score_bonus = min(18, events * 5 + min(articles, 3) * 2)
    confidence_bonus = min(16, 6 + events * 3 + min(articles, 2))
    followup_bonus = min(12, 4 + events * 2 + min(articles, 2))
    return (score_bonus, confidence_bonus, followup_bonus)


def company_enrichment_supporting_events(subject: str, enrichment: dict[str, Any], *, limit: int = 3) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if str(enrichment.get("status") or "") != "pass":
        return items
    published_at = str(enrichment.get("latest_published_at") or "").strip()
    title = company_enrichment_signal_text(subject, enrichment)
    if not title:
        return items
    items.append(
        {
            "source": f"company_enrichment:{str(enrichment.get('lane') or 'unknown')}",
            "event_id": f"company_enrichment:{subject}:1",
            "event_type": classify_catalyst_type(title, object_type="company"),
            "title": title,
            "headline": title,
            "summary": title,
            "published_at": published_at,
        }
    )
    return items


def load_company_mapping_index() -> dict[str, dict[str, str]]:
    payload = ensure_company_mapping_cache(load_industry_registry(), build_if_missing=False, refresh_stale=False)
    index: dict[str, dict[str, str]] = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        name = normalize_company_subject(str(row.get("company_name") or ""))
        industry_id = str(row.get("industry_id") or "").strip()
        if not name or not industry_id or name in index:
            continue
        index[name] = {
            "company_name": str(row.get("company_name") or "").strip(),
            "stock_code": str(row.get("stock_code") or "").strip(),
            "industry_id": industry_id,
            "industry_label": str(row.get("industry_label") or "").strip(),
        }
    for row in load_company_mapping_overrides():
        industry_id = str(row.get("industry_id") or "").strip()
        industry_label = str(row.get("industry_label") or "").strip()
        stock_code = str(row.get("stock_code") or "").strip()
        canonical_name = str(row.get("company_name") or "").strip()
        aliases = [str(item).strip() for item in str(row.get("aliases") or "").split("|") if str(item).strip()]
        for raw_name in [canonical_name] + aliases:
            name = normalize_company_subject(raw_name)
            if not name or not industry_id:
                continue
            index[name] = {
                "company_name": canonical_name or raw_name,
                "stock_code": stock_code,
                "industry_id": industry_id,
                "industry_label": industry_label,
            }
    return index


def event_score(text: str, source: str = "") -> int:
    cleaned = clean_display_text(text)
    score = 0
    subject = extract_event_subject(cleaned)
    if subject and not is_generic_subject(subject):
        score += 12
    if any(keyword in cleaned for keyword in POSITIVE_EVENT_KEYWORDS):
        score += 10
    if any(keyword in cleaned for keyword in NEGATIVE_EVENT_KEYWORDS):
        score -= 18
    if any(keyword in cleaned for keyword in NEGATIVE_COMPANY_EVENT_KEYWORDS):
        score -= 20
    if any(keyword in cleaned for keyword in LOW_SIGNAL_COMPANY_EVENT_KEYWORDS):
        score -= 18
    if any(keyword in cleaned for keyword in MATERIAL_RISK_COMPANY_EVENT_KEYWORDS):
        score += 16
    if any(keyword in cleaned for keyword in GENERIC_EVENT_KEYWORDS):
        score -= 10
    if any(keyword in cleaned for keyword in NON_INDUSTRY_EVENT_KEYWORDS):
        score -= 18
    if cleaned == "industry_scan_snapshot":
        score -= 20
    if source.startswith("akshare:stock_notice_report"):
        score += 10
    if source.startswith("proxy:"):
        score += 8
    return score


def event_polarity(text: str) -> str:
    cleaned = clean_display_text(text)
    if any(keyword in cleaned for keyword in NEGATIVE_EVENT_KEYWORDS):
        return "negative"
    if any(keyword in cleaned for keyword in NEGATIVE_COMPANY_EVENT_KEYWORDS):
        return "negative"
    if any(keyword in cleaned for keyword in POSITIVE_EVENT_KEYWORDS):
        return "positive"
    return "neutral"


def is_low_signal_company_event(text: str) -> bool:
    cleaned = clean_display_text(text)
    return any(keyword in cleaned for keyword in LOW_SIGNAL_COMPANY_EVENT_KEYWORDS)


def classify_catalyst_type(text: str, *, object_type: str, proxy_family: str = "") -> str:
    cleaned = clean_display_text(text)
    if proxy_family:
        return "industry_proxy"
    if any(keyword in cleaned for keyword in ("获批", "批准", "注册证书", "临床试验")):
        return "approval_registration"
    if any(keyword in cleaned for keyword in ("订单", "中标", "招标")):
        return "order_project"
    if any(keyword in cleaned for keyword in ("净利润", "预增", "业绩", "营收", "亏损")):
        return "earnings_guidance"
    if any(keyword in cleaned for keyword in ("并购", "重组", "分拆", "终止收购", "终止筹划", "重大资产重组")):
        return "merger_restructuring"
    if any(keyword in cleaned for keyword in ("回购", "增持", "股东增持", "员工持股")):
        return "buyback_shareholder_support"
    if any(keyword in cleaned for keyword in ("定增", "配股", "可转债", "发行H股", "再融资", "募资", "募投")):
        return "financing_dilution"
    if any(keyword in cleaned for keyword in ("诉讼", "立案", "调查", "监管函", "罚单", "行政处罚", "问询函")):
        return "litigation_regulatory"
    if any(keyword in cleaned for keyword in ("分红", "派息", "特别股息", "现金红利")):
        return "dividend_capital_return"
    if any(keyword in cleaned for keyword in ("停牌", "减持")):
        return "capital_markets"
    if any(keyword in cleaned for keyword in ("摘牌", "退市", "风险警示", "终止上市")):
        return "distress_delisting"
    if any(keyword in cleaned for keyword in ("政策", "监管", "关税", "谈判", "发布会")):
        return "policy_regulation" if object_type != "company" else "macro_geopolitical"
    if any(keyword in cleaned for keyword in ("上车", "交付", "排期", "运行", "部署")):
        return "product_operation"
    if object_type == "macro":
        return "industry_proxy"
    if object_type == "industry":
        return "policy_regulation"
    return "general_corporate"


def classify_hard_or_soft(catalyst_type: str, text: str) -> str:
    cleaned = clean_display_text(text)
    if catalyst_type in {
        "approval_registration",
        "order_project",
        "merger_restructuring",
        "buyback_shareholder_support",
        "financing_dilution",
        "litigation_regulatory",
        "dividend_capital_return",
        "distress_delisting",
    }:
        return "hard"
    if catalyst_type == "capital_markets":
        return "hard" if any(keyword in cleaned for keyword in ("停牌", "回购", "增持", "减持", "并购", "重组", "分拆")) else "soft"
    if catalyst_type == "earnings_guidance":
        return "hard" if any(keyword in cleaned for keyword in ("净利润", "亏损", "预增", "业绩", "业绩指引")) else "soft"
    if catalyst_type == "industry_proxy":
        return "soft"
    return "soft"


def classify_catalyst_stage(catalyst_type: str, hard_or_soft: str, text: str) -> str:
    cleaned = clean_display_text(text)
    if catalyst_type == "distress_delisting":
        return "active_risk"
    if catalyst_type in {"litigation_regulatory"}:
        return "announced"
    if hard_or_soft == "hard":
        if any(keyword in cleaned for keyword in ("停牌", "获批", "批准", "注册证书", "终止上市", "摘牌")):
            return "announced"
        if any(keyword in cleaned for keyword in ("订单", "中标", "交付", "回购", "增持", "分红", "派息")):
            return "execution_window"
        return "announced"
    if catalyst_type == "industry_proxy":
        return "monitoring"
    if catalyst_type in {"policy_regulation", "macro_geopolitical"}:
        return "signal_clustered"
    if catalyst_type == "product_operation":
        return "confirmation_window"
    return "monitoring"


def classify_due_window(catalyst_type: str, stage: str, text: str) -> str:
    cleaned = clean_display_text(text)
    if stage == "active_risk":
        return CURRENCY_WINDOW_SHORT
    if any(keyword in cleaned for keyword in ("停牌", "获批", "批准", "注册证书")):
        return CURRENCY_WINDOW_SHORT
    if catalyst_type in {
        "earnings_guidance",
        "order_project",
        "capital_markets",
        "merger_restructuring",
        "buyback_shareholder_support",
        "financing_dilution",
        "litigation_regulatory",
        "dividend_capital_return",
    }:
        return CURRENCY_WINDOW_MEDIUM
    if catalyst_type in {"industry_proxy", "policy_regulation", "macro_geopolitical"}:
        return CURRENCY_WINDOW_LONG
    return CURRENCY_WINDOW_OPEN


def build_next_milestone(subject: str, catalyst_type: str, text: str, fallback: str) -> str:
    cleaned = clean_display_text(text)
    if catalyst_type == "approval_registration":
        return f"确认“{subject}”后续商业化、放量或进一步审批进展"
    if catalyst_type == "order_project":
        return f"确认“{subject}”订单是否兑现为交付和收入确认"
    if catalyst_type == "earnings_guidance":
        if any(keyword in cleaned for keyword in NEGATIVE_EVENT_KEYWORDS):
            return f"确认“{subject}”业绩承压是否继续恶化，或是否出现修复迹象"
        return f"确认“{subject}”事件披露后 1-3 个交易日是否继续有资金承接，并观察同链条是否跟随"
    if catalyst_type == "merger_restructuring":
        return f"确认“{subject}”重组/并购动作是否继续推进并形成正式交易里程碑"
    if catalyst_type == "buyback_shareholder_support":
        return f"确认“{subject}”股东支持动作是否继续执行并带来交易层确认"
    if catalyst_type == "financing_dilution":
        return f"确认“{subject}”再融资安排是否落地，以及是否带来摊薄或产能扩张影响"
    if catalyst_type == "litigation_regulatory":
        return f"确认“{subject}”监管/诉讼进展是否继续升级，或被正式解除"
    if catalyst_type == "dividend_capital_return":
        return f"确认“{subject}”分红/派息安排是否正式落地并形成资本回报确认"
    if catalyst_type == "capital_markets":
        return f"确认“{subject}”资本动作是否带来后续里程碑或交易确认"
    if catalyst_type == "distress_delisting":
        return f"确认“{subject}”风险事件是否继续恶化或被市场充分定价"
    if catalyst_type == "industry_proxy":
        return fallback
    if catalyst_type in {"policy_regulation", "macro_geopolitical"}:
        return "确认后续政策/监管表态是否升级成可执行里程碑"
    if catalyst_type == "product_operation" or "上车" in cleaned:
        return f"确认“{subject}”事件是否扩散到更多订单、量产或产业链跟随"
    return fallback


def classify_event_driven_lane(
    *,
    hard_or_soft: str,
    catalyst_stage: str,
    radar_bucket: str,
    polarity: str = "neutral",
) -> str:
    if catalyst_stage == "active_risk" or polarity == "negative":
        return "risk_monitor"
    if radar_bucket in {"strong_alert", "strong_candidate", "research_candidate"}:
        if hard_or_soft == "hard":
            return "hard_catalyst_board"
        return "soft_catalyst_watchlist"
    return "background_monitor"


def build_confirmation_gap(
    *,
    object_type: str,
    catalyst_type: str,
    polarity: str = "neutral",
    row: dict[str, Any] | None = None,
    subject: str = "",
    proxy_name: str = "",
    mapping_hit: dict[str, str] | None = None,
) -> str:
    row = row or {}
    if object_type == "industry":
        if polarity == "negative":
            return "当前负面线索已出现，但仍需确认是否从单一主体扩散成行业性风险。"
        if catalyst_type in {"policy_regulation", "macro_geopolitical"} and not row.get("announcement_events"):
            return "当前更像政策/舆情驱动，仍缺公司经营层或结构化事件的第二层确认。"
        gaps: list[str] = []
        if not row.get("announcement_events"):
            gaps.append("结构化事件")
        if not row.get("fundamental_proxy_evidence"):
            gaps.append("行业代理变量")
        if float(row.get("money_flow_score") or 0.0) < 0.50:
            gaps.append("资金共振")
        if not gaps:
            return "当前确认缺口较小，后续主要看里程碑兑现和扩散强度。"
        return "当前仍缺" + "、".join(gaps[:3]) + "确认。"
    if object_type == "company":
        return build_company_confirmation_gap(
            subject=subject,
            catalyst_type=catalyst_type,
            polarity=polarity,
            mapping_hit=mapping_hit,
            price_context={},
        )
    if object_type == "macro":
        proxy_family = str((row.get("fundamental_proxy_evidence") or {}).get("proxy_family") or "")
        industry_name = str(row.get("display_name_cn") or row.get("industry_id") or "").strip()
        if row.get("announcement_events"):
            return f"{proxy_name} 已形成初步共振，仍需确认其能否持续映射到 {industry_name or '行业'} 表现。"
        return build_macro_confirmation_gap(proxy_name, industry_name or "相关行业", proxy_family)
    return "当前仍需更多跨层确认。"


def build_hedge_difficulty(
    *,
    object_type: str,
    etf_symbol: str | None = None,
    proxy_family: str = "",
) -> str:
    if etf_symbol:
        return HEDGE_DIFFICULTY_LOW
    if object_type == "industry":
        return HEDGE_DIFFICULTY_MEDIUM
    if object_type == "macro":
        if proxy_family in {"shipping_cycle", "broker_cycle", "hog_cycle", "utility_electricity"}:
            return HEDGE_DIFFICULTY_MEDIUM
        return HEDGE_DIFFICULTY_HIGH
    return HEDGE_DIFFICULTY_HIGH


def build_failure_mode(
    *,
    object_type: str,
    catalyst_type: str,
    hard_or_soft: str,
    polarity: str = "neutral",
    mapping_hit: dict[str, str] | None = None,
) -> str:
    if object_type == "industry":
        if polarity == "negative":
            return "若负面事件停留在少数主体而没有行业扩散，当前行业风险会被高估。"
        if catalyst_type in {"policy_regulation", "macro_geopolitical"}:
            return "若后续没有结构化事件、资金或经营数据接力，当前更可能只是叙事脉冲。"
        if catalyst_type in {"merger_restructuring", "financing_dilution"}:
            return "若资本运作停留在预案或审批阶段，当前很可能无法兑现为行业级机会。"
        if hard_or_soft == "soft":
            return "若代理变量和价格结构不能连续共振，当前更可能只是阶段性扰动。"
        return "若里程碑没有兑现为订单、业绩或扩散确认，当前强度会回落。"
    if object_type == "company":
        if polarity == "negative":
            return "若负面冲击没有向产业链扩散，它更可能停留在个股层风险。"
        if mapping_hit is None:
            return "若公司代码或行业归属判断错误，当前研究方向可能会被带偏。"
        return "若事件没有兑现为订单、资本动作或行业扩散，容易退化成一次性个股脉冲。"
    if object_type == "macro":
        return "若代理变量反弹不能延续，或与价格、结构化事件脱节，容易形成伪催化。"
    return "若后续没有新增确认，当前对象可能退回背景观察。"


def classify_evidence_quality(
    *,
    object_type: str,
    catalyst_type: str,
    event_driven_lane: str,
    flags: dict[str, bool] | None = None,
    thematic_early_signal: bool = False,
    hard_or_soft: str = "",
) -> str:
    flags = flags or {}
    if event_driven_lane == "risk_monitor":
        return EVIDENCE_QUALITY_RISK
    if object_type == "macro":
        return EVIDENCE_QUALITY_PROXY
    if object_type == "company":
        if hard_or_soft == "hard" or catalyst_type in {
            "approval_registration",
            "order_project",
            "earnings_guidance",
            "capital_markets",
            "merger_restructuring",
            "buyback_shareholder_support",
            "financing_dilution",
            "litigation_regulatory",
            "dividend_capital_return",
        }:
            return EVIDENCE_QUALITY_STRUCTURED
        return EVIDENCE_QUALITY_EARLY
    if flags.get("has_announcement"):
        return EVIDENCE_QUALITY_STRUCTURED
    if flags.get("has_proxy"):
        return EVIDENCE_QUALITY_PROXY
    if thematic_early_signal:
        return EVIDENCE_QUALITY_EARLY
    return EVIDENCE_QUALITY_NARRATIVE


def classify_triage_action(
    *,
    object_type: str,
    radar_bucket: str,
    event_driven_lane: str,
    evidence_quality: str,
    radar_score: int,
    confidence: int,
    why_now_strength: int,
    followup_value: int,
    catalyst_type: str,
    hard_or_soft: str,
    parent_bucket: str = "observe",
    parent_support_level: str = "weak",
) -> str:
    if event_driven_lane == "risk_monitor" or evidence_quality == EVIDENCE_QUALITY_RISK:
        return TRIAGE_RISK
    if evidence_quality == EVIDENCE_QUALITY_STRUCTURED:
        structured_industry_immediate = (
            object_type == "industry"
            and radar_bucket == "strong_alert"
            and confidence >= 75
            and why_now_strength >= 70
            and followup_value >= 75
        )
        if (
            radar_bucket == "strong_alert"
            and confidence >= 68
            and followup_value >= 65
            and parent_bucket in {"strong_alert", "strong_candidate", "research_candidate"}
            and parent_support_level in {"structured", "thematic"}
        ):
            return TRIAGE_IMMEDIATE
        structured_hard_immediate = (
            radar_bucket == "strong_candidate"
            and hard_or_soft == "hard"
            and catalyst_type in {
                "approval_registration",
                "order_project",
                "capital_markets",
                "merger_restructuring",
                "buyback_shareholder_support",
                "dividend_capital_return",
            }
            and confidence >= 78
            and why_now_strength >= 72
            and followup_value >= 82
            and parent_support_level != "weak"
        )
        structured_earnings_immediate = (
            radar_bucket == "strong_candidate"
            and catalyst_type == "earnings_guidance"
            and confidence >= 78
            and why_now_strength >= 72
            and followup_value >= 72
            and parent_bucket in {"strong_alert", "strong_candidate"}
            and parent_support_level in {"structured", "thematic"}
        )
        if (
            structured_industry_immediate
            or structured_hard_immediate
            or structured_earnings_immediate
        ):
            return TRIAGE_IMMEDIATE
        return TRIAGE_WATCH
    if evidence_quality == EVIDENCE_QUALITY_PROXY:
        proxy_immediate = (
            catalyst_type == "industry_proxy"
            and object_type != "macro"
            and radar_bucket == "strong_alert"
            and confidence >= 82
            and why_now_strength >= 78
            and followup_value >= 84
        )
        macro_policy_immediate = (
            object_type == "macro"
            and catalyst_type == "policy_regulation"
            and radar_bucket == "strong_alert"
            and confidence >= 80
            and why_now_strength >= 75
        )
        if proxy_immediate or macro_policy_immediate:
            return TRIAGE_IMMEDIATE
        if radar_score >= 60 or radar_bucket in {"research_candidate", "strong_candidate", "strong_alert"}:
            return TRIAGE_WATCH
        return TRIAGE_BACKGROUND
    if evidence_quality == EVIDENCE_QUALITY_EARLY:
        early_general_watch = (
            catalyst_type == "general_corporate"
            and hard_or_soft == "soft"
            and radar_bucket in {"strong_candidate", "research_candidate"}
            and radar_score >= 72
            and confidence >= 64
            and why_now_strength >= 72
            and followup_value >= 82
            and parent_support_level == "structured"
        )
        early_watch = (
            radar_bucket in {"strong_candidate", "research_candidate"}
            and radar_score >= 64
            and why_now_strength >= 68
            and followup_value >= 72
        )
        if catalyst_type == "general_corporate" and hard_or_soft == "soft":
            return TRIAGE_WATCH if early_general_watch else TRIAGE_BACKGROUND
        return TRIAGE_WATCH if early_watch else TRIAGE_BACKGROUND
    if radar_bucket == "research_candidate":
        return TRIAGE_WATCH
    return TRIAGE_BACKGROUND


def score_to_bucket(score: int) -> tuple[str, str]:
    if score >= 86:
        return "strong_alert", "strong_alert"
    if score >= 72:
        return "strong_candidate", "candidate"
    if score >= 58:
        return "research_candidate", "watch"
    return "observe", "none"


def runtime_state_from_bucket(bucket: str) -> str:
    if bucket == "strong_alert":
        return "strong_alert"
    if bucket == "strong_candidate":
        return "candidate"
    if bucket == "research_candidate":
        return "warming"
    return "cold"


def alert_level_from_bucket(bucket: str) -> str:
    if bucket == "strong_alert":
        return "strong_alert"
    if bucket == "strong_candidate":
        return "candidate"
    if bucket == "research_candidate":
        return "watch"
    return "none"


def downgrade_bucket(bucket: str, steps: int = 1) -> str:
    try:
        idx = BUCKET_SEQUENCE.index(bucket)
    except ValueError:
        return bucket
    return BUCKET_SEQUENCE[max(0, idx - steps)]


def map_bucket(row: dict[str, Any]) -> tuple[str, str]:
    state = str(row.get("industry_state") or "").strip()
    total_score = float(row.get("total_score") or 0.0)
    if state == "strong_alert":
        return "strong_alert", "strong_alert"
    if state == "candidate":
        return "strong_candidate", "candidate"
    if total_score >= 0.60:
        return "research_candidate", "watch"
    return "observe", "none"


def get_runtime_state(row: dict[str, Any]) -> str:
    state = str(row.get("industry_state") or "").strip()
    if state in RUNTIME_STATE_ORDER:
        return state
    return "cold"


def build_why_now(row: dict[str, Any]) -> str:
    reasons: list[str] = []
    if float(row.get("heat_score") or 0.0) >= 0.50:
        reasons.append("行业热度抬升")
    if float(row.get("money_flow_score") or 0.0) >= 0.60:
        reasons.append("资金结构转强")
    if float(row.get("policy_score") or 0.0) > 0:
        reasons.append("政策/舆情有增量")
    if float(row.get("announcement_score") or 0.0) > 0:
        reasons.append("结构化事件出现补充确认")
    if float(row.get("fundamental_proxy_score") or 0.0) > 0:
        reasons.append("行业代理变量出现改善")
    if float(row.get("aux_flow_score") or 0.0) >= 0.50:
        reasons.append("显性资金痕迹增强")
    if not reasons:
        reasons.append("总分与结构指标显示行业出现早期变化")
    return "，".join(reasons[:3]) + "。"


def build_key_evidence(row: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    evidence.append(f"总分 {float(row.get('total_score') or 0.0):.2f}，行业状态 {row.get('industry_state', 'unknown')}")
    evidence.append(
        f"资金 {float(row.get('money_flow_score') or 0.0):.2f} / 热度 {float(row.get('heat_score') or 0.0):.2f} / 基本面 {float(row.get('fundamental_score') or 0.0):.2f}"
    )
    if row.get("policy_articles"):
        evidence.append(f"政策/舆情事件 {len(row.get('policy_articles', []))} 条")
    if row.get("announcement_events"):
        evidence.append(f"结构化事件 {len(row.get('announcement_events', []))} 条")
    summary_cn = str((row.get("fundamental_proxy_evidence") or {}).get("summary_cn") or "").strip()
    if summary_cn:
        evidence.append(f"行业代理：{summary_cn}")
    matched = row.get("overlay_names") or []
    if matched:
        evidence.append(f"主题/产业链映射：{', '.join(str(item) for item in matched[:3])}")
    return evidence[:5]


def build_supporting_events(row: dict[str, Any]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for idx, notice in enumerate(row.get("announcement_events") or []):
        if not isinstance(notice, dict):
            continue
        title = str(notice.get("title") or notice.get("signal_tag") or f"announcement_{idx+1}").strip() or f"announcement_{idx+1}"
        events.append(
            {
                "source": "news_event_hub:structured_event",
                "event_id": f"announcement:{row.get('industry_id')}:{idx + 1}",
                "event_type": str(notice.get("signal_tag") or "").strip(),
                "title": title[:120],
                "headline": title[:120],
                "summary": title[:120],
                "published_at": str(notice.get("published_at") or "").strip(),
            }
        )
    for idx, event in enumerate(row.get("shared_events") or []):
        if not isinstance(event, dict):
            continue
        title = normalized_event_title(event)
        if not title:
            continue
        events.append(
            {
                "source": str(event.get("source_id") or event.get("source") or "news_event_hub"),
                "event_id": str(event.get("event_id") or f"shared:{row.get('industry_id')}:{idx + 1}"),
                "event_type": str(event.get("event_type") or "").strip(),
                "title": title[:120],
                "headline": title[:120],
                "summary": title[:120],
                "published_at": str(event.get("published_at") or "").strip(),
            }
        )
    proxy_summary = str((row.get("fundamental_proxy_evidence") or {}).get("summary_cn") or "").strip()
    if proxy_summary:
        events.append(
            {
                "source": f"proxy:{str((row.get('fundamental_proxy_evidence') or {}).get('proxy_family') or 'industry_proxy')}",
                "event_id": f"proxy:{row.get('industry_id')}",
                "event_type": "industry_proxy",
                "title": proxy_summary[:120],
                "headline": proxy_summary[:120],
                "summary": proxy_summary[:120],
            }
        )
    for idx, article in enumerate(row.get("policy_articles") or []):
        if not isinstance(article, dict):
            continue
        source_id = str(article.get("source_id") or "news_event_hub").strip() or "news_event_hub"
        title = str(article.get("title") or article.get("headline") or f"policy_article_{idx+1}").strip() or f"policy_article_{idx+1}"
        events.append(
            {
                "source": source_id,
                "event_id": f"policy:{row.get('industry_id')}:{idx + 1}",
                "event_type": str(article.get("event_type") or "policy_article").strip(),
                "title": title[:120],
                "headline": title[:120],
                "summary": title[:120],
                "published_at": str(article.get("published_at") or "").strip(),
            }
        )
    return dedupe_supporting_events(events, limit=8)


def ranked_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw_events: list[dict[str, Any]] = []
    for idx, notice in enumerate(row.get("announcement_events") or []):
        if not isinstance(notice, dict):
            continue
        title = str(notice.get("title") or notice.get("signal_tag") or f"announcement_{idx+1}").strip()
        if not title:
            continue
        raw_events.append(
            {
                "source": "news_event_hub:structured_event",
                "event_id": f"announcement:{row.get('industry_id')}:{idx + 1}",
                "event_type": title[:120],
                "published_at": str(notice.get("published_at") or "").strip(),
            }
        )
    for idx, event in enumerate(row.get("shared_events") or []):
        if not isinstance(event, dict):
            continue
        title = normalized_event_title(event)
        if not title:
            continue
        raw_events.append(
            {
                "source": str(event.get("source_id") or event.get("source") or "news_event_hub"),
                "event_id": str(event.get("event_id") or f"shared:{row.get('industry_id')}:{idx + 1}"),
                "event_type": title[:120],
                "published_at": str(event.get("published_at") or "").strip(),
            }
        )
    proxy_summary = str((row.get("fundamental_proxy_evidence") or {}).get("summary_cn") or "").strip()
    if proxy_summary:
        raw_events.append(
            {
                "source": f"proxy:{str((row.get('fundamental_proxy_evidence') or {}).get('proxy_family') or 'industry_proxy')}",
                "event_id": f"proxy:{row.get('industry_id')}",
                "event_type": proxy_summary[:120],
                "published_at": "",
            }
        )
    for idx, article in enumerate(row.get("policy_articles") or []):
        if not isinstance(article, dict):
            continue
        source_id = str(article.get("source_id") or "news_event_hub").strip() or "news_event_hub"
        title = str(article.get("title") or article.get("headline") or f"policy_article_{idx+1}").strip()
        if not title:
            continue
        raw_events.append(
            {
                "source": source_id,
                "event_id": f"policy:{row.get('industry_id')}:{idx + 1}",
                "event_type": title[:120],
                "published_at": str(article.get("published_at") or "").strip(),
            }
        )
    deduped: dict[str, dict[str, Any]] = {}
    for event in raw_events:
        key = supporting_event_display_key(event)
        existing = deduped.get(key)
        if existing is None or supporting_event_priority(event) < supporting_event_priority(existing):
            deduped[key] = event
    ranked: list[dict[str, Any]] = []
    for event in sorted(deduped.values(), key=supporting_event_priority)[:8]:
        text = clean_display_text(event.get("event_type") or "")
        subject = extract_event_subject(text)
        ranked.append(
            {
                "source": str(event.get("source") or ""),
                "event_id": str(event.get("event_id") or ""),
                "event_type": text,
                "subject": subject,
                "score": event_score(text, str(event.get("source") or "")),
                "polarity": event_polarity(text),
                "published_at": str(event.get("published_at") or ""),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def select_primary_industry_event(row: dict[str, Any], ranked: list[dict[str, Any]]) -> str:
    for event in ranked:
        source = str(event.get("source") or "")
        if source.startswith(("akshare:stock_notice_report", "proxy:")):
            return str(event.get("event_type") or "")
    for event in ranked:
        subject = str(event.get("subject") or "").strip()
        if int(event.get("score") or 0) >= 8 and subject and not is_generic_subject(subject):
            return str(event.get("event_type") or "")
    proxy_summary = str((row.get("fundamental_proxy_evidence") or {}).get("summary_cn") or "").strip()
    if proxy_summary:
        return proxy_summary
    return "industry_scan_snapshot"


def industry_structured_evidence_flags(row: dict[str, Any], ranked: list[dict[str, Any]]) -> dict[str, bool]:
    has_announcement = bool(row.get("announcement_events"))
    has_proxy = bool(row.get("fundamental_proxy_evidence"))
    has_named_event = False
    for event in ranked:
        source = str(event.get("source") or "")
        subject = str(event.get("subject") or "").strip()
        if source.startswith(("akshare:stock_notice_report", "proxy:")):
            continue
        if int(event.get("score") or 0) < 0:
            continue
        if subject and not is_generic_subject(subject):
            has_named_event = True
            break
    return {
        "has_announcement": has_announcement,
        "has_proxy": has_proxy,
        "has_named_event": has_named_event,
        "has_structured": has_announcement or has_proxy or has_named_event,
    }


def has_thematic_early_signal(row: dict[str, Any], ranked: list[dict[str, Any]]) -> bool:
    positive_signals = [
        event
        for event in ranked
        if int(event.get("score") or 0) >= 8
        and not str(event.get("source") or "").startswith(("akshare:stock_notice_report", "proxy:"))
    ]
    if len(positive_signals) < 2:
        return False
    has_overlay = bool(row.get("overlay_names"))
    money_flow_score = float(row.get("money_flow_score") or 0.0)
    heat_score = float(row.get("heat_score") or 0.0)
    aux_flow_score = float(row.get("aux_flow_score") or 0.0)
    return has_overlay or money_flow_score >= 0.68 or heat_score >= 0.50 or aux_flow_score >= 0.45


def apply_industry_ranking_adjustment(
    *,
    row: dict[str, Any],
    ranked: list[dict[str, Any]],
    bucket: str,
    radar_score: int,
    why_now_strength: int,
    confidence: int,
    followup_value: int,
) -> dict[str, Any]:
    flags = industry_structured_evidence_flags(row, ranked)
    adjusted_bucket = bucket
    adjusted_score = radar_score
    adjusted_why_now_strength = why_now_strength
    adjusted_confidence = confidence
    adjusted_followup_value = followup_value
    adjustment_notes: list[str] = []
    thematic_early_signal = has_thematic_early_signal(row, ranked)
    if not flags["has_structured"] and not thematic_early_signal:
        if bucket in {"research_candidate", "strong_candidate"}:
            adjusted_bucket = downgrade_bucket(bucket, 1)
            adjustment_notes.append("缺少结构化事件承接，排序 bucket 下调一档")
        adjusted_score = max(0, radar_score - 10)
        adjusted_why_now_strength = max(0, why_now_strength - 10)
        adjusted_confidence = max(0, confidence - 15)
        adjusted_followup_value = max(0, followup_value - 10)
    elif not flags["has_structured"] and thematic_early_signal:
        adjusted_score = max(0, radar_score - 4)
        adjusted_confidence = max(0, confidence - 8)
        adjusted_followup_value = max(0, followup_value - 4)
        adjustment_notes.append("缺少硬确认，但保留为早期主题/扩散线索")
    elif not flags["has_announcement"] and not flags["has_proxy"]:
        adjusted_score = max(0, radar_score - 4)
        adjusted_confidence = max(0, confidence - 6)
        adjustment_notes.append("缺少结构化事件和 proxy 双确认，排序轻度降权")
    return {
        "bucket": adjusted_bucket,
        "alert_level": alert_level_from_bucket(adjusted_bucket),
        "radar_score": adjusted_score,
        "why_now_strength": adjusted_why_now_strength,
        "confidence": adjusted_confidence,
        "followup_value": adjusted_followup_value,
        "flags": flags,
        "thematic_early_signal": thematic_early_signal,
        "notes": adjustment_notes,
    }


def cap_industry_bucket_without_actionable_trigger(
    *,
    row: dict[str, Any],
    ranked: list[dict[str, Any]],
    top_event_text: str,
    flags: dict[str, bool],
    radar_score: int,
    bucket: str,
) -> tuple[int, str, str | None]:
    cleaned = clean_display_text(top_event_text)
    money_flow_score = float(row.get("money_flow_score") or 0.0)
    has_proxy = bool(flags.get("has_proxy"))
    has_announcement = bool(flags.get("has_announcement"))
    has_named_event = bool(flags.get("has_named_event"))
    announcement_count = len(row.get("announcement_events") or [])
    named_subject_count = len(
        {
            str(event.get("subject") or "").strip()
            for event in ranked
            if str(event.get("subject") or "").strip()
            and not is_generic_subject(str(event.get("subject") or "").strip())
            and not str(event.get("source") or "").startswith("proxy:")
        }
    )
    independent_confirmation_layers = 0
    if has_proxy:
        independent_confirmation_layers += 1
    if announcement_count >= 2:
        independent_confirmation_layers += 1
    if named_subject_count >= 2:
        independent_confirmation_layers += 1
    if money_flow_score >= 0.45:
        independent_confirmation_layers += 1

    if cleaned in {"", "industry_scan_snapshot", "当前没有可展开的具体事件标题"}:
        adjusted_score = min(radar_score, 84)
        adjusted_bucket = bucket_cap(score_to_bucket(adjusted_score)[0], "strong_candidate")
        return adjusted_score, adjusted_bucket, "行业缺少可展开的核心事件标题，不进入强提醒。"

    if bucket == "strong_alert" and independent_confirmation_layers < 2:
        adjusted_score = min(radar_score, 84)
        adjusted_bucket = bucket_cap(score_to_bucket(adjusted_score)[0], "strong_candidate")
        return adjusted_score, adjusted_bucket, "行业当前只有单层确认，尚未形成足以进入强提醒的跨层共振。"

    if not has_announcement and not has_proxy:
        if bucket == "strong_alert":
            cap_name = "strong_candidate" if money_flow_score >= 0.75 and has_named_event else "research_candidate"
            adjusted_score = min(radar_score, 84 if cap_name == "strong_candidate" else 71)
            adjusted_bucket = bucket_cap(score_to_bucket(adjusted_score)[0], cap_name)
            return adjusted_score, adjusted_bucket, "行业当前主要由热度/舆情抬升，但缺少结构化事件或 proxy 双确认。"
        if bucket == "strong_candidate" and not has_named_event:
            adjusted_score = min(radar_score, 71)
            adjusted_bucket = bucket_cap(score_to_bucket(adjusted_score)[0], "research_candidate")
            return adjusted_score, adjusted_bucket, "行业仍缺可执行触发，不保留在强候选层。"

    return radar_score, bucket, None


def build_followup_path(row: dict[str, Any]) -> list[str]:
    items: list[str] = []
    rep_symbol = stringify_symbol(row.get("representative_stock_code"), row.get("representative_stock_name"))
    if rep_symbol:
        items.append(f"检查代表股是否继续确认：{rep_symbol}")
    etf_symbol = stringify_symbol(row.get("etf_proxy_code"), row.get("etf_proxy_name"))
    if etf_symbol:
        items.append(f"检查 ETF 代理是否同步确认：{etf_symbol}")
    summary_cn = str((row.get("fundamental_proxy_evidence") or {}).get("summary_cn") or "").strip()
    if summary_cn:
        items.append(f"跟踪行业代理变量是否延续：{summary_cn}")
    if row.get("policy_articles"):
        items.append("复核政策/舆情事件是否继续扩散")
    if row.get("announcement_events"):
        items.append("复核结构化事件是否改变研究判断")
    if not items:
        items.append("继续跟踪下一轮扫描是否出现新增确认")
    return items[:4]


def build_risk_flags(row: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    if not row.get("announcement_events"):
        risks.append("当前缺少结构化事件确认")
    if not row.get("fundamental_proxy_evidence"):
        risks.append("当前缺少行业代理变量确认")
    if float(row.get("heat_score") or 0.0) > 0.60 and float(row.get("money_flow_score") or 0.0) < 0.50:
        risks.append("热度强于资金确认，可能偏叙事先行")
    if str(row.get("industry_state") or "") == "warming":
        risks.append("当前仍处于早期升温阶段")
    if not risks:
        risks.append("需要继续观察下一轮扫描是否延续")
    return risks[:3]


def build_followup_value(row: dict[str, Any]) -> int:
    score = 20
    if stringify_symbol(row.get("representative_stock_code"), row.get("representative_stock_name")):
        score += 25
    if stringify_symbol(row.get("etf_proxy_code"), row.get("etf_proxy_name")):
        score += 15
    if row.get("fundamental_proxy_evidence"):
        score += 15
    if row.get("policy_articles"):
        score += 10
    if row.get("announcement_events"):
        score += 10
    if row.get("overlay_names"):
        score += 5
    return min(100, score)


def classify_parent_support_level(row: dict[str, Any]) -> str:
    if row.get("announcement_events") or row.get("fundamental_proxy_evidence"):
        return "structured"
    has_overlay = bool(row.get("overlay_names"))
    money_flow_score = float(row.get("money_flow_score") or 0.0)
    heat_score = float(row.get("heat_score") or 0.0)
    aux_flow_score = float(row.get("aux_flow_score") or 0.0)
    if has_overlay and (money_flow_score >= 0.55 or heat_score >= 0.50 or aux_flow_score >= 0.45):
        return "thematic"
    if money_flow_score >= 0.70 and heat_score >= 0.55:
        return "thematic"
    return "weak"


def build_company_why_now_strength(
    *,
    row: dict[str, Any],
    event_rank_score: int,
    catalyst_type: str,
    hard_or_soft: str,
    polarity: str,
) -> int:
    score = build_why_now_strength(row)
    score += max(0, min(16, event_rank_score))
    if catalyst_type in {
        "approval_registration",
        "order_project",
        "capital_markets",
        "merger_restructuring",
        "buyback_shareholder_support",
        "dividend_capital_return",
    }:
        score += 6
    if catalyst_type == "general_corporate" and hard_or_soft == "soft":
        score -= 6
    if polarity == "negative":
        score -= 12
    return max(0, min(100, score))


def build_company_confidence(
    *,
    row: dict[str, Any],
    event_rank_score: int,
    mapping_hit: dict[str, str] | None,
    catalyst_type: str,
    hard_or_soft: str,
) -> int:
    score = build_confidence(row)
    score += 8 if mapping_hit is not None else -12
    if hard_or_soft == "hard":
        score += 4
    if catalyst_type in {
        "approval_registration",
        "order_project",
        "capital_markets",
        "merger_restructuring",
        "buyback_shareholder_support",
        "dividend_capital_return",
    }:
        score += 6
    elif catalyst_type == "earnings_guidance":
        score += 2
    elif catalyst_type == "general_corporate":
        score -= 6
    elif catalyst_type in {"financing_dilution", "litigation_regulatory"}:
        score -= 4
    if row.get("announcement_events"):
        score += 4
    if row.get("fundamental_proxy_evidence"):
        score += 6
    score += max(-4, min(8, event_rank_score // 2))
    return max(0, min(100, score))


def build_company_followup_value(
    *,
    row: dict[str, Any],
    event_rank_score: int,
    mapping_hit: dict[str, str] | None,
    catalyst_type: str,
    hard_or_soft: str,
    polarity: str,
    overlays: list[str],
    parent_support_level: str,
) -> int:
    score = build_followup_value(row)
    score += 6 if mapping_hit is not None else -15
    if catalyst_type == "earnings_guidance":
        score -= 12
        if not row.get("fundamental_proxy_evidence"):
            score -= 8
        if not row.get("announcement_events"):
            score -= 6
    if catalyst_type == "general_corporate":
        score -= 12
        if parent_support_level != "structured":
            score -= 6
        if not row.get("announcement_events"):
            score -= 6
        if not row.get("fundamental_proxy_evidence"):
            score -= 6
    if catalyst_type in {
        "approval_registration",
        "order_project",
        "capital_markets",
        "merger_restructuring",
        "buyback_shareholder_support",
        "dividend_capital_return",
    }:
        score += 10
    if catalyst_type in {"financing_dilution", "litigation_regulatory"}:
        score -= 8
    if row.get("fundamental_proxy_evidence"):
        score += 8
    if row.get("announcement_events"):
        score += 4
    if len(overlays) >= 2:
        score += 6
    elif overlays:
        score += 3
    if parent_support_level == "structured":
        score += 8
    elif parent_support_level == "thematic":
        score += 3
    else:
        score -= 8
    if float(row.get("money_flow_score") or 0.0) >= 0.60:
        score += 4
    if float(row.get("aux_flow_score") or 0.0) >= 0.50:
        score += 4
    if event_rank_score >= 18:
        score += 4
    if hard_or_soft != "hard":
        score -= 8
    if polarity == "negative":
        score -= 20
    return max(0, min(100, score))


def company_sentiment_raw(context: dict[str, Any], key: str) -> float:
    return float(context.get(key) or 0.0) if context else 0.0


def apply_company_sentiment_score(base_score: int, sentiment_context: dict[str, Any]) -> int:
    if not sentiment_context:
        return base_score
    composite = company_sentiment_raw(sentiment_context, "company_composite_sentiment")
    event = company_sentiment_raw(sentiment_context, "company_event_sentiment")
    adjustment = int(round(composite * 10 + event * 4))
    return max(0, min(100, base_score + adjustment))


def apply_company_sentiment_why_now(base_score: int, sentiment_context: dict[str, Any]) -> int:
    if not sentiment_context:
        return base_score
    event = company_sentiment_raw(sentiment_context, "company_event_sentiment")
    market = company_sentiment_raw(sentiment_context, "company_market_sentiment")
    adjustment = int(round(event * 12 + market * 4))
    return max(0, min(100, base_score + adjustment))


def apply_company_sentiment_confidence(base_score: int, sentiment_context: dict[str, Any]) -> int:
    if not sentiment_context:
        return base_score
    composite = company_sentiment_raw(sentiment_context, "company_composite_sentiment")
    market = company_sentiment_raw(sentiment_context, "company_market_sentiment")
    adjustment = int(round(composite * 8 + market * 6))
    return max(0, min(100, base_score + adjustment))


def apply_company_sentiment_followup(base_score: int, sentiment_context: dict[str, Any]) -> int:
    if not sentiment_context:
        return base_score
    composite = company_sentiment_raw(sentiment_context, "company_composite_sentiment")
    event = company_sentiment_raw(sentiment_context, "company_event_sentiment")
    adjustment = int(round(composite * 4 + event * 3))
    return max(0, min(100, base_score + adjustment))


def quant_signal_raw(context: dict[str, Any], branch: str, key: str) -> float | None:
    if not context:
        return None
    bundle = dict(context.get("bundle_summary") or {})
    branch_payload = dict(bundle.get("signals", {}).get(branch) or {})
    return optional_number(branch_payload.get(key))


def quant_signal_text(context: dict[str, Any]) -> str:
    if not context:
        return ""
    bundle = dict(context.get("bundle_summary") or {})
    alpha = dict(bundle.get("signals", {}).get("alpha158") or {})
    timesfm = dict(bundle.get("signals", {}).get("timesfm") or {})
    parts: list[str] = []
    normalized_score = optional_number(alpha.get("normalized_score"))
    rank = optional_number(alpha.get("rank"))
    if normalized_score is not None:
        alpha_text = f"Alpha158 {normalized_score:.2f}"
        if rank is not None:
            alpha_text += f" / rank {int(rank)}"
        driver = str(alpha.get("primary_driver_label") or alpha.get("primary_driver_feature") or "").strip()
        if driver:
            alpha_text += f" / driver {driver}"
        parts.append(alpha_text)
    directional_view = str(timesfm.get("directional_view") or "").strip()
    expected_return_5d = optional_number(timesfm.get("expected_return_5d"))
    if directional_view or expected_return_5d is not None:
        timesfm_text = f"TimesFM {directional_view or 'n/a'}"
        if expected_return_5d is not None:
            timesfm_text += f" / 5d {expected_return_5d * 100:.1f}%"
        confidence_hint = str(timesfm.get("confidence_hint") or "").strip()
        if confidence_hint:
            timesfm_text += f" / {confidence_hint}"
        parts.append(timesfm_text)
    return "；".join(parts[:2])


def apply_company_quant_score(base_score: int, quant_context: dict[str, Any]) -> int:
    if not quant_context or str(quant_context.get("status") or "") != "pass":
        return base_score
    bundle = dict(quant_context.get("bundle_summary") or {})
    alpha = dict(bundle.get("signals", {}).get("alpha158") or {})
    timesfm = dict(bundle.get("signals", {}).get("timesfm") or {})
    normalized_score = optional_number(alpha.get("normalized_score"))
    expected_return_5d = optional_number(timesfm.get("expected_return_5d"))
    adjustment = 0
    if normalized_score is not None:
        if normalized_score >= 0.80:
            adjustment += 6
        elif normalized_score <= 0.20:
            adjustment -= 6
    if expected_return_5d is not None:
        if expected_return_5d >= 0.02:
            adjustment += 4
        elif expected_return_5d <= -0.02:
            adjustment -= 4
    return max(0, min(100, base_score + adjustment))


def apply_company_quant_why_now(base_score: int, quant_context: dict[str, Any]) -> int:
    if not quant_context or str(quant_context.get("status") or "") != "pass":
        return base_score
    directional_view = str(((quant_context.get("bundle_summary") or {}).get("signals", {}).get("timesfm", {}) or {}).get("directional_view") or "").strip()
    expected_return_5d = quant_signal_raw(quant_context, "timesfm", "expected_return_5d")
    adjustment = 0
    if directional_view == "bullish":
        adjustment += 6
    elif directional_view == "bearish":
        adjustment -= 6
    if expected_return_5d is not None and expected_return_5d >= 0.02:
        adjustment += 4
    return max(0, min(100, base_score + adjustment))


def apply_company_quant_confidence(base_score: int, quant_context: dict[str, Any]) -> int:
    if not quant_context or str(quant_context.get("status") or "") != "pass":
        return base_score
    normalized_score = quant_signal_raw(quant_context, "alpha158", "normalized_score")
    confidence_hint = str(((quant_context.get("bundle_summary") or {}).get("signals", {}).get("timesfm", {}) or {}).get("confidence_hint") or "").strip()
    adjustment = 0
    if normalized_score is not None:
        if normalized_score >= 0.80:
            adjustment += 4
        elif normalized_score <= 0.20:
            adjustment -= 4
    if confidence_hint in {"higher_confidence", "medium_confidence"}:
        adjustment += 3
    elif confidence_hint == "low_confidence":
        adjustment -= 3
    return max(0, min(100, base_score + adjustment))


def apply_company_quant_followup(base_score: int, quant_context: dict[str, Any]) -> int:
    if not quant_context or str(quant_context.get("status") or "") != "pass":
        return base_score
    availability = dict(quant_context.get("availability") or {})
    adjustment = 0
    if availability.get("alpha158"):
        adjustment += 2
    if availability.get("timesfm"):
        adjustment += 2
    return max(0, min(100, base_score + adjustment))


def company_context_number(context: dict[str, Any], key: str) -> float | None:
    if not context or key not in context:
        return None
    return optional_number(context.get(key))


def company_context_lag_days(context: dict[str, Any]) -> int:
    if not context:
        return 0
    try:
        return max(0, int(context.get("lag_days") or 0))
    except (TypeError, ValueError):
        return 0


def market_date_tokens(date_text: str) -> set[str]:
    text = str(date_text or "").strip()[:10]
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
    }


def event_implies_target_day_non_trading(*, text: str, event_date: str, market_date: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned or "停牌" not in cleaned:
        return False
    if any(token and token in cleaned for token in market_date_tokens(market_date)):
        return True
    if "明起停牌" in cleaned or "明日停牌" in cleaned:
        return bool(event_date and market_date and event_date < market_date)
    return False


def enrich_price_context_for_non_trading(
    *,
    price_context: dict[str, Any],
    catalyst_text: str,
    event_date: str,
    market_date: str,
) -> dict[str, Any]:
    if not price_context:
        return price_context
    lag_days = company_context_lag_days(price_context)
    as_of_date = str(price_context.get("as_of_date") or "").strip()
    previous_trade_date = previous_market_weekday_text(market_date)
    if (
        lag_days > 0
        and as_of_date
        and previous_trade_date
        and as_of_date == previous_trade_date
        and event_implies_target_day_non_trading(text=catalyst_text, event_date=event_date, market_date=market_date)
    ):
        enriched = dict(price_context)
        enriched["trading_status"] = "halt_on_market_sample_date"
        enriched["non_trading_reason"] = f"{market_date} 起停牌，当前最后有效收盘为前一交易日 {as_of_date}"
        return enriched
    return price_context


def company_price_reaction_text(context: dict[str, Any]) -> str:
    if not context:
        return ""
    lag_days = company_context_lag_days(context)
    as_of_date = str(context.get("as_of_date") or "").strip()
    trading_status = str(context.get("trading_status") or "").strip()
    daily_return = company_context_number(context, "daily_return")
    close = company_context_number(context, "close")
    amount_ratio_20 = company_context_number(context, "amount_ratio_20")
    positive_days_5d = company_context_number(context, "positive_days_5d")
    parts: list[str] = []
    if trading_status == "halt_on_market_sample_date" and as_of_date:
        parts.append(f"停牌前最后收盘 {as_of_date}")
    elif lag_days > 0 and as_of_date:
        parts.append(f"价格样本 {as_of_date}（滞后 {lag_days} 天）")
    if close is not None and close > 0:
        parts.append(f"收盘 {close:.2f}")
    if daily_return is not None:
        parts.append(f"收盘涨跌 {pct_text(daily_return)}")
    if amount_ratio_20 is not None:
        parts.append(f"20日量比 {ratio_x_text(amount_ratio_20)}")
    if positive_days_5d is not None:
        parts.append(f"近5日阳线占比 {positive_days_5d:.0%}")
    return " / ".join(parts)


def company_price_reaction_penalty(catalyst_type: str, context: dict[str, Any]) -> int:
    if catalyst_type != "earnings_guidance" or not context:
        return 0
    if company_context_lag_days(context) > 0:
        return 0
    daily_return = company_context_number(context, "daily_return")
    amount_ratio_20 = company_context_number(context, "amount_ratio_20")
    if daily_return is None:
        return 0
    penalty = 0
    if daily_return >= 0.09:
        penalty += 8
    elif daily_return >= 0.06:
        penalty += 5
    elif daily_return >= 0.04:
        penalty += 2
    if amount_ratio_20 is not None and amount_ratio_20 >= 1.8:
        penalty += 2
    return penalty


def bucket_cap(bucket: str, cap_bucket_name: str) -> str:
    try:
        current_idx = BUCKET_SEQUENCE.index(bucket)
        cap_idx = BUCKET_SEQUENCE.index(cap_bucket_name)
    except ValueError:
        return bucket
    return BUCKET_SEQUENCE[min(current_idx, cap_idx)]


def cap_company_bucket_by_triage(
    *,
    radar_score: int,
    bucket: str,
    alert_level: str,
    triage_action: str,
    catalyst_type: str,
) -> tuple[int, str, str, str]:
    if triage_action != TRIAGE_BACKGROUND:
        return radar_score, bucket, alert_level, ""
    cap_name = "observe" if catalyst_type == "general_corporate" else "research_candidate"
    adjusted_score = min(radar_score, 56 if cap_name == "observe" else 70)
    adjusted_bucket = bucket_cap(score_to_bucket(adjusted_score)[0], cap_name)
    adjusted_alert = alert_level_from_bucket(adjusted_bucket)
    note = "背景观察级公司对象不进入强候选/强提醒分层。"
    return adjusted_score, adjusted_bucket, adjusted_alert, note


def company_price_data_gate(
    *,
    catalyst_type: str,
    catalyst_text: str,
    price_context: dict[str, Any],
    event_sample_date_text: str,
    market_sample_date_text: str,
) -> dict[str, Any]:
    if not company_requires_price_confirmation(catalyst_type):
        return {"status": "pass"}
    if not price_context:
        return {
            "status": "missing_price_context",
            "score_penalty": 18,
            "bucket_cap": "research_candidate",
            "triage_cap": TRIAGE_WATCH,
            "note": "当前缺少 canonical 收盘样本，不能确认事件后的市场反应。",
            "followup": "系统自动动作：下一次生产运行先补 canonical 日线收盘、涨跌与量能；PM 只看补齐后的承接结论是否支持继续研究。",
        }
    lag_days = company_context_lag_days(price_context)
    price_sample_date = price_context_sample_date(price_context)
    event_date = event_sample_date_text[:10]
    market_date = market_sample_date_text[:10]
    if str(price_context.get("trading_status") or "") == "halt_on_market_sample_date":
        return {
            "status": "expected_non_trading_halt",
            "note": str(price_context.get("non_trading_reason") or f"{market_date or '目标交易日'} 起停牌，当前最后有效收盘为 {price_sample_date or '前一交易日'}。").strip(),
            "followup": "优先跟踪停牌进展、方案披露与复牌安排，再在复牌首日确认市场反应。",
        }
    if lag_days >= 5:
        return {
            "status": "stale_price_context",
            "score_penalty": 18,
            "bucket_cap": "research_candidate",
            "triage_cap": TRIAGE_WATCH,
            "note": f"当前价格样本停在 {price_sample_date or 'unknown'}，相对有效市场样本 {market_date or 'unknown'} 已滞后 {lag_days} 天。",
            "followup": f"系统自动动作：下一次生产运行刷新 canonical 价格样本到 {market_date or '最新交易日'}；PM 只看补齐后是否仍值得进入高优先级研究队列。",
        }
    if lag_days >= 2:
        return {
            "status": "delayed_price_context",
            "score_penalty": 8,
            "bucket_cap": "strong_candidate",
            "triage_cap": TRIAGE_WATCH,
            "note": f"当前价格样本停在 {price_sample_date or 'unknown'}，比有效市场样本落后 {lag_days} 天，交易确认仍不完整。",
            "followup": f"系统自动动作：下一次生产运行补齐 {market_date or '最新交易日'} 收盘与量能；PM 只看是否升级到立即研究。",
        }
    if event_date and market_date and event_date > market_date:
        return {
            "status": "awaiting_next_trade_day",
            "note": f"事件发生在当前有效市场样本 {market_date} 之后，首个交易日收盘反应要等下一个交易日才能确认。",
            "followup": f"系统自动动作：下一次生产运行自动刷新 {event_date} 事件后的首个交易日收盘、涨跌和量能；PM 只看首日承接是直接兑现、冲高回落还是继续扩散。",
        }
    if event_date and price_sample_date and price_sample_date < event_date:
        return {
            "status": "missing_post_event_price",
            "score_penalty": 14,
            "bucket_cap": "strong_candidate",
            "triage_cap": TRIAGE_WATCH,
            "note": f"当前最新价格停在 {price_sample_date}，尚未覆盖 {event_date} 事件后的首个交易日收盘反应。",
            "followup": f"系统自动动作：下一次生产运行补齐 {event_date} 事件后的首个交易日收盘、涨跌和量能；PM 只看是否已经先行定价。",
        }
    return {"status": "pass"}


def apply_company_price_gate(
    *,
    gate: dict[str, Any],
    composite_score: int,
    confidence: int,
    followup_value: int,
    bucket: str,
    triage_action: str,
) -> tuple[int, int, int, str, str, str]:
    status = str(gate.get("status") or "pass")
    if status in {"pass", "expected_non_trading_halt"}:
        return composite_score, confidence, followup_value, bucket, alert_level_from_bucket(bucket), triage_action
    penalty = int(gate.get("score_penalty") or 0)
    adjusted_score = max(0, composite_score - penalty)
    adjusted_confidence = max(0, confidence - max(4, penalty // 2))
    adjusted_followup = max(0, followup_value - max(4, min(12, penalty)))
    capped_bucket = bucket_cap(bucket, str(gate.get("bucket_cap") or bucket))
    adjusted_bucket = score_to_bucket(adjusted_score)[0]
    adjusted_bucket = bucket_cap(adjusted_bucket, capped_bucket)
    adjusted_alert = alert_level_from_bucket(adjusted_bucket)
    adjusted_triage = triage_action
    if str(gate.get("triage_cap") or "") == TRIAGE_WATCH and triage_action == TRIAGE_IMMEDIATE:
        adjusted_triage = TRIAGE_WATCH
    if adjusted_bucket in {"research_candidate", "observe"} and adjusted_triage == TRIAGE_IMMEDIATE:
        adjusted_triage = TRIAGE_WATCH
    return adjusted_score, adjusted_confidence, adjusted_followup, adjusted_bucket, adjusted_alert, adjusted_triage


def company_requires_price_confirmation(catalyst_type: str) -> bool:
    return catalyst_type in {
        "earnings_guidance",
        "approval_registration",
        "order_project",
        "capital_markets",
        "buyback_shareholder_support",
        "dividend_capital_return",
        "merger_restructuring",
        "financing_dilution",
    }


def build_company_confirmation_gap(
    *,
    subject: str,
    catalyst_type: str,
    polarity: str,
    mapping_hit: dict[str, str] | None,
    price_context: dict[str, Any],
) -> str:
    if polarity == "negative":
        return f"“{subject}”事件本身较明确，但是否会扩散成行业层影响仍待确认。"
    if mapping_hit is None:
        instrument = str(price_context.get("instrument") or "").strip()
        if instrument:
            return f"“{subject}”价格代码已能对应到 {instrument}，但行业归属仍待核实。"
        return f"“{subject}”事件已经出现，但公司代码与行业归属仍待核实。"
    lag_days = company_context_lag_days(price_context)
    as_of_date = str(price_context.get("as_of_date") or "").strip()
    daily_return = company_context_number(price_context, "daily_return")
    if catalyst_type == "earnings_guidance":
        if lag_days > 0 and as_of_date:
            return f"当前缺少“{subject}”事件披露当日收盘反应，现有价格样本停在 {as_of_date}，不能直接判断利好是否已被市场兑现。"
        if daily_return is not None and daily_return >= 0.07:
            return f"业绩事件已落地，但当日收盘反应 {pct_text(daily_return)}，需要确认利好是否已经被价格充分兑现。"
        return "业绩事件已经落地，接下来要确认 1-3 个交易日是否还有增量承接，以及同链条标的是否跟随。"
    if lag_days > 0 and as_of_date and company_requires_price_confirmation(catalyst_type):
        return f"事件本身已出现，但现有价格样本停在 {as_of_date}，还不能判断市场是否已经先行定价。"
    if catalyst_type == "buyback_shareholder_support":
        return "股东支持动作已披露，仍需确认是否开始执行，以及成交量和价格是否继续给出确认。"
    if catalyst_type == "order_project":
        return "订单/项目线索已经出现，仍需确认交付节奏、收入确认或更多客户跟进。"
    if catalyst_type in {"approval_registration", "dividend_capital_return", "capital_markets"}:
        return "事件本身较清晰，接下来更重要的是确认执行进度和交易层承接。"
    return f"“{subject}”事件已形成初步线索，仍需确认兑现力度和行业扩散。"


def build_company_followup_path(
    *,
    subject: str,
    resolved_industry_name: str,
    catalyst_type: str,
    etf_symbol: str | None,
    price_context: dict[str, Any],
    polarity: str,
) -> list[str]:
    items: list[str] = []
    lag_days = company_context_lag_days(price_context)
    daily_return = company_context_number(price_context, "daily_return")
    if lag_days > 0 and company_requires_price_confirmation(catalyst_type):
        items.append(f"先补“{subject}”事件披露当日或次日的收盘反应，再判断市场是否已经先行定价")
    if catalyst_type == "earnings_guidance":
        if polarity == "negative":
            items.append(f"再看“{subject}”利空披露后 1-3 个交易日是否继续走弱，而不是一次性出清")
        elif lag_days > 0:
            items.append(f"再看“{subject}”后续是否继续有增量资金承接")
        elif daily_return is not None and daily_return >= 0.07:
            items.append(f"先看“{subject}”业绩利好在收盘后是否已被充分兑现，次日还能否继续放量站稳")
        else:
            items.append(f"先看“{subject}”业绩事件后 1-3 个交易日是否继续有增量资金承接")
        items.append(f"再看 {resolved_industry_name} 同链条标的是否跟随，而不是只停留在单一个股")
    elif catalyst_type == "buyback_shareholder_support":
        items.append(f"确认“{subject}”增持/回购计划是否开始执行，并观察量价是否继续给出确认")
        items.append(f"再看 {resolved_industry_name} 代表股与 ETF 是否同步确认")
    elif catalyst_type == "order_project":
        items.append(f"确认“{subject}”订单是否进入交付/收入确认，而不是停留在事件层")
        items.append(f"再看 {resolved_industry_name} 产业链是否出现更多跟单或扩单")
    elif catalyst_type == "approval_registration":
        items.append(f"确认“{subject}”获批/注册后是否快速进入放量或商业化阶段")
        items.append(f"再看 {resolved_industry_name} 是否出现配套订单或竞品跟随")
    elif catalyst_type == "capital_markets":
        items.append(f"确认“{subject}”资本动作是否继续推进，并形成交易层确认")
    else:
        items.append(f"先核实“{subject}”事件的真实性与持续性")
        items.append(f"再看 {resolved_industry_name} 行业是否出现进一步扩散确认")
    if etf_symbol:
        items.append(f"观察相关 ETF 是否同步放量确认：{etf_symbol}")
    deduped: list[str] = []
    for item in items:
        if item and item not in deduped:
            deduped.append(item)
    return deduped[:4]


def build_direct_company_candidate_row(
    *,
    candidate: dict[str, Any],
    event: dict[str, Any],
    target_row: dict[str, Any] | None,
    overlays: list[str],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    base_row = dict(target_row or {})
    subject = normalize_company_subject(candidate_entity_name(event, candidate))
    direct_signal = direct_candidate_signal_text(candidate, event)
    verified_signal = company_enrichment_signal_text(subject, enrichment)
    prefer_verified_signal = bool(verified_signal and company_signal_is_placeholder(subject, direct_signal))
    signal_text = verified_signal if prefer_verified_signal else (direct_signal or verified_signal)
    verified_rank_floor = company_enrichment_rank_floor(enrichment=enrichment, signal_text=verified_signal) if verified_signal else 0
    event_rank = max(0.0, min(1.0, max(event_sort_score(event), float(verified_rank_floor)) / 100.0))
    announcement_events = []
    policy_articles = []
    structured_signal = bool(signal_text and not company_signal_is_placeholder(subject, signal_text) and classify_catalyst_type(signal_text, object_type="company") != "general_corporate")
    if is_structured_feed_event(event) or structured_signal:
        announcement_events.append(
            {
                "title": signal_text,
                "signal_tag": classify_catalyst_type(signal_text, object_type="company"),
                "event_state": str(event.get("event_state") or "confirmed").strip() or "confirmed",
                "score": event_sort_score(event),
            }
        )
    else:
        policy_articles.append(
            {
                "source_id": str(event.get("source_id") or event.get("source") or "news_event_hub").strip() or "news_event_hub",
                "title": signal_text,
                "headline": signal_text,
                "published_at": str(event.get("published_at") or "").strip(),
                "score": event_sort_score(event),
            }
        )
    base_row.setdefault("industry_id", str((target_row or {}).get("industry_id") or "").strip())
    base_row.setdefault("display_name_cn", str((target_row or {}).get("display_name_cn") or "").strip())
    base_row["announcement_events"] = announcement_events or list(base_row.get("announcement_events") or [])
    base_row["policy_articles"] = policy_articles or list(base_row.get("policy_articles") or [])
    base_row["shared_events"] = candidate_events(candidate)[:10]
    base_row["overlay_names"] = overlays
    base_row.setdefault("flow_signal_evidence", {})
    base_row.setdefault("fundamental_proxy_evidence", dict((target_row or {}).get("fundamental_proxy_evidence") or {}))
    base_row["announcement_score"] = max(float(base_row.get("announcement_score") or 0.0), event_rank)
    base_row["policy_score"] = max(float(base_row.get("policy_score") or 0.0), event_rank if policy_articles else 0.0)
    base_row["heat_score"] = max(float(base_row.get("heat_score") or 0.0), min(1.0, event_rank * 0.92))
    base_row["fundamental_score"] = max(float(base_row.get("fundamental_score") or 0.0), event_rank if announcement_events else 0.0)
    base_row["fundamental_proxy_score"] = float(base_row.get("fundamental_proxy_score") or 0.0)
    base_row["money_flow_score"] = float(base_row.get("money_flow_score") or 0.0)
    base_row["aux_flow_score"] = float(base_row.get("aux_flow_score") or 0.0)
    base_row["total_score"] = max(float(base_row.get("total_score") or 0.0), event_rank * 0.65)
    return base_row


def build_direct_company_candidate_object(
    candidate: dict[str, Any],
    *,
    row_index: dict[str, dict[str, Any]],
    company_mapping_index: dict[str, dict[str, str]],
    company_enrichment_index: dict[str, dict[str, Any]],
    company_price_index: dict[str, dict[str, dict[str, Any]]],
    company_sentiment_index: dict[str, dict[str, dict[str, Any]]],
    company_quant_index: dict[str, dict[str, Any]],
    market_sample_date: str,
) -> dict[str, Any] | None:
    event = candidate_primary_event(candidate)
    subject = normalize_company_subject(candidate_entity_name(event, candidate))
    if not subject:
        return None
    mapping_hit = company_mapping_index.get(subject)
    if mapping_hit is None:
        return None
    target_row = row_index.get(str((mapping_hit or {}).get("industry_id") or "").strip()) if mapping_hit else None
    resolved_industry_name = str((mapping_hit or {}).get("industry_label") or (target_row or {}).get("display_name_cn") or "").strip()
    stock_code = str((mapping_hit or {}).get("stock_code") or "").strip()
    overlays = candidate_theme_overlays(candidate, event)
    enrichment = lookup_company_enrichment(candidate, company_enrichment_index)
    synthetic_row = build_direct_company_candidate_row(candidate=candidate, event=event, target_row=target_row, overlays=overlays, enrichment=enrichment)
    direct_signal = direct_candidate_signal_text(candidate, event)
    verified_signal = company_enrichment_signal_text(subject, enrichment)
    signal_from_enrichment = bool(verified_signal and company_signal_is_placeholder(subject, direct_signal))
    catalyst_text = verified_signal if signal_from_enrichment else (direct_signal or verified_signal)
    catalyst_type = classify_catalyst_type(catalyst_text, object_type="company")
    hard_or_soft = classify_hard_or_soft(catalyst_type, catalyst_text)
    polarity = event_polarity(catalyst_text)
    event_date = event_sample_date(event)
    source_quality = candidate_source_quality(candidate, event)
    entity_anchor_confidence = max(float(candidate.get("entity_mapping_confidence") or 0.0), 0.0)
    event_rank_score = int(event_sort_score(event))
    social_signal_floor_applied = False
    if (
        str(event.get("event_type") or "").strip() == "social_signal"
        and source_quality == "trusted"
        and mapping_hit is not None
    ):
        event_rank_score = max(event_rank_score, 12)
        social_signal_floor_applied = True
    verified_rank_floor = company_enrichment_rank_floor(enrichment=enrichment, signal_text=catalyst_text)
    if verified_rank_floor:
        event_rank_score = max(event_rank_score, verified_rank_floor)
    price_context = lookup_company_price(
        company_price_index,
        subject=subject,
        stock_code=stock_code,
        company_name=str((mapping_hit or {}).get("company_name") or subject),
    )
    price_context = enrich_price_context_for_non_trading(
        price_context=price_context,
        catalyst_text=catalyst_text,
        event_date=event_date,
        market_date=market_sample_date,
    )
    price_instrument = str(price_context.get("instrument") or "").strip()
    if not stock_code and price_instrument:
        stock_code = price_instrument
    sentiment_context = lookup_company_sentiment(
        company_sentiment_index,
        subject=subject,
        stock_code=stock_code,
        company_name=str((mapping_hit or {}).get("company_name") or subject),
    )
    quant_context = lookup_quant_signal_context(
        company_quant_index,
        stock_code=stock_code,
        market_symbol=stock_code,
    )
    parent_score = clamp_score(float(synthetic_row.get("total_score") or 0.0))
    parent_bucket, _ = map_bucket(synthetic_row)
    parent_support_level = classify_parent_support_level(synthetic_row)
    composite_score = max(0, min(100, int(round(parent_score * 0.45 + min(event_rank_score, 36) + (24 if hard_or_soft == "hard" else 16)))))
    if source_quality == "trusted":
        composite_score += 6
    elif source_quality == "low":
        composite_score -= 8
    if entity_anchor_confidence >= 0.90:
        composite_score += 4
    composite_score = adjust_company_composite_score(
        base_score=composite_score,
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
        parent_support_level=parent_support_level,
        row=synthetic_row,
        polarity=polarity,
    )
    enrichment_score_bonus, enrichment_confidence_bonus, enrichment_followup_bonus = company_enrichment_bonus(enrichment)
    composite_score += enrichment_score_bonus
    composite_score = apply_company_sentiment_score(composite_score, sentiment_context)
    composite_score = apply_company_quant_score(composite_score, quant_context)
    if composite_score < 44:
        if source_quality == "trusted" and mapping_hit is not None:
            composite_score = 46
            social_signal_floor_applied = True
        else:
            return None
    bucket, alert_level = score_to_bucket(composite_score)
    if mapping_hit is None and bucket == "strong_alert":
        bucket = "strong_candidate"
        alert_level = alert_level_from_bucket(bucket)
    catalyst_stage = classify_catalyst_stage(catalyst_type, hard_or_soft, catalyst_text)
    next_milestone = build_next_milestone(
        subject,
        catalyst_type,
        catalyst_text,
        f"确认“{subject}”事件是否继续扩散到{resolved_industry_name or '同类标的'}",
    )
    milestone_due_window = classify_due_window(catalyst_type, catalyst_stage, catalyst_text)
    event_driven_lane = classify_event_driven_lane(
        hard_or_soft=hard_or_soft,
        catalyst_stage=catalyst_stage,
        radar_bucket=bucket,
        polarity=polarity,
    )
    evidence_quality = classify_evidence_quality(
        object_type="company",
        catalyst_type=catalyst_type,
        event_driven_lane=event_driven_lane,
        hard_or_soft=hard_or_soft,
    )
    why_now_strength = build_company_why_now_strength(
        row=synthetic_row,
        event_rank_score=event_rank_score,
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
        polarity=polarity,
    )
    if source_quality == "trusted":
        why_now_strength += 6
    elif source_quality == "low":
        why_now_strength -= 6
    why_now_strength = apply_company_sentiment_why_now(max(0, min(100, why_now_strength)), sentiment_context)
    why_now_strength = apply_company_quant_why_now(why_now_strength, quant_context)
    confidence = build_company_confidence(
        row=synthetic_row,
        event_rank_score=event_rank_score,
        mapping_hit=mapping_hit,
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
    )
    confidence += int(round(entity_anchor_confidence * 12))
    if source_quality == "trusted":
        confidence += 8
    elif source_quality == "low":
        confidence -= 8
    confidence += enrichment_confidence_bonus
    confidence = apply_company_sentiment_confidence(max(0, min(100, confidence)), sentiment_context)
    confidence = apply_company_quant_confidence(confidence, quant_context)
    followup_value = build_company_followup_value(
        row=synthetic_row,
        event_rank_score=event_rank_score,
        mapping_hit=mapping_hit,
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
        polarity=polarity,
        overlays=overlays,
        parent_support_level=parent_support_level,
    )
    if source_quality == "trusted":
        followup_value += 4
    followup_value += enrichment_followup_bonus
    followup_value = apply_company_sentiment_followup(max(0, min(100, followup_value)), sentiment_context)
    followup_value = apply_company_quant_followup(followup_value, quant_context)
    triage_action = classify_triage_action(
        object_type="company",
        radar_bucket=bucket,
        event_driven_lane=event_driven_lane,
        evidence_quality=evidence_quality,
        radar_score=composite_score,
        confidence=confidence,
        why_now_strength=why_now_strength,
        followup_value=followup_value,
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
        parent_bucket=parent_bucket,
        parent_support_level=parent_support_level,
    )
    price_gate = company_price_data_gate(
        catalyst_type=catalyst_type,
        catalyst_text=catalyst_text,
        price_context=price_context,
        event_sample_date_text=event_date,
        market_sample_date_text=market_sample_date,
    )
    composite_score, confidence, followup_value, bucket, alert_level, triage_action = apply_company_price_gate(
        gate=price_gate,
        composite_score=composite_score,
        confidence=confidence,
        followup_value=followup_value,
        bucket=bucket,
        triage_action=triage_action,
    )
    composite_score, bucket, alert_level, triage_cap_note = cap_company_bucket_by_triage(
        radar_score=composite_score,
        bucket=bucket,
        alert_level=alert_level,
        triage_action=triage_action,
        catalyst_type=catalyst_type,
    )
    composite_score = max(0, min(100, int(composite_score)))
    confidence = max(0, min(100, int(confidence)))
    followup_value = max(0, min(100, int(followup_value)))
    why_now_strength = max(0, min(100, int(why_now_strength)))
    confirmation_gap = build_company_confirmation_gap(
        subject=subject,
        catalyst_type=catalyst_type,
        polarity=polarity,
        mapping_hit=mapping_hit,
        price_context=price_context,
    )
    followup_path = build_company_followup_path(
        subject=subject,
        resolved_industry_name=resolved_industry_name or "同类标的",
        catalyst_type=catalyst_type,
        etf_symbol=None,
        price_context=price_context,
        polarity=polarity,
    )
    if str(price_gate.get("status") or "") != "pass":
        gate_note = str(price_gate.get("note") or "").strip()
        gate_followup = str(price_gate.get("followup") or "").strip()
        if gate_note:
            confirmation_gap = gate_note
        if gate_followup:
            followup_path = [gate_followup, *followup_path]
    hedge_difficulty = build_hedge_difficulty(object_type="company")
    failure_mode = build_failure_mode(
        object_type="company",
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
        polarity=polarity,
        mapping_hit=mapping_hit,
    )
    price_reaction = company_price_reaction_text(price_context)
    sentiment_line = compact_company_sentiment_text(sentiment_context)
    key_evidence = [
        f"新闻事件分数 {event_rank_score}，事件状态 {str(event.get('event_state') or 'unknown')}",
        f"源质量 {source_quality} / 映射锚点 {entity_anchor_confidence:.2f}",
    ]
    if resolved_industry_name:
        key_evidence.append(f"所属方向：{resolved_industry_name}")
    if stock_code:
        key_evidence.append(f"股票代码：{stock_code}")
    if sentiment_line:
        key_evidence.append(f"情绪侧车：{sentiment_line}")
    quant_line = quant_signal_text(quant_context)
    if quant_line:
        key_evidence.append(f"量化侧车：{quant_line}")
    if price_reaction:
        key_evidence.append(f"收盘反应：{price_reaction}")
    gate_note = str(price_gate.get("note") or "").strip()
    if gate_note:
        key_evidence.append(f"数据门禁：{gate_note}")
    if social_signal_floor_applied:
        key_evidence.append("保留为待补核公司线索：已命中可信来源与公司映射，但仍需上游新闻补核。")
    if str(enrichment.get("status") or "") == "pass":
        events_after, articles_after = company_enrichment_counts(enrichment)
        if signal_from_enrichment:
            key_evidence.append(
                "上游补核：events {events} / articles {articles} | lane {lane}".format(
                    events=events_after,
                    articles=articles_after,
                    lane=str(enrichment.get("lane") or "unknown"),
                )
            )
        else:
            key_evidence.append(
                "上游补核已返回相关文章，但目前仍以评论/讨论型标题为主，暂不升级为结构化公司事件。"
            )
    if triage_cap_note:
        key_evidence.append(f"分流门禁：{triage_cap_note}")
    risk_flags = [failure_mode]
    if gate_note:
        risk_flags.append(gate_note)
    if social_signal_floor_applied:
        risk_flags.append("当前主要来自社交/摘要线索，未补核前只作为待确认公司对象。")
    if triage_cap_note:
        risk_flags.append(triage_cap_note)
    why_now_label = FEED_EVENT_TYPE_LABELS.get(str(event.get("event_type") or "").strip(), "公司事件")
    if str(event.get("event_type") or "").strip() == "social_signal" and not company_signal_is_placeholder(subject, catalyst_text):
        why_now_label = "公司事件"
    return {
        "radar_object_type": "company",
        "radar_object_id": str(candidate.get("radar_object_id") or f"company:{subject}"),
        "radar_object_name": subject,
        "radar_object_scope": str(candidate.get("radar_object_scope") or (f"CN equity / {resolved_industry_name}" if resolved_industry_name else "shared_feed/company")),
        "runtime_state": runtime_state_from_bucket(bucket),
        "radar_bucket": bucket,
        "radar_score": composite_score,
        "rank_in_bucket": 0,
        "rank_overall": 0,
        "alert_level": alert_level,
        "catalyst_type": catalyst_type,
        "hard_or_soft": hard_or_soft,
        "catalyst_stage": catalyst_stage,
        "next_milestone": next_milestone,
        "milestone_due_window": milestone_due_window,
        "event_driven_lane": event_driven_lane,
        "evidence_quality": evidence_quality,
        "triage_action": triage_action,
        "confirmation_gap": confirmation_gap,
        "hedge_difficulty": hedge_difficulty,
        "failure_mode": failure_mode,
        "worth_watching": composite_score,
        "why_now_strength": why_now_strength,
        "confidence": confidence,
        "followup_value": followup_value,
        "why_now": f"新闻系统捕捉到“{subject}”的{why_now_label}，当前值得进入研究分流。",
        "key_evidence": key_evidence[:6],
        "supporting_events": candidate_supporting_events(candidate, enrichment=enrichment),
        "followup_path": followup_path,
        "risk_flags": risk_flags[:3],
        "is_new": False,
        "is_upgraded": False,
        "previous_bucket": bucket,
        "trigger_state": "report_only",
        "dedup_key": f"{str(candidate.get('radar_object_id') or f'company:{subject}')}:{bucket}",
        "primary_symbols": [stringify_symbol(stock_code, subject) or subject],
        "etf_proxies": [],
        "theme_overlays": overlays,
        "research_links": build_research_links(str(candidate.get("radar_object_id") or f"company:{subject}"), subject),
        "sentiment_context": sentiment_context,
        "price_context": price_context,
        "quant_signal_context": quant_context,
    }


def build_direct_candidate_objects(
    payload: dict[str, Any],
    *,
    row_index: dict[str, dict[str, Any]],
    company_mapping_index: dict[str, dict[str, str]],
    company_enrichment_index: dict[str, dict[str, Any]],
    company_price_index: dict[str, dict[str, dict[str, Any]]],
    company_sentiment_index: dict[str, dict[str, dict[str, Any]]],
    company_quant_index: dict[str, dict[str, Any]],
    market_sample_date: str,
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        object_type = str(candidate.get("radar_object_type") or "").strip()
        if object_type == "company":
            item = build_direct_company_candidate_object(
                candidate,
                row_index=row_index,
                company_mapping_index=company_mapping_index,
                company_enrichment_index=company_enrichment_index,
                company_price_index=company_price_index,
                company_sentiment_index=company_sentiment_index,
                company_quant_index=company_quant_index,
                market_sample_date=market_sample_date,
            )
            if item is not None:
                objects.append(item)
    return objects


def build_macro_followup_path(proxy_name: str, industry_name: str, proxy_family: str) -> list[str]:
    if proxy_family == "broker_cycle":
        return [
            "继续看两融余额 20 日增速和融资买入额是否同步抬升",
            f"再看 {industry_name} 券商股与 ETF 是否跟随确认",
        ]
    if proxy_family == "hog_cycle":
        return [
            "继续看猪价 5 日变化、猪粮比和生猪指数是否同步改善",
            f"再看 {industry_name} 养殖股是否跟随，而不是只停留在商品端波动",
        ]
    if proxy_family == "utility_electricity":
        return [
            "继续看全社会与第二产业用电同比是否同步抬升",
            f"再看 {industry_name} 是否继续获得订单或业绩层确认",
        ]
    if proxy_family == "shipping_cycle":
        return [
            "继续看 BDI 与油运/成品油运价是否同步延续",
            f"再看 {industry_name} 航运股与 ETF 是否继续承接",
        ]
    return [
        f"继续跟踪 {proxy_name} 是否延续",
        f"回看 {industry_name} 行业是否继续与该代理变量共振",
    ]


def build_macro_confirmation_gap(proxy_name: str, industry_name: str, proxy_family: str) -> str:
    if proxy_family == "broker_cycle":
        return f"{proxy_name} 已经给出方向，但仍需确认券商股、ETF 和成交结构是否同步共振。"
    if proxy_family == "hog_cycle":
        return f"{proxy_name} 已经出现改善线索，但仍需确认农林牧渔个股是否跟随，而不是只停留在现货端波动。"
    if proxy_family == "utility_electricity":
        return f"{proxy_name} 已有方向性变化，但仍需确认是否能传导到 {industry_name} 的订单或盈利线索。"
    if proxy_family == "shipping_cycle":
        return f"{proxy_name} 已有方向性变化，但仍需确认航运股和油运链是否继续承接。"
    return f"{proxy_name} 已有方向性变化，但仍缺公司结构化事件或订单层跟进。"


def adjust_company_composite_score(
    *,
    base_score: int,
    catalyst_type: str,
    hard_or_soft: str,
    parent_support_level: str,
    row: dict[str, Any],
    polarity: str,
) -> int:
    score = base_score
    if catalyst_type == "general_corporate" and hard_or_soft == "soft":
        score -= 8
        if parent_support_level == "weak":
            score -= 8
        elif parent_support_level == "thematic":
            score -= 4
        if not row.get("announcement_events"):
            score -= 4
        if not row.get("fundamental_proxy_evidence"):
            score -= 4
    if polarity == "negative" and catalyst_type == "general_corporate":
        score -= 6
    return max(0, min(100, score))


def build_why_now_strength(row: dict[str, Any]) -> int:
    score = (
        float(row.get("heat_score") or 0.0) * 0.35
        + float(row.get("policy_score") or 0.0) * 0.25
        + float(row.get("announcement_score") or 0.0) * 0.20
        + float(row.get("aux_flow_score") or 0.0) * 0.20
    )
    return clamp_score(min(1.0, score))


def build_confidence(row: dict[str, Any]) -> int:
    source_richness = 0.0
    if row.get("policy_articles"):
        source_richness += 0.20
    if row.get("announcement_events"):
        source_richness += 0.20
    if row.get("fundamental_proxy_evidence"):
        source_richness += 0.20
    score = (
        float(row.get("fundamental_score") or 0.0) * 0.40
        + float(row.get("policy_score") or 0.0) * 0.20
        + float(row.get("announcement_score") or 0.0) * 0.20
        + source_richness
    )
    return clamp_score(min(1.0, score))


def build_company_objects(
    row: dict[str, Any],
    *,
    row_index: dict[str, dict[str, Any]],
    company_mapping_index: dict[str, dict[str, str]],
    company_price_index: dict[str, dict[str, dict[str, Any]]],
    company_sentiment_index: dict[str, dict[str, dict[str, Any]]],
    company_quant_index: dict[str, dict[str, Any]],
    market_sample_date: str,
) -> list[dict[str, Any]]:
    industry_name = str(row.get("display_name_cn") or row.get("industry_id") or "").strip()
    if not industry_name:
        return []
    objects: list[dict[str, Any]] = []
    seen_subjects: set[str] = set()
    for event in ranked_events(row):
        raw_subject = str(event.get("subject") or "").strip()
        event_text = str(event.get("event_type") or "")
        subject = normalize_company_subject(raw_subject)
        if not looks_like_company_subject(subject, event_text):
            continue
        if is_low_signal_company_event(event_text):
            continue
        if subject in seen_subjects:
            continue
        mapping_hit = company_mapping_index.get(subject)
        if mapping_hit is None:
            continue
        target_row = row
        resolved_industry_name = industry_name
        stock_code = ""
        if mapping_hit is not None:
            target_row = row_index.get(mapping_hit["industry_id"], row)
            resolved_industry_name = str(mapping_hit.get("industry_label") or industry_name).strip() or industry_name
            stock_code = str(mapping_hit.get("stock_code") or "").strip()
        event_date = event_sample_date(event)
        price_context = lookup_company_price(
            company_price_index,
            subject=subject,
            stock_code=stock_code,
            company_name=str((mapping_hit or {}).get("company_name") or subject),
        )
        price_context = enrich_price_context_for_non_trading(
            price_context=price_context,
            catalyst_text=event_text,
            event_date=event_date,
            market_date=market_sample_date,
        )
        sentiment_context = lookup_company_sentiment(
            company_sentiment_index,
            subject=subject,
            stock_code=stock_code,
            company_name=str((mapping_hit or {}).get("company_name") or subject),
        )
        quant_context = lookup_quant_signal_context(
            company_quant_index,
            stock_code=stock_code,
            market_symbol=stock_code,
        )
        seen_subjects.add(subject)
        event_rank_score = int(event.get("score") or 0)
        parent_score = clamp_score(float(target_row.get("total_score") or row.get("total_score") or 0.0))
        etf_symbol = stringify_symbol(target_row.get("etf_proxy_code"), target_row.get("etf_proxy_name"))
        overlays = [str(item).strip() for item in (target_row.get("overlay_names") or []) if str(item).strip()]
        parent_bucket, _ = map_bucket(target_row)
        parent_support_level = classify_parent_support_level(target_row)
        polarity = str(event.get("polarity") or "neutral")
        catalyst_type = classify_catalyst_type(event_text, object_type="company")
        hard_or_soft = classify_hard_or_soft(catalyst_type, event_text)
        composite_score = max(0, min(100, int(round(parent_score * 0.58 + 20 + event_rank_score))))
        composite_score = adjust_company_composite_score(
            base_score=composite_score,
            catalyst_type=catalyst_type,
            hard_or_soft=hard_or_soft,
            parent_support_level=parent_support_level,
            row=target_row,
            polarity=polarity,
        )
        composite_score = apply_company_sentiment_score(composite_score, sentiment_context)
        composite_score = apply_company_quant_score(composite_score, quant_context)
        if composite_score < 48:
            continue
        bucket, alert_level = score_to_bucket(composite_score)
        catalyst_stage = classify_catalyst_stage(catalyst_type, hard_or_soft, event_text)
        next_milestone = build_next_milestone(
            subject,
            catalyst_type,
            event_text,
            f"确认“{subject}”事件是否继续扩散到 {resolved_industry_name} 行业层",
        )
        milestone_due_window = classify_due_window(catalyst_type, catalyst_stage, event_text)
        event_driven_lane = classify_event_driven_lane(
            hard_or_soft=hard_or_soft,
            catalyst_stage=catalyst_stage,
            radar_bucket=bucket,
            polarity=polarity,
        )
        evidence_quality = classify_evidence_quality(
            object_type="company",
            catalyst_type=catalyst_type,
            event_driven_lane=event_driven_lane,
            hard_or_soft=hard_or_soft,
        )
        company_why_now_strength = build_company_why_now_strength(
            row=target_row,
            event_rank_score=event_rank_score,
            catalyst_type=catalyst_type,
            hard_or_soft=hard_or_soft,
            polarity=polarity,
        )
        company_why_now_strength = apply_company_sentiment_why_now(company_why_now_strength, sentiment_context)
        company_why_now_strength = apply_company_quant_why_now(company_why_now_strength, quant_context)
        company_confidence = build_company_confidence(
            row=target_row,
            event_rank_score=event_rank_score,
            mapping_hit=mapping_hit,
            catalyst_type=catalyst_type,
            hard_or_soft=hard_or_soft,
        )
        company_confidence = apply_company_sentiment_confidence(company_confidence, sentiment_context)
        company_confidence = apply_company_quant_confidence(company_confidence, quant_context)
        company_followup_value = build_company_followup_value(
            row=target_row,
            event_rank_score=event_rank_score,
            mapping_hit=mapping_hit,
            catalyst_type=catalyst_type,
            hard_or_soft=hard_or_soft,
            polarity=polarity,
            overlays=overlays,
            parent_support_level=parent_support_level,
        )
        company_followup_value = apply_company_sentiment_followup(company_followup_value, sentiment_context)
        company_followup_value = apply_company_quant_followup(company_followup_value, quant_context)
        reaction_penalty = company_price_reaction_penalty(catalyst_type, price_context)
        triage_action = classify_triage_action(
            object_type="company",
            radar_bucket=bucket,
            event_driven_lane=event_driven_lane,
            evidence_quality=evidence_quality,
            radar_score=composite_score,
            confidence=company_confidence,
            why_now_strength=company_why_now_strength,
            followup_value=company_followup_value,
            catalyst_type=catalyst_type,
            hard_or_soft=hard_or_soft,
            parent_bucket=parent_bucket,
            parent_support_level=parent_support_level,
        )
        if reaction_penalty:
            composite_score = max(0, composite_score - reaction_penalty)
            company_confidence = max(0, company_confidence - min(4, reaction_penalty // 2))
            company_followup_value = max(0, company_followup_value - reaction_penalty)
            bucket, alert_level = score_to_bucket(composite_score)
            triage_action = classify_triage_action(
                object_type="company",
                radar_bucket=bucket,
                event_driven_lane=event_driven_lane,
                evidence_quality=evidence_quality,
                radar_score=composite_score,
                confidence=company_confidence,
                why_now_strength=company_why_now_strength,
                followup_value=company_followup_value,
                catalyst_type=catalyst_type,
                hard_or_soft=hard_or_soft,
                parent_bucket=parent_bucket,
                parent_support_level=parent_support_level,
            )
        price_gate = company_price_data_gate(
            catalyst_type=catalyst_type,
            catalyst_text=event_text,
            price_context=price_context,
            event_sample_date_text=event_date,
            market_sample_date_text=market_sample_date,
        )
        composite_score, company_confidence, company_followup_value, bucket, alert_level, triage_action = apply_company_price_gate(
            gate=price_gate,
            composite_score=composite_score,
            confidence=company_confidence,
            followup_value=company_followup_value,
            bucket=bucket,
            triage_action=triage_action,
        )
        composite_score, bucket, alert_level, triage_cap_note = cap_company_bucket_by_triage(
            radar_score=composite_score,
            bucket=bucket,
            alert_level=alert_level,
            triage_action=triage_action,
            catalyst_type=catalyst_type,
        )
        composite_score = max(0, min(100, int(composite_score)))
        company_confidence = max(0, min(100, int(company_confidence)))
        company_followup_value = max(0, min(100, int(company_followup_value)))
        company_why_now_strength = max(0, min(100, int(company_why_now_strength)))
        confirmation_gap = build_company_confirmation_gap(
            subject=subject,
            catalyst_type=catalyst_type,
            polarity=polarity,
            mapping_hit=mapping_hit,
            price_context=price_context,
        )
        hedge_difficulty = build_hedge_difficulty(object_type="company", etf_symbol=etf_symbol)
        failure_mode = build_failure_mode(
            object_type="company",
            catalyst_type=catalyst_type,
            hard_or_soft=hard_or_soft,
            polarity=polarity,
            mapping_hit=mapping_hit,
        )
        why_now = (
            f"{subject} 事件为 {resolved_industry_name} 提供了新的公司层线索。"
            if polarity != "negative"
            else f"{subject} 事件偏负面，当前更适合作为个股层风险线索，而不是直接当作 {resolved_industry_name} 行业确认。"
        )
        followup_path = build_company_followup_path(
            subject=subject,
            resolved_industry_name=resolved_industry_name,
            catalyst_type=catalyst_type,
            etf_symbol=etf_symbol,
            price_context=price_context,
            polarity=polarity,
        )
        if str(price_gate.get("status") or "") != "pass":
            gate_note = str(price_gate.get("note") or "").strip()
            gate_followup = str(price_gate.get("followup") or "").strip()
            if gate_note:
                confirmation_gap = gate_note
            if gate_followup:
                followup_path = [gate_followup, *followup_path]
        risk_flags = ["公司事件未必能直接代表整个行业。"]
        if polarity == "negative":
            risk_flags.append("当前更像个股层利空，不能直接外推成行业逻辑。")
        if mapping_hit is None:
            risk_flags.append("该个股的代码与行业归属尚未确认，当前先按事件命中的行业上下文暂挂。")
        price_reaction = company_price_reaction_text(price_context)
        if price_reaction:
            key_reaction_text = f"收盘反应：{price_reaction}"
        else:
            key_reaction_text = ""
        composite_label = str(sentiment_context.get("company_composite_label") or "")
        if composite_label == "偏弱":
            risk_flags.append("情绪侧车仍偏弱，说明事件与行情确认尚未完全共振。")
        elif composite_label == "偏强":
            followup_path.insert(1, "情绪侧车偏强，可优先核实这是不是正在被市场承认的催化。")
        if reaction_penalty >= 5:
            risk_flags.append("事件披露当日价格反应已经偏强，注意利好兑现过快。")
        gate_note = str(price_gate.get("note") or "").strip()
        if gate_note:
            risk_flags.append(gate_note)
        if triage_cap_note:
            risk_flags.append(triage_cap_note)
        primary_symbol = stringify_symbol(stock_code, subject) or subject
        key_evidence = [
            f"公司事件：{event_text}",
            f"所属方向：{resolved_industry_name}",
            f"股票代码：{stock_code}" if stock_code else "股票代码：未确认",
            f"母行业分数 {parent_score}",
        ]
        if key_reaction_text:
            key_evidence.append(key_reaction_text)
        sentiment_evidence = compact_company_sentiment_text(sentiment_context)
        if sentiment_evidence:
            key_evidence.append(sentiment_evidence)
        quant_evidence = quant_signal_text(quant_context)
        if quant_evidence:
            key_evidence.append(f"量化侧车：{quant_evidence}")
        if gate_note:
            key_evidence.append(f"数据门禁：{gate_note}")
        if triage_cap_note:
            key_evidence.append(f"分流门禁：{triage_cap_note}")
        if sentiment_context and company_sentiment_raw(sentiment_context, "company_composite_sentiment") >= 0.25:
            why_now += f" 情绪侧车显示当前公司综合情绪 {sentiment_bias_text(sentiment_context.get('company_composite_sentiment'))}。"
        elif sentiment_context and company_sentiment_raw(sentiment_context, "company_composite_sentiment") <= -0.20:
            why_now += " 但情绪侧车仍偏弱，说明这条线索还没有完全得到盘口和传播面的确认。"
        objects.append(
            {
                "radar_object_type": "company",
                "radar_object_id": f"company:{subject}",
                "radar_object_name": subject,
                "radar_object_scope": f"CN A-share company event / {resolved_industry_name}",
                "runtime_state": runtime_state_from_bucket(bucket),
                "radar_bucket": bucket,
                "radar_score": composite_score,
                "rank_in_bucket": 0,
                "rank_overall": 0,
                "alert_level": alert_level,
                "catalyst_type": catalyst_type,
                "hard_or_soft": hard_or_soft,
                "catalyst_stage": catalyst_stage,
                "next_milestone": next_milestone,
                "milestone_due_window": milestone_due_window,
                "event_driven_lane": event_driven_lane,
                "evidence_quality": evidence_quality,
                "triage_action": triage_action,
                "confirmation_gap": confirmation_gap,
                "hedge_difficulty": hedge_difficulty,
                "failure_mode": failure_mode,
                "worth_watching": composite_score,
                "why_now_strength": company_why_now_strength,
                "confidence": company_confidence,
                "followup_value": company_followup_value,
                "sentiment_context": sentiment_context,
                "price_context": price_context,
                "quant_signal_context": quant_context,
                "why_now": why_now,
                "key_evidence": key_evidence,
                "supporting_events": [
                    {
                        "source": str(event.get("source") or "radar"),
                        "event_id": str(event.get("event_id") or f"company:{subject}"),
                        "event_type": str(event.get("event_type") or "").strip(),
                        "title": event_text,
                        "headline": event_text,
                        "summary": event_text,
                        "published_at": str(event.get("published_at") or "").strip(),
                    }
                ],
                "followup_path": followup_path,
                "risk_flags": risk_flags[:4],
                "is_new": False,
                "is_upgraded": False,
                "previous_bucket": bucket,
                "trigger_state": "report_only",
                "dedup_key": f"company:{subject}:{bucket}",
                "primary_symbols": [primary_symbol],
                "etf_proxies": [etf_symbol] if etf_symbol else [],
                "theme_overlays": overlays,
                "research_links": build_research_links(f"company:{subject}", subject),
            }
        )
        if len(objects) >= 2:
            break
    return objects


def build_proxy_key_evidence(industry_name: str, proxy_family: str, proxy: dict[str, Any]) -> list[str]:
    summary = str(proxy.get("summary_cn") or PROXY_NAME_MAP.get(proxy_family) or industry_name).strip()
    if proxy_family == "shipping_cycle":
        return [
            f"{summary}",
            f"BDI {proxy.get('bdi_latest')}，3个月变化 {float(proxy.get('bdi_three_month_return') or 0.0):.1f}%",
            f"BDTI {proxy.get('freight_bdti_latest')} / BCTI {proxy.get('freight_bcti_latest')}",
        ]
    if proxy_family == "utility_electricity":
        return [
            f"{summary}",
            f"全社会用电同比 {float(proxy.get('society_electricity_yoy') or 0.0):.1f}%",
            f"第二产业用电同比 {float(proxy.get('industry2_electricity_yoy') or 0.0):.1f}%",
        ]
    if proxy_family == "hog_cycle":
        hog_feed_ratio = optional_number(proxy.get("hog_feed_ratio_latest"))
        above_6m_ma = proxy.get("hog_index_above_6m_ma")
        above_6m_ma_text = "是" if above_6m_ma is True else "否" if above_6m_ma is False else "未知"
        return [
            f"{summary}",
            f"猪价 {proxy.get('hog_price_latest')} | 5日 {pct_text(proxy.get('hog_price_5d_return'))} | 猪粮比 {hog_feed_ratio:.2f}" if hog_feed_ratio is not None else f"猪价 {proxy.get('hog_price_latest')} | 5日 {pct_text(proxy.get('hog_price_5d_return'))}",
            f"生猪指数 {proxy.get('hog_index_latest')} | 站上6月均线 {above_6m_ma_text}",
        ]
    if proxy_family == "broker_cycle":
        return [
            f"{summary}",
            f"两融余额20日 {pct_text(proxy.get('market_total_balance_20d_return'))}",
            f"融资买入额 {amount_yi_text(proxy.get('market_margin_buy_latest'))}",
        ]
    return [summary]


def build_macro_objects(row: dict[str, Any]) -> list[dict[str, Any]]:
    proxy = row.get("fundamental_proxy_evidence") or {}
    if not isinstance(proxy, dict) or not proxy:
        return []
    proxy_family = str(proxy.get("proxy_family") or "").strip()
    industry_name = str(row.get("display_name_cn") or row.get("industry_id") or "").strip()
    if not industry_name:
        return []
    proxy_name = str(PROXY_NAME_MAP.get(proxy_family) or proxy.get("summary_cn") or f"{industry_name} 代理变量").strip()
    parent_score = clamp_score(float(row.get("total_score") or 0.0))
    proxy_score = max(0, min(100, int(round(parent_score * 0.45 + float(row.get("fundamental_score") or 0.0) * 35 + 18))))
    bucket, alert_level = score_to_bucket(proxy_score)
    overlays = [str(item).strip() for item in (row.get("overlay_names") or []) if str(item).strip()]
    catalyst_type = classify_catalyst_type(proxy_name, object_type="macro", proxy_family=proxy_family)
    hard_or_soft = classify_hard_or_soft(catalyst_type, proxy_name)
    catalyst_stage = classify_catalyst_stage(catalyst_type, hard_or_soft, proxy_name)
    followup_path = build_macro_followup_path(proxy_name, industry_name, proxy_family)
    next_milestone = followup_path[0]
    milestone_due_window = classify_due_window(catalyst_type, catalyst_stage, proxy_name)
    event_driven_lane = classify_event_driven_lane(
        hard_or_soft=hard_or_soft,
        catalyst_stage=catalyst_stage,
        radar_bucket=bucket,
    )
    evidence_quality = classify_evidence_quality(
        object_type="macro",
        catalyst_type=catalyst_type,
        event_driven_lane=event_driven_lane,
        hard_or_soft=hard_or_soft,
    )
    triage_action = classify_triage_action(
        object_type="macro",
        radar_bucket=bucket,
        event_driven_lane=event_driven_lane,
        evidence_quality=evidence_quality,
        radar_score=proxy_score,
        confidence=clamp_score(float(row.get("fundamental_score") or 0.0) * 100),
        why_now_strength=clamp_score(float(row.get("fundamental_proxy_score") or 0.0) * 100),
        followup_value=82,
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
    )
    confirmation_gap = build_macro_confirmation_gap(proxy_name, industry_name, proxy_family)
    hedge_difficulty = build_hedge_difficulty(object_type="macro", proxy_family=proxy_family)
    failure_mode = build_failure_mode(
        object_type="macro",
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
    )
    return [
        {
            "radar_object_type": "macro",
            "radar_object_id": f"macro:{row.get('industry_id')}:{proxy_family or 'proxy'}",
            "radar_object_name": proxy_name,
            "radar_object_scope": f"CN macro / industry proxy / {industry_name}",
            "runtime_state": runtime_state_from_bucket(bucket),
            "radar_bucket": bucket,
            "radar_score": proxy_score,
            "rank_in_bucket": 0,
            "rank_overall": 0,
            "alert_level": alert_level,
            "catalyst_type": catalyst_type,
            "hard_or_soft": hard_or_soft,
            "catalyst_stage": catalyst_stage,
            "next_milestone": next_milestone,
            "milestone_due_window": milestone_due_window,
            "event_driven_lane": event_driven_lane,
            "evidence_quality": evidence_quality,
            "triage_action": triage_action,
            "confirmation_gap": confirmation_gap,
            "hedge_difficulty": hedge_difficulty,
            "failure_mode": failure_mode,
            "worth_watching": proxy_score,
            "why_now_strength": clamp_score(float(row.get("fundamental_proxy_score") or 0.0) * 100),
            "confidence": clamp_score(float(row.get("fundamental_score") or 0.0) * 100),
            "followup_value": 82,
            "why_now": f"{proxy_name} 对 {industry_name} 出现了值得继续跟踪的商品/代理变量变化。",
            "key_evidence": build_proxy_key_evidence(industry_name, proxy_family, proxy),
            "supporting_events": [
                {
                    "source": f"proxy:{proxy_family or 'industry_proxy'}",
                    "event_id": f"macro:{row.get('industry_id')}:{proxy_family or 'proxy'}",
                    "event_type": "industry_proxy",
                    "title": "；".join(build_proxy_key_evidence(industry_name, proxy_family, proxy)[:2]),
                    "headline": "；".join(build_proxy_key_evidence(industry_name, proxy_family, proxy)[:2]),
                    "summary": "；".join(build_proxy_key_evidence(industry_name, proxy_family, proxy)[:2]),
                }
            ],
            "followup_path": followup_path,
            "risk_flags": ["代理变量只能提供方向线索，不能单独替代行业确认。"],
            "is_new": False,
            "is_upgraded": False,
            "previous_bucket": bucket,
            "trigger_state": "report_only",
            "dedup_key": f"macro:{row.get('industry_id')}:{proxy_family or 'proxy'}:{bucket}",
            "primary_symbols": [],
            "etf_proxies": [],
            "theme_overlays": overlays,
            "research_links": build_research_links(f"macro:{row.get('industry_id')}:{proxy_family or 'proxy'}", proxy_name),
        }
    ]


def build_object(
    row: dict[str, Any],
    bark_industry_ids: set[str],
    *,
    status_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bucket, alert_level = map_bucket(row)
    industry_id = str(row.get("industry_id") or "").strip()
    total_score = float(row.get("total_score") or 0.0)
    rep_symbol = stringify_symbol(row.get("representative_stock_code"), row.get("representative_stock_name"))
    etf_symbol = stringify_symbol(row.get("etf_proxy_code"), row.get("etf_proxy_name"))
    overlays = [str(item).strip() for item in (row.get("overlay_names") or []) if str(item).strip()]
    ranked = ranked_events(row)
    base_radar_score = clamp_score(total_score)
    base_why_now_strength = build_why_now_strength(row)
    base_confidence = build_confidence(row)
    base_followup_value = build_followup_value(row)
    adjusted = apply_industry_ranking_adjustment(
        row=row,
        ranked=ranked,
        bucket=bucket,
        radar_score=base_radar_score,
        why_now_strength=base_why_now_strength,
        confidence=base_confidence,
        followup_value=base_followup_value,
    )
    bucket = str(adjusted["bucket"])
    alert_level = str(adjusted["alert_level"])
    top_event_text = select_primary_industry_event(row, ranked)
    industry_cap_score, industry_cap_bucket, industry_cap_note = cap_industry_bucket_without_actionable_trigger(
        row=row,
        ranked=ranked,
        top_event_text=top_event_text,
        flags=dict(adjusted["flags"]),
        radar_score=int(adjusted["radar_score"]),
        bucket=bucket,
    )
    bucket = industry_cap_bucket
    alert_level = alert_level_from_bucket(bucket)
    runtime_state = runtime_state_from_bucket(bucket)
    default_previous_bucket = (
        "strong_candidate"
        if bucket == "strong_alert"
        else "research_candidate"
        if bucket == "strong_candidate"
        else "observe"
    )
    is_new = industry_id in bark_industry_ids
    is_upgraded = bool(is_new and bucket == "strong_alert")
    previous_bucket = default_previous_bucket if (is_new or is_upgraded) else bucket
    trigger_state = (
        "bark_candidate"
        if bucket == "strong_alert" and is_new
        else "suppressed"
        if bucket == "strong_alert"
        else "report_only"
    )
    if status_context:
        is_new = bool(status_context.get("is_new", is_new))
        is_upgraded = bool(status_context.get("is_upgraded", is_upgraded))
        previous_bucket = str(status_context.get("previous_bucket") or previous_bucket)
        trigger_state = str(status_context.get("trigger_state") or trigger_state)
    catalyst_type = classify_catalyst_type(top_event_text, object_type="industry")
    hard_or_soft = classify_hard_or_soft(catalyst_type, top_event_text)
    catalyst_stage = classify_catalyst_stage(catalyst_type, hard_or_soft, top_event_text)
    fallback_milestone = build_followup_path(row)[0]
    next_milestone = build_next_milestone(str(row.get("display_name_cn") or industry_id), catalyst_type, top_event_text, fallback_milestone)
    milestone_due_window = classify_due_window(catalyst_type, catalyst_stage, top_event_text)
    event_polarity_text = event_polarity(top_event_text)
    event_driven_lane = classify_event_driven_lane(
        hard_or_soft=hard_or_soft,
        catalyst_stage=catalyst_stage,
        radar_bucket=bucket,
        polarity=event_polarity_text,
    )
    evidence_quality = classify_evidence_quality(
        object_type="industry",
        catalyst_type=catalyst_type,
        event_driven_lane=event_driven_lane,
        flags=dict(adjusted["flags"]),
        thematic_early_signal=bool(adjusted["thematic_early_signal"]),
        hard_or_soft=hard_or_soft,
    )
    triage_action = classify_triage_action(
        object_type="industry",
        radar_bucket=bucket,
        event_driven_lane=event_driven_lane,
        evidence_quality=evidence_quality,
        radar_score=int(industry_cap_score),
        confidence=int(adjusted["confidence"]),
        why_now_strength=int(adjusted["why_now_strength"]),
        followup_value=int(adjusted["followup_value"]),
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
    )
    confirmation_gap = build_confirmation_gap(
        object_type="industry",
        catalyst_type=catalyst_type,
        polarity=event_polarity_text,
        row=row,
    )
    hedge_difficulty = build_hedge_difficulty(object_type="industry", etf_symbol=etf_symbol)
    failure_mode = build_failure_mode(
        object_type="industry",
        catalyst_type=catalyst_type,
        hard_or_soft=hard_or_soft,
        polarity=event_polarity_text,
    )
    key_evidence = build_key_evidence(row)
    for note in adjusted["notes"]:
        key_evidence.append(f"排序调整：{note}")
    if industry_cap_note:
        key_evidence.append(f"分流门禁：{industry_cap_note}")
    return {
        "radar_object_type": "industry",
        "radar_object_id": industry_id,
        "radar_object_name": str(row.get("display_name_cn") or industry_id),
        "radar_object_scope": "CN A-share industry",
        "runtime_state": runtime_state,
        "radar_bucket": bucket,
        "radar_score": int(industry_cap_score),
        "rank_in_bucket": 0,
        "rank_overall": int(row.get("rank_desc") or 0) or 0,
        "alert_level": alert_level,
        "catalyst_type": catalyst_type,
        "hard_or_soft": hard_or_soft,
        "catalyst_stage": catalyst_stage,
        "next_milestone": next_milestone,
        "milestone_due_window": milestone_due_window,
        "event_driven_lane": event_driven_lane,
        "evidence_quality": evidence_quality,
        "triage_action": triage_action,
        "confirmation_gap": confirmation_gap,
        "hedge_difficulty": hedge_difficulty,
        "failure_mode": failure_mode,
        "worth_watching": int(industry_cap_score),
        "why_now_strength": int(adjusted["why_now_strength"]),
        "confidence": int(adjusted["confidence"]),
        "followup_value": int(adjusted["followup_value"]),
        "why_now": build_why_now(row),
        "key_evidence": key_evidence[:6],
        "supporting_events": build_supporting_events(row),
        "followup_path": build_followup_path(row),
        "risk_flags": [*build_risk_flags(row), *([industry_cap_note] if industry_cap_note else [])][:3],
        "is_new": is_new,
        "is_upgraded": is_upgraded,
        "previous_bucket": previous_bucket,
        "trigger_state": trigger_state,
        "dedup_key": f"industry:{industry_id}:{alert_level if alert_level != 'none' else bucket}",
        "primary_symbols": [rep_symbol] if rep_symbol else [],
        "etf_proxies": [etf_symbol] if etf_symbol else [],
        "theme_overlays": overlays,
        "research_links": build_research_links(industry_id, str(row.get("display_name_cn") or industry_id)),
    }


def compute_bucket_ranks(objects: list[dict[str, Any]]) -> None:
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for item in objects:
        by_bucket.setdefault(str(item["radar_bucket"]), []).append(item)
    for bucket, items in by_bucket.items():
        ordered = sorted(items, key=lambda item: (-int(item["radar_score"]), int(item["rank_overall"])))
        for idx, item in enumerate(ordered, start=1):
            item["rank_in_bucket"] = idx


def compute_overall_ranks(objects: list[dict[str, Any]]) -> None:
    for idx, item in enumerate(objects, start=1):
        item["rank_overall"] = idx


def dedupe_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in objects:
        object_id = str(item.get("radar_object_id") or "").strip()
        if not object_id:
            continue
        existing = deduped.get(object_id)
        if existing is None:
            deduped[object_id] = item
            continue
        if int(item.get("radar_score") or 0) > int(existing.get("radar_score") or 0):
            deduped[object_id] = item
    return list(deduped.values())


def build_snapshot(
    payload: dict[str, Any],
    *,
    company_enrichment: dict[str, Any] | None = None,
    quant_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(payload.get("candidates"), list):
        rows = build_rows_from_candidate_pool(payload)
        bark_industry_ids: set[str] = set()
        radar_run_id = str(payload.get("run_id") or payload.get("candidate_pool_run_id") or "candidate-pool:unknown")
        report_date = str(payload.get("report_date") or payload.get("as_of_date") or payload.get("generated_at") or "")[:10]
        market_sample_date = str(payload.get("market_sample_date") or payload.get("expected_sample_date") or report_date)[:10]
        event_window_end_date = str(payload.get("event_window_end_date") or report_date)[:10]
        expected_sample_date = str(payload.get("expected_sample_date") or market_sample_date)[:10]
        market_sample_ready_time = str(payload.get("market_sample_ready_time") or "").strip()
    else:
        rows = payload.get("all_industries")
        alerts = payload.get("alerts")
        if not isinstance(rows, list):
            raise SystemExit("Radar payload missing candidate pool or legacy all_industries list.")
        bark_industry_ids = {
            str(item.get("industry_id") or "").strip()
            for item in (alerts if isinstance(alerts, list) else [])
            if isinstance(item, dict)
        }
        radar_run_id = str(payload.get("run_id") or "scan:unknown")
        report_date = str(payload.get("run_at") or payload.get("generated_at") or "")[:10]
        market_sample_date = report_date
        event_window_end_date = report_date
        expected_sample_date = report_date
        market_sample_ready_time = ""
    row_index = {
        str(row.get("industry_id") or "").strip(): row
        for row in rows
        if isinstance(row, dict) and str(row.get("industry_id") or "").strip()
    }
    company_mapping_index = load_company_mapping_index()
    company_enrichment_index = build_company_enrichment_index(company_enrichment or {})
    market_sentiment_context = build_market_sentiment_context(target_date=market_sample_date)
    company_price_index = load_company_price_index(target_date=market_sample_date)
    company_sentiment_index = load_company_sentiment_index(target_date=market_sample_date)
    company_quant_index = build_quant_signal_index(quant_signals or {})
    objects: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        objects.append(build_object(row, bark_industry_ids))
        objects.extend(
            build_company_objects(
                row,
                row_index=row_index,
                company_mapping_index=company_mapping_index,
                company_price_index=company_price_index,
                company_sentiment_index=company_sentiment_index,
                company_quant_index=company_quant_index,
                market_sample_date=market_sample_date,
            )
        )
        objects.extend(build_macro_objects(row))
    if isinstance(payload.get("candidates"), list):
        objects.extend(
            build_direct_candidate_objects(
                payload,
                row_index=row_index,
                company_mapping_index=company_mapping_index,
                company_enrichment_index=company_enrichment_index,
                company_price_index=company_price_index,
                company_sentiment_index=company_sentiment_index,
                company_quant_index=company_quant_index,
                market_sample_date=market_sample_date,
            )
        )
    objects = dedupe_objects(objects)
    objects.sort(
        key=lambda item: (
            BUCKET_ORDER.get(str(item["radar_bucket"]), 99),
            RUNTIME_STATE_ORDER.get(str(item["runtime_state"]), 99),
            -int(item["radar_score"]),
            int(item["rank_overall"] or 0),
            str(item["radar_object_name"]),
        )
    )
    compute_bucket_ranks(objects)
    compute_overall_ranks(objects)
    summary = {
        "candidate_count": len(objects),
        "strong_alert_count": sum(1 for item in objects if item["radar_bucket"] == "strong_alert"),
        "strong_candidate_count": sum(1 for item in objects if item["radar_bucket"] == "strong_candidate"),
        "research_candidate_count": sum(1 for item in objects if item["radar_bucket"] == "research_candidate"),
        "observe_count": sum(1 for item in objects if item["radar_bucket"] == "observe"),
        "bark_trigger_count": sum(1 for item in objects if item["trigger_state"] == "bark_candidate"),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": radar_run_id,
        "radar_run_id": radar_run_id,
        "as_of_date": report_date,
        "report_date": report_date,
        "market_sample_date": market_sample_date,
        "event_window_end_date": event_window_end_date,
        "expected_sample_date": expected_sample_date,
        "market_tz": "Asia/Shanghai",
        "market_sample_ready_time": market_sample_ready_time,
        "ranking_version": "v1",
        "preference_profile": {
            "early_discovery_first": True,
            "mixed_object_board": True,
            "bucketed_report": True,
        },
        "market_sentiment_context": market_sentiment_context,
        "quant_signal_status": str((quant_signals or {}).get("status") or "skip"),
        "summary": summary,
        "objects": objects,
    }


def default_dated_output(payload: dict[str, Any]) -> Path:
    generated_at = str(payload.get("generated_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
    stamp = generated_at.replace("-", "").replace(":", "").replace("+00:00", "Z").replace("T", "T")
    stamp = stamp[:15] + "Z" if len(stamp) >= 15 and not stamp.endswith("Z") else stamp
    return DEFAULT_OUTPUT_DIR / f"radar_opportunity_snapshot_{stamp}.json"


def main() -> int:
    args = parse_args()
    payload = load_payload(args.input_candidate_pool)
    company_enrichment = load_optional_payload(args.input_company_enrichment)
    quant_signals = load_optional_payload(args.input_quant_signals)
    snapshot = build_snapshot(payload, company_enrichment=company_enrichment, quant_signals=quant_signals)
    latest_output = args.latest_output
    dated_output = args.output or default_dated_output(snapshot)
    write_json(latest_output, snapshot)
    write_json(dated_output, snapshot)
    print(
        json.dumps(
            {
                "latest_output": str(latest_output),
                "dated_output": str(dated_output),
                "candidate_count": snapshot["summary"]["candidate_count"],
                "bark_trigger_count": snapshot["summary"]["bark_trigger_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
