#!/usr/bin/env python3
"""Build a calibration-oriented markdown summary from the latest Radar snapshot."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_INPUT_BARK = ROOT / "output" / "snapshots" / "radar_bark_summary_latest.json"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "reports"
DEFAULT_LATEST_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_calibration_latest.md"

BUCKET_ORDER = {
    "strong_alert": 0,
    "strong_candidate": 1,
    "research_candidate": 2,
    "observe": 3,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT)
    parser.add_argument("--input-bark-summary", type=Path, default=DEFAULT_INPUT_BARK)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return payload


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


def sorted_objects(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for item in snapshot.get("objects") or [] if isinstance(item, dict)]
    items.sort(
        key=lambda item: (
            BUCKET_ORDER.get(str(item.get("radar_bucket") or ""), 99),
            -int(item.get("radar_score") or 0),
            int(item.get("rank_overall") or 9999),
        )
    )
    return items


def bark_blockers(item: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if item.get("radar_bucket") != "strong_alert" and item.get("alert_level") != "strong_alert":
        blockers.append("未进入 strong_alert")
    if not bool(item.get("is_new")) and not bool(item.get("is_upgraded")):
        blockers.append("不是新增/升级对象")
    if int(item.get("why_now_strength") or 0) < 60:
        blockers.append("why_now_strength < 60")
    if int(item.get("confidence") or 0) < 50:
        blockers.append("confidence < 50")
    if int(item.get("followup_value") or 0) < 60:
        blockers.append("followup_value < 60")
    if str(item.get("trigger_state") or "") == "suppressed":
        blockers.append("trigger_state=suppressed")
    return blockers


def top_focus_items(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    return items[:limit]


def blocker_counter(items: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for item in items:
        if item.get("trigger_state") == "bark_candidate":
            continue
        for blocker in bark_blockers(item):
            counter[blocker] += 1
    return counter.most_common()


def triage_items(items: list[dict[str, Any]], action: str, limit: int = 8) -> list[dict[str, Any]]:
    matched = [item for item in items if str(item.get("triage_action") or "") == action]
    return matched[:limit]


def format_focus_line(item: dict[str, Any]) -> str:
    blockers = bark_blockers(item)
    blocker_text = "；".join(blockers[:3]) if blockers else "已满足 Bark 条件"
    return (
        f"- {item.get('radar_object_name')} | {item.get('radar_bucket')} | score={int(item.get('radar_score') or 0)} | "
        f"why_now={int(item.get('why_now_strength') or 0)} | confidence={int(item.get('confidence') or 0)} | "
        f"followup={int(item.get('followup_value') or 0)} | Bark 阻塞：{blocker_text}"
    )


def build_report(snapshot: dict[str, Any], bark_summary: dict[str, Any]) -> str:
    items = sorted_objects(snapshot)
    summary = snapshot.get("summary") or {}
    run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "")
    as_of_date = str(snapshot.get("as_of_date") or "")
    generated_at = format_timestamp_label(snapshot.get("generated_at"))
    bark_trigger_count = int(bark_summary.get("trigger_count") or 0)
    focus_items = top_focus_items(items)
    blockers = blocker_counter(focus_items)
    low_confidence_front = [
        item for item in items
        if item.get("radar_bucket") in {"strong_candidate", "research_candidate"}
        and int(item.get("confidence") or 0) < 50
    ][:5]
    downgraded_items = [
        item for item in items
        if any("排序调整：" in str(text) for text in (item.get("key_evidence") or []))
    ]
    downgraded_to_observe = [item for item in downgraded_items if item.get("radar_bucket") == "observe"][:6]
    early_signal_kept = [
        item
        for item in downgraded_items
        if item.get("radar_bucket") in {"research_candidate", "strong_candidate"}
        and any("保留为早期主题/扩散线索" in str(text) for text in (item.get("key_evidence") or []))
    ][:6]
    immediate_items = triage_items(items, "immediate_research", limit=8)
    watch_items = triage_items(items, "thesis_watch", limit=8)

    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_calibration"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar 排序校准摘要 {as_of_date} 样本"',
        "---",
        "",
        "# Radar 排序校准摘要",
        "",
        f"市场样本日期 {as_of_date} | 本地生成 {generated_at or 'unknown'} | 运行批次 {run_id}",
        "",
        "## 一、总体判断",
        "",
        (
            f"- 本轮共有 `{int(summary.get('candidate_count') or 0)}` 个对象进入 snapshot，"
            f"`strong_candidate {int(summary.get('strong_candidate_count') or 0)}` 个，"
            f"`research_candidate {int(summary.get('research_candidate_count') or 0)}` 个，"
            f"`strong_alert {int(summary.get('strong_alert_count') or 0)}` 个。"
        ),
        f"- Bark 实际触发 `{bark_trigger_count}` 条。",
    ]
    if generated_at and as_of_date and generated_at[:10] != as_of_date:
        lines.append(f"- 这份校准摘要基于 `{as_of_date}` 的市场样本重建，本地最近一次生成时间为 `{generated_at}`。")

    if bark_trigger_count == 0:
        lines.append("- 当前系统更像“研究优先级排序器”，而不是“必须每天出提醒的告警器”；这一轮先不为了出 Bark 而下调阈值。")
    else:
        lines.append("- 当前系统已经出现可触发 Bark 的对象，后续重点转到去重和升级重发治理。")

    lines.extend(["", "## 二、前排对象为什么没有进 Bark", ""])
    if focus_items:
        for item in focus_items:
            lines.append(format_focus_line(item))
    else:
        lines.append("- 当前没有可分析对象。")

    lines.extend(["", "## 三、前排阻塞因子分布", ""])
    if blockers:
        for label, count in blockers[:8]:
            lines.append(f"- {label}：{count} 次")
    else:
        lines.append("- 当前前排对象已全部满足 Bark 条件。")

    lines.extend(["", "## 四、低置信但值得跟的对象", ""])
    if low_confidence_front:
        for item in low_confidence_front:
            lines.append(
                f"- {item.get('radar_object_name')} | {item.get('radar_bucket')} | score={int(item.get('radar_score') or 0)} | "
                f"confidence={int(item.get('confidence') or 0)} | why_now={int(item.get('why_now_strength') or 0)} | "
                f"followup={int(item.get('followup_value') or 0)}"
            )
    else:
        lines.append("- 当前前排对象的 confidence 没有显著短板。")

    lines.extend(
        [
            "",
            "## 五、研究分流校准观察",
            "",
        ]
    )
    if immediate_items:
        lines.append("### 当前进入 immediate_research 的对象")
        lines.append("")
        for item in immediate_items:
            lines.append(
                f"- {item.get('radar_object_name')} | {item.get('radar_bucket')} | {item.get('evidence_quality')} | "
                f"confidence={int(item.get('confidence') or 0)} | why_now={int(item.get('why_now_strength') or 0)} | "
                f"{item.get('triage_reason') or '已满足立即研究条件'}"
            )
        lines.append("")
    else:
        lines.append("- 当前没有对象进入 immediate_research。")
        lines.append("")
    if watch_items:
        lines.append("### 当前被压回 thesis_watch 的对象")
        lines.append("")
        for item in watch_items:
            lines.append(
                f"- {item.get('radar_object_name')} | {item.get('radar_bucket')} | {item.get('evidence_quality')} | "
                f"confidence={int(item.get('confidence') or 0)} | why_now={int(item.get('why_now_strength') or 0)} | "
                f"{item.get('triage_reason') or '当前更适合继续跟踪'}"
            )
        lines.append("")
    else:
        lines.append("- 当前没有对象落在 thesis_watch。")
        lines.append("")

    lines.extend(
        [
            "## 六、证据质量降权观察",
            "",
        ]
    )
    if downgraded_to_observe:
        lines.append("### 已被压回 observe 的弱证据对象")
        lines.append("")
        for item in downgraded_to_observe:
            lines.append(
                f"- {item.get('radar_object_name')} | score={int(item.get('radar_score') or 0)} | "
                f"{next((text for text in (item.get('key_evidence') or []) if '排序调整：' in str(text)), '')}"
            )
        lines.append("")
    else:
        lines.append("- 当前没有被明确压回 observe 的弱证据对象。")
        lines.append("")
    if early_signal_kept:
        lines.append("### 仍保留研究资格的早期主题线索")
        lines.append("")
        for item in early_signal_kept:
            lines.append(
                f"- {item.get('radar_object_name')} | {item.get('radar_bucket')} | score={int(item.get('radar_score') or 0)} | "
                f"{next((text for text in (item.get('key_evidence') or []) if '排序调整：' in str(text)), '')}"
            )
        lines.append("")
    else:
        lines.append("- 当前没有需要特别保留研究资格的早期主题线索。")
        lines.append("")

    lines.extend(
        [
            "## 七、下一步校准动作",
            "",
            "- 先继续观察 `strong_candidate` 前排对象是否能在后续运行中自然升级，而不是先人为下调 Bark 门槛。",
            "- 继续观察 `immediate_research / thesis_watch` 的分层是否稳定，尤其是 proxy 和政策驱动对象不要再次被过宽抬升。",
            "- 如果连续多轮都出现 `score 高 / followup 高` 但 `confidence` 偏低的对象，再考虑把日报和 Bark 进一步拆成不同阈值面。",
            "- 优先保留 `早发现优先` 的方向，但继续用 `followup_value` 压制纯热度对象。",
            "",
        ]
    )
    return "\n".join(lines)


def default_dated_output(snapshot: dict[str, Any]) -> Path:
    date_text = str(snapshot.get("as_of_date") or datetime.now(timezone.utc).date().isoformat()).replace("-", "")
    return DEFAULT_OUTPUT_DIR / f"radar_calibration_{date_text}.md"


def main() -> int:
    args = parse_args()
    snapshot = load_json(args.input_snapshot)
    bark_summary = load_json(args.input_bark_summary)
    report_text = build_report(snapshot, bark_summary)
    latest_output = args.latest_output
    dated_output = args.output or default_dated_output(snapshot)
    write_text(latest_output, report_text)
    write_text(dated_output, report_text)
    print(
        json.dumps(
            {
                "latest_output": str(latest_output),
                "dated_output": str(dated_output),
                "run_id": snapshot.get("run_id") or snapshot.get("radar_run_id"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
