#!/usr/bin/env python3
"""Validate the Radar catalyst inventory JSON without third-party dependencies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "output" / "inventory" / "radar_catalyst_inventory_latest.json"

OBJECT_TYPES = {
    "industry",
    "macro",
    "company",
    "special_situation",
    "watchlist_priority_change",
}
BUCKETS = {
    "observe",
    "research_candidate",
    "strong_candidate",
    "strong_alert",
}
ALERT_LEVELS = {"none", "watch", "candidate", "strong_alert"}
CATALYST_TYPES = {
    "earnings_guidance",
    "approval_registration",
    "order_project",
    "capital_markets",
    "merger_restructuring",
    "buyback_shareholder_support",
    "financing_dilution",
    "litigation_regulatory",
    "dividend_capital_return",
    "distress_delisting",
    "industry_proxy",
    "policy_regulation",
    "macro_geopolitical",
    "product_operation",
    "general_corporate",
}
HARD_SOFT = {"hard", "soft"}
CATALYST_STAGES = {
    "announced",
    "execution_window",
    "confirmation_window",
    "signal_clustered",
    "monitoring",
    "active_risk",
}
EVENT_DRIVEN_LANES = {"hard_catalyst_board", "soft_catalyst_watchlist", "risk_monitor", "background_monitor"}
EVIDENCE_QUALITY = {"structured_confirmed", "proxy_confirmed", "early_thematic", "narrative_only", "risk_signal"}
TRIAGE_ACTIONS = {"immediate_research", "thesis_watch", "risk_review", "background_only"}
DUE_WINDOWS = {"days_1_3", "days_3_10", "weeks_2_6", "open_ended"}
INVENTORY_STATES = {"open", "watching", "confirmed", "risk_review", "background", "expired"}
PRESENCE_STATUS = {"active", "missing_recent"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Radar catalyst inventory JSON path.")
    return parser.parse_args()


def expect(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_history_entry(entry: Any, prefix: str, errors: list[str]) -> None:
    expect(isinstance(entry, dict), f"{prefix} must be object", errors)
    if not isinstance(entry, dict):
        return
    for key in ("run_id", "as_of_date", "inventory_state", "presence_status"):
        expect(isinstance(entry.get(key), str) and str(entry.get(key)).strip() != "", f"{prefix}.{key} missing", errors)
    for key in ("radar_bucket", "triage_action"):
        expect(isinstance(entry.get(key), str), f"{prefix}.{key} missing", errors)
    expect(entry.get("inventory_state") in INVENTORY_STATES, f"{prefix}.inventory_state invalid", errors)
    expect(entry.get("presence_status") in PRESENCE_STATUS, f"{prefix}.presence_status invalid", errors)
    expect(entry.get("radar_bucket") in BUCKETS or entry.get("radar_bucket") == "", f"{prefix}.radar_bucket invalid", errors)
    expect(entry.get("triage_action") in TRIAGE_ACTIONS or entry.get("triage_action") == "", f"{prefix}.triage_action invalid", errors)
    expect(isinstance(entry.get("radar_score"), int) and int(entry.get("radar_score")) >= 0, f"{prefix}.radar_score invalid", errors)


def validate_entry(entry: Any, prefix: str, errors: list[str]) -> None:
    expect(isinstance(entry, dict), f"{prefix} must be object", errors)
    if not isinstance(entry, dict):
        return
    for key in (
        "inventory_id",
        "radar_object_id",
        "radar_object_name",
        "radar_object_type",
        "current_state",
        "presence_status",
        "first_seen_at",
        "first_seen_sample_date",
        "last_seen_at",
        "last_seen_sample_date",
        "last_seen_run_id",
        "transition_note",
        "radar_bucket",
        "triage_action",
        "event_driven_lane",
        "catalyst_type",
        "hard_or_soft",
        "catalyst_stage",
        "evidence_quality",
        "alert_level",
        "next_milestone",
        "milestone_due_window",
        "confirmation_gap",
        "failure_mode",
    ):
        expect(isinstance(entry.get(key), str) and str(entry.get(key)).strip() != "", f"{prefix}.{key} missing", errors)
    expect(entry.get("radar_object_type") in OBJECT_TYPES, f"{prefix}.radar_object_type invalid", errors)
    expect(entry.get("current_state") in INVENTORY_STATES, f"{prefix}.current_state invalid", errors)
    previous_state = str(entry.get("previous_state") or "")
    expect(previous_state in INVENTORY_STATES or previous_state == "", f"{prefix}.previous_state invalid", errors)
    expect(entry.get("presence_status") in PRESENCE_STATUS, f"{prefix}.presence_status invalid", errors)
    expect(entry.get("radar_bucket") in BUCKETS, f"{prefix}.radar_bucket invalid", errors)
    expect(entry.get("triage_action") in TRIAGE_ACTIONS, f"{prefix}.triage_action invalid", errors)
    expect(entry.get("event_driven_lane") in EVENT_DRIVEN_LANES, f"{prefix}.event_driven_lane invalid", errors)
    expect(entry.get("catalyst_type") in CATALYST_TYPES, f"{prefix}.catalyst_type invalid", errors)
    expect(entry.get("hard_or_soft") in HARD_SOFT, f"{prefix}.hard_or_soft invalid", errors)
    expect(entry.get("catalyst_stage") in CATALYST_STAGES, f"{prefix}.catalyst_stage invalid", errors)
    expect(entry.get("evidence_quality") in EVIDENCE_QUALITY, f"{prefix}.evidence_quality invalid", errors)
    expect(entry.get("alert_level") in ALERT_LEVELS, f"{prefix}.alert_level invalid", errors)
    expect(entry.get("milestone_due_window") in DUE_WINDOWS, f"{prefix}.milestone_due_window invalid", errors)
    for key in ("seen_count", "missed_runs", "state_transition_count", "radar_score", "rank_overall"):
        expect(isinstance(entry.get(key), int) and int(entry.get(key)) >= 0, f"{prefix}.{key} invalid", errors)
    expect(isinstance(entry.get("state_changed"), bool), f"{prefix}.state_changed invalid", errors)
    for key in ("primary_symbols", "etf_proxies", "theme_overlays", "research_links"):
        expect(is_string_list(entry.get(key)), f"{prefix}.{key} must be string list", errors)
    history = entry.get("state_history")
    expect(isinstance(history, list) and len(history) >= 1, f"{prefix}.state_history invalid", errors)
    if isinstance(history, list):
        for idx, item in enumerate(history):
            validate_history_entry(item, f"{prefix}.state_history[{idx}]", errors)


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("generated_at", "run_id", "as_of_date", "market_tz", "inventory_version"):
        expect(isinstance(payload.get(key), str) and str(payload.get(key)).strip() != "", f"{key} missing", errors)
    expect(payload.get("inventory_version") == "v1", "inventory_version must be v1", errors)
    summary = payload.get("summary")
    expect(isinstance(summary, dict), "summary must be object", errors)
    if isinstance(summary, dict):
        for key in (
            "candidate_count",
            "inventory_count",
            "active_count",
            "missing_recent_count",
            "confirmed_count",
            "watching_count",
            "open_count",
            "risk_review_count",
            "background_count",
            "expired_count",
            "new_item_count",
            "state_changed_count",
        ):
            expect(isinstance(summary.get(key), int) and int(summary.get(key)) >= 0, f"summary.{key} invalid", errors)
    inventory = payload.get("inventory")
    expect(isinstance(inventory, list), "inventory must be list", errors)
    if isinstance(inventory, list):
        for idx, entry in enumerate(inventory):
            validate_entry(entry, f"inventory[{idx}]", errors)
    return errors


def main() -> int:
    args = parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Radar catalyst inventory root must be a JSON object.")
    errors = validate_payload(payload)
    if errors:
        print(json.dumps({"status": "fail", "error_count": len(errors), "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(
        json.dumps(
            {
                "status": "pass",
                "inventory_count": int((payload.get("summary") or {}).get("inventory_count") or 0),
                "confirmed_count": int((payload.get("summary") or {}).get("confirmed_count") or 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
