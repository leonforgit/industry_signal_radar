from __future__ import annotations

from datetime import datetime, timedelta
from difflib import SequenceMatcher
import re
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from radar_company_mapping import company_names_by_industry, ensure_company_mapping_cache
from radar_industry_registry import (
    build_industry_overlay_context,
    load_theme_chain_overlay_members,
    load_theme_chain_overlays,
    parse_heat_keywords,
)

try:
    import akshare as ak
except ImportError:  # pragma: no cover - public CI treats AkShare as optional
    ak = None


MARKET_TZ = ZoneInfo("Asia/Shanghai")
TITLE_SIMILARITY_THRESHOLD = 0.88
COMPANY_HEADLINE_MARKERS = (
    "股份",
    "科技",
    "集团",
    "药业",
    "医药",
    "制药",
    "银行",
    "证券",
    "保险",
    "能源",
    "电气",
    "电子",
    "通信",
    "新材",
    "物流",
    "石油",
    "重工",
    "航空",
    "船舶",
    "酒店",
)


def market_date_str(run_dt: datetime) -> str:
    return run_dt.astimezone(MARKET_TZ).strftime("%Y%m%d")


def industry_keywords(item: dict[str, str]) -> list[str]:
    keywords = parse_heat_keywords(item.get("heat_keywords", ""))
    display_name = str(item.get("display_name_cn", "")).strip()
    if not keywords and display_name and display_name not in keywords:
        keywords.append(display_name)
    return [keyword for keyword in keywords if keyword]


def overlay_keywords_for_industry(
    industry_id: str,
    context: dict[str, list[dict[str, str]]] | None = None,
) -> list[str]:
    overlay_context = context
    if overlay_context is None:
        overlay_context = build_industry_overlay_context(
            overlays=load_theme_chain_overlays(),
            members=load_theme_chain_overlay_members(),
        )
    items = overlay_context.get(industry_id, [])
    keywords: list[str] = []
    for item in items:
        role = str(item.get("role", "")).strip()
        confidence = float(item.get("confidence") or 0.0)
        if role != "primary" and confidence < 0.95:
            continue
        keywords.extend(parse_heat_keywords(item.get("overlay_keywords", "")))
    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if keyword in seen:
            continue
        seen.add(keyword)
        deduped.append(keyword)
    return deduped


def article_matches(text: str, item: dict[str, str], match_keywords: list[str] | None = None) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    keywords = match_keywords if match_keywords is not None else industry_keywords(item)
    return any(keyword in normalized for keyword in keywords)


def article_matches_company_names(text: str, company_names: list[str]) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return any(company_name and company_name in normalized for company_name in company_names)


def headline_supports_company_match(title: str) -> bool:
    normalized = str(title or "").strip()
    if not normalized:
        return False
    if any(marker in normalized for marker in COMPANY_HEADLINE_MARKERS):
        return True
    for separator in ("：", ":"):
        if separator not in normalized:
            continue
        prefix = normalized.split(separator, 1)[0].strip()
        if 2 <= len(prefix) <= 16:
            return True
    return False


def empty_news_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "title",
            "content",
            "date",
            "time",
            "published_at",
            "timestamp_quality",
            "title_norm",
            "source_id",
            "source_label",
        ]
    )


def text_series(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    if column in frame.columns:
        return frame[column].astype(str)
    return pd.Series([default] * len(frame.index), index=frame.index, dtype="object")


def normalize_title_key(title: str) -> str:
    normalized = str(title or "").strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[【】\[\]（）()“”\"'‘’]", "", normalized)
    normalized = re.sub(r"[，,。.!！？?：:；;、/\\|]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_datetime_fields(
    date_text: str,
    time_text: str,
    run_dt: datetime,
    *,
    default_date_fmt: str = "%Y%m%d",
) -> tuple[str, str, str, str]:
    cleaned_date = str(date_text or "").strip()
    cleaned_time = str(time_text or "").strip()
    timestamp_quality = "missing"

    parsed_date: datetime | None = None
    if cleaned_date:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", default_date_fmt):
            try:
                parsed_date = datetime.strptime(cleaned_date, fmt)
                break
            except ValueError:
                continue
    if parsed_date is None:
        parsed_date = run_dt.astimezone(MARKET_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    cleaned_date = parsed_date.strftime("%Y-%m-%d")

    full_dt: datetime | None = None
    if cleaned_time:
        candidates = [
            f"{cleaned_date} {cleaned_time}",
            cleaned_time,
        ]
        for raw in candidates:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S%z",
            ):
                try:
                    full_dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            if full_dt is not None:
                break

    if full_dt is not None:
        if full_dt.tzinfo is None:
            full_dt = full_dt.replace(tzinfo=MARKET_TZ)
        else:
            full_dt = full_dt.astimezone(MARKET_TZ)
        cleaned_time = full_dt.strftime("%H:%M:%S")
        timestamp_quality = "full"
        return cleaned_date, cleaned_time, full_dt.isoformat(timespec="seconds"), timestamp_quality

    inferred_dt = parsed_date.replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=MARKET_TZ)
    cleaned_time = "12:00:00"
    timestamp_quality = "date_only_inferred"
    return cleaned_date, cleaned_time, inferred_dt.isoformat(timespec="seconds"), timestamp_quality


