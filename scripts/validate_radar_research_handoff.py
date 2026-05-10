#!/usr/bin/env python3
"""Validate the Radar research handoff JSON without third-party dependencies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "output" / "handoffs" / "radar_research_handoff_latest.json"

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
EVIDENCE_QUALITY = {"structured_confirmed", "proxy_confirmed", "early_thematic", "narrative_only", "risk_signal"}
TRIAGE_ACTIONS = {"immediate_research", "thesis_watch", "risk_review", "background_only"}
HEDGE_DIFFICULTY = {"low", "medium", "high"}
DUE_WINDOWS = {"days_1_3", "days_3_10", "weeks_2_6", "open_ended"}
INVENTORY_STATES = {"open", "watching", "confirmed", "risk_review", "background", "expired"}
INVENTORY_PRESENCE = {"active", "missing_recent"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Research handoff JSON path.")
    return parser.parse_args()


def expect(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_event(event: Any, prefix: str, errors: list[str]) -> None:
    expect(isinstance(event, dict), f"{prefix} must be object", errors)
    if not isinstance(event, dict):
        return
    for key in ("source", "event_id", "event_type"):
        expect(isinstance(event.get(key), str) and str(event.get(key)).strip() != "", f"{prefix}.{key} missing", errors)


def validate_item(item: Any, prefix: str, errors: list[str]) -> None:
    expect(isinstance(item, dict), f"{prefix} must be object", errors)
    if not isinstance(item, dict):
        return
    expect(item.get("radar_object_type") in OBJECT_TYPES, f"{prefix}.radar_object_type invalid", errors)
    expect(item.get("radar_bucket") in BUCKETS, f"{prefix}.radar_bucket invalid", errors)
    expect(item.get("alert_level") in ALERT_LEVELS, f"{prefix}.alert_level invalid", errors)
    expect(item.get("evidence_quality") in EVIDENCE_QUALITY, f"{prefix}.evidence_quality invalid", errors)
    expect(item.get("triage_action") in TRIAGE_ACTIONS, f"{prefix}.triage_action invalid", errors)
    expect(item.get("hedge_difficulty") in HEDGE_DIFFICULTY, f"{prefix}.hedge_difficulty invalid", errors)
    expect(item.get("milestone_due_window") in DUE_WINDOWS, f"{prefix}.milestone_due_window invalid", errors)
    expect(item.get("inventory_state") in INVENTORY_STATES, f"{prefix}.inventory_state invalid", errors)
    previous_state = str(item.get("inventory_previous_state") or "")
    expect(previous_state in INVENTORY_STATES or previous_state == "", f"{prefix}.inventory_previous_state invalid", errors)
    expect(item.get("inventory_presence") in INVENTORY_PRESENCE, f"{prefix}.inventory_presence invalid", errors)
    for key in (
        "radar_object_id",
        "radar_object_name",
        "triage_reason",
        "why_now",
        "confirmation_gap",
        "next_milestone",
        "failure_mode",
        "research_question",
        "inventory_transition_note",
        "inventory_first_seen_at",
        "inventory_last_seen_at",
    ):
        expect(isinstance(item.get(key), str) and str(item.get(key)).strip() != "", f"{prefix}.{key} missing", errors)
    expect(isinstance(item.get("radar_score"), int) and 0 <= int(item.get("radar_score")) <= 100, f"{prefix}.radar_score invalid", errors)
    expect(isinstance(item.get("rank_overall"), int) and int(item.get("rank_overall")) >= 1, f"{prefix}.rank_overall invalid", errors)
    expect(isinstance(item.get("inventory_seen_count"), int) and int(item.get("inventory_seen_count")) >= 0, f"{prefix}.inventory_seen_count invalid", errors)
    expect(isinstance(item.get("inventory_state_changed"), bool), f"{prefix}.inventory_state_changed invalid", errors)
    for key in ("key_evidence", "research_checkpoints", "followup_path", "primary_symbols", "etf_proxies", "theme_overlays", "research_links"):
        expect(is_string_list(item.get(key)), f"{prefix}.{key} must be string list", errors)
    supporting_events = item.get("supporting_events")
    expect(isinstance(supporting_events, list), f"{prefix}.supporting_events must be list", errors)
    if isinstance(supporting_events, list):
        for idx, event in enumerate(supporting_events):
            validate_event(event, f"{prefix}.supporting_events[{idx}]", errors)


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("generated_at", "run_id", "as_of_date"):
        expect(isinstance(payload.get(key), str) and str(payload.get(key)).strip() != "", f"{key} missing", errors)
    expect(isinstance(payload.get("candidate_count"), int) and int(payload.get("candidate_count")) >= 0, "candidate_count invalid", errors)
    inventory_summary = payload.get("inventory_summary")
    expect(isinstance(inventory_summary, dict), "inventory_summary must be object", errors)
    queues = payload.get("queues")
    expect(isinstance(queues, dict), "queues must be object", errors)
    if isinstance(queues, dict):
        for queue_name in ("immediate_research", "thesis_watch", "risk_review"):
            queue_items = queues.get(queue_name)
            expect(isinstance(queue_items, list), f"queues.{queue_name} must be list", errors)
            if isinstance(queue_items, list):
                for idx, item in enumerate(queue_items):
                    validate_item(item, f"queues.{queue_name}[{idx}]", errors)
    priority_items = payload.get("priority_items")
    expect(isinstance(priority_items, list), "priority_items must be list", errors)
    if isinstance(priority_items, list):
        for idx, item in enumerate(priority_items):
            validate_item(item, f"priority_items[{idx}]", errors)
    return errors


def main() -> int:
    args = parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Research handoff root must be a JSON object.")
    errors = validate_payload(payload)
    if errors:
        print(json.dumps({"status": "fail", "error_count": len(errors), "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    queue_counts = {name: len(items) for name, items in (payload.get("queues") or {}).items() if isinstance(items, list)}
    print(json.dumps({"status": "pass", "queue_counts": queue_counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
