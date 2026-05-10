#!/usr/bin/env python3
"""Build the local Radar workspace outputs from canonical news, sentiment, and market inputs."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

from radar_current_health import reconcile_harness_current_health


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_RUNS_DIR = ROOT / "output" / "runs"
DEFAULT_HARNESS_MANIFEST = DEFAULT_RUNS_DIR / "radar_harness_manifest_latest.json"
WORKSPACE_OUTPUT_LOCK = ROOT / "state" / "radar_workspace_outputs.lock"
RUN_BUNDLE_RELATIVE_FILES = (
    "output/snapshots/radar_candidate_pool_latest.json",
    "output/snapshots/radar_opportunity_snapshot_latest.json",
    "output/snapshots/radar_daily_battlecard_snapshot_latest.json",
    "output/snapshots/radar_bark_summary_latest.json",
    "output/sidecars/news_event_hub/consumer_exports/sync_manifest_latest.json",
    "output/inventory/radar_catalyst_inventory_latest.json",
    "output/inventory/radar_catalyst_inventory_latest.md",
    "output/inventory/radar_daily_battlecard_inventory_latest.json",
    "output/inventory/radar_daily_battlecard_inventory_latest.md",
    "output/handoffs/radar_research_handoff_latest.json",
    "output/handoffs/radar_research_handoff_latest.md",
    "output/handoffs/radar_daily_battlecard_handoff_latest.json",
    "output/handoffs/radar_daily_battlecard_handoff_latest.md",
    "output/reports/radar_source_readiness_latest.json",
    "output/reports/radar_price_freshness_latest.json",
    "output/reports/radar_fundamental_coverage_latest.json",
    "output/reports/radar_news_verification_latest.json",
    "output/reports/radar_structural_signal_latest.json",
    "output/reports/radar_ipo_watchlist_latest.json",
    "output/reports/radar_hk_ipo_watchlist_latest.json",
    "output/reports/radar_kimi_editorial_latest.json",
    "output/reports/radar_kimi_research_harness_latest.json",
    "output/reports/radar_daily_report_latest.md",
    "output/reports/radar_daily_report_latest.html",
    "output/reports/radar_daily_report_latest.pdf",
    "output/reports/radar_daily_battlecard_latest.md",
    "output/reports/radar_daily_battlecard_latest.html",
    "output/reports/radar_daily_battlecard_latest.pdf",
    "output/reports/radar_calibration_latest.md",
    "output/reports/radar_data_substrate_audit_latest.json",
    "output/reports/radar_report_quality_latest.json",
    "output/reports/radar_report_quality_latest.md",
    "output/reports/radar_daily_battlecard_quality_latest.json",
    "output/reports/radar_daily_battlecard_quality_latest.md",
    "output/runs/radar_daily_battlecard_manifest_latest.json",
    "output/runs/radar_harness_manifest_latest.json",
    "output/agent_tasks/radar_agent_task_queue_latest.json",
    "output/agent_tasks/radar_agent_task_queue_latest.md",
)
BATTLECARD_ALIASES = (
    ("output/snapshots/radar_opportunity_snapshot_latest.json", "output/snapshots/radar_daily_battlecard_snapshot_latest.json"),
    ("output/inventory/radar_catalyst_inventory_latest.json", "output/inventory/radar_daily_battlecard_inventory_latest.json"),
    ("output/inventory/radar_catalyst_inventory_latest.md", "output/inventory/radar_daily_battlecard_inventory_latest.md"),
    ("output/handoffs/radar_research_handoff_latest.json", "output/handoffs/radar_daily_battlecard_handoff_latest.json"),
    ("output/handoffs/radar_research_handoff_latest.md", "output/handoffs/radar_daily_battlecard_handoff_latest.md"),
    ("output/reports/radar_daily_report_latest.md", "output/reports/radar_daily_battlecard_latest.md"),
    ("output/reports/radar_daily_report_latest.html", "output/reports/radar_daily_battlecard_latest.html"),
    ("output/reports/radar_daily_report_latest.pdf", "output/reports/radar_daily_battlecard_latest.pdf"),
    ("output/reports/radar_report_quality_latest.json", "output/reports/radar_daily_battlecard_quality_latest.json"),
    ("output/reports/radar_report_quality_latest.md", "output/reports/radar_daily_battlecard_quality_latest.md"),
)
STEP_OUTPUTS: dict[str, tuple[str, ...]] = {
    "sync_shared_news_event_hub_exports": (
        "output/sidecars/news_event_hub/consumer_exports/sync_manifest_latest.json",
        "output/sidecars/news_event_hub/consumer_exports/industry_radar_feed_latest.json",
    ),
    "build_radar_market_substrate": ("output/sidecars/market/radar_market_manifest.json",),
    "build_radar_candidate_pool": ("output/snapshots/radar_candidate_pool_latest.json",),
    "build_radar_company_enrichment_sidecar": ("output/reports/radar_company_enrichment_latest.json",),
    "ensure_radar_price_freshness": ("output/reports/radar_price_freshness_latest.json",),
    "ensure_radar_fundamental_coverage": ("output/reports/radar_fundamental_coverage_latest.json",),
    "ensure_radar_sentiment_freshness": ("output/reports/radar_sentiment_freshness_latest.json",),
    "build_radar_source_readiness": ("output/reports/radar_source_readiness_latest.json",),
    "build_radar_company_price_sidecar": ("output/sidecars/equity_prices/company_price_snapshot_latest.csv",),
    "build_radar_quant_signal_sidecar": ("output/reports/radar_quant_signal_latest.json",),
    "build_radar_opportunity_snapshot": ("output/snapshots/radar_opportunity_snapshot_latest.json",),
    "build_radar_news_verification_sidecar": ("output/reports/radar_news_verification_latest.json",),
    "build_radar_catalyst_inventory": ("output/inventory/radar_catalyst_inventory_latest.json",),
    "build_radar_bark_summary": ("output/snapshots/radar_bark_summary_latest.json",),
    "build_radar_research_handoff": ("output/handoffs/radar_research_handoff_latest.json",),
    "build_radar_structural_signal_sidecar": ("output/reports/radar_structural_signal_latest.json",),
    "build_radar_ipo_watchlist": ("output/reports/radar_ipo_watchlist_latest.json",),
    "build_radar_hk_ipo_watchlist": ("output/reports/radar_hk_ipo_watchlist_latest.json",),
    "build_radar_kimi_editorial": ("output/reports/radar_kimi_editorial_latest.json",),
    "build_radar_kimi_research_harness": ("output/reports/radar_kimi_research_harness_latest.json",),
    "render_radar_daily_report": (
        "output/reports/radar_daily_report_latest.md",
        "output/reports/radar_daily_report_latest.pdf",
    ),
    "build_radar_calibration_summary": ("output/reports/radar_calibration_latest.md",),
    "build_radar_data_substrate_audit": ("output/reports/radar_data_substrate_audit_latest.json",),
    "check_radar_report_quality": ("output/reports/radar_report_quality_latest.json",),
    "publish_daily_battlecard_aliases": ("output/runs/radar_daily_battlecard_manifest_latest.json",),
    "build_radar_agent_task_queue": ("output/agent_tasks/radar_agent_task_queue_latest.json",),
    "write_run_bundle": ("output/runs/radar_run_bundle_latest.json",),
}
AGENT_STEPS = {
    "build_radar_kimi_editorial": "kimi_editorial",
    "build_radar_kimi_research_harness": "kimi_research_harness",
    "build_radar_agent_task_queue": "codex_harness_queue",
}
DEGRADED_STATUSES = {"warn", "error", "fail", "action_required", "deterministic_fallback", "partial_pass", "disabled", "skip_no_credentials"}
PRODUCTION_FAIL_OUTPUT_STATUSES = {"error", "fail", "deterministic_fallback", "partial_pass", "disabled", "skip_no_credentials"}
WARN_OUTPUT_STATUSES = {"warn", "action_required"}
DEFAULT_NODE_CONTRACT: dict[str, Any] = {
    "domain": "production",
    "required": True,
    "command_fail_domain": "production",
    "output_fail_domain": "production",
    "output_warn_domain": "maintenance",
    "blocker_domain": "production",
    "warning_domain": "maintenance",
    "risk_flag_domain": "research",
    "ignore_output_statuses": [],
    "fail_output_statuses": sorted(PRODUCTION_FAIL_OUTPUT_STATUSES),
    "abort_on_output_fail": True,
}
NODE_CONTRACTS: dict[str, dict[str, Any]] = {
    "build_radar_company_enrichment_sidecar": {
        "domain": "maintenance",
        "required": False,
        "output_fail_domain": "maintenance",
        "output_warn_domain": "maintenance",
        "warning_domain": "maintenance",
        "abort_on_output_fail": False,
    },
    "build_radar_quant_signal_sidecar": {
        "domain": "research",
        "required": False,
        "output_fail_domain": "research",
        "output_warn_domain": "research",
        "warning_domain": "research",
        "abort_on_output_fail": False,
    },
    "build_radar_structural_signal_sidecar": {
        "domain": "research",
        "output_warn_domain": "research",
        "warning_domain": "research",
    },
    "build_radar_ipo_watchlist": {
        "domain": "research",
        "output_warn_domain": "research",
        "warning_domain": "research",
    },
    "build_radar_hk_ipo_watchlist": {
        "domain": "research",
        "output_warn_domain": "research",
        "warning_domain": "research",
    },
    "build_radar_kimi_editorial": {
        "domain": "research_agent",
        "output_warn_domain": "research",
        "warning_domain": "research",
        "timeout_seconds": 240,
    },
    "build_radar_kimi_research_harness": {
        "domain": "research_agent",
        "output_fail_domain": "production",
        "output_warn_domain": "research",
        "warning_domain": "research",
        "risk_flag_domain": "research",
        "abort_on_output_fail": True,
        "timeout_seconds": 1200,
    },
    "check_radar_report_quality": {
        "domain": "quality_gate",
        "output_fail_domain": "production",
        "output_warn_domain": "production",
        "warning_domain": "production",
        "blocker_domain": "production",
    },
    "build_radar_agent_task_queue": {
        "domain": "control_plane",
        "required": False,
        "ignore_output_statuses": ["action_required", "warn"],
        "output_warn_domain": "maintenance",
        "warning_domain": "maintenance",
        "abort_on_output_fail": False,
    },
    "write_run_bundle": {
        "domain": "publish",
    },
    "publish_daily_battlecard_aliases": {
        "domain": "publish",
    },
}
ACTIVE_HARNESS: "HarnessManifest | None" = None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL, help="Radar candidate pool path.")
    return parser.parse_args(argv)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tail_text(value: str, *, limit: int = 4000) -> str:
    text = str(value or "").strip()
    return text[-limit:] if len(text) > limit else text


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def summarize_output(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
        "exists": path.exists(),
    }
    if path.exists():
        summary["bytes"] = path.stat().st_size
    if path.suffix.lower() == ".json" and path.exists():
        payload = load_optional_json(path)
        for key in ("status", "run_id", "radar_run_id", "as_of_date", "report_date", "generated_at", "note", "summary"):
            if key in payload:
                summary[key] = payload.get(key)
        for key in ("warning_count", "failure_count", "task_count", "ready_count", "claimed_count", "blocked_count"):
            if key in payload:
                summary[key] = payload.get(key)
        if "warnings" in payload:
            explicit_warning_count = int(summary.get("warning_count") or 0)
            summary["warning_count"] = max(explicit_warning_count, len(payload.get("warnings") or []))
        if "blockers" in payload:
            summary["blocker_count"] = len(payload.get("blockers") or [])
        if "risk_flags" in payload:
            summary["risk_flag_count"] = len(payload.get("risk_flags") or [])
        if "operational_flags" in payload:
            summary["operational_flag_count"] = len(payload.get("operational_flags") or [])
    return summary


def output_summaries(label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative in STEP_OUTPUTS.get(label, ()):
        rows.append(summarize_output(ROOT / relative))
    return rows


def output_statuses(outputs: list[dict[str, Any]]) -> list[str]:
    statuses: list[str] = []
    for item in outputs:
        status = str(item.get("status") or "").strip()
        if status:
            statuses.append(status)
    return statuses


def node_contract(label: str) -> dict[str, Any]:
    contract = dict(DEFAULT_NODE_CONTRACT)
    contract.update(NODE_CONTRACTS.get(label, {}))
    contract["ignore_output_statuses"] = sorted(str(x) for x in (contract.get("ignore_output_statuses") or []))
    contract["fail_output_statuses"] = sorted(str(x) for x in (contract.get("fail_output_statuses") or PRODUCTION_FAIL_OUTPUT_STATUSES))
    return contract


def public_node_contract(contract: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "domain",
        "required",
        "command_fail_domain",
        "output_fail_domain",
        "output_warn_domain",
        "blocker_domain",
        "warning_domain",
        "risk_flag_domain",
        "ignore_output_statuses",
        "fail_output_statuses",
        "abort_on_output_fail",
    )
    return {key: contract.get(key) for key in keys}


def severity_rank(value: str) -> int:
    return {"pass": 0, "warn": 1, "fail": 2}.get(value, 0)


def merge_dimension(current: str, incoming: str) -> str:
    return incoming if severity_rank(incoming) > severity_rank(current) else current


def attention_item(
    *,
    label: str,
    domain: str,
    severity: str,
    reason: str,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "label": label,
        "domain": domain,
        "severity": severity,
        "reason": reason,
    }
    if output:
        item["output_path"] = output.get("path")
        if output.get("status"):
            item["output_status"] = output.get("status")
    return item


def classify_step(label: str, step_status: str, outputs: list[dict[str, Any]], *, returncode: int | None = None) -> dict[str, Any]:
    contract = node_contract(label)
    dimensions = {
        "production": "pass",
        "research": "pass",
        "maintenance": "pass",
        "delivery": "not_applicable",
    }
    attention: list[dict[str, Any]] = []

    def add(domain: str, severity: str, reason: str, output: dict[str, Any] | None = None) -> None:
        if domain not in dimensions:
            domain = "maintenance"
        dimensions[domain] = merge_dimension(dimensions[domain], severity)
        attention.append(attention_item(label=label, domain=domain, severity=severity, reason=reason, output=output))

    if step_status != "pass":
        add(
            str(contract.get("command_fail_domain") or "production"),
            "fail",
            f"command_status={step_status} returncode={returncode}",
        )

    ignored_statuses = set(str(x) for x in (contract.get("ignore_output_statuses") or []))
    fail_statuses = set(str(x) for x in (contract.get("fail_output_statuses") or PRODUCTION_FAIL_OUTPUT_STATUSES))
    for output in outputs:
        status = str(output.get("status") or "").strip()
        if status and status not in ignored_statuses:
            if status in fail_statuses:
                add(str(contract.get("output_fail_domain") or "production"), "fail", f"output_status={status}", output)
            elif status in WARN_OUTPUT_STATUSES:
                add(str(contract.get("output_warn_domain") or "maintenance"), "warn", f"output_status={status}", output)
        blocker_count = int(output.get("blocker_count") or 0)
        if blocker_count > 0:
            add(str(contract.get("blocker_domain") or "production"), "fail", f"blocker_count={blocker_count}", output)
        failure_count = int(output.get("failure_count") or 0)
        if failure_count > 0:
            add(str(contract.get("output_fail_domain") or "production"), "fail", f"failure_count={failure_count}", output)
        warning_count = int(output.get("warning_count") or 0)
        if warning_count > 0:
            add(str(contract.get("warning_domain") or "maintenance"), "warn", f"warning_count={warning_count}", output)
        risk_flag_count = int(output.get("risk_flag_count") or 0)
        if risk_flag_count > 0:
            add(str(contract.get("risk_flag_domain") or "research"), "warn", f"risk_flag_count={risk_flag_count}", output)
        operational_flag_count = int(output.get("operational_flag_count") or 0)
        if operational_flag_count > 0:
            add(str(contract.get("risk_flag_domain") or "research"), "warn", f"operational_flag_count={operational_flag_count}", output)

    return {
        "contract": public_node_contract(contract),
        "dimensions": dimensions,
        "attention_items": attention,
        "abort_on_output_fail": bool(contract.get("abort_on_output_fail")),
    }


class HarnessManifest:
    def __init__(self, *, input_candidate_pool: Path) -> None:
        self.started_at = utc_now_iso()
        self.payload: dict[str, Any] = {
            "schema_version": "radar_harness_manifest.v2",
            "generated_at": self.started_at,
            "status": "running",
            "production_status": "running",
            "research_status": "running",
            "maintenance_status": "running",
            "delivery_status": "not_applicable",
            "run_id": "",
            "report_date": "",
            "market_sample_date": "",
            "input_candidate_pool": str(input_candidate_pool),
            "workspace_root": str(ROOT),
            "steps": [],
            "degraded_steps": [],
            "production_failures": [],
            "production_warnings": [],
            "research_warnings": [],
            "maintenance_warnings": [],
            "failed_step": "",
            "error": "",
        }
        self.write()

    def write(self) -> None:
        DEFAULT_HARNESS_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        self.payload["generated_at"] = utc_now_iso()
        DEFAULT_HARNESS_MANIFEST.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def refresh_run_context(self) -> None:
        snapshot = load_optional_json(ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json")
        quality = load_optional_json(ROOT / "output" / "reports" / "radar_report_quality_latest.json")
        self.payload["run_id"] = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or quality.get("run_id") or self.payload.get("run_id") or "")
        self.payload["report_date"] = str(snapshot.get("report_date") or snapshot.get("event_window_end_date") or snapshot.get("as_of_date") or self.payload.get("report_date") or "")
        self.payload["market_sample_date"] = str(snapshot.get("market_sample_date") or quality.get("sample_date") or self.payload.get("market_sample_date") or "")

    def rebuild_attention_indexes(self) -> None:
        production_failures: list[dict[str, Any]] = []
        production_warnings: list[dict[str, Any]] = []
        research_warnings: list[dict[str, Any]] = []
        maintenance_warnings: list[dict[str, Any]] = []
        for step in self.payload.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for item in step.get("attention_items") or []:
                if not isinstance(item, dict):
                    continue
                domain = str(item.get("domain") or "")
                severity = str(item.get("severity") or "")
                if domain == "production" and severity == "fail":
                    production_failures.append(item)
                elif domain == "production":
                    production_warnings.append(item)
                elif domain == "research":
                    research_warnings.append(item)
                elif domain == "maintenance":
                    maintenance_warnings.append(item)
        self.payload["production_failures"] = production_failures
        self.payload["production_warnings"] = production_warnings
        self.payload["research_warnings"] = research_warnings
        self.payload["maintenance_warnings"] = maintenance_warnings
        self.payload["degraded_steps"] = [*production_failures, *production_warnings]
        self.payload["production_status"] = "fail" if production_failures else "warn" if production_warnings else "pass"
        self.payload["research_status"] = "warn" if research_warnings else "pass"
        self.payload["maintenance_status"] = "warn" if maintenance_warnings else "pass"
        self.payload["delivery_status"] = "not_applicable"

    def append_step(self, step: dict[str, Any]) -> None:
        classification = classify_step(
            str(step.get("label") or ""),
            str(step.get("status") or ""),
            step.get("outputs") or [],
            returncode=step.get("returncode"),
        )
        step["harness_contract"] = classification["contract"]
        step["status_dimensions"] = classification["dimensions"]
        step["attention_items"] = classification["attention_items"]
        self.payload.setdefault("steps", []).append(step)
        self.rebuild_attention_indexes()
        self.refresh_run_context()
        self.write()

    def run_command(self, label: str, command: list[str]) -> None:
        started_at = utc_now_iso()
        started = time.monotonic()
        timeout_seconds = node_contract(label).get("timeout_seconds")
        try:
            result = subprocess.run(
                command,
                check=False,
                text=True,
                capture_output=True,
                timeout=float(timeout_seconds) if timeout_seconds else None,
            )
            timed_out = False
            stdout = result.stdout
            stderr = result.stderr
            returncode = result.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            returncode = 124
            result = None
        outputs = output_summaries(label)
        step = {
            "label": label,
            "kind": "command",
            "agent_role": AGENT_STEPS.get(label, "script_node"),
            "command": command,
            "started_at": started_at,
            "completed_at": utc_now_iso(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "returncode": returncode,
            "status": "fail" if timed_out else "pass" if returncode == 0 else "fail",
            "timeout_seconds": timeout_seconds,
            "timed_out": timed_out,
            "stdout_tail": tail_text(stdout),
            "stderr_tail": tail_text(stderr),
            "outputs": outputs,
        }
        self.append_step(step)
        if stdout.strip():
            print(stdout.strip())
        if returncode != 0:
            if timed_out:
                message = f"{label} timed out after {timeout_seconds}s"
            else:
                message = stderr.strip() or stdout.strip() or f"{label} exited with {returncode}"
            raise SystemExit(f"{label} failed: {message}")
        if step.get("status_dimensions", {}).get("production") == "fail" and step.get("harness_contract", {}).get("abort_on_output_fail"):
            reasons = ", ".join(
                str(item.get("reason") or "")
                for item in (step.get("attention_items") or [])
                if item.get("domain") == "production" and item.get("severity") == "fail"
            )
            raise SystemExit(f"{label} failed production contract: {reasons or 'production output failed'}")

    def run_callable(self, label: str, func: Callable[[], Any]) -> Any:
        started_at = utc_now_iso()
        started = time.monotonic()
        try:
            result = func()
        except BaseException as exc:
            self.append_step(
                {
                    "label": label,
                    "kind": "callable",
                    "agent_role": "harness_node",
                    "started_at": started_at,
                    "completed_at": utc_now_iso(),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "status": "fail",
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "outputs": output_summaries(label),
                }
            )
            raise
        self.append_step(
            {
                "label": label,
                "kind": "callable",
                "agent_role": "harness_node",
                "started_at": started_at,
                "completed_at": utc_now_iso(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "status": "pass",
                "outputs": output_summaries(label),
            }
        )
        return result

    def finalize(self, status: str, error: str = "") -> None:
        self.refresh_run_context()
        self.rebuild_attention_indexes()
        if status == "pass" and self.payload.get("production_status") == "fail":
            status = "fail"
        self.payload["status"] = status
        self.payload["workspace_status"] = status
        self.payload["workspace_production_status"] = self.payload.get("production_status") or status
        self.payload["completed_at"] = utc_now_iso()
        self.payload["error"] = error
        if error:
            steps = self.payload.get("steps") or []
            failed = next(
                (
                    item
                    for item in reversed(steps)
                    if item.get("status") == "fail" or item.get("status_dimensions", {}).get("production") == "fail"
                ),
                {},
            )
            self.payload["failed_step"] = str(failed.get("label") or "")
        self.write()
        if not error:
            reconcile_harness_current_health(reason="workspace_finalize")
            self.payload = load_optional_json(DEFAULT_HARNESS_MANIFEST) or self.payload
        run_id = str(self.payload.get("run_id") or "")
        if run_id:
            bundle_target = DEFAULT_RUNS_DIR / safe_run_dir_name(run_id) / "output" / "runs" / "radar_harness_manifest_latest.json"
            if bundle_target.parent.exists():
                bundle_target.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_step(label: str, command: list[str]) -> None:
    if ACTIVE_HARNESS is not None:
        ACTIVE_HARNESS.run_command(label, command)
        return
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"{label} exited with {result.returncode}"
        raise SystemExit(f"{label} failed: {message}")
    stdout = result.stdout.strip()
    if stdout:
        print(stdout)


@contextmanager
def workspace_output_lock(label: str, *, blocking: bool) -> object:
    WORKSPACE_OUTPUT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with WORKSPACE_OUTPUT_LOCK.open("a+", encoding="utf-8") as handle:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError:
            yield None
            return
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"owner": label, "pid": os.getpid(), "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
        handle.flush()
        try:
            yield handle
        finally:
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def extract_report_run_id(report_text: str) -> str:
    patterns = (
        r'codex_output_run_id:\s*"([^"]+)"',
        r"codex_output_run_id:\s*'([^']+)'",
        r"运行批次[：:]\s*`([^`]+)`",
    )
    for pattern in patterns:
        match = re.search(pattern, report_text)
        if match:
            return match.group(1).strip()
    return ""


def safe_run_dir_name(run_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id or "").strip())
    return cleaned.strip("_") or datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")


def publish_daily_battlecard_aliases() -> None:
    snapshot_path = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
    quality_path = ROOT / "output" / "reports" / "radar_report_quality_latest.json"
    report_path = ROOT / "output" / "reports" / "radar_daily_report_latest.md"
    snapshot = read_json(snapshot_path)
    quality = read_json(quality_path)
    report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    snapshot_run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "").strip()
    quality_run_id = str(quality.get("run_id") or "").strip()
    report_run_id = extract_report_run_id(report_text)
    if str(quality.get("status") or "") != "pass":
        raise SystemExit("daily battlecard publish blocked: quality status is not pass")
    if not snapshot_run_id or not quality_run_id or not report_run_id:
        raise SystemExit("daily battlecard publish blocked: missing snapshot/quality/report run_id")
    if quality_run_id != snapshot_run_id or report_run_id != snapshot_run_id:
        raise SystemExit(
            "daily battlecard publish blocked: run_id mismatch "
            f"snapshot={snapshot_run_id} quality={quality_run_id} report={report_run_id}"
        )
    files: list[dict[str, object]] = []
    for source_relative, target_relative in BATTLECARD_ALIASES:
        source = ROOT / source_relative
        target = ROOT / target_relative
        if not source.exists():
            raise SystemExit(f"daily battlecard publish blocked: missing source {source_relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({"source": source_relative, "path": target_relative, "status": "copied", "bytes": target.stat().st_size})
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "pass",
        "run_id": snapshot_run_id,
        "report_date": str(snapshot.get("report_date") or snapshot.get("event_window_end_date") or snapshot.get("as_of_date") or ""),
        "market_sample_date": str(snapshot.get("market_sample_date") or ""),
        "quality_status": str(quality.get("status") or ""),
        "files": files,
    }
    output_path = ROOT / "output" / "runs" / "radar_daily_battlecard_manifest_latest.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "battlecard_manifest": str(output_path), "run_id": snapshot_run_id}, ensure_ascii=False))


def write_run_bundle() -> None:
    snapshot = read_json(ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json")
    quality = read_json(ROOT / "output" / "reports" / "radar_report_quality_latest.json")
    run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or quality.get("run_id") or "")
    bundle_dir = DEFAULT_RUNS_DIR / safe_run_dir_name(run_id)
    files: list[dict[str, object]] = []
    for relative in RUN_BUNDLE_RELATIVE_FILES:
        source = ROOT / relative
        if not source.exists():
            files.append({"path": relative, "status": "missing"})
            continue
        target = bundle_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append(
            {
                "path": relative,
                "status": "copied",
                "bytes": target.stat().st_size,
            }
        )
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "report_date": str(snapshot.get("report_date") or snapshot.get("event_window_end_date") or snapshot.get("as_of_date") or ""),
        "market_sample_date": str(snapshot.get("market_sample_date") or ""),
        "quality_status": str(quality.get("status") or ""),
        "bundle_dir": str(bundle_dir),
        "files": files,
    }
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    DEFAULT_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (DEFAULT_RUNS_DIR / "radar_run_bundle_latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "pass", "run_bundle": str(bundle_dir / "manifest.json"), "run_id": run_id}, ensure_ascii=False))


def build_workspace_outputs(args: argparse.Namespace) -> int:
    run_step(
        "sync_shared_news_event_hub_exports",
        [sys.executable, str(ROOT / "scripts" / "sync_shared_news_event_hub_exports.py")],
    )
    run_step(
        "build_radar_market_substrate",
        [sys.executable, str(ROOT / "scripts" / "build_radar_market_substrate.py")],
    )
    run_step(
        "build_radar_candidate_pool",
        [sys.executable, str(ROOT / "scripts" / "build_radar_candidate_pool.py"), "--latest-output", str(args.input_candidate_pool)],
    )
    run_step(
        "build_radar_company_enrichment_sidecar",
        [sys.executable, str(ROOT / "scripts" / "build_radar_company_enrichment_sidecar.py"), "--input-candidate-pool", str(args.input_candidate_pool)],
    )
    run_step(
        "ensure_radar_price_freshness",
        [sys.executable, str(ROOT / "scripts" / "ensure_radar_price_freshness.py"), "--input-candidate-pool", str(args.input_candidate_pool)],
    )
    run_step(
        "ensure_radar_fundamental_coverage",
        [sys.executable, str(ROOT / "scripts" / "ensure_radar_fundamental_coverage.py"), "--input-candidate-pool", str(args.input_candidate_pool)],
    )
    run_step(
        "ensure_radar_sentiment_freshness",
        [sys.executable, str(ROOT / "scripts" / "ensure_radar_sentiment_freshness.py")],
    )
    run_step(
        "build_radar_source_readiness",
        [sys.executable, str(ROOT / "scripts" / "build_radar_source_readiness.py")],
    )
    run_step(
        "build_radar_company_price_sidecar",
        [sys.executable, str(ROOT / "scripts" / "build_radar_company_price_sidecar.py"), "--input-candidate-pool", str(args.input_candidate_pool)],
    )
    run_step(
        "build_radar_quant_signal_sidecar",
        [sys.executable, str(ROOT / "scripts" / "build_radar_quant_signal_sidecar.py"), "--input-candidate-pool", str(args.input_candidate_pool)],
    )
    run_step(
        "build_radar_opportunity_snapshot",
        [sys.executable, str(ROOT / "scripts" / "build_radar_opportunity_snapshot.py"), "--input-candidate-pool", str(args.input_candidate_pool)],
    )
    run_step(
        "validate_radar_opportunity_snapshot",
        [sys.executable, str(ROOT / "scripts" / "validate_radar_opportunity_snapshot.py")],
    )
    run_step(
        "build_radar_news_verification_sidecar",
        [sys.executable, str(ROOT / "scripts" / "build_radar_news_verification_sidecar.py")],
    )
    run_step(
        "build_radar_catalyst_inventory",
        [sys.executable, str(ROOT / "scripts" / "build_radar_catalyst_inventory.py")],
    )
    run_step(
        "validate_radar_catalyst_inventory",
        [sys.executable, str(ROOT / "scripts" / "validate_radar_catalyst_inventory.py")],
    )
    run_step(
        "build_radar_bark_summary",
        [sys.executable, str(ROOT / "scripts" / "build_radar_bark_summary.py")],
    )
    run_step(
        "build_radar_research_handoff",
        [sys.executable, str(ROOT / "scripts" / "build_radar_research_handoff.py")],
    )
    run_step(
        "validate_radar_research_handoff",
        [sys.executable, str(ROOT / "scripts" / "validate_radar_research_handoff.py")],
    )
    run_step(
        "build_radar_structural_signal_sidecar",
        [sys.executable, str(ROOT / "scripts" / "build_radar_structural_signal_sidecar.py")],
    )
    run_step(
        "build_radar_ipo_watchlist",
        [sys.executable, str(ROOT / "scripts" / "build_radar_ipo_watchlist.py")],
    )
    run_step(
        "build_radar_hk_ipo_watchlist",
        [sys.executable, str(ROOT / "scripts" / "build_radar_hk_ipo_watchlist.py")],
    )
    run_step(
        "build_radar_kimi_editorial",
        [sys.executable, str(ROOT / "scripts" / "build_radar_kimi_editorial.py")],
    )
    run_step(
        "build_radar_kimi_research_harness",
        [sys.executable, str(ROOT / "scripts" / "build_radar_kimi_research_harness.py")],
    )
    run_step(
        "validate_radar_kimi_research",
        [sys.executable, str(ROOT / "scripts" / "validate_radar_kimi_research.py")],
    )
    run_step(
        "render_radar_daily_report",
        [sys.executable, str(ROOT / "scripts" / "render_radar_daily_report.py")],
    )
    run_step(
        "build_radar_calibration_summary",
        [sys.executable, str(ROOT / "scripts" / "build_radar_calibration_summary.py")],
    )
    run_step(
        "build_radar_data_substrate_audit",
        [sys.executable, str(ROOT / "scripts" / "build_radar_data_substrate_audit.py"), "--candidate-pool", str(args.input_candidate_pool)],
    )
    run_step(
        "check_radar_report_quality",
        [sys.executable, str(ROOT / "scripts" / "check_radar_report_quality.py")],
    )
    if ACTIVE_HARNESS is not None:
        ACTIVE_HARNESS.run_callable("publish_daily_battlecard_aliases", publish_daily_battlecard_aliases)
    else:
        publish_daily_battlecard_aliases()
    run_step(
        "build_radar_agent_task_queue",
        [sys.executable, str(ROOT / "scripts" / "build_radar_agent_task_queue.py")],
    )
    if ACTIVE_HARNESS is not None:
        ACTIVE_HARNESS.run_callable("write_run_bundle", write_run_bundle)
    else:
        write_run_bundle()
    return 0


def main(argv: list[str] | None = None) -> int:
    global ACTIVE_HARNESS
    args = parse_args(argv or sys.argv[1:])
    harness = HarnessManifest(input_candidate_pool=args.input_candidate_pool)
    ACTIVE_HARNESS = harness
    try:
        with workspace_output_lock("daily_workspace_build", blocking=True):
            result = build_workspace_outputs(args)
        harness.finalize("pass")
        return result
    except BaseException as exc:
        harness.finalize("fail", f"{exc.__class__.__name__}: {exc}")
        raise
    finally:
        ACTIVE_HARNESS = None


if __name__ == "__main__":
    raise SystemExit(main())
