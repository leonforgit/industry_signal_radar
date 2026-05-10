#!/usr/bin/env python3
"""Validate that Radar upstream news, sentiment, and market inputs are fresh enough for a daily report run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from build_radar_market_substrate import resolve_market_path
from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_current_health import reconcile_harness_current_health
from radar_freshness_utils import DEFAULT_MARKET_OPEN_TIME, DEFAULT_MARKET_SAMPLE_READY_TIME, DEFAULT_MARKET_TZ, expected_sample_date
from radar_sentiment_sidecar import resolve_sidecar_path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_source_readiness_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_source_readiness_latest.md"
DEFAULT_SHARED_NEWS_MIRROR_DIR = ROOT / "output" / "sidecars" / "news_event_hub" / "consumer_exports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_shared_news_path(raw_path: Any) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        return Path("")
    candidate = Path(text).expanduser()
    if candidate.exists():
        return candidate
    mirror_candidate = DEFAULT_SHARED_NEWS_MIRROR_DIR / candidate.name
    if mirror_candidate.exists():
        return mirror_candidate
    legacy_candidate = ROOT.parent / "news_event_hub" / "state" / "consumer_exports" / candidate.name
    if legacy_candidate.exists():
        return legacy_candidate
    return candidate


def resolve_expected_sample_date(config_path: Path) -> str:
    source_cfg = load_config_section(config_path, "source_readiness")
    market_tz = str(source_cfg.get("market_tz") or DEFAULT_MARKET_TZ)
    market_sample_ready_time = str(
        source_cfg.get("market_sample_ready_time")
        or source_cfg.get("market_open_time")
        or DEFAULT_MARKET_SAMPLE_READY_TIME
    )
    return expected_sample_date(
        {"market_tz": market_tz, "market_sample_ready_time": market_sample_ready_time},
        market_tz=market_tz,
        market_open_time=market_sample_ready_time,
    ).isoformat()


def assess_shared_news(config_path: Path, *, now: datetime, max_age_minutes: int) -> dict[str, Any]:
    cfg = load_config_section(config_path, "shared_news_event_hub")
    targets = {
        "industry_radar_feed": resolve_shared_news_path(cfg.get("industry_radar_feed_path")),
        "opportunity_report_feed": resolve_shared_news_path(cfg.get("opportunity_report_feed_path")),
        "research_feed": resolve_shared_news_path(cfg.get("research_feed_path")),
    }
    files: list[dict[str, Any]] = []
    blockers: list[str] = []
    for name, path in targets.items():
        if not path.exists():
            blockers.append(f"{name} 缺失")
            files.append({"name": name, "path": str(path), "status": "fail", "generated_at": "", "age_minutes": None})
            continue
        payload = load_json(path)
        generated_at = parse_iso_datetime(payload.get("generated_at"))
        age_minutes = None if generated_at is None else max(0.0, (now - generated_at).total_seconds() / 60.0)
        status = "pass"
        if generated_at is None:
            status = "fail"
            blockers.append(f"{name} 缺少 generated_at")
        elif age_minutes is not None and age_minutes > max_age_minutes:
            status = "fail"
            blockers.append(f"{name} 超过 {max_age_minutes} 分钟 freshness 窗口")
        files.append(
            {
                "name": name,
                "path": str(path),
                "status": status,
                "generated_at": generated_at.isoformat() if generated_at else "",
                "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
            }
        )
    status = "pass" if not blockers else "fail"
    return {
        "status": status,
        "files": files,
        "blockers": blockers,
        "max_age_minutes": max_age_minutes,
    }


def assess_source_health(
    config_path: Path,
    *,
    now: datetime,
    max_age_minutes: int,
    warn_if_down_count_gt: int,
    fail_if_ok_count_lt: int,
    critical_source_ids: set[str],
) -> dict[str, Any]:
    cfg = load_config_section(config_path, "shared_news_event_hub")
    path = resolve_shared_news_path(cfg.get("source_health_path"))
    if not path.exists():
        return {
            "status": "warn",
            "path": str(path),
            "generated_at": "",
            "age_minutes": None,
            "summary": {"total": 0, "ok": 0, "degraded": 0, "down": 0, "other": 0},
            "warnings": ["source_health_latest.json 缺失，无法判断共享新闻底座真实健康"],
            "blockers": [],
            "degraded_sources": [],
            "down_sources": [],
        }
    payload = load_json(path)
    generated_at = parse_iso_datetime(payload.get("generated_at"))
    age_minutes = None if generated_at is None else max(0.0, (now - generated_at).total_seconds() / 60.0)
    rows = [item for item in (payload.get("source_health") or []) if isinstance(item, dict)]
    counts = {"total": len(rows), "ok": 0, "degraded": 0, "down": 0, "other": 0}
    degraded_sources: list[dict[str, Any]] = []
    down_sources: list[dict[str, Any]] = []
    critical_down: list[str] = []
    critical_degraded: list[str] = []
    noncritical_down: list[str] = []
    noncritical_degraded: list[str] = []
    for item in rows:
        status = str(item.get("status") or "").strip().lower()
        source_id = str(item.get("source_id") or "").strip()
        if status in {"ok", "pass"}:
            counts["ok"] += 1
        elif status in {"degraded", "warn"}:
            counts["degraded"] += 1
            degraded_sources.append(item)
            if source_id in critical_source_ids:
                critical_degraded.append(source_id)
            elif source_id:
                noncritical_degraded.append(source_id)
        elif status in {"down", "fail", "failed", "error"}:
            counts["down"] += 1
            down_sources.append(item)
            if source_id in critical_source_ids:
                critical_down.append(source_id)
            elif source_id:
                noncritical_down.append(source_id)
        else:
            counts["other"] += 1
    warnings: list[str] = []
    non_blocking_warnings: list[str] = []
    blockers: list[str] = []
    if generated_at is None:
        warnings.append("source_health_latest.json 缺少 generated_at")
    elif age_minutes is not None and age_minutes > max_age_minutes:
        warnings.append(f"source_health_latest.json 超过 {max_age_minutes} 分钟 freshness 窗口")
    if critical_degraded:
        warnings.append("共享新闻底座核心源 degraded：" + " / ".join(sorted(critical_degraded)))
    if noncritical_down and len(noncritical_down) > warn_if_down_count_gt:
        non_blocking_warnings.append(f"共享新闻底座非核心源 down：{len(noncritical_down)} 个")
    if noncritical_degraded:
        non_blocking_warnings.append(f"共享新闻底座非核心源 degraded：{len(noncritical_degraded)} 个")
    if counts["ok"] < fail_if_ok_count_lt:
        blockers.append(f"共享新闻底座 ok 源不足：ok={counts['ok']} < {fail_if_ok_count_lt}")
    if critical_down:
        blockers.append("共享新闻底座核心源 down：" + " / ".join(sorted(critical_down)))
    status = "fail" if blockers else ("warn" if warnings or non_blocking_warnings else "pass")
    def compact_source(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": item.get("source_id"),
            "status": item.get("status"),
            "articles_last_24h": item.get("articles_last_24h"),
            "last_article_at": item.get("last_article_at"),
            "error_message": item.get("error_message"),
        }
    return {
        "status": status,
        "path": str(path),
        "generated_at": generated_at.isoformat() if generated_at else "",
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "summary": counts,
        "critical_summary": {
            "critical_down": len(critical_down),
            "critical_degraded": len(critical_degraded),
            "noncritical_down": len(noncritical_down),
            "noncritical_degraded": len(noncritical_degraded),
        },
        "warnings": warnings,
        "non_blocking_warnings": non_blocking_warnings,
        "blockers": blockers,
        "degraded_sources": [compact_source(item) for item in degraded_sources[:8]],
        "down_sources": [compact_source(item) for item in down_sources[:8]],
    }


def latest_csv_date(path: Path) -> str:
    if not path.exists():
        return ""
    frame = pd.read_csv(path, usecols=["datetime"])
    if "datetime" not in frame.columns or frame.empty:
        return ""
    parsed = pd.to_datetime(frame["datetime"], errors="coerce").dropna()
    if parsed.empty:
        return ""
    return parsed.max().date().isoformat()


def assess_sentiment(
    config_path: Path,
    *,
    expected_date: str,
    max_lag_days: int,
    warning_lag_days: int,
) -> dict[str, Any]:
    cfg = load_config_section(config_path, "sentiment_sidecar")
    mirror_dir = ROOT / str(cfg.get("mirror_dir") or "output/sidecars/sentiment")
    targets = {
        "market_sentiment": resolve_sidecar_path(cfg.get("market_csv_path"), mirror_dir=mirror_dir),
        "company_sentiment": resolve_sidecar_path(cfg.get("company_csv_path"), mirror_dir=mirror_dir),
    }
    files: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    expected = datetime.fromisoformat(expected_date).date()
    for name, path in targets.items():
        latest_date_text = latest_csv_date(path)
        if not latest_date_text:
            blockers.append(f"{name} 缺失或无有效日期")
            files.append({"name": name, "path": str(path), "status": "fail", "latest_date": "", "lag_days": None})
            continue
        latest_date = datetime.fromisoformat(latest_date_text).date()
        lag_days = max(0, (expected - latest_date).days)
        if lag_days <= max_lag_days:
            status = "pass"
        elif lag_days <= warning_lag_days:
            status = "warn"
            warnings.append(f"{name} 落后期望样本日期 {lag_days} 天")
        else:
            status = "fail"
            blockers.append(f"{name} 落后期望样本日期 {lag_days} 天")
        files.append(
            {
                "name": name,
                "path": str(path),
                "status": status,
                "latest_date": latest_date_text,
                "lag_days": lag_days,
            }
        )
    status = "pass" if not blockers else "fail"
    return {
        "status": status,
        "files": files,
        "blockers": blockers,
        "warnings": warnings,
        "max_lag_days": max_lag_days,
        "warning_lag_days": warning_lag_days,
    }


def assess_market(config_path: Path, *, expected_date: str) -> dict[str, Any]:
    cfg = load_config_section(config_path, "canonical_market_substrate")
    mirror_dir = ROOT / str(cfg.get("mirror_dir") or "output/sidecars/market")
    manifest_path = resolve_market_path(cfg.get("manifest_path"), mirror_dir=mirror_dir)
    if not manifest_path.exists():
        return {
            "status": "fail",
            "manifest_path": str(manifest_path),
            "latest_trade_date": "",
            "blockers": ["market manifest 缺失"],
        }
    payload = load_json(manifest_path)
    latest_trade_date = str(payload.get("latest_trade_date") or "").strip()
    warnings = payload.get("warnings") or []
    blockers: list[str] = []
    if latest_trade_date != expected_date:
        blockers.append(f"latest_trade_date={latest_trade_date or 'missing'} 与 expected_sample_date={expected_date} 不一致")
    status = "pass" if not blockers else "fail"
    return {
        "status": status,
        "manifest_path": str(manifest_path),
        "latest_trade_date": latest_trade_date,
        "warnings": warnings,
        "blockers": blockers,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_source_readiness"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Source Readiness {payload.get("expected_sample_date") or "unknown"}"',
        "---",
        "",
        "# Radar Source Readiness",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 期望样本日期：`{payload.get('expected_sample_date')}`",
        f"- 生成时间：`{payload.get('generated_at')}`",
        "",
        "## Shared News",
        "",
    ]
    shared = payload.get("shared_news") or {}
    lines.append(f"- 状态：`{shared.get('status')}`")
    for item in shared.get("files") or []:
        lines.append(
            "- {name}: `{status}` | generated_at `{generated_at}` | age_minutes `{age_minutes}`".format(
                name=item.get("name"),
                status=item.get("status"),
                generated_at=item.get("generated_at") or "missing",
                age_minutes=item.get("age_minutes"),
            )
        )
    lines.extend(["", "## Market", ""])
    market = payload.get("market") or {}
    lines.append(f"- 状态：`{market.get('status')}`")
    lines.append(f"- latest_trade_date：`{market.get('latest_trade_date')}`")
    lines.append(f"- manifest_path：`{market.get('manifest_path')}`")
    lines.extend(["", "## Sentiment", ""])
    sentiment = payload.get("sentiment") or {}
    lines.append(f"- 状态：`{sentiment.get('status')}`")
    for item in sentiment.get("files") or []:
        lines.append(
            "- {name}: `{status}` | latest_date `{latest_date}` | lag_days `{lag_days}`".format(
                name=item.get("name"),
                status=item.get("status"),
                latest_date=item.get("latest_date") or "missing",
                lag_days=item.get("lag_days"),
            )
        )
    lines.extend(["", "## Shared News Source Health", ""])
    source_health = payload.get("source_health") or {}
    summary = source_health.get("summary") or {}
    critical_summary = source_health.get("critical_summary") or {}
    lines.append(f"- 状态：`{source_health.get('status')}`")
    lines.append(
        "- 汇总：ok `{ok}` / degraded `{degraded}` / down `{down}` / total `{total}`".format(
            ok=summary.get("ok", 0),
            degraded=summary.get("degraded", 0),
            down=summary.get("down", 0),
            total=summary.get("total", 0),
        )
    )
    lines.append(
        "- 核心源：down `{critical_down}` / degraded `{critical_degraded}`；非核心源：down `{noncritical_down}` / degraded `{noncritical_degraded}`".format(
            critical_down=critical_summary.get("critical_down", 0),
            critical_degraded=critical_summary.get("critical_degraded", 0),
            noncritical_down=critical_summary.get("noncritical_down", 0),
            noncritical_degraded=critical_summary.get("noncritical_degraded", 0),
        )
    )
    for item in source_health.get("down_sources") or []:
        lines.append(f"- down: `{item.get('source_id')}` | {item.get('error_message') or ''}")
    lines.extend(["", "## Warnings", ""])
    warnings = payload.get("warnings") or []
    if warnings:
        for item in warnings:
            lines.append(f"- {item}")
    else:
        lines.append("- 无")
    non_blocking = source_health.get("non_blocking_warnings") or []
    if non_blocking:
        lines.extend(["", "## Non-Blocking Source Notes", ""])
        for item in non_blocking:
            lines.append(f"- {item}")
    lines.extend(["", "## Blockers", ""])
    blockers = payload.get("blockers") or []
    if blockers:
        for item in blockers:
            lines.append(f"- {item}")
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    now = datetime.now(timezone.utc)
    source_cfg = load_config_section(args.config, "source_readiness")
    json_output = args.json_output
    md_output = args.md_output
    if json_output == DEFAULT_JSON_OUTPUT and str(source_cfg.get("output_json_path") or "").strip():
        json_output = ROOT / str(source_cfg.get("output_json_path") or "").strip()
    if md_output == DEFAULT_MD_OUTPUT and str(source_cfg.get("output_md_path") or "").strip():
        md_output = ROOT / str(source_cfg.get("output_md_path") or "").strip()
    shared_cfg = load_config_section(args.config, "shared_news_event_hub")
    market_tz = str(source_cfg.get("market_tz") or DEFAULT_MARKET_TZ)
    market_sample_ready_time = str(
        source_cfg.get("market_sample_ready_time")
        or source_cfg.get("market_open_time")
        or DEFAULT_MARKET_SAMPLE_READY_TIME
    )
    expected_date = resolve_expected_sample_date(args.config)
    shared_news = assess_shared_news(
        args.config,
        now=now,
        max_age_minutes=int(source_cfg.get("max_shared_news_age_minutes") or shared_cfg.get("max_feed_age_minutes") or 90),
    )
    source_health = assess_source_health(
        args.config,
        now=now,
        max_age_minutes=int(source_cfg.get("max_source_health_age_minutes") or source_cfg.get("max_shared_news_age_minutes") or shared_cfg.get("max_feed_age_minutes") or 90),
        warn_if_down_count_gt=int(source_cfg.get("warn_if_source_health_down_count_gt") or 0),
        fail_if_ok_count_lt=int(source_cfg.get("fail_if_source_health_ok_count_lt") or 3),
        critical_source_ids={
            str(item).strip()
            for item in (source_cfg.get("critical_source_ids") or [])
            if str(item).strip()
        },
    )
    market = assess_market(args.config, expected_date=expected_date)
    sentiment = assess_sentiment(
        args.config,
        expected_date=expected_date,
        max_lag_days=int(source_cfg.get("max_sentiment_lag_days") or 0),
        warning_lag_days=max(
            int(source_cfg.get("max_sentiment_warning_lag_days") or 3),
            int(source_cfg.get("max_sentiment_lag_days") or 0),
        ),
    )
    blockers = [
        *(shared_news.get("blockers") or []),
        *(source_health.get("blockers") or []),
        *(market.get("blockers") or []),
        *(sentiment.get("blockers") or []),
    ]
    warnings = [
        *(source_health.get("warnings") or []),
        *(source_health.get("non_blocking_warnings") or []),
        *(market.get("warnings") or []),
        *(sentiment.get("warnings") or []),
    ]
    status = "fail" if blockers else ("warn" if warnings else "pass")
    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "expected_sample_date": expected_date,
        "market_tz": market_tz,
        "market_sample_ready_time": market_sample_ready_time,
        "status": status,
        "shared_news": shared_news,
        "source_health": source_health,
        "market": market,
        "sentiment": sentiment,
        "blockers": blockers,
        "warnings": warnings,
    }
    write_json(json_output, payload)
    write_text(md_output, render_markdown(payload))
    if json_output.resolve() == DEFAULT_JSON_OUTPUT.resolve():
        reconcile_harness_current_health(reason="source_readiness_updated")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload.get("status") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
