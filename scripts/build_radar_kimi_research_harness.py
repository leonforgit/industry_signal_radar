#!/usr/bin/env python3
"""Build structured Kimi research verdicts for Radar report ranking."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from radar_current_health import reconcile_harness_current_health
from radar_config import DEFAULT_CONFIG_PATH, load_config_section

from build_radar_kimi_editorial import (
    call_kimi_json_with_candidates,
    compact_sidecar_items,
    discover_credential_candidates,
    opportunity_precheck,
    summarize_event,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_INPUT_HANDOFF = ROOT / "output" / "handoffs" / "radar_research_handoff_latest.json"
DEFAULT_INPUT_COMPANY_ENRICHMENT = ROOT / "output" / "reports" / "radar_company_enrichment_latest.json"
DEFAULT_INPUT_NEWS_VERIFICATION = ROOT / "output" / "reports" / "radar_news_verification_latest.json"
DEFAULT_INPUT_IPO_WATCHLIST = ROOT / "output" / "reports" / "radar_ipo_watchlist_latest.json"
DEFAULT_INPUT_HK_IPO_WATCHLIST = ROOT / "output" / "reports" / "radar_hk_ipo_watchlist_latest.json"
DEFAULT_INPUT_STRUCTURAL_SIGNAL = ROOT / "output" / "reports" / "radar_structural_signal_latest.json"
DEFAULT_INPUT_SOURCE_READINESS = ROOT / "output" / "reports" / "radar_source_readiness_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_kimi_research_harness_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_kimi_research_harness_latest.md"
DEFAULT_PROGRESS_OUTPUT = ROOT / "output" / "runs" / "radar_kimi_research_progress_latest.json"

DECISIONS = {"promote", "keep", "watch_only", "reject", "risk_review", "research_gap"}
RISK_KEYWORDS = ("ST", "风险警示", "退市", "摘牌", "亏损", "减持", "终止", "监管函", "处罚", "立案")
SHALLOW_ACTION_PATTERNS = ("核实真实性", "确认是否真实", "继续关注", "看是否确认", "补具体公告原文", "定位风险来源", "确认是否影响前排对象")
MODEL_PROVENANCE = "model_generated"
MISSING_MODEL_PROVENANCE = "missing_model_verdict"
DETERMINISTIC_PROVENANCE = "deterministic_fallback"
IPO_VERDICT_LIMIT = 8
STRUCTURAL_VERDICT_LIMIT = 6
STRUCTURAL_REQUIRED_MIN = 3
SANITIZED_REPLACEMENTS = {
    "交易指令": "执行建议",
    "交易": "研究执行",
    "申购": "参与研究",
    "买入": "纳入研究",
    "卖出": "移出研究",
    "仓位": "报告展示",
    "下单": "执行",
    "荐股": "个股研究",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT)
    parser.add_argument("--input-handoff", type=Path, default=DEFAULT_INPUT_HANDOFF)
    parser.add_argument("--input-company-enrichment", type=Path, default=DEFAULT_INPUT_COMPANY_ENRICHMENT)
    parser.add_argument("--input-news-verification", type=Path, default=DEFAULT_INPUT_NEWS_VERIFICATION)
    parser.add_argument("--input-ipo-watchlist", type=Path, default=DEFAULT_INPUT_IPO_WATCHLIST)
    parser.add_argument("--input-hk-ipo-watchlist", type=Path, default=DEFAULT_INPUT_HK_IPO_WATCHLIST)
    parser.add_argument("--input-structural-signal", type=Path, default=DEFAULT_INPUT_STRUCTURAL_SIGNAL)
    parser.add_argument("--input-source-readiness", type=Path, default=DEFAULT_INPUT_SOURCE_READINESS)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return payload


def read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    if path.resolve() == DEFAULT_JSON_OUTPUT.resolve():
        reconcile_harness_current_health(reason="kimi_research_updated")


def write_progress(stage: str, **fields: Any) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": stage,
        **fields,
    }
    try:
        write_text(DEFAULT_PROGRESS_OUTPUT, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        return


def resolve_output_path(value: Any, default: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return default
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def sanitize_text(value: Any) -> str:
    text = compact_text(value)
    for old, new in SANITIZED_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text


def sanitize_for_model(value: Any, *, max_string_len: int = 180) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_for_model(item, max_string_len=max_string_len) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_for_model(item, max_string_len=max_string_len) for item in value]
    if isinstance(value, str):
        text = sanitize_text(value)
        return text[:max_string_len]
    return value


def safe_dossier_for_model(dossier: dict[str, Any]) -> dict[str, Any]:
    safe = sanitize_for_model(dossier, max_string_len=120)
    if not isinstance(safe, dict):
        return {}
    evidence_rows = []
    for row in dossier.get("evidence") or []:
        if not isinstance(row, dict):
            continue
        evidence_rows.append(
            {
                "id": str(row.get("id") or ""),
                "type": str(row.get("type") or ""),
                "text": f"{row.get('type') or 'evidence'} available",
            }
        )
    safe["evidence"] = evidence_rows[:7]
    safe["evidence_text_redacted"] = True
    return safe


def compact_discovery_diagnostics(*payloads: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
            source_results = (item.get("discovery") or {}).get("source_results") if isinstance(item.get("discovery"), dict) else []
            slow_sources = [
                {
                    "source_id": str(row.get("source_id") or ""),
                    "status": str(row.get("status") or ""),
                    "elapsed_seconds": float(row.get("elapsed_seconds") or 0.0),
                    "matched_items": int(row.get("matched_items") or 0),
                    "eligible_items": int(row.get("eligible_items") or 0),
                    "error": str(row.get("error") or "")[:160],
                }
                for row in (source_results or [])
                if isinstance(row, dict) and (float(row.get("elapsed_seconds") or 0.0) >= 3.0 or str(row.get("error") or ""))
            ]
            if not diagnostics and not slow_sources and not item.get("live_discovery_error"):
                continue
            rows.append(
                {
                    "name": str(item.get("radar_object_name") or item.get("name") or ""),
                    "status": str(item.get("status") or ""),
                    "lane": str(item.get("lane") or ""),
                    "live_discovery_error": str(item.get("live_discovery_error") or ""),
                    "total_elapsed_seconds": diagnostics.get("total_elapsed_seconds"),
                    "phase_timings_seconds": diagnostics.get("phase_timings_seconds") or {},
                    "slow_sources": slow_sources[:4],
                }
            )
    rows.sort(key=lambda row: float(row.get("total_elapsed_seconds") or 0.0), reverse=True)
    return rows[:limit]


def event_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for event in item.get("supporting_events") or []:
        if not isinstance(event, dict):
            continue
        for key in ("title", "headline", "summary", "event_type"):
            text = compact_text(event.get(key))
            if text:
                parts.append(text)
    parts.extend(compact_text(x) for x in (item.get("key_evidence") or []))
    parts.extend(compact_text(item.get(k)) for k in ("why_now", "confirmation_gap", "failure_mode"))
    return "；".join(part for part in parts if part)


def is_risk_event(item: dict[str, Any]) -> bool:
    text = event_text(item)
    return any(keyword in text for keyword in RISK_KEYWORDS)


def evidence_pack(item: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for idx, event in enumerate(item.get("supporting_events") or [], start=1):
        if not isinstance(event, dict):
            continue
        rows.append(
            {
                "id": f"event_{idx}",
                "type": "event",
                "text": summarize_event(event),
            }
        )
    for idx, evidence in enumerate(item.get("key_evidence") or [], start=1):
        text = compact_text(evidence)
        if text:
            rows.append({"id": f"evidence_{idx}", "type": "evidence", "text": text})
    price_context = item.get("price_context") or {}
    if isinstance(price_context, dict) and price_context:
        price_parts = []
        for key in ("as_of_date", "close", "daily_return", "amount_ratio_20", "positive_days_5d"):
            value = price_context.get(key)
            if value not in (None, ""):
                price_parts.append(f"{key}={value}")
        rows.append({"id": "price_1", "type": "price", "text": " / ".join(price_parts)})
    return rows[:7]


def hard_guardrails(item: dict[str, Any]) -> dict[str, Any]:
    precheck = opportunity_precheck(item)
    disqualifiers: list[str] = []
    max_decision = "promote"
    max_weight_delta = 40
    if str(item.get("radar_object_type") or "") == "company" and precheck.get("status") in {
        "priced_in",
        "missing_price",
        "partially_priced",
        "hot_price_reaction",
    }:
        disqualifiers.append(str(precheck.get("status") or "price_guardrail"))
        max_decision = "watch_only"
        max_weight_delta = -20 if precheck.get("status") == "hot_price_reaction" else -35
    if is_risk_event(item):
        disqualifiers.append("risk_event")
        max_decision = "risk_review"
        max_weight_delta = -60
    if str(item.get("radar_object_type") or "") == "industry":
        primary = [str(x).strip() for x in (item.get("primary_symbols") or []) if str(x).strip()]
        etfs = [str(x).strip() for x in (item.get("etf_proxies") or []) if str(x).strip()]
        if not primary or not etfs:
            disqualifiers.append("missing_representative_or_etf")
            max_decision = "research_gap"
            max_weight_delta = -30
    return {
        "opportunity_precheck": precheck,
        "disqualifiers": disqualifiers,
        "max_decision": max_decision,
        "max_weight_delta": max_weight_delta,
    }


def deterministic_verdict(dossier: dict[str, Any]) -> dict[str, Any]:
    guardrails = dossier.get("hard_guardrails") or {}
    disqualifiers = [str(x) for x in (guardrails.get("disqualifiers") or []) if str(x)]
    if "risk_event" in disqualifiers:
        decision = "risk_review"
        weight_delta = -60
    elif disqualifiers:
        decision = "watch_only" if guardrails.get("max_decision") == "watch_only" else "research_gap"
        weight_delta = int(guardrails.get("max_weight_delta") or -30)
    elif str(dossier.get("triage_action") or "") == "immediate_research":
        decision = "keep"
        weight_delta = 0
    elif str(dossier.get("radar_object_type") or "") == "industry":
        decision = "keep"
        weight_delta = 5
    else:
        decision = "watch_only"
        weight_delta = -10
    evidence_ids = [row.get("id") for row in (dossier.get("evidence") or []) if row.get("id")]
    return {
        "object_id": dossier.get("object_id") or "",
        "name": dossier.get("name") or "",
        "task": "opportunity_screen",
        "decision": decision,
        "weight_delta": weight_delta,
        "reason": guardrails.get("opportunity_precheck", {}).get("reason")
        or dossier.get("why_now")
        or "deterministic baseline",
        "evidence_ids": evidence_ids[:3],
        "next_action": {
            "when": "1-3个交易日",
            "check": (dossier.get("followup_path") or ["价格承接", "量能变化"])[:3],
        },
        "disqualifiers": disqualifiers,
        "confidence": 0.45,
        "provenance": DETERMINISTIC_PROVENANCE,
        "model_generated": False,
    }


def build_dossier(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_id": item.get("radar_object_id") or item.get("candidate_id") or item.get("radar_object_name") or "",
        "name": item.get("radar_object_name") or "",
        "radar_object_type": item.get("radar_object_type") or "",
        "radar_bucket": item.get("radar_bucket") or "",
        "triage_action": item.get("triage_action") or "",
        "radar_score": item.get("radar_score"),
        "rank_overall": item.get("rank_overall"),
        "catalyst_type": item.get("catalyst_type") or "",
        "hard_or_soft": item.get("hard_or_soft") or "",
        "why_now": item.get("why_now") or "",
        "confirmation_gap": item.get("confirmation_gap") or "",
        "failure_mode": item.get("failure_mode") or "",
        "primary_symbols": item.get("primary_symbols") or [],
        "etf_proxies": item.get("etf_proxies") or [],
        "followup_path": item.get("followup_path") or [],
        "price_context": item.get("price_context") or {},
        "evidence": evidence_pack(item),
        "hard_guardrails": hard_guardrails(item),
    }


def pick_candidate_items(snapshot: dict[str, Any], handoff: dict[str, Any], structural_signal: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    snapshot_by_name = {
        str(item.get("radar_object_name") or ""): item
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict) and str(item.get("radar_object_name") or "")
    }
    ordered: list[tuple[int, dict[str, Any]]] = []

    def add(item: Any, priority: int = 50) -> None:
        if not isinstance(item, dict):
            return
        key = str(item.get("radar_object_id") or item.get("candidate_id") or item.get("radar_object_name") or "")
        if not key or key in by_id:
            return
        by_id[key] = item
        guardrail = hard_guardrails(item)
        disq = set(str(x) for x in (guardrail.get("disqualifiers") or []))
        if disq:
            priority += 60
        if str(item.get("radar_object_type") or "") == "industry":
            priority -= 10
        if str(item.get("triage_action") or "") == "immediate_research":
            priority -= 20
        if str(item.get("triage_action") or "") == "risk_review":
            priority += 40
        try:
            rank = int(item.get("rank_overall") or 999)
        except (TypeError, ValueError):
            rank = 999
        ordered.append((priority * 1000 + rank, item))

    for row in structural_signal.get("items") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if name in snapshot_by_name:
            add(snapshot_by_name[name], priority=0)

    for item in handoff.get("priority_items") or []:
        add(item, priority=20)
    queues = handoff.get("queues") or {}
    if isinstance(queues, dict):
        for queue_name in ("immediate_research", "thesis_watch", "risk_review"):
            for item in queues.get(queue_name) or []:
                add(item, priority=30 if queue_name == "immediate_research" else 45)
    for item in snapshot.get("objects") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("triage_action") or "") in {"background_only"}:
            continue
        add(item, priority=60)
    return [item for _, item in sorted(ordered, key=lambda row: row[0])[:limit]]


def market_context(snapshot: dict[str, Any], source_readiness: dict[str, Any]) -> dict[str, Any]:
    market = source_readiness.get("market") if isinstance(source_readiness.get("market"), dict) else {}
    expected_sample_date = str(snapshot.get("expected_sample_date") or source_readiness.get("expected_sample_date") or "")
    market_sample_date = str(snapshot.get("market_sample_date") or snapshot.get("as_of_date") or "")
    latest_trade_date = str(market.get("latest_trade_date") or expected_sample_date or market_sample_date)
    return {
        "report_date": str(snapshot.get("report_date") or snapshot.get("as_of_date") or ""),
        "market_tz": str(snapshot.get("market_tz") or "Asia/Shanghai"),
        "market_sample_date": market_sample_date,
        "expected_sample_date": expected_sample_date,
        "latest_trade_date": latest_trade_date,
        "sample_is_expected": bool(expected_sample_date and market_sample_date == expected_sample_date),
        "market_status": str(market.get("status") or ""),
        "calendar_note": "若 sample_is_expected=true，说明价格主样本已对齐最近交易日；周末/节假日不要要求补非交易日收盘。",
    }


def build_prompt(
    *,
    dossiers: list[dict[str, Any]],
    ipo_watchlist: dict[str, Any],
    hk_ipo_watchlist: dict[str, Any],
    structural_signal: dict[str, Any],
    company_enrichment: dict[str, Any],
    news_verification: dict[str, Any],
    source_readiness: dict[str, Any],
    snapshot: dict[str, Any],
    safe_mode: bool = False,
) -> str:
    prompt_dossiers = dossiers
    prompt_ipo_watchlist = ipo_watchlist
    prompt_hk_ipo_watchlist = hk_ipo_watchlist
    prompt_structural_signal = structural_signal
    if safe_mode:
        prompt_dossiers = [safe_dossier_for_model(dossier) for dossier in dossiers]
        prompt_ipo_watchlist = {"items": compact_sidecar_items(ipo_watchlist, key="items", limit=3), "target_date": ipo_watchlist.get("target_date")}
        prompt_hk_ipo_watchlist = {"items": compact_sidecar_items(hk_ipo_watchlist, key="items", limit=3), "target_date": hk_ipo_watchlist.get("target_date")}
        prompt_structural_signal = {"items": compact_sidecar_items(structural_signal, key="items", limit=3), "method": structural_signal.get("method")}
        prompt_ipo_watchlist = sanitize_for_model(prompt_ipo_watchlist, max_string_len=100)
        prompt_hk_ipo_watchlist = sanitize_for_model(prompt_hk_ipo_watchlist, max_string_len=100)
        prompt_structural_signal = sanitize_for_model(prompt_structural_signal, max_string_len=100)
    context = {
        "task": "radar_kimi_research_harness_v1",
        "market_context": market_context(snapshot, source_readiness),
        "candidates": prompt_dossiers,
        "ipo_watchlist": {
            "items": compact_sidecar_items(prompt_ipo_watchlist, key="items", limit=8),
            "target_date": prompt_ipo_watchlist.get("target_date") if isinstance(prompt_ipo_watchlist, dict) else None,
        },
        "hk_ipo_watchlist": {
            "items": compact_sidecar_items(prompt_hk_ipo_watchlist, key="items", limit=8),
            "target_date": prompt_hk_ipo_watchlist.get("target_date") if isinstance(prompt_hk_ipo_watchlist, dict) else None,
            "source": prompt_hk_ipo_watchlist.get("source") if isinstance(prompt_hk_ipo_watchlist, dict) else None,
        },
        "structural_signal": {
            "items": compact_sidecar_items(prompt_structural_signal, key="items", limit=STRUCTURAL_REQUIRED_MIN),
            "method": prompt_structural_signal.get("method") if isinstance(prompt_structural_signal, dict) else None,
        },
        "discovery_diagnostics": compact_discovery_diagnostics(company_enrichment, news_verification),
    }
    opening = (
        "你是晨报研究质检器。只基于 JSON context 给研究分流 verdict，不输出正文；weight_delta 只是报告版面优先级。\n"
        if safe_mode
        else "你是事件驱动基金的受控研究员。只基于 JSON context 给 verdict，不写正文，不给交易指令。\n"
    )
    return (
        opening
        + "返回严格 JSON object："
        '{"summary":"","verdicts":[{"object_id":"","name":"","task":"opportunity_screen","decision":"promote|keep|watch_only|reject|risk_review|research_gap","weight_delta":0,"reason":"","evidence_ids":["event_1"],"next_action":{"when":"1-3个交易日","check":["具体指标"]},"disqualifiers":[],"confidence":0.0}],"ipo_verdicts":[{"object_id":"","name":"","task":"ipo_subscription_screen","decision":"promote|keep|watch_only|reject|research_gap","weight_delta":0,"reason":"","evidence_ids":["event_1"],"next_action":{"when":"申购期内","check":["具体指标"]},"disqualifiers":[],"confidence":0.0}],"structural_verdicts":[{"object_id":"","name":"","task":"structural_signal_screen","decision":"promote|keep|watch_only|reject|research_gap","weight_delta":0,"reason":"","structural_thesis":"","transmission_chain":["征兆->产业链->标的/ETF"],"watch_window":"30-120d","evidence":["具体证据"],"next_action":{"when":"30-120d","check":["具体指标"]},"disqualifiers":[],"confidence":0.0}],"risk_flags":[]}\n'
        "硬约束：\n"
        "1. 每个 candidate 都有 hard_guardrails；如果 max_decision 不是 promote，你不得给出更高决策，weight_delta 不得高于 max_weight_delta。\n"
        "2. 纯财报且 priced_in / missing_price，只能 watch_only 或 reject；awaiting_first_trade 是系统自动补齐项，不得仅因首日尚未产生而降权。\n"
        "3. 风险事件只能 risk_review 或 reject，不能 promote/keep。\n"
        "4. 行业机会没有代表股或 ETF，只能 research_gap。\n"
        "5. 若 market_context.sample_is_expected=true，不得因为 report_date 晚于 market_sample_date 就判定价格系统滞后；只能要求下一交易日承接观察。\n"
        "6. 需要确认不等于 watch_only。只要无硬性 disqualifier、证据可验证、代表股/ETF 明确，应给 keep；催化强且未定价可 promote。\n"
        "7. next_action.check 必须是具体指标或文件，不准写“继续关注/核实真实性/看是否确认”。\n"
        "8. 若 discovery_diagnostics 显示源超时、源失败或阶段耗时异常，必须基于具体 source_id/phase 判断原因，不准泛泛写“人工核实”。\n"
        "8. evidence_ids 必须引用 dossier.evidence 里的 id。\n"
        "9. 新股资料缺发售价、基石、公开发售倍数时，只能 priority_screen/research_gap，不给参与判断。\n"
        "10. weight_delta 是报告版面优先级：promote=+15到+40，keep=0到+15，watch_only=-10到-25，reject/risk/research_gap 必须为负。\n"
        "11. reason 最多 40 个汉字；每个 next_action.check 最多 4 项。\n\n"
        "12. 如果 JSON context.structural_signal.items 非空，structural_verdicts 必须覆盖至少前 3 条；candidates 为空时不要把结构性结论写进 verdicts。\n"
        "13. structural_verdicts.transmission_chain 必须写清“早期征兆 -> 产业链传导 -> 代表股/ETF 或需验证变量”，不准只复述事件标题。\n"
        "14. 如果 JSON context.ipo_watchlist.items 或 hk_ipo_watchlist.items 非空，ipo_verdicts 必须列出达标与不达标公司；已过申购窗口只能 reject/watch_only。\n\n"
        "JSON context:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def build_structural_repair_prompt(
    *,
    structural_signal: dict[str, Any],
    source_readiness: dict[str, Any],
    snapshot: dict[str, Any],
    safe_mode: bool = False,
) -> str:
    prompt_structural_signal = sanitize_for_model(
        {
            "items": compact_sidecar_items(structural_signal, key="items", limit=STRUCTURAL_REQUIRED_MIN),
            "method": structural_signal.get("method"),
        },
        max_string_len=180,
    )
    if safe_mode:
        prompt_structural_signal = {
            "items": compact_sidecar_items(structural_signal, key="items", limit=STRUCTURAL_REQUIRED_MIN),
            "method": structural_signal.get("method"),
        }
        prompt_structural_signal = sanitize_for_model(prompt_structural_signal, max_string_len=140)
    context = {
        "task": "radar_structural_signal_repair",
        "market_context": market_context(snapshot, source_readiness),
        "structural_signal": {
            "items": compact_sidecar_items(prompt_structural_signal, key="items", limit=STRUCTURAL_REQUIRED_MIN),
            "method": prompt_structural_signal.get("method") if isinstance(prompt_structural_signal, dict) else None,
        },
    }
    return (
        "你是事件驱动基金的结构性线索研究员。只基于 JSON context 补齐 structural_verdicts，不写正文。\n"
        "返回严格 JSON object："
        '{"summary":"","verdicts":[],"ipo_verdicts":[],"structural_verdicts":[{"object_id":"","name":"","task":"structural_signal_screen","decision":"promote|keep|watch_only|reject|research_gap","weight_delta":0,"reason":"","structural_thesis":"","transmission_chain":["早期征兆->产业链传导->代表股/ETF或验证变量"],"watch_window":"30-120d","evidence":["具体证据"],"next_action":{"when":"30-120d","check":["具体指标"]},"disqualifiers":[],"confidence":0.0}],"risk_flags":[]}\n'
        "硬约束：\n"
        "1. structural_signal.items 非空时，structural_verdicts 不能为空，至少覆盖前 3 条。\n"
        "2. object_id 使用 radar_object_id；name 使用原 name。\n"
        "3. 只出现政策/舆情、缺少订单/价格/财务二次确认时，不得 promote；可 keep 或 watch_only。\n"
        "4. transmission_chain 必须写清“征兆 -> 产业链环节 -> 代表股/ETF/验证变量”，不能只复述 thesis。\n"
        "5. next_action.check 必须是可执行指标，例如订单、价格、销量、资金、公告、ETF 成交额，不准写继续关注/核实真实性。\n"
        "6. reason 最多 40 个汉字；weight_delta 遵守 promote=+15到+40，keep=0到+15，watch_only=-10到-25，reject/research_gap 为负。\n\n"
        "JSON context:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def build_ipo_repair_prompt(
    *,
    ipo_watchlist: dict[str, Any],
    hk_ipo_watchlist: dict[str, Any],
    source_readiness: dict[str, Any],
    snapshot: dict[str, Any],
    safe_mode: bool = False,
) -> str:
    prompt_ipo_watchlist = ipo_watchlist
    prompt_hk_ipo_watchlist = hk_ipo_watchlist
    if safe_mode:
        prompt_ipo_watchlist = {"items": compact_sidecar_items(ipo_watchlist, key="items", limit=IPO_VERDICT_LIMIT), "target_date": ipo_watchlist.get("target_date")}
        prompt_hk_ipo_watchlist = {"items": compact_sidecar_items(hk_ipo_watchlist, key="items", limit=IPO_VERDICT_LIMIT), "target_date": hk_ipo_watchlist.get("target_date")}
        prompt_ipo_watchlist = sanitize_for_model(prompt_ipo_watchlist, max_string_len=160)
        prompt_hk_ipo_watchlist = sanitize_for_model(prompt_hk_ipo_watchlist, max_string_len=160)
    context = {
        "task": "radar_ipo_verdict_repair",
        "market_context": market_context(snapshot, source_readiness),
        "ipo_watchlist": {
            "items": compact_sidecar_items(prompt_ipo_watchlist, key="items", limit=IPO_VERDICT_LIMIT),
            "target_date": prompt_ipo_watchlist.get("target_date") if isinstance(prompt_ipo_watchlist, dict) else None,
        },
        "hk_ipo_watchlist": {
            "items": compact_sidecar_items(prompt_hk_ipo_watchlist, key="items", limit=IPO_VERDICT_LIMIT),
            "target_date": prompt_hk_ipo_watchlist.get("target_date") if isinstance(prompt_hk_ipo_watchlist, dict) else None,
            "source": prompt_hk_ipo_watchlist.get("source") if isinstance(prompt_hk_ipo_watchlist, dict) else None,
        },
    }
    return (
        "你是港股/A股 IPO 打新预检研究员。只基于 JSON context 补齐 ipo_verdicts，不写正文。\n"
        "返回严格 JSON object："
        '{"summary":"","verdicts":[],"ipo_verdicts":[{"object_id":"","name":"","task":"ipo_subscription_screen","decision":"promote|keep|watch_only|reject|research_gap","weight_delta":0,"reason":"","evidence_ids":["event_1"],"next_action":{"when":"申购期内","check":["具体指标"]},"disqualifiers":[],"confidence":0.0}],"structural_verdicts":[],"risk_flags":[]}\n'
        "硬约束：\n"
        f"1. ipo_watchlist.items 与 hk_ipo_watchlist.items 合计非空时，ipo_verdicts 必须覆盖前 {IPO_VERDICT_LIMIT} 条以内的全部公司/ETF；object_id 优先使用 code。\n"
        "2. 已过申购窗口只能 reject 或 watch_only；缺发售价、招股书、保荐人或认购拥挤度只能 research_gap/watch_only。\n"
        "3. 必须列出达标与不达标对象，不准只列可申购对象。\n"
        "4. next_action.check 必须是打新决策指标，例如公开认购倍数、孖展倍数、定价区间、保荐人战绩、基石比例、可比估值。\n"
        "5. reason 最多 40 个汉字；reject 权重 <= -40，research_gap <= -20，watch_only <= -5。\n\n"
        "JSON context:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def verdict_by_object(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = [item for item in (payload.get("verdicts") or []) if isinstance(item, dict)]
    result: dict[str, dict[str, Any]] = {}
    for item in rows:
        object_id = str(item.get("object_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if object_id:
            result[object_id] = item
        if name:
            result[name] = item
    return result


def allowed_decision_rank(decision: str) -> int:
    ranks = {"reject": 0, "risk_review": 1, "research_gap": 2, "watch_only": 3, "keep": 4, "promote": 5}
    return ranks.get(decision, 3)


def clamp_decision(decision: str, max_decision: str) -> str:
    if allowed_decision_rank(decision) <= allowed_decision_rank(max_decision):
        return decision
    return max_decision if max_decision in DECISIONS else "watch_only"


def normalize_action(action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        return {"when": "1-3个交易日", "check": ["价格承接", "量能变化"]}
    when = compact_text(action.get("when")) or "1-3个交易日"
    when = when.replace("1-3 trading days", "1-3个交易日").replace("trading days", "个交易日")
    checks = [compact_text(x) for x in (action.get("check") or []) if compact_text(x)] if isinstance(action.get("check"), list) else []
    checks = [x for x in checks if not any(pattern in x for pattern in SHALLOW_ACTION_PATTERNS)]
    if not checks:
        checks = ["价格承接", "量能变化"]
    return {"when": when, "check": checks[:4]}


def parse_risk_flag_candidate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = compact_text(value)
    if not text.startswith("{") or not text.endswith("}"):
        return value
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return value
    return parsed if isinstance(parsed, dict) else value


def string_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        return [compact_text(item) for item in value if compact_text(item)][:limit]
    text = compact_text(value)
    return [text] if text else []


def default_risk_action(source_id: str = "") -> dict[str, Any]:
    checks = (
        [f"拉取 {source_id} 最近3次 collector 响应码/错误日志", f"复跑 {source_id} 关联对象 discovery 并记录 eligible/matched counts"]
        if source_id
        else ["补齐 risk_flag.source_id 或失败节点名称", "列出受影响对象、缺失字段和对应 artifact path"]
    )
    return {"when": "1-3个交易日", "check": checks}


def normalize_risk_flag(raw: Any, *, provenance: str = MODEL_PROVENANCE) -> dict[str, Any] | None:
    candidate = parse_risk_flag_candidate(raw)
    if isinstance(candidate, dict):
        embedded_detail = parse_risk_flag_candidate(candidate.get("detail"))
        if isinstance(embedded_detail, dict):
            merged = dict(embedded_detail)
            for key, value in candidate.items():
                if key == "detail":
                    continue
                if value not in (None, "", [], {}):
                    merged[key] = value
            candidate = merged
        flag_type = compact_text(candidate.get("flag_type") or candidate.get("type") or candidate.get("category")) or "model_risk"
        source_id = compact_text(candidate.get("source_id") or candidate.get("source") or "")
        affected_names = string_list(
            candidate.get("affected_names")
            or candidate.get("affected_lanes")
            or candidate.get("affected_candidates")
            or candidate.get("affected_items")
            or candidate.get("names")
            or candidate.get("affected"),
            limit=12,
        )
        detail = (
            compact_text(candidate.get("detail"))
            or compact_text(candidate.get("reason"))
            or compact_text(candidate.get("message"))
            or compact_text(candidate.get("description"))
            or "；".join(
                item
                for item in (
                    f"影响：{compact_text(candidate.get('impact'))}" if compact_text(candidate.get("impact")) else "",
                    f"修复：{compact_text(candidate.get('remediation'))}" if compact_text(candidate.get("remediation")) else "",
                    f"缓释：{compact_text(candidate.get('mitigation'))}" if compact_text(candidate.get("mitigation")) else "",
                    compact_text(candidate.get("error_message")),
                )
                if item
            )
            or compact_text(candidate)
        )
        raw_action = candidate.get("next_action") or candidate.get("action")
        if not raw_action and (compact_text(candidate.get("remediation")) or compact_text(candidate.get("mitigation"))):
            raw_action = {
                "when": "1-3个交易日",
                "check": [compact_text(candidate.get("remediation")) or compact_text(candidate.get("mitigation"))],
            }
        next_action = normalize_action(raw_action) if raw_action else default_risk_action(source_id)
        checks = [str(item) for item in (next_action.get("check") or [])]
        if checks and set(checks).issubset({"价格承接", "量能变化"}):
            next_action = default_risk_action(source_id)
        row_provenance = compact_text(candidate.get("provenance")) or provenance
    else:
        detail = compact_text(candidate)
        if not detail:
            return None
        flag_type = "model_risk"
        source_id = ""
        affected_names = []
        next_action = default_risk_action()
        row_provenance = provenance
    model_generated = row_provenance == MODEL_PROVENANCE
    return {
        "flag_type": flag_type,
        "source_id": source_id,
        "affected_names": affected_names,
        "detail": detail,
        "next_action": next_action,
        "provenance": row_provenance,
        "model_generated": model_generated,
    }


def normalize_risk_flags(raw_flags: Any, *, provenance: str = MODEL_PROVENANCE, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    flags = raw_flags if isinstance(raw_flags, list) else [raw_flags]
    for raw in flags:
        row = normalize_risk_flag(raw, provenance=provenance)
        if not row:
            continue
        key = "|".join([str(row.get("flag_type") or ""), str(row.get("source_id") or ""), str(row.get("detail") or "")[:120]])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def normalize_verdict(raw: dict[str, Any], dossier: dict[str, Any], *, provenance: str) -> dict[str, Any]:
    baseline = deterministic_verdict(dossier)
    guardrails = dossier.get("hard_guardrails") or {}
    decision = str(raw.get("decision") or baseline["decision"]).strip()
    if decision not in DECISIONS:
        decision = baseline["decision"]
    decision = clamp_decision(decision, str(guardrails.get("max_decision") or "promote"))
    try:
        weight_delta = int(raw.get("weight_delta"))
    except (TypeError, ValueError):
        weight_delta = int(baseline.get("weight_delta") or 0)
    max_weight_delta = int(guardrails.get("max_weight_delta") if guardrails.get("max_weight_delta") is not None else 40)
    weight_delta = min(weight_delta, max_weight_delta)
    if decision == "promote":
        weight_delta = max(weight_delta, 15)
    elif decision == "keep":
        weight_delta = max(weight_delta, 0)
    elif decision == "watch_only":
        weight_delta = min(weight_delta, -10)
    elif decision == "risk_review":
        weight_delta = min(weight_delta, -40)
    elif decision == "research_gap":
        weight_delta = min(weight_delta, -30)
    elif decision == "reject":
        weight_delta = min(weight_delta, -50)
    weight_delta = min(weight_delta, max_weight_delta)
    evidence_id_set = {str(row.get("id")) for row in (dossier.get("evidence") or []) if row.get("id")}
    raw_ids = raw.get("evidence_ids") if isinstance(raw.get("evidence_ids"), list) else []
    evidence_ids = [str(x) for x in raw_ids if str(x) in evidence_id_set]
    if not evidence_ids:
        evidence_ids = [str(x) for x in baseline.get("evidence_ids", []) if str(x) in evidence_id_set]
    disqualifiers = sorted(
        set(str(x) for x in (guardrails.get("disqualifiers") or []) if str(x))
        | set(str(x) for x in (raw.get("disqualifiers") or []) if str(x))
    )
    confidence = raw.get("confidence", baseline.get("confidence", 0.5))
    try:
        confidence_float = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_float = 0.5
    model_generated = provenance == MODEL_PROVENANCE
    return {
        "object_id": dossier.get("object_id") or "",
        "name": dossier.get("name") or "",
        "task": "opportunity_screen",
        "decision": decision,
        "weight_delta": weight_delta,
        "reason": compact_text(raw.get("reason")) or baseline.get("reason") or "",
        "evidence_ids": evidence_ids[:5],
        "next_action": normalize_action(raw.get("next_action") or baseline.get("next_action")),
        "disqualifiers": disqualifiers,
        "confidence": confidence_float,
        "provenance": provenance,
        "model_generated": model_generated,
    }


def normalize_sidecar_verdict(raw: dict[str, Any], *, provenance: str = MODEL_PROVENANCE) -> dict[str, Any]:
    row = dict(raw)
    row.setdefault("provenance", provenance)
    row.setdefault("model_generated", provenance == MODEL_PROVENANCE)
    decision = str(row.get("decision") or "").strip()
    if "weight_delta" in row:
        try:
            row["weight_delta"] = int(float(row.get("weight_delta") or 0))
        except (TypeError, ValueError):
            row["weight_delta"] = 0
    else:
        row["weight_delta"] = 0
    if decision == "promote":
        row["weight_delta"] = max(int(row.get("weight_delta") or 0), 10)
    elif decision == "keep":
        row["weight_delta"] = max(int(row.get("weight_delta") or 0), 0)
    elif decision == "watch_only":
        row["weight_delta"] = min(int(row.get("weight_delta") or 0), -5)
    elif decision == "research_gap":
        row["weight_delta"] = min(int(row.get("weight_delta") or 0), -20)
    elif decision == "reject":
        row["weight_delta"] = min(int(row.get("weight_delta") or 0), -40)
    if "reason" in row:
        row["reason"] = compact_text(row.get("reason"))
    if "next_action" in row:
        row["next_action"] = normalize_action(row.get("next_action"))
    if "disqualifiers" in row and not isinstance(row.get("disqualifiers"), list):
        row["disqualifiers"] = [compact_text(row.get("disqualifiers"))] if compact_text(row.get("disqualifiers")) else []
    if not row.get("evidence") and isinstance(row.get("evidence_ids"), list):
        evidence = [compact_text(item) for item in (row.get("evidence_ids") or []) if compact_text(item)]
        if evidence:
            row["evidence"] = evidence[:5]
    if "confidence" in row:
        try:
            row["confidence"] = max(0.0, min(1.0, float(row.get("confidence"))))
        except (TypeError, ValueError):
            row["confidence"] = 0.5
    return row


def normalize_payload(raw_payload: dict[str, Any], dossiers: list[dict[str, Any]]) -> dict[str, Any]:
    raw_by_id = verdict_by_object(raw_payload)
    verdicts = []
    missing_model_verdicts = []
    for dossier in dossiers:
        raw = raw_by_id.get(str(dossier.get("object_id") or "")) or raw_by_id.get(str(dossier.get("name") or ""))
        if raw:
            verdicts.append(normalize_verdict(raw, dossier, provenance=MODEL_PROVENANCE))
        else:
            missing_model_verdicts.append(str(dossier.get("name") or dossier.get("object_id") or ""))
            verdicts.append(normalize_verdict({}, dossier, provenance=MISSING_MODEL_PROVENANCE))
    ipo_rows = [normalize_sidecar_verdict(item) for item in (raw_payload.get("ipo_verdicts") or []) if isinstance(item, dict)]
    structural_rows = [normalize_sidecar_verdict(item) for item in (raw_payload.get("structural_verdicts") or []) if isinstance(item, dict)]
    risk_flags = normalize_risk_flags(raw_payload.get("risk_flags") or [], limit=3)
    model_generated_count = sum(1 for item in verdicts if item.get("model_generated") is True)
    coverage_ratio = (model_generated_count / len(dossiers)) if dossiers else 1.0
    return {
        "summary": compact_text(raw_payload.get("summary")),
        "verdicts": verdicts,
        "ipo_verdicts": ipo_rows[:IPO_VERDICT_LIMIT],
        "structural_verdicts": structural_rows[:STRUCTURAL_VERDICT_LIMIT],
        "risk_flags": risk_flags[:3],
        "model_coverage": {
            "expected_count": len(dossiers),
            "model_generated_count": model_generated_count,
            "fallback_count": max(len(dossiers) - model_generated_count, 0),
            "coverage_ratio": round(coverage_ratio, 4),
        },
        "missing_model_verdicts": missing_model_verdicts[:12],
    }


def deterministic_payload(dossiers: list[dict[str, Any]], *, summary: str = "deterministic fallback") -> dict[str, Any]:
    verdicts = [deterministic_verdict(dossier) for dossier in dossiers]
    return {
        "summary": summary,
        "verdicts": verdicts,
        "ipo_verdicts": [],
        "structural_verdicts": [],
        "risk_flags": [],
        "model_coverage": {
            "expected_count": len(dossiers),
            "model_generated_count": 0,
            "fallback_count": len(dossiers),
            "coverage_ratio": 0.0 if dossiers else 1.0,
        },
        "missing_model_verdicts": [str(dossier.get("name") or dossier.get("object_id") or "") for dossier in dossiers][:12],
    }


def sidecar_has_items(payload: dict[str, Any]) -> bool:
    return any(isinstance(item, dict) for item in (payload.get("items") or []))


def normalize_sidecar_key(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    upper = text.upper()
    match = re.fullmatch(r"(\d{4,6})\.(HK|SZ|SH|BJ|SS)", upper)
    if match:
        digits, market = match.groups()
        return digits.zfill(5) if market == "HK" else digits.zfill(6)
    if re.fullmatch(r"\d{4}", text):
        return text.zfill(5)
    return text


def sidecar_item_key(item: dict[str, Any]) -> str:
    for key in ("object_id", "radar_object_id", "code", "ticker", "name", "short_name_cn", "display_name_cn"):
        value = normalize_sidecar_key(item.get(key))
        if value:
            return value
    return ""


def sidecar_verdict_key(item: dict[str, Any]) -> str:
    for key in ("object_id", "code", "name"):
        value = normalize_sidecar_key(item.get(key))
        if value:
            return value
    return ""


def sidecar_required_keys(payloads: list[dict[str, Any]], *, limit: int) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for payload in payloads:
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = sidecar_item_key(item)
            if key and key not in seen:
                keys.append(key)
                seen.add(key)
            if len(keys) >= limit:
                return keys
    return keys


def coverage_for_sidecar(required_keys: list[str], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    covered: list[str] = []
    seen: set[str] = set()
    required_set = set(required_keys)
    for row in verdicts:
        if not isinstance(row, dict) or row.get("model_generated") is not True:
            continue
        key = sidecar_verdict_key(row)
        if required_set and key not in required_set:
            continue
        if key and key not in seen:
            covered.append(key)
            seen.add(key)
    missing = [key for key in required_keys if key not in seen]
    required_count = len(required_keys)
    return {
        "required_count": required_count,
        "model_generated_count": len(covered),
        "coverage_ratio": round((len(covered) / required_count) if required_count else 1.0, 4),
        "required_keys": required_keys,
        "covered_keys": covered[: max(len(required_keys), 1)],
        "missing_required_keys": missing,
    }


def dedupe_sidecar_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    order: list[str] = []
    by_key: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        key = sidecar_verdict_key(row) or f"row_{idx}"
        if key not in by_key:
            order.append(key)
        by_key[key] = row
    return [by_key[key] for key in order[:limit]]


def with_sidecar_coverage(
    normalized: dict[str, Any],
    *,
    ipo_watchlist: dict[str, Any],
    hk_ipo_watchlist: dict[str, Any],
    structural_signal: dict[str, Any],
) -> dict[str, Any]:
    result = dict(normalized)
    ipo_available = len([item for payload in (ipo_watchlist, hk_ipo_watchlist) for item in (payload.get("items") or []) if isinstance(item, dict)])
    structural_available = len([item for item in (structural_signal.get("items") or []) if isinstance(item, dict)])
    ipo_required = sidecar_required_keys([ipo_watchlist, hk_ipo_watchlist], limit=min(IPO_VERDICT_LIMIT, ipo_available))
    structural_required = sidecar_required_keys([structural_signal], limit=min(STRUCTURAL_REQUIRED_MIN, structural_available))
    result["sidecar_coverage"] = {
        "ipo": {
            "available_count": ipo_available,
            **coverage_for_sidecar(ipo_required, [item for item in (result.get("ipo_verdicts") or []) if isinstance(item, dict)]),
        },
        "structural": {
            "available_count": structural_available,
            **coverage_for_sidecar(structural_required, [item for item in (result.get("structural_verdicts") or []) if isinstance(item, dict)]),
        },
    }
    return result


def sidecar_coverage_gaps(normalized: dict[str, Any]) -> list[str]:
    coverage = normalized.get("sidecar_coverage") if isinstance(normalized.get("sidecar_coverage"), dict) else {}
    gaps: list[str] = []
    for key in ("ipo", "structural"):
        row = coverage.get(key) if isinstance(coverage.get(key), dict) else {}
        missing = [str(item) for item in (row.get("missing_required_keys") or []) if str(item)]
        if missing:
            gaps.append(f"{key}_coverage={row.get('model_generated_count', 0)}/{row.get('required_count', 0)} missing={','.join(missing[:3])}")
    return gaps


def merge_normalized_payloads(parts: list[dict[str, Any]], dossiers: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts: list[dict[str, Any]] = []
    ipo_verdicts: list[dict[str, Any]] = []
    structural_verdicts: list[dict[str, Any]] = []
    risk_flags: list[dict[str, Any]] = []
    summaries: list[str] = []
    for part in parts:
        verdicts.extend(item for item in (part.get("verdicts") or []) if isinstance(item, dict))
        ipo_verdicts.extend(item for item in (part.get("ipo_verdicts") or []) if isinstance(item, dict))
        structural_verdicts.extend(item for item in (part.get("structural_verdicts") or []) if isinstance(item, dict))
        risk_flags.extend(normalize_risk_flags(part.get("risk_flags") or [], limit=3))
        if compact_text(part.get("summary")):
            summaries.append(compact_text(part.get("summary")))
    model_generated_count = sum(1 for item in verdicts if item.get("model_generated") is True)
    fallback_count = max(len(dossiers) - model_generated_count, 0)
    return {
        "summary": "；".join(summaries[:3]),
        "verdicts": verdicts,
        "ipo_verdicts": dedupe_sidecar_rows(ipo_verdicts, limit=IPO_VERDICT_LIMIT),
        "structural_verdicts": dedupe_sidecar_rows(structural_verdicts, limit=STRUCTURAL_VERDICT_LIMIT),
        "risk_flags": normalize_risk_flags(risk_flags, limit=3),
        "model_coverage": {
            "expected_count": len(dossiers),
            "model_generated_count": model_generated_count,
            "fallback_count": fallback_count,
            "coverage_ratio": round((model_generated_count / len(dossiers)) if dossiers else 1.0, 4),
        },
        "missing_model_verdicts": [
            str(item.get("name") or item.get("object_id") or "")
            for item in verdicts
            if isinstance(item, dict) and item.get("model_generated") is not True
        ][:12],
    }


def call_planned_model_shards(
    *,
    credential_candidates: list[dict[str, Any]],
    dossiers: list[dict[str, Any]],
    ipo_watchlist: dict[str, Any],
    hk_ipo_watchlist: dict[str, Any],
    structural_signal: dict[str, Any],
    company_enrichment: dict[str, Any],
    news_verification: dict[str, Any],
    source_readiness: dict[str, Any],
    snapshot: dict[str, Any],
    max_tokens: int,
    timeout_seconds: float,
    retry_attempts: int,
    retry_timeout_seconds: float | None,
    batch_size: int,
) -> tuple[dict[str, Any], dict[str, Any], str, list[str]]:
    parts: list[dict[str, Any]] = []
    recoveries: list[str] = []
    errors: list[str] = []
    credential_warnings: list[str] = []
    used_credential: dict[str, Any] = {}
    batch_size = max(1, batch_size)
    total_shards = len(range(0, len(dossiers), batch_size))
    write_progress("planned_start", candidate_count=len(dossiers), batch_size=batch_size, planned_shards=total_shards)
    for offset in range(0, len(dossiers), batch_size):
        batch = dossiers[offset : offset + batch_size]
        shard_index = offset // batch_size + 1
        write_progress("candidate_shard_start", shard_index=shard_index, planned_shards=total_shards, batch_size=len(batch))
        prompt = build_prompt(
            dossiers=batch,
            ipo_watchlist={},
            hk_ipo_watchlist={},
            structural_signal={},
            company_enrichment=company_enrichment,
            news_verification=news_verification,
            source_readiness=source_readiness,
            snapshot=snapshot,
        )
        try:
            raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                candidates=credential_candidates,
                prompt=prompt,
                max_tokens=min(max_tokens, 1800),
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
                retry_timeout_seconds=retry_timeout_seconds,
            )
            credential_warnings.extend(credential_errors[:1])
            write_progress("candidate_shard_pass", shard_index=shard_index, planned_shards=total_shards, batch_size=len(batch))
        except Exception as exc:  # noqa: BLE001
            safe_prompt = build_prompt(
                dossiers=batch,
                ipo_watchlist={},
                hk_ipo_watchlist={},
                structural_signal={},
                company_enrichment=company_enrichment,
                news_verification=news_verification,
                source_readiness=source_readiness,
                snapshot=snapshot,
                safe_mode=True,
            )
            try:
                raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                    candidates=credential_candidates,
                    prompt=safe_prompt,
                    max_tokens=min(max_tokens, 1400),
                    timeout_seconds=timeout_seconds,
                    retry_attempts=retry_attempts,
                    retry_timeout_seconds=retry_timeout_seconds,
                )
                credential_warnings.extend(credential_errors[:1])
                recoveries.append(f"candidate_shard_{offset // batch_size + 1}")
                write_progress("candidate_shard_recovered", shard_index=shard_index, planned_shards=total_shards, batch_size=len(batch))
            except Exception as retry_exc:  # noqa: BLE001
                if len(batch) > 4:
                    errors.append(f"shard_{offset // batch_size + 1}_split_after={type(retry_exc).__name__}:{str(retry_exc)[:100]}")
                    for mini_offset in range(0, len(batch), 4):
                        mini_batch = batch[mini_offset : mini_offset + 4]
                        mini_prompt = build_prompt(
                            dossiers=mini_batch,
                            ipo_watchlist={},
                            hk_ipo_watchlist={},
                            structural_signal={},
                            company_enrichment=company_enrichment,
                            news_verification=news_verification,
                            source_readiness=source_readiness,
                            snapshot=snapshot,
                            safe_mode=True,
                        )
                        try:
                            mini_raw, used_credential, credential_errors = call_kimi_json_with_candidates(
                                candidates=credential_candidates,
                                prompt=mini_prompt,
                                max_tokens=min(max_tokens, 1200),
                                timeout_seconds=timeout_seconds,
                                retry_attempts=retry_attempts,
                                retry_timeout_seconds=retry_timeout_seconds,
                            )
                            credential_warnings.extend(credential_errors[:1])
                            parts.append(normalize_payload(mini_raw, mini_batch))
                            recoveries.append(f"candidate_subshard_{offset // batch_size + 1}_{mini_offset // 4 + 1}")
                            write_progress(
                                "candidate_subshard_pass",
                                shard_index=shard_index,
                                subshard_index=mini_offset // 4 + 1,
                                batch_size=len(mini_batch),
                            )
                        except Exception as mini_exc:  # noqa: BLE001
                            errors.append(
                                f"subshard_{offset // batch_size + 1}_{mini_offset // 4 + 1}:"
                                f"{type(mini_exc).__name__}:{str(mini_exc)[:120]}"
                            )
                            parts.append(
                                deterministic_payload(
                                    mini_batch,
                                    summary=f"deterministic subshard fallback {offset // batch_size + 1}.{mini_offset // 4 + 1}",
                                )
                            )
                            write_progress(
                                "candidate_subshard_fallback",
                                shard_index=shard_index,
                                subshard_index=mini_offset // 4 + 1,
                                batch_size=len(mini_batch),
                                error_type=type(mini_exc).__name__,
                            )
                    continue
                errors.append(f"shard_{offset // batch_size + 1}:{type(retry_exc).__name__}:{str(retry_exc)[:140]}")
                parts.append(deterministic_payload(batch, summary=f"deterministic shard fallback {offset // batch_size + 1}"))
                write_progress("candidate_shard_fallback", shard_index=shard_index, planned_shards=total_shards, batch_size=len(batch), error_type=type(retry_exc).__name__)
                continue
        parts.append(normalize_payload(raw_payload, batch))

    sidecar_jobs: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], int]] = []
    if sidecar_has_items(ipo_watchlist) or sidecar_has_items(hk_ipo_watchlist):
        sidecar_jobs.append(("ipo", ipo_watchlist, hk_ipo_watchlist, {}, 1400))
    if sidecar_has_items(structural_signal):
        compact_structural_signal = sanitize_for_model(
            {
                "items": compact_sidecar_items(structural_signal, key="items", limit=STRUCTURAL_REQUIRED_MIN),
                "method": structural_signal.get("method"),
            },
            max_string_len=180,
        )
        sidecar_jobs.append(("structural", {}, {}, compact_structural_signal, 1000))

    for label, job_ipo_watchlist, job_hk_ipo_watchlist, job_structural_signal, job_max_tokens in sidecar_jobs:
        write_progress("sidecar_start", sidecar=label, sidecar_count=len(sidecar_jobs))
        sidecar_prompt = build_prompt(
            dossiers=[],
            ipo_watchlist=job_ipo_watchlist,
            hk_ipo_watchlist=job_hk_ipo_watchlist,
            structural_signal=job_structural_signal,
            company_enrichment=company_enrichment,
            news_verification=news_verification,
            source_readiness=source_readiness,
            snapshot=snapshot,
        )
        try:
            raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                candidates=credential_candidates,
                prompt=sidecar_prompt,
                max_tokens=min(max_tokens, job_max_tokens),
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
                retry_timeout_seconds=retry_timeout_seconds,
            )
            credential_warnings.extend(credential_errors[:1])
            write_progress("sidecar_pass", sidecar=label, sidecar_count=len(sidecar_jobs))
        except Exception as exc:  # noqa: BLE001
            safe_sidecar_prompt = build_prompt(
                dossiers=[],
                ipo_watchlist=job_ipo_watchlist,
                hk_ipo_watchlist=job_hk_ipo_watchlist,
                structural_signal=job_structural_signal,
                company_enrichment=company_enrichment,
                news_verification=news_verification,
                source_readiness=source_readiness,
                snapshot=snapshot,
                safe_mode=True,
            )
            try:
                raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                    candidates=credential_candidates,
                    prompt=safe_sidecar_prompt,
                    max_tokens=min(max_tokens, max(900, job_max_tokens - 200)),
                    timeout_seconds=timeout_seconds,
                    retry_attempts=retry_attempts,
                    retry_timeout_seconds=retry_timeout_seconds,
                )
                credential_warnings.extend(credential_errors[:1])
                recoveries.append(f"{label}_sidecar")
                write_progress("sidecar_recovered", sidecar=label, sidecar_count=len(sidecar_jobs))
            except Exception as retry_exc:  # noqa: BLE001
                errors.append(f"{label}_sidecar:{type(retry_exc).__name__}:{str(retry_exc)[:140]}")
                write_progress("sidecar_failed", sidecar=label, sidecar_count=len(sidecar_jobs), error_type=type(retry_exc).__name__)
            else:
                parts.append(normalize_payload(raw_payload, []))
        else:
            parts.append(normalize_payload(raw_payload, []))

    normalized = merge_normalized_payloads(parts, dossiers)
    if sidecar_has_items(structural_signal) and not normalized.get("structural_verdicts"):
        repair_prompt = build_structural_repair_prompt(
            structural_signal=structural_signal,
            source_readiness=source_readiness,
            snapshot=snapshot,
        )
        try:
            raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                candidates=credential_candidates,
                prompt=repair_prompt,
                max_tokens=min(max_tokens, 1400),
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
                retry_timeout_seconds=retry_timeout_seconds,
            )
            credential_warnings.extend(credential_errors[:1])
            repair_normalized = normalize_payload(raw_payload, [])
            if repair_normalized.get("structural_verdicts"):
                parts.append(repair_normalized)
                recoveries.append("structural_repair")
                normalized = merge_normalized_payloads(parts, dossiers)
            else:
                safe_repair_prompt = build_structural_repair_prompt(
                    structural_signal=structural_signal,
                    source_readiness=source_readiness,
                    snapshot=snapshot,
                    safe_mode=True,
                )
                raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                    candidates=credential_candidates,
                    prompt=safe_repair_prompt,
                    max_tokens=min(max_tokens, 1200),
                    timeout_seconds=timeout_seconds,
                    retry_attempts=retry_attempts,
                    retry_timeout_seconds=retry_timeout_seconds,
                )
                credential_warnings.extend(credential_errors[:1])
                repair_normalized = normalize_payload(raw_payload, [])
                if repair_normalized.get("structural_verdicts"):
                    parts.append(repair_normalized)
                    recoveries.append("structural_repair_safe")
                    normalized = merge_normalized_payloads(parts, dossiers)
                else:
                    errors.append("structural_repair:empty_structural_verdicts")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"structural_repair:{type(exc).__name__}:{str(exc)[:140]}")
    normalized = with_sidecar_coverage(
        normalized,
        ipo_watchlist=ipo_watchlist,
        hk_ipo_watchlist=hk_ipo_watchlist,
        structural_signal=structural_signal,
    )
    ipo_coverage = (normalized.get("sidecar_coverage") or {}).get("ipo") if isinstance(normalized.get("sidecar_coverage"), dict) else {}
    ipo_missing_keys = (ipo_coverage or {}).get("missing_required_keys") if isinstance(ipo_coverage, dict) else []
    if (sidecar_has_items(ipo_watchlist) or sidecar_has_items(hk_ipo_watchlist)) and ipo_missing_keys:
        ipo_prompt = build_ipo_repair_prompt(
            ipo_watchlist=ipo_watchlist,
            hk_ipo_watchlist=hk_ipo_watchlist,
            source_readiness=source_readiness,
            snapshot=snapshot,
        )
        try:
            raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                candidates=credential_candidates,
                prompt=ipo_prompt,
                max_tokens=min(max_tokens, 1800),
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
                retry_timeout_seconds=retry_timeout_seconds,
            )
            credential_warnings.extend(credential_errors[:1])
            repair_normalized = normalize_payload(raw_payload, [])
            if repair_normalized.get("ipo_verdicts"):
                parts.append(repair_normalized)
                recoveries.append("ipo_repair")
                normalized = merge_normalized_payloads(parts, dossiers)
                normalized = with_sidecar_coverage(
                    normalized,
                    ipo_watchlist=ipo_watchlist,
                    hk_ipo_watchlist=hk_ipo_watchlist,
                    structural_signal=structural_signal,
                )
            else:
                safe_ipo_prompt = build_ipo_repair_prompt(
                    ipo_watchlist=ipo_watchlist,
                    hk_ipo_watchlist=hk_ipo_watchlist,
                    source_readiness=source_readiness,
                    snapshot=snapshot,
                    safe_mode=True,
                )
                raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                    candidates=credential_candidates,
                    prompt=safe_ipo_prompt,
                    max_tokens=min(max_tokens, 1400),
                    timeout_seconds=timeout_seconds,
                    retry_attempts=retry_attempts,
                    retry_timeout_seconds=retry_timeout_seconds,
                )
                credential_warnings.extend(credential_errors[:1])
                repair_normalized = normalize_payload(raw_payload, [])
                if repair_normalized.get("ipo_verdicts"):
                    parts.append(repair_normalized)
                    recoveries.append("ipo_repair_safe")
                    normalized = merge_normalized_payloads(parts, dossiers)
                    normalized = with_sidecar_coverage(
                        normalized,
                        ipo_watchlist=ipo_watchlist,
                        hk_ipo_watchlist=hk_ipo_watchlist,
                        structural_signal=structural_signal,
                    )
                else:
                    errors.append("ipo_repair:empty_ipo_verdicts")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ipo_repair:{type(exc).__name__}:{str(exc)[:140]}")
    if recoveries:
        flags = [item for item in (normalized.get("operational_flags") or []) if isinstance(item, dict)]
        flags.append({"flag_type": "successful_recovery", "recoveries": recoveries[:6]})
        normalized["operational_flags"] = flags[:6]
    coverage = normalized.get("model_coverage") if isinstance(normalized.get("model_coverage"), dict) else {}
    sidecar_missing = []
    if (sidecar_has_items(ipo_watchlist) or sidecar_has_items(hk_ipo_watchlist)) and not normalized.get("ipo_verdicts"):
        sidecar_missing.append("ipo_verdicts")
    if sidecar_has_items(structural_signal) and not normalized.get("structural_verdicts"):
        sidecar_missing.append("structural_verdicts")
    sidecar_missing.extend(sidecar_coverage_gaps(normalized))
    if int(coverage.get("fallback_count") or 0) or errors or sidecar_missing:
        status = "partial_pass"
    else:
        status = "pass"
    note_parts = [f"planned_shards={len(range(0, len(dossiers), batch_size))}", f"sidecar_shards={len(sidecar_jobs)}"]
    if recoveries:
        note_parts.append(f"successful_recoveries={len(recoveries)}")
    if credential_warnings:
        note_parts.append(f"credential_warnings={len(credential_warnings)}")
    note_parts.extend(errors[:2])
    note_parts.extend(f"missing_{item}" for item in sidecar_missing)
    note = "; ".join(note_parts)
    return normalized, used_credential, status, [note.strip("; ")]


def build_payload(
    *,
    status: str,
    note: str,
    provider: str,
    model: str,
    model_family: str,
    base_url: str,
    snapshot: dict[str, Any],
    dossiers: list[dict[str, Any]],
    normalized: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "note": note,
        "provider": provider,
        "model": model,
        "model_family": model_family,
        "base_url": base_url,
        "run_id": str(snapshot.get("run_id") or snapshot.get("radar_run_id") or ""),
        "as_of_date": str(snapshot.get("as_of_date") or snapshot.get("market_sample_date") or ""),
        "candidate_count": len(dossiers),
        "dossiers": dossiers,
        **normalized,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_kimi_research_harness"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Kimi Research Harness {payload.get("as_of_date", "")}"',
        "---",
        "",
        "# Radar Kimi Research Harness",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 样本日期：`{payload.get('as_of_date')}`",
        f"- verdict 数：`{len(payload.get('verdicts') or [])}`",
        f"- 模型生成覆盖：`{(payload.get('model_coverage') or {}).get('model_generated_count', 0)}/{(payload.get('model_coverage') or {}).get('expected_count', 0)}`",
        "",
    ]
    if payload.get("summary"):
        lines.extend(["## Summary", "", f"- {payload.get('summary')}", ""])
    rows = [item for item in (payload.get("verdicts") or []) if isinstance(item, dict)]
    if rows:
        lines.extend(["## Verdicts", "", "| 对象 | 来源 | 决策 | 权重 | 理由 | 下一步 |", "| --- | --- | --- | ---: | --- | --- |"])
        for item in rows[:12]:
            action = item.get("next_action") or {}
            checks = "；".join(str(x) for x in (action.get("check") or [])) if isinstance(action, dict) else ""
            lines.append(
                "| {name} | {provenance} | {decision} | {weight} | {reason} | {when}: {checks} |".format(
                    name=str(item.get("name") or "").replace("|", "/"),
                    provenance=str(item.get("provenance") or "").replace("|", "/"),
                    decision=str(item.get("decision") or ""),
                    weight=int(item.get("weight_delta") or 0),
                    reason=str(item.get("reason") or "").replace("|", "/"),
                    when=str(action.get("when") or "") if isinstance(action, dict) else "",
                    checks=checks.replace("|", "/"),
                )
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    cfg = load_config_section(args.config, "kimi_research_harness")
    if not cfg:
        cfg = load_config_section(args.config, "kimi_editorial")
    enabled = bool(cfg.get("enabled", True))
    provider = str(cfg.get("provider") or "kimi_code_k2_6")
    base_url = str(cfg.get("base_url") or "https://api.kimi.com/coding").strip().rstrip("/")
    model = str(cfg.get("model") or "anthropic/kimi-for-coding").strip()
    model_family = str(cfg.get("model_family") or "").strip()
    timeout_seconds = float(cfg.get("timeout_seconds") or 60)
    retry_attempts = int(cfg.get("retry_attempts") or 0)
    retry_timeout_seconds = float(cfg.get("retry_timeout_seconds") or max(timeout_seconds * 2, 120.0))
    max_tokens = int(cfg.get("max_tokens") or 3200)
    max_candidates = int(cfg.get("max_candidates") or 12)
    planned_batch_size = int(cfg.get("planned_batch_size") or 4)
    use_planned_batches = bool(cfg.get("use_planned_batches", True))
    output_json = args.output_json or resolve_output_path(cfg.get("output_json_path"), DEFAULT_JSON_OUTPUT)
    output_md = args.output_md or resolve_output_path(cfg.get("output_md_path"), DEFAULT_MD_OUTPUT)

    snapshot = read_json(args.input_snapshot)
    handoff = read_optional_json(args.input_handoff)
    ipo_watchlist = read_optional_json(args.input_ipo_watchlist)
    hk_ipo_watchlist = read_optional_json(args.input_hk_ipo_watchlist)
    structural_signal = read_optional_json(args.input_structural_signal)
    company_enrichment = read_optional_json(args.input_company_enrichment)
    news_verification = read_optional_json(args.input_news_verification)
    source_readiness = read_optional_json(args.input_source_readiness)
    dossiers = [build_dossier(item) for item in pick_candidate_items(snapshot, handoff, structural_signal, max_candidates)]
    deterministic = with_sidecar_coverage(
        deterministic_payload(dossiers),
        ipo_watchlist=ipo_watchlist,
        hk_ipo_watchlist=hk_ipo_watchlist,
        structural_signal=structural_signal,
    )

    if not enabled:
        payload = build_payload(
            status="disabled",
            note="kimi_research_harness.enabled=false",
            provider=provider,
            model=model,
            model_family=model_family,
            base_url=base_url,
            snapshot=snapshot,
            dossiers=dossiers,
            normalized=deterministic,
        )
        write_json(output_json, payload)
        write_text(output_md, render_markdown(payload))
        print(json.dumps({"status": payload["status"], "output_json": str(output_json), "verdict_count": len(payload.get("verdicts") or [])}, ensure_ascii=False))
        return 0

    credential_candidates = discover_credential_candidates(cfg)
    if credential_candidates:
        base_url = credential_candidates[0].get("base_url") or base_url
        model = credential_candidates[0].get("model") or model
    if not credential_candidates:
        payload = build_payload(
            status="deterministic_fallback",
            note="No Kimi credentials discovered; emitted deterministic harness verdicts.",
            provider=provider,
            model=model,
            model_family=model_family,
            base_url=base_url,
            snapshot=snapshot,
            dossiers=dossiers,
            normalized=deterministic,
        )
        write_json(output_json, payload)
        write_text(output_md, render_markdown(payload))
        print(json.dumps({"status": payload["status"], "output_json": str(output_json), "verdict_count": len(payload.get("verdicts") or [])}, ensure_ascii=False))
        return 0

    if use_planned_batches:
        try:
            normalized, used_credential, status, notes = call_planned_model_shards(
                credential_candidates=credential_candidates,
                dossiers=dossiers,
                ipo_watchlist=ipo_watchlist,
                hk_ipo_watchlist=hk_ipo_watchlist,
                structural_signal=structural_signal,
                company_enrichment=company_enrichment,
                news_verification=news_verification,
                source_readiness=source_readiness,
                snapshot=snapshot,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
                retry_timeout_seconds=retry_timeout_seconds,
                batch_size=planned_batch_size,
            )
            base_url = used_credential.get("base_url") or base_url
            model = used_credential.get("model") or model
            note = "; ".join(item for item in notes if item)
        except Exception as exc:  # noqa: BLE001
            normalized = deterministic
            status = "deterministic_fallback"
            note = f"planned Kimi research shards failed: {type(exc).__name__}: {exc}"
        payload = build_payload(
            status=status,
            note=note,
            provider=provider,
            model=model,
            model_family=model_family,
            base_url=base_url,
            snapshot=snapshot,
            dossiers=dossiers,
            normalized=normalized,
        )
        write_json(output_json, payload)
        write_text(output_md, render_markdown(payload))
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "output_json": str(output_json),
                    "output_md": str(output_md),
                    "verdict_count": len(payload.get("verdicts") or []),
                },
                ensure_ascii=False,
            )
        )
        return 0

    prompt = build_prompt(
        dossiers=dossiers,
        ipo_watchlist=ipo_watchlist,
        hk_ipo_watchlist=hk_ipo_watchlist,
        structural_signal=structural_signal,
        company_enrichment=company_enrichment,
        news_verification=news_verification,
        source_readiness=source_readiness,
        snapshot=snapshot,
    )
    try:
        raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
            candidates=credential_candidates,
            prompt=prompt,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
            retry_timeout_seconds=retry_timeout_seconds,
        )
        base_url = used_credential.get("base_url") or base_url
        model = used_credential.get("model") or model
        normalized = normalize_payload(raw_payload, dossiers)
        normalized = with_sidecar_coverage(
            normalized,
            ipo_watchlist=ipo_watchlist,
            hk_ipo_watchlist=hk_ipo_watchlist,
            structural_signal=structural_signal,
        )
        coverage = normalized.get("model_coverage") if isinstance(normalized.get("model_coverage"), dict) else {}
        coverage_gaps = sidecar_coverage_gaps(normalized)
        if int(coverage.get("fallback_count") or 0) or coverage_gaps:
            status = "partial_pass"
            note = "model_missing_verdicts=" + " / ".join(str(x) for x in normalized.get("missing_model_verdicts") or [])
            if coverage_gaps:
                note = (note + "; " if note else "") + "; ".join(coverage_gaps)
        else:
            status = "pass"
            note = "; ".join(f"credential_fallback={item}" for item in credential_errors[:2])
    except Exception as exc:  # noqa: BLE001
        first_error = f"{type(exc).__name__}: {exc}"
        recoverable_error = any(
            marker in first_error.lower()
            for marker in (
                "high risk",
                "invalid_request_error",
                "jsondecodeerror",
                "no json object",
                "expecting",
                "unterminated string",
            )
        )
        if recoverable_error:
            safe_prompt = build_prompt(
                dossiers=dossiers,
                ipo_watchlist=ipo_watchlist,
                hk_ipo_watchlist=hk_ipo_watchlist,
                structural_signal=structural_signal,
                company_enrichment=company_enrichment,
                news_verification=news_verification,
                source_readiness=source_readiness,
                snapshot=snapshot,
                safe_mode=True,
            )
            try:
                raw_payload, used_credential, credential_errors = call_kimi_json_with_candidates(
                    candidates=credential_candidates,
                    prompt=safe_prompt,
                    max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                    retry_attempts=retry_attempts,
                    retry_timeout_seconds=retry_timeout_seconds,
                )
                base_url = used_credential.get("base_url") or base_url
                model = used_credential.get("model") or model
                normalized = normalize_payload(raw_payload, dossiers)
                normalized = with_sidecar_coverage(
                    normalized,
                    ipo_watchlist=ipo_watchlist,
                    hk_ipo_watchlist=hk_ipo_watchlist,
                    structural_signal=structural_signal,
                )
                status = "partial_pass"
                note = f"retried_with_sanitized_prompt_after={first_error[:160]}; " + "; ".join(
                    f"credential_fallback={item}" for item in credential_errors[:2]
                )
            except Exception as retry_exc:  # noqa: BLE001
                batch_verdicts: list[dict[str, Any]] = []
                batch_errors: list[str] = []
                batch_success_count = 0
                for offset in range(0, len(dossiers), 4):
                    batch = dossiers[offset : offset + 4]
                    batch_prompt = build_prompt(
                        dossiers=batch,
                        ipo_watchlist={},
                        hk_ipo_watchlist={},
                        structural_signal={},
                        company_enrichment=company_enrichment,
                        news_verification=news_verification,
                        source_readiness=source_readiness,
                        snapshot=snapshot,
                        safe_mode=True,
                    )
                    try:
                        batch_raw, used_credential, _credential_errors = call_kimi_json_with_candidates(
                            candidates=credential_candidates,
                            prompt=batch_prompt,
                            max_tokens=min(max_tokens, 1200),
                            timeout_seconds=timeout_seconds,
                            retry_attempts=retry_attempts,
                            retry_timeout_seconds=retry_timeout_seconds,
                        )
                        base_url = used_credential.get("base_url") or base_url
                        model = used_credential.get("model") or model
                        batch_normalized = normalize_payload(batch_raw, batch)
                        batch_verdicts.extend(batch_normalized.get("verdicts") or [])
                        coverage = batch_normalized.get("model_coverage") if isinstance(batch_normalized.get("model_coverage"), dict) else {}
                        batch_success_count += int(coverage.get("model_generated_count") or 0)
                    except Exception as batch_exc:  # noqa: BLE001
                        batch_errors.append(f"batch_{offset // 4 + 1}:{type(batch_exc).__name__}:{str(batch_exc)[:120]}")
                        batch_verdicts.extend(deterministic_verdict(dossier) for dossier in batch)
                if batch_success_count:
                    fallback_count = max(len(dossiers) - batch_success_count, 0)
                    normalized = {
                        "summary": f"sanitized batch retry partial: {batch_success_count}/{len(dossiers)} model verdicts",
                        "verdicts": batch_verdicts,
                        "ipo_verdicts": [],
                        "structural_verdicts": [],
                        "risk_flags": normalize_risk_flags(batch_errors, provenance=DETERMINISTIC_PROVENANCE, limit=3),
                        "model_coverage": {
                            "expected_count": len(dossiers),
                            "model_generated_count": batch_success_count,
                            "fallback_count": fallback_count,
                            "coverage_ratio": round((batch_success_count / len(dossiers)) if dossiers else 1.0, 4),
                        },
                        "missing_model_verdicts": [
                            str(item.get("name") or item.get("object_id") or "")
                            for item in batch_verdicts
                            if isinstance(item, dict) and item.get("model_generated") is not True
                        ][:12],
                    }
                    normalized = with_sidecar_coverage(
                        normalized,
                        ipo_watchlist=ipo_watchlist,
                        hk_ipo_watchlist=hk_ipo_watchlist,
                        structural_signal=structural_signal,
                    )
                    status = "partial_pass"
                    note = (
                        f"retried_with_sanitized_batches_after={first_error[:140]}; "
                        f"single_retry={type(retry_exc).__name__}: {str(retry_exc)[:140]}"
                    )
                else:
                    normalized = deterministic
                    status = "deterministic_fallback"
                    note = (
                        f"Kimi research harness failed: {first_error}; "
                        f"sanitized retry failed: {type(retry_exc).__name__}: {retry_exc}; "
                        f"batch retry failed: {' | '.join(batch_errors[:3])}"
                    )
        else:
            normalized = deterministic
            status = "deterministic_fallback"
            note = f"Kimi research harness failed: {first_error}"
    payload = build_payload(
        status=status,
        note=note,
        provider=provider,
        model=model,
        model_family=model_family,
        base_url=base_url,
        snapshot=snapshot,
        dossiers=dossiers,
        normalized=normalized,
    )
    write_json(output_json, payload)
    write_text(output_md, render_markdown(payload))
    print(
        json.dumps(
            {
                "status": payload["status"],
                "output_json": str(output_json),
                "output_md": str(output_md),
                "verdict_count": len(payload.get("verdicts") or []),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
