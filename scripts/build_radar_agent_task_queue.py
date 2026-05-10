#!/usr/bin/env python3
"""Build a durable Agent Harness task queue from Radar production health artifacts."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_JSON = Path(os.environ.get("RADAR_AGENT_TASK_QUEUE_JSON") or ROOT / "output" / "agent_tasks" / "radar_agent_task_queue_latest.json")
DEFAULT_OUTPUT_MD = Path(os.environ.get("RADAR_AGENT_TASK_QUEUE_MD") or ROOT / "output" / "agent_tasks" / "radar_agent_task_queue_latest.md")
TASK_QUEUE_LOCK = Path(os.environ.get("RADAR_AGENT_TASK_QUEUE_LOCK") or ROOT / "state" / "radar_agent_task_queue.lock")
TERMINAL_STATUSES = {"done"}
ACTIVE_STATUSES = {"ready", "claimed", "running", "blocked"}
LEASED_STATUSES = {"claimed", "running"}
ACTION_SUCCESS = 0
ACTION_VALIDATION_FAILED = 2
ACTION_STALE_TASK_STATE = 3


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc_datetime(value: Any) -> datetime | None:
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


def tail_text(value: str, *, limit: int = 2000) -> str:
    text = str(value or "").strip()
    return text[-limit:] if len(text) > limit else text


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--claim", metavar="TASK_ID", help="Claim a ready task for an agent.")
    action.add_argument("--start", metavar="TASK_ID", help="Mark a claimed task as running.")
    action.add_argument("--complete", metavar="TASK_ID", help="Complete a claimed/running task with a review verdict.")
    action.add_argument("--block", metavar="TASK_ID", help="Block a task with a reason.")
    action.add_argument("--release", metavar="TASK_ID", help="Release a claimed/running task back to ready.")
    action.add_argument("--run-ready", action="store_true", help="Claim and validate ready low-risk tasks.")
    parser.add_argument("--agent", default="codex", help="Agent id for claim/start/complete actions.")
    parser.add_argument("--lease-minutes", type=int, default=90)
    parser.add_argument("--review-verdict", choices=["pass", "fail", "needs_retry"], default="pass")
    parser.add_argument("--review-notes", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--validation-timeout-seconds", type=int, default=900)
    parser.add_argument("--max-tasks", type=int, default=int(os.environ.get("RADAR_AGENT_TASK_AUTORUN_MAX") or 1))
    parser.add_argument("--auto-priorities", default=os.environ.get("RADAR_AGENT_TASK_AUTORUN_PRIORITIES") or "P2,P3")
    parser.add_argument("--run-repair-commands", action="store_true", help="Run task repair_commands before validation during complete.")
    return parser.parse_args(argv)


def task_id(title: str, identity: str) -> str:
    digest = hashlib.sha256(f"{title}|{identity}".encode("utf-8")).hexdigest()[:10]
    return f"radar-agent-task-{digest}"


def trigger_hash(trigger: str) -> str:
    return hashlib.sha256(trigger.encode("utf-8")).hexdigest()[:16]


def make_task(
    *,
    title: str,
    trigger: str,
    owner_agent: str,
    priority: str,
    allowed_write_paths: list[str],
    context_paths: list[str],
    validation_commands: list[str],
    repair_commands: list[str] | None = None,
    agent_prompt: str = "",
    dedupe_key: str = "",
    auto_run_with_active_blockers: bool = False,
) -> dict[str, Any]:
    stable_identity = dedupe_key or trigger
    task_key = task_id(title, stable_identity)
    now = utc_now_iso()
    return {
        "task_id": task_key,
        "status": "ready",
        "lifecycle": "ready_for_claim",
        "priority": priority,
        "owner_agent": owner_agent,
        "title": title,
        "trigger": trigger,
        "dedupe_key": stable_identity,
        "trigger_hash": trigger_hash(trigger),
        "allowed_write_paths": allowed_write_paths,
        "context_paths": context_paths,
        "repair_commands": repair_commands or [],
        "validation_commands": validation_commands,
        "auto_run_with_active_blockers": auto_run_with_active_blockers,
        "agent_prompt": agent_prompt or "",
        "done_when": "repair commands have run, validation commands pass, and the trigger no longer appears in current health artifacts",
        "review_required": True,
        "attempt_count": 0,
        "max_attempts": 2 if priority in {"P0", "P1"} else 1,
        "claimed_by": "",
        "claimed_at": "",
        "lease_expires_at": "",
        "result_artifact_path": f"output/agent_tasks/results/{task_key}.json",
        "review_verdict": "pending",
        "review_notes": "",
        "created_at": now,
        "updated_at": now,
        "state_transitions": [{"status": "ready", "at": now, "reason": "task generated from health artifacts"}],
    }


def append_transition(task: dict[str, Any], status: str, reason: str) -> None:
    task.setdefault("state_transitions", []).append({"status": status, "at": utc_now_iso(), "reason": reason})
    task["updated_at"] = utc_now_iso()


@contextmanager
def locked_queue_state() -> Any:
    TASK_QUEUE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with TASK_QUEUE_LOCK.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def lease_expired(task: dict[str, Any], *, now: datetime | None = None) -> bool:
    if str(task.get("status") or "") not in LEASED_STATUSES:
        return False
    expires_at = parse_utc_datetime(task.get("lease_expires_at"))
    if expires_at is None:
        return False
    return expires_at <= (now or datetime.now(timezone.utc))


def reclaim_expired_leases(tasks: list[dict[str, Any]], *, protected_task_id: str = "", protected_agent: str = "") -> None:
    now = datetime.now(timezone.utc)
    for task in tasks:
        if not isinstance(task, dict) or not lease_expired(task, now=now):
            continue
        if (
            protected_task_id
            and str(task.get("task_id") or "") == protected_task_id
            and (not protected_agent or str(task.get("claimed_by") or "") == protected_agent)
        ):
            continue
        attempts = int(task.get("attempt_count") or 0)
        max_attempts = int(task.get("max_attempts") or 1)
        reason = f"lease expired at {task.get('lease_expires_at')}"
        task["claimed_by"] = ""
        task["claimed_at"] = ""
        task["lease_expires_at"] = ""
        if attempts < max_attempts:
            task["status"] = "ready"
            task["lifecycle"] = "ready_for_claim"
            append_transition(task, "ready", reason)
        else:
            task["status"] = "blocked"
            task["lifecycle"] = "lease_expired"
            task["review_verdict"] = "needs_retry"
            task["review_notes"] = reason
            append_transition(task, "blocked", reason)


def queue_payload(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    reclaim_expired_leases(tasks)
    status_counts: dict[str, int] = {}
    for task in tasks:
        task_status = str(task.get("status") or "unknown")
        status_counts[task_status] = status_counts.get(task_status, 0) + 1
    return {
        "generated_at": utc_now_iso(),
        "status": queue_status(tasks),
        "task_count": len(tasks),
        "pending_count": len([task for task in tasks if str(task.get("status") or "") in ACTIVE_STATUSES]),
        "ready_count": status_counts.get("ready", 0),
        "claimed_count": status_counts.get("claimed", 0) + status_counts.get("running", 0),
        "blocked_count": status_counts.get("blocked", 0),
        "done_count": status_counts.get("done", 0),
        "status_counts": status_counts,
        "execution_contract": {
            "claim_required": True,
            "lease_required": True,
            "result_artifact_required": True,
            "review_verdict_required": True,
            "validation_required_for_done": True,
            "repair_commands_supported": True,
            "auto_worker_runs_repair_before_validation": True,
            "expired_lease_reclaim": True,
            "allowed_statuses": ["ready", "claimed", "running", "blocked", "done"],
            "actions": ["claim", "start", "complete", "block", "release"],
        },
        "tasks": tasks,
    }


def write_payload(payload: dict[str, Any]) -> None:
    atomic_write_text(DEFAULT_OUTPUT_JSON, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(DEFAULT_OUTPUT_MD, render_markdown(payload))


def find_task(payload: dict[str, Any], task_key: str) -> dict[str, Any]:
    for task in payload.get("tasks") or []:
        if isinstance(task, dict) and str(task.get("task_id") or "") == task_key:
            return task
    raise SystemExit(f"task not found: {task_key}")


def result_artifact_path(task: dict[str, Any]) -> Path:
    raw = Path(str(task.get("result_artifact_path") or ""))
    return raw if raw.is_absolute() else ROOT / raw


def run_validation_commands(commands: list[str], *, timeout_seconds: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        command_text = str(command or "").strip()
        if not command_text:
            continue
        started = time.monotonic()
        row: dict[str, Any] = {"command": command_text, "status": "running", "timeout_seconds": timeout_seconds}
        try:
            completed = subprocess.run(
                command_text,
                cwd=ROOT,
                shell=True,
                check=False,
                text=True,
                capture_output=True,
                timeout=max(timeout_seconds, 1),
            )
        except subprocess.TimeoutExpired as exc:
            row.update(
                {
                    "status": "fail",
                    "timed_out": True,
                    "returncode": None,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "stdout_tail": tail_text(exc.stdout or ""),
                    "stderr_tail": tail_text(exc.stderr or f"validation timed out after {timeout_seconds}s"),
                }
            )
        else:
            row.update(
                {
                    "status": "pass" if completed.returncode == 0 else "fail",
                    "timed_out": False,
                    "returncode": completed.returncode,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "stdout_tail": tail_text(completed.stdout),
                    "stderr_tail": tail_text(completed.stderr),
                }
            )
        results.append(row)
    return results


def validation_passed(results: list[dict[str, Any]], commands: list[str]) -> bool:
    expected_count = len([command for command in commands if str(command or "").strip()])
    if expected_count <= 0:
        return False
    return len(results) == expected_count and all(str(item.get("status") or "") == "pass" for item in results)


def validation_lease_seconds(commands: list[str], *, timeout_seconds: int) -> int:
    command_count = max(len([command for command in commands if str(command or "").strip()]), 1)
    return max(max(timeout_seconds, 1) * command_count + 60, 60)


def completion_command_budget(task: dict[str, Any]) -> list[str]:
    repair_commands = [str(item) for item in (task.get("repair_commands") or []) if str(item).strip()]
    validation_commands = [str(item) for item in (task.get("validation_commands") or []) if str(item).strip()]
    return [*repair_commands, *validation_commands]


def completion_timeout_seconds(task: dict[str, Any], *, validation_timeout_seconds: int) -> int:
    command_count = max(len(completion_command_budget(task)), 1)
    return max(validation_timeout_seconds * command_count + 120, validation_timeout_seconds + 30)


def prepare_complete_task(payload: dict[str, Any], task_key: str, *, agent: str, validation_timeout_seconds: int = 900) -> dict[str, Any]:
    task = find_task(payload, task_key)
    if str(task.get("status") or "") not in {"claimed", "running"}:
        raise SystemExit(f"task {task_key} is not claimed/running; status={task.get('status')}")
    if task.get("claimed_by") and task.get("claimed_by") != agent:
        raise SystemExit(f"task {task_key} claimed_by={task.get('claimed_by')}, not {agent}")
    commands = [str(item) for item in (task.get("validation_commands") or []) if str(item).strip()]
    execution_budget = completion_command_budget(task)
    if str(task.get("status") or "") == "claimed":
        task["status"] = "running"
    task["lifecycle"] = "validating"
    task["lease_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=validation_lease_seconds(execution_budget, timeout_seconds=validation_timeout_seconds))
    ).isoformat(timespec="seconds")
    append_transition(task, "running", f"validation_started_by={agent}")
    return {
        "task_id": task_key,
        "agent": agent,
        "trigger_hash": task.get("trigger_hash"),
        "result_artifact_path": str(task.get("result_artifact_path") or ""),
        "validation_commands": commands,
        "repair_commands": [str(item) for item in (task.get("repair_commands") or []) if str(item).strip()],
        "lease_expires_at": task.get("lease_expires_at"),
    }


def write_result_artifact(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def build_result_payload(
    *,
    completion_context: dict[str, Any],
    review_verdict: str,
    review_notes: str,
    validation_results: list[dict[str, Any]],
    apply_status: str,
    repair_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    commands = [str(item) for item in (completion_context.get("validation_commands") or []) if str(item).strip()]
    repair_results = repair_results or []
    repair_ok = all(str(item.get("status") or "") == "pass" for item in repair_results) if repair_results else True
    validation_ok = validation_passed(validation_results, commands) if review_verdict == "pass" else False
    return {
        "generated_at": utc_now_iso(),
        "task_id": completion_context.get("task_id"),
        "agent": completion_context.get("agent"),
        "review_verdict": review_verdict,
        "review_notes": review_notes,
        "repair_status": "pass" if repair_ok else "fail",
        "repair_commands": [str(item) for item in (completion_context.get("repair_commands") or []) if str(item).strip()],
        "repair_results": repair_results,
        "validation_status": "pass" if validation_ok else "not_run" if review_verdict != "pass" else "fail",
        "validation_commands": commands,
        "validation_results": validation_results,
        "trigger_hash": completion_context.get("trigger_hash"),
        "apply_status": apply_status,
    }


def claim_task(payload: dict[str, Any], task_key: str, *, agent: str, lease_minutes: int) -> dict[str, Any]:
    task = find_task(payload, task_key)
    if str(task.get("status") or "") != "ready":
        raise SystemExit(f"task {task_key} is not ready; status={task.get('status')}")
    if int(task.get("attempt_count") or 0) >= int(task.get("max_attempts") or 1):
        raise SystemExit(f"task {task_key} reached max_attempts")
    now = utc_now_iso()
    task["status"] = "claimed"
    task["lifecycle"] = "claimed"
    task["claimed_by"] = agent
    task["claimed_at"] = now
    task["lease_expires_at"] = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + max(lease_minutes, 1) * 60, timezone.utc).isoformat(timespec="seconds")
    task["attempt_count"] = int(task.get("attempt_count") or 0) + 1
    append_transition(task, "claimed", f"claimed_by={agent}")
    return payload


def start_task(payload: dict[str, Any], task_key: str, *, agent: str) -> dict[str, Any]:
    task = find_task(payload, task_key)
    if str(task.get("status") or "") != "claimed":
        raise SystemExit(f"task {task_key} is not claimed; status={task.get('status')}")
    if task.get("claimed_by") and task.get("claimed_by") != agent:
        raise SystemExit(f"task {task_key} claimed_by={task.get('claimed_by')}, not {agent}")
    task["status"] = "running"
    task["lifecycle"] = "running"
    append_transition(task, "running", f"started_by={agent}")
    return payload


def complete_task(
    payload: dict[str, Any],
    task_key: str,
    *,
    agent: str,
    review_verdict: str,
    review_notes: str,
    validation_timeout_seconds: int = 900,
    run_repair_commands: bool = False,
) -> dict[str, Any]:
    completion_context = prepare_complete_task(payload, task_key, agent=agent, validation_timeout_seconds=validation_timeout_seconds)
    repair_commands = [str(item) for item in (completion_context.get("repair_commands") or []) if str(item).strip()]
    repair_results = run_validation_commands(repair_commands, timeout_seconds=validation_timeout_seconds) if run_repair_commands and review_verdict == "pass" else []
    repair_ok = all(str(item.get("status") or "") == "pass" for item in repair_results) if repair_results else True
    commands = [str(item) for item in (completion_context.get("validation_commands") or []) if str(item).strip()]
    validation_results = run_validation_commands(commands, timeout_seconds=validation_timeout_seconds) if review_verdict == "pass" and repair_ok else []
    return apply_complete_task(
        payload,
        completion_context=completion_context,
        review_verdict=review_verdict,
        review_notes=review_notes,
        repair_results=repair_results,
        validation_results=validation_results,
    )


def apply_complete_task(
    payload: dict[str, Any],
    *,
    completion_context: dict[str, Any],
    review_verdict: str,
    review_notes: str,
    validation_results: list[dict[str, Any]],
    repair_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    task_key = str(completion_context.get("task_id") or "")
    agent = str(completion_context.get("agent") or "")
    commands = [str(item) for item in (completion_context.get("validation_commands") or []) if str(item).strip()]
    validation_ok = validation_passed(validation_results, commands) if review_verdict == "pass" else False
    raw_artifact_path = Path(str(completion_context.get("result_artifact_path") or ""))
    artifact_path = raw_artifact_path if raw_artifact_path.is_absolute() else ROOT / raw_artifact_path
    task = next(
        (
            item
            for item in (payload.get("tasks") or [])
            if isinstance(item, dict) and str(item.get("task_id") or "") == task_key
        ),
        None,
    )
    if task is None:
        result_payload = build_result_payload(
            completion_context=completion_context,
            review_verdict=review_verdict,
            review_notes=review_notes,
            repair_results=repair_results,
            validation_results=validation_results,
            apply_status="resolved_before_apply" if validation_ok else "missing_task_after_validation",
        )
        write_result_artifact(artifact_path, result_payload)
        if not validation_ok:
            payload["_action_exit_code"] = ACTION_VALIDATION_FAILED
        return payload
    stale_reasons: list[str] = []
    if str(task.get("trigger_hash") or "") != str(completion_context.get("trigger_hash") or ""):
        stale_reasons.append("trigger_hash_changed")
    if str(task.get("claimed_by") or "") != agent:
        stale_reasons.append(f"claimed_by={task.get('claimed_by')}")
    if str(task.get("status") or "") not in {"claimed", "running"}:
        stale_reasons.append(f"status={task.get('status')}")
    if stale_reasons:
        result_payload = build_result_payload(
            completion_context=completion_context,
            review_verdict=review_verdict,
            review_notes=review_notes,
            repair_results=repair_results,
            validation_results=validation_results,
            apply_status="stale_task_state:" + ",".join(stale_reasons),
        )
        write_result_artifact(artifact_path, result_payload)
        payload["_action_exit_code"] = ACTION_STALE_TASK_STATE
        return payload
    result_payload = build_result_payload(
        completion_context=completion_context,
        review_verdict=review_verdict,
        review_notes=review_notes,
        repair_results=repair_results,
        validation_results=validation_results,
        apply_status="applied",
    )
    write_result_artifact(artifact_path, result_payload)
    task["review_verdict"] = review_verdict if validation_ok or review_verdict != "pass" else "fail"
    task["review_notes"] = review_notes
    task["result_artifact_path"] = str(artifact_path.relative_to(ROOT) if artifact_path.is_relative_to(ROOT) else artifact_path)
    task["claimed_by"] = agent
    if review_verdict == "pass" and validation_ok:
        task["status"] = "done"
        task["lifecycle"] = "reviewed_pass"
        task["claimed_at"] = ""
        task["lease_expires_at"] = ""
    elif review_verdict == "pass":
        task["status"] = "blocked"
        task["lifecycle"] = "validation_failed"
        task["review_notes"] = (review_notes + " " if review_notes else "") + "validation failed; see result artifact"
        payload["_action_exit_code"] = ACTION_VALIDATION_FAILED
    elif review_verdict == "needs_retry" and int(task.get("attempt_count") or 0) < int(task.get("max_attempts") or 1):
        task["status"] = "ready"
        task["lifecycle"] = "ready_for_claim"
        task["claimed_by"] = ""
        task["claimed_at"] = ""
        task["lease_expires_at"] = ""
    else:
        task["status"] = "blocked"
        task["lifecycle"] = "reviewed_blocked"
    append_transition(task, task["status"], f"completed_by={agent} review_verdict={review_verdict}")
    return payload


def block_task(payload: dict[str, Any], task_key: str, *, reason: str) -> dict[str, Any]:
    task = find_task(payload, task_key)
    task["status"] = "blocked"
    task["lifecycle"] = "blocked"
    task["review_notes"] = reason
    append_transition(task, "blocked", reason or "blocked")
    return payload


def release_task(payload: dict[str, Any], task_key: str, *, reason: str) -> dict[str, Any]:
    task = find_task(payload, task_key)
    if str(task.get("status") or "") not in {"claimed", "running", "blocked"}:
        raise SystemExit(f"task {task_key} cannot be released from status={task.get('status')}")
    attempts = int(task.get("attempt_count") or 0)
    max_attempts = int(task.get("max_attempts") or 1)
    task["status"] = "ready"
    task["lifecycle"] = "ready_for_claim"
    task["claimed_by"] = ""
    task["claimed_at"] = ""
    task["lease_expires_at"] = ""
    if attempts >= max_attempts:
        task["attempt_count"] = max(max_attempts - 1, 0)
    append_transition(task, "ready", reason or "released")
    return payload


def auto_priority_set(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def priority_rank(value: Any) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(str(value or ""), 9)


def run_ready_tasks(*, agent: str, priorities: set[str], max_tasks: int, validation_timeout_seconds: int, lease_minutes: int) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    skipped: dict[str, Any] = {}
    max_count = max(max_tasks, 0)
    for _ in range(max_count):
        with locked_queue_state():
            payload = read_json(DEFAULT_OUTPUT_JSON)
            if not payload:
                payload = queue_payload(build_tasks())
            existing_tasks = [item for item in (payload.get("tasks") or []) if isinstance(item, dict)]
            reclaim_expired_leases(existing_tasks)
            payload["tasks"] = existing_tasks
            candidates = sorted(
                [
                    item
                    for item in existing_tasks
                    if str(item.get("status") or "") == "ready"
                    and str(item.get("priority") or "") in priorities
                    and item.get("repair_commands")
                    and item.get("validation_commands")
                ],
                key=lambda item: (priority_rank(item.get("priority")), str(item.get("created_at") or ""), str(item.get("task_id") or "")),
            )
            blocking_tasks = [
                item
                for item in existing_tasks
                if str(item.get("status") or "") not in TERMINAL_STATUSES and str(item.get("priority") or "") in {"P0", "P1"}
            ]
            if blocking_tasks:
                candidates = [item for item in candidates if item.get("auto_run_with_active_blockers") is True]
                if not candidates:
                    skipped = {
                        "ran_at": utc_now_iso(),
                        "agent": agent,
                        "max_tasks": max_count,
                        "priorities": sorted(priorities),
                        "skipped_reason": "active_p0_p1_tasks",
                        "blocked_by": [str(item.get("task_id") or "") for item in blocking_tasks[:5]],
                        "results": results,
                    }
                    payload = queue_payload(existing_tasks)
                    payload["auto_worker"] = skipped
                    write_payload(payload)
                    break
            task = candidates[0] if candidates else None
            if not task:
                payload = queue_payload(existing_tasks)
                write_payload(payload)
                break
            task_key = str(task.get("task_id") or "")
            timeout_seconds = completion_timeout_seconds(task, validation_timeout_seconds=validation_timeout_seconds)
            payload = claim_task(payload, task_key, agent=agent, lease_minutes=lease_minutes)
            payload = queue_payload([item for item in (payload.get("tasks") or []) if isinstance(item, dict)])
            write_payload(payload)
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--complete",
                task_key,
                "--agent",
                agent,
                "--review-verdict",
                "pass",
                "--validation-timeout-seconds",
                str(validation_timeout_seconds),
                "--run-repair-commands",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        results.append(
            {
                "task_id": task_key,
                "returncode": completed.returncode,
                "stdout_tail": tail_text(completed.stdout),
                "stderr_tail": tail_text(completed.stderr),
            }
        )
    with locked_queue_state():
        payload = read_json(DEFAULT_OUTPUT_JSON)
        if not payload:
            payload = queue_payload(build_tasks())
        tasks = [item for item in (payload.get("tasks") or []) if isinstance(item, dict)]
        payload = queue_payload(tasks)
        payload["auto_worker"] = skipped or {
            "ran_at": utc_now_iso(),
            "agent": agent,
            "max_tasks": max_count,
            "priorities": sorted(priorities),
            "results": results,
        }
        write_payload(payload)
    return payload


def grouped_attention_items(items: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        if not label:
            continue
        grouped.setdefault(label, []).append(item)
    return sorted(grouped.items(), key=lambda pair: pair[0])


def unique_context_paths(items: list[dict[str, Any]], fallback: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for item in items:
        path = str(item.get("output_path") or "").strip()
        if path and path not in seen:
            paths.append(path)
            seen.add(path)
    if fallback not in seen:
        paths.append(fallback)
    return paths


def merge_previous_task_state(tasks: list[dict[str, Any]], previous_payload: dict[str, Any]) -> list[dict[str, Any]]:
    previous = {
        str(item.get("task_id") or ""): item
        for item in (previous_payload.get("tasks") or [])
        if isinstance(item, dict) and str(item.get("task_id") or "")
    }
    merged: list[dict[str, Any]] = []
    for task in tasks:
        old = previous.get(str(task.get("task_id") or ""))
        if not old or old.get("trigger_hash") != task.get("trigger_hash"):
            merged.append(task)
            continue
        for key in (
            "attempt_count",
            "claimed_by",
            "claimed_at",
            "lease_expires_at",
            "result_artifact_path",
            "review_verdict",
            "review_notes",
            "created_at",
            "state_transitions",
        ):
            if old.get(key) not in (None, ""):
                task[key] = old.get(key)
        if str(old.get("status") or "") in {"claimed", "running", "blocked", "done"}:
            task["status"] = old.get("status")
            task["lifecycle"] = old.get("lifecycle") or task.get("lifecycle")
        if str(old.get("status") or "") == "done":
            append_transition(task, "ready", "trigger still present after done; reopened")
            task["status"] = "ready"
            task["lifecycle"] = "ready_for_claim"
            task["attempt_count"] = 0
            task["claimed_by"] = ""
            task["claimed_at"] = ""
            task["lease_expires_at"] = ""
            task["review_verdict"] = "pending"
            task["review_notes"] = "trigger still present after done; reopened"
        task["updated_at"] = utc_now_iso()
        merged.append(task)
    reclaim_expired_leases(merged)
    return merged


def build_tasks() -> list[dict[str, Any]]:
    harness = read_json(ROOT / "output" / "runs" / "radar_harness_manifest_latest.json")
    source_readiness = read_json(ROOT / "output" / "reports" / "radar_source_readiness_latest.json")
    quality = read_json(ROOT / "output" / "reports" / "radar_report_quality_latest.json")
    kimi_research = read_json(ROOT / "output" / "reports" / "radar_kimi_research_harness_latest.json")
    news_verification = read_json(ROOT / "output" / "reports" / "radar_news_verification_latest.json")
    delivery = read_json(ROOT / "health" / "radar_daily_email_delivery_latest.json")
    scheduled_delivery = read_json(ROOT / "health" / "radar_daily_email_delivery_scheduled_latest.json")
    tasks: list[dict[str, Any]] = []
    kimi_note = str(kimi_research.get("note") or "")
    kimi_summary = str(kimi_research.get("summary") or "")
    kimi_degraded = (
        "retry" in kimi_note.lower()
        or "successful_recoveries" in kimi_note.lower()
        or "batch" in kimi_summary.lower()
        or bool(kimi_research.get("operational_flags"))
    )

    harness_attention = (
        [*(harness.get("production_failures") or []), *(harness.get("production_warnings") or [])]
        if str(harness.get("schema_version") or "") == "radar_harness_manifest.v2"
        else (harness.get("degraded_steps") or [])
    )
    for item in harness_attention:
        label = str(item.get("label") or "")
        if not label:
            continue
        if label == "build_radar_agent_task_queue":
            continue
        severity = str(item.get("severity") or "")
        priority = "P1" if severity == "fail" or item.get("status") == "fail" else "P2"
        tasks.append(
            make_task(
                title=f"修复 Radar 生产节点：{label}",
                trigger=json.dumps(item, ensure_ascii=False),
                owner_agent="codex",
                priority=priority,
                allowed_write_paths=["scripts/", "config/", "docs/", "workpapers/"],
                context_paths=[
                    "output/runs/radar_harness_manifest_latest.json",
                    "output/reports/radar_report_quality_latest.json",
                ],
                validation_commands=[
                    "python3 scripts/build_radar_workspace_outputs.py",
                    "python3 scripts/check_radar_report_quality.py",
                ],
                repair_commands=["python3 scripts/build_radar_workspace_outputs.py"],
                agent_prompt="Inspect the failing production node, apply a minimal fix inside allowed_write_paths, then rerun the workspace build and quality gate.",
                dedupe_key=f"production_node:{label}",
            )
        )

    if str(harness.get("schema_version") or "") == "radar_harness_manifest.v2":
        for label, items in grouped_attention_items(harness.get("research_warnings")):
            if label == "build_radar_kimi_research_harness" and kimi_degraded:
                continue
            tasks.append(
                make_task(
                    title=f"复核 Radar 研究告警：{label}",
                    trigger=json.dumps({"label": label, "warning_count": len(items), "warnings": items[:6]}, ensure_ascii=False),
                    owner_agent="kimi",
                    priority="P3",
                    allowed_write_paths=["scripts/", "config/", "docs/", "workpapers/"],
                    context_paths=[
                        "output/runs/radar_harness_manifest_latest.json",
                        *unique_context_paths(items, "output/reports/radar_kimi_research_harness_latest.json"),
                    ],
                    validation_commands=[
                        "python3 scripts/build_radar_kimi_research_harness.py",
                        "python3 scripts/validate_radar_kimi_research.py",
                    ],
                    repair_commands=["python3 scripts/build_radar_kimi_research_harness.py"],
                    agent_prompt="Rebuild or repair the degraded research sidecar and verify the Kimi research contract.",
                    dedupe_key=f"research_warning:{label}",
                )
            )
        for item in harness.get("maintenance_warnings") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "")
            if not label:
                continue
            if label in {"build_radar_source_readiness", "check_radar_report_quality"}:
                continue
            tasks.append(
                make_task(
                    title=f"收敛 Radar 维护告警：{label}",
                    trigger=json.dumps(item, ensure_ascii=False),
                    owner_agent="codex",
                    priority="P3",
                    allowed_write_paths=["scripts/", "config/", "../news_event_hub/scripts/", "../news_event_hub/config/"],
                    context_paths=[
                        "output/runs/radar_harness_manifest_latest.json",
                        str(item.get("output_path") or "output/reports/radar_company_enrichment_latest.json"),
                    ],
                    validation_commands=[
                        "python3 scripts/build_radar_company_enrichment_sidecar.py",
                        "python3 scripts/check_radar_report_quality.py",
                    ],
                    repair_commands=["python3 scripts/build_radar_company_enrichment_sidecar.py"],
                    agent_prompt="Refresh the maintenance sidecar, inspect remaining warnings, and only complete when quality no longer depends on this warning.",
                    dedupe_key=f"maintenance_warning:{label}",
                )
            )

    source_health = source_readiness.get("source_health") or {}
    if str(source_health.get("status") or "") in {"warn", "fail"}:
        summary = source_health.get("summary") or {}
        tasks.append(
            make_task(
                title="复核共享新闻底座降级源",
                trigger=f"source_health={source_health.get('status')} ok={summary.get('ok')} degraded={summary.get('degraded')} down={summary.get('down')}",
                owner_agent="kimi",
                priority="P1" if str(source_health.get("status") or "") == "fail" else "P2",
                allowed_write_paths=["../news_event_hub/scripts/", "../news_event_hub/config/", "scripts/", "docs/"],
                context_paths=[
                    "output/reports/radar_source_readiness_latest.json",
                    "../news_event_hub/state/consumer_exports/source_health_latest.json",
                ],
                validation_commands=[
                    "python3 scripts/build_radar_source_readiness.py",
                    "python3 scripts/check_radar_report_quality.py",
                ],
                repair_commands=[
                    "python3 scripts/sync_shared_news_event_hub_exports.py",
                    "python3 scripts/build_radar_source_readiness.py",
                ],
                agent_prompt="Refresh News Event Hub consumer exports, inspect down/degraded source ids, and route persistent collector failures to the news system before validating Radar quality.",
                dedupe_key="root_cause:shared_news_source_health",
                auto_run_with_active_blockers=True,
            )
        )

    if str(kimi_research.get("status") or "") != "pass" or kimi_degraded:
        related_warnings = [
            item
            for item in (harness.get("research_warnings") or [])
            if isinstance(item, dict) and str(item.get("label") or "") == "build_radar_kimi_research_harness"
        ]
        tasks.append(
            make_task(
                title="收敛 Kimi research harness 降级路径",
                trigger=json.dumps(
                    {
                        "status": kimi_research.get("status"),
                        "summary": kimi_summary,
                        "note": kimi_note[:240],
                        "risk_flags": kimi_research.get("risk_flags") or [],
                        "operational_flags": kimi_research.get("operational_flags") or [],
                        "research_warning_count": len(related_warnings),
                        "research_warnings": related_warnings[:6],
                    },
                    ensure_ascii=False,
                ),
                owner_agent="codex",
                priority="P1" if str(kimi_research.get("status") or "") != "pass" else "P3",
                allowed_write_paths=["scripts/build_radar_kimi_research_harness.py", "scripts/validate_radar_kimi_research.py", "scripts/check_radar_report_quality.py", "config/"],
                context_paths=[
                    "output/reports/radar_kimi_research_harness_latest.json",
                    "output/reports/radar_report_quality_latest.json",
                ],
                validation_commands=[
                    "python3 scripts/build_radar_kimi_research_harness.py",
                    "python3 scripts/validate_radar_kimi_research.py",
                    "python3 scripts/check_radar_report_quality.py",
                ],
                repair_commands=["python3 scripts/build_radar_kimi_research_harness.py"],
                agent_prompt="Rerun the Kimi research harness, inspect operational_flags/risk_flags, and keep the task open if model coverage or sidecar coverage remains degraded.",
                dedupe_key="root_cause:kimi_research_harness",
            )
        )

    if str(news_verification.get("status") or "") == "warn":
        tasks.append(
            make_task(
                title="补齐前排对象新闻补核缺口",
                trigger="radar_news_verification_latest.json status=warn",
                owner_agent="kimi",
                priority="P2",
                allowed_write_paths=["scripts/build_radar_news_verification_sidecar.py", "../news_event_hub/scripts/"],
                context_paths=[
                    "output/reports/radar_news_verification_latest.json",
                    "output/snapshots/radar_opportunity_snapshot_latest.json",
                ],
                validation_commands=[
                    "python3 scripts/build_radar_news_verification_sidecar.py",
                    "python3 scripts/check_radar_report_quality.py",
                ],
                repair_commands=["python3 scripts/build_radar_news_verification_sidecar.py"],
                agent_prompt="Use the News Event Hub sidecar to refill top-object verification gaps; completion requires the front-page verification blocker to disappear.",
                dedupe_key="root_cause:news_verification",
                auto_run_with_active_blockers=True,
            )
        )

    if str(delivery.get("status") or "") in {"warn", "fail"}:
        tasks.append(
            make_task(
                title="修复日报邮件投递链",
                trigger=f"delivery_status={delivery.get('status')} detail={delivery.get('detail')}",
                owner_agent="codex",
                priority="P1",
                allowed_write_paths=["scripts/run_radar_daily_report_email.sh", "scripts/send_radar_daily_report_email.py", "config/systemd/"],
                context_paths=["health/radar_daily_email_delivery_latest.json", "logs/industry_signal_radar_daily_report.log"],
                validation_commands=[
                    "bash -n scripts/run_radar_daily_report_email.sh",
                    "python3 scripts/send_radar_daily_report_email.py --report-transport local --dry-run",
                ],
                repair_commands=["bash -n scripts/run_radar_daily_report_email.sh"],
                agent_prompt="Inspect delivery manifests, SMTP/dry-run status, and systemd wrapper logs; repair only the delivery path.",
                dedupe_key="delivery:latest",
            )
        )

    if (
        scheduled_delivery
        and str(scheduled_delivery.get("status") or "") in {"warn", "fail"}
        and str(delivery.get("systemd_invocation_id") or "") != str(scheduled_delivery.get("systemd_invocation_id") or "")
    ):
        tasks.append(
            make_task(
                title="修复日报定时投递链",
                trigger=(
                    f"scheduled_delivery_status={scheduled_delivery.get('status')} "
                    f"detail={scheduled_delivery.get('detail')} "
                    f"started_at={scheduled_delivery.get('invocation_started_at')}"
                ),
                owner_agent="codex",
                priority="P1",
                allowed_write_paths=["scripts/run_radar_daily_report_email.sh", "scripts/send_radar_daily_report_email.py", "config/systemd/"],
                context_paths=[
                    "health/radar_daily_email_delivery_scheduled_latest.json",
                    "health/radar_daily_email_delivery_latest.json",
                    "logs/industry_signal_radar_daily_report.log",
                ],
                validation_commands=[
                    "bash -n scripts/run_radar_daily_report_email.sh",
                    "python3 scripts/send_radar_daily_report_email.py --report-transport local --dry-run",
                ],
                repair_commands=["bash -n scripts/run_radar_daily_report_email.sh"],
                agent_prompt="Inspect the scheduled delivery manifest and systemd unit path; do not mark done until scheduled delivery can be attributed to a valid invocation.",
                dedupe_key="delivery:scheduled",
            )
        )

    if str(quality.get("status") or "") != "pass":
        tasks.append(
            make_task(
                title="解除 Radar battlecard 质量阻断项",
                trigger=f"quality_status={quality.get('status')} blockers={quality.get('blockers')}",
                owner_agent="codex",
                priority="P0",
                allowed_write_paths=["scripts/", "config/", "docs/"],
                context_paths=["output/reports/radar_report_quality_latest.json", "output/reports/radar_daily_report_latest.md"],
                validation_commands=["python3 scripts/check_radar_report_quality.py"],
                repair_commands=["python3 scripts/build_radar_workspace_outputs.py"],
                agent_prompt="This is the top-level quality blocker. Fix the underlying blocker first, rebuild the workspace, then require the quality gate to pass.",
                dedupe_key="quality_gate:battlecard",
            )
        )

    unique: dict[str, dict[str, Any]] = {}
    for task in tasks:
        unique[str(task["task_id"])] = task
    previous_payload = read_json(DEFAULT_OUTPUT_JSON)
    return merge_previous_task_state(list(unique.values()), previous_payload)


def queue_status(tasks: list[dict[str, Any]]) -> str:
    active_tasks = [task for task in tasks if str(task.get("status") or "") not in TERMINAL_STATUSES]
    if not active_tasks:
        return "pass"
    if any(str(task.get("priority") or "") in {"P0", "P1"} for task in active_tasks):
        return "action_required"
    return "warn"


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        "codex_output: true",
        'codex_output_category: "radar_agent_task_queue"',
        'codex_output_entity: "radar_workspace"',
        'codex_output_title: "Radar Agent Task Queue"',
        "---",
        "",
        "# Radar Agent Task Queue",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 待执行任务：`{payload.get('ready_count')}`",
        f"- 已认领任务：`{payload.get('claimed_count')}`",
        "",
    ]
    for task in payload.get("tasks") or []:
        lines.extend(
            [
                f"## {task.get('priority')} {task.get('title')}",
                "",
                f"- task_id：`{task.get('task_id')}`",
                f"- status：`{task.get('status')}`",
                f"- owner_agent：`{task.get('owner_agent')}`",
                f"- trigger：{task.get('trigger')}",
                f"- repair：`{' && '.join(task.get('repair_commands') or []) or 'manual_agent_fix_required'}`",
                f"- validation：`{' && '.join(task.get('validation_commands') or [])}`",
                "",
            ]
        )
    if not payload.get("tasks"):
        lines.append("- 暂无待派发 Agent 任务。")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args(sys.argv[1:])
    if args.run_ready:
        payload = run_ready_tasks(
            agent=args.agent,
            priorities=auto_priority_set(args.auto_priorities),
            max_tasks=args.max_tasks,
            validation_timeout_seconds=args.validation_timeout_seconds,
            lease_minutes=args.lease_minutes,
        )
        print(
            json.dumps(
                {
                    "status": payload.get("status"),
                    "action": "run_ready",
                    "executed_count": len((payload.get("auto_worker") or {}).get("results") or []),
                    "output_json": str(DEFAULT_OUTPUT_JSON),
                },
                ensure_ascii=False,
            )
        )
        return ACTION_SUCCESS
    if args.complete:
        with locked_queue_state():
            payload = read_json(DEFAULT_OUTPUT_JSON)
            if not payload:
                payload = queue_payload(build_tasks())
            existing_tasks = [item for item in (payload.get("tasks") or []) if isinstance(item, dict)]
            reclaim_expired_leases(existing_tasks)
            payload["tasks"] = existing_tasks
            completion_context = prepare_complete_task(
                payload,
                args.complete,
                agent=args.agent,
                validation_timeout_seconds=args.validation_timeout_seconds,
            )
            payload = queue_payload([item for item in (payload.get("tasks") or []) if isinstance(item, dict)])
            write_payload(payload)
        repair_commands = [str(item) for item in (completion_context.get("repair_commands") or []) if str(item).strip()]
        repair_results = (
            run_validation_commands(repair_commands, timeout_seconds=args.validation_timeout_seconds)
            if args.run_repair_commands and args.review_verdict == "pass"
            else []
        )
        repair_ok = all(str(item.get("status") or "") == "pass" for item in repair_results) if repair_results else True
        commands = [str(item) for item in (completion_context.get("validation_commands") or []) if str(item).strip()]
        validation_results = run_validation_commands(commands, timeout_seconds=args.validation_timeout_seconds) if args.review_verdict == "pass" and repair_ok else []
        with locked_queue_state():
            payload = read_json(DEFAULT_OUTPUT_JSON)
            if not payload:
                payload = queue_payload(build_tasks())
            existing_tasks = [item for item in (payload.get("tasks") or []) if isinstance(item, dict)]
            reclaim_expired_leases(existing_tasks, protected_task_id=args.complete, protected_agent=args.agent)
            payload["tasks"] = existing_tasks
            payload = apply_complete_task(
                payload,
                completion_context=completion_context,
                review_verdict=args.review_verdict,
                review_notes=args.review_notes,
                repair_results=repair_results,
                validation_results=validation_results,
            )
            action_exit_code = int(payload.pop("_action_exit_code", ACTION_SUCCESS) or ACTION_SUCCESS)
            payload = queue_payload([item for item in (payload.get("tasks") or []) if isinstance(item, dict)])
            write_payload(payload)
            print(json.dumps({"status": payload["status"], "action": "complete", "output_json": str(DEFAULT_OUTPUT_JSON)}, ensure_ascii=False))
            return action_exit_code

    with locked_queue_state():
        if not any((args.claim, args.start, args.complete, args.block, args.release)):
            tasks = build_tasks()
            payload = queue_payload(tasks)
            write_payload(payload)
            print(json.dumps({"status": payload["status"], "task_count": len(tasks), "output_json": str(DEFAULT_OUTPUT_JSON)}, ensure_ascii=False))
            return ACTION_SUCCESS

        payload = read_json(DEFAULT_OUTPUT_JSON)
        if not payload:
            payload = queue_payload(build_tasks())
        existing_tasks = [item for item in (payload.get("tasks") or []) if isinstance(item, dict)]
        reclaim_expired_leases(existing_tasks)
        payload["tasks"] = existing_tasks
        if args.claim:
            payload = claim_task(payload, args.claim, agent=args.agent, lease_minutes=args.lease_minutes)
            action = "claim"
        elif args.start:
            payload = start_task(payload, args.start, agent=args.agent)
            action = "start"
        elif args.block:
            payload = block_task(payload, args.block, reason=args.reason)
            action = "block"
        elif args.release:
            payload = release_task(payload, args.release, reason=args.reason)
            action = "release"
        else:
            raise SystemExit("no action")
        action_exit_code = int(payload.pop("_action_exit_code", ACTION_SUCCESS) or ACTION_SUCCESS)
        payload = queue_payload([item for item in (payload.get("tasks") or []) if isinstance(item, dict)])
        write_payload(payload)
        print(json.dumps({"status": payload["status"], "action": action, "output_json": str(DEFAULT_OUTPUT_JSON)}, ensure_ascii=False))
        return action_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
