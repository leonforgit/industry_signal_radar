from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from radar_config import load_config_section, load_source_manifest
from radar_event_db import DEFAULT_SCHEMA_PATH, initialize_event_db
from radar_runtime_health import ensure_event_db, record_runtime_health, write_json


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap the industry signal radar runtime safely.")
    parser.add_argument("--check-only", action="store_true", help="Validate paths and planned actions without writing files.")
    parser.add_argument("--run-at", default=None, help="ISO 8601 timestamp override. Defaults to current UTC time.")
    parser.add_argument(
        "--runtime-root-override",
        type=Path,
        default=None,
        help="Optional local override for runtime root, useful for dry runs and smoke tests.",
    )
    parser.add_argument("--skip-health", action="store_true", help="Skip writing runtime health after bootstrap.")
    return parser.parse_args()


def is_same_or_subpath(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return target == root


def resolve_runtime_paths(runtime_root_override: Path | None = None) -> dict[str, Path]:
    raw = load_config_section(None, "runtime_paths")
    if runtime_root_override is None:
        return {key: Path(str(value)) for key, value in raw.items()}

    root = runtime_root_override
    return {
        "runtime_root": root,
        "config_dir": root / "config",
        "state_dir": root / "state",
        "event_db_path": root / "state" / "industry_signal_radar.db",
        "log_dir": root / "logs",
        "cache_dir": root / "cache",
        "output_dir": root / "output",
        "health_dir": root / "health",
        "lock_dir": root / "locks",
    }


def validate_runtime_guardrails(runtime_paths: dict[str, Path]) -> dict[str, Any]:
    isolation = load_config_section(None, "isolation")
    deployment = load_config_section(None, "deployment")
    runtime_root = runtime_paths["runtime_root"]
    shared_paths = [Path(str(item)) for item in isolation.get("shared_read_only_paths", [])]
    forbidden_paths = [Path(str(item)) for item in isolation.get("forbidden_write_paths", [])]
    forbidden_units = {str(item) for item in isolation.get("forbidden_systemd_units", [])}

    errors: list[str] = []
    warnings: list[str] = []

    for name, path in runtime_paths.items():
        if name == "runtime_root":
            continue
        if not is_same_or_subpath(path, runtime_root):
            errors.append(f"{name} is outside runtime_root: {path}")

    for shared_path in shared_paths:
        if is_same_or_subpath(runtime_root, shared_path) or is_same_or_subpath(shared_path, runtime_root):
            errors.append(f"runtime_root overlaps shared read-only path: {shared_path}")

    for forbidden_path in forbidden_paths:
        for name, path in runtime_paths.items():
            if is_same_or_subpath(path, forbidden_path) or is_same_or_subpath(forbidden_path, path):
                errors.append(f"{name} overlaps forbidden write path: {forbidden_path}")

    service_name = str(deployment.get("service_name", ""))
    timer_name = str(deployment.get("timer_name", ""))
    if service_name in forbidden_units:
        errors.append(f"service_name conflicts with forbidden systemd unit: {service_name}")
    if timer_name in forbidden_units:
        errors.append(f"timer_name conflicts with forbidden systemd unit: {timer_name}")

    if not shared_paths:
        warnings.append("shared_read_only_paths is empty")
    if not forbidden_paths:
        warnings.append("forbidden_write_paths is empty")

    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "runtime_root": str(runtime_root),
    }


def materialize_runtime_dirs(runtime_paths: dict[str, Path]) -> list[str]:
    created: list[str] = []
    for key in ["runtime_root", "config_dir", "state_dir", "log_dir", "cache_dir", "output_dir", "health_dir", "lock_dir"]:
        path = runtime_paths[key]
        path.mkdir(parents=True, exist_ok=True)
        created.append(str(path))
    return created


def build_summary(
    run_at: str,
    check_only: bool,
    runtime_paths: dict[str, Path],
    guardrails: dict[str, Any],
    source_manifest: dict[str, Any],
    created_paths: list[str],
) -> dict[str, Any]:
    return {
        "run_at": run_at,
        "check_only": check_only,
        "runtime_root": str(runtime_paths["runtime_root"]),
        "event_db_path": str(runtime_paths["event_db_path"]),
        "output_dir": str(runtime_paths["output_dir"]),
        "health_dir": str(runtime_paths["health_dir"]),
        "manifest_name": source_manifest.get("manifest_name", ""),
        "manifest_version": source_manifest.get("manifest_version", 1),
        "source_count": len(source_manifest.get("sources", [])) if isinstance(source_manifest.get("sources", []), list) else 0,
        "guardrails": guardrails,
        "created_paths": created_paths,
    }


def write_bootstrap_summary(runtime_paths: dict[str, Path], summary: dict[str, Any]) -> Path:
    output_path = runtime_paths["output_dir"] / "runtime_bootstrap_latest.json"
    write_json(output_path, summary)
    return output_path


def register_bootstrap_run(connection: sqlite3.Connection, run_id: str, run_at: str) -> None:
    connection.execute(
        """
        INSERT INTO radar_runs (
            run_id, run_label, mode, started_at, completed_at, status, host, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            completed_at=excluded.completed_at,
            status=excluded.status,
            note=excluded.note
        """,
        (
            run_id,
            "runtime_bootstrap",
            "bootstrap",
            run_at,
            run_at,
            "success",
            "remote-runner",
            "bootstrap runner completed",
        ),
    )


def record_bootstrap_health(runtime_paths: dict[str, Path], run_id: str, run_at: str, detail_text: str) -> None:
    connection = sqlite3.connect(runtime_paths["event_db_path"])
    ensure_event_db(connection, DEFAULT_SCHEMA_PATH)
    register_bootstrap_run(connection, run_id, run_at)
    record_runtime_health(
        connection=connection,
        run_id=run_id,
        recorded_at=run_at,
        health_key="runtime_bootstrap",
        status="pass",
        metric_value=1.0,
        detail_text=detail_text,
    )
    connection.commit()
    connection.close()


def main() -> None:
    args = parse_args()
    run_at = args.run_at or utc_now_iso()
    runtime_paths = resolve_runtime_paths(args.runtime_root_override)
    source_manifest = load_source_manifest()
    guardrails = validate_runtime_guardrails(runtime_paths)
    created_paths: list[str] = []

    if guardrails["status"] != "pass":
        print(json.dumps(build_summary(run_at, args.check_only, runtime_paths, guardrails, source_manifest, created_paths), ensure_ascii=False, indent=2))
        raise SystemExit(1)

    if not args.check_only:
        created_paths = materialize_runtime_dirs(runtime_paths)
        initialize_event_db(runtime_paths["event_db_path"], DEFAULT_SCHEMA_PATH, run_at)
        if not args.skip_health:
            record_bootstrap_health(
                runtime_paths=runtime_paths,
                run_id=f"bootstrap:{run_at}",
                run_at=run_at,
                detail_text="runtime bootstrap completed",
            )

    summary = build_summary(run_at, args.check_only, runtime_paths, guardrails, source_manifest, created_paths)
    if not args.check_only:
        summary["bootstrap_summary_path"] = str(write_bootstrap_summary(runtime_paths, summary))

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
