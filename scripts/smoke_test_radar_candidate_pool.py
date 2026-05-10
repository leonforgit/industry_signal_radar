#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone

from build_radar_candidate_pool import build_candidate_pool_payload


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    run_dt = datetime(2026, 4, 12, 9, 0, tzinfo=timezone.utc)
    industry_feed = {
        "generated_at": "2026-04-12T08:58:00Z",
        "industries": [
            {
                "industry": "航运",
                "shared_news_score": 0.74,
                "event_count": 2,
                "shared_events": [
                    {
                        "event_id": "evt_ship_shared",
                        "event_type": "industry_proxy",
                        "title": "BDI 与油运运价同步抬升",
                        "score": 72.0,
                        "supporting_articles": [
                            {
                                "source_family": "social:xueqiu",
                                "source_id": "xueqiu_tracked_search",
                                "canonical_url": "https://xueqiu.com/example",
                            }
                        ],
                    }
                ],
                "policy_articles": [
                    {
                        "event_id": "evt_ship",
                        "event_type": "industry_event",
                        "title": "运价线索抬升",
                        "score": 61.0,
                        "supporting_articles": [
                            {
                                "source_family": "social:xueqiu",
                                "source_id": "xueqiu_tracked_search",
                                "canonical_url": "https://xueqiu.com/example2",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    extra_feeds = [
        (
            "news_event_hub.opportunity_report_feed_latest",
            {
                "top_events": [
                    {
                        "title": "中国电网投资继续抬升",
                        "event_id": "evt_macro_001",
                        "event_type": "macro_event",
                        "primary_industry": "公用事业",
                        "score": 64.0,
                        "supporting_articles": [
                            {
                                "source_family": "social:xueqiu",
                                "source_id": "xueqiu_tracked_search",
                                "canonical_url": "https://xueqiu.com/example3",
                            }
                        ],
                    }
                ]
            },
        )
    ]
    market_index = {
        "sw_l1_801170": {
            "as_of_date": "2026-04-12",
            "aux_flow_score": 0.72,
            "sector_flow_score": 0.68,
            "fundamental_proxy_score": 0.83,
            "flow_signal_evidence": {"sector_main_net_inflow": 1.5e9},
            "fundamental_proxy_evidence": {"proxy_family": "shipping_cycle", "summary_cn": "BDI / 航运运价"},
        },
        "sw_l1_801010": {
            "as_of_date": "2026-04-12",
            "aux_flow_score": 0.41,
            "sector_flow_score": 0.35,
            "fundamental_proxy_score": 0.65,
            "flow_signal_evidence": {"sector_main_net_inflow": 8.6e8},
            "fundamental_proxy_evidence": {"proxy_family": "hog_cycle", "summary_cn": "猪价 / 饲料 / 生猪指数"},
        }
    }
    payload = build_candidate_pool_payload(
        run_dt=run_dt,
        rows=[],
        industry_feed_payload=industry_feed,
        extra_feed_payloads=extra_feeds,
        market_index=market_index,
    )
    assert_true(len(payload["candidates"]) == 3, "candidate pool should keep news-seeded candidates and market-only candidates")
    industry_candidate = next(item for item in payload["candidates"] if item["radar_object_type"] == "industry")
    assert_true(industry_candidate["candidate_origin"] == ["shared_feed", "canonical_market"], "industry candidate should include canonical market origin when evidence exists")
    assert_true(len(industry_candidate["sidecar_evidence"]) == 2, "industry candidate should carry flow and proxy evidence")
    assert_true(len(industry_candidate["shared_feed_events"]) >= 2, "industry candidate should merge industry feed shared events")
    assert_true(industry_candidate["market_context"]["fundamental_proxy_evidence"]["proxy_family"] == "shipping_cycle", "industry candidate should expose canonical proxy evidence")
    utility_candidate = next(item for item in payload["candidates"] if item["radar_object_id"] == "sw_l1_801160")
    assert_true(utility_candidate["candidate_origin"] == ["shared_feed"], "industry seeded only from news feed should remain shared-feed only")
    hog_candidate = next(item for item in payload["candidates"] if item["radar_object_id"] == "sw_l1_801010")
    assert_true(hog_candidate["candidate_origin"] == ["canonical_market"], "market-only signal should still create an industry candidate")
    assert_true(hog_candidate["shared_feed_events"] == [], "market-only industry candidate should not invent shared-feed events")
    print("radar_candidate_pool_smoke_ok")


if __name__ == "__main__":
    main()
