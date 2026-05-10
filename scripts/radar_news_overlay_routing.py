from __future__ import annotations

from pathlib import Path
from typing import Any

from radar_industry_registry import load_non_industry_news_overlays, parse_heat_keywords


ROOT = Path(__file__).resolve().parent.parent


def overlay_keywords(item: dict[str, str]) -> list[str]:
    return [keyword for keyword in parse_heat_keywords(item.get("overlay_keywords", "")) if keyword]


def classify_non_industry_overlay(
    text: str,
    overlays: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    overlay_rows = overlays if overlays is not None else load_non_industry_news_overlays()
    best_match: dict[str, Any] | None = None
    for item in overlay_rows:
        if str(item.get("status", "")).strip() != "active":
            continue
        matched_keywords = [keyword for keyword in overlay_keywords(item) if keyword in normalized]
        if not matched_keywords:
            continue
        payload = {
            "overlay_id": str(item.get("overlay_id", "")).strip(),
            "display_name_cn": str(item.get("display_name_cn", "")).strip(),
            "route_kind": str(item.get("route_kind", "")).strip(),
            "matched_keywords": matched_keywords[:5],
            "keyword_count": len(matched_keywords),
            "score": (
                len(matched_keywords),
                max(len(keyword) for keyword in matched_keywords),
                len(str(item.get("display_name_cn", "")).strip()),
            ),
        }
        if best_match is None or payload["score"] > best_match["score"]:
            best_match = payload
    if best_match is None:
        return None
    best_match.pop("score", None)
    return best_match
