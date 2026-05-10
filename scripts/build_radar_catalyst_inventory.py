#!/usr/bin/env python3
"""Build a persistent Radar catalyst inventory from the latest opportunity snapshot."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "inventory"
DEFAULT_LATEST_JSON_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_catalyst_inventory_latest.json"
DEFAULT_LATEST_MD_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_catalyst_inventory_latest.md"
MAX_HISTORY = 12
EXPIRE_AFTER_MISSED_RUNS = 2

STATE_OPEN = "open"
STATE_WATCHING = "watching"
STATE_CONFIRMED = "confirmed"
STATE_RISK = "risk_review"
STATE_BACKGROUND = "background"
STATE_EXPIRED = "expired"
PRESENCE_ACTIVE = "active"
PRESENCE_MISSING = "missing_recent"

STATE_ORDER = {
    STATE_CONFIRMED: 0,
    STATE_WATCHING: 1,
    STATE_OPEN: 2,
    STATE_RISK: 3,
    STATE_BACKGROUND: 4,
    STATE_EXPIRED: 5,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT, help="Radar snapshot JSON path.")
    parser.add_argument(
        "--previous-inventory",
        type=Path,
        default=DEFAULT_LATEST_JSON_OUTPUT,
        help="Previous Radar catalyst inventory JSON path.",
    )
    parser.add_argument("--json-output", type=Path, default=None, help="Optional dated JSON output path.")
    parser.add_argument(
        "--latest-json-output",
        type=Path,
        default=DEFAULT_LATEST_JSON_OUTPUT,
        help="Latest Radar catalyst inventory JSON path.",
    )
    parser.add_argument("--md-output", type=Path, default=None, help="Optional dated Markdown output path.")
    parser.add_argument(
        "--latest-md-output",
        type=Path,
        default=DEFAULT_LATEST_MD_OUTPUT,
        help="Latest Radar catalyst inventory Markdown path.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return payload


def load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return payload


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
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def format_timestamp_label(value: Any) -> str:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return str(value or "").strip()
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def default_dated_json_output(snapshot: dict[str, Any]) -> Path:
    as_of_date = str(snapshot.get("as_of_date") or "unknown").replace("-", "")
    return DEFAULT_OUTPUT_DIR / f"radar_catalyst_inventory_{as_of_date}.json"


def default_dated_md_output(snapshot: dict[str, Any]) -> Path:
    as_of_date = str(snapshot.get("as_of_date") or "unknown").replace("-", "")
    return DEFAULT_OUTPUT_DIR / f"radar_catalyst_inventory_{as_of_date}.md"


def derive_inventory_state(item: dict[str, Any]) -> str:
    triage_action = str(item.get("triage_action") or "")
    event_lane = str(item.get("event_driven_lane") or "")
    evidence_quality = str(item.get("evidence_quality") or "")
    radar_bucket = str(item.get("radar_bucket") or "")
    if triage_action == "risk_review" or event_lane == "risk_monitor":
        return STATE_RISK
    if triage_action == "immediate_research":
        return STATE_CONFIRMED
    if triage_action == "thesis_watch" or radar_bucket in {"research_candidate", "strong_candidate", "strong_alert"}:
        return STATE_WATCHING
    if evidence_quality in {"structured_confirmed", "proxy_confirmed", "early_thematic"}:
        return STATE_OPEN
    return STATE_BACKGROUND


def transition_note(
    *,
    previous_state: str | None,
    current_state: str,
    presence_status: str,
    current_name: str,
) -> str:
    if previous_state is None:
        return f"{current_name} 首次进入 Radar catalyst inventory。"
    if presence_status == PRESENCE_MISSING and current_state == STATE_EXPIRED:
        return f"{current_name} 已连续缺席多轮快照，当前从 inventory 转为 expired。"
    if presence_status == PRESENCE_MISSING:
        return f"{current_name} 本轮未出现在快照里，先保留为 missing_recent 观察。"
    if previous_state != current_state:
        return f"{current_name} 的 inventory 状态从 {previous_state} 迁移到 {current_state}。"
    return f"{current_name} 延续在 {current_state} 状态。"


def build_history_entry(
    *,
    snapshot: dict[str, Any],
    inventory_state: str,
    presence_status: str,
    item: dict[str, Any] | None,
) -> dict[str, Any]:
    item = item or {}
    return {
        "run_id": str(snapshot.get("run_id") or snapshot.get("radar_run_id") or ""),
        "as_of_date": str(snapshot.get("as_of_date") or ""),
        "inventory_state": inventory_state,
        "presence_status": presence_status,
        "radar_bucket": str(item.get("radar_bucket") or ""),
        "triage_action": str(item.get("triage_action") or ""),
        "radar_score": int(item.get("radar_score") or 0),
    }


def dedupe_state_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}
    for entry in history:
        if not isinstance(entry, dict):
            continue
        key = (str(entry.get("run_id") or ""), str(entry.get("presence_status") or ""))
        if key in positions:
            result[positions[key]] = entry
        else:
            positions[key] = len(result)
            result.append(entry)
    return result[-MAX_HISTORY:]


def derive_seen_count(history: list[dict[str, Any]]) -> int:
    return sum(1 for entry in history if str(entry.get("presence_status") or "") == PRESENCE_ACTIVE)


def derive_transition_count(history: list[dict[str, Any]]) -> int:
    count = 0
    previous_state = ""
    for entry in history:
        current_state = str(entry.get("inventory_state") or "")
        if previous_state and current_state and current_state != previous_state:
            count += 1
        if current_state:
            previous_state = current_state
    return count


def derive_previous_state_from_history(history: list[dict[str, Any]], current_run_id: str) -> str | None:
    cleaned = dedupe_state_history(history)
    if not cleaned:
        return None
    last_entry = cleaned[-1]
    if str(last_entry.get("run_id") or "") == current_run_id:
        if len(cleaned) >= 2:
            previous_state = str(cleaned[-2].get("inventory_state") or "").strip()
            return previous_state or None
        return None
    previous_state = str(last_entry.get("inventory_state") or "").strip()
    return previous_state or None


def build_entry(
    *,
    snapshot: dict[str, Any],
    item: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    current_state = derive_inventory_state(item)
    current_run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "")
    same_run_rebuild = bool(previous) and str(previous.get("last_seen_run_id") or "") == current_run_id
    previous_state = None
    current_name = str(item.get("radar_object_name") or "")
    current_history = dedupe_state_history(list(previous.get("state_history") or [])) if previous else []
    if previous:
        previous_state = derive_previous_state_from_history(current_history, current_run_id)
    next_history_entry = build_history_entry(
        snapshot=snapshot,
        inventory_state=current_state,
        presence_status=PRESENCE_ACTIVE,
        item=item,
    )
    if same_run_rebuild and current_history:
        current_history[-1] = next_history_entry
    else:
        current_history.append(next_history_entry)
    current_history = dedupe_state_history(current_history)
    first_seen_at = str(previous.get("first_seen_at") or "").strip() if previous else ""
    first_seen_date = str(previous.get("first_seen_sample_date") or "").strip() if previous else ""
    if not first_seen_at:
        first_seen_at = str(snapshot.get("generated_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if not first_seen_date:
        first_seen_date = str(snapshot.get("as_of_date") or "")
    seen_count = derive_seen_count(current_history)
    state_changed = previous_state is not None and previous_state != current_state and not same_run_rebuild
    return {
        "inventory_id": str(item.get("radar_object_id") or ""),
        "radar_object_id": str(item.get("radar_object_id") or ""),
        "radar_object_name": current_name,
        "radar_object_type": str(item.get("radar_object_type") or ""),
        "current_state": current_state,
        "previous_state": previous_state or "",
        "presence_status": PRESENCE_ACTIVE,
        "state_changed": state_changed,
        "first_seen_at": first_seen_at,
        "first_seen_sample_date": first_seen_date,
        "last_seen_at": str(snapshot.get("generated_at") or ""),
        "last_seen_sample_date": str(snapshot.get("as_of_date") or ""),
        "last_seen_run_id": current_run_id,
        "seen_count": seen_count,
        "missed_runs": 0,
        "state_transition_count": derive_transition_count(current_history),
        "transition_note": transition_note(
            previous_state=previous_state,
            current_state=current_state,
            presence_status=PRESENCE_ACTIVE,
            current_name=current_name,
        ),
        "radar_bucket": str(item.get("radar_bucket") or ""),
        "triage_action": str(item.get("triage_action") or ""),
        "event_driven_lane": str(item.get("event_driven_lane") or ""),
        "catalyst_type": str(item.get("catalyst_type") or ""),
        "hard_or_soft": str(item.get("hard_or_soft") or ""),
        "catalyst_stage": str(item.get("catalyst_stage") or ""),
        "evidence_quality": str(item.get("evidence_quality") or ""),
        "alert_level": str(item.get("alert_level") or ""),
        "radar_score": int(item.get("radar_score") or 0),
        "rank_overall": int(item.get("rank_overall") or 0),
        "next_milestone": str(item.get("next_milestone") or ""),
        "milestone_due_window": str(item.get("milestone_due_window") or ""),
        "confirmation_gap": str(item.get("confirmation_gap") or ""),
        "failure_mode": str(item.get("failure_mode") or ""),
        "primary_symbols": list(item.get("primary_symbols") or []),
        "etf_proxies": list(item.get("etf_proxies") or []),
        "theme_overlays": list(item.get("theme_overlays") or []),
        "research_links": list(item.get("research_links") or []),
        "state_history": current_history,
    }


def build_missing_entry(
    *,
    snapshot: dict[str, Any],
    previous: dict[str, Any],
) -> dict[str, Any]:
    previous_state = str(previous.get("current_state") or "").strip() or STATE_BACKGROUND
    missed_runs = int(previous.get("missed_runs") or 0) + 1
    expired = missed_runs >= EXPIRE_AFTER_MISSED_RUNS
    current_state = STATE_EXPIRED if expired else previous_state
    previous_history = dedupe_state_history(list(previous.get("state_history") or []))
    previous_history.append(
        build_history_entry(
            snapshot=snapshot,
            inventory_state=current_state,
            presence_status=PRESENCE_MISSING,
            item=None,
        )
    )
    previous_history = dedupe_state_history(previous_history)
    current_name = str(previous.get("radar_object_name") or "")
    state_changed = current_state != previous_state
    return {
        **previous,
        "current_state": current_state,
        "previous_state": previous_state,
        "presence_status": PRESENCE_MISSING,
        "state_changed": state_changed,
        "last_seen_run_id": str(previous.get("last_seen_run_id") or ""),
        "missed_runs": missed_runs,
        "seen_count": derive_seen_count(previous_history),
        "state_transition_count": derive_transition_count(previous_history),
        "transition_note": transition_note(
            previous_state=previous_state,
            current_state=current_state,
            presence_status=PRESENCE_MISSING,
            current_name=current_name,
        ),
        "state_history": previous_history,
    }


def summarize_inventory(snapshot: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    state_counts = {
        STATE_CONFIRMED: 0,
        STATE_WATCHING: 0,
        STATE_OPEN: 0,
        STATE_RISK: 0,
        STATE_BACKGROUND: 0,
        STATE_EXPIRED: 0,
    }
    presence_counts = {
        PRESENCE_ACTIVE: 0,
        PRESENCE_MISSING: 0,
    }
    new_items = 0
    state_changed = 0
    for entry in entries:
        state = str(entry.get("current_state") or "")
        presence = str(entry.get("presence_status") or "")
        if state in state_counts:
            state_counts[state] += 1
        if presence in presence_counts:
            presence_counts[presence] += 1
        if int(entry.get("seen_count") or 0) == 1:
            new_items += 1
        if bool(entry.get("state_changed")):
            state_changed += 1
    return {
        "candidate_count": int((snapshot.get("summary") or {}).get("candidate_count") or 0),
        "inventory_count": len(entries),
        "active_count": presence_counts[PRESENCE_ACTIVE],
        "missing_recent_count": presence_counts[PRESENCE_MISSING],
        "confirmed_count": state_counts[STATE_CONFIRMED],
        "watching_count": state_counts[STATE_WATCHING],
        "open_count": state_counts[STATE_OPEN],
        "risk_review_count": state_counts[STATE_RISK],
        "background_count": state_counts[STATE_BACKGROUND],
        "expired_count": state_counts[STATE_EXPIRED],
        "new_item_count": new_items,
        "state_changed_count": state_changed,
    }


def inventory_sort_key(entry: dict[str, Any]) -> tuple[int, int, int, int, str]:
    return (
        STATE_ORDER.get(str(entry.get("current_state") or ""), 99),
        0 if str(entry.get("presence_status") or "") == PRESENCE_ACTIVE else 1,
        -int(entry.get("radar_score") or 0),
        int(entry.get("rank_overall") or 9999),
        str(entry.get("radar_object_name") or ""),
    )


def build_inventory(snapshot: dict[str, Any], previous_inventory: dict[str, Any] | None) -> dict[str, Any]:
    previous_index = {
        str(item.get("radar_object_id") or ""): item
        for item in (previous_inventory.get("inventory") or [])
        if isinstance(item, dict) and str(item.get("radar_object_id") or "").strip()
    } if previous_inventory else {}
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in snapshot.get("objects") or []:
        if not isinstance(item, dict):
            continue
        object_id = str(item.get("radar_object_id") or "").strip()
        if not object_id:
            continue
        entries.append(build_entry(snapshot=snapshot, item=item, previous=previous_index.get(object_id)))
        seen_ids.add(object_id)
    for object_id, previous in previous_index.items():
        if object_id in seen_ids:
            continue
        entries.append(build_missing_entry(snapshot=snapshot, previous=previous))
    entries.sort(key=inventory_sort_key)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": str(snapshot.get("run_id") or snapshot.get("radar_run_id") or ""),
        "as_of_date": str(snapshot.get("as_of_date") or ""),
        "market_tz": str(snapshot.get("market_tz") or "Asia/Shanghai"),
        "inventory_version": "v1",
        "summary": summarize_inventory(snapshot, entries),
        "inventory": entries,
    }


def board_lines(title: str, entries: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    if not entries:
        lines.extend(["- 无", ""])
        return lines
    for entry in entries:
        lines.append(
            "- {name} | {state} | {bucket} | seen {seen} runs | 里程碑：{milestone}".format(
                name=str(entry.get("radar_object_name") or ""),
                state=str(entry.get("current_state") or ""),
                bucket=str(entry.get("radar_bucket") or ""),
                seen=int(entry.get("seen_count") or 0),
                milestone=str(entry.get("next_milestone") or "") or "继续观察",
            )
        )
    lines.append("")
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    entries = [item for item in (payload.get("inventory") or []) if isinstance(item, dict)]
    confirmed = [item for item in entries if item.get("current_state") == STATE_CONFIRMED and item.get("presence_status") == PRESENCE_ACTIVE][:8]
    watching = [item for item in entries if item.get("current_state") == STATE_WATCHING and item.get("presence_status") == PRESENCE_ACTIVE][:10]
    risks = [item for item in entries if item.get("current_state") == STATE_RISK and item.get("presence_status") == PRESENCE_ACTIVE][:8]
    expired = [item for item in entries if item.get("current_state") == STATE_EXPIRED][:8]
    changed = [item for item in entries if bool(item.get("state_changed"))][:10]
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_catalyst_inventory"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Catalyst Inventory {payload.get("as_of_date") or "unknown"}"',
        "---",
        "",
        "# Radar Catalyst Inventory",
        "",
        f"- 样本日期：`{payload.get('as_of_date')}`",
        f"- 运行批次：`{payload.get('run_id')}`",
        f"- 生成时间：`{format_timestamp_label(payload.get('generated_at'))}`",
        f"- 当前 inventory：`active {summary.get('active_count', 0)} / missing_recent {summary.get('missing_recent_count', 0)} / confirmed {summary.get('confirmed_count', 0)} / watching {summary.get('watching_count', 0)} / open {summary.get('open_count', 0)} / risk_review {summary.get('risk_review_count', 0)} / expired {summary.get('expired_count', 0)}`",
        f"- 本轮新增：`{summary.get('new_item_count', 0)}`，状态迁移：`{summary.get('state_changed_count', 0)}`",
        "",
    ]
    lines.extend(board_lines("Confirmed", confirmed))
    lines.extend(board_lines("Watching", watching))
    lines.extend(board_lines("Risk Review", risks))
    lines.extend(board_lines("Expired", expired))
    lines.extend(["## 状态迁移", ""])
    if changed:
        for entry in changed:
            lines.append(f"- {entry.get('transition_note')}")
    else:
        lines.append("- 本轮没有新的状态迁移。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    snapshot = load_json(args.input_snapshot)
    previous_inventory = load_optional_json(args.previous_inventory)
    inventory = build_inventory(snapshot, previous_inventory)
    dated_json_output = args.json_output or default_dated_json_output(snapshot)
    dated_md_output = args.md_output or default_dated_md_output(snapshot)
    markdown = render_markdown(inventory)
    write_json(args.latest_json_output, inventory)
    write_json(dated_json_output, inventory)
    write_text(args.latest_md_output, markdown)
    write_text(dated_md_output, markdown)
    print(
        json.dumps(
            {
                "latest_json_output": str(args.latest_json_output),
                "latest_md_output": str(args.latest_md_output),
                "inventory_count": int((inventory.get("summary") or {}).get("inventory_count") or 0),
                "confirmed_count": int((inventory.get("summary") or {}).get("confirmed_count") or 0),
                "state_changed_count": int((inventory.get("summary") or {}).get("state_changed_count") or 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
