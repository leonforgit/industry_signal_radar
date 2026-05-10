from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import time
import traceback

from radar_scan_runner import run_scan


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_boot_id() -> str:
    boot_path = Path("/proc/sys/kernel/random/boot_id")
    if not boot_path.exists():
        return ""
    return boot_path.read_text(encoding="utf-8").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a detached one-shot validation for Industry Signal Radar.")
    parser.add_argument("--validation-id", required=True, help="Stable validation identifier.")
    parser.add_argument("--run-at", required=True, help="ISO 8601 timestamp override passed to radar_scan_runner.")
    parser.add_argument("--status-file", type=Path, required=True, help="JSON status file output path.")
    parser.add_argument("--log-file", type=Path, required=True, help="Validation log output path.")
    parser.add_argument("--runtime-root-override", type=Path, default=None, help="Optional runtime root override.")
    parser.add_argument("--lock-path", type=Path, default=None, help="Optional shared run lock path.")
    parser.add_argument("--lock-timeout-seconds", type=int, default=180, help="Seconds to wait for the shared run lock.")
    parser.add_argument("--skip-bark", action="store_true", help="Skip Bark delivery while validating.")
    return parser.parse_args()


def write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_runner_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        check_only=False,
        bootstrap_only=False,
        skip_bark=args.skip_bark,
        run_at=args.run_at,
        runtime_root_override=args.runtime_root_override,
    )


def acquire_lock(lock_path: Path, timeout_seconds: int):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    deadline = time.time() + max(timeout_seconds, 0)
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{utc_now_iso()}\n")
            handle.flush()
            return handle
        except BlockingIOError:
            if time.time() >= deadline:
                handle.close()
                raise TimeoutError(f"timed out waiting for lock: {lock_path}")
            time.sleep(1)


def main() -> None:
    args = parse_args()
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    start_payload = {
        "validation_id": args.validation_id,
        "run_at": args.run_at,
        "started_at": utc_now_iso(),
        "status": "running",
        "log_path": str(args.log_file),
        "boot_id": read_boot_id(),
    }
    write_status(args.status_file, start_payload)

    with args.log_file.open("w", encoding="utf-8") as handle, redirect_stdout(handle), redirect_stderr(handle):
        print(json.dumps(start_payload, ensure_ascii=False))
        lock_handle = None
        try:
            if args.lock_path is not None:
                print(
                    json.dumps(
                        {
                            "validation_id": args.validation_id,
                            "lock_path": str(args.lock_path),
                            "lock_timeout_seconds": args.lock_timeout_seconds,
                            "lock_status": "waiting",
                        },
                        ensure_ascii=False,
                    )
                )
                lock_handle = acquire_lock(args.lock_path, args.lock_timeout_seconds)
                print(
                    json.dumps(
                        {
                            "validation_id": args.validation_id,
                            "lock_path": str(args.lock_path),
                            "lock_status": "acquired",
                        },
                        ensure_ascii=False,
                    )
                )
            payload = run_scan(build_runner_args(args))
            success_payload = {
                "validation_id": args.validation_id,
                "run_at": args.run_at,
                "completed_at": utc_now_iso(),
                "status": "success",
                "return_code": 0,
                "log_path": str(args.log_file),
                "target_run_id": str(payload.get("run_id", "")),
                "industry_count": int(payload.get("industry_count", 0)),
                "alert_count": int(payload.get("alert_count", 0)),
                "execution_mode": str(payload.get("execution_mode", "")),
                "boot_id": read_boot_id(),
            }
            write_status(args.status_file, success_payload)
            print(json.dumps(success_payload, ensure_ascii=False))
        except BaseException as exc:  # noqa: BLE001
            exit_code = 1
            status = "failed"
            if isinstance(exc, SystemExit):
                if isinstance(exc.code, int):
                    exit_code = int(exc.code)
                elif exc.code is None:
                    exit_code = 0
                status = "system_exit"
            error_payload = {
                "validation_id": args.validation_id,
                "run_at": args.run_at,
                "completed_at": utc_now_iso(),
                "status": status,
                "return_code": exit_code,
                "log_path": str(args.log_file),
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "boot_id": read_boot_id(),
            }
            write_status(args.status_file, error_payload)
            print(json.dumps(error_payload, ensure_ascii=False))
            raise
        finally:
            if lock_handle is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()


if __name__ == "__main__":
    main()
