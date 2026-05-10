from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from radar_config import load_config_section

try:
    import akshare as ak
except ImportError:  # pragma: no cover - exercised by public CI without optional providers
    ak = None


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_MAX_AGE_HOURS = 24


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def is_st_name(name: str) -> bool:
    normalized = str(name).upper().replace(" ", "")
    return "ST" in normalized


def clean_constituent_frame(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    cleaned["名称"] = cleaned["名称"].astype(str)
    cleaned = cleaned[~cleaned["名称"].map(is_st_name)].copy()
    if "成交额" in cleaned.columns:
        cleaned["成交额"] = safe_numeric(cleaned["成交额"]).fillna(0)
    else:
        cleaned["成交额"] = 0.0
    return cleaned


def is_usable_company_name(name: str) -> bool:
    normalized = str(name).strip()
    if not normalized:
        return False
    if is_st_name(normalized):
        return False
    if len(normalized) >= 4:
        return True
    keep_suffixes = (
        "科技",
        "股份",
        "药业",
        "银行",
        "证券",
        "保险",
        "能源",
        "电气",
        "电子",
        "通信",
    )
    return len(normalized) >= 3 and normalized.endswith(keep_suffixes)


def resolve_cache_dir() -> Path:
    runtime_paths = load_config_section(None, "runtime_paths")
    raw = runtime_paths.get("cache_dir")
    base = Path(str(raw)) if raw else ROOT / "cache"
    if base.is_absolute() and str(base).startswith("/root/"):
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            base = ROOT / "cache"
    target = base / "company_name_index"
    target.mkdir(parents=True, exist_ok=True)
    return target


def cache_path() -> Path:
    return resolve_cache_dir() / "industry_company_name_index.json"


def empty_company_mapping_payload() -> dict[str, Any]:
    return {
        "generated_at": "",
        "source_id": "akshare:stock_board_industry_cons_em",
        "entry_count": 0,
        "rows": [],
        "status": "empty",
    }


def load_cached_company_mapping(path: Path | None = None) -> dict[str, Any] | None:
    target = path or cache_path()
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_cache_fresh(payload: dict[str, Any], max_age_hours: int) -> bool:
    generated_at = str(payload.get("generated_at", "")).strip()
    if not generated_at:
        return False
    try:
        generated_dt = datetime.fromisoformat(generated_at)
    except ValueError:
        return False
    if generated_dt.tzinfo is None:
        generated_dt = generated_dt.replace(tzinfo=timezone.utc)
    return utc_now() - generated_dt.astimezone(timezone.utc) <= timedelta(hours=max_age_hours)


def build_company_mapping_payload(registry: list[dict[str, str]]) -> dict[str, Any]:
    if ak is None:
        payload = empty_company_mapping_payload()
        payload["status"] = "missing_optional_dependency"
        payload["errors"] = [
            {
                "dependency": "akshare",
                "error": "Install akshare to build the live company mapping cache.",
            }
        ]
        return payload

    rows: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for item in registry:
        board_name = str(item.get("display_name_cn", "")).strip()
        if not board_name:
            continue
        try:
            frame = clean_constituent_frame(ak.stock_board_industry_cons_em(symbol=board_name))
        except Exception as exc:
            errors.append(
                {
                    "industry_id": str(item.get("industry_id", "")).strip(),
                    "industry_label": board_name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if frame.empty:
            continue
        frame = frame.sort_values(by=["成交额"], ascending=[False]).reset_index(drop=True)
        for _, row in frame.iterrows():
            company_name = str(row.get("名称", "")).strip()
            stock_code = str(row.get("代码", "")).strip()
            if not is_usable_company_name(company_name):
                continue
            rows.append(
                {
                    "company_name": company_name,
                    "stock_code": stock_code,
                    "industry_id": str(item.get("industry_id", "")).strip(),
                    "industry_label": str(item.get("display_name_cn", "")).strip(),
                    "sw_code": str(item.get("sw_code", "")).strip(),
                }
            )
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["company_name"], row["industry_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return {
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "source_id": "akshare:stock_board_industry_cons_em",
        "entry_count": len(deduped),
        "rows": deduped,
        "error_count": len(errors),
        "errors": errors[:20],
        "status": "partial" if errors else "pass",
    }


def ensure_company_mapping_cache(
    registry: list[dict[str, str]],
    *,
    max_age_hours: int = DEFAULT_CACHE_MAX_AGE_HOURS,
    build_if_missing: bool = True,
    refresh_stale: bool = True,
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or cache_path()
    cached = load_cached_company_mapping(target)
    if cached is not None and is_cache_fresh(cached, max_age_hours=max_age_hours):
        return cached
    if cached is not None and not refresh_stale:
        return cached
    if cached is None and not build_if_missing:
        return empty_company_mapping_payload()
    payload = build_company_mapping_payload(registry)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def company_names_by_industry(payload: dict[str, Any]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        industry_id = str(row.get("industry_id", "")).strip()
        company_name = str(row.get("company_name", "")).strip()
        if not industry_id or not company_name:
            continue
        grouped.setdefault(industry_id, []).append(company_name)
    deduped: dict[str, list[str]] = {}
    for industry_id, names in grouped.items():
        seen: set[str] = set()
        ordered: list[str] = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        deduped[industry_id] = ordered
    return deduped
