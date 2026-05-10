#!/usr/bin/env python3
"""Build a Radar-side news-verification sidecar by querying News Event Hub for thin-evidence names."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_upstream_bridge import load_deployment_config, run_local_python, run_remote_python


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_INPUT_COMPANY_ENRICHMENT = ROOT / "output" / "reports" / "radar_company_enrichment_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_news_verification_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_news_verification_latest.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT)
    parser.add_argument("--input-company-enrichment", type=Path, default=DEFAULT_INPUT_COMPANY_ENRICHMENT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--force-all", action="store_true", help="Verify all eligible companies instead of only thin-evidence names.")
    return parser.parse_args()


def load_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return payload


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def extract_stock_code(item: dict[str, Any]) -> str:
    for raw in item.get("primary_symbols") or []:
        text = str(raw or "").strip()
        match = re.search(r"(\d{6})(?:\b|$)", text)
        if match:
            return match.group(1)
        match = re.search(r"((?:HK|hk)?\d{4,5})(?:\b|$)", text)
        if match:
            return match.group(1).upper()
    return ""


def extend_discovery_runtime_args(script_args: list[str], cfg: dict[str, Any]) -> None:
    request_timeout = cfg.get("discovery_request_timeout_seconds")
    if request_timeout is not None:
        script_args.extend(["--request-timeout-seconds", str(max(int(request_timeout), 1))])
    for source_id in cfg.get("discovery_source_ids") or []:
        clean = str(source_id or "").strip()
        if clean:
            script_args.extend(["--source-id", clean])
    max_live_sources = int(cfg.get("discovery_max_live_sources") or 0)
    if max_live_sources > 0:
        script_args.extend(["--max-live-sources", str(max_live_sources)])
    max_browser_sources = int(cfg.get("discovery_max_browser_sources") or 0)
    if max_browser_sources > 0:
        script_args.extend(["--max-browser-sources", str(max_browser_sources)])
    if bool(cfg.get("discovery_skip_event_rebuild")):
        script_args.append("--skip-event-rebuild")
    if bool(cfg.get("discovery_skip_consumer_export")):
        script_args.append("--skip-consumer-export")


def sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    return (
        0 if str(item.get("triage_action") or "") == "immediate_research" else 1,
        0 if str(item.get("radar_bucket") or "") == "strong_alert" else 1 if str(item.get("radar_bucket") or "") == "strong_candidate" else 2,
        -int(item.get("radar_score") or 0),
    )


def needs_verification(item: dict[str, Any], *, force_all: bool) -> bool:
    if force_all:
        return True
    if str(item.get("radar_object_type") or "") != "company":
        return False
    evidence_quality = str(item.get("evidence_quality") or "")
    confidence = int(item.get("confidence") or 0)
    supporting_count = len(item.get("supporting_events") or [])
    confirmation_gap = str(item.get("confirmation_gap") or "")
    if evidence_quality in {"early_thematic", "narrative_only"}:
        return True
    if confidence < 78:
        return True
    if supporting_count <= 2:
        return True
    if any(token in confirmation_gap for token in ("仍待确认", "待核实", "待确认", "不能直接判断", "尚未覆盖")):
        return True
    return False


def should_expand_live_discovery_budget(item: dict[str, Any]) -> bool:
    if str(item.get("triage_action") or "") == "immediate_research":
        return True
    if str(item.get("radar_bucket") or "") in {"strong_alert", "strong_candidate"}:
        return True
    try:
        return int(item.get("rank_overall") or 9999) <= 10
    except (TypeError, ValueError):
        return False


def fast_lookup_profile(feed: dict[str, Any], company_name: str) -> dict[str, Any] | None:
    if not feed:
        return None
    query_index = feed.get("entity_query_index") or {}
    entity_profiles = feed.get("entity_profiles") or {}
    lookup = query_index.get(company_name)
    if isinstance(lookup, dict):
        profile = entity_profiles.get(str(lookup.get("entity_name") or "").strip()) or entity_profiles.get(str(lookup.get("entity_id") or "").strip())
        if isinstance(profile, dict):
            return profile
    for profile in entity_profiles.values():
        if not isinstance(profile, dict):
            continue
        if str(profile.get("entity_name") or "").strip() == company_name:
            return profile
    return None


def summarize_fast_profile(item: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    events = [row for row in (profile.get("top_events") or []) if isinstance(row, dict)]
    evidence = [row for row in (profile.get("evidence_bundle") or []) if isinstance(row, dict)]
    latest = str(profile.get("latest_seen_at") or "").strip()
    status = "pass" if events or evidence else "warn"
    titles: list[str] = []
    for row in events[:3]:
        title = str(row.get("event_title") or row.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    for row in evidence[:3]:
        title = str(row.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    return {
        "radar_object_id": str(item.get("radar_object_id") or ""),
        "radar_object_name": str(item.get("radar_object_name") or ""),
        "triage_action": str(item.get("triage_action") or ""),
        "lane": "research_feed_fastpath",
        "status": status,
        "coverage_before": {
            "matching_articles": int(len(evidence)),
            "matching_events": int(len(events)),
        },
        "coverage_after": {
            "matching_articles": int(len(evidence)),
            "matching_events": int(len(events)),
        },
        "discovery": {
            "sources_run": 0,
            "inserted_total": 0,
            "updated_total": 0,
        },
        "latest_published_at": latest,
        "top_titles": titles[:3],
    }


def summarize_enrichment_item(item: dict[str, Any], enrichment: dict[str, Any]) -> dict[str, Any]:
    trusted_titles = [str(x).strip() for x in (enrichment.get("trusted_titles") or []) if str(x).strip()]
    return {
        "radar_object_id": str(item.get("radar_object_id") or ""),
        "radar_object_name": str(item.get("radar_object_name") or ""),
        "triage_action": str(item.get("triage_action") or ""),
        "lane": f"company_enrichment:{str(enrichment.get('lane') or 'unknown')}",
        "status": str(enrichment.get("status") or "warn"),
        "coverage_before": {
            "matching_articles": int(((enrichment.get("coverage_before") or {}).get("matching_articles") or 0)),
            "matching_events": int(((enrichment.get("coverage_before") or {}).get("matching_events") or 0)),
        },
        "coverage_after": {
            "matching_articles": int(((enrichment.get("coverage_after") or {}).get("matching_articles") or 0)),
            "matching_events": int(((enrichment.get("coverage_after") or {}).get("matching_events") or 0)),
        },
        "discovery": {
            "sources_run": int(((enrichment.get("discovery") or {}).get("sources_run") or 0)),
            "inserted_total": int(((enrichment.get("discovery") or {}).get("inserted_total") or 0)),
            "updated_total": int(((enrichment.get("discovery") or {}).get("updated_total") or 0)),
        },
        "latest_published_at": str(enrichment.get("latest_published_at") or ""),
        "top_titles": (trusted_titles or [str(x).strip() for x in (enrichment.get("top_titles") or []) if str(x).strip()])[:3],
    }


def title_matches_company(title: str, company_name: str, ticker: str) -> bool:
    text = str(title or "").strip()
    if not text:
        return False
    if company_name and company_name in text:
        return True
    if ticker and ticker in text:
        return True
    return False


def supporting_event_title(row: dict[str, Any]) -> str:
    for key in ("title", "headline", "summary", "event_title"):
        title = str(row.get(key) or "").strip()
        if title:
            return title
    return ""


def snapshot_supporting_event_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    object_name = str(item.get("radar_object_name") or "").strip()
    ticker = extract_stock_code(item)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in item.get("supporting_events") or []:
        if not isinstance(row, dict):
            continue
        title = supporting_event_title(row)
        source = str(row.get("source") or "").strip()
        event_id = str(row.get("event_id") or "").strip()
        if not title_matches_company(title, object_name, ticker):
            continue
        trusted_source = bool(event_id) or source.startswith("news_event_hub")
        if not trusted_source and str(item.get("evidence_quality") or "") != "structured_confirmed":
            continue
        key = event_id or title
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def summarize_snapshot_supporting_events(
    item: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    lane: str,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not rows:
        return None
    titles: list[str] = []
    timestamps: list[str] = []
    event_ids: list[str] = []
    for row in rows:
        title = supporting_event_title(row)
        if title and title not in titles:
            titles.append(title)
        published_at = str(row.get("published_at") or "").strip()
        if published_at:
            timestamps.append(published_at)
        event_id = str(row.get("event_id") or "").strip()
        if event_id and event_id not in event_ids:
            event_ids.append(event_id)
    coverage = {
        "matching_articles": 0,
        "matching_events": len(rows),
        "snapshot_supporting_events": len(rows),
    }
    return {
        "radar_object_id": str(item.get("radar_object_id") or ""),
        "radar_object_name": str(item.get("radar_object_name") or ""),
        "triage_action": str(item.get("triage_action") or ""),
        "lane": lane,
        "status": "pass",
        "coverage_before": coverage,
        "coverage_after": coverage,
        "discovery": {
            "sources_run": 0,
            "inserted_total": 0,
            "updated_total": 0,
        },
        "diagnostics": diagnostics or {},
        "latest_published_at": max(timestamps) if timestamps else "",
        "top_titles": titles[:3],
        "snapshot_supporting_event_ids": event_ids[:5],
    }


def top_titles(result: dict[str, Any], *, company_name: str, ticker: str, limit: int = 3) -> list[str]:
    research = result.get("research") or {}
    titles: list[str] = []
    for row in (research.get("events") or []):
        if not isinstance(row, dict):
            continue
        title = str(row.get("event_title") or row.get("title") or "").strip()
        if title and title_matches_company(title, company_name, ticker) and title not in titles:
            titles.append(title)
        if len(titles) >= limit:
            return titles
    for row in (research.get("articles") or []):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if title and title_matches_company(title, company_name, ticker) and title not in titles:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def latest_published_at(result: dict[str, Any]) -> str:
    research = result.get("research") or {}
    timestamps: list[str] = []
    for group_name in ("events", "articles", "evidence_bundle"):
        for row in (research.get(group_name) or []):
            if not isinstance(row, dict):
                continue
            text = str(row.get("published_at") or "").strip()
            if text:
                timestamps.append(text)
    return max(timestamps) if timestamps else ""


def summarize_result(item: dict[str, Any], result: dict[str, Any], *, lane: str) -> dict[str, Any]:
    coverage_before = result.get("coverage_before") or {}
    coverage_after = result.get("coverage_after") or {}
    discovery = result.get("discovery") or {}
    object_name = str(item.get("radar_object_name") or "")
    ticker = extract_stock_code(item)
    filtered_titles = top_titles(result, company_name=object_name, ticker=ticker)
    status = "pass" if int(coverage_after.get("matching_events") or 0) > 0 or filtered_titles else "warn"
    return {
        "radar_object_id": str(item.get("radar_object_id") or ""),
        "radar_object_name": object_name,
        "triage_action": str(item.get("triage_action") or ""),
        "lane": lane,
        "status": status,
        "coverage_before": {
            "matching_articles": int(coverage_before.get("matching_articles") or 0),
            "matching_events": int(coverage_before.get("matching_events") or 0),
        },
        "coverage_after": {
            "matching_articles": int(coverage_after.get("matching_articles") or 0),
            "matching_events": int(coverage_after.get("matching_events") or 0),
        },
        "discovery": {
            "sources_run": int(discovery.get("sources_run") or 0),
            "inserted_total": int(discovery.get("inserted_total") or 0),
            "updated_total": int(discovery.get("updated_total") or 0),
            "source_results": [
                {
                    "source_id": str(row.get("source_id") or ""),
                    "status": str(row.get("status") or ""),
                    "error": str(row.get("error") or ""),
                    "elapsed_seconds": float(row.get("elapsed_seconds") or 0.0),
                    "fetched_items": int(row.get("fetched_items") or 0),
                    "matched_items": int(row.get("matched_items") or 0),
                    "eligible_items": int(row.get("eligible_items") or 0),
                }
                for row in (discovery.get("source_results") or [])
                if isinstance(row, dict)
            ],
        },
        "diagnostics": result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {},
        "latest_published_at": latest_published_at(result),
        "top_titles": filtered_titles,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_news_verification"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar News Verification {payload.get("report_date") or "unknown"}"',
        "---",
        "",
        "# Radar News Verification",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 事件窗口日期：`{payload.get('report_date')}`",
        f"- 核实对象数：`{payload.get('verified_count')}`",
        f"- 生成时间：`{payload.get('generated_at')}`",
        "",
    ]
    items = payload.get("items") or []
    if not items:
        lines.append("- 今日没有触发新闻补核。")
        return "\n".join(lines) + "\n"
    for item in items:
        lines.extend(
            [
                f"## {item.get('radar_object_name')}",
                "",
                "- 状态：`{status}` | 研究动作：`{triage}` | lane：`{lane}`".format(
                    status=item.get("status"),
                    triage=item.get("triage_action"),
                    lane=item.get("lane"),
                ),
                "- 覆盖：before events {before_events} / articles {before_articles} -> after events {after_events} / articles {after_articles}".format(
                    before_events=int(((item.get("coverage_before") or {}).get("matching_events") or 0)),
                    before_articles=int(((item.get("coverage_before") or {}).get("matching_articles") or 0)),
                    after_events=int(((item.get("coverage_after") or {}).get("matching_events") or 0)),
                    after_articles=int(((item.get("coverage_after") or {}).get("matching_articles") or 0)),
                ),
                "- 最新相关时间：`{latest}`".format(latest=item.get("latest_published_at") or "unknown"),
            ]
        )
        for title in item.get("top_titles") or []:
            lines.append(f"- 标题：{title}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    snapshot = load_snapshot(args.input_snapshot)
    cfg = load_config_section(args.config, "news_verification_sidecar")
    deployment = load_deployment_config(args.config)
    top_limit = max(int(cfg.get("top_company_limit") or 6), 1)
    timeout_seconds = max(int(cfg.get("timeout_seconds") or 240), 60)
    allowlist = {str(x) for x in (cfg.get("triage_allowlist") or ["immediate_research", "thesis_watch"])}

    objects = [
        item
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict)
        and str(item.get("radar_object_type") or "") == "company"
        and str(item.get("triage_action") or "") in allowlist
    ]
    objects.sort(key=sort_key)
    candidates = [item for item in objects if needs_verification(item, force_all=args.force_all)][:top_limit]
    research_feed_path = ROOT / "output" / "sidecars" / "news_event_hub" / "consumer_exports" / "research_feed_latest.json"
    research_feed = load_optional_json(research_feed_path)
    company_enrichment = load_optional_json(args.input_company_enrichment)
    enrichment_index = company_enrichment.get("by_object_id") or {}
    live_discovery_threshold_events = max(int(cfg.get("live_discovery_threshold_events") or 2), 0)
    live_discovery_threshold_articles = max(int(cfg.get("live_discovery_threshold_articles") or 4), 0)
    raw_max_live_discovery_objects = cfg.get("max_live_discovery_objects")
    max_live_discovery_objects = max(int(raw_max_live_discovery_objects if raw_max_live_discovery_objects is not None else 2), 0)
    raw_allow_budget_expansion = cfg.get("allow_live_discovery_budget_expansion")
    allow_live_discovery_budget_expansion = True if raw_allow_budget_expansion is None else bool(raw_allow_budget_expansion)
    expanded_live_discovery_limit = max(
        max_live_discovery_objects,
        int(cfg.get("expanded_live_discovery_limit") or top_limit),
    )
    live_discovery_count = 0

    items: list[dict[str, Any]] = []
    host = str(deployment.get("server_host") or "").strip()
    ssh_options = str(deployment.get("ssh_options") or "").strip()

    local_script = Path(str(cfg.get("local_script_path") or (ROOT.parent / "news_event_hub" / "scripts" / "run_company_discovery.py"))).expanduser()
    remote_script = str(cfg.get("remote_script_path") or "/opt/news-event-hub/scripts/run_company_discovery.py").strip()
    local_python_bin = str(cfg.get("local_python_bin") or sys.executable).strip() or sys.executable
    remote_python_bin = str(cfg.get("remote_python_bin") or "python3").strip() or "python3"

    for item in candidates:
        company_name = str(item.get("radar_object_name") or "").strip()
        if not company_name:
            continue
        snapshot_support_rows = snapshot_supporting_event_rows(item)
        enrichment_item = enrichment_index.get(str(item.get("radar_object_id") or "").strip())
        if isinstance(enrichment_item, dict):
            items.append(summarize_enrichment_item(item, enrichment_item))
            continue
        fast_profile = fast_lookup_profile(research_feed, company_name)
        if fast_profile is not None:
            fast_summary = summarize_fast_profile(item, fast_profile)
            fast_events = int((fast_summary.get("coverage_after") or {}).get("matching_events") or 0)
            fast_articles = int((fast_summary.get("coverage_after") or {}).get("matching_articles") or 0)
            if fast_events >= live_discovery_threshold_events or fast_articles >= live_discovery_threshold_articles:
                items.append(fast_summary)
                continue
        expanded_live_discovery_for_item = False
        if live_discovery_count >= max_live_discovery_objects:
            expanded_live_discovery_for_item = (
                allow_live_discovery_budget_expansion
                and live_discovery_count < expanded_live_discovery_limit
                and should_expand_live_discovery_budget(item)
            )
        if live_discovery_count >= max_live_discovery_objects and not expanded_live_discovery_for_item:
            snapshot_summary = summarize_snapshot_supporting_events(
                item,
                snapshot_support_rows,
                lane="snapshot_supporting_events_cap_fallback",
                diagnostics={
                    "live_discovery_deferred": True,
                    "deferred_reason": f"max_live_discovery_objects={max_live_discovery_objects}",
                },
            )
            if snapshot_summary is not None:
                items.append(snapshot_summary)
                continue
            if fast_profile is not None:
                fast_summary = summarize_fast_profile(item, fast_profile)
                fast_summary["lane"] = "research_feed_fastpath_cap_fallback"
                fast_summary["diagnostics"] = {
                    "live_discovery_deferred": True,
                    "deferred_reason": f"max_live_discovery_objects={max_live_discovery_objects}",
                }
                items.append(fast_summary)
                continue
            items.append(
                {
                    "radar_object_id": str(item.get("radar_object_id") or ""),
                    "radar_object_name": company_name,
                    "triage_action": str(item.get("triage_action") or ""),
                    "lane": "live_discovery_deferred",
                    "status": "warn",
                    "error": f"max_live_discovery_objects={max_live_discovery_objects}",
                    "coverage_before": {},
                    "coverage_after": {},
                    "discovery": {},
                    "latest_published_at": "",
                    "top_titles": [],
                }
            )
            continue
        ticker = extract_stock_code(item)
        script_args = [
            "--company",
            company_name,
            "--article-limit",
            str(int(cfg.get("article_limit") or 8)),
            "--event-limit",
            str(int(cfg.get("event_limit") or 5)),
        ]
        if ticker:
            script_args.extend(["--ticker", ticker])
        extend_discovery_runtime_args(script_args, cfg)

        result: dict[str, Any] | None = None
        lane = "none"
        error = ""
        live_discovery_count += 1
        if remote_script and Path(remote_script).exists():
            result, error = run_local_python(
                Path(remote_script),
                script_args,
                timeout_seconds=timeout_seconds,
                python_bin=remote_python_bin,
            )
            lane = "local_remote_path"
        elif local_script.exists() and not host:
            result, error = run_local_python(
                local_script,
                script_args,
                timeout_seconds=timeout_seconds,
                python_bin=local_python_bin,
            )
            lane = "local_workspace"
        elif host and remote_script:
            result, error = run_remote_python(
                host=host,
                ssh_options=ssh_options,
                python_bin=remote_python_bin,
                script_path=remote_script,
                script_args=script_args,
                timeout_seconds=timeout_seconds,
            )
            lane = "ssh_remote"
        if result is None:
            if fast_profile is not None:
                fast_summary = summarize_fast_profile(item, fast_profile)
                fast_summary["lane"] = "research_feed_fastpath_fallback"
                fast_summary["live_discovery_error"] = error or "live_discovery_failed"
                if expanded_live_discovery_for_item:
                    fast_summary["diagnostics"] = {
                        "live_discovery_budget_expanded": True,
                        "original_max_live_discovery_objects": max_live_discovery_objects,
                        "expanded_live_discovery_limit": expanded_live_discovery_limit,
                        "live_discovery_error": error or "live_discovery_failed",
                    }
                items.append(fast_summary)
                continue
            snapshot_summary = summarize_snapshot_supporting_events(
                item,
                snapshot_support_rows,
                lane="snapshot_supporting_events_discovery_fallback",
                diagnostics={
                    "live_discovery_error": error or "live_discovery_failed",
                    "live_discovery_budget_expanded": expanded_live_discovery_for_item,
                    "original_max_live_discovery_objects": max_live_discovery_objects,
                    "expanded_live_discovery_limit": expanded_live_discovery_limit,
                },
            )
            if snapshot_summary is not None:
                items.append(snapshot_summary)
                continue
            items.append(
                {
                    "radar_object_id": str(item.get("radar_object_id") or ""),
                    "radar_object_name": company_name,
                    "triage_action": str(item.get("triage_action") or ""),
                    "lane": lane,
                    "status": "fail",
                    "error": error or "verification_failed",
                    "coverage_before": {},
                    "coverage_after": {},
                    "discovery": {},
                    "latest_published_at": "",
                    "top_titles": [],
                }
            )
            continue
        result_summary = summarize_result(item, result, lane=lane)
        if expanded_live_discovery_for_item:
            diagnostics = result_summary.setdefault("diagnostics", {})
            if isinstance(diagnostics, dict):
                diagnostics["live_discovery_budget_expanded"] = True
                diagnostics["original_max_live_discovery_objects"] = max_live_discovery_objects
                diagnostics["expanded_live_discovery_limit"] = expanded_live_discovery_limit
        if str(result_summary.get("status") or "") != "pass":
            snapshot_summary = summarize_snapshot_supporting_events(
                item,
                snapshot_support_rows,
                lane="snapshot_supporting_events_after_live_discovery",
                diagnostics={
                    "live_discovery_lane": lane,
                    "live_discovery_status": str(result_summary.get("status") or ""),
                    "live_discovery_coverage_after": result_summary.get("coverage_after") or {},
                    "live_discovery_sources_run": int(((result_summary.get("discovery") or {}).get("sources_run") or 0)),
                    "live_discovery_budget_expanded": expanded_live_discovery_for_item,
                    "original_max_live_discovery_objects": max_live_discovery_objects,
                    "expanded_live_discovery_limit": expanded_live_discovery_limit,
                },
            )
            if snapshot_summary is not None:
                items.append(snapshot_summary)
                continue
        items.append(result_summary)

    status = "pass" if all(str(item.get("status") or "") == "pass" for item in items) else "warn"
    if not items:
        status = "skip"
    payload = {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": str(snapshot.get("run_id") or snapshot.get("radar_run_id") or ""),
        "report_date": str(snapshot.get("event_window_end_date") or snapshot.get("report_date") or ""),
        "market_sample_date": str(snapshot.get("market_sample_date") or ""),
        "verified_count": len(items),
        "items": items,
    }
    write_json(args.json_output, payload)
    write_text(args.md_output, render_markdown(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status in {"pass", "skip"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
