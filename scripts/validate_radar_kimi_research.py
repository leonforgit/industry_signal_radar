#!/usr/bin/env python3
"""Validate Radar Kimi research harness output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "output" / "reports" / "radar_kimi_research_harness_latest.json"
STATUSES = {"pass", "partial_pass", "deterministic_fallback", "disabled", "skip_no_credentials"}
DECISIONS = {"promote", "keep", "watch_only", "reject", "risk_review", "research_gap"}
BAD_ACTION_TEXT = ("核实真实性", "确认是否真实", "继续关注", "看是否确认", "定位风险来源", "确认是否影响前排对象")
PROVENANCES = {"model_generated", "missing_model_verdict", "deterministic_fallback"}
DEGRADED_TEXT = ("retry", "retried", "batch", "fallback", "jsondecodeerror", "timeout", "sanitized")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    return parser.parse_args()


def expect(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def validate_verdict(item: Any, prefix: str, errors: list[str]) -> None:
    expect(isinstance(item, dict), f"{prefix} must be object", errors)
    if not isinstance(item, dict):
        return
    for key in ("object_id", "name", "task", "reason"):
        expect(isinstance(item.get(key), str) and str(item.get(key)).strip(), f"{prefix}.{key} missing", errors)
    expect(item.get("decision") in DECISIONS, f"{prefix}.decision invalid", errors)
    expect(isinstance(item.get("weight_delta"), int), f"{prefix}.weight_delta invalid", errors)
    decision = str(item.get("decision") or "")
    weight_delta = int(item.get("weight_delta") or 0)
    if decision == "promote":
        expect(weight_delta >= 10, f"{prefix}.promote must carry positive weight", errors)
    if decision == "watch_only":
        expect(weight_delta <= -5, f"{prefix}.watch_only must be a downgrade", errors)
    if decision == "research_gap":
        expect(weight_delta <= -20, f"{prefix}.research_gap must be a downgrade", errors)
    if decision == "reject":
        expect(weight_delta <= -40, f"{prefix}.reject must be a strong downgrade", errors)
    expect(string_list(item.get("evidence_ids")), f"{prefix}.evidence_ids must be non-empty string list", errors)
    disqualifiers = item.get("disqualifiers")
    expect(isinstance(disqualifiers, list) and all(isinstance(x, str) for x in disqualifiers), f"{prefix}.disqualifiers invalid", errors)
    action = item.get("next_action")
    expect(isinstance(action, dict), f"{prefix}.next_action must be object", errors)
    if isinstance(action, dict):
        expect(isinstance(action.get("when"), str) and str(action.get("when")).strip(), f"{prefix}.next_action.when missing", errors)
        expect(string_list(action.get("check")), f"{prefix}.next_action.check must be non-empty string list", errors)
        text = " ".join(str(x) for x in (action.get("check") or []))
        expect(not any(token in text for token in BAD_ACTION_TEXT), f"{prefix}.next_action.check contains shallow action", errors)
    confidence = item.get("confidence")
    expect(isinstance(confidence, (int, float)) and 0 <= float(confidence) <= 1, f"{prefix}.confidence invalid", errors)
    expect(item.get("provenance") in PROVENANCES, f"{prefix}.provenance invalid", errors)
    expect(isinstance(item.get("model_generated"), bool), f"{prefix}.model_generated must be boolean", errors)
    if item.get("provenance") == "model_generated":
        expect(item.get("model_generated") is True, f"{prefix}.model_generated inconsistent with provenance", errors)
    if item.get("provenance") in {"missing_model_verdict", "deterministic_fallback"}:
        expect(item.get("model_generated") is False, f"{prefix}.model_generated inconsistent with fallback provenance", errors)
    if (
        "priced_in" in (disqualifiers or [])
        or "missing_price" in (disqualifiers or [])
        or "partially_priced" in (disqualifiers or [])
    ):
        expect(item.get("decision") in {"watch_only", "reject", "research_gap"}, f"{prefix}.decision violates price guardrail", errors)
        expect(int(item.get("weight_delta") or 0) <= -30, f"{prefix}.weight_delta violates price guardrail", errors)
    if "hot_price_reaction" in (disqualifiers or []):
        expect(item.get("decision") in {"watch_only", "reject", "research_gap"}, f"{prefix}.decision violates hot price guardrail", errors)
        expect(int(item.get("weight_delta") or 0) <= -20, f"{prefix}.weight_delta violates hot price guardrail", errors)
    if "risk_event" in (disqualifiers or []):
        expect(item.get("decision") in {"risk_review", "reject"}, f"{prefix}.decision violates risk guardrail", errors)
        expect(int(item.get("weight_delta") or 0) <= -40, f"{prefix}.weight_delta violates risk guardrail", errors)


def validate_sidecar_common(item: Any, prefix: str, errors: list[str]) -> None:
    expect(isinstance(item, dict), f"{prefix} must be object", errors)
    if not isinstance(item, dict):
        return
    for key in ("object_id", "name", "task", "reason"):
        expect(isinstance(item.get(key), str) and str(item.get(key)).strip(), f"{prefix}.{key} missing", errors)
    expect(item.get("decision") in DECISIONS, f"{prefix}.decision invalid", errors)
    expect(isinstance(item.get("weight_delta"), int), f"{prefix}.weight_delta invalid", errors)
    decision = str(item.get("decision") or "")
    weight_delta = int(item.get("weight_delta") or 0)
    if decision == "promote":
        expect(weight_delta >= 10, f"{prefix}.promote must carry positive weight", errors)
    if decision == "watch_only":
        expect(weight_delta <= -5, f"{prefix}.watch_only must be a downgrade", errors)
    if decision == "research_gap":
        expect(weight_delta <= -20, f"{prefix}.research_gap must be a downgrade", errors)
    if decision == "reject":
        expect(weight_delta <= -40, f"{prefix}.reject must be a strong downgrade", errors)
    action = item.get("next_action")
    expect(isinstance(action, dict), f"{prefix}.next_action must be object", errors)
    if isinstance(action, dict):
        expect(isinstance(action.get("when"), str) and str(action.get("when")).strip(), f"{prefix}.next_action.when missing", errors)
        expect(string_list(action.get("check")), f"{prefix}.next_action.check must be non-empty string list", errors)
        text = " ".join(str(x) for x in (action.get("check") or []))
        expect(not any(token in text for token in BAD_ACTION_TEXT), f"{prefix}.next_action.check contains shallow action", errors)
    disqualifiers = item.get("disqualifiers")
    expect(isinstance(disqualifiers, list) and all(isinstance(x, str) for x in disqualifiers), f"{prefix}.disqualifiers invalid", errors)
    confidence = item.get("confidence")
    expect(isinstance(confidence, (int, float)) and 0 <= float(confidence) <= 1, f"{prefix}.confidence invalid", errors)
    expect(item.get("provenance") in PROVENANCES, f"{prefix}.provenance invalid", errors)
    expect(item.get("model_generated") is True, f"{prefix}.model_generated must be true", errors)


def validate_ipo_verdict(item: Any, prefix: str, errors: list[str]) -> None:
    validate_sidecar_common(item, prefix, errors)
    if not isinstance(item, dict):
        return
    expect(item.get("task") == "ipo_subscription_screen", f"{prefix}.task must be ipo_subscription_screen", errors)
    expect(string_list(item.get("evidence_ids")), f"{prefix}.evidence_ids must be non-empty string list", errors)


def validate_structural_verdict(item: Any, prefix: str, errors: list[str]) -> None:
    validate_sidecar_common(item, prefix, errors)
    if not isinstance(item, dict):
        return
    expect(item.get("task") == "structural_signal_screen", f"{prefix}.task must be structural_signal_screen", errors)
    for key in ("structural_thesis", "watch_window"):
        expect(isinstance(item.get(key), str) and str(item.get(key)).strip(), f"{prefix}.{key} missing", errors)
    chain = item.get("transmission_chain")
    expect(
        (isinstance(chain, str) and chain.strip()) or string_list(chain),
        f"{prefix}.transmission_chain must be non-empty string or string list",
        errors,
    )
    expect(string_list(item.get("evidence")), f"{prefix}.evidence must be non-empty string list", errors)


def sidecar_actual_counts(payload: dict[str, Any], key: str) -> tuple[int, int]:
    field = f"{key}_verdicts"
    rows = payload.get(field)
    if not isinstance(rows, list):
        return 0, 0
    model_generated_count = sum(1 for item in rows if isinstance(item, dict) and item.get("model_generated") is True)
    return len(rows), model_generated_count


def validate_sidecar_coverage(payload: dict[str, Any], errors: list[str]) -> None:
    coverage = payload.get("sidecar_coverage")
    expect(isinstance(coverage, dict), "sidecar_coverage missing", errors)
    if not isinstance(coverage, dict):
        return
    for key in ("ipo", "structural"):
        row = coverage.get(key)
        expect(isinstance(row, dict), f"sidecar_coverage.{key} missing", errors)
        if not isinstance(row, dict):
            continue
        for count_key in ("available_count", "required_count", "model_generated_count"):
            expect(isinstance(row.get(count_key), int), f"sidecar_coverage.{key}.{count_key} invalid", errors)
        expect(isinstance(row.get("coverage_ratio"), (int, float)), f"sidecar_coverage.{key}.coverage_ratio invalid", errors)
        missing = row.get("missing_required_keys")
        expect(isinstance(missing, list) and all(isinstance(item, str) for item in missing), f"sidecar_coverage.{key}.missing_required_keys invalid", errors)
        required_keys = row.get("required_keys")
        covered_keys = row.get("covered_keys")
        expect(isinstance(required_keys, list) and all(isinstance(item, str) and item.strip() for item in required_keys), f"sidecar_coverage.{key}.required_keys invalid", errors)
        expect(isinstance(covered_keys, list) and all(isinstance(item, str) and item.strip() for item in covered_keys), f"sidecar_coverage.{key}.covered_keys invalid", errors)
        actual_count, actual_model_generated_count = sidecar_actual_counts(payload, key)
        available_count = int(row.get("available_count") or 0)
        required_count = int(row.get("required_count") or 0)
        model_generated_count = int(row.get("model_generated_count") or 0)
        required_key_list = required_keys if isinstance(required_keys, list) else []
        covered_key_list = covered_keys if isinstance(covered_keys, list) else []
        missing_key_list = missing if isinstance(missing, list) else []
        expected_ratio = round((model_generated_count / required_count) if required_count else 1.0, 4)
        expect(actual_count <= available_count, f"sidecar_coverage.{key}.available_count below actual {key}_verdicts length", errors)
        expect(required_count <= available_count, f"sidecar_coverage.{key}.required_count exceeds available_count", errors)
        expect(len(required_key_list) == required_count, f"sidecar_coverage.{key}.required_keys length mismatch", errors)
        expect(len(set(required_key_list)) == len(required_key_list), f"sidecar_coverage.{key}.required_keys duplicates", errors)
        expect(len(covered_key_list) == model_generated_count, f"sidecar_coverage.{key}.covered_keys length mismatch", errors)
        expect(len(set(covered_key_list)) == len(covered_key_list), f"sidecar_coverage.{key}.covered_keys duplicates", errors)
        expect(set(covered_key_list).issubset(set(required_key_list)), f"sidecar_coverage.{key}.covered_keys outside required_keys", errors)
        expected_missing = [item for item in required_key_list if item not in set(covered_key_list)]
        expect(missing_key_list == expected_missing, f"sidecar_coverage.{key}.missing_required_keys mismatch", errors)
        expect(model_generated_count <= required_count, f"sidecar_coverage.{key}.model_generated_count exceeds required_count", errors)
        if available_count > 0 and payload.get("status") == "pass":
            expect(required_count > 0, f"pass payload sidecar_coverage.{key} has available items but required_count=0", errors)
        if available_count == 0:
            expect(actual_count == 0, f"sidecar_coverage.{key} has no available items but {key}_verdicts is non-empty", errors)
        expect(
            abs(float(row.get("coverage_ratio") or 0.0) - expected_ratio) <= 0.0001,
            f"sidecar_coverage.{key}.coverage_ratio mismatch",
            errors,
        )
        expect(
            model_generated_count <= actual_model_generated_count,
            f"sidecar_coverage.{key}.model_generated_count exceeds actual {key}_verdicts model_generated count",
            errors,
        )
        if required_count > 0:
            expect(actual_count > 0, f"sidecar_coverage.{key} requires verdicts but {key}_verdicts is empty", errors)
        if payload.get("status") == "pass":
            expect(not missing, f"pass payload missing required {key} sidecar coverage: {missing}", errors)
            expect(
                model_generated_count >= required_count,
                f"pass payload {key} sidecar coverage incomplete",
                errors,
            )
            expect(
                actual_model_generated_count >= required_count,
                f"pass payload actual {key}_verdicts model coverage incomplete",
                errors,
            )


def validate_risk_flag(item: Any, prefix: str, errors: list[str]) -> None:
    expect(isinstance(item, dict), f"{prefix} must be object", errors)
    if not isinstance(item, dict):
        return
    for key in ("flag_type", "detail", "provenance"):
        expect(isinstance(item.get(key), str) and str(item.get(key)).strip(), f"{prefix}.{key} missing", errors)
    source_id = item.get("source_id")
    expect(source_id is None or isinstance(source_id, str), f"{prefix}.source_id invalid", errors)
    affected_names = item.get("affected_names")
    expect(isinstance(affected_names, list) and all(isinstance(x, str) for x in affected_names), f"{prefix}.affected_names invalid", errors)
    action = item.get("next_action")
    expect(isinstance(action, dict), f"{prefix}.next_action must be object", errors)
    if isinstance(action, dict):
        expect(isinstance(action.get("when"), str) and str(action.get("when")).strip(), f"{prefix}.next_action.when missing", errors)
        expect(string_list(action.get("check")), f"{prefix}.next_action.check must be non-empty string list", errors)
        text = " ".join(str(x) for x in (action.get("check") or []))
        expect(not any(token in text for token in BAD_ACTION_TEXT), f"{prefix}.next_action.check contains shallow action", errors)
    expect(item.get("provenance") in PROVENANCES, f"{prefix}.provenance invalid", errors)
    expect(isinstance(item.get("model_generated"), bool), f"{prefix}.model_generated must be boolean", errors)
    if item.get("provenance") == "model_generated":
        expect(item.get("model_generated") is True, f"{prefix}.model_generated inconsistent with provenance", errors)


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expect(payload.get("status") in STATUSES, "status invalid", errors)
    for key in ("generated_at", "run_id", "as_of_date"):
        expect(isinstance(payload.get(key), str) and str(payload.get(key)).strip(), f"{key} missing", errors)
    expect(isinstance(payload.get("candidate_count"), int) and int(payload.get("candidate_count")) >= 0, "candidate_count invalid", errors)
    verdicts = payload.get("verdicts")
    expect(isinstance(verdicts, list), "verdicts must be list", errors)
    if isinstance(verdicts, list):
        if payload.get("status") == "pass":
            expect(len(verdicts) >= 3, "pass payload must contain at least 3 verdicts", errors)
            expect(len(verdicts) == int(payload.get("candidate_count") or 0), "pass payload must cover every candidate", errors)
        for idx, item in enumerate(verdicts):
            validate_verdict(item, f"verdicts[{idx}]", errors)
        model_generated_count = sum(1 for item in verdicts if isinstance(item, dict) and item.get("model_generated") is True)
        fallback_count = max(len(verdicts) - model_generated_count, 0)
        coverage = payload.get("model_coverage")
        expect(isinstance(coverage, dict), "model_coverage missing", errors)
        if isinstance(coverage, dict):
            expect(int(coverage.get("expected_count") or 0) == int(payload.get("candidate_count") or 0), "model_coverage.expected_count mismatch", errors)
            expect(int(coverage.get("model_generated_count") or 0) == model_generated_count, "model_coverage.model_generated_count mismatch", errors)
            expect(int(coverage.get("fallback_count") or 0) == fallback_count, "model_coverage.fallback_count mismatch", errors)
        if payload.get("status") == "pass":
            expect(fallback_count == 0, "pass payload must not contain fallback verdicts", errors)
            degraded_blob = str(payload.get("note") or "").lower()
            expect(not any(token in degraded_blob for token in DEGRADED_TEXT), "pass payload contains degraded retry/fallback markers", errors)
    ipo_verdicts = payload.get("ipo_verdicts")
    structural_verdicts = payload.get("structural_verdicts")
    expect(isinstance(ipo_verdicts, list), "ipo_verdicts must be list", errors)
    expect(isinstance(structural_verdicts, list), "structural_verdicts must be list", errors)
    if isinstance(ipo_verdicts, list):
        for idx, item in enumerate(ipo_verdicts):
            validate_ipo_verdict(item, f"ipo_verdicts[{idx}]", errors)
    if isinstance(structural_verdicts, list):
        for idx, item in enumerate(structural_verdicts):
            validate_structural_verdict(item, f"structural_verdicts[{idx}]", errors)
    validate_sidecar_coverage(payload, errors)
    risk_flags = payload.get("risk_flags")
    expect(isinstance(risk_flags, list), "risk_flags must be list", errors)
    if isinstance(risk_flags, list):
        for idx, item in enumerate(risk_flags):
            validate_risk_flag(item, f"risk_flags[{idx}]", errors)
    return errors


def main() -> int:
    args = parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Kimi research harness root must be a JSON object.")
    errors = validate_payload(payload)
    result = {
        "status": "fail" if errors else "pass",
        "error_count": len(errors),
        "errors": errors,
        "harness_status": payload.get("status"),
        "verdict_count": len(payload.get("verdicts") or []),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
