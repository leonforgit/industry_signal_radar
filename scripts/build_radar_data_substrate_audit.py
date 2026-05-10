#!/usr/bin/env python3
"""Summarize whether Radar's canonical-first data substrate is strong enough for daily runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from radar_company_targets import extract_company_targets
from radar_canonical_writeback_queue import DEFAULT_FUNDAMENTAL_QUEUE, DEFAULT_PRICE_QUEUE


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_SOURCE_READINESS = ROOT / "output" / "reports" / "radar_source_readiness_latest.json"
DEFAULT_PRICE_FRESHNESS = ROOT / "output" / "reports" / "radar_price_freshness_latest.json"
DEFAULT_FUNDAMENTAL_COVERAGE = ROOT / "output" / "reports" / "radar_fundamental_coverage_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_data_substrate_audit_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_data_substrate_audit_latest.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-pool", type=Path, default=DEFAULT_CANDIDATE_POOL)
    parser.add_argument("--source-readiness", type=Path, default=DEFAULT_SOURCE_READINESS)
    parser.add_argument("--price-freshness", type=Path, default=DEFAULT_PRICE_FRESHNESS)
    parser.add_argument("--fundamental-coverage", type=Path, default=DEFAULT_FUNDAMENTAL_COVERAGE)
    parser.add_argument("--price-writeback-queue", type=Path, default=DEFAULT_PRICE_QUEUE)
    parser.add_argument("--fundamental-writeback-queue", type=Path, default=DEFAULT_FUNDAMENTAL_QUEUE)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_data_substrate_audit"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Data Substrate Audit {payload.get("status") or "unknown"}"',
        "---",
        "",
        "# Radar Data Substrate Audit",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 事件层：`{payload.get('source_readiness_status')}`",
        f"- 行情层：`{payload.get('price_freshness_status')}`",
        f"- 财务层：`{payload.get('fundamental_coverage_status')}`",
        f"- 已解析公司对象：`{payload.get('resolved_company_target_count')}`",
        f"- 未解析公司对象：`{payload.get('unresolved_company_target_count')}`",
        "",
        "## Findings",
        "",
    ]
    findings = payload.get("findings") or []
    if findings:
        for item in findings:
            lines.append(f"- {item}")
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    candidate_pool = load_json(args.candidate_pool)
    source_readiness = load_json(args.source_readiness)
    price_freshness = load_json(args.price_freshness)
    fundamental_coverage = load_json(args.fundamental_coverage)
    price_writeback_queue = load_json_list(args.price_writeback_queue)
    fundamental_writeback_queue = load_json_list(args.fundamental_writeback_queue)
    resolved_targets, unresolved_targets = extract_company_targets(candidate_pool)

    findings: list[str] = []
    source_status = str(source_readiness.get("status") or "")
    price_status = str(price_freshness.get("status") or "")
    fundamental_status = str(fundamental_coverage.get("status") or "")
    source_blockers = [str(item) for item in (source_readiness.get("blockers") or []) if str(item).strip()]
    if source_blockers:
        findings.extend(source_blockers)
    source_warnings = [str(item) for item in (source_readiness.get("warnings") or []) if str(item).strip()]
    if source_warnings:
        findings.extend(f"source readiness warning: {item}" for item in source_warnings[:8])
    market_warnings = ((source_readiness.get("market") or {}).get("warnings") or [])
    if market_warnings:
        findings.extend(
            f"canonical market warning: {str(item.get('source_id') or '')} | {str(item.get('note') or '')}".strip()
            for item in market_warnings[:8]
        )
    price_stale_targets = ((price_freshness.get("csv_status") or {}).get("stale_targets") or [])
    if price_status == "warn" and price_stale_targets:
        findings.extend(
            (
                "price freshness warning: "
                f"{str(item.get('radar_object_name') or '')} "
                f"as_of={str(item.get('as_of_date') or '')} "
                f"lag_days={str(item.get('lag_days') or '')}"
            ).strip()
            for item in price_stale_targets[:8]
            if isinstance(item, dict)
        )
    missing_financials = fundamental_coverage.get("missing_targets") or []
    if missing_financials:
        findings.append(
            "missing financial coverage: "
            + " / ".join(
                f"{str(item.get('radar_object_name') or '')}[{','.join(str(x) for x in (item.get('missing_required') or []))}]"
                for item in missing_financials[:6]
            )
        )
    if unresolved_targets:
        findings.append(
            "unresolved company mappings: "
            + " / ".join(str(item.get("radar_object_name") or "") for item in unresolved_targets[:6])
        )
    if price_writeback_queue:
        findings.append(f"canonical price writeback deferred: {len(price_writeback_queue)} queued record(s)")
    if fundamental_writeback_queue:
        findings.append(f"canonical fundamental writeback deferred: {len(fundamental_writeback_queue)} queued record(s)")

    status = "pass"
    if source_status == "fail" or source_blockers:
        status = "fail"
    elif price_status == "fail":
        status = "fail"
    elif fundamental_status == "fail":
        status = "fail"
    elif source_status == "warn" or price_status == "warn" or fundamental_status == "warn" or findings:
        status = "warn"

    payload = {
        "status": status,
        "source_readiness_status": source_status,
        "price_freshness_status": price_status,
        "fundamental_coverage_status": fundamental_status,
        "resolved_company_target_count": len(resolved_targets),
        "unresolved_company_target_count": len(unresolved_targets),
        "price_writeback_queue_count": len(price_writeback_queue),
        "fundamental_writeback_queue_count": len(fundamental_writeback_queue),
        "findings": findings,
    }
    write_json(args.json_output, payload)
    write_text(args.md_output, render_markdown(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status in {"pass", "warn"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
