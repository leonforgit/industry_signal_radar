#!/usr/bin/env python3
"""Build a research handoff board from the Radar opportunity snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_INPUT_INVENTORY = ROOT / "output" / "inventory" / "radar_catalyst_inventory_latest.json"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "handoffs"
DEFAULT_LATEST_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_research_handoff_latest.md"
DEFAULT_LATEST_JSON_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_research_handoff_latest.json"
TRIAGE_ORDER = [
    ("immediate_research", "Immediate Research Queue"),
    ("thesis_watch", "Thesis Watch"),
    ("risk_review", "Risk Review"),
]
TRIAGE_PRIORITY = {
    "immediate_research": 0,
    "thesis_watch": 1,
    "risk_review": 2,
    "background_only": 3,
}
EVIDENCE_PRIORITY = {
    "structured_confirmed": 0,
    "proxy_confirmed": 1,
    "early_thematic": 2,
    "risk_signal": 3,
    "narrative_only": 4,
}
HANDOFF_QUEUE_LIMITS = {
    "immediate_research": 12,
    "thesis_watch": 36,
    "risk_review": 24,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT, help="Radar snapshot JSON path.")
    parser.add_argument(
        "--input-inventory",
        type=Path,
        default=DEFAULT_INPUT_INVENTORY,
        help="Radar catalyst inventory JSON path.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional dated handoff output path.")
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT, help="Latest handoff output path.")
    parser.add_argument("--json-output", type=Path, default=None, help="Optional dated handoff JSON output path.")
    parser.add_argument("--latest-json-output", type=Path, default=DEFAULT_LATEST_JSON_OUTPUT, help="Latest handoff JSON output path.")
    return parser.parse_args()


def load_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return payload


def load_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return payload


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_timestamp_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def triage_items(snapshot: dict[str, Any], action: str, limit: int) -> list[dict[str, Any]]:
    items = [
        item
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict) and str(item.get("triage_action") or "") == action
    ]
    items.sort(
        key=lambda item: (
            TRIAGE_PRIORITY.get(str(item.get("triage_action") or ""), 99),
            EVIDENCE_PRIORITY.get(str(item.get("evidence_quality") or ""), 99),
            -int(item.get("radar_score") or 0),
            int(item.get("rank_overall") or 9999),
            str(item.get("radar_object_name") or ""),
        )
    )
    return items[:limit]


def has_confirmed_price(item: dict[str, Any]) -> bool:
    if str(item.get("radar_object_type") or "") != "company":
        return True
    context = item.get("price_context") or {}
    if not isinstance(context, dict) or not context:
        return False
    return int(context.get("lag_days") or 0) <= 0 and bool(str(context.get("as_of_date") or "").strip())


def compact_item(item: dict[str, Any], *, inventory_item: dict[str, Any] | None = None) -> dict[str, Any]:
    inventory_item = inventory_item or {}
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "radar_object_id": item.get("radar_object_id"),
        "radar_object_name": item.get("radar_object_name"),
        "radar_object_type": item.get("radar_object_type"),
        "inventory_state": inventory_item.get("current_state") or "open",
        "inventory_previous_state": inventory_item.get("previous_state") or "",
        "inventory_presence": inventory_item.get("presence_status") or "active",
        "inventory_seen_count": int(inventory_item.get("seen_count") or 0),
        "inventory_state_changed": bool(inventory_item.get("state_changed")),
        "inventory_transition_note": inventory_item.get("transition_note") or "not_in_inventory",
        "inventory_first_seen_at": inventory_item.get("first_seen_at") or generated_at,
        "inventory_last_seen_at": inventory_item.get("last_seen_at") or generated_at,
        "radar_bucket": item.get("radar_bucket"),
        "alert_level": item.get("alert_level"),
        "radar_score": item.get("radar_score"),
        "rank_overall": item.get("rank_overall"),
        "catalyst_type": item.get("catalyst_type"),
        "hard_or_soft": item.get("hard_or_soft"),
        "catalyst_stage": item.get("catalyst_stage"),
        "evidence_quality": item.get("evidence_quality"),
        "triage_action": item.get("triage_action"),
        "triage_reason": build_triage_reason(item),
        "why_now": item.get("why_now"),
        "key_evidence": item.get("key_evidence") or [],
        "supporting_events": item.get("supporting_events") or [],
        "confirmation_gap": item.get("confirmation_gap"),
        "next_milestone": item.get("next_milestone"),
        "milestone_due_window": item.get("milestone_due_window"),
        "research_question": build_research_question(item),
        "research_checkpoints": build_research_checkpoints(item),
        "followup_path": item.get("followup_path") or [],
        "hedge_difficulty": item.get("hedge_difficulty"),
        "failure_mode": item.get("failure_mode"),
        "primary_symbols": item.get("primary_symbols") or [],
        "etf_proxies": item.get("etf_proxies") or [],
        "theme_overlays": item.get("theme_overlays") or [],
        "quant_signal_context": item.get("quant_signal_context") or {},
        "price_context": item.get("price_context") or {},
        "research_links": item.get("research_links") or [],
    }


def build_research_question(item: dict[str, Any]) -> str:
    name = str(item.get("radar_object_name") or "该对象")
    object_type = str(item.get("radar_object_type") or "")
    catalyst_type = str(item.get("catalyst_type") or "")
    if object_type == "company":
        if catalyst_type in {"earnings_guidance", "order_project", "approval_registration"}:
            return f"{name} 这条公司事件，能不能从单点公告升级成可持续的产业链机会？"
        return f"{name} 当前更像一次性公司催化，还是值得继续追踪的持续机会？"
    if object_type == "industry":
        return f"{name} 当前是主题脉冲，还是已经进入可持续的行业级研究窗口？"
    if object_type == "macro":
        return f"{name} 这个代理变量变化，会不会继续传导到行业和具体标的？"
    return f"{name} 当前最值得验证的核心机会假设是什么？"


def build_triage_reason(item: dict[str, Any]) -> str:
    triage_action = str(item.get("triage_action") or "")
    evidence_quality = str(item.get("evidence_quality") or "")
    bucket = str(item.get("radar_bucket") or "")
    if triage_action == "immediate_research":
        return f"{bucket} 且证据质量达到 {evidence_quality}，已经具备立即展开研究的条件。"
    if triage_action == "thesis_watch":
        return f"当前更像早期线索，仍需继续跟里程碑与确认缺口，所以保留在 thesis_watch。"
    if triage_action == "risk_review":
        return "当前对象更适合作为风险复核，而不是正向机会推进。"
    return "当前对象更多承担背景观察作用，不进入主研究队列。"


def build_research_checkpoints(item: dict[str, Any]) -> list[str]:
    checkpoints: list[str] = []
    next_milestone = str(item.get("next_milestone") or "").strip()
    confirmation_gap = str(item.get("confirmation_gap") or "").strip()
    failure_mode = str(item.get("failure_mode") or "").strip()
    followup = [str(value).strip() for value in (item.get("followup_path") or []) if str(value).strip()]
    if next_milestone:
        checkpoints.append(f"里程碑：{next_milestone}")
    checkpoints.extend(followup[:2])
    if confirmation_gap:
        checkpoints.append(f"确认缺口：{confirmation_gap}")
    if failure_mode:
        checkpoints.append(f"失败路径：{failure_mode}")
    deduped: list[str] = []
    seen: set[str] = set()
    for checkpoint in checkpoints:
        if checkpoint in seen:
            continue
        seen.add(checkpoint)
        deduped.append(checkpoint)
        if len(deduped) >= 4:
            break
    return deduped


def build_handoff_payload(snapshot: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("summary") or {}
    inventory_index = {
        str(item.get("radar_object_id") or ""): item
        for item in (inventory.get("inventory") or [])
        if isinstance(item, dict) and str(item.get("radar_object_id") or "").strip()
    }
    immediate_candidates = [
        item
        for item in triage_items(snapshot, "immediate_research", limit=999)
        if has_confirmed_price(item)
    ]
    immediate_items = immediate_candidates[: HANDOFF_QUEUE_LIMITS["immediate_research"]]
    watch_items = triage_items(snapshot, "thesis_watch", limit=HANDOFF_QUEUE_LIMITS["thesis_watch"])
    risk_items = triage_items(snapshot, "risk_review", limit=HANDOFF_QUEUE_LIMITS["risk_review"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": str(snapshot.get("run_id") or snapshot.get("radar_run_id") or ""),
        "as_of_date": str(snapshot.get("as_of_date") or ""),
        "candidate_count": int(summary.get("candidate_count") or 0),
        "inventory_summary": inventory.get("summary") or {},
        "queues": {
            "immediate_research": [compact_item(item, inventory_item=inventory_index.get(str(item.get("radar_object_id") or ""))) for item in immediate_items],
            "thesis_watch": [compact_item(item, inventory_item=inventory_index.get(str(item.get("radar_object_id") or ""))) for item in watch_items],
            "risk_review": [compact_item(item, inventory_item=inventory_index.get(str(item.get("radar_object_id") or ""))) for item in risk_items],
        },
        "priority_items": [
            compact_item(item, inventory_item=inventory_index.get(str(item.get("radar_object_id") or "")))
            for item in (immediate_items[:5] if immediate_items else watch_items[:5])
        ],
    }


def join_list(values: Any, limit: int = 3) -> str:
    if not isinstance(values, list):
        return ""
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return "；".join(cleaned[:limit])


def brief_line(item: dict[str, Any]) -> str:
    symbols = join_list((item.get("primary_symbols") or []) + (item.get("etf_proxies") or []), limit=3) or "无直接标的"
    milestone = str(item.get("next_milestone") or "").strip() or "继续跟踪下一轮确认"
    return (
        "- {name} | {type} | {bucket} | 账本状态：{inventory_state} | 证据质量：{quality} | 里程碑：{milestone} | 标的：{symbols}".format(
            name=str(item.get("radar_object_name") or ""),
            type=str(item.get("radar_object_type") or ""),
            bucket=str(item.get("radar_bucket") or ""),
            inventory_state=str(item.get("inventory_state") or "open"),
            quality=str(item.get("evidence_quality") or ""),
            milestone=milestone,
            symbols=symbols,
        )
    )


def detail_block(index: int, item: dict[str, Any]) -> list[str]:
    supporting = join_list([event.get("event_type") for event in (item.get("supporting_events") or []) if isinstance(event, dict)], limit=3)
    evidence = join_list(item.get("key_evidence") or [], limit=4)
    followup = join_list(item.get("followup_path") or [], limit=3)
    risks = join_list(item.get("risk_flags") or [], limit=3)
    lines = [
        f"### {index}. {item.get('radar_object_name', '')}",
        "",
        f"- 类型 / 分桶：{item.get('radar_object_type', '')} / {item.get('radar_bucket', '')}",
        f"- Why now：{item.get('why_now', '')}",
        f"- 催化剂：{item.get('catalyst_type', '')} | {item.get('hard_or_soft', '')} | {item.get('catalyst_stage', '')}",
        f"- Catalyst Inventory：{item.get('inventory_previous_state', '') or 'new'} -> {item.get('inventory_state', '')} | seen {item.get('inventory_seen_count', 0)} runs",
        f"- 状态迁移备注：{item.get('inventory_transition_note', '') or '无'}",
        f"- 证据质量 / 分流动作：{item.get('evidence_quality', '')} / {item.get('triage_action', '')}",
        f"- 分流原因：{build_triage_reason(item)}",
        f"- 研究问题：{build_research_question(item)}",
        f"- 核心证据：{evidence or '无'}",
        f"- 事件线索：{supporting or '无'}",
        f"- 确认缺口：{item.get('confirmation_gap', '') or '暂无显著缺口'}",
        f"- 下一里程碑：{item.get('next_milestone', '') or '继续跟踪'} | 窗口：{item.get('milestone_due_window', '') or 'open_ended'}",
        f"- 研究动作：{followup or '继续观察下一轮变化'}",
        f"- 对冲难度 / 失败路径：{item.get('hedge_difficulty', '') or 'unknown'} / {item.get('failure_mode', '') or '暂无'}",
        f"- 风险提示：{risks or '无'}",
        f"- 研究检查点：{join_list(build_research_checkpoints(item), limit=4) or '无'}",
        f"- 关联标的：{join_list((item.get('primary_symbols') or []) + (item.get('etf_proxies') or []) + (item.get('theme_overlays') or []), limit=5) or '无'}",
        "",
    ]
    return lines


def render_handoff(snapshot: dict[str, Any], inventory: dict[str, Any]) -> str:
    as_of_date = str(snapshot.get("as_of_date") or "")
    run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "")
    generated_at = format_timestamp_label(snapshot.get("generated_at"))
    summary = snapshot.get("summary") or {}
    payload = build_handoff_payload(snapshot, inventory)
    inventory_summary = payload.get("inventory_summary") or {}
    immediate_items = payload.get("queues", {}).get("immediate_research") or []
    watch_items = payload.get("queues", {}).get("thesis_watch") or []
    risk_items = payload.get("queues", {}).get("risk_review") or []
    lines: list[str] = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_research_handoff"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar 研究交接板 {as_of_date} 样本"',
        "---",
        "",
        "# Radar 研究交接板",
        "",
        f"市场样本日期 {as_of_date} | 本地生成 {generated_at or 'unknown'} | 运行批次 {run_id}",
        "",
        "## 一、交接摘要",
        "",
        "- 这份 handoff 只服务研究动作分流，不重复写整份日报。",
        "- `Immediate Research Queue` 是今天优先打开的对象；`Thesis Watch` 是需要继续盯里程碑的对象；`Risk Review` 是需要防守或复核的对象。",
        "- 这份 handoff 默认同时消费 `Catalyst Inventory`，所以会显式告诉你对象是首次入库、持续跟踪，还是状态升级。",
        (
            f"- 当前交接板基于 `{as_of_date}` 的市场样本重建，本地最近一次生成时间为 `{generated_at}`。"
            if generated_at and as_of_date and generated_at[:10] != as_of_date
            else "- 当前交接板与最新市场样本时间一致。"
        ),
        "- 当前 snapshot 共 `{candidate}` 个对象，其中 `immediate_research {immediate}` 个，`thesis_watch {watch}` 个，`risk_review {risk}` 个。".format(
            candidate=int(summary.get("candidate_count") or 0),
            immediate=len(immediate_items),
            watch=len(watch_items),
            risk=len(risk_items),
        ),
        "- 当前 Catalyst Inventory 为 `confirmed {confirmed} / watching {watching} / open {open_count} / risk_review {risk_count} / expired {expired}`。".format(
            confirmed=int(inventory_summary.get("confirmed_count") or 0),
            watching=int(inventory_summary.get("watching_count") or 0),
            open_count=int(inventory_summary.get("open_count") or 0),
            risk_count=int(inventory_summary.get("risk_review_count") or 0),
            expired=int(inventory_summary.get("expired_count") or 0),
        ),
        "",
        "## 二、Immediate Research Queue",
        "",
    ]
    if immediate_items:
        for item in immediate_items:
            lines.append(brief_line(item))
    else:
        lines.append("- 无")
    lines.extend(["", "## 三、Thesis Watch", ""])
    if watch_items:
        for item in watch_items:
            lines.append(brief_line(item))
    else:
        lines.append("- 无")
    lines.extend(["", "## 四、Risk Review", ""])
    if risk_items:
        for item in risk_items:
            lines.append(brief_line(item))
    else:
        lines.append("- 无")
    lines.extend(["", "## 五、优先展开对象", ""])
    priority_items = immediate_items[:5] if immediate_items else watch_items[:5]
    if priority_items:
        for idx, item in enumerate(priority_items, start=1):
            lines.extend(detail_block(idx, item))
    else:
        lines.append("- 当前没有可以展开的对象。")
        lines.append("")
    lines.extend(
        [
            "## 六、执行提示",
            "",
            "- 只有 `triage_action = immediate_research` 的强对象才应继续考虑 Bark 或立即进入研究。",
            "- `thesis_watch` 默认服务下一轮观察与里程碑确认，不应该因为 headline 热度直接升级成即时提醒。",
            "- `risk_review` 用来提醒这条逻辑更像风险检查而不是机会追踪。",
            "",
        ]
    )
    return "\n".join(lines)


def default_dated_output(snapshot: dict[str, Any]) -> Path:
    date_text = str(snapshot.get("as_of_date") or datetime.now(timezone.utc).date().isoformat()).replace("-", "")
    return DEFAULT_OUTPUT_DIR / f"radar_research_handoff_{date_text}.md"


def default_dated_json_output(snapshot: dict[str, Any]) -> Path:
    date_text = str(snapshot.get("as_of_date") or datetime.now(timezone.utc).date().isoformat()).replace("-", "")
    return DEFAULT_OUTPUT_DIR / f"radar_research_handoff_{date_text}.json"


def main() -> int:
    args = parse_args()
    snapshot = load_snapshot(args.input_snapshot)
    inventory = load_inventory(args.input_inventory)
    handoff_text = render_handoff(snapshot, inventory)
    handoff_payload = build_handoff_payload(snapshot, inventory)
    latest_output = args.latest_output
    dated_output = args.output or default_dated_output(snapshot)
    latest_json_output = args.latest_json_output
    dated_json_output = args.json_output or default_dated_json_output(snapshot)
    write_text(latest_output, handoff_text)
    write_text(dated_output, handoff_text)
    write_json(latest_json_output, handoff_payload)
    write_json(dated_json_output, handoff_payload)
    print(
        json.dumps(
            {
                "latest_output": str(latest_output),
                "dated_output": str(dated_output),
                "latest_json_output": str(latest_json_output),
                "dated_json_output": str(dated_json_output),
                "immediate_research_count": len(triage_items(snapshot, "immediate_research", limit=999)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
