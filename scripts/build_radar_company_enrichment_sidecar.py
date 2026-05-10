#!/usr/bin/env python3
"""Build a pre-snapshot company-enrichment sidecar from News Event Hub research inputs."""

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
DEFAULT_INPUT_CANDIDATE_POOL = ROOT / "output" / "snapshots" / "radar_candidate_pool_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_company_enrichment_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_company_enrichment_latest.md"
SOCIAL_PREFIX_RE = re.compile(
    r"^[^$]{0,80}(?:\d{2}-\d{2}\s+\d{2}:\d{2}|修改于\d{2}-\d{2}\s+\d{2}:\d{2}|[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}).*?来自[^$]{1,40}\s*",
)
LOW_QUALITY_SOURCE_PREFIXES = ("social:", "forum:", "search:")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-candidate-pool", type=Path, default=DEFAULT_INPUT_CANDIDATE_POOL)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--force-all", action="store_true")
    return parser.parse_args()


def load_json_object(path: Path) -> dict[str, Any]:
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


def normalize_company_name(text: Any) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"\[[^\]]*\]\s*", "", cleaned)
    cleaned = re.sub(r"【[^】]*】\s*", "", cleaned)
    cleaned = re.sub(r"（[^）]*）", "", cleaned)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip("“”\"'")


def clean_title(text: Any) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def sanitize_title_for_company(company_name: str, title: str) -> str:
    text = clean_title(title)
    if not text:
        return ""
    text = re.sub(
        rf"^.*?\${re.escape(company_name)}(?:\((?:SH|SZ|BJ|HK)\d{{4,6}}\))?\$\s*",
        "",
        text,
        count=1,
    ).strip()
    text = SOCIAL_PREFIX_RE.sub("", text).strip()
    text = text.lstrip(".：:;；- ").strip()
    if not text:
        return ""
    if company_name and not text.startswith(company_name):
        text = f"{company_name}：{text}"
    return text[:120]


def is_low_quality_source_family(source_family: Any) -> bool:
    text = str(source_family or "").strip().lower()
    if not text:
        return False
    return text.startswith(LOW_QUALITY_SOURCE_PREFIXES)


def extract_stock_code(candidate: dict[str, Any]) -> str:
    texts: list[str] = []
    for event in candidate.get("shared_feed_events") or []:
        if not isinstance(event, dict):
            continue
        texts.append(str(event.get("event_title") or "").strip())
        for article in event.get("supporting_articles") or []:
            if isinstance(article, dict):
                texts.append(str(article.get("title") or "").strip())
    for text in texts:
        match = re.search(r"\((?:SH|SZ|BJ)(\d{6})\)", text)
        if match:
            return match.group(1)
        match = re.search(r"\((?:HK)(\d{4,5})\)", text)
        if match:
            return f"HK{match.group(1)}"
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


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, float, float, str]:
    source_quality = str(candidate.get("source_quality") or "")
    quality_rank = 0 if source_quality == "trusted" else 1 if source_quality == "mixed" else 2
    mapping_conf = float(candidate.get("entity_mapping_confidence") or 0.0)
    source_priority = float(candidate.get("source_priority") or 0.0)
    return (
        quality_rank,
        -mapping_conf,
        -source_priority,
        str(candidate.get("radar_object_name") or ""),
    )


def should_enrich(candidate: dict[str, Any], *, force_all: bool) -> bool:
    if force_all:
        return True
    if str(candidate.get("radar_object_type") or "") != "company":
        return False
    source_quality = str(candidate.get("source_quality") or "")
    mapping_conf = float(candidate.get("entity_mapping_confidence") or 0.0)
    source_priority = float(candidate.get("source_priority") or 0.0)
    return source_quality in {"trusted", "mixed"} or mapping_conf >= 0.6 or source_priority >= 45.0


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