def title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def cluster_policy_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    sorted_articles = sorted(
        articles,
        key=lambda item: str(item.get("published_at", "")),
        reverse=True,
    )
    for article in sorted_articles:
        title_norm = str(article.get("title_norm", "")).strip()
        matched_cluster: dict[str, Any] | None = None
        for cluster in clusters:
            anchor = cluster["anchor"]
            same_day = str(article.get("date", "")) == str(anchor.get("date", ""))
            if not same_day:
                continue
            anchor_title_norm = str(anchor.get("title_norm", "")).strip()
            similarity = title_similarity(title_norm, anchor_title_norm)
            if title_norm and title_norm == anchor_title_norm:
                matched_cluster = cluster
                cluster["match_mode"] = "exact"
                break
            if similarity >= TITLE_SIMILARITY_THRESHOLD:
                matched_cluster = cluster
                cluster["match_mode"] = "fuzzy"
                break
        if matched_cluster is None:
            clusters.append(
                {
                    "anchor": article,
                    "articles": [article],
                    "source_ids": {str(article.get("source_id", "")).strip()},
                    "match_mode": "exact",
                }
            )
            continue
        matched_cluster["articles"].append(article)
        matched_cluster["source_ids"].add(str(article.get("source_id", "")).strip())

    collapsed: list[dict[str, Any]] = []
    for cluster in clusters:
        anchor = cluster["anchor"]
        source_ids = sorted(source_id for source_id in cluster["source_ids"] if source_id)
        collapsed.append(
            {
                "source_id": str(anchor.get("source_id", "")).strip(),
                "source_label": str(anchor.get("source_label", "")).strip(),
                "source_ids": source_ids,
                "source_count": len(source_ids),
                "title": str(anchor.get("title", "")).strip(),
                "date": str(anchor.get("date", "")).strip(),
                "time": str(anchor.get("time", "")).strip(),
                "published_at": str(anchor.get("published_at", "")).strip(),
                "timestamp_quality": str(anchor.get("timestamp_quality", "")).strip(),
                "cluster_size": len(cluster["articles"]),
                "dedupe_mode": str(cluster.get("match_mode", "exact")),
            }
        )
    return collapsed


def fetch_cctv_news(run_dt: datetime) -> tuple[pd.DataFrame, dict[str, Any]]:
    if ak is None:
        return empty_news_frame(), {
            "source_id": "akshare:news_cctv",
            "status": "warn",
            "used_date": market_date_str(run_dt),
            "fetched_count": 0,
            "matched_article_count": 0,
            "note": "missing_optional_dependency=akshare",
        }
    primary_date = market_date_str(run_dt)
    fallback_date = (run_dt.astimezone(MARKET_TZ) - timedelta(days=1)).strftime("%Y%m%d")
    for date_str in [primary_date, fallback_date]:
        try:
            frame = ak.news_cctv(date=date_str).copy()
        except Exception as exc:
            return empty_news_frame(), {
                "source_id": "akshare:news_cctv",
                "status": "warn",
                "used_date": date_str,
                "fetched_count": 0,
                "matched_article_count": 0,
                "note": f"error={type(exc).__name__}: {exc}",
            }
        if frame.empty:
            continue
        normalized = pd.DataFrame(
            {
                "title": text_series(frame, "title"),
                "content": text_series(frame, "content"),
                "date": [normalize_datetime_fields(value, "", run_dt, default_date_fmt="%Y%m%d")[0] for value in text_series(frame, "date", date_str)],
                "time": [normalize_datetime_fields(value, "", run_dt, default_date_fmt="%Y%m%d")[1] for value in text_series(frame, "date", date_str)],
                "published_at": [normalize_datetime_fields(value, "", run_dt, default_date_fmt="%Y%m%d")[2] for value in text_series(frame, "date", date_str)],
                "timestamp_quality": [normalize_datetime_fields(value, "", run_dt, default_date_fmt="%Y%m%d")[3] for value in text_series(frame, "date", date_str)],
                "title_norm": text_series(frame, "title").map(normalize_title_key),
                "source_id": "akshare:news_cctv",
                "source_label": "news_cctv",
            }
        )
        return normalized, {
            "source_id": "akshare:news_cctv",
            "status": "pass",
            "used_date": date_str,
            "fetched_count": len(normalized.index),
            "matched_article_count": 0,
            "note": f"used_date={date_str}",
        }
    return empty_news_frame(), {
        "source_id": "akshare:news_cctv",
        "status": "warn",
        "used_date": primary_date,
        "fetched_count": 0,
        "matched_article_count": 0,
        "note": f"used_date={primary_date}; empty",
    }


