from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

try:
    from radar_config import load_config_section
except ModuleNotFoundError:
    from scripts.radar_config import load_config_section


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_MARKET_DB = ROOT / "output" / "sidecars" / "market" / "radar_market.db"
REMOTE_MARKET_PREFIX = "/opt/quant-runtime/data_substrate/radar_market/"


def resolve_market_path(raw_path: Any, *, mirror_dir: Path | None = None) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        return DEFAULT_LOCAL_MARKET_DB
    candidate = Path(text).expanduser()
    if candidate.exists():
        return candidate
    if candidate.is_absolute() and text.startswith(REMOTE_MARKET_PREFIX):
        active_mirror_dir = mirror_dir or ROOT / "output" / "sidecars" / "market"
        return active_mirror_dir / candidate.name
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def load_canonical_industry_market_index(
    *,
    target_date: str,
    config_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    cfg = load_config_section(config_path, "canonical_market_substrate")
    if not bool(cfg.get("enabled", True)):
        return {}
    mirror_dir = ROOT / str(cfg.get("mirror_dir") or "output/sidecars/market")
    db_path = resolve_market_path(cfg.get("db_path"), mirror_dir=mirror_dir)
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        flow_rows = conn.execute(
            """
            SELECT
                trade_date,
                industry_id,
                aux_flow_score,
                sector_flow_score,
                northbound_score,
                margin_score,
                lhb_score,
                etf_flow_score,
                etf_share_score,
                flow_signal_evidence_json
            FROM industry_flow_daily
            WHERE trade_date <= ?
            ORDER BY trade_date DESC
            """,
            (target_date,),
        ).fetchall()
        proxy_rows = conn.execute(
            """
            SELECT
                trade_date,
                industry_id,
                proxy_family,
                fundamental_proxy_score,
                fundamental_proxy_evidence_json
            FROM industry_proxy_daily
            WHERE trade_date <= ?
            ORDER BY trade_date DESC
            """,
            (target_date,),
        ).fetchall()
    finally:
        conn.close()

    index: dict[str, dict[str, Any]] = {}
    for row in flow_rows:
        trade_date, industry_id, aux_flow_score, sector_flow_score, northbound_score, margin_score, lhb_score, etf_flow_score, etf_share_score, evidence_json = row
        if industry_id in index:
            continue
        index[industry_id] = {
            "as_of_date": str(trade_date),
            "aux_flow_score": float(aux_flow_score or 0.0),
            "sector_flow_score": float(sector_flow_score or 0.0),
            "northbound_score": float(northbound_score or 0.0),
            "margin_score": float(margin_score or 0.0),
            "lhb_score": float(lhb_score or 0.0),
            "etf_flow_score": float(etf_flow_score or 0.0),
            "etf_share_score": float(etf_share_score or 0.0),
            "flow_signal_evidence": json.loads(evidence_json or "{}"),
            "fundamental_proxy_score": 0.0,
            "fundamental_proxy_evidence": {},
        }
    for row in proxy_rows:
        trade_date, industry_id, proxy_family, proxy_score, evidence_json = row
        bucket = index.setdefault(
            industry_id,
            {
                "as_of_date": str(trade_date),
                "aux_flow_score": 0.0,
                "sector_flow_score": 0.0,
                "northbound_score": 0.0,
                "margin_score": 0.0,
                "lhb_score": 0.0,
                "etf_flow_score": 0.0,
                "etf_share_score": 0.0,
                "flow_signal_evidence": {},
                "fundamental_proxy_score": 0.0,
                "fundamental_proxy_evidence": {},
            },
        )
        if bucket.get("fundamental_proxy_evidence"):
            continue
        evidence = json.loads(evidence_json or "{}")
        bucket["as_of_date"] = str(trade_date)
        bucket["fundamental_proxy_score"] = float(proxy_score or 0.0)
        bucket["fundamental_proxy_evidence"] = evidence
        bucket["proxy_family"] = str(proxy_family or evidence.get("proxy_family") or "").strip()
    return index
