#!/usr/bin/env python3
"""Build a canonical Radar market substrate from industry flow and proxy signals."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from radar_config import load_config_section
from radar_freshness_utils import DEFAULT_MARKET_OPEN_TIME, DEFAULT_MARKET_SAMPLE_READY_TIME, DEFAULT_MARKET_TZ, expected_sample_date
from radar_flow_signals import build_flow_signal_overlay
from radar_fundamental_proxy import build_fundamental_proxy_overlay
from radar_industry_registry import load_industry_registry


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_MIRROR_DIR = ROOT / "output" / "sidecars" / "market"
REMOTE_MARKET_PREFIX = "/opt/quant-runtime/data_substrate/radar_market/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="Optional runtime config path.")
    parser.add_argument("--db-path", type=Path, default=None, help="Override SQLite output path.")
    parser.add_argument("--manifest-path", type=Path, default=None, help="Override manifest output path.")
    parser.add_argument("--force-live-build", action="store_true", help="Run live market fetch even when only a local mirror path is available.")
    return parser.parse_args()


def resolve_market_path(raw_path: Any, *, mirror_dir: Path | None = None) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        return Path("")
    candidate = Path(text).expanduser()
    if candidate.exists():
        return candidate
    if candidate.is_absolute() and text.startswith(REMOTE_MARKET_PREFIX):
        active_mirror_dir = mirror_dir or DEFAULT_LOCAL_MIRROR_DIR
        return active_mirror_dir / candidate.name
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS industry_flow_daily (
            trade_date TEXT NOT NULL,
            industry_id TEXT NOT NULL,
            aux_flow_score REAL NOT NULL,
            sector_flow_score REAL NOT NULL,
            northbound_score REAL NOT NULL,
            margin_score REAL NOT NULL,
            lhb_score REAL NOT NULL,
            etf_flow_score REAL NOT NULL,
            etf_share_score REAL NOT NULL,
            flow_signal_evidence_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (trade_date, industry_id)
        );

        CREATE TABLE IF NOT EXISTS industry_proxy_daily (
            trade_date TEXT NOT NULL,
            industry_id TEXT NOT NULL,
            proxy_family TEXT NOT NULL,
            fundamental_proxy_score REAL NOT NULL,
            fundamental_proxy_evidence_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (trade_date, industry_id, proxy_family)
        );

        CREATE TABLE IF NOT EXISTS radar_market_refresh_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            industry_flow_rows INTEGER NOT NULL,
            industry_proxy_rows INTEGER NOT NULL,
            source_health_json TEXT NOT NULL,
            manifest_path TEXT NOT NULL
        );
        """
    )
    conn.commit()