def fetch_cls_news() -> tuple[pd.DataFrame, dict[str, Any]]:
    if ak is None:
        return empty_news_frame(), {
            "source_id": "akshare:stock_info_global_cls",
            "status": "warn",
            "used_date": "",
            "fetched_count": 0,
            "matched_article_count": 0,
            "note": "missing_optional_dependency=akshare",
        }
    try:
        frame = ak.stock_info_global_cls().copy()
    except Exception as exc:
        return empty_news_frame(), {
            "source_id": "akshare:stock_info_global_cls",
            "status": "warn",
            "used_date": "",
            "fetched_count": 0,
            "matched_article_count": 0,
            "note": f"error={type(exc).__name__}: {exc}",
        }
    if frame.empty:
        return empty_news_frame(), {
            "source_id": "akshare:stock_info_global_cls",
            "status": "warn",
            "used_date": "",
            "fetched_count": 0,
            "matched_article_count": 0,
            "note": "empty",
        }
    normalized = pd.DataFrame(
        {
            "title": text_series(frame, "标题"),
            "content": text_series(frame, "内容"),
            "date": [normalize_datetime_fields(date_text, time_text, datetime.now(MARKET_TZ))[0] for date_text, time_text in zip(text_series(frame, "发布日期"), text_series(frame, "发布时间"))],
            "time": [normalize_datetime_fields(date_text, time_text, datetime.now(MARKET_TZ))[1] for date_text, time_text in zip(text_series(frame, "发布日期"), text_series(frame, "发布时间"))],
            "published_at": [normalize_datetime_fields(date_text, time_text, datetime.now(MARKET_TZ))[2] for date_text, time_text in zip(text_series(frame, "发布日期"), text_series(frame, "发布时间"))],
            "timestamp_quality": [normalize_datetime_fields(date_text, time_text, datetime.now(MARKET_TZ))[3] for date_text, time_text in zip(text_series(frame, "发布日期"), text_series(frame, "发布时间"))],
            "title_norm": text_series(frame, "标题").map(normalize_title_key),
            "source_id": "akshare:stock_info_global_cls",
            "source_label": "stock_info_global_cls",
        }
    )
    latest_date = str(normalized["date"].iloc[0]) if not normalized.empty else ""
    return normalized, {
        "source_id": "akshare:stock_info_global_cls",
        "status": "pass",
        "used_date": latest_date,
        "fetched_count": len(normalized.index),
        "matched_article_count": 0,
        "note": f"used_date={latest_date}",
    }


