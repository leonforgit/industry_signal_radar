#!/usr/bin/env python3
"""Build a compact A/H IPO watchlist for the Radar daily report."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from radar_freshness_utils import DEFAULT_MARKET_TZ, market_now


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_ipo_watchlist_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_ipo_watchlist_latest.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default="", help="Target date in YYYY-MM-DD. Defaults to current Asia/Shanghai date.")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--include-upcoming-days", type=int, default=3)
    return parser.parse_args()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def target_date_text(raw: str) -> str:
    text = str(raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return market_now(DEFAULT_MARKET_TZ).date().isoformat()


def parse_calendar_date(value: Any, *, target_year: int) -> str:
    text = str(value or "").strip()
    if not text or text in {"-", "None", "NaT", "nan"}:
        return ""
    text = text.replace("/", "-")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"(\d{1,2})-(\d{1,2})", text)
    if match:
        month, day = match.groups()
        return f"{target_year:04d}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if match:
        month, day = match.groups()
        return f"{target_year:04d}-{int(month):02d}-{int(day):02d}"
    return ""


def safe_float(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def format_value(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"None", "NaT", "nan"} else text


def first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if str(value or "").strip() and str(value).strip() not in {"None", "NaT", "nan"}:
            return value
    return ""


def call_akshare(name: str) -> tuple[pd.DataFrame, str]:
    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), f"akshare_import_error={type(exc).__name__}: {exc}"
    try:
        frame = getattr(ak, name)()
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), f"{name}_error={type(exc).__name__}: {exc}"
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame(), f"{name}_returned_non_dataframe"
    return frame, ""


def classify_market(source: str, row: dict[str, Any]) -> str:
    code = str(first_value(row, "股票代码", "证劵代码", "证券代码", "stock_code")).strip()
    exchange = str(row.get("交易所") or "").strip()
    if source == "stock_ipo_hk_ths" and (code.startswith(("0", "8", "9")) and len(code) == 5):
        return "HK"
    if code.startswith(("920", "8")) and len(code) == 6:
        return "A-BJ"
    if "北京" in exchange or str(row.get("板块") or "") == "北交所":
        return "A-BJ"
    if "上海" in exchange:
        return "A-SH"
    if "深圳" in exchange:
        return "A-SZ"
    return "A/H"


def ipo_dates(row: dict[str, Any], *, target_year: int) -> dict[str, str]:
    return {
        "subscription_date": parse_calendar_date(row.get("申购日期"), target_year=target_year),
        "lottery_date": parse_calendar_date(first_value(row, "中签号公布日", "摇号结果公告日", "中签公告日", "中签缴款日期"), target_year=target_year),
        "payment_date": parse_calendar_date(first_value(row, "中签缴款日期", "中签缴款日"), target_year=target_year),
        "listing_date": parse_calendar_date(row.get("上市日期"), target_year=target_year),
    }


def event_tags(dates: dict[str, str], *, target_date: str) -> list[str]:
    tags: list[str] = []
    if dates.get("subscription_date") == target_date:
        tags.append("今日申购")
    if dates.get("listing_date") == target_date:
        tags.append("今日上市")
    if dates.get("payment_date") == target_date:
        tags.append("今日缴款")
    if dates.get("lottery_date") == target_date and "今日缴款" not in tags:
        tags.append("今日中签")
    return tags


def score_ipo(row: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    issue_pe = safe_float(row.get("发行市盈率"))
    industry_pe = safe_float(row.get("行业市盈率"))
    issue_price = safe_float(first_value(row, "发行价格", "发行价"))
    win_rate = safe_float(row.get("中签率") or row.get("中签率（%）"))
    if issue_pe is not None and industry_pe is not None and industry_pe > 0:
        discount = issue_pe / industry_pe
        if discount <= 0.75:
            reasons.append(f"发行PE低于行业PE约{(1 - discount) * 100:.0f}%")
        elif discount >= 1.15:
            reasons.append(f"发行PE高于行业PE约{(discount - 1) * 100:.0f}%")
    if issue_price is not None:
        reasons.append(f"发行价{issue_price:g}")
    if win_rate is not None:
        reasons.append(f"中签率{win_rate:g}%")
    joined = "；".join(reasons)
    if "低于行业PE" in joined and "发行价" in joined:
        return "优先申购初筛", reasons[:3]
    if "高于行业PE" in joined:
        return "谨慎申购", reasons[:3]
    return "资料待补", reasons[:3] or ["IPO 明细源暂未返回估值/发行价字段，下一次生产运行继续自动补齐后再判断是否申购"]


def normalize_rows(frame: pd.DataFrame, *, source: str, target_date: str) -> list[dict[str, Any]]:
    target_year = int(target_date[:4])
    rows: list[dict[str, Any]] = []
    for raw in frame.fillna("").to_dict(orient="records"):
        if not isinstance(raw, dict):
            continue
        dates = ipo_dates(raw, target_year=target_year)
        tags = event_tags(dates, target_date=target_date)
        if not tags:
            continue
        potential, reasons = score_ipo(raw)
        has_terms = bool(format_value(first_value(raw, "发行价格", "发行价")) or format_value(raw.get("发行市盈率")))
        if "今日上市" in tags and "今日申购" not in tags:
            potential = "已过申购窗口"
            action = "不作为今日申购机会；仅用于复盘打新模型，不写成上市后二级买入动作"
        elif "今日申购" in tags:
            action = (
                "今天给出是否申购结论：核发行价、发行 PE/行业 PE、募资用途、网上发行规模和中签率预期"
                if has_terms
                else "系统自动动作：先补发行价、发行 PE、行业 PE 和招股书字段；补齐前不进入申购建议"
            )
        else:
            action = (
                "在申购日前完成是否申购结论；开放申购后只更新定价、中签率和申购上限"
                if has_terms
                else "系统自动动作：下一次生产运行继续补发行价、发行 PE、中签率和招股书字段；PM 只看补齐后的申购判断"
            )
        rows.append(
            {
                "name": format_value(first_value(raw, "股票简称", "证券简称")),
                "code": format_value(first_value(raw, "股票代码", "证劵代码", "证券代码")),
                "market": classify_market(source, raw),
                "source": source,
                "event_tags": tags,
                "potential": potential,
                "initial_research_points": reasons,
                "subscription_date": dates.get("subscription_date", ""),
                "listing_date": dates.get("listing_date", ""),
                "payment_date": dates.get("payment_date", ""),
                "issue_price": format_value(first_value(raw, "发行价格", "发行价")),
                "issue_pe": format_value(raw.get("发行市盈率")),
                "industry_pe": format_value(raw.get("行业市盈率")),
                "online_issue": format_value(first_value(raw, "网上发行", "网上发行（万股）", "上网发行数量")),
                "subscription_limit": format_value(first_value(raw, "申购上限", "申购上限（万股）", "网上申购上限")),
                "execution_action": action,
            }
        )
    return rows


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_ipo_watchlist"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar IPO Watchlist {payload.get("target_date") or "unknown"}"',
        "---",
        "",
        "# Radar IPO Watchlist",
        "",
        f"- 目标日期：`{payload.get('target_date')}`",
        f"- 状态：`{payload.get('status')}`",
        f"- 今日 IPO 事件数：`{len(payload.get('items') or [])}`",
        "",
    ]
    notes = payload.get("source_notes") or []
    if notes:
        lines.append("- source notes：" + " / ".join(str(item) for item in notes[:4]))
        lines.append("")
    items = payload.get("items") or []
    if not items:
        lines.append("- 今日未抓到 A/H 新股申购、上市或缴款事件。")
        return "\n".join(lines) + "\n"
    lines.extend(["| 名称 | 市场 | 今日事件 | 初筛 | 关键点 |", "| --- | --- | --- | --- | --- |"])
    for item in items:
        points = "；".join(str(x) for x in (item.get("initial_research_points") or []))
        lines.append(
            "| {name} {code} | {market} | {tags} | {potential} | {points} |".format(
                name=str(item.get("name") or "").replace("|", "/"),
                code=str(item.get("code") or ""),
                market=str(item.get("market") or ""),
                tags="、".join(str(x) for x in (item.get("event_tags") or [])),
                potential=str(item.get("potential") or ""),
                points=points.replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    target_date = target_date_text(args.target_date)
    source_notes: list[str] = []
    items: list[dict[str, Any]] = []
    for source in ("stock_xgsglb_em", "stock_new_ipo_cninfo", "stock_ipo_hk_ths"):
        frame, note = call_akshare(source)
        if note:
            source_notes.append(note)
            continue
        items.extend(normalize_rows(frame, source=source, target_date=target_date))
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        code = str(item.get("code") or "")
        tags = ",".join(item.get("event_tags") or [])
        key = (code, tags) if code else ("", str(item.get("name") or "").lstrip("N"), tags)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    status = "pass" if not source_notes or deduped else ("warn" if source_notes else "pass")
    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "target_date": target_date,
        "status": status,
        "source_notes": source_notes,
        "items": deduped,
    }
    write_json(args.output_json, payload)
    write_text(args.output_md, render_markdown(payload))
    print(json.dumps({"status": status, "target_date": target_date, "item_count": len(deduped)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
