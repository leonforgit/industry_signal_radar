from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

from radar_config import load_config_section
from radar_industry_registry import (
    build_industry_overlay_context,
    load_theme_chain_overlay_members,
    load_theme_chain_overlays,
    parse_heat_keywords,
)
from radar_news_policy import build_news_policy_overlay as build_private_news_policy_overlay


DEFAULT_SHARED_SOURCE_ID = "shared:news_event_hub:industry_radar_feed"


def normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    for token in (" ", "_", "-", "/", "\\", "（", "）", "(", ")", "：", ":", "、"):
        text = text.replace(token, "")
    return text


def parse_iso_datetime(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def shared_news_config() -> dict[str, Any]:
    section = load_config_section(None, "shared_news_event_hub")
    return section if isinstance(section, dict) else {}


def load_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def display_name_to_industry_id(registry: list[dict[str, str]]) -> dict[str, str]:
    return {
        str(item.get("display_name_cn", "")).strip(): str(item.get("industry_id", "")).strip()
        for item in registry
        if str(item.get("display_name_cn", "")).strip() and str(item.get("industry_id", "")).strip()
    }


def build_registry_alias_map(
    registry: list[dict[str, str]],
    configured_aliases: dict[str, Any] | None = None,
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    overlay_context = build_industry_overlay_context(
        overlays=load_theme_chain_overlays(),
        members=load_theme_chain_overlay_members(),
    )
    display_lookup = display_name_to_industry_id(registry)
    if configured_aliases:
        for source_name, target_name in configured_aliases.items():
            source_key = normalize_text(str(source_name))
            industry_id = display_lookup.get(str(target_name).strip(), "")
            if source_key and industry_id:
                aliases[source_key] = industry_id
    for item in registry:
        industry_id = str(item.get("industry_id", "")).strip()
        if not industry_id:
            continue
        raw_terms = [str(item.get("display_name_cn", "")).strip()]
        raw_terms.extend(parse_heat_keywords(item.get("heat_keywords", "")))
        for overlay in overlay_context.get(industry_id, []):
            raw_terms.extend(parse_heat_keywords(overlay.get("overlay_keywords", "")))
            display_name = str(overlay.get("display_name_cn", "")).strip()
            if display_name:
                raw_terms.append(display_name)
        for term in raw_terms:
            key = normalize_text(term)
            if key and key not in aliases:
                aliases[key] = industry_id
    return aliases


def resolve_industry_id(
    feed_name: str,
    registry: list[dict[str, str]],
    alias_map: dict[str, str],
) -> str | None:
    normalized_feed = normalize_text(feed_name)
    if not normalized_feed:
        return None
    if normalized_feed in alias_map:
        return alias_map[normalized_feed]
    best_industry_id: str | None = None
    best_score = -1
    for item in registry:
        industry_id = str(item.get("industry_id", "")).strip()
        if not industry_id:
            continue
        terms = [str(item.get("display_name_cn", "")).strip()]
        terms.extend(parse_heat_keywords(item.get("heat_keywords", "")))
        for term in terms:
            normalized_term = normalize_text(term)
            if not normalized_term:
                continue
            score = -1
            if normalized_feed == normalized_term:
                score = 100 + len(normalized_term)
            elif normalized_term in normalized_feed or normalized_feed in normalized_term:
                score = 60 + min(len(normalized_feed), len(normalized_term))
            if score > best_score:
                best_score = score
                best_industry_id = industry_id
    return best_industry_id if best_score >= 60 else None


def convert_policy_articles(
    shared_items: list[dict[str, Any]],
    *,
    source_id: str,
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for item in shared_items[:5]:
        published_at = str(item.get("published_at", "")).strip()
        published_dt = parse_iso_datetime(published_at)
        converted.append(
            {
                "event_id": str(item.get("event_id", "")).strip(),
                "source_id": source_id,
                "source_label": "news_event_hub",
                "title": str(item.get("title", "")).strip(),
                "date": published_dt.date().isoformat() if published_dt else "",
                "time": published_dt.strftime("%H:%M:%S") if published_dt else "",
                "published_at": published_at,
                "timestamp_quality": "exact" if published_dt else "unknown",
                "match_reason": "shared_event",
                "source_count": int(item.get("source_count") or item.get("confirmation_count") or 0),
                "event_rank_score": float(item.get("score") or 0.0),
                "event_type": str(item.get("event_type", "")).strip(),
            }
        )
    return converted


def map_shared_status(status: str) -> str:
    mapping = {
        "ok": "pass",
        "degraded": "warn",
        "down": "fail",
    }
    return mapping.get(str(status).strip(), "warn")


def build_shared_news_policy_overlay(
    registry: list[dict[str, str]],
    run_dt: datetime,
    *,
    config: dict[str, Any] | None = None,
    fallback_builder: Callable[[list[dict[str, str]], datetime], tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    shared_cfg = config if config is not None else shared_news_config()
    if not bool(shared_cfg.get("enabled", False)):
        builder = fallback_builder or build_private_news_policy_overlay
        return builder(registry, run_dt)

    feed_path = Path(str(shared_cfg.get("industry_radar_feed_path") or "")).expanduser()
    health_path = Path(str(shared_cfg.get("source_health_path") or "")).expanduser()
    source_id = str(shared_cfg.get("source_id") or DEFAULT_SHARED_SOURCE_ID).strip() or DEFAULT_SHARED_SOURCE_ID
    fallback_to_private = bool(shared_cfg.get("fallback_to_private_overlay", True))
    max_feed_age_minutes = max(int(shared_cfg.get("max_feed_age_minutes", 90) or 90), 1)

    if not feed_path.exists():
        if fallback_to_private:
            return (fallback_builder or build_private_news_policy_overlay)(registry, run_dt)
        return (
            {str(item["industry_id"]): {"policy_score": 0.0, "policy_articles": []} for item in registry},
            [
                {
                    "source_id": source_id,
                    "status": "fail",
                    "fetched_count": 0,
                    "matched_article_count": 0,
                    "note": f"shared feed missing: {feed_path}",
                }
            ],
        )

    payload = load_json_file(feed_path)
    generated_at = parse_iso_datetime(str(payload.get("generated_at", "")).strip())
    feed_age_minutes: float | None = None
    if generated_at is not None:
        feed_age_minutes = max((run_dt.astimezone(timezone.utc) - generated_at).total_seconds() / 60.0, 0.0)
    if feed_age_minutes is not None and feed_age_minutes > max_feed_age_minutes and fallback_to_private:
        return (fallback_builder or build_private_news_policy_overlay)(registry, run_dt)

    alias_map = build_registry_alias_map(registry, configured_aliases=shared_cfg.get("industry_aliases"))
    overlay = {str(item["industry_id"]): {"policy_score": 0.0, "policy_articles": []} for item in registry}
    matched_count = 0
    unmatched: list[str] = []
    for item in payload.get("industries", []):
        if not isinstance(item, dict):
            continue
        feed_name = str(item.get("industry", "")).strip()
        industry_id = resolve_industry_id(feed_name, registry, alias_map)
        if not industry_id:
            if feed_name:
                unmatched.append(feed_name)
            continue
        matched_count += 1
        bucket = overlay[industry_id]
        bucket["policy_score"] = min(1.0, float(item.get("shared_news_score") or 0.0))
        bucket["policy_articles"] = convert_policy_articles(
            item.get("policy_articles", []),
            source_id=source_id,
        )

    note_parts = [f"shared_feed={feed_path}", f"mapped_industries={matched_count}"]
    if feed_age_minutes is not None:
        note_parts.append(f"feed_age_minutes={feed_age_minutes:.1f}")
    if unmatched:
        note_parts.append("unmatched=" + ",".join(sorted(set(unmatched))[:8]))

    aggregated_status = "pass"
    if feed_age_minutes is not None and feed_age_minutes > max_feed_age_minutes:
        aggregated_status = "warn"
    if matched_count == 0:
        aggregated_status = "warn"

    shared_health_rows = []
    if health_path.exists():
        try:
            health_payload = load_json_file(health_path)
            for row in health_payload.get("source_health", []):
                if not isinstance(row, dict):
                    continue
                shared_health_rows.append(row)
        except json.JSONDecodeError:
            note_parts.append(f"source_health_decode_failed={health_path}")

    underlying_summary = ""
    if shared_health_rows:
        counts: dict[str, int] = {"pass": 0, "warn": 0, "fail": 0}
        for row in shared_health_rows:
            counts[map_shared_status(str(row.get("status", "")))] += 1
        underlying_summary = f"underlying(pass={counts['pass']},warn={counts['warn']},fail={counts['fail']})"
        note_parts.append(underlying_summary)
        if counts["fail"] > 0:
            aggregated_status = "warn"

    return (
        overlay,
        [
            {
                "source_id": source_id,
                "status": aggregated_status,
                "fetched_count": len(payload.get("industries", [])),
                "matched_article_count": matched_count,
                "note": "; ".join(note_parts),
            }
        ],
    )
