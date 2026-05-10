#!/usr/bin/env python3
"""Build a snapshot-driven Bark summary from the Radar opportunity snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "snapshots"
DEFAULT_LATEST_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_bark_summary_latest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT, help="Radar snapshot JSON path.")
    parser.add_argument("--output", type=Path, default=None, help="Optional dated Bark summary path.")
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT, help="Latest Bark summary path.")
    return parser.parse_args()


def load_snapshot(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def should_trigger_bark(item: dict[str, Any]) -> bool:
    level_ok = item.get("radar_bucket") == "strong_alert" or item.get("alert_level") == "strong_alert"
    state_ok = bool(item.get("is_new")) or bool(item.get("is_upgraded"))
    score_ok = (
        int(item.get("why_now_strength") or 0) >= 60
        and int(item.get("confidence") or 0) >= 50
        and int(item.get("followup_value") or 0) >= 60
    )
    trigger_state = str(item.get("trigger_state") or "")
    lane = str(item.get("event_driven_lane") or "")
    hedge_difficulty = str(item.get("hedge_difficulty") or "")
    confirmation_gap = str(item.get("confirmation_gap") or "")
    triage_action = str(item.get("triage_action") or "")
    evidence_quality = str(item.get("evidence_quality") or "")
    confidence = int(item.get("confidence") or 0)
    soft_with_large_gap = (
        str(item.get("hard_or_soft") or "") == "soft"
        and confirmation_gap.startswith("当前仍缺")
        and confidence < 65
    )
    high_hedge_unconfirmed = hedge_difficulty == "high" and confidence < 70
    risk_lane_block = lane == "risk_monitor"
    triage_block = triage_action != "immediate_research"
    weak_evidence_block = evidence_quality not in {"structured_confirmed", "proxy_confirmed"}
    return (
        level_ok
        and state_ok
        and score_ok
        and trigger_state != "suppressed"
        and not soft_with_large_gap
        and not high_hedge_unconfirmed
        and not risk_lane_block
        and not triage_block
        and not weak_evidence_block
    )


def build_title(item: dict[str, Any]) -> str:
    return f"Radar | {item.get('radar_object_name', '')} | {item.get('radar_bucket', '')}"


def build_body(item: dict[str, Any]) -> str:
    evidence = "；".join(str(x) for x in (item.get("key_evidence") or [])[:3]) or "暂无补充证据"
    next_step = "；".join(str(x) for x in (item.get("followup_path") or [])[:2]) or "继续观察下一轮变化"
    confirmation_gap = str(item.get("confirmation_gap") or "").strip()
    failure_mode = str(item.get("failure_mode") or "").strip()
    body_lines = [
        str(item.get("why_now", "")).strip(),
        f"证据：{evidence}",
        f"确认缺口：{confirmation_gap or '暂无显著确认缺口'}",
        f"失败路径：{failure_mode or '暂无显著失败路径'}",
        f"下一步：{next_step}",
    ]
    return "\n".join(body_lines)


def build_trigger(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "dedup_key": item.get("dedup_key"),
        "title": build_title(item),
        "body": build_body(item),
        "radar_object_type": item.get("radar_object_type"),
        "radar_object_id": item.get("radar_object_id"),
        "radar_object_name": item.get("radar_object_name"),
        "runtime_state": item.get("runtime_state"),
        "radar_bucket": item.get("radar_bucket"),
        "alert_level": item.get("alert_level"),
        "trigger_state": item.get("trigger_state"),
        "why_now_strength": item.get("why_now_strength"),
        "confidence": item.get("confidence"),
        "followup_value": item.get("followup_value"),
        "event_driven_lane": item.get("event_driven_lane"),
        "confirmation_gap": item.get("confirmation_gap"),
        "hedge_difficulty": item.get("hedge_difficulty"),
        "failure_mode": item.get("failure_mode"),
        "evidence_quality": item.get("evidence_quality"),
        "triage_action": item.get("triage_action"),
        "is_new": item.get("is_new"),
        "is_upgraded": item.get("is_upgraded"),
        "primary_symbols": item.get("primary_symbols") or [],
        "etf_proxies": item.get("etf_proxies") or [],
        "theme_overlays": item.get("theme_overlays") or [],
    }


def build_bark_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    market_sentiment = snapshot.get("market_sentiment_context") or {}
    market_composite = float(market_sentiment.get("market_composite_sentiment") or 0.0)
    items = [item for item in snapshot.get("objects") or [] if isinstance(item, dict)]
    triggers = [
        build_trigger(item)
        for item in items
        if should_trigger_bark(item)
        and not (
            market_composite <= -0.35
            and str(item.get("hard_or_soft") or "") == "soft"
        )
    ]
    suppressed_count = sum(
        1
        for item in items
        if (item.get("radar_bucket") == "strong_alert" or item.get("alert_level") == "strong_alert")
        and (
            not should_trigger_bark(item)
            or (
                market_composite <= -0.35
                and str(item.get("hard_or_soft") or "") == "soft"
            )
        )
    )
    run_id = snapshot.get("run_id") or snapshot.get("radar_run_id")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "radar_run_id": run_id,
        "as_of_date": snapshot.get("as_of_date"),
        "market_sentiment_context": market_sentiment,
        "trigger_count": len(triggers),
        "suppressed_count": suppressed_count,
        "triggers": triggers,
    }


def default_dated_output(snapshot: dict[str, Any]) -> Path:
    date_text = str(snapshot.get("as_of_date") or datetime.now(timezone.utc).date().isoformat()).replace("-", "")
    return DEFAULT_OUTPUT_DIR / f"radar_bark_summary_{date_text}.json"


def main() -> int:
    args = parse_args()
    snapshot = load_snapshot(args.input_snapshot)
    payload = build_bark_summary(snapshot)
    latest_output = args.latest_output
    dated_output = args.output or default_dated_output(snapshot)
    write_json(latest_output, payload)
    write_json(dated_output, payload)
    print(
        json.dumps(
            {
                "latest_output": str(latest_output),
                "dated_output": str(dated_output),
                "trigger_count": int(payload.get("trigger_count") or 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
