from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from radar_config import load_config_section, load_source_manifest, load_manifest_sources


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = ROOT / "config" / "event_db_schema.sql"


def configured_event_db_path() -> Path:
    runtime_paths = load_config_section(None, "runtime_paths")
    raw = runtime_paths.get("event_db_path")
    return Path(str(raw)) if raw else ROOT / "output" / "industry_signal_radar.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the industry signal radar event database.")
    parser.add_argument("--db", type=Path, default=configured_event_db_path(), help="SQLite database path.")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH, help="SQL schema file path.")
    parser.add_argument(
        "--snapshot-at",
        default=None,
        help="UTC timestamp for the source registry snapshot. Defaults to now.",
    )
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply_schema(connection: sqlite3.Connection, schema_path: Path) -> None:
    with schema_path.open("r", encoding="utf-8") as handle:
        connection.executescript(handle.read())


def set_schema_meta(connection: sqlite3.Connection, key: str, value: str, updated_at: str) -> None:
    connection.execute(
        """
        INSERT INTO schema_meta (meta_key, meta_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET
            meta_value=excluded.meta_value,
            updated_at=excluded.updated_at
        """,
        (key, value, updated_at),
    )


def snapshot_source_registry(connection: sqlite3.Connection, snapshot_at: str, manifest: dict[str, Any]) -> int:
    sources = load_manifest_sources()
    manifest_version = int(manifest.get("manifest_version", 1))
    inserted = 0
    for source in sources:
        connection.execute(
            """
            INSERT OR REPLACE INTO source_registry_snapshots (
                snapshot_at, manifest_version, source_id, primary_category,
                integration_status, priority, trust_tier, weight_tier,
                scheduler_class, access_mode, collector_owner, locator,
                raw_source_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_at,
                manifest_version,
                str(source.get("source_id", "")),
                str(source.get("primary_category", "")),
                str(source.get("integration_status", "")),
                str(source.get("priority", "")),
                str(source.get("trust_tier", "")),
                str(source.get("weight_tier", "")),
                str(source.get("scheduler_class", "")),
                str(source.get("access_mode", "")),
                str(source.get("collector_owner", "")),
                str(source.get("locator", "")),
                json.dumps(source, ensure_ascii=False, sort_keys=True),
            ),
        )
        inserted += 1
    return inserted


def initialize_event_db(db_path: Path, schema_path: Path, snapshot_at: str) -> dict[str, Any]:
    manifest = load_source_manifest()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    last_exc: sqlite3.OperationalError | None = None
    source_count = 0
    for attempt in range(6):
        connection = sqlite3.connect(db_path, timeout=30)
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            apply_schema(connection, schema_path)

            set_schema_meta(connection, "schema_name", "industry_signal_radar_event_db", snapshot_at)
            set_schema_meta(connection, "schema_version", "1", snapshot_at)
            set_schema_meta(connection, "source_manifest_name", str(manifest.get("manifest_name", "")), snapshot_at)
            set_schema_meta(connection, "source_manifest_version", str(manifest.get("manifest_version", 1)), snapshot_at)

            source_count = snapshot_source_registry(connection, snapshot_at, manifest)
            connection.commit()
            last_exc = None
            break
        except sqlite3.OperationalError as exc:
            connection.rollback()
            if "database is locked" not in str(exc).lower():
                connection.close()
                raise
            last_exc = exc
            connection.close()
            if attempt < 5:
                time.sleep(5)
                continue
        else:
            connection.close()
            break
        finally:
            try:
                connection.close()
            except Exception:
                pass
    if last_exc is not None:
        raise last_exc

    return {
        "db_path": str(db_path),
        "schema_path": str(schema_path),
        "snapshot_at": snapshot_at,
        "manifest_name": manifest.get("manifest_name", ""),
        "manifest_version": manifest.get("manifest_version", 1),
        "source_count": source_count,
    }


def main() -> None:
    args = parse_args()
    snapshot_at = args.snapshot_at or utc_now_iso()
    summary = initialize_event_db(args.db, args.schema, snapshot_at)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