def fetch_em_news() -> tuple[pd.DataFrame, dict[str, Any]]:
    if ak is None:
        return empty_news_frame(), {
            "source_id": "akshare:stock_info_global_em",
            "status": "warn",
            "used_date": "",
            "fetched_count": 0,
            "matched_article_count": 0,
            "note": "missing_optional_dependency=akshare",
        }
    try:
        frame = ak.stock_info_global_em().copy()
    except Exception as exc:
        return empty_news_frame(), {
            "source_id": "akshare:stock_info_global_em",
            "status": "warn",
            "used_date": "",
            "fetched_count": 0,
            "matched_article_count": 0,
            "note": f"error={type(exc).__name__}: {exc}",
        }
    if frame.empty:
        return empty_news_frame(), {
            "source_id": "akshare:stock_info_global_em",
            "status": "warn",
            "used_date": "",
            "fetched_count": 0,
            "matched_article_count": 0,
            "note": "empty",
        }
    published_at = text_series(frame, "发布时间")
    normalized = pd.DataFrame(
        {
            "title": text_series(frame, "标题"),
            "content": text_series(frame, "摘要"),
            "date": [normalize_datetime_fields("", value, datetime.now(MARKET_TZ))[0] for value in published_at],
            "time": [normalize_datetime_fields("", value, datetime.now(MARKET_TZ))[1] for value in published_at],
            "published_at": [normalize_datetime_fields("", value, datetime.now(MARKET_TZ))[2] for value in published_at],
            "timestamp_quality": [normalize_datetime_fields("", value, datetime.now(MARKET_TZ))[3] for value in published_at],
            "title_norm": text_series(frame, "标题").map(normalize_title_key),
            "source_id": "akshare:stock_info_global_em",
            "source_label": "stock_info_global_em",
        }
    )
    latest_date = str(normalized["date"].iloc[0]) if not normalized.empty else ""
    return normalized, {
        "source_id": "akshare:stock_info_global_em",
        "status": "pass",
        "used_date": latest_date,
        "fetched_count": len(normalized.index),
        "matched_article_count": 0,
        "note": f"used_date={latest_date}",
    }


def build_news_policy_overlay(
    registry: list[dict[str, str]], run_dt: datetime
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    overlay_context = build_industry_overlay_context(
        overlays=load_theme_chain_overlays(),
        members=load_theme_chain_overlay_members(),
    )
    item_keywords: dict[str, list[str]] = {}
    company_mapping_payload = ensure_company_mapping_cache(
        registry,
        build_if_missing=False,
        refresh_stale=False,
    )
    company_name_map = company_names_by_industry(company_mapping_payload)
    for item in registry:
        combined = industry_keywords(item)
        combined.extend(overlay_keywords_for_industry(item["industry_id"], overlay_context))
        deduped: list[str] = []
        seen: set[str] = set()
        for keyword in combined:
            if keyword in seen:
                continue
            seen.add(keyword)
            deduped.append(keyword)
        item_keywords[item["industry_id"]] = deduped

    source_frames_and_meta = [
        fetch_cctv_news(run_dt),
        fetch_cls_news(),
        fetch_em_news(),
    ]
    overlay: dict[str, dict[str, Any]] = {
        item["industry_id"]: {
            "policy_score": 0.0,
            "policy_articles": [],
        }
        for item in registry
    }
    source_health: list[dict[str, Any]] = []

    for frame, meta in source_frames_and_meta:
        matched_article_count = 0
        for _, row in frame.iterrows():
            title = str(row.get("title", "")).strip()
            content = str(row.get("content", "")).strip()
            combined = f"{title}\n{content[:240]}"
            if not combined.strip():
                continue
            for item in registry:
                keyword_hit = article_matches(combined, item, item_keywords.get(item["industry_id"], []))
                company_hit = False
                if not keyword_hit and headline_supports_company_match(title):
                    company_hit = article_matches_company_names(
                        title,
                        company_name_map.get(item["industry_id"], []),
                    )
                if not keyword_hit and not company_hit:
                    continue
                payload = {
                    "source_id": str(row.get("source_id", meta.get("source_id", ""))),
                    "source_label": str(row.get("source_label", "")),
                    "title": title,
                    "date": str(row.get("date", meta.get("used_date", ""))),
                    "time": str(row.get("time", "")).strip(),
                    "published_at": str(row.get("published_at", "")).strip(),
                    "timestamp_quality": str(row.get("timestamp_quality", "")).strip(),
                    "title_norm": str(row.get("title_norm", "")).strip(),
                    "match_reason": "company_name" if company_hit and not keyword_hit else "keyword",
                }
                bucket = overlay[item["industry_id"]]
                if payload not in bucket["policy_articles"]:
                    bucket["policy_articles"].append(payload)
                    matched_article_count += 1
        meta["matched_article_count"] = matched_article_count
        source_health.append(meta)

    for value in overlay.values():
        clustered = cluster_policy_articles(value["policy_articles"])
        value["policy_articles"] = clustered[:5]
        score = 0.0
        for article in value["policy_articles"]:
            article_weight = 0.22
            if int(article.get("source_count", 1) or 1) >= 2:
                article_weight += 0.08
            if str(article.get("timestamp_quality", "")) == "full":
                article_weight += 0.02
            score += min(article_weight, 0.35)
        value["policy_score"] = min(1.0, score)

    return overlay, source_health
