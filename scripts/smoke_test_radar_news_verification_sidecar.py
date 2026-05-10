#!/usr/bin/env python3
"""Smoke-test Radar news verification fallback to snapshot supporting events."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_company(name: str, code: str, index: int) -> dict:
    return {
        "radar_object_id": f"company:{name}",
        "radar_object_name": name,
        "radar_object_type": "company",
        "triage_action": "immediate_research",
        "radar_bucket": "strong_alert",
        "radar_score": 100 - index,
        "confidence": 100,
        "evidence_quality": "structured_confirmed",
        "primary_symbols": [f"{code}.SZ"],
        "supporting_events": [
            {
                "source": "news_event_hub.research_feed_latest",
                "event_id": f"evt_smoke_{index}",
                "title": f"{name}：第一季度净利润同比增长{100 + index}%",
                "headline": f"{name}：第一季度净利润同比增长{100 + index}%",
                "summary": f"{name}：第一季度净利润同比增长{100 + index}%",
                "published_at": "2026-05-04T08:00:00+00:00",
            }
        ],
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        snapshot_path = tmp_root / "snapshot.json"
        enrichment_path = tmp_root / "missing_enrichment.json"
        output_path = tmp_root / "news_verification.json"
        md_output_path = tmp_root / "news_verification.md"
        config_path = tmp_root / "runtime_defaults.json"
        companies = [
            build_company("甲信电子", "000100", 1),
            build_company("乙微设备", "688012", 2),
            build_company("丙晶科技", "003026", 3),
            build_company("丁安医疗", "002432", 4),
            build_company("戊测科技", "688372", 5),
            build_company("己音电子", "301329", 6),
        ]
        write_json(
            snapshot_path,
            {
                "run_id": "smoke-news-verification",
                "event_window_end_date": "2026-05-04",
                "market_sample_date": "2026-04-30",
                "objects": companies,
            },
        )
        write_json(
            config_path,
            {
                "deployment": {},
                "news_verification_sidecar": {
                    "top_company_limit": 6,
                    "max_live_discovery_objects": 0,
                    "live_discovery_threshold_events": 2,
                    "live_discovery_threshold_articles": 4,
                    "local_script_path": str(tmp_root / "missing_run_company_discovery.py"),
                    "remote_script_path": str(tmp_root / "missing_remote_run_company_discovery.py"),
                    "triage_allowlist": ["immediate_research"],
                },
            },
        )
        command = [
            sys.executable,
            str(ROOT / "scripts" / "build_radar_news_verification_sidecar.py"),
            "--config",
            str(config_path),
            "--input-snapshot",
            str(snapshot_path),
            "--input-company-enrichment",
            str(enrichment_path),
            "--json-output",
            str(output_path),
            "--md-output",
            str(md_output_path),
        ]
        subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert payload.get("status") == "pass", payload
        assert payload.get("verified_count") == 6, payload
        for item in payload.get("items") or []:
            assert item.get("status") == "pass", item
            assert item.get("lane") == "snapshot_supporting_events_discovery_fallback", item
            assert (item.get("coverage_after") or {}).get("snapshot_supporting_events") == 1, item
            assert (item.get("diagnostics") or {}).get("live_discovery_budget_expanded") is True, item
    print("radar_news_verification_sidecar_smoke_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
