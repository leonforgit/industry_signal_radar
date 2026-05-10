#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from build_radar_bark_summary import build_bark_summary
from radar_scan_runner import process_alerts


ROOT = Path(__file__).resolve().parent.parent


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "config" / "event_db_schema.sql").read_text(encoding="utf-8"))
    return conn


def seed_run(conn: sqlite3.Connection, run_id: str, run_at: str) -> None:
    conn.execute(
        """
        INSERT INTO radar_runs (run_id, run_label, mode, started_at, status, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, run_id, "smoke", run_at, "running", "smoke"),
    )


def seed_snapshot(conn: sqlite3.Connection, run_id: str, run_at: str, industry_id: str, industry_label: str) -> None:
    snapshot_id = f"{industry_id}:{run_at}"
    conn.execute(
        """
        INSERT INTO signal_snapshots (
            snapshot_id, run_id, snapshot_at, industry_id, industry_label, industry_state,
            total_score, money_flow_score, news_score, fundamental_score, policy_score,
            signal_summary_json, evidence_json, source_ids_json, raw_snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            run_id,
            run_at,
            industry_id,
            industry_label,
            "strong_alert",
            0.92,
            0.88,
            0.95,
            0.87,
            0.95,
            "{}",
            "{}",
            "[]",
            "{}",
        ),
    )


def main() -> None:
    conn = build_conn()
    run_dt = datetime(2026, 4, 12, 9, 0, tzinfo=timezone.utc)
    run_at = run_dt.isoformat(timespec="seconds")
    run_id = f"scan:{run_at}"
    seed_run(conn, run_id, run_at)
    seed_snapshot(conn, run_id, run_at, "sw_l1_801170", "交通运输")
    seed_snapshot(conn, run_id, run_at, "sw_l1_801790", "非银金融")
    row = {
        "industry_id": "sw_l1_801170",
        "display_name_cn": "交通运输",
        "industry_state": "strong_alert",
        "total_score": 0.92,
        "money_flow_score": 0.88,
        "heat_score": 0.91,
        "fundamental_score": 0.87,
        "policy_score": 0.95,
        "announcement_score": 0.80,
        "aux_flow_score": 0.80,
        "fundamental_proxy_score": 0.72,
        "policy_articles": [{"title": "运价线索抬升", "source_id": "shared:news_event_hub", "event_id": "evt_ship"}],
        "announcement_events": [{"title": "订单扩张", "signal_tags": ["订单"]}],
        "fundamental_proxy_evidence": {"summary_cn": "BDI + 航运代理改善"},
        "overlay_names": ["shipping_chain"],
        "representative_stock_name": "招商轮船",
        "representative_stock_code": "601872",
        "etf_proxy_name": "物流ETF",
        "etf_proxy_code": "516910",
        "rank_desc": 1,
    }
    risk_row = {
        "industry_id": "sw_l1_801790",
        "display_name_cn": "非银金融",
        "industry_state": "strong_alert",
        "total_score": 0.90,
        "money_flow_score": 0.82,
        "heat_score": 0.90,
        "fundamental_score": 0.50,
        "policy_score": 0.90,
        "announcement_score": 0.70,
        "aux_flow_score": 0.60,
        "fundamental_proxy_score": 0.0,
        "policy_articles": [{"title": "中泰证券：市场重心已明显从“外部情绪博弈”转向“内部基本面修复”", "source_id": "shared:news_event_hub", "event_id": "evt_fin"}],
        "announcement_events": [{"title": "某券商：风险警示事项进展公告", "signal_tags": ["风险警示"]}],
        "fundamental_proxy_evidence": {},
        "overlay_names": ["broker_chain"],
        "representative_stock_name": "东方财富",
        "representative_stock_code": "300059",
        "etf_proxy_name": "证券保险ETF",
        "etf_proxy_code": "512070",
        "rank_desc": 2,
    }
    alerts = process_alerts(
        conn,
        run_dt,
        [row, risk_row],
        {"run_id": run_id},
        skip_bark=True,
        openbb_context=None,
    )
    assert_true(len(alerts) == 1, "strong_alert row should produce one live Bark candidate")
    alert_row = conn.execute("SELECT alert_level, title, body FROM alert_events").fetchone()
    dispatch_row = conn.execute("SELECT status, target_count FROM dispatch_attempts").fetchone()
    assert_true(alert_row is not None and alert_row["alert_level"] == "strong_alert", "alert_events should record strong_alert level")
    assert_true(alert_row is not None and "Radar | 交通运输" in alert_row["title"], "alert title should come from snapshot-driven Bark trigger")
    assert_true(dispatch_row is not None and dispatch_row["status"] == "skipped_by_flag", "dispatch_attempts should record skipped_by_flag in smoke mode")
    risk_alert = conn.execute("SELECT COUNT(*) AS cnt FROM alert_events WHERE industry_id = ?", ("sw_l1_801790",)).fetchone()
    assert_true(risk_alert is not None and int(risk_alert["cnt"]) == 0, "risk-monitor style industry row should be suppressed by snapshot-driven Bark gate")
    bark_summary = build_bark_summary(
        {
            "generated_at": run_at,
            "run_id": run_id,
            "radar_run_id": run_id,
            "as_of_date": "2026-04-12",
            "objects": [
                {
                    "dedup_key": "ok",
                    "radar_object_type": "industry",
                    "radar_object_id": "sw_l1_801170",
                    "radar_object_name": "交通运输",
                    "runtime_state": "strong_alert",
                    "radar_bucket": "strong_alert",
                    "alert_level": "strong_alert",
                    "trigger_state": "bark_candidate",
                    "why_now_strength": 72,
                    "confidence": 68,
                    "followup_value": 75,
                    "event_driven_lane": "hard_catalyst_board",
                    "hard_or_soft": "hard",
                    "confirmation_gap": "当前仍缺价格扩散确认。",
                    "hedge_difficulty": "medium",
                    "failure_mode": "若运价不能持续，事件将回落。",
                    "evidence_quality": "proxy_confirmed",
                    "triage_action": "immediate_research",
                    "is_new": True,
                    "is_upgraded": False,
                    "why_now": "运价与代理变量同步走强。",
                    "key_evidence": ["BDI 改善"],
                    "followup_path": ["确认航运链价格是否扩散"],
                },
                {
                    "dedup_key": "watch",
                    "radar_object_type": "industry",
                    "radar_object_id": "sw_l1_801880",
                    "radar_object_name": "汽车",
                    "runtime_state": "strong_alert",
                    "radar_bucket": "strong_alert",
                    "alert_level": "strong_alert",
                    "trigger_state": "bark_candidate",
                    "why_now_strength": 70,
                    "confidence": 62,
                    "followup_value": 70,
                    "event_driven_lane": "soft_catalyst_watchlist",
                    "hard_or_soft": "soft",
                    "confirmation_gap": "当前仍缺订单与公告确认。",
                    "hedge_difficulty": "medium",
                    "failure_mode": "若主题没有扩散，容易回落。",
                    "evidence_quality": "early_thematic",
                    "triage_action": "thesis_watch",
                    "is_new": True,
                    "is_upgraded": False,
                    "why_now": "主题热度扩散，但仍缺硬确认。",
                    "key_evidence": ["主题扩散"],
                    "followup_path": ["继续跟订单与公告"],
                },
            ],
        }
    )
    assert_true(int(bark_summary["trigger_count"]) == 1, "only immediate_research strong-alert objects should stay eligible for Bark")
    print("snapshot_bark_dispatch_smoke_ok")


if __name__ == "__main__":
    main()
