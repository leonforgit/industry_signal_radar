#!/usr/bin/env python3
"""Ensure Radar candidate companies have canonical fundamental coverage."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from radar_company_targets import extract_company_targets
from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_price_sidecar import resolve_repo_path
from radar_upstream_bridge import (
    build_scp_command,
    build_ssh_command,
    load_deployment_config,
    run_local_python,
    run_remote_python,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_fundamental_coverage_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_fundamental_coverage_latest.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--force-refresh", action="store_true")
    return parser.parse_args()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def copy_remote_file(*, host: str, ssh_options: str, remote_path: str, local_path: Path, timeout_seconds: int) -> tuple[bool, str]:
    command = build_scp_command(ssh_options, f"{host}:{remote_path}", str(local_path))
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"scp exited with {result.returncode}"
        return False, message
    return True, ""


def push_local_file_to_remote(*, host: str, ssh_options: str, local_path: Path, remote_path: str, timeout_seconds: int) -> tuple[bool, str]:
    mkdir_command = build_ssh_command(host, ssh_options, ["mkdir", "-p", str(Path(remote_path).parent)])
    mkdir_result = subprocess.run(
        mkdir_command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if mkdir_result.returncode != 0:
        message = mkdir_result.stderr.strip() or mkdir_result.stdout.strip() or f"mkdir exited with {mkdir_result.returncode}"
        return False, message
    command = build_scp_command(ssh_options, str(local_path), f"{host}:{remote_path}")
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"scp exited with {result.returncode}"
        return False, message
    return True, ""


def fetch_remote_text(*, host: str, ssh_options: str, remote_path: str, timeout_seconds: int) -> tuple[str, str]:
    command = build_ssh_command(host, ssh_options, ["cat", remote_path])
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"ssh cat exited with {result.returncode}"
        return "", message
    return result.stdout, ""


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_fundamental_coverage"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Fundamental Coverage {payload.get("status") or "unknown"}"',
        "---",
        "",
        "# Radar Fundamental Coverage",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 使用路径：`{payload.get('used_lane')}`",
        f"- 已解析公司：`{payload.get('resolved_count')}`",
        f"- 未解析公司：`{payload.get('unresolved_count')}`",
        f"- A股摘要 fallback：`{payload.get('summary_fallback_count')}`",
        "",
        "## Missing Required Statements",
        "",
    ]
    missing_targets = payload.get("missing_targets") or []
    if missing_targets:
        for item in missing_targets:
            lines.append(
                "- {name}: {items}".format(
                    name=str(item.get("radar_object_name") or ""),
                    items=" / ".join(str(x) for x in (item.get("missing_required") or [])) or "unknown",
                )
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## Unresolved Targets", ""])
    unresolved_targets = payload.get("unresolved_targets") or []
    if unresolved_targets:
        for item in unresolved_targets:
            lines.append(f"- {str(item.get('radar_object_name') or '')}")
    else:
        lines.append("- 无")
    lines.extend(["", "## Attempts", ""])
    attempts = payload.get("attempts") or []
    if attempts:
        for item in attempts:
            lines.append(
                "- {lane}: `{status}` | {note}".format(
                    lane=str(item.get("lane") or ""),
                    status=str(item.get("status") or ""),
                    note=str(item.get("note") or ""),
                )
            )
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def load_company_targets(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return extract_company_targets(payload)


def evaluate_bridge_payload(payload: dict[str, Any], unresolved_targets: list[dict[str, str]]) -> dict[str, Any]:
    results = payload.get("results") or []
    missing_targets = [
        {
            "radar_object_name": str(item.get("radar_object_name") or ""),
            "market_symbol": str(item.get("market_symbol") or ""),
            "missing_required": list(item.get("missing_required") or []),
        }
        for item in results
        if item.get("missing_required") and str(item.get("coverage_mode") or "") != "summary_fallback"
    ]
    resolved_count = int(payload.get("resolved_count") or len(results))
    status = "pass"
    if not resolved_count:
        status = "skip"
    elif missing_targets:
        status = "warn"
    return {
        "status": status,
        "resolved_count": resolved_count,
        "unresolved_count": int(payload.get("unresolved_count") or len(unresolved_targets)),
        "binding_failure_count": int(payload.get("binding_failure_count") or 0),
        "openbb_service_status": str(payload.get("openbb_service_status") or ""),
        "openbb_base_url": str(payload.get("openbb_base_url") or ""),
        "openbb_note": str(payload.get("openbb_note") or ""),
        "summary_fallback_count": int(payload.get("summary_fallback_count") or 0),
        "results": results,
        "missing_targets": missing_targets,
        "unresolved_targets": unresolved_targets,
    }


def main() -> int:
    args = parse_args()
    bridge_cfg = load_config_section(args.config, "canonical_fundamental_bridge")
    refresh_cfg = load_config_section(args.config, "upstream_fundamental_refresh")
    deployment = load_deployment_config(args.config)
    resolved_targets, unresolved_targets = load_company_targets(args.input_candidate_pool)

    bridge_output_json = resolve_repo_path(
        bridge_cfg.get("output_path"),
        fallback=ROOT / "output" / "sidecars" / "fundamentals" / "company_fundamental_backfill_latest.json",
    )
    timeout_seconds = max(int(refresh_cfg.get("timeout_seconds") or 900), 60)
    attempts: list[dict[str, Any]] = []

    def build_payload(status: str, used_lane: str, bridge_payload: dict[str, Any] | None, note: str = "") -> dict[str, Any]:
        evaluated = evaluate_bridge_payload(bridge_payload or {}, unresolved_targets)
        return {
            "status": status if status else evaluated.get("status"),
            "used_lane": used_lane,
            "resolved_count": len(resolved_targets),
            "unresolved_count": len(unresolved_targets),
            "bridge_json_path": str(bridge_output_json),
            "attempts": attempts,
            "note": note,
            **evaluated,
        }

    def write_and_exit(payload: dict[str, Any], exit_code: int) -> int:
        write_json(args.json_output, payload)
        write_text(args.md_output, render_markdown(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exit_code

    if not resolved_targets:
        return write_and_exit(build_payload("skip", "no_company_targets", {}, note="candidate pool 中没有已解析公司对象"), 0)

    local_bridge_script = ROOT / "scripts" / "build_radar_canonical_fundamental_bridge.py"
    local_payload, local_error = run_local_python(
        local_bridge_script,
        [
            "--config",
            str(args.config),
            "--input-candidate-pool",
            str(args.input_candidate_pool),
            "--output",
            str(bridge_output_json),
            *(["--force-fetch"] if args.force_refresh else []),
        ],
        timeout_seconds=timeout_seconds,
    )
    if local_payload is not None:
        local_status = str(local_payload.get("status") or "pass")
        local_openbb_status = str(local_payload.get("openbb_service_status") or "")
        attempts.append(
            {
                "lane": "local_bridge",
                "status": local_status,
                "note": str(local_payload.get("openbb_note") or ""),
            }
        )
        can_accept_local = local_status == "pass" or local_openbb_status == "pass"
        if can_accept_local:
            payload = build_payload("", "local_bridge", local_payload)
            return write_and_exit(payload, 0 if payload.get("status") in {"pass", "warn", "skip"} else 1)
        attempts.append(
            {
                "lane": "local_bridge_gate",
                "status": "skip",
                "note": f"continue_to_remote because openbb_service_status={local_openbb_status or 'missing'}",
            }
        )
    else:
        attempts.append({"lane": "local_bridge", "status": "fail", "note": local_error})

    remote_script = str(refresh_cfg.get("remote_bridge_script") or "").strip()
    remote_json = str(refresh_cfg.get("remote_fundamental_bridge_json_path") or "").strip()
    remote_host = str(deployment.get("server_host") or "").strip()
    remote_root = str(deployment.get("remote_root") or "").strip().rstrip("/")
    remote_python = str(refresh_cfg.get("remote_python_bin") or f"{deployment.get('remote_venv')}/bin/python").strip()
    if remote_script and remote_host and remote_json and remote_root:
        remote_candidate_pool_path = str(
            refresh_cfg.get("remote_candidate_pool_path") or f"{remote_root}/output/snapshots/radar_candidate_pool_latest.json"
        )
        pushed, push_error = push_local_file_to_remote(
            host=remote_host,
            ssh_options=str(deployment.get("ssh_options") or ""),
            local_path=args.input_candidate_pool,
            remote_path=remote_candidate_pool_path,
            timeout_seconds=timeout_seconds,
        )
        attempts.append(
            {
                "lane": "sync_local_candidate_pool",
                "status": "pass" if pushed else "fail",
                "note": "" if pushed else push_error,
            }
        )
        remote_payload, remote_error = run_remote_python(
            host=remote_host,
            ssh_options=str(deployment.get("ssh_options") or ""),
            python_bin=remote_python,
            script_path=remote_script,
            script_args=[
                "--config",
                f"{remote_root}/config/runtime_defaults.json",
                "--input-candidate-pool",
                remote_candidate_pool_path,
                "--output",
                remote_json,
                *(["--force-fetch"] if args.force_refresh else []),
            ],
            timeout_seconds=timeout_seconds,
        )
        if remote_payload is not None:
            attempts.append({"lane": "remote_bridge", "status": str(remote_payload.get("status") or "pass"), "note": ""})
            bridge_output_json.parent.mkdir(parents=True, exist_ok=True)
            copied, copy_error = copy_remote_file(
                host=remote_host,
                ssh_options=str(deployment.get("ssh_options") or ""),
                remote_path=remote_json,
                local_path=bridge_output_json,
                timeout_seconds=timeout_seconds,
            )
            if not copied:
                remote_text, fetch_error = fetch_remote_text(
                    host=remote_host,
                    ssh_options=str(deployment.get("ssh_options") or ""),
                    remote_path=remote_json,
                    timeout_seconds=timeout_seconds,
                )
                if remote_text:
                    bridge_output_json.write_text(remote_text, encoding="utf-8")
                    attempts.append({"lane": "sync_remote_bridge_json", "status": "pass", "note": "ssh_cat_fallback"})
                else:
                    attempts.append({"lane": "sync_remote_bridge_json", "status": "fail", "note": copy_error or fetch_error})
            else:
                attempts.append({"lane": "sync_remote_bridge_json", "status": "pass", "note": ""})
            payload = build_payload("", "remote_bridge", remote_payload)
            return write_and_exit(payload, 0 if payload.get("status") in {"pass", "warn", "skip"} else 1)
        attempts.append({"lane": "remote_bridge", "status": "fail", "note": remote_error})

    return write_and_exit(build_payload("fail", "bridge_unavailable", {}, note="local/remote fundamental bridge 都不可用"), 1)


if __name__ == "__main__":
    raise SystemExit(main())
