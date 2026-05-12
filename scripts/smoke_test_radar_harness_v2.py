#!/usr/bin/env python3
"""Smoke-test Radar Harness v2 status classification semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shlex
import threading
import time
import subprocess
import sys
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import radar_current_health
from build_radar_agent_task_queue import (
    claim_task,
    complete_task,
    completion_timeout_seconds,
    make_task,
    queue_payload,
    reclaim_expired_leases,
    release_task,
    run_validation_commands,
    start_task,
)
from build_radar_kimi_research_harness import coverage_for_sidecar, dedupe_sidecar_rows
from build_radar_source_readiness import assess_source_health
from build_radar_workspace_outputs import classify_step
from build_radar_workspace_outputs import node_contract
from build_radar_kimi_editorial import call_kimi
from radar_freshness_utils import expected_sample_date
from validate_radar_kimi_research import validate_payload


ROOT = Path(__file__).resolve().parent.parent


class SlowKimiHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        time.sleep(2)
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(b'{"choices":[{"message":{"content":"{}"}}]}')
        except BrokenPipeError:
            return

    def log_message(self, _format: str, *_args: object) -> None:
        return


def assert_dimension(label: str, classification: dict, domain: str, expected: str) -> None:
    actual = classification["dimensions"].get(domain)
    if actual != expected:
        raise AssertionError(f"{label}: expected {domain}={expected}, got {actual}; {classification}")


def main() -> int:
    shanghai = ZoneInfo("Asia/Shanghai")
    cutoff_snapshot = {"market_tz": "Asia/Shanghai", "market_sample_ready_time": "18:00"}
    before_sample_ready = expected_sample_date(
        cutoff_snapshot,
        now=datetime(2026, 5, 7, 15, 37, tzinfo=shanghai),
    ).isoformat()
    after_sample_ready = expected_sample_date(
        cutoff_snapshot,
        now=datetime(2026, 5, 7, 18, 30, tzinfo=shanghai),
    ).isoformat()
    if before_sample_ready != "2026-05-06" or after_sample_ready != "2026-05-07":
        raise AssertionError(
            f"market sample ready cutoff broken: before={before_sample_ready} after={after_sample_ready}"
        )
    ipo_key_coverage = coverage_for_sidecar(["07630", "001365"], [{"object_id": "07630.HK", "model_generated": True}, {"object_id": "001365.SZ", "model_generated": True}])
    if ipo_key_coverage.get("model_generated_count") != 2 or ipo_key_coverage.get("missing_required_keys"):
        raise AssertionError(f"IPO sidecar key normalization broken: {ipo_key_coverage}")
    deduped_ipo_rows = dedupe_sidecar_rows([{"object_id": "001365.SZ"}, {"object_id": "001365"}], limit=8)
    if len(deduped_ipo_rows) != 1:
        raise AssertionError(f"IPO sidecar exchange suffix dedupe broken: {deduped_ipo_rows}")
    kimi_timeout = int(node_contract("build_radar_kimi_research_harness").get("timeout_seconds") or 0)
    if kimi_timeout <= 0 or kimi_timeout > 1500:
        raise AssertionError(f"Kimi research command timeout should be bounded, got {kimi_timeout}")
    slow_server = HTTPServer(("127.0.0.1", 0), SlowKimiHandler)
    slow_thread = threading.Thread(target=slow_server.serve_forever, daemon=True)
    slow_thread.start()
    slow_started = time.monotonic()
    try:
        try:
            call_kimi(
                token="smoke",
                base_url=f"http://127.0.0.1:{slow_server.server_port}",
                model="kimi-k2.6",
                prompt="{}",
                max_tokens=8,
                timeout_seconds=1,
            )
        except TimeoutError:
            pass
        else:
            raise AssertionError("slow Kimi HTTP call should hard-timeout")
        elapsed = time.monotonic() - slow_started
        if elapsed > 3:
            raise AssertionError(f"slow Kimi HTTP call exceeded hard-timeout budget: elapsed={elapsed:.2f}s")
    finally:
        slow_server.shutdown()
        slow_server.server_close()
        slow_thread.join(timeout=1)

    kimi_risk_only = classify_step(
        "build_radar_kimi_research_harness",
        "pass",
        [{"path": "output/reports/radar_kimi_research_harness_latest.json", "status": "pass", "risk_flag_count": 3}],
        returncode=0,
    )
    assert_dimension("kimi_risk_only", kimi_risk_only, "production", "pass")
    assert_dimension("kimi_risk_only", kimi_risk_only, "research", "warn")

    kimi_partial = classify_step(
        "build_radar_kimi_research_harness",
        "pass",
        [{"path": "output/reports/radar_kimi_research_harness_latest.json", "status": "partial_pass"}],
        returncode=0,
    )
    assert_dimension("kimi_partial", kimi_partial, "production", "fail")

    company_long_tail = classify_step(
        "build_radar_company_enrichment_sidecar",
        "pass",
        [{"path": "output/reports/radar_company_enrichment_latest.json", "status": "pass", "warning_count": 2}],
        returncode=0,
    )
    assert_dimension("company_long_tail", company_long_tail, "production", "pass")
    assert_dimension("company_long_tail", company_long_tail, "maintenance", "warn")

    queue_action_required = classify_step(
        "build_radar_agent_task_queue",
        "pass",
        [{"path": "output/agent_tasks/radar_agent_task_queue_latest.json", "status": "action_required", "task_count": 1}],
        returncode=0,
    )
    assert_dimension("queue_action_required", queue_action_required, "production", "pass")
    assert_dimension("queue_action_required", queue_action_required, "maintenance", "pass")

    quality_warning = classify_step(
        "check_radar_report_quality",
        "pass",
        [{"path": "output/reports/radar_report_quality_latest.json", "status": "pass", "warning_count": 1}],
        returncode=0,
    )
    assert_dimension("quality_warning", quality_warning, "production", "warn")

    explicit_warning_count = classify_step(
        "build_radar_company_enrichment_sidecar",
        "pass",
        [{"path": "output/reports/radar_company_enrichment_latest.json", "status": "pass", "warning_count": 3}],
        returncode=0,
    )
    assert_dimension("explicit_warning_count", explicit_warning_count, "maintenance", "warn")

    operational_recovery = classify_step(
        "build_radar_kimi_research_harness",
        "pass",
        [{"path": "output/reports/radar_kimi_research_harness_latest.json", "status": "pass", "operational_flag_count": 1}],
        returncode=0,
    )
    assert_dimension("operational_recovery", operational_recovery, "production", "pass")
    assert_dimension("operational_recovery", operational_recovery, "research", "warn")

    with TemporaryDirectory() as tmpdir:
        current_health_root = Path(tmpdir) / "current-health"
        current_health_root.mkdir(parents=True)
        manifest_path = current_health_root / "radar_harness_manifest_latest.json"
        artifact_path = current_health_root / "artifact.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "radar_harness_manifest.v2",
                    "status": "fail",
                    "production_status": "fail",
                    "workspace_status": "pass",
                    "workspace_production_status": "pass",
                    "completed_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        artifact_path.write_text(json.dumps({"status": "pass", "warnings": []}, ensure_ascii=False), encoding="utf-8")
        original_root = radar_current_health.ROOT
        original_manifest = radar_current_health.DEFAULT_HARNESS_MANIFEST
        original_artifacts = radar_current_health.CRITICAL_ARTIFACTS
        try:
            radar_current_health.ROOT = current_health_root
            radar_current_health.DEFAULT_HARNESS_MANIFEST = manifest_path
            radar_current_health.CRITICAL_ARTIFACTS = (("artifact", "artifact.json"),)
            health = radar_current_health.reconcile_harness_current_health(reason="smoke_recovery")
        finally:
            radar_current_health.ROOT = original_root
            radar_current_health.DEFAULT_HARNESS_MANIFEST = original_manifest
            radar_current_health.CRITICAL_ARTIFACTS = original_artifacts
        recovered_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if health.get("status") != "warn" or recovered_manifest.get("status") != "warn":
            raise AssertionError(f"current health should recover stale fail to current warn: {recovered_manifest}")
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "radar_harness_manifest.v2",
                    "status": "pass",
                    "production_status": "pass",
                    "workspace_status": "pass",
                    "workspace_production_status": "pass",
                    "completed_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        artifact_path.write_text(json.dumps({"status": "pass", "warning_count": 2, "warnings": []}, ensure_ascii=False), encoding="utf-8")
        try:
            radar_current_health.ROOT = current_health_root
            radar_current_health.DEFAULT_HARNESS_MANIFEST = manifest_path
            radar_current_health.CRITICAL_ARTIFACTS = (("artifact", "artifact.json"),)
            warning_health = radar_current_health.reconcile_harness_current_health(reason="smoke_warning_count")
        finally:
            radar_current_health.ROOT = original_root
            radar_current_health.DEFAULT_HARNESS_MANIFEST = original_manifest
            radar_current_health.CRITICAL_ARTIFACTS = original_artifacts
        if warning_health.get("status") != "warn" or int(warning_health["artifacts"][0].get("warning_count") or 0) != 2:
            raise AssertionError(f"current health should honor explicit warning_count: {warning_health}")
        for bad_artifact in (
            {"generated_at": "missing-status"},
            {"status": "pass", "blocker_count": 1, "blockers": []},
            {"status": "pass", "failure_count": 1},
        ):
            artifact_path.write_text(json.dumps(bad_artifact, ensure_ascii=False), encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "radar_harness_manifest.v2",
                        "status": "pass",
                        "production_status": "pass",
                        "workspace_status": "pass",
                        "workspace_production_status": "pass",
                        "completed_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            try:
                radar_current_health.ROOT = current_health_root
                radar_current_health.DEFAULT_HARNESS_MANIFEST = manifest_path
                radar_current_health.CRITICAL_ARTIFACTS = (("artifact", "artifact.json"),)
                bad_health = radar_current_health.reconcile_harness_current_health(reason="smoke_bad_artifact")
            finally:
                radar_current_health.ROOT = original_root
                radar_current_health.DEFAULT_HARNESS_MANIFEST = original_manifest
                radar_current_health.CRITICAL_ARTIFACTS = original_artifacts
            if bad_health.get("status") != "fail":
                raise AssertionError(f"current health should fail malformed critical artifact: {bad_artifact} -> {bad_health}")

        task = make_task(
            title="smoke task",
            trigger="trigger",
            owner_agent="codex",
            priority="P3",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            validation_commands=[f"{sys.executable} -c \"print('validation ok')\""],
        )
        task["result_artifact_path"] = f"{tmpdir}/result.json"
        payload = queue_payload([task])
        payload = claim_task(payload, task["task_id"], agent="codex-smoke", lease_minutes=5)
        payload = start_task(payload, task["task_id"], agent="codex-smoke")
        payload = complete_task(payload, task["task_id"], agent="codex-smoke", review_verdict="pass", review_notes="smoke")
        final_task = payload["tasks"][0]
        if final_task.get("status") != "done" or final_task.get("review_verdict") != "pass":
            raise AssertionError(f"task state machine failed: {final_task}")

        failing_task = make_task(
            title="failing validation",
            trigger="trigger",
            owner_agent="codex",
            priority="P3",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            validation_commands=[f"{sys.executable} -c \"import sys; sys.exit(3)\""],
        )
        failing_task["result_artifact_path"] = f"{tmpdir}/failing-result.json"
        failing_payload = queue_payload([failing_task])
        failing_payload = claim_task(failing_payload, failing_task["task_id"], agent="codex-smoke", lease_minutes=5)
        failing_payload = start_task(failing_payload, failing_task["task_id"], agent="codex-smoke")
        failing_payload = complete_task(failing_payload, failing_task["task_id"], agent="codex-smoke", review_verdict="pass", review_notes="smoke")
        failed_task = failing_payload["tasks"][0]
        if failed_task.get("status") != "blocked" or failed_task.get("lifecycle") != "validation_failed":
            raise AssertionError(f"validation failure did not block completion: {failed_task}")

        lease_task = make_task(
            title="lease expiry",
            trigger="trigger",
            owner_agent="codex",
            priority="P1",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            validation_commands=[f"{sys.executable} -c \"print('validation ok')\""],
        )
        lease_payload = queue_payload([lease_task])
        lease_payload = claim_task(lease_payload, lease_task["task_id"], agent="codex-smoke", lease_minutes=5)
        lease_payload["tasks"][0]["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
        reclaim_expired_leases(lease_payload["tasks"])
        reclaimed_task = lease_payload["tasks"][0]
        if reclaimed_task.get("status") != "ready" or reclaimed_task.get("claimed_by"):
            raise AssertionError(f"expired lease was not reclaimed: {reclaimed_task}")

        release_task_row = make_task(
            title="release retry",
            trigger="trigger",
            owner_agent="codex",
            priority="P3",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            validation_commands=[f"{sys.executable} -c \"print('validation ok')\""],
        )
        release_payload = queue_payload([release_task_row])
        release_payload = claim_task(release_payload, release_task_row["task_id"], agent="codex-smoke", lease_minutes=5)
        release_payload = release_task(release_payload, release_task_row["task_id"], reason="abandoned smoke")
        released_task = release_payload["tasks"][0]
        if released_task.get("status") != "ready" or int(released_task.get("attempt_count") or 0) != 0:
            raise AssertionError(f"released task cannot be reclaimed cleanly: {released_task}")

        dedupe_a = make_task(
            title="stable dedupe",
            trigger="trigger version a",
            owner_agent="codex",
            priority="P3",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            validation_commands=[f"{sys.executable} -c \"print('validation ok')\""],
            dedupe_key="node:stable",
        )
        dedupe_b = make_task(
            title="stable dedupe",
            trigger="trigger version b",
            owner_agent="codex",
            priority="P3",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            validation_commands=[f"{sys.executable} -c \"print('validation ok')\""],
            dedupe_key="node:stable",
        )
        if dedupe_a.get("task_id") != dedupe_b.get("task_id") or dedupe_a.get("trigger_hash") == dedupe_b.get("trigger_hash"):
            raise AssertionError(f"stable dedupe should keep one task id but refresh trigger hash: {dedupe_a} {dedupe_b}")

        repair_budget_task = make_task(
            title="repair timeout budget",
            trigger="trigger",
            owner_agent="codex",
            priority="P3",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            repair_commands=["python3 -c \"print('repair')\""],
            validation_commands=["python3 -c \"print('validation')\""],
        )
        expected_timeout = 5 * 2 + 120
        if completion_timeout_seconds(repair_budget_task, validation_timeout_seconds=5) != expected_timeout:
            raise AssertionError(f"auto-worker timeout must include repair + validation commands: {repair_budget_task}")

        validation_env_results = run_validation_commands(
            [f"RADAR_SMOKE_ENV=ok {shlex.quote(sys.executable)} -c \"import os; assert os.environ['RADAR_SMOKE_ENV'] == 'ok'\""],
            timeout_seconds=5,
        )
        if not validation_env_results or validation_env_results[0].get("status") != "pass":
            raise AssertionError(f"validation env prefix failed without shell: {validation_env_results}")

        injection_marker = Path(tmpdir) / "shell_injection_marker"
        validation_injection_results = run_validation_commands(
            [f"{shlex.quote(sys.executable)} -c \"print('validation ok')\" ; touch {shlex.quote(str(injection_marker))}"],
            timeout_seconds=5,
        )
        if injection_marker.exists() or not validation_injection_results or validation_injection_results[0].get("status") != "pass":
            raise AssertionError(f"validation command should not execute shell metacharacters: {validation_injection_results}")

        queue_json = Path(tmpdir) / "queue.json"
        queue_md = Path(tmpdir) / "queue.md"
        queue_lock = Path(tmpdir) / "queue.lock"
        result_path = Path(tmpdir) / "cli-result.json"
        env = {
            **os.environ,
            "RADAR_AGENT_TASK_QUEUE_JSON": str(queue_json),
            "RADAR_AGENT_TASK_QUEUE_MD": str(queue_md),
            "RADAR_AGENT_TASK_QUEUE_LOCK": str(queue_lock),
        }
        queue_env = (
            f"RADAR_AGENT_TASK_QUEUE_JSON={shlex.quote(str(queue_json))} "
            f"RADAR_AGENT_TASK_QUEUE_MD={shlex.quote(str(queue_md))} "
            f"RADAR_AGENT_TASK_QUEUE_LOCK={shlex.quote(str(queue_lock))}"
        )
        cli_task = make_task(
            title="cli reentrant validation",
            trigger="trigger",
            owner_agent="codex",
            priority="P1",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            validation_commands=[f"{queue_env} {shlex.quote(sys.executable)} scripts/build_radar_agent_task_queue.py"],
        )
        cli_task["result_artifact_path"] = str(result_path)
        queue_json.write_text(json.dumps(queue_payload([cli_task]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        claim = subprocess.run(
            [sys.executable, "scripts/build_radar_agent_task_queue.py", "--claim", cli_task["task_id"], "--agent", "codex-smoke"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if claim.returncode != 0:
            raise AssertionError(f"cli claim failed: {claim.stdout} {claim.stderr}")
        complete = subprocess.run(
            [
                sys.executable,
                "scripts/build_radar_agent_task_queue.py",
                "--complete",
                cli_task["task_id"],
                "--agent",
                "codex-smoke",
                "--review-verdict",
                "pass",
                "--validation-timeout-seconds",
                "10",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if complete.returncode != 0:
            raise AssertionError(f"cli complete reentrant validation failed: {complete.stdout} {complete.stderr}")
        result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        if result_payload.get("validation_status") != "pass":
            raise AssertionError(f"cli result did not record validation pass: {result_payload}")

        lease_window_queue_json = Path(tmpdir) / "lease-window-queue.json"
        lease_window_queue_md = Path(tmpdir) / "lease-window-queue.md"
        lease_window_queue_lock = Path(tmpdir) / "lease-window-queue.lock"
        lease_window_result = Path(tmpdir) / "lease-window-result.json"
        lease_window_env = {
            **os.environ,
            "RADAR_AGENT_TASK_QUEUE_JSON": str(lease_window_queue_json),
            "RADAR_AGENT_TASK_QUEUE_MD": str(lease_window_queue_md),
            "RADAR_AGENT_TASK_QUEUE_LOCK": str(lease_window_queue_lock),
        }
        lease_window_task = make_task(
            title="lease expires during validation",
            trigger="trigger",
            owner_agent="codex",
            priority="P1",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            validation_commands=[f"{sys.executable} -c \"import time; time.sleep(3); print('validation ok')\""],
        )
        lease_window_task["status"] = "claimed"
        lease_window_task["lifecycle"] = "claimed"
        lease_window_task["claimed_by"] = "codex-smoke"
        lease_window_task["claimed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lease_window_task["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(timespec="seconds")
        lease_window_task["attempt_count"] = 1
        lease_window_task["result_artifact_path"] = str(lease_window_result)
        lease_window_queue_json.write_text(json.dumps(queue_payload([lease_window_task]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lease_window_complete = subprocess.run(
            [
                sys.executable,
                "scripts/build_radar_agent_task_queue.py",
                "--complete",
                lease_window_task["task_id"],
                "--agent",
                "codex-smoke",
                "--review-verdict",
                "pass",
                "--validation-timeout-seconds",
                "6",
            ],
            cwd=ROOT,
            env=lease_window_env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if lease_window_complete.returncode != 0:
            raise AssertionError(f"lease window complete failed: {lease_window_complete.stdout} {lease_window_complete.stderr}")
        lease_window_payload = json.loads(lease_window_queue_json.read_text(encoding="utf-8"))
        lease_window_final = lease_window_payload["tasks"][0]
        if lease_window_final.get("status") != "done":
            raise AssertionError(f"lease window task was not completed: {lease_window_final}")
        lease_window_artifact = json.loads(lease_window_result.read_text(encoding="utf-8"))
        if lease_window_artifact.get("validation_status") != "pass" or lease_window_artifact.get("apply_status") != "applied":
            raise AssertionError(f"lease window artifact invalid: {lease_window_artifact}")

        auto_queue_json = Path(tmpdir) / "auto-queue.json"
        auto_queue_md = Path(tmpdir) / "auto-queue.md"
        auto_queue_lock = Path(tmpdir) / "auto-queue.lock"
        auto_result = Path(tmpdir) / "auto-result.json"
        auto_env = {
            **os.environ,
            "RADAR_AGENT_TASK_QUEUE_JSON": str(auto_queue_json),
            "RADAR_AGENT_TASK_QUEUE_MD": str(auto_queue_md),
            "RADAR_AGENT_TASK_QUEUE_LOCK": str(auto_queue_lock),
        }
        auto_task = make_task(
            title="auto runnable task",
            trigger="trigger",
            owner_agent="codex",
            priority="P3",
            allowed_write_paths=["scripts/"],
            context_paths=["output/runs/radar_harness_manifest_latest.json"],
            repair_commands=[f"{sys.executable} -c \"print('auto repair ok')\""],
            validation_commands=[f"{sys.executable} -c \"print('auto validation ok')\""],
        )
        auto_task["result_artifact_path"] = str(auto_result)
        auto_queue_json.write_text(json.dumps(queue_payload([auto_task]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        auto_run = subprocess.run(
            [
                sys.executable,
                "scripts/build_radar_agent_task_queue.py",
                "--run-ready",
                "--agent",
                "codex-smoke-auto",
                "--max-tasks",
                "1",
                "--auto-priorities",
                "P3",
                "--validation-timeout-seconds",
                "10",
            ],
            cwd=ROOT,
            env=auto_env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if auto_run.returncode != 0:
            raise AssertionError(f"auto run-ready failed: {auto_run.stdout} {auto_run.stderr}")
        auto_payload = json.loads(auto_queue_json.read_text(encoding="utf-8"))
        if auto_payload["tasks"][0].get("status") != "done":
            raise AssertionError(f"auto run-ready did not complete task: {auto_payload}")

        blocker_queue_json = Path(tmpdir) / "blocker-auto-queue.json"
        blocker_queue_md = Path(tmpdir) / "blocker-auto-queue.md"
        blocker_queue_lock = Path(tmpdir) / "blocker-auto-queue.lock"
        blocker_result = Path(tmpdir) / "blocker-auto-result.json"
        blocker_env = {
            **os.environ,
            "RADAR_AGENT_TASK_QUEUE_JSON": str(blocker_queue_json),
            "RADAR_AGENT_TASK_QUEUE_MD": str(blocker_queue_md),
            "RADAR_AGENT_TASK_QUEUE_LOCK": str(blocker_queue_lock),
        }
        p0_blocker = make_task(
            title="quality blocker",
            trigger="top-level quality fail",
            owner_agent="codex",
            priority="P0",
            allowed_write_paths=["scripts/"],
            context_paths=["output/reports/radar_report_quality_latest.json"],
            validation_commands=[f"{sys.executable} -c \"print('quality still blocked')\""],
            dedupe_key="quality_gate:battlecard",
        )
        root_cause_task = make_task(
            title="source root cause",
            trigger="source health warn",
            owner_agent="kimi",
            priority="P2",
            allowed_write_paths=["scripts/"],
            context_paths=["output/reports/radar_source_readiness_latest.json"],
            repair_commands=[f"{sys.executable} -c \"print('source repair ok')\""],
            validation_commands=[f"{sys.executable} -c \"print('source validation ok')\""],
            dedupe_key="root_cause:source",
            auto_run_with_active_blockers=True,
        )
        root_cause_task["result_artifact_path"] = str(blocker_result)
        blocker_queue_json.write_text(json.dumps(queue_payload([p0_blocker, root_cause_task]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        blocker_auto_run = subprocess.run(
            [
                sys.executable,
                "scripts/build_radar_agent_task_queue.py",
                "--run-ready",
                "--agent",
                "codex-smoke-auto",
                "--max-tasks",
                "1",
                "--auto-priorities",
                "P2",
                "--validation-timeout-seconds",
                "10",
            ],
            cwd=ROOT,
            env=blocker_env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if blocker_auto_run.returncode != 0:
            raise AssertionError(f"blocker-aware auto run failed: {blocker_auto_run.stdout} {blocker_auto_run.stderr}")
        blocker_payload = json.loads(blocker_queue_json.read_text(encoding="utf-8"))
        final_tasks = {item["task_id"]: item for item in blocker_payload["tasks"]}
        if final_tasks[root_cause_task["task_id"]].get("status") != "done" or final_tasks[p0_blocker["task_id"]].get("status") != "ready":
            raise AssertionError(f"auto-worker should run root-cause P2 while P0 remains active: {blocker_payload}")

        source_health_path = Path(tmpdir) / "source_health_latest.json"
        source_health_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source_health": [
                        {"source_id": "critical_ok", "status": "ok"},
                        {"source_id": "long_tail_down", "status": "down", "error_message": "noncritical outage"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        source_config_path = Path(tmpdir) / "runtime_defaults.json"
        source_config_path.write_text(
            json.dumps({"shared_news_event_hub": {"source_health_path": str(source_health_path)}}, ensure_ascii=False),
            encoding="utf-8",
        )
        source_health = assess_source_health(
            source_config_path,
            now=datetime.now(timezone.utc),
            max_age_minutes=60,
            warn_if_down_count_gt=0,
            fail_if_ok_count_lt=1,
            critical_source_ids=set(),
        )
        if source_health.get("status") != "warn" or not source_health.get("non_blocking_warnings"):
            raise AssertionError(f"noncritical down sources should make source health warn: {source_health}")

        sidecar_coverage = {
            "ipo": {
                "available_count": 0,
                "required_count": 0,
                "model_generated_count": 0,
                "coverage_ratio": 1.0,
                "required_keys": [],
                "covered_keys": [],
                "missing_required_keys": [],
            },
            "structural": {
                "available_count": 0,
                "required_count": 0,
                "model_generated_count": 0,
                "coverage_ratio": 1.0,
                "required_keys": [],
                "covered_keys": [],
                "missing_required_keys": [],
            },
        }
        base_research_payload = {
            "status": "disabled",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": "smoke-run",
            "as_of_date": "2026-05-05",
            "candidate_count": 0,
            "verdicts": [],
            "ipo_verdicts": [],
            "structural_verdicts": [],
            "model_coverage": {"expected_count": 0, "model_generated_count": 0, "fallback_count": 0, "coverage_ratio": 1.0},
            "sidecar_coverage": sidecar_coverage,
        }
        def model_verdict(idx: int) -> dict[str, object]:
            return {
                "object_id": f"object_{idx}",
                "name": f"对象{idx}",
                "task": "opportunity_screen",
                "reason": "模型结论完整",
                "decision": "keep",
                "weight_delta": 0,
                "evidence_ids": ["event_1"],
                "disqualifiers": [],
                "next_action": {"when": "1-3个交易日", "check": ["检查价格承接和同业扩散"]},
                "confidence": 0.8,
                "provenance": "model_generated",
                "model_generated": True,
            }

        forged_sidecar_payload = dict(
            base_research_payload,
            status="pass",
            candidate_count=3,
            verdicts=[model_verdict(idx) for idx in range(3)],
            model_coverage={"expected_count": 3, "model_generated_count": 3, "fallback_count": 0, "coverage_ratio": 1.0},
            risk_flags=[],
            sidecar_coverage={
                "ipo": {
                    "available_count": 1,
                    "required_count": 1,
                    "model_generated_count": 1,
                    "coverage_ratio": 1.0,
                    "required_keys": ["ipo_1"],
                    "covered_keys": ["ipo_1"],
                    "missing_required_keys": [],
                },
                "structural": {
                    "available_count": 1,
                    "required_count": 1,
                    "model_generated_count": 1,
                    "coverage_ratio": 1.0,
                    "required_keys": ["structural_1"],
                    "covered_keys": ["structural_1"],
                    "missing_required_keys": [],
                },
            },
        )
        if not validate_payload(forged_sidecar_payload):
            raise AssertionError("forged sidecar_coverage with empty sidecar verdict lists should fail validation")
        hidden_required_payload = dict(
            forged_sidecar_payload,
            sidecar_coverage={
                "ipo": {
                    "available_count": 1,
                    "required_count": 0,
                    "model_generated_count": 0,
                    "coverage_ratio": 1.0,
                    "required_keys": [],
                    "covered_keys": [],
                    "missing_required_keys": [],
                },
                "structural": {
                    "available_count": 1,
                    "required_count": 0,
                    "model_generated_count": 0,
                    "coverage_ratio": 1.0,
                    "required_keys": [],
                    "covered_keys": [],
                    "missing_required_keys": [],
                },
            },
        )
        if not validate_payload(hidden_required_payload):
            raise AssertionError("available sidecar items with required_count=0 should fail validation")
        inconsistent_coverage_payload = dict(
            forged_sidecar_payload,
            ipo_verdicts=[
                {
                    "object_id": "ipo_1",
                    "name": "新股一",
                    "task": "ipo_subscription_screen",
                    "decision": "watch_only",
                    "weight_delta": -10,
                    "reason": "资料不足",
                    "evidence_ids": ["ipo_event_1"],
                    "next_action": {"when": "申购期内", "check": ["核对公开认购倍数"]},
                    "disqualifiers": [],
                    "confidence": 0.6,
                    "provenance": "model_generated",
                    "model_generated": True,
                }
            ],
            sidecar_coverage={
                "ipo": {
                    "available_count": 1,
                    "required_count": 1,
                    "model_generated_count": 1,
                    "coverage_ratio": 1.0,
                    "required_keys": ["ipo_1"],
                    "covered_keys": ["ipo_2"],
                    "missing_required_keys": [],
                },
                "structural": {
                    "available_count": 0,
                    "required_count": 0,
                    "model_generated_count": 0,
                    "coverage_ratio": 1.0,
                    "required_keys": [],
                    "covered_keys": [],
                    "missing_required_keys": [],
                },
            },
        )
        if not validate_payload(inconsistent_coverage_payload):
            raise AssertionError("covered_keys outside required_keys should fail validation")
        string_flag_payload = dict(base_research_payload, risk_flags=["source degraded"])
        if not validate_payload(string_flag_payload):
            raise AssertionError("string risk_flags should fail Kimi research validation")
        structured_flag_payload = dict(
            base_research_payload,
            risk_flags=[
                {
                    "flag_type": "source_degradation",
                    "source_id": "weibo_tracked_mobile",
                    "affected_names": ["TCL科技"],
                    "detail": "source degraded",
                    "next_action": {"when": "1-3个交易日", "check": ["检查 API 响应码"]},
                    "provenance": "model_generated",
                    "model_generated": True,
                }
            ],
        )
        structured_errors = validate_payload(structured_flag_payload)
        if structured_errors:
            raise AssertionError(f"structured risk_flags should validate: {structured_errors}")
        shallow_flag_payload = dict(
            base_research_payload,
            risk_flags=[
                {
                    "flag_type": "source_degradation",
                    "source_id": "weibo_tracked_mobile",
                    "affected_names": ["TCL科技"],
                    "detail": "source degraded",
                    "next_action": {"when": "1-3个交易日", "check": ["定位风险来源/确认是否影响前排对象"]},
                    "provenance": "model_generated",
                    "model_generated": True,
                }
            ],
        )
        if not validate_payload(shallow_flag_payload):
            raise AssertionError("shallow risk_flag next_action should fail Kimi research validation")

    print("radar_harness_v2_smoke: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