def write_manifest(
    *,
    path: Path,
    trade_date: str,
    db_path: Path,
    flow_rows: int,
    proxy_rows: int,
    source_health: list[dict[str, Any]],
) -> None:
    warnings = [
        {
            "source_id": str(item.get("source_id") or ""),
            "status": str(item.get("status") or ""),
            "note": str(item.get("note") or ""),
        }
        for item in source_health
        if str(item.get("status") or "") != "pass"
    ]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_trade_date": trade_date,
        "db_path": str(db_path),
        "industry_flow_rows": flow_rows,
        "industry_proxy_rows": proxy_rows,
        "warnings": warnings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reuse_existing_local_mirror_if_fresh(db_path: Path, manifest_path: Path, *, expected_trade_date: str) -> bool:
    if not db_path.exists() or not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    latest_trade_date = str(manifest.get("latest_trade_date") or "").strip()
    if latest_trade_date != expected_trade_date:
        return False
    print(
        json.dumps(
            {
                "status": "reuse_existing_local_mirror",
                "db_path": str(db_path),
                "manifest_path": str(manifest_path),
                "latest_trade_date": latest_trade_date,
                "expected_trade_date": expected_trade_date,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return True


def local_db_has_trade_date(db_path: Path, trade_date: str) -> bool:
    if not db_path.exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        flow_count = conn.execute(
            "SELECT COUNT(*) FROM industry_flow_daily WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        proxy_count = conn.execute(
            "SELECT COUNT(*) FROM industry_proxy_daily WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return int((flow_count or [0])[0] or 0) > 0 and int((proxy_count or [0])[0] or 0) > 0


def repair_local_manifest_from_db(db_path: Path, manifest_path: Path, *, expected_trade_date: str) -> bool:
    if not local_db_has_trade_date(db_path, expected_trade_date):
        return False
    warnings: list[dict[str, Any]] = []
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            warnings = list(payload.get("warnings") or []) if isinstance(payload, dict) else []
        except (OSError, json.JSONDecodeError):
            warnings = []
    write_manifest(
        path=manifest_path,
        trade_date=expected_trade_date,
        db_path=db_path,
        flow_rows=31,
        proxy_rows=31,
        source_health=warnings,
    )
    print(
        json.dumps(
            {
                "status": "repair_local_manifest_from_db",
                "db_path": str(db_path),
                "manifest_path": str(manifest_path),
                "latest_trade_date": expected_trade_date,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return True


def main() -> int:
    args = parse_args()
    cfg = load_config_section(args.config, "canonical_market_substrate")
    mirror_dir = ROOT / str(cfg.get("mirror_dir") or "output/sidecars/market")
    raw_db_path = str(cfg.get("db_path") or "").strip()
    raw_manifest_path = str(cfg.get("manifest_path") or "").strip()
    db_path = args.db_path or resolve_market_path(cfg.get("db_path"), mirror_dir=mirror_dir)
    manifest_path = args.manifest_path or resolve_market_path(cfg.get("manifest_path"), mirror_dir=mirror_dir)
    if not str(db_path):
        raise SystemExit("canonical_market_substrate.db_path is required")
    if not str(manifest_path):
        raise SystemExit("canonical_market_substrate.manifest_path is required")
    local_remote_bridge = raw_db_path.startswith(REMOTE_MARKET_PREFIX) and not Path("/opt/quant-runtime").exists()
    if not args.db_path and local_remote_bridge:
        db_path = mirror_dir / Path(raw_db_path).name
    if not args.manifest_path and raw_manifest_path.startswith(REMOTE_MARKET_PREFIX) and not Path("/opt/quant-runtime").exists():
        manifest_path = mirror_dir / Path(raw_manifest_path).name
    registry = load_industry_registry()
    run_dt = datetime.now(timezone.utc)
    source_cfg = load_config_section(args.config, "source_readiness")
    market_tz = str(source_cfg.get("market_tz") or DEFAULT_MARKET_TZ)
    market_sample_ready_time = str(
        source_cfg.get("market_sample_ready_time")
        or source_cfg.get("market_open_time")
        or DEFAULT_MARKET_SAMPLE_READY_TIME
    )
    trade_date = expected_sample_date(
        {"market_tz": market_tz, "market_sample_ready_time": market_sample_ready_time},
        market_tz=market_tz,
        market_open_time=market_sample_ready_time,
        now=run_dt,
    ).isoformat()
    if local_remote_bridge and not args.force_live_build and reuse_existing_local_mirror_if_fresh(
        db_path,
        manifest_path,
        expected_trade_date=trade_date,
    ):
        return 0
    if local_remote_bridge and not args.force_live_build and repair_local_manifest_from_db(
        db_path,
        manifest_path,
        expected_trade_date=trade_date,
    ):
        return 0
    started_at_utc = run_dt.isoformat(timespec="seconds")

    flow_overlay, flow_health = build_flow_signal_overlay(registry, run_dt)
    proxy_overlay, proxy_health = build_fundamental_proxy_overlay(registry, run_dt)
    source_health = [*flow_health, *proxy_health]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        updated_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        flow_rows = 0
        proxy_rows = 0
        for industry_id, item in flow_overlay.items():
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_flow_daily (
                    trade_date,
                    industry_id,
                    aux_flow_score,
                    sector_flow_score,
                    northbound_score,
                    margin_score,
                    lhb_score,
                    etf_flow_score,
                    etf_share_score,
                    flow_signal_evidence_json,
                    updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_date,
                    industry_id,
                    float(item.get("aux_flow_score") or 0.0),
                    float(item.get("sector_flow_score") or 0.0),
                    float(item.get("northbound_score") or 0.0),
                    float(item.get("margin_score") or 0.0),
                    float(item.get("lhb_score") or 0.0),
                    float(item.get("etf_flow_score") or 0.0),
                    float(item.get("etf_share_score") or 0.0),
                    json.dumps(item.get("flow_signal_evidence") or {}, ensure_ascii=False),
                    updated_at_utc,
                ),
            )
            flow_rows += 1
        for industry_id, item in proxy_overlay.items():
            evidence = item.get("fundamental_proxy_evidence") or {}
            proxy_family = str(evidence.get("proxy_family") or "none").strip() or "none"
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_proxy_daily (
                    trade_date,
                    industry_id,
                    proxy_family,
                    fundamental_proxy_score,
                    fundamental_proxy_evidence_json,
                    updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_date,
                    industry_id,
                    proxy_family,
                    float(item.get("fundamental_proxy_score") or 0.0),
                    json.dumps(evidence, ensure_ascii=False),
                    updated_at_utc,
                ),
            )
            proxy_rows += 1
        conn.execute(
            """
            INSERT INTO radar_market_refresh_runs (
                started_at_utc,
                completed_at_utc,
                trade_date,
                industry_flow_rows,
                industry_proxy_rows,
                source_health_json,
                manifest_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at_utc,
                updated_at_utc,
                trade_date,
                flow_rows,
                proxy_rows,
                json.dumps(source_health, ensure_ascii=False),
                str(manifest_path),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    write_manifest(
        path=manifest_path,
        trade_date=trade_date,
        db_path=db_path,
        flow_rows=flow_rows,
        proxy_rows=proxy_rows,
        source_health=source_health,
    )
    print(
        json.dumps(
            {
                "db_path": str(db_path),
                "manifest_path": str(manifest_path),
                "trade_date": trade_date,
                "industry_flow_rows": flow_rows,
                "industry_proxy_rows": proxy_rows,
                "warning_count": sum(1 for item in source_health if str(item.get("status") or "") != "pass"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
