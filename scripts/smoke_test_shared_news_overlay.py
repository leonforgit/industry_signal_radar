#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from radar_shared_news import build_shared_news_policy_overlay


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sample_registry() -> list[dict[str, str]]:
    return [
        {
            "industry_id": "sw_l1_801080",
            "display_name_cn": "电子",
            "heat_keywords": "电子;半导体;消费电子",
        },
        {
            "industry_id": "sw_l1_801170",
            "display_name_cn": "交通运输",
            "heat_keywords": "交通运输;物流;航运",
        },
        {
            "industry_id": "sw_l1_801960",
            "display_name_cn": "石油石化",
            "heat_keywords": "石油石化;油气;炼化",
        },
    ]


def main() -> None:
    registry = sample_registry()
    run_dt = datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc)

    with TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        feed_path = root / "industry_radar_feed_latest.json"
        health_path = root / "source_health_latest.json"
        feed_path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-04-07T08:30:00+00:00",
                    "industries": [
                        {
                            "industry": "半导体",
                            "shared_news_score": 0.74,
                            "policy_articles": [
                                {
                                    "event_id": "evt_chip",
                                    "title": "台积电加码先进封装",
                                    "published_at": "2026-04-07T08:20:00+00:00",
                                    "source_count": 2,
                                    "score": 74.0,
                                    "event_type": "production_supply",
                                }
                            ],
                        },
                        {
                            "industry": "航运",
                            "shared_news_score": 0.41,
                            "policy_articles": [
                                {
                                    "event_id": "evt_ship",
                                    "title": "航线扰动带动运价关注",
                                    "published_at": "2026-04-07T08:10:00+00:00",
                                    "source_count": 1,
                                    "score": 41.0,
                                    "event_type": "commodity_disruption",
                                }
                            ],
                        },
                        {
                            "industry": "石油天然气",
                            "shared_news_score": 0.63,
                            "policy_articles": [
                                {
                                    "event_id": "evt_oil",
                                    "title": "OPEC 延长减产",
                                    "published_at": "2026-04-07T08:00:00+00:00",
                                    "source_count": 2,
                                    "score": 63.0,
                                    "event_type": "commodity_disruption",
                                }
                            ],
                        },
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        health_path.write_text(
            json.dumps(
                {
                    "source_health": [
                        {"source_id": "cls_telegraph_html", "status": "ok"},
                        {"source_id": "reuters_macro_bing", "status": "degraded"},
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        overlay, source_health = build_shared_news_policy_overlay(
            registry,
            run_dt,
            config={
                "enabled": True,
                "industry_radar_feed_path": str(feed_path),
                "source_health_path": str(health_path),
                "source_id": "shared:news_event_hub:industry_radar_feed",
                "max_feed_age_minutes": 90,
                "fallback_to_private_overlay": False,
                "industry_aliases": {
                    "石油天然气": "石油石化",
                    "半导体": "电子",
                    "航运": "交通运输",
                },
            },
        )

        assert_true(abs(float(overlay["sw_l1_801080"]["policy_score"]) - 0.74) < 1e-9, "shared feed should map 半导体 into 电子 and preserve score")
        assert_true(overlay["sw_l1_801170"]["policy_articles"][0]["title"] == "航线扰动带动运价关注", "shared feed should map 航运 into 交通运输")
        assert_true(overlay["sw_l1_801960"]["policy_articles"][0]["source_id"] == "shared:news_event_hub:industry_radar_feed", "shared policy articles should carry the shared source id")
        assert_true(source_health[0]["status"] == "pass", "fresh shared feed with mapped industries should record pass health")

        fallback_called = {"value": False}

        def fallback_builder(_registry: list[dict[str, str]], _run_dt: datetime):
            fallback_called["value"] = True
            return (
                {item["industry_id"]: {"policy_score": 0.0, "policy_articles": []} for item in _registry},
                [{"source_id": "private_overlay", "status": "pass", "fetched_count": 1, "matched_article_count": 1, "note": "fallback"}],
            )

        _, fallback_health = build_shared_news_policy_overlay(
            registry,
            run_dt,
            config={
                "enabled": True,
                "industry_radar_feed_path": str(root / "missing.json"),
                "source_health_path": str(health_path),
                "fallback_to_private_overlay": True,
            },
            fallback_builder=fallback_builder,
        )
        assert_true(fallback_called["value"], "missing shared feed should fall back to the private overlay when configured")
        assert_true(fallback_health[0]["source_id"] == "private_overlay", "fallback result should be returned unchanged")

    print("shared_news_overlay_smoke_ok")


if __name__ == "__main__":
    main()
