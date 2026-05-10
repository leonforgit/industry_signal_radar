#!/usr/bin/env python3
"""Build a structural-signal sidecar for longer-horizon Radar theses."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_INPUT_INVENTORY = ROOT / "output" / "inventory" / "radar_catalyst_inventory_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_structural_signal_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_structural_signal_latest.md"

STRUCTURAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "供需/价格周期": ("涨价", "价格大幅", "价格上行", "紧缺", "供需", "库存", "去库", "补库", "产能利用"),
    "订单/需求验证": ("订单", "中标", "合同", "框架协议", "交付", "客户", "销量", "出货"),
    "产能/资本开支": ("扩产", "产能", "投产", "产线", "募投", "资本开支", "建设项目", "项目投资"),
    "政策/标准/牌照": ("政策", "规划", "标准", "获批", "批准", "许可", "牌照", "注册证", "审批"),
    "资本动作/治理": ("回购", "增持", "股权激励", "员工持股", "定增", "并购", "重组", "发行H股", "分拆"),
    "基本面质量": ("同比增长", "扭亏", "双增", "毛利较上年增长", "现金流改善", "经营质量"),
    "全球化/国产替代": ("出海", "出口", "海外", "国际", "国产替代", "自主可控"),
}
NOISE_KEYWORDS = (
    "续聘会计师",
    "独立董事提名人",
    "年度报告摘要",
    "监管函",
    "问询函",
    "处罚",
    "维持",
    "评级",
    "目标价",
    "持续推荐",
)
NEGATIVE_CONTEXT_KEYWORDS = ("同比下降", "亏损扩大", "净亏损", "下滑", "承压")
GENERIC_AGGREGATE_EVIDENCE_PATTERN = re.compile(r"^(政策|舆情|政策/舆情|行业|新闻)事件\s*\d+\s*条$")
RESEARCH_PUBLISHER_SUFFIXES = ("证券", "期货", "研究所", "研究院", "投顾", "财富", "资管")
RESEARCH_PUBLISHER_NAMES = ("中金公司", "华泰证券", "国泰君安", "中信证券", "招商证券", "东吴证券", "海通证券", "广发证券", "申万宏源", "光大证券")
RESEARCH_VIEW_KEYWORDS = ("研报", "研究报告", "发布研报", "称，", "认为", "预计", "维持", "目标价", "施工旺季", "景气", "产业链")
OWN_COMPANY_ACTION_KEYWORDS = (
    "净利润",
    "营业收入",
    "归母",
    "回购",
    "增持",
    "减持",
    "分红",
    "战略合作",
    "启动合作",
    "签订",
    "中标",
    "合同",
    "股权",
    "定增",
    "发行",
    "获批",
    "监管",
    "处罚",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT)
    parser.add_argument("--input-inventory", type=Path, default=DEFAULT_INPUT_INVENTORY)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--limit", type=int, default=8)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def is_generic_aggregate_evidence(text: str) -> bool:
    cleaned = clean_text(text)
    return bool(GENERIC_AGGREGATE_EVIDENCE_PATTERN.match(cleaned))


def event_texts(item: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for event in item.get("supporting_events") or []:
        if not isinstance(event, dict):
            continue
        text = clean_text(event.get("title") or event.get("headline") or event.get("summary") or event.get("event_type"))
        if text and not is_generic_aggregate_evidence(text):
            texts.append(text)
    for evidence in item.get("key_evidence") or []:
        text = clean_text(evidence)
        if text and not is_generic_aggregate_evidence(text):
            texts.append(text)
    return texts


def looks_like_research_publisher(name: str) -> bool:
    return name in RESEARCH_PUBLISHER_NAMES or any(name.endswith(suffix) for suffix in RESEARCH_PUBLISHER_SUFFIXES)


def is_publisher_research_view(name: str, text: str) -> bool:
    if not name or not looks_like_research_publisher(name):
        return False
    prefix = f"{name}："
    if not text.startswith(prefix):
        return False
    body = text[len(prefix) :].strip()
    if not body:
        return False
    if "发布研报" in body or "研报称" in body or "研究报告" in body:
        return True
    first_clause = body[:80]
    if any(keyword in first_clause for keyword in OWN_COMPANY_ACTION_KEYWORDS):
        return False
    return any(keyword in first_clause for keyword in RESEARCH_VIEW_KEYWORDS)


def category_hits(texts: list[str]) -> dict[str, list[str]]:
    signal_texts = [text for text in texts if not any(token in text for token in NOISE_KEYWORDS)]
    joined = "；".join(signal_texts)
    hits: dict[str, list[str]] = {}
    for category, keywords in STRUCTURAL_KEYWORDS.items():
        matched = [keyword for keyword in keywords if keyword in joined]
        if category == "基本面质量" and any(token in joined for token in NEGATIVE_CONTEXT_KEYWORDS) and not any(
            token in joined for token in ("扭亏", "双增", "毛利较上年增长", "经营质量")
        ):
            matched = []
        if matched:
            hits[category] = matched[:4]
    return hits


def inventory_index(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("radar_object_id") or ""): item
        for item in (inventory.get("inventory") or [])
        if isinstance(item, dict) and str(item.get("radar_object_id") or "").strip()
    }


def price_reaction(item: dict[str, Any]) -> tuple[float | None, str]:
    context = item.get("price_context") or {}
    if not isinstance(context, dict) or not context:
        return None, ""
    try:
        daily_return = float(context.get("daily_return"))
    except (TypeError, ValueError):
        daily_return = None
    close = context.get("close")
    as_of_date = str(context.get("as_of_date") or "")
    if daily_return is None:
        return None, f"价格样本 {as_of_date or 'unknown'}"
    return daily_return, f"收盘 {close} / 涨跌 {daily_return * 100:+.1f}% / 样本 {as_of_date or 'unknown'}"


def score_item(item: dict[str, Any], inv: dict[str, Any] | None) -> dict[str, Any] | None:
    name = str(item.get("radar_object_name") or "")
    texts = [text for text in event_texts(item) if not is_publisher_research_view(name, text)]
    hits = category_hits(texts)
    if not hits:
        return None
    if not any(text and not is_generic_aggregate_evidence(text) for text in texts):
        return None
    joined = "；".join(texts)
    if any(token in joined for token in NOISE_KEYWORDS) and len(hits) <= 1:
        return None
    if any(token in joined for token in NEGATIVE_CONTEXT_KEYWORDS) and len(hits) <= 1:
        return None

    seen_count = int((inv or {}).get("seen_count") or 0)
    state = str((inv or {}).get("current_state") or "")
    daily_return, reaction = price_reaction(item)
    event_count = len(item.get("supporting_events") or [])
    evidence_quality = str(item.get("evidence_quality") or "")
    triage_action = str(item.get("triage_action") or "")

    score = 35 + len(hits) * 12 + min(event_count, 4) * 4 + min(seen_count, 8) * 3
    if state == "confirmed":
        score += 8
    if evidence_quality == "structured_confirmed":
        score += 8
    if triage_action == "thesis_watch":
        score += 4
    if daily_return is not None:
        if abs(daily_return) > 1:
            return None
        if daily_return < 0 and any(category in hits for category in ("基本面质量", "供需/价格周期", "订单/需求验证")):
            score += 8
        if daily_return > 0.08:
            score -= 8
    score = max(0, min(100, score))
    if score < 55:
        return None

    categories = list(hits)
    thesis = f"{name} 出现 {' + '.join(categories[:2])} 线索"
    if daily_return is not None and daily_return <= 0.02:
        thesis += "，价格尚未充分反映"
    elif daily_return is not None and daily_return > 0.08:
        thesis += "，但短线可能已抢跑"

    evidence: list[str] = []
    for text in texts:
        if any(token in text for token in NOISE_KEYWORDS):
            continue
        if any(token in text for token in NEGATIVE_CONTEXT_KEYWORDS) and not any(token in text for token in ("扭亏", "双增", "毛利较上年增长")):
            continue
        if any(keyword in text for keywords in hits.values() for keyword in keywords):
            evidence.append(text)
        if len(evidence) >= 3:
            break
    if reaction:
        evidence.append(reaction)

    return {
        "radar_object_id": item.get("radar_object_id"),
        "name": name,
        "object_type": item.get("radar_object_type"),
        "primary_symbols": item.get("primary_symbols") or [],
        "industry_or_scope": item.get("radar_object_scope") or "",
        "structural_score": score,
        "categories": categories,
        "keyword_hits": hits,
        "thesis": thesis,
        "evidence": evidence[:4],
        "inventory_seen_count": seen_count,
        "inventory_state": state,
        "watch_window": "30-120d",
        "next_research_action": next_action(categories, item),
        "risk_or_disconfirming_evidence": risk_text(item, categories),
    }


def next_action(categories: list[str], item: dict[str, Any]) -> str:
    if "供需/价格周期" in categories:
        return "补产品价格、库存和同链条公司验证，确认是否从单点业绩扩散为周期线索"
    if "订单/需求验证" in categories:
        return "补订单金额、客户质量和交付周期，判断是否能进入 1-2 个季度收入确认"
    if "产能/资本开支" in categories:
        return "补产能投放节奏、资本开支强度和下游需求，确认是否进入扩产周期"
    if "资本动作/治理" in categories:
        return "补公告原文、交易结构和股东动机，判断是否形成中期资本市场主线"
    if "基本面质量" in categories:
        return "拆收入、毛利率、现金流和存货，确认业绩质量是否可持续"
    return "补 30/60/120 天证据轨迹，确认是否由事件线索升级为结构性 thesis"


def risk_text(item: dict[str, Any], categories: list[str]) -> str:
    failure = clean_text(item.get("failure_mode") or item.get("confirmation_gap") or "")
    if failure:
        return failure
    if len(categories) == 1:
        return "目前仍是单一证据链，若后续没有第二来源确认，应降级为短期事件"
    return "若价格先行透支或后续数据不再改善，应降级为观察线索"


def build_payload(snapshot: dict[str, Any], inventory: dict[str, Any], *, limit: int) -> dict[str, Any]:
    inv_index = inventory_index(inventory)
    candidates: list[dict[str, Any]] = []
    for item in snapshot.get("objects") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("triage_action") or "") == "risk_review":
            continue
        scored = score_item(item, inv_index.get(str(item.get("radar_object_id") or "")))
        if scored:
            candidates.append(scored)
    candidates.sort(key=lambda item: (-int(item.get("structural_score") or 0), str(item.get("name") or "")))
    category_counter: Counter[str] = Counter()
    for item in candidates:
        category_counter.update(str(category) for category in (item.get("categories") or []))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report_date": str(snapshot.get("event_window_end_date") or snapshot.get("report_date") or snapshot.get("as_of_date") or ""),
        "run_id": str(snapshot.get("radar_run_id") or snapshot.get("run_id") or ""),
        "status": "pass",
        "method": "heuristic_structural_signal_v1",
        "category_counts": dict(category_counter),
        "items": candidates[:limit],
        "candidate_count": len(candidates),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_structural_signal"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Structural Signal {payload.get("report_date") or "unknown"}"',
        "---",
        "",
        "# Radar Structural Signal",
        "",
        f"- 状态：`{payload.get('status')}`",
        f"- 日期：`{payload.get('report_date')}`",
        f"- 候选数：`{payload.get('candidate_count')}`",
        "",
    ]
    items = payload.get("items") or []
    if not items:
        lines.append("- 当前没有达到阈值的中长期结构线索。")
        return "\n".join(lines) + "\n"
    for item in items:
        lines.extend(
            [
                f"## {item.get('name')}",
                "",
                f"- 分数：`{item.get('structural_score')}` | 类别：{' / '.join(str(x) for x in (item.get('categories') or []))} | 窗口：`{item.get('watch_window')}`",
                f"- Thesis：{item.get('thesis')}",
                f"- 动作：{item.get('next_research_action')}",
                f"- 风险：{item.get('risk_or_disconfirming_evidence')}",
            ]
        )
        for evidence in item.get("evidence") or []:
            lines.append(f"- 证据：{evidence}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    snapshot = load_json(args.input_snapshot)
    inventory = load_json(args.input_inventory)
    payload = build_payload(snapshot, inventory, limit=max(args.limit, 1))
    write_json(args.output_json, payload)
    write_text(args.output_md, render_markdown(payload))
    print(json.dumps({"status": payload["status"], "item_count": len(payload["items"]), "output_json": str(args.output_json)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
