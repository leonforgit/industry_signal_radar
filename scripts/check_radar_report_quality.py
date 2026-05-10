#!/usr/bin/env python3
"""Check whether the latest Radar daily report satisfies PM-grade report rules."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from radar_current_health import reconcile_harness_current_health
from radar_freshness_utils import summarize_snapshot_freshness


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES = ROOT / "config" / "radar_report_rules_v1.json"
DEFAULT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_INVENTORY = ROOT / "output" / "inventory" / "radar_catalyst_inventory_latest.json"
DEFAULT_HANDOFF = ROOT / "output" / "handoffs" / "radar_research_handoff_latest.json"
DEFAULT_REPORT = ROOT / "output" / "reports" / "radar_daily_report_latest.md"
DEFAULT_REPORT_PDF = ROOT / "output" / "reports" / "radar_daily_report_latest.pdf"
DEFAULT_SOURCE_READINESS = ROOT / "output" / "reports" / "radar_source_readiness_latest.json"
DEFAULT_KIMI_EDITORIAL = ROOT / "output" / "reports" / "radar_kimi_editorial_latest.json"
DEFAULT_KIMI_RESEARCH = ROOT / "output" / "reports" / "radar_kimi_research_harness_latest.json"
DEFAULT_PRICE_FRESHNESS = ROOT / "output" / "reports" / "radar_price_freshness_latest.json"
DEFAULT_FUNDAMENTAL_COVERAGE = ROOT / "output" / "reports" / "radar_fundamental_coverage_latest.json"
DEFAULT_NEWS_VERIFICATION = ROOT / "output" / "reports" / "radar_news_verification_latest.json"
DEFAULT_DATA_SUBSTRATE_AUDIT = ROOT / "output" / "reports" / "radar_data_substrate_audit_latest.json"
DEFAULT_IPO_WATCHLIST = ROOT / "output" / "reports" / "radar_ipo_watchlist_latest.json"
DEFAULT_HK_IPO_WATCHLIST = ROOT / "output" / "reports" / "radar_hk_ipo_watchlist_latest.json"
DEFAULT_STRUCTURAL_SIGNAL = ROOT / "output" / "reports" / "radar_structural_signal_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_report_quality_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_report_quality_latest.md"
KIMI_OPERATIONAL_DEGRADED_MARKERS = (
    "retry",
    "retried",
    "batch",
    "fallback",
    "jsondecodeerror",
    "timeout",
    "sanitized",
    "successful_recoveries",
    "structural_repair",
    "sidecar_repair",
)
GENERIC_KEY_EVENT_LOGIC_PHRASES = (
    "事件已进入研究队列",
    "先用价格承接、量能和同方向扩散确认强度",
    "未确认前不给高弹性假设",
    "确认后再上调",
)
EARNINGS_EVENT_KEYWORDS = (
    "一季度",
    "第一季度",
    "二季度",
    "半年度",
    "三季度",
    "年报",
    "季报",
    "财报",
    "业绩",
    "净利润",
    "归母净利润",
    "同比增长",
    "同比增加",
    "扭亏",
)
STRUCTURAL_EVENT_KEYWORDS = (
    "并购",
    "重组",
    "收购",
    "订单",
    "合同",
    "中标",
    "获批",
    "注册",
    "产线",
    "产能",
    "客户",
    "交付",
    "回购",
    "增持",
    "股权激励",
    "分拆",
    "出海",
    "牌照",
)
COMMODITY_PRICE_DOWN_KEYWORDS = ("跌破", "下跌", "回落", "走低", "承压", "转跌")
OIL_SUPPLY_INCREASE_KEYWORDS = ("产量配额提高", "提高石油产量", "增产")
PRICED_IN_DAILY_RETURN = 0.04
PARTIAL_PRICED_IN_DAILY_RETURN = 0.02
PRICED_IN_AMOUNT_RATIO = 2.0
PRICED_IN_POSITIVE_DAYS = 0.8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--report-pdf", type=Path, default=DEFAULT_REPORT_PDF)
    parser.add_argument("--source-readiness", type=Path, default=DEFAULT_SOURCE_READINESS)
    parser.add_argument("--kimi-editorial", type=Path, default=DEFAULT_KIMI_EDITORIAL)
    parser.add_argument("--kimi-research", type=Path, default=DEFAULT_KIMI_RESEARCH)
    parser.add_argument("--price-freshness", type=Path, default=DEFAULT_PRICE_FRESHNESS)
    parser.add_argument("--fundamental-coverage", type=Path, default=DEFAULT_FUNDAMENTAL_COVERAGE)
    parser.add_argument("--news-verification", type=Path, default=DEFAULT_NEWS_VERIFICATION)
    parser.add_argument("--data-substrate-audit", type=Path, default=DEFAULT_DATA_SUBSTRATE_AUDIT)
    parser.add_argument("--ipo-watchlist", type=Path, default=DEFAULT_IPO_WATCHLIST)
    parser.add_argument("--hk-ipo-watchlist", type=Path, default=DEFAULT_HK_IPO_WATCHLIST)
    parser.add_argument("--structural-signal", type=Path, default=DEFAULT_STRUCTURAL_SIGNAL)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def extract_top_opportunity_names(report_text: str, limit: int) -> list[str]:
    names: list[str] = []
    markers = ("## 二、三条作战线", "## 五、今日 Top Opportunities", "## 四、Top Opportunities")
    marker = next((item for item in markers if item in report_text), "")
    if not marker:
        return names
    section = report_text.split(marker, 1)[1]
    for line in section.splitlines():
        if not line.startswith("### "):
            continue
        content = line[4:].strip()
        parts = content.split(" | ")
        head = parts[0].strip()
        if ". " in head:
            _, name = head.split(". ", 1)
        else:
            name = head
        names.append(name.strip())
        if len(names) >= limit:
            break
    return names


def extract_summary_queue_counts(report_text: str) -> dict[str, int]:
    pattern = re.compile(
        r"当前研究分流为 `Immediate Research (?P<immediate>\d+) / Thesis Watch (?P<thesis>\d+) / Risk Review (?P<risk>\d+)`。"
    )
    match = pattern.search(report_text)
    if not match:
        return {}
    return {
        "immediate_research": int(match.group("immediate")),
        "thesis_watch": int(match.group("thesis")),
        "risk_review": int(match.group("risk")),
    }


def extract_report_run_id(report_text: str) -> str:
    patterns = (
        r'codex_output_run_id:\s*"([^"]+)"',
        r"codex_output_run_id:\s*'([^']+)'",
        r"运行批次[：:]\s*`([^`]+)`",
    )
    for pattern in patterns:
        match = re.search(pattern, report_text)
        if match:
            return match.group(1).strip()
    return ""


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def ipo_watchlist_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def ipo_display_name(item: dict[str, Any]) -> str:
    for key in ("short_name_cn", "display_name_cn", "name_cn", "name"):
        value = clean_text(item.get(key))
        if value:
            return value
    return clean_text(item.get("code"))


def ipo_watchlist_status(payload: dict[str, Any]) -> str:
    return str(payload.get("status") or "missing").strip() if payload else "missing"


def parse_date_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def parse_datetime_text(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def event_display_text(event: dict[str, Any]) -> str:
    for key in ("title", "headline", "summary", "event_type"):
        text = clean_text(event.get(key))
        if text:
            return text
    return ""


def latest_event_date(item: dict[str, Any]) -> str:
    dates = [
        parse_date_text(event.get("published_at"))
        for event in (item.get("supporting_events") or [])
        if isinstance(event, dict)
    ]
    dates = [date for date in dates if date]
    return max(dates) if dates else ""


def item_event_text(item: dict[str, Any]) -> str:
    texts: list[str] = []
    for event in item.get("supporting_events") or []:
        if isinstance(event, dict):
            texts.append(event_display_text(event))
    texts.extend(clean_text(x) for x in (item.get("key_evidence") or []))
    texts.append(clean_text(item.get("catalyst_type")))
    texts.append(clean_text(item.get("why_now")))
    return "；".join(text for text in texts if text)


def resolve_item_industry(item: dict[str, Any]) -> str:
    for evidence in item.get("key_evidence") or []:
        text = clean_text(evidence)
        if text.startswith("所属方向："):
            return text.replace("所属方向：", "", 1).strip()
    scope = clean_text(item.get("radar_object_scope"))
    if " / " in scope:
        return scope.rsplit(" / ", 1)[-1].strip()
    return ""


def primary_symbol_text(item: dict[str, Any]) -> str:
    primary = item.get("primary_symbols") or []
    if isinstance(primary, list) and primary:
        return clean_text(primary[0])
    return clean_text(item.get("radar_object_name"))


def report_campaign_section(report_text: str) -> str:
    marker = "## 二、三条作战线"
    if marker not in report_text:
        return ""
    section = report_text.split(marker, 1)[1]
    for end_marker in ("## 三、中长期隐性线索", "## 四、IPO / 打新申购窗口", "## 五、后台状态"):
        if end_marker in section:
            return section.split(end_marker, 1)[0]
    return section


def promoted_company_absorbed_by_top_industry(
    item: dict[str, Any],
    *,
    top_name_set: set[str],
    campaign_section: str,
) -> bool:
    if str(item.get("radar_object_type") or "") != "company":
        return False
    industry_name = resolve_item_industry(item)
    if not industry_name or industry_name not in top_name_set:
        return False
    name = clean_text(item.get("radar_object_name"))
    symbol = primary_symbol_text(item)
    symbol_compact = symbol.replace(" ", "")
    code_match = re.search(r"\b(\d{6})\b", symbol)
    code = code_match.group(1) if code_match else ""
    if not name or name not in campaign_section:
        return False
    return bool(
        (symbol and symbol in campaign_section)
        or (symbol_compact and symbol_compact in campaign_section.replace(" ", ""))
        or (code and code in campaign_section)
    )


def is_earnings_only_item(item: dict[str, Any]) -> bool:
    if str(item.get("radar_object_type") or "") != "company":
        return False
    text = item_event_text(item)
    if not any(keyword in text for keyword in EARNINGS_EVENT_KEYWORDS):
        return False
    return not any(keyword in text for keyword in STRUCTURAL_EVENT_KEYWORDS)


def opportunity_precheck_status(item: dict[str, Any]) -> str:
    if not is_earnings_only_item(item):
        return "not_applicable"
    context = item.get("price_context") or {}
    if not isinstance(context, dict) or not context:
        return "missing_price"
    price_date = parse_date_text(context.get("as_of_date"))
    event_date = latest_event_date(item)
    if event_date and price_date and event_date > price_date:
        return "awaiting_first_trade"
    daily_return = optional_float(context.get("daily_return"))
    amount_ratio_20 = optional_float(context.get("amount_ratio_20"))
    positive_days_5d = optional_float(context.get("positive_days_5d"))
    if (
        daily_return is not None
        and daily_return >= PRICED_IN_DAILY_RETURN
        and (
            (amount_ratio_20 is not None and amount_ratio_20 >= PRICED_IN_AMOUNT_RATIO)
            or (positive_days_5d is not None and positive_days_5d >= PRICED_IN_POSITIVE_DAYS)
        )
    ):
        return "priced_in"
    if daily_return is not None and daily_return >= PARTIAL_PRICED_IN_DAILY_RETURN:
        return "partially_priced"
    return "needs_confirmation"


def directional_conflict_reason(item: dict[str, Any]) -> str:
    if str(item.get("radar_object_type") or "") != "industry":
        return ""
    name = str(item.get("radar_object_name") or "").strip()
    text = item_event_text(item)
    if name in {"有色金属", "黄金"} and "黄金" in text and any(token in text for token in COMMODITY_PRICE_DOWN_KEYWORDS):
        return "黄金价格下行却进入多头Top"
    if (
        name in {"石油石化", "煤炭"}
        and any(token in text for token in OIL_SUPPLY_INCREASE_KEYWORDS)
        and "缺行业代理变量" in str(item.get("confirmation_gap") or "")
    ):
        return "供给增加/方向混合且缺代理确认"
    return ""


def kimi_research_verdict_maps(kimi_research: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for item in kimi_research.get("verdicts") or []:
        if not isinstance(item, dict):
            continue
        object_id = str(item.get("object_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if object_id:
            by_id[object_id] = item
        if name:
            by_name[name] = item
    return by_id, by_name


def kimi_research_verdict(item: dict[str, Any], kimi_research: dict[str, Any]) -> dict[str, Any]:
    by_id, by_name = kimi_research_verdict_maps(kimi_research)
    object_id = str(item.get("radar_object_id") or item.get("candidate_id") or "").strip()
    name = str(item.get("radar_object_name") or "").strip()
    return by_id.get(object_id) or by_name.get(name) or {}


def kimi_model_coverage(kimi_research: dict[str, Any]) -> dict[str, int | float]:
    verdicts = [item for item in (kimi_research.get("verdicts") or []) if isinstance(item, dict)]
    coverage = kimi_research.get("model_coverage") if isinstance(kimi_research.get("model_coverage"), dict) else {}
    model_generated_count = int(
        coverage.get("model_generated_count")
        if coverage.get("model_generated_count") is not None
        else sum(1 for item in verdicts if item.get("model_generated") is True)
    )
    expected_count = int(coverage.get("expected_count") if coverage.get("expected_count") is not None else kimi_research.get("candidate_count") or len(verdicts))
    fallback_count = int(coverage.get("fallback_count") if coverage.get("fallback_count") is not None else max(expected_count - model_generated_count, 0))
    ratio = float(coverage.get("coverage_ratio") if coverage.get("coverage_ratio") is not None else ((model_generated_count / expected_count) if expected_count else 1.0))
    return {
        "expected_count": expected_count,
        "model_generated_count": model_generated_count,
        "fallback_count": fallback_count,
        "coverage_ratio": ratio,
    }


def kimi_has_operational_degradation(kimi_research: dict[str, Any]) -> bool:
    text = str(kimi_research.get("note") or "").lower()
    return bool(kimi_research.get("operational_flags")) or any(marker in text for marker in KIMI_OPERATIONAL_DEGRADED_MARKERS)


def kimi_sidecar_coverage_gap(kimi_research: dict[str, Any], key: str) -> str:
    coverage = kimi_research.get("sidecar_coverage") if isinstance(kimi_research.get("sidecar_coverage"), dict) else {}
    row = coverage.get(key) if isinstance(coverage.get(key), dict) else {}
    if not row:
        return f"{key}=missing_coverage"
    missing = [str(item) for item in (row.get("missing_required_keys") or []) if str(item)]
    required = int(row.get("required_count") or 0)
    generated = int(row.get("model_generated_count") or 0)
    if missing or generated < required:
        return f"{key}={generated}/{required} missing={','.join(missing[:3])}"
    return ""


def check_quality(
    *,
    rules: dict[str, Any],
    snapshot: dict[str, Any],
    inventory: dict[str, Any],
    handoff: dict[str, Any],
    report_text: str,
    report_pdf_exists: bool,
    report_pdf_issue: str,
    source_readiness: dict[str, Any],
    kimi_editorial: dict[str, Any],
    kimi_research: dict[str, Any],
    price_freshness: dict[str, Any],
    fundamental_coverage: dict[str, Any],
    news_verification: dict[str, Any],
    data_substrate_audit: dict[str, Any],
    ipo_watchlist: dict[str, Any],
    hk_ipo_watchlist: dict[str, Any],
    structural_signal: dict[str, Any],
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    quality = rules.get("quality_gates") or {}
    blockers: list[str] = []
    warnings: list[str] = []
    passed_checks: list[str] = []

    required_sections = [str(x) for x in (quality.get("required_sections") or [])]
    for section in required_sections:
        if section not in report_text:
            blockers.append(f"缺少关键章节：{section}")
    if not blockers:
        passed_checks.append("关键章节齐全")

    report_line_count = len(report_text.splitlines())
    warn_line_count = optional_int(quality.get("warn_if_report_line_count_gt"))
    if warn_line_count is not None and report_line_count > warn_line_count:
        warnings.append(f"日报正文偏长：line_count={report_line_count} > {warn_line_count}")
    else:
        passed_checks.append(f"日报长度在规则范围内：line_count={report_line_count}")

    if quality.get("forbid_source_tags_in_report") and "akshare:" in report_text:
        blockers.append("日报正文仍包含原始源标签 `akshare:`")
    else:
        passed_checks.append("日报正文未暴露原始源标签")

    if not report_pdf_exists:
        detail = f"：{report_pdf_issue}" if report_pdf_issue else ""
        blockers.append(f"日报 PDF 产物缺失或不是本次生成，当前邮件链无法按 PDF 正常投递{detail}")
    else:
        passed_checks.append("日报 PDF 产物已生成且与本次 Markdown 同步")

    snapshot_run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "").strip()
    report_run_id = extract_report_run_id(report_text)
    sidecar_run_ids: dict[str, str] = {
        "snapshot": snapshot_run_id,
        "report_footer": report_run_id,
        "handoff": str(handoff.get("run_id") or "").strip(),
        "inventory": str(inventory.get("run_id") or "").strip(),
        "kimi_research": str(kimi_research.get("run_id") or "").strip(),
    }
    if str(kimi_editorial.get("status") or "") == "pass":
        sidecar_run_ids["kimi_editorial"] = str(kimi_editorial.get("run_id") or "").strip()
    missing_run_ids = [name for name, value in sidecar_run_ids.items() if not value]
    if missing_run_ids:
        blockers.append("批次一致性缺少 run_id：" + " / ".join(missing_run_ids))
    mismatched_run_ids = [
        f"{name}={value}"
        for name, value in sidecar_run_ids.items()
        if value and snapshot_run_id and value != snapshot_run_id
    ]
    if mismatched_run_ids:
        blockers.append("批次一致性失败：" + " / ".join(mismatched_run_ids) + f" / snapshot={snapshot_run_id}")
    elif not missing_run_ids:
        passed_checks.append("报告、handoff、inventory、Kimi verdict 与 snapshot 运行批次一致")

    if source_readiness:
        source_readiness_status = str(source_readiness.get("status") or "").strip()
        if source_readiness_status == "pass":
            passed_checks.append("上游源数据就绪：news / sentiment / market freshness 全部通过")
        elif source_readiness_status == "warn":
            source_warnings = [str(item) for item in (source_readiness.get("warnings") or []) if str(item).strip()]
            message = "上游源数据降级：" + (" / ".join(source_warnings[:5]) if source_warnings else "source_readiness=warn")
            readiness_blockers = [str(item) for item in (source_readiness.get("blockers") or []) if str(item).strip()]
            if readiness_blockers:
                blockers.append(message + " / blockers=" + " / ".join(readiness_blockers[:5]))
            else:
                warnings.append(message)
        else:
            blockers.append("上游源数据未就绪：news / sentiment / market 至少一层 freshness 失败")
        source_warnings = [str(item) for item in (source_readiness.get("warnings") or []) if str(item).strip()]
        source_health = source_readiness.get("source_health") or {}
        if source_warnings and source_readiness_status == "pass":
            warnings.append("上游源健康存在提醒：" + " / ".join(source_warnings[:5]))
        source_health_status = str(source_health.get("status") or "").strip()
        if source_health_status in {"warn", "fail"}:
            summary = source_health.get("summary") or {}
            source_health_message = (
                "共享新闻底座有降级源："
                f"ok={summary.get('ok', 0)} degraded={summary.get('degraded', 0)} down={summary.get('down', 0)}"
            )
            source_health_blockers = [str(item) for item in (source_health.get("blockers") or []) if str(item).strip()]
            critical_summary = source_health.get("critical_summary") or {}
            critical_down = int(critical_summary.get("critical_down") or 0)
            if source_health_status == "fail" or source_health_blockers or critical_down > 0:
                detail = " / blockers=" + " / ".join(source_health_blockers[:5]) if source_health_blockers else ""
                blockers.append(source_health_message + detail)
            else:
                warnings.append(source_health_message)
    else:
        warnings.append("缺少 source readiness 结果，无法确认上游源数据是否全部就绪")

    kimi_status = str(kimi_editorial.get("status") or "").strip()
    if kimi_status == "pass":
        passed_checks.append("PM 编辑摘要层已生成")
    elif kimi_status in {"skip_no_credentials", "missing", "skip"}:
        passed_checks.append(f"PM 编辑摘要层为可选增强层：status={kimi_status or 'missing'}")
    elif kimi_status:
        warnings.append(f"PM 编辑摘要层未启用：status={kimi_status}")

    kimi_research_status = str(kimi_research.get("status") or "").strip()
    kimi_coverage = kimi_model_coverage(kimi_research) if kimi_research else {"expected_count": 0, "model_generated_count": 0, "fallback_count": 0, "coverage_ratio": 0.0}
    kimi_degraded = kimi_has_operational_degradation(kimi_research) if kimi_research else False
    if kimi_research_status == "pass":
        if int(kimi_coverage.get("fallback_count") or 0) > 0 or float(kimi_coverage.get("coverage_ratio") or 0.0) < 1.0:
            blockers.append(
                "Kimi research harness 标记 pass 但存在非模型 verdict："
                f"model_generated={kimi_coverage.get('model_generated_count')}/{kimi_coverage.get('expected_count')}"
            )
        elif kimi_degraded:
            passed_checks.append("Kimi research harness 已生成全量模型 verdict；可恢复降级由 operational_flags 进入研究 backlog")
        else:
            passed_checks.append("Kimi research harness 已生成全量模型 verdict")
    elif kimi_research_status == "partial_pass":
        message = (
            "Kimi research harness 仅部分通过："
            f"model_generated={kimi_coverage.get('model_generated_count')}/{kimi_coverage.get('expected_count')}"
        )
        if quality.get("require_kimi_research_harness"):
            blockers.append(message)
        else:
            warnings.append(message)
    elif kimi_research_status == "deterministic_fallback":
        if quality.get("require_kimi_research_harness"):
            blockers.append("Kimi research harness 被标记为必需，但本轮未调用模型，仅使用 deterministic fallback verdict")
        else:
            warnings.append("Kimi research harness 未调用模型，当前使用 deterministic fallback verdict")
    elif kimi_research_status in {"disabled", "skip_no_credentials", "missing", ""}:
        if quality.get("require_kimi_research_harness"):
            blockers.append(f"Kimi research harness 缺失或未启用：status={kimi_research_status or 'missing'}")
        else:
            warnings.append(f"Kimi research harness 未启用：status={kimi_research_status or 'missing'}")
    else:
        warnings.append(f"Kimi research harness 状态异常：status={kimi_research_status}")

    ipo_research_items = [
        item
        for item in [*(ipo_watchlist.get("items") or []), *(hk_ipo_watchlist.get("items") or [])]
        if isinstance(item, dict)
    ]
    structural_research_items = [item for item in (structural_signal.get("items") or []) if isinstance(item, dict)]
    if kimi_research_status == "pass":
        if ipo_research_items and not [item for item in (kimi_research.get("ipo_verdicts") or []) if isinstance(item, dict)]:
            blockers.append("Kimi research harness pass 但缺少 IPO verdict，无法证明打新候选已被模型预检")
        if structural_research_items and not [item for item in (kimi_research.get("structural_verdicts") or []) if isinstance(item, dict)]:
            blockers.append("Kimi research harness pass 但缺少 structural verdict，无法证明中长期线索已被模型预检")
        ipo_gap = kimi_sidecar_coverage_gap(kimi_research, "ipo") if ipo_research_items else ""
        structural_gap = kimi_sidecar_coverage_gap(kimi_research, "structural") if structural_research_items else ""
        if ipo_gap:
            blockers.append("Kimi research harness pass 但 IPO verdict 覆盖不足：" + ipo_gap)
        if structural_gap:
            blockers.append("Kimi research harness pass 但 structural verdict 覆盖不足：" + structural_gap)

    queues = handoff.get("queues") or {}
    immediate_items = queues.get("immediate_research") or []

    price_refresh_status = str(price_freshness.get("status") or "").strip()
    handoff_immediate_names = {
        str(item.get("radar_object_name") or "")
        for item in immediate_items
        if isinstance(item, dict) and str(item.get("radar_object_type") or "") == "company"
    }
    report_surface_price_names = {
        str(item.get("radar_object_name") or "")
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict)
        and str(item.get("radar_object_type") or "") == "company"
        and (
            str(item.get("radar_object_name") or "") in handoff_immediate_names
            or int(item.get("rank_overall") or 9999) <= int(rules.get("top_opportunities_limit") or 5)
        )
    }
    if price_refresh_status == "pass":
        passed_checks.append("价格补数门禁通过：canonical price 已按需补齐")
    elif price_refresh_status == "warn":
        stale_targets = (price_freshness.get("csv_status") or {}).get("stale_targets") or []
        missing_targets = set((price_freshness.get("csv_status") or {}).get("missing_targets") or [])
        visible_stale_targets = [
            item
            for item in stale_targets
            if str(item.get("radar_object_name") or "") in report_surface_price_names
        ]
        visible_missing_targets = sorted(missing_targets & report_surface_price_names)
        if visible_stale_targets:
            warnings.append(
                "价格补数存在报告前排公司缺口："
                + " / ".join(
                    f"{str(item.get('radar_object_name') or '')}({str(item.get('as_of_date') or 'unknown')})"
                    for item in visible_stale_targets[:5]
                )
            )
        elif visible_missing_targets:
            warnings.append("价格补数存在报告前排公司缺口：" + " / ".join(visible_missing_targets[:5]))
        else:
            passed_checks.append("价格补数长尾候选仍有缺口，但报告前排公司已覆盖最新价格")
    elif price_refresh_status:
        blockers.append(f"价格补数门禁失败：status={price_refresh_status}")
    else:
        warnings.append("缺少价格补数状态，无法确认 stale price 是否已触发主动补齐")

    fundamental_coverage_status = str(fundamental_coverage.get("status") or "").strip()
    if fundamental_coverage_status in {"pass", "skip"}:
        passed_checks.append(f"财务补抓层已运行：status={fundamental_coverage_status}")
    elif fundamental_coverage_status == "warn":
        missing_targets = fundamental_coverage.get("missing_targets") or []
        visible_missing_targets = [
            item
            for item in missing_targets
            if isinstance(item, dict) and str(item.get("radar_object_name") or "") in report_surface_price_names
        ]
        if visible_missing_targets:
            warnings.append(
                "财务覆盖存在报告前排缺口："
                + " / ".join(
                    f"{str(item.get('radar_object_name') or '')}[{','.join(str(x) for x in (item.get('missing_required') or []))}]"
                    for item in visible_missing_targets[:5]
                )
            )
        elif missing_targets:
            passed_checks.append("财务覆盖长尾候选仍有缺口，但报告前排公司已覆盖可用财务摘要")
        else:
            warnings.append("财务覆盖为 warn，但未返回缺口对象列表")
    elif fundamental_coverage_status:
        blockers.append(f"财务补抓层失败：status={fundamental_coverage_status}")
    else:
        warnings.append("缺少财务补抓状态，无法确认 candidate 公司财务数据是否可得")

    news_verification_status = str(news_verification.get("status") or "").strip()
    if news_verification_status in {"pass", "skip"}:
        passed_checks.append(f"新闻补核层已运行：status={news_verification_status}")
    elif news_verification_status == "warn":
        problem_items = [
            item
            for item in (news_verification.get("items") or [])
            if isinstance(item, dict) and str(item.get("status") or "") not in {"pass", "skip"}
        ]
        visible_problem_items = [
            item
            for item in problem_items
            if str(item.get("radar_object_name") or "") in report_surface_price_names
            or str(item.get("radar_object_name") or "") in top_name_set
            or str(item.get("triage_action") or "") == "immediate_research"
        ]
        if visible_problem_items:
            blockers.append(
                "新闻补核层存在前排/Immediate 缺口："
                + " / ".join(
                    f"{str(item.get('radar_object_name') or '')}({str(item.get('error') or item.get('status') or '')})"
                    for item in visible_problem_items[:5]
                )
            )
        else:
            passed_checks.append("新闻补核层已运行；仅长尾对象未补齐，不阻断晨报。")
    elif news_verification_status:
        warnings.append(f"新闻补核层未完全通过：status={news_verification_status}")

    data_substrate_audit_status = str(data_substrate_audit.get("status") or "").strip()
    if data_substrate_audit_status == "pass":
        passed_checks.append("数据底座审计通过：事件/行情/财务主链均可持续")
    elif data_substrate_audit_status == "warn":
        findings = data_substrate_audit.get("findings") or []
        long_tail_financial_findings = [
            str(item)
            for item in findings
            if str(item).startswith("missing financial coverage:")
            and not any(name and name in str(item) for name in report_surface_price_names)
        ]
        material_findings = [str(item) for item in findings if str(item) not in set(long_tail_financial_findings)]
        if material_findings:
            warnings.append("数据底座审计存在提醒：" + " / ".join(material_findings[:5]))
        else:
            passed_checks.append("数据底座审计仅剩长尾财务覆盖提醒，报告前排未受影响")
    elif data_substrate_audit_status:
        blockers.append(f"数据底座审计失败：status={data_substrate_audit_status}")
    else:
        warnings.append("缺少数据底座审计结果，无法判断 canonical-first 主链是否可持续")

    if quality.get("forbid_mapping_placeholders_in_top_opportunities") and (
        "股票代码：待补映射" in report_text or "股票代码：未确认" in report_text
    ):
        warnings.append("日报正文仍包含股票代码占位提示，建议继续补映射")
    else:
        passed_checks.append("日报正文未出现股票代码占位提示")

    if quality.get("forbid_generic_key_event_logic"):
        generic_hits = [phrase for phrase in GENERIC_KEY_EVENT_LOGIC_PHRASES if phrase in report_text]
        if generic_hits:
            blockers.append("今日关键事件仍包含兜底式逻辑传导：" + " / ".join(generic_hits))
        else:
            passed_checks.append("今日关键事件已给出具体影响对象、传导链和弹性假设")

    ipo_status = ipo_watchlist_status(ipo_watchlist)
    hk_ipo_status = ipo_watchlist_status(hk_ipo_watchlist)
    ipo_items = ipo_watchlist_items(ipo_watchlist)
    hk_ipo_items = ipo_watchlist_items(hk_ipo_watchlist)
    reviewed_ipo_names = [name for item in [*ipo_items, *hk_ipo_items] if (name := ipo_display_name(item))]
    if quality.get("require_ipo_source_gate"):
        bad_ipo_statuses = []
        if ipo_status != "pass":
            bad_ipo_statuses.append(f"A股 IPO={ipo_status}")
        if hk_ipo_status != "pass":
            bad_ipo_statuses.append(f"港股 IPO={hk_ipo_status}")
        if bad_ipo_statuses:
            blockers.append("IPO 打新源未完成全量预检：" + " / ".join(bad_ipo_statuses))
        elif reviewed_ipo_names and "今日未抓到 A/H 新股申购窗口或可复盘的新股对象" in report_text:
            blockers.append("IPO watchlist 已有研究对象，但报告仍写成未抓到可复盘对象：" + " / ".join(reviewed_ipo_names[:5]))
        elif reviewed_ipo_names:
            missing_ipo_names = [name for name in reviewed_ipo_names[:8] if name and name not in report_text]
            if missing_ipo_names:
                blockers.append("IPO watchlist 已研究对象未进入报告：" + " / ".join(missing_ipo_names[:5]))
            else:
                passed_checks.append("IPO 打新板块已覆盖 A/H watchlist 中的可申购与不达标对象")
        else:
            passed_checks.append("IPO 打新源已完成预检，本轮没有 A/H 可复盘对象")

    shallow_action_patterns = (
        r"动作：先核实",
        r"动作：今日核实",
        r"建议动作：今日核实",
        r"建议动作：先核实",
        r"今天最该补的一个验证",
    )
    shallow_action_hits = [pattern for pattern in shallow_action_patterns if re.search(pattern, report_text)]
    if shallow_action_hits and kimi_status == "pass":
        blockers.append("Kimi 前置初研已启用，但日报仍把浅层核实动作暴露给 PM：" + " / ".join(shallow_action_hits))
    elif shallow_action_hits:
        warnings.append("日报仍包含浅层核实动作，建议启用 Kimi 前置初研后再发布")
    else:
        passed_checks.append("Kimi 前置初研动作已落到正文，未把浅层核实口径暴露给 PM")

    quant_signal_status = str(snapshot.get("quant_signal_status") or "").strip()
    if quant_signal_status == "pass":
        passed_checks.append("量化信号侧车已接入：snapshot 含 Alpha158 / TimesFM / quant bundle 上下文")
    elif quant_signal_status:
        warnings.append(f"量化信号侧车未完全通过：status={quant_signal_status}")

    queues = handoff.get("queues") or {}
    immediate_items = queues.get("immediate_research") or []
    queue_counts = {name: len(items) for name, items in queues.items() if isinstance(items, list)}
    min_immediate = int(quality.get("min_immediate_research") or 0)
    max_immediate = int(quality.get("max_immediate_research") or 99)
    immediate_count = len(immediate_items)
    report_date = str(snapshot.get("report_date") or "")
    market_sample_date = str(snapshot.get("market_sample_date") or "")
    thesis_items = queues.get("thesis_watch") or []
    delayed_thesis_count = sum(
        1
        for item in thesis_items
        if isinstance(item, dict)
        and any(
            token in str(item.get("confirmation_gap") or "")
            for token in ("下一个交易日", "尚未覆盖", "价格样本停在", "先刷新 canonical")
        )
    )
    ready_immediate_candidates = [
        item
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict)
        and str(item.get("radar_bucket") or "") in {"strong_alert", "strong_candidate"}
        and str(item.get("triage_action") or "") not in {"background_only", "risk_review"}
        and not any(
            token in str(item.get("confirmation_gap") or "")
            for token in ("接下来要确认", "仍缺", "仍需确认", "待确认", "缺少 canonical", "不能确认", "尚未覆盖")
        )
    ]
    if immediate_count < min_immediate:
        strong_buckets = {
            str(item.get("radar_bucket") or "")
            for item in (snapshot.get("objects") or [])
            if isinstance(item, dict)
        }
        if immediate_count == 0 and report_date > market_sample_date and delayed_thesis_count >= 2:
            passed_checks.append(
                "Immediate Research 当前为空，但前排对象大多仍在等待下一交易日价格确认，周末/盘后样本口径可接受。"
            )
        elif immediate_count == 0 and "strong_candidate" not in strong_buckets and "strong_alert" not in strong_buckets:
            passed_checks.append(
                "Immediate Research 当前为空，但本轮没有足够强的 strong_candidate / strong_alert，对安静交易日口径可接受。"
            )
        elif immediate_count == 0 and not ready_immediate_candidates:
            passed_checks.append(
                "Immediate Research 当前为空，但前排对象仍普遍处于待确认/待跟踪状态，留在 thesis_watch 或 risk_review 更符合口径。"
            )
        else:
            warnings.append(f"Immediate Research Queue 数量偏低：{immediate_count} < {min_immediate}")
    elif immediate_count > max_immediate:
        warnings.append(f"Immediate Research Queue 数量偏高：{immediate_count} > {max_immediate}")
    else:
        passed_checks.append(f"Immediate Research Queue 数量在目标区间内：{immediate_count}")

    stale_immediate: list[str] = []
    for item in immediate_items:
        if not isinstance(item, dict) or str(item.get("radar_object_type") or "") != "company":
            continue
        context = item.get("price_context") or {}
        lag_days = int(context.get("lag_days") or 0) if isinstance(context, dict) else 0
        confirmation_gap = str(item.get("confirmation_gap") or "")
        if (context and lag_days > 0) or "尚未覆盖" in confirmation_gap or "缺少 canonical 收盘样本" in confirmation_gap:
            stale_immediate.append(str(item.get("radar_object_name") or "unknown"))
    if stale_immediate:
        blockers.append("Immediate Research 仍包含缺少价格确认的公司对象：" + " / ".join(stale_immediate))
    elif immediate_items:
        passed_checks.append("Immediate Research 队列已通过价格确认门槛")

    summary_counts = extract_summary_queue_counts(report_text)
    if not summary_counts:
        warnings.append("顶部摘要缺少研究分流计数字段")
    elif summary_counts != queue_counts:
        blockers.append(f"顶部摘要研究分流计数与 handoff 不一致：summary={summary_counts} handoff={queue_counts}")
    else:
        passed_checks.append("顶部摘要研究分流计数与 handoff 一致")

    freshness_reference_now = parse_datetime_text(snapshot.get("generated_at"))
    freshness = summarize_snapshot_freshness(snapshot, now=freshness_reference_now)
    sample_age = freshness.get("sample_age_days")
    freshness_lag = freshness.get("freshness_lag_days")
    warn_age = optional_int(quality.get("warn_if_sample_age_days_gt"))
    fail_age = optional_int(quality.get("fail_if_sample_age_days_gt"))
    warn_lag = optional_int(quality.get("warn_if_freshness_lag_days_gt"))
    fail_lag = optional_int(quality.get("fail_if_freshness_lag_days_gt"))
    if sample_age is None:
        warnings.append("无法计算市场样本新鲜度")
    else:
        if fail_lag is not None and freshness_lag is not None and freshness_lag > fail_lag:
            blockers.append(f"市场样本落后于期望样本日期：freshness_lag_days={freshness_lag} > {fail_lag}")
        elif warn_lag is not None and freshness_lag is not None and freshness_lag > warn_lag:
            warnings.append(f"市场样本落后于期望样本日期：freshness_lag_days={freshness_lag} > {warn_lag}")
        elif freshness_lag == 0:
            passed_checks.append(
                "市场样本已与期望样本日期对齐：sample_age_days={age} / freshness_lag_days=0".format(
                    age=sample_age,
                )
            )
        elif fail_age is not None and sample_age > fail_age:
            blockers.append(f"市场样本过旧：sample_age_days={sample_age} > {fail_age}")
        elif warn_age is not None and sample_age > warn_age:
            warnings.append(f"市场样本偏旧：sample_age_days={sample_age} > {warn_age}")
        else:
            passed_checks.append(
                "市场样本新鲜度在规则范围内：sample_age_days={age} / freshness_lag_days={lag}".format(
                    age=sample_age,
                    lag=freshness_lag if freshness_lag is not None else "unknown",
                )
            )

    if quality.get("require_inventory_alignment"):
        inventory_run_id = str(inventory.get("run_id") or "").strip()
        snapshot_run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "").strip()
        if not inventory_run_id:
            blockers.append("缺少 Radar catalyst inventory 输出")
        elif inventory_run_id != snapshot_run_id:
            blockers.append(f"Catalyst inventory 与 snapshot run_id 不一致：inventory={inventory_run_id} snapshot={snapshot_run_id}")
        else:
            passed_checks.append("Catalyst inventory 与 snapshot 已对齐")

    top_limit = int(rules.get("top_opportunities_limit") or 5)
    top_names = extract_top_opportunity_names(report_text, top_limit)
    if quality.get("require_immediate_items_in_top_opportunities"):
        missing = [item.get("radar_object_name") for item in immediate_items if item.get("radar_object_name") not in top_names]
        if missing:
            warnings.append("Top Opportunities 未完整覆盖 immediate_research 对象：" + " / ".join(str(x) for x in missing))
        else:
            passed_checks.append("Top Opportunities 已覆盖 immediate_research 队列")

    excluded = {str(x) for x in (rules.get("top_opportunities_exclude_triage") or [])}
    top_name_set = set(top_names)
    top_objects = [
        item
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict) and str(item.get("radar_object_name") or "") in top_name_set
    ]
    bad_top = [
        str(item.get("radar_object_name") or "")
        for item in top_objects
        if str(item.get("triage_action") or "") in excluded
    ]
    if bad_top:
        blockers.append("Top Opportunities 含被禁止的背景对象：" + " / ".join(bad_top))
    else:
        passed_checks.append("Top Opportunities 未包含 background_only 对象")

    directional_conflicts = [
        f"{str(item.get('radar_object_name') or '')}({reason})"
        for item in top_objects
        for reason in [directional_conflict_reason(item)]
        if reason
    ]
    if directional_conflicts:
        blockers.append("Top Opportunities 含方向不清或反向的大宗商品线索：" + " / ".join(directional_conflicts))
    else:
        passed_checks.append("Top Opportunities 未把反向/混合大宗商品线索当作主线机会")

    if quality.get("require_kimi_research_harness"):
        missing_verdict_top = []
        fallback_verdict_top = []
        rejected_top = []
        for item in top_objects:
            verdict = kimi_research_verdict(item, kimi_research)
            if not verdict:
                missing_verdict_top.append(str(item.get("radar_object_name") or ""))
                continue
            if verdict.get("model_generated") is not True:
                fallback_verdict_top.append(
                    f"{str(item.get('radar_object_name') or '')}({str(verdict.get('provenance') or 'unknown')})"
                )
                continue
            decision = str(verdict.get("decision") or "").strip()
            if decision in {"watch_only", "reject", "risk_review", "research_gap"}:
                rejected_top.append(f"{str(item.get('radar_object_name') or '')}({decision})")
        if missing_verdict_top:
            blockers.append("Top Opportunities 含未经过 Kimi research harness 预检对象：" + " / ".join(missing_verdict_top))
        elif fallback_verdict_top:
            blockers.append("Top Opportunities 含非模型生成的 Kimi verdict：" + " / ".join(fallback_verdict_top))
        elif rejected_top:
            blockers.append("Top Opportunities 含 Kimi research harness 降权/拒绝对象：" + " / ".join(rejected_top))
        else:
            passed_checks.append("Top Opportunities 已全部经过 Kimi research harness 预检并避开降权/拒绝对象")

        promoted = [
            item
            for item in (kimi_research.get("verdicts") or [])
            if isinstance(item, dict) and str(item.get("decision") or "") == "promote" and int(item.get("weight_delta") or 0) > 0
        ]
        promoted_names = [str(item.get("name") or "") for item in promoted if str(item.get("name") or "")]
        snapshot_by_name = {
            str(item.get("radar_object_name") or ""): item
            for item in (snapshot.get("objects") or [])
            if isinstance(item, dict) and str(item.get("radar_object_name") or "")
        }
        campaign_section = report_campaign_section(report_text)
        absorbed_promoted: list[str] = []
        missing_promoted: list[str] = []
        for name in promoted_names[:top_limit]:
            if name in top_name_set:
                continue
            snapshot_item = snapshot_by_name.get(name)
            if snapshot_item and promoted_company_absorbed_by_top_industry(
                snapshot_item,
                top_name_set=top_name_set,
                campaign_section=campaign_section,
            ):
                absorbed_promoted.append(name)
                continue
            missing_promoted.append(name)
        if missing_promoted and len(top_names) >= top_limit:
            warnings.append("Kimi research harness promoted 对象未进入 Top Opportunities 或主线吸收展示：" + " / ".join(missing_promoted))
        elif absorbed_promoted:
            passed_checks.append("Kimi promoted 公司已作为 Top 行业主线的触发公司展示：" + " / ".join(absorbed_promoted))
        elif promoted_names:
            passed_checks.append("Kimi research harness promoted 对象已进入或本轮 Top 未满")

    if quality.get("forbid_downgraded_earnings_in_top_opportunities"):
        downgraded = [
            f"{str(item.get('radar_object_name') or '')}({opportunity_precheck_status(item)})"
            for item in top_objects
            if opportunity_precheck_status(item) in {"priced_in", "missing_price"}
        ]
        if downgraded:
            blockers.append("Top Opportunities 含应被财报/价格预检降权的对象：" + " / ".join(downgraded))
        else:
            passed_checks.append("Top Opportunities 已剔除已定价或缺价格的纯业绩对象；首日尚未产生的对象进入自动补齐跟踪")

    if quality.get("require_industry_tracking_symbols_in_report"):
        missing_tracking: list[str] = []
        for item in top_objects:
            if str(item.get("radar_object_type") or "") != "industry":
                continue
            name = str(item.get("radar_object_name") or "")
            primary = [str(x).strip() for x in (item.get("primary_symbols") or []) if str(x).strip()]
            etfs = [str(x).strip() for x in (item.get("etf_proxies") or []) if str(x).strip()]
            missing = [symbol for symbol in [*(primary[:2]), *(etfs[:2])] if symbol and symbol not in report_text]
            if missing:
                missing_tracking.append(f"{name}[{'/'.join(missing)}]")
        if missing_tracking:
            blockers.append("Top industry opportunities 未展示代表股/ETF：" + " / ".join(missing_tracking))
        else:
            passed_checks.append("Top industry opportunities 已展示代表股/ETF 跟踪口径")

    report_status = "pass" if not blockers else "fail"
    return {
        "generated_at": generated_at,
        "status": report_status,
        "rule_version": str(rules.get("version") or "v1"),
        "sample_date": str(snapshot.get("market_sample_date") or snapshot.get("as_of_date") or ""),
        "report_date": str(snapshot.get("report_date") or snapshot.get("event_window_end_date") or snapshot.get("as_of_date") or ""),
        "report_line_count": report_line_count,
        "sample_age_days": sample_age,
        "expected_sample_date": str(freshness.get("expected_sample_date") or ""),
        "freshness_lag_days": freshness_lag,
        "run_id": str(snapshot.get("run_id") or snapshot.get("radar_run_id") or ""),
        "report_footer_run_id": extract_report_run_id(report_text),
        "sidecar_run_ids": sidecar_run_ids,
        "queue_counts": {name: len(items) for name, items in queues.items() if isinstance(items, list)},
        "top_opportunities": top_names,
        "source_readiness_status": str(source_readiness.get("status") or ""),
        "kimi_editorial_status": kimi_status or "missing",
        "kimi_research_status": kimi_research_status or "missing",
        "price_freshness_status": price_refresh_status or "missing",
        "fundamental_coverage_status": fundamental_coverage_status or "missing",
        "news_verification_status": news_verification_status or "missing",
        "data_substrate_audit_status": data_substrate_audit_status or "missing",
        "ipo_watchlist_status": ipo_status,
        "hk_ipo_watchlist_status": hk_ipo_status,
        "ipo_watchlist_count": len(ipo_items),
        "hk_ipo_watchlist_count": len(hk_ipo_items),
        "blockers": blockers,
        "warnings": warnings,
        "passed_checks": passed_checks,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_report_quality"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar 日报质量检查 {payload.get("report_date") or payload.get("sample_date") or "unknown"}"',
        "---",
        "",
        "# Radar 日报质量检查",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 市场样本日期：`{payload.get('sample_date')}`",
        f"- 事件窗口日期：`{payload.get('report_date')}`",
        f"- 日报行数：`{payload.get('report_line_count')}`",
        f"- 样本年龄：`{payload.get('sample_age_days')}`",
        f"- 期望样本日期：`{payload.get('expected_sample_date')}`",
        f"- freshness lag：`{payload.get('freshness_lag_days')}`",
        f"- 运行批次：`{payload.get('run_id')}`",
        f"- 生成时间：`{payload.get('generated_at')}`",
        f"- Source readiness：`{payload.get('source_readiness_status')}`",
        f"- PM 编辑摘要层：`{payload.get('kimi_editorial_status')}`",
        f"- Kimi research harness：`{payload.get('kimi_research_status')}`",
        f"- 价格补数：`{payload.get('price_freshness_status')}`",
        f"- 财务补抓：`{payload.get('fundamental_coverage_status')}`",
        f"- 新闻补核：`{payload.get('news_verification_status')}`",
        f"- 数据底座审计：`{payload.get('data_substrate_audit_status')}`",
        f"- A股 IPO 预检：`{payload.get('ipo_watchlist_status')}` / `{payload.get('ipo_watchlist_count')}`",
        f"- 港股 IPO 预检：`{payload.get('hk_ipo_watchlist_status')}` / `{payload.get('hk_ipo_watchlist_count')}`",
        f"- 队列计数：`{payload.get('queue_counts')}`",
        f"- Top Opportunities：`{' / '.join(payload.get('top_opportunities') or [])}`",
        "",
        "## 通过项",
        "",
    ]
    passed = payload.get("passed_checks") or []
    if passed:
        for item in passed:
            lines.append(f"- {item}")
    else:
        lines.append("- 无")
    lines.extend(["", "## Warnings", ""])
    warnings = payload.get("warnings") or []
    if warnings:
        for item in warnings:
            lines.append(f"- {item}")
    else:
        lines.append("- 无")
    lines.extend(["", "## Blockers", ""])
    blockers = payload.get("blockers") or []
    if blockers:
        for item in blockers:
            lines.append(f"- {item}")
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def validate_report_pdf(report_path: Path, pdf_path: Path) -> tuple[bool, str]:
    if not pdf_path.exists():
        return False, "missing_pdf"
    try:
        report_stat = report_path.stat()
        pdf_stat = pdf_path.stat()
        if pdf_stat.st_size < 1024:
            return False, f"pdf_too_small:{pdf_stat.st_size}"
        if pdf_stat.st_mtime + 1 < report_stat.st_mtime:
            return False, "stale_pdf_mtime"
        with pdf_path.open("rb") as handle:
            head = handle.read(8)
            handle.seek(max(pdf_stat.st_size - 2048, 0))
            tail = handle.read()
        if not head.startswith(b"%PDF"):
            return False, "missing_pdf_header"
        if b"%%EOF" not in tail:
            return False, "missing_pdf_eof"
        return True, ""
    except OSError as exc:
        return False, f"os_error:{exc}"


def main() -> int:
    args = parse_args()
    rules = load_json(args.rules)
    snapshot = load_json(args.snapshot)
    inventory = load_json(args.inventory) if args.inventory.exists() else {}
    handoff = load_json(args.handoff)
    source_readiness = load_json(args.source_readiness) if args.source_readiness.exists() else {}
    kimi_editorial = load_json(args.kimi_editorial) if args.kimi_editorial.exists() else {}
    kimi_research = load_json(args.kimi_research) if args.kimi_research.exists() else {}
    price_freshness = load_json(args.price_freshness) if args.price_freshness.exists() else {}
    fundamental_coverage = load_json(args.fundamental_coverage) if args.fundamental_coverage.exists() else {}
    news_verification = load_json(args.news_verification) if args.news_verification.exists() else {}
    data_substrate_audit = load_json(args.data_substrate_audit) if args.data_substrate_audit.exists() else {}
    ipo_watchlist = load_json(args.ipo_watchlist) if args.ipo_watchlist.exists() else {}
    hk_ipo_watchlist = load_json(args.hk_ipo_watchlist) if args.hk_ipo_watchlist.exists() else {}
    structural_signal = load_json(args.structural_signal) if args.structural_signal.exists() else {}
    report_text = args.report.read_text(encoding="utf-8")
    report_pdf_ok, report_pdf_issue = validate_report_pdf(args.report, args.report_pdf)
    payload = check_quality(
        rules=rules,
        snapshot=snapshot,
        inventory=inventory,
        handoff=handoff,
        report_text=report_text,
        report_pdf_exists=report_pdf_ok,
        report_pdf_issue=report_pdf_issue,
        source_readiness=source_readiness,
        kimi_editorial=kimi_editorial,
        kimi_research=kimi_research,
        price_freshness=price_freshness,
        fundamental_coverage=fundamental_coverage,
        news_verification=news_verification,
        data_substrate_audit=data_substrate_audit,
        ipo_watchlist=ipo_watchlist,
        hk_ipo_watchlist=hk_ipo_watchlist,
        structural_signal=structural_signal,
    )
    write_json(args.json_output, payload)
    write_text(args.md_output, render_markdown(payload))
    if args.json_output.resolve() == DEFAULT_JSON_OUTPUT.resolve():
        reconcile_harness_current_health(reason="report_quality_updated")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload.get("status") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
