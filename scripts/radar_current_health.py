#!/usr/bin/env python3
"""Reconcile Radar harness latest with the current critical artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HARNESS_MANIFEST = ROOT / "output" / "runs" / "radar_harness_manifest_latest.json"
CRITICAL_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("source_readiness", "output/reports/radar_source_readiness_latest.json"),
    ("report_quality", "output/reports/radar_report_quality_latest.json"),
    ("kimi_research", "output/reports/radar_kimi_research_harness_latest.json"),
)
FAIL_STATUSES = {"fail", "error", "deterministic_fallback", "partial_pass", "disabled", "skip_no_credentials"}
WARN_STATUSES = {"warn", "action_required"}
PASS_STATUSES = {"pass"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def artifact_summary(label: str, relative: str, *, harness_completed_at: datetime | None) -> dict[str, Any]:
    path = ROOT / relative
    row: dict[str, Any] = {
        "label": label,
        "path": relative,
        "exists": path.exists(),
    }
    if not path.exists():
        row["status"] = "missing"
        row["current_health_status"] = "fail"
        row["reason"] = "critical artifact missing"
        return row
    stat = path.stat()
    payload = load_json(path)
    mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    status = str(payload.get("status") or "").strip()
    explicit_warning_count = int_value(payload.get("warning_count"))
    warnings_count = len(payload.get("warnings") or []) if isinstance(payload.get("warnings"), list) else 0
    explicit_blocker_count = int_value(payload.get("blocker_count"))
    blockers_count = len(payload.get("blockers") or []) if isinstance(payload.get("blockers"), list) else 0
    failure_count = int_value(payload.get("failure_count"))
    row.update(
        {
            "bytes": stat.st_size,
            "mtime": mtime.isoformat(timespec="seconds"),
            "sha256": sha256_file(path),
            "status": status or "unknown",
            "generated_at": str(payload.get("generated_at") or ""),
            "run_id": str(payload.get("run_id") or payload.get("radar_run_id") or ""),
            "warning_count": max(explicit_warning_count, warnings_count),
            "blocker_count": max(explicit_blocker_count, blockers_count),
            "failure_count": failure_count,
        }
    )
    if not status:
        row["current_health_status"] = "fail"
        row["reason"] = "artifact status missing"
    elif status in FAIL_STATUSES or int(row.get("blocker_count") or 0) > 0 or int(row.get("failure_count") or 0) > 0:
        row["current_health_status"] = "fail"
        row["reason"] = f"artifact status={status or 'unknown'} blockers={row.get('blocker_count')} failures={row.get('failure_count')}"
    elif status in WARN_STATUSES or int(row.get("warning_count") or 0) > 0:
        row["current_health_status"] = "warn"
        row["reason"] = f"artifact status={status or 'unknown'} warnings={row.get('warning_count')}"
    elif status not in PASS_STATUSES:
        row["current_health_status"] = "fail"
        row["reason"] = f"artifact status unknown: {status}"
    else:
        row["current_health_status"] = "pass"
    if harness_completed_at and mtime > harness_completed_at:
        row["newer_than_harness"] = True
        if row["current_health_status"] == "pass":
            row["current_health_status"] = "warn"
            row["reason"] = "artifact changed after harness finalized"
    return row


def worse(left: str, right: str) -> str:
    rank = {"pass": 0, "warn": 1, "fail": 2}
    return right if rank.get(right, 0) > rank.get(left, 0) else left


def reconcile_harness_current_health(*, reason: str = "") -> dict[str, Any]:
    manifest_path = DEFAULT_HARNESS_MANIFEST
    manifest = load_json(manifest_path)
    if not manifest or manifest.get("schema_version") != "radar_harness_manifest.v2":
        return {"status": "skip", "reason": "missing_harness_manifest"}
    if "workspace_status" not in manifest:
        manifest["workspace_status"] = str(manifest.get("status") or "pass")
    if "workspace_production_status" not in manifest:
        manifest["workspace_production_status"] = str(manifest.get("production_status") or manifest.get("status") or "pass")
    completed_at = parse_utc(manifest.get("completed_at"))
    artifacts = [
        artifact_summary(label, relative, harness_completed_at=completed_at)
        for label, relative in CRITICAL_ARTIFACTS
    ]
    health_status = "pass"
    for item in artifacts:
        health_status = worse(health_status, str(item.get("current_health_status") or "pass"))
    current_health = {
        "generated_at": utc_now_iso(),
        "status": health_status,
        "reason": reason,
        "artifacts": artifacts,
    }
    manifest["current_artifact_health"] = current_health
    manifest["current_health_status"] = health_status
    manifest["status"] = health_status
    manifest["production_status"] = health_status
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return current_health
