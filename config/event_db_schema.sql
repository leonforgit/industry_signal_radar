PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS radar_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    run_label TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    host TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS source_registry_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at TEXT NOT NULL,
    manifest_version INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    primary_category TEXT NOT NULL,
    integration_status TEXT NOT NULL,
    priority TEXT NOT NULL,
    trust_tier TEXT,
    weight_tier TEXT,
    scheduler_class TEXT,
    access_mode TEXT,
    collector_owner TEXT,
    locator TEXT,
    raw_source_json TEXT NOT NULL,
    UNIQUE(snapshot_at, source_id)
);

CREATE TABLE IF NOT EXISTS signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    run_id TEXT REFERENCES radar_runs(run_id) ON DELETE SET NULL,
    snapshot_at TEXT NOT NULL,
    industry_id TEXT NOT NULL,
    industry_label TEXT,
    industry_state TEXT,
    total_score REAL,
    money_flow_score REAL,
    news_score REAL,
    fundamental_score REAL,
    policy_score REAL,
    signal_summary_json TEXT,
    evidence_json TEXT,
    source_ids_json TEXT,
    raw_snapshot_json TEXT
);

CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL UNIQUE,
    run_id TEXT REFERENCES radar_runs(run_id) ON DELETE SET NULL,
    snapshot_id TEXT REFERENCES signal_snapshots(snapshot_id) ON DELETE SET NULL,
    industry_id TEXT NOT NULL,
    industry_label TEXT,
    alert_level TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    total_score REAL,
    title TEXT,
    body TEXT,
    bark_group TEXT,
    bark_payload_json TEXT,
    evidence_json TEXT,
    source_ids_json TEXT,
    suppress_until TEXT,
    escalated_from TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS dispatch_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL REFERENCES alert_events(alert_id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    target_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    response_summary_json TEXT,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS source_health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES radar_runs(run_id) ON DELETE SET NULL,
    source_id TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL,
    lag_seconds INTEGER,
    fetched_count INTEGER,
    inserted_count INTEGER,
    error_count INTEGER,
    note TEXT,
    raw_health_json TEXT
);

CREATE TABLE IF NOT EXISTS runtime_health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES radar_runs(run_id) ON DELETE SET NULL,
    recorded_at TEXT NOT NULL,
    health_key TEXT NOT NULL,
    status TEXT NOT NULL,
    metric_value REAL,
    detail_text TEXT,
    raw_payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_radar_runs_started_at
    ON radar_runs (started_at);

CREATE INDEX IF NOT EXISTS idx_source_registry_snapshots_source_id
    ON source_registry_snapshots (source_id, snapshot_at);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_industry_snapshot_at
    ON signal_snapshots (industry_id, snapshot_at);

CREATE INDEX IF NOT EXISTS idx_alert_events_industry_created_at
    ON alert_events (industry_id, created_at);

CREATE INDEX IF NOT EXISTS idx_alert_events_dedup_key
    ON alert_events (dedup_key, created_at);

CREATE INDEX IF NOT EXISTS idx_dispatch_attempts_alert_id
    ON dispatch_attempts (alert_id, attempted_at);

CREATE INDEX IF NOT EXISTS idx_source_health_checks_source_checked_at
    ON source_health_checks (source_id, checked_at);

CREATE INDEX IF NOT EXISTS idx_runtime_health_events_key_recorded_at
    ON runtime_health_events (health_key, recorded_at);
