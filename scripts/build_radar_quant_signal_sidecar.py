#!/usr/bin/env python3
"""Build a Radar-side quant signal sidecar from the shared quant research query surface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from radar_company_targets import extract_company_targets
from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_signal_access import RadarSignalAccessFacade


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_OUTPUT = ROOT / "output" / "sidecars" / "quant" / "radar_quant_signal_sidecar_latest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--refresh-if-missing", action="store_true")
    parser.add_argument("--refresh-upstream", action="store_true")
    return parser.parse_args()


def load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    args = parse_args()
    cfg = load_config_section(args.config, "signal_access")
    company_limit = max(1, int(cfg.get("company_sidecar_limit") or 16))
    payload = load_payload(args.input_candidate_pool)
    targets, unresolved = extract_company_targets(payload)
    facade = RadarSignalAccessFacade.from_config(args.config)
    refresh_if_missing = bool(
        args.refresh_if_missing
        or (bool(cfg.get("refresh_if_missing_default", False)) and not args.refresh_upstream)
    )

    market_result = facade.query_market(
        refresh_if_missing=refresh_if_missing,
        refresh_upstream=bool(args.refresh_upstream),
    ) if facade.enabled() else None

    instrument_results: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for target in targets:
        symbol = str(target.get("market_symbol") or "").strip().upper()
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        result = facade.query_instrument(
            symbol,
            refresh_if_missing=refresh_if_missing,
            refresh_upstream=bool(args.refresh_upstream),
        ) if facade.enabled() else None
        instrument_results.append(
            {
                "symbol": symbol,
                "company_name": str(target.get("radar_object_name") or "").strip(),
                "stock_code": str(target.get("stock_code") or "").strip(),
                "status": str((result.status if result else "skip") or "skip"),
                "note": str((result.note if result else "signal_access_disabled") or ""),
                "payload": dict((result.payload if result else {}) or {}),
            }
        )
        if len(instrument_results) >= company_limit:
            break

    output_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": str(payload.get("run_id") or payload.get("candidate_pool_run_id") or "candidate-pool:unknown"),
        "market_sample_date": str(payload.get("market_sample_date") or payload.get("expected_sample_date") or "")[:10],
        "event_window_end_date": str(payload.get("event_window_end_date") or payload.get("report_date") or "")[:10],
        "status": "pass" if facade.enabled() else "skip",
        "query_script_path": str(facade.query_script_path),
        "quant_config_path": str(facade.quant_config_path),
        "candidate_count": len(targets),
        "queried_company_count": len(instrument_results),
        "unresolved_target_count": len(unresolved),
        "market_result": {
            "status": str((market_result.status if market_result else "skip") or "skip"),
            "note": str((market_result.note if market_result else "signal_access_disabled") or ""),
            "payload": dict((market_result.payload if market_result else {}) or {}),
        },
        "instrument_results": instrument_results,
        "unresolved_targets": unresolved,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