def summarize_fast_profile(candidate: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    events = [row for row in (profile.get("top_events") or []) if isinstance(row, dict)]
    evidence = [row for row in (profile.get("evidence_bundle") or []) if isinstance(row, dict)]
    titles: list[str] = []
    trusted_titles: list[str] = []
    company_name = str(candidate.get("radar_object_name") or "")
    for row in [*events[:4], *evidence[:4]]:
        title = sanitize_title_for_company(company_name, str(row.get("event_title") or row.get("title") or ""))
        if title and title not in titles:
            titles.append(title)
        if title and not is_low_quality_source_family(row.get("source_family")) and title not in trusted_titles:
            trusted_titles.append(title)
    trusted_evidence_count = sum(1 for row in [*events, *evidence] if not is_low_quality_source_family(row.get("source_family")))
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "radar_object_id": str(candidate.get("radar_object_id") or ""),
        "radar_object_name": company_name,
        "status": "pass" if trusted_evidence_count > 0 else "warn",
        "lane": "research_feed_fastpath",
        "coverage_before": {
            "matching_events": int(len(events)),
            "matching_articles": int(len(evidence)),
        },
        "coverage_after": {
            "matching_events": int(len(events)),
            "matching_articles": int(len(evidence)),
        },
        "discovery": {
            "sources_run": 0,
            "inserted_total": 0,
            "updated_total": 0,
        },
        "latest_published_at": str(profile.get("latest_seen_at") or ""),
        "top_titles": titles[:3],
        "trusted_titles": trusted_titles[:3],
        "trusted_evidence_count": trusted_evidence_count,
    }


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


def top_titles(result: dict[str, Any], *, company_name: str, ticker: str, limit: int = 3) -> list[str]:
    research = result.get("research") or {}
    titles: list[str] = []
    for group_name in ("events", "articles", "evidence_bundle"):
        for row in (research.get(group_name) or []):
            if not isinstance(row, dict):
                continue
            raw_title = str(row.get("event_title") or row.get("title") or "").strip()
            if not raw_title:
                continue
            if company_name not in raw_title and ticker and ticker not in raw_title:
                continue
            title = sanitize_title_for_company(company_name, raw_title)
            if title and title not in titles:
                titles.append(title)
            if len(titles) >= limit:
                return titles
    return titles


def title_matches_company(title: str, company_name: str, ticker: str) -> bool:
    text = str(title or "").strip()
    if not text:
        return False
    if company_name and company_name in text:
        return True
    if ticker and ticker in text:
        return True
    return False


def trusted_titles(result: dict[str, Any], *, company_name: str, ticker: str, limit: int = 3) -> list[str]:
    research = result.get("research") or {}
    titles: list[str] = []
    for group_name in ("events", "articles", "evidence_bundle"):
        for row in (research.get(group_name) or []):
            if not isinstance(row, dict):
                continue
            if is_low_quality_source_family(row.get("source_family")):
                continue
            title = str(row.get("event_title") or row.get("title") or "").strip()
            if title and title_matches_company(title, company_name, ticker) and title not in titles:
                titles.append(title)
            if len(titles) >= limit:
                return titles
    return titles


