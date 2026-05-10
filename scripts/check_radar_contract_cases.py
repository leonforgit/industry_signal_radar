#!/usr/bin/env python3
"""Check the scenario fixtures for the Radar contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "radar_contract_validation_cases_v1.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Scenario fixture path.")
    return parser.parse_args()


def expect(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_case(case: dict[str, Any], errors: list[str]) -> None:
    case_id = str(case.get("case_id") or "unknown")
    item = case.get("snapshot_object")
    expected = case.get("expected")
    expect(isinstance(item, dict), f"{case_id}: snapshot_object missing", errors)
    expect(isinstance(expected, dict), f"{case_id}: expected missing", errors)
    if not isinstance(item, dict) or not isinstance(expected, dict):
        return
    if case_id == "industry_report_only":
        expect(item.get("radar_bucket") == "research_candidate", f"{case_id}: bucket should be research_candidate", errors)
        expect(item.get("trigger_state") == "report_only", f"{case_id}: trigger_state should be report_only", errors)
        expect(expected.get("should_trigger_bark") is False, f"{case_id}: should not trigger Bark", errors)
    elif case_id == "upgrade_to_strong_alert":
        expect(item.get("radar_bucket") == "strong_alert", f"{case_id}: bucket should be strong_alert", errors)
        expect(item.get("is_upgraded") is True, f"{case_id}: should be upgraded", errors)
        expect(expected.get("should_trigger_bark") is True, f"{case_id}: should trigger Bark", errors)
    elif case_id == "hot_but_low_followup":
        expect(int(item.get("followup_value") or 0) < 50, f"{case_id}: followup should stay low", errors)
        expect(item.get("trigger_state") == "suppressed", f"{case_id}: should be suppressed", errors)
        expect(expected.get("should_trigger_bark") is False, f"{case_id}: should not trigger Bark", errors)


def main() -> int:
    args = parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise SystemExit("cases must be a list")
    errors: list[str] = []
    for case in cases:
        if isinstance(case, dict):
            validate_case(case, errors)
        else:
            errors.append("case entry must be object")
    if errors:
        print(json.dumps({"status": "fail", "error_count": len(errors), "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "pass", "case_count": len(cases)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
