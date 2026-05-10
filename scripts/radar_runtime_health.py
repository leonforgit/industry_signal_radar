from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from radar_config import load_config_section
from radar_event_db import DEFAULT_SCHEMA_PATH, apply_schema, configured_event_db_path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def configured_health_output_path() -> Path:
    runtime_paths = load_config_section(None, "runtime_paths")
    health_dir = runtime_paths.get("health_dir")
    base = Path(str(health_dir)) if health_dir else Path.cwd() / "health"
    return base / "runtime_health_latest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record runtime health for the industry signal radar.")
    parser.add_argument("--db", type=Path, default=configured_event_db_path(), help="SQLite database path.")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH, help="SQL schema file path.")
    parser.add_argument(
        "--health-output",
        type=Path,
        default=configured_health_output_path(),
        help="Latest runtime health JSON output path.",
    )
    parser.add_argument("--run-id", default=None, help="Optional radar run identifier.")
    parser.add_argument("--health-key", required=True, help="Logical health check key, for example bark_dispatch or source_fetch.")
    parser.add_argument("--status", required=True, help="Health status, for example pass, warn or fail.")
    parser.add_argument("--metric-value", type=float, default=None, help="Optional numeric metric value.")
    parser.add_argument("--detail", default="", help="Optional health detail text.")
    parser.add_argument("--source-id", default=None, help="Optional source identifier for source-level health recording.")
    parser.add_argument("--lag-seconds", type=int, default=None, help="Optional source lag in seconds.")
    parser.add_argument("--fetched-count", type=int, default=None, help="Optional fetched row count.")
    parser.add_argument("--inserted-count", type=int, default=None, help="Optional inserted row count.")
    parser.add_argument("--error-count", type=int, default=None, help="Optional error count.")
    parser.add_argument("--note", default="", help="Optional source health note.")
    return parser.parse_args()


def ensure_event_db(connection: sqlite3.Connection, schema_path: Path) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    apply_schema(connection, schema_path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_runtime_health(
    connection: sqlite3.Connection,
    run_id: str | None,
    recorded_at: str,
    health_key: str,
    status: str,
    metric_value: float | None,
    detail_text: str,
) -> None:
    connection.execute(
        """
        INSERT INTO runtime_health_events (
            run_id, recorded_at, health_key, status, metric_value, detail_text, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            recorded_at,
            health_key,
            status,
            metric_value,
            detail_text,
            json.dumps(
                {
                    "health_key": health_key,
                    "status": status,
                    "metric_value": metric_value,
                    "detail_text": detail_text,
                },
                ensure_ascii=False,
            ),
        ),
    )


def record_source_health(
    connection: sqlite3.Connection,
    run_id: str | None,
    recorded_at: str,
    source_id: str,
    status: str,
    lag_seconds: int | None,
    fetched_count: int | None,
    inserted_count: int | None,
    error_count: int | None,
    note: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_health_checks (
            run_id, source_id, checked_at, status, lag_seconds,
            fetched_count, inserted_count, error_count, note, raw_health_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            source_id,
            recorded_at,
            status,
            lag_seconds,
            fetched_count,
            inserted_count,
            error_count,
            note,
            json.dumps(
                {
                    "source_id": source_id,
                    "status": status,
                    "lag_seconds": lag_seconds,
                    "fetched_count": fetched_count,
                    "inserted_count": inserted_count,
                    "error_count": error_count,
                    "note": note,
                },
                ensure_ascii=False,
            ),
        ),
    )


def build_summary(args: argparse.Namespace, recorded_at: str, db_path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "recorded_at": recorded_at,
        "db_path": str(db_path),
        "run_id": args.run_id,
        "health_key": args.health_key,
        "status": args.status,
        "metric_value": args.metric_value,
        "detail_text": args.detail,
    }
    if args.source_id:
        payload["source_health"] = {
            "source_id": args.source_id,
            "lag_seconds": args.lag_seconds,
            "fetched_count": args.fetched_count,
            "inserted_count": args.inserted_count,
            "error_count": args.error_count,
            "note": args.note,
        }
    return payload


def main() -> None:
    args = parse_args()
    recorded_at = utc_now_iso()
    args.db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(args.db)
    ensure_event_db(connection, args.schema)
    record_runtime_health(
        connection=connection,
        run_id=args.run_id,
        recorded_at=recorded_at,
        health_key=args.health_key,
        status=args.status,
        metric_value=args.metric_value,
        detail_text=args.detail,
    )
    if args.source_id:
        record_source_health(
            connection=connection,
            run_id=args.run_id,
            recorded_at=recorded_at,
            source_id=args.source_id,
            status=args.status,
            lag_seconds=args.lag_seconds,
            fetched_count=args.fetched_count,
            inserted_count=args.inserted_count,
            error_count=args.error_count,
            note=args.note,
        )
    connection.commit()
    connection.close()

    summary = build_summary(args, recorded_at, args.db)
    write_json(args.health_output, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
