#!/usr/bin/env python3
"""Validate the Radar opportunity snapshot without third-party dependencies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"

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
TRIGGER_STATES = {"report_only", "bark_candidate", "bark_sent", "suppressed"}
RUNTIME_STATES = {"cold", "warming", "candidate", "strong_alert"}
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
CATALYST_STAGES = {"announced", "execution_window", "confirmation_window", "signal_clustered", "monitoring", "active_risk"}
DUE_WINDOWS = {"days_1_3", "days_3_10", "weeks_2_6", "open_ended"}
EVENT_DRIVEN_LANES = {"hard_catalyst_board", "soft_catalyst_watchlist", "risk_monitor", "background_monitor"}
HEDGE_DIFFICULTY = {"low", "medium", "high"}
EVIDENCE_QUALITY = {"structured_confirmed", "proxy_confirmed", "early_thematic", "narrative_only", "risk_signal"}
TRIAGE_ACTIONS = {"immediate_research", "thesis_watch", "risk_review", "background_only"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Snapshot JSON path.")
    return parser.parse_args()


def expect(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def is_score(value: Any) -> bool:
    return isinstance(value, int) and 0 <= value <= 100


def is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_market_sentiment_context(context: Any, prefix: str, errors: list[str]) -> None:
    expect(isinstance(context, dict), f"{prefix} must be object", errors)
    if not isinstance(context, dict) or not context:
        return
    for key in ("market_flow_score", "market_event_score", "market_composite_score"):
        expect(is_score(context.get(key)), f"{prefix}.{key} must be int 0-100", errors)
    expect(isinstance(context.get("market_flow_label"), str), f"{prefix}.market_flow_label missing", errors)
    expect(isinstance(context.get("market_event_label"), str), f"{prefix}.market_event_label missing", errors)
    expect(isinstance(context.get("market_composite_label"), str), f"{prefix}.market_composite_label missing", errors)


def validate_company_sentiment_context(context: Any, prefix: str, errors: list[str]) -> None:
    if context in (None, {}):
        return
    expect(isinstance(context, dict), f"{prefix} must be object", errors)
    if not isinstance(context, dict):
        return
    has_sentiment_scores = any(
        context.get(key) not in (None, "")
        for key in (
            "company_market_sentiment",
            "company_event_sentiment",
            "company_composite_sentiment",
            "company_market_score",
            "company_event_score",
            "company_composite_score",
        )
    )
    if not has_sentiment_scores:
        return
    for key in ("company_market_score", "company_event_score", "company_composite_score"):
        expect(is_score(context.get(key)), f"{prefix}.{key} must be int 0-100", errors)
    for key in ("company_market_label", "company_event_label", "company_composite_label"):
        expect(isinstance(context.get(key), str), f"{prefix}.{key} missing", errors)


def validate_object(item: dict[str, Any], idx: int, errors: list[str]) -> None:
    prefix = f"objects[{idx}]"
    expect(item.get("radar_object_type") in OBJECT_TYPES, f"{prefix}.radar_object_type invalid", errors)
    expect(item.get("runtime_state") in RUNTIME_STATES, f"{prefix}.runtime_state invalid", errors)
    expect(item.get("radar_bucket") in BUCKETS, f"{prefix}.radar_bucket invalid", errors)
    expect(item.get("alert_level") in ALERT_LEVELS, f"{prefix}.alert_level invalid", errors)
    expect(item.get("catalyst_type") in CATALYST_TYPES, f"{prefix}.catalyst_type invalid", errors)
    expect(item.get("hard_or_soft") in HARD_SOFT, f"{prefix}.hard_or_soft invalid", errors)
    expect(item.get("catalyst_stage") in CATALYST_STAGES, f"{prefix}.catalyst_stage invalid", errors)
    expect(item.get("milestone_due_window") in DUE_WINDOWS, f"{prefix}.milestone_due_window invalid", errors)
    expect(item.get("event_driven_lane") in EVENT_DRIVEN_LANES, f"{prefix}.event_driven_lane invalid", errors)
    expect(item.get("evidence_quality") in EVIDENCE_QUALITY, f"{prefix}.evidence_quality invalid", errors)
    expect(item.get("triage_action") in TRIAGE_ACTIONS, f"{prefix}.triage_action invalid", errors)
    expect(item.get("hedge_difficulty") in HEDGE_DIFFICULTY, f"{prefix}.hedge_difficulty invalid", errors)
    expect(item.get("trigger_state") in TRIGGER_STATES, f"{prefix}.trigger_state invalid", errors)
    for key in (
        "radar_object_id",
        "radar_object_name",
        "radar_object_scope",
        "why_now",
        "next_milestone",
        "dedup_key",
        "confirmation_gap",
        "failure_mode",
    ):
        expect(isinstance(item.get(key), str) and str(item.get(key)).strip() != "", f"{prefix}.{key} missing", errors)
    for key in ("radar_score", "worth_watching", "why_now_strength", "confidence", "followup_value"):
        expect(is_score(item.get(key)), f"{prefix}.{key} must be int 0-100", errors)
    for key in ("rank_in_bucket", "rank_overall"):
        expect(isinstance(item.get(key), int) and int(item.get(key)) >= 1, f"{prefix}.{key} must be positive int", errors)
    for key in ("key_evidence", "followup_path", "risk_flags", "primary_symbols", "etf_proxies", "theme_overlays", "research_links"):
        expect(is_string_list(item.get(key)), f"{prefix}.{key} must be string list", errors)
    validate_company_sentiment_context(item.get("sentiment_context"), f"{prefix}.sentiment_context", errors)
    expect(isinstance(item.get("is_new"), bool), f"{prefix}.is_new must be bool", errors)
    expect(isinstance(item.get("is_upgraded"), bool), f"{prefix}.is_upgraded must be bool", errors)
    expect(item.get("previous_bucket") in BUCKETS, f"{prefix}.previous_bucket invalid", errors)
    supporting_events = item.get("supporting_events")
    expect(isinstance(supporting_events, list), f"{prefix}.supporting_events must be list", errors)
    if isinstance(supporting_events, list):
        for event_idx, event in enumerate(supporting_events):
            eprefix = f"{prefix}.supporting_events[{event_idx}]"
            expect(isinstance(event, dict), f"{eprefix} must be object", errors)
            if isinstance(event, dict):
                for key in ("source", "event_id", "event_type"):
                    expect(isinstance(event.get(key), str) and str(event.get(key)).strip() != "", f"{eprefix}.{key} missing", errors)


def validate_snapshot(snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("generated_at", "radar_run_id", "as_of_date", "market_tz", "ranking_version"):
        expect(isinstance(snapshot.get(key), str) and str(snapshot.get(key)).strip() != "", f"{key} missing", errors)
    expect(snapshot.get("ranking_version") == "v1", "ranking_version must be v1", errors)
    pref = snapshot.get("preference_profile")
    expect(isinstance(pref, dict), "preference_profile must be object", errors)
    if isinstance(pref, dict):
        for key in ("early_discovery_first", "mixed_object_board", "bucketed_report"):
            expect(isinstance(pref.get(key), bool), f"preference_profile.{key} must be bool", errors)
    summary = snapshot.get("summary")
    expect(isinstance(summary, dict), "summary must be object", errors)
    if isinstance(summary, dict):
        for key in (
            "candidate_count",
            "strong_alert_count",
            "strong_candidate_count",
            "research_candidate_count",
            "observe_count",
            "bark_trigger_count",
        ):
            expect(isinstance(summary.get(key), int) and int(summary.get(key)) >= 0, f"summary.{key} must be non-negative int", errors)
    validate_market_sentiment_context(snapshot.get("market_sentiment_context"), "market_sentiment_context", errors)
    objects = snapshot.get("objects")
    expect(isinstance(objects, list), "objects must be list", errors)
    if isinstance(objects, list):
        for idx, item in enumerate(objects):
            expect(isinstance(item, dict), f"objects[{idx}] must be object", errors)
            if isinstance(item, dict):
                validate_object(item, idx, errors)
    return errors


def main() -> int:
    args = parse_args()
    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict):
        raise SystemExit("Snapshot root must be a JSON object.")
    errors = validate_snapshot(snapshot)
    if errors:
        print(json.dumps({"status": "fail", "error_count": len(errors), "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "pass", "object_count": len(snapshot.get("objects", []))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