def summarize_result(candidate: dict[str, Any], result: dict[str, Any], *, lane: str) -> dict[str, Any]:
    coverage_before = result.get("coverage_before") or {}
    coverage_after = result.get("coverage_after") or {}
    discovery = result.get("discovery") or {}
    company_name = str(candidate.get("radar_object_name") or "")
    ticker = extract_stock_code(candidate)
    filtered_titles = top_titles(result, company_name=company_name, ticker=ticker)
    trusted_filtered_titles = trusted_titles(result, company_name=company_name, ticker=ticker)
    status = "pass" if trusted_filtered_titles else "warn"
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "radar_object_id": str(candidate.get("radar_object_id") or ""),
        "radar_object_name": company_name,
        "status": status,
        "lane": lane,
        "coverage_before": {
            "matching_events": int(coverage_before.get("matching_events") or 0),
            "matching_articles": int(coverage_before.get("matching_articles") or 0),
        },
        "coverage_after": {
            "matching_events": int(coverage_after.get("matching_events") or 0),
            "matching_articles": int(coverage_after.get("matching_articles") or 0),
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
        "trusted_titles": trusted_filtered_titles,
        "trusted_evidence_count": len(trusted_filtered_titles),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Radar Company Enrichment",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 事件窗口日期：`{payload.get('report_date')}`",
        f"- 补核对象数：`{payload.get('enriched_count')}`",
        f"- 生成时间：`{payload.get('generated_at')}`",
        "",
    ]
    items = payload.get("items") or []
    if not items:
        lines.append("- 今日没有触发公司补核。")
        return "\n".join(lines) + "\n"
    for item in items:
        lines.extend(
            [
                f"## {item.get('radar_object_name')}",
                "",
                f"- 状态：`{item.get('status')}` | lane：`{item.get('lane')}`",
                "- 覆盖：before events {before_events} / articles {before_articles} -> after events {after_events} / articles {after_articles}".format(
                    before_events=int(((item.get("coverage_before") or {}).get("matching_events") or 0)),
                    before_articles=int(((item.get("coverage_before") or {}).get("matching_articles") or 0)),
                    after_events=int(((item.get("coverage_after") or {}).get("matching_events") or 0)),
                    after_articles=int(((item.get("coverage_after") or {}).get("matching_articles") or 0)),
                ),
                f"- 最新相关时间：`{item.get('latest_published_at') or 'unknown'}`",
            ]
        )
        for title in item.get("top_titles") or []:
            lines.append(f"- 标题：{title}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    candidate_pool = load_json_object(args.input_candidate_pool)
    cfg = load_config_section(args.config, "company_enrichment_sidecar") or load_config_section(args.config, "news_verification_sidecar")
    deployment = load_deployment_config(args.config)
    top_limit = max(int(cfg.get("top_company_limit") or 8), 1)
    timeout_seconds = max(int(cfg.get("timeout_seconds") or 240), 60)
    live_discovery_threshold_events = max(int(cfg.get("live_discovery_threshold_events") or 2), 0)
    live_discovery_threshold_articles = max(int(cfg.get("live_discovery_threshold_articles") or 4), 0)
    raw_max_live_discovery_objects = cfg.get("max_live_discovery_objects")
    max_live_discovery_objects = max(int(raw_max_live_discovery_objects if raw_max_live_discovery_objects is not None else 2), 0)
    research_feed_path = ROOT / "output" / "sidecars" / "news_event_hub" / "consumer_exports" / "research_feed_latest.json"
    research_feed = load_optional_json(research_feed_path)

    candidates = [
        candidate
        for candidate in (candidate_pool.get("candidates") or [])
        if isinstance(candidate, dict) and should_enrich(candidate, force_all=args.force_all)
    ]
    candidates.sort(key=candidate_sort_key)
    candidates = candidates[:top_limit]

    items: list[dict[str, Any]] = []
    host = str(deployment.get("server_host") or "").strip()
    ssh_options = str(deployment.get("ssh_options") or "").strip()
    local_script = Path(str(cfg.get("local_script_path") or (ROOT.parent / "news_event_hub" / "scripts" / "run_company_discovery.py"))).expanduser()
    remote_script = str(cfg.get("remote_script_path") or "/opt/news-event-hub/scripts/run_company_discovery.py").strip()
    local_python_bin = str(cfg.get("local_python_bin") or sys.executable).strip() or sys.executable
    remote_python_bin = str(cfg.get("remote_python_bin") or "python3").strip() or "python3"
    live_discovery_count = 0

    for candidate in candidates:
        company_name = normalize_company_name(candidate.get("radar_object_name"))
        if not company_name:
            continue
        fast_profile = fast_lookup_profile(research_feed, company_name)
        if fast_profile is not None:
            fast_summary = summarize_fast_profile(candidate, fast_profile)
            fast_events = int((fast_summary.get("coverage_after") or {}).get("matching_events") or 0)
            fast_articles = int((fast_summary.get("coverage_after") or {}).get("matching_articles") or 0)
            if fast_events >= live_discovery_threshold_events or fast_articles >= live_discovery_threshold_articles or live_discovery_count >= max_live_discovery_objects:
                items.append(fast_summary)
                continue
        if live_discovery_count >= max_live_discovery_objects:
            items.append(
                {
                    "candidate_id": str(candidate.get("candidate_id") or ""),
                    "radar_object_id": str(candidate.get("radar_object_id") or ""),
                    "radar_object_name": company_name,
                    "status": "warn",
                    "lane": "live_discovery_deferred",
                    "error": f"max_live_discovery_objects={max_live_discovery_objects}",
                    "coverage_before": {},
                    "coverage_after": {},
                    "discovery": {},
                    "latest_published_at": "",
                    "top_titles": [],
                }
            )
            continue
        ticker = extract_stock_code(candidate)
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

        live_discovery_count += 1
        result: dict[str, Any] | None = None
        error = ""
        lane = "none"
        if remote_script and Path(remote_script).exists():
            try:
                result, error = run_local_python(
                    Path(remote_script),
                    script_args,
                    timeout_seconds=timeout_seconds,
                    python_bin=remote_python_bin,
                )
            except Exception as exc:
                result = None
                error = f"{type(exc).__name__}: {exc}"
            lane = "local_remote_path"
        elif local_script.exists() and not host:
            try:
                result, error = run_local_python(
                    local_script,
                    script_args,
                    timeout_seconds=timeout_seconds,
                    python_bin=local_python_bin,
                )
            except Exception as exc:
                result = None
                error = f"{type(exc).__name__}: {exc}"
            lane = "local_workspace"
        elif host and remote_script:
            try:
                result, error = run_remote_python(
                    host=host,
                    ssh_options=ssh_options,
                    python_bin=remote_python_bin,
                    script_path=remote_script,
                    script_args=script_args,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                result = None
                error = f"{type(exc).__name__}: {exc}"
            lane = "ssh_remote"
        if result is None:
            if fast_profile is not None:
                fallback = summarize_fast_profile(candidate, fast_profile)
                fallback["lane"] = "research_feed_fastpath_fallback"
                fallback["live_discovery_error"] = error or "live_discovery_failed"
                items.append(fallback)
                continue
            items.append(
                {
                    "candidate_id": str(candidate.get("candidate_id") or ""),
                    "radar_object_id": str(candidate.get("radar_object_id") or ""),
                    "radar_object_name": company_name,
                    "status": "fail",
                    "lane": lane,
                    "error": error or "enrichment_failed",
                    "coverage_before": {},
                    "coverage_after": {},
                    "discovery": {},
                    "latest_published_at": "",
                    "top_titles": [],
                }
            )
            continue
        items.append(summarize_result(candidate, result, lane=lane))

    by_object_id: dict[str, dict[str, Any]] = {}
    for item in items:
        object_id = str(item.get("radar_object_id") or "").strip()
        if object_id:
            by_object_id[object_id] = item

    failed_items = [item for item in items if str(item.get("status") or "") == "fail"]
    warning_items = [item for item in items if str(item.get("status") or "") == "warn"]
    status = "fail" if failed_items else "pass" if items else "skip"
    payload = {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": str(candidate_pool.get("run_id") or candidate_pool.get("candidate_pool_run_id") or ""),
        "report_date": str(candidate_pool.get("report_date") or candidate_pool.get("as_of_date") or ""),
        "market_sample_date": str(candidate_pool.get("market_sample_date") or ""),
        "enriched_count": len(items),
        "warning_count": len(warning_items),
        "failure_count": len(failed_items),
        "non_blocking_warnings": [
            f"{item.get('radar_object_name')} 暂未补到可信公司新闻"
            for item in warning_items[:8]
        ],
        "items": items,
        "by_object_id": by_object_id,
    }
    write_json(args.json_output, payload)
    write_text(args.md_output, render_markdown(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
