from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = ROOT / "data" / "industry_registry_sw_level1.csv"
DEFAULT_ETF_PROXY_PATH = ROOT / "data" / "industry_etf_proxy_candidates.csv"
DEFAULT_ETF_PRIMARY_PATH = ROOT / "data" / "industry_etf_proxies_primary.csv"
DEFAULT_STOCK_PROXY_PATH = ROOT / "data" / "industry_representative_stock_candidates.csv"
DEFAULT_STOCK_PRIMARY_PATH = ROOT / "data" / "industry_representative_stocks_primary.csv"
DEFAULT_THEME_OVERLAY_PATH = ROOT / "data" / "theme_chain_overlays.csv"
DEFAULT_THEME_MEMBER_PATH = ROOT / "data" / "theme_chain_overlay_members.csv"
DEFAULT_NON_INDUSTRY_NEWS_OVERLAY_PATH = ROOT / "data" / "non_industry_news_overlays.csv"


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [{str(key): str(value or "") for key, value in row.items()} for row in rows]


def load_industry_registry(path: Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    return load_csv_rows(target)


def parse_heat_keywords(raw: str) -> list[str]:
    return [item.strip() for item in str(raw).split(";") if item.strip()]


def load_industry_etf_proxy_candidates(path: Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path is not None else DEFAULT_ETF_PROXY_PATH
    return load_csv_rows(target)


def load_industry_etf_primary(path: Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path is not None else DEFAULT_ETF_PRIMARY_PATH
    return load_csv_rows(target)


def load_industry_stock_proxy_candidates(path: Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path is not None else DEFAULT_STOCK_PROXY_PATH
    return load_csv_rows(target)


def load_industry_stock_primary(path: Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path is not None else DEFAULT_STOCK_PRIMARY_PATH
    return load_csv_rows(target)


def load_theme_chain_overlays(path: Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path is not None else DEFAULT_THEME_OVERLAY_PATH
    return load_csv_rows(target)


def load_theme_chain_overlay_members(path: Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path is not None else DEFAULT_THEME_MEMBER_PATH
    return load_csv_rows(target)


def load_non_industry_news_overlays(path: Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path is not None else DEFAULT_NON_INDUSTRY_NEWS_OVERLAY_PATH
    return load_csv_rows(target)


def build_industry_overlay_context(
    overlays: list[dict[str, str]] | None = None,
    members: list[dict[str, str]] | None = None,
) -> dict[str, list[dict[str, str]]]:
    overlay_rows = overlays if overlays is not None else load_theme_chain_overlays()
    member_rows = members if members is not None else load_theme_chain_overlay_members()
    overlay_map = {str(item.get("overlay_id", "")): item for item in overlay_rows}
    context: dict[str, list[dict[str, str]]] = {}
    for member in member_rows:
        industry_id = str(member.get("industry_id", "")).strip()
        overlay_id = str(member.get("overlay_id", "")).strip()
        if not industry_id or not overlay_id:
            continue
        overlay_item = overlay_map.get(overlay_id, {})
        payload = {
            "overlay_id": overlay_id,
            "display_name_cn": str(overlay_item.get("display_name_cn", "")),
            "object_kind": str(overlay_item.get("object_kind", "")),
            "status": str(overlay_item.get("status", "")),
            "overlay_keywords": str(overlay_item.get("overlay_keywords", "")),
            "role": str(member.get("role", "")),
            "confidence": str(member.get("confidence", "")),
            "notes": str(overlay_item.get("notes", "")),
        }
        context.setdefault(industry_id, []).append(payload)
    for industry_id, items in context.items():
        items.sort(
            key=lambda item: (
                0 if str(item.get("role", "")) == "primary" else 1,
                -(float(item.get("confidence") or 0.0)),
                str(item.get("display_name_cn", "")),
            )
        )
        context[industry_id] = items
    return context
