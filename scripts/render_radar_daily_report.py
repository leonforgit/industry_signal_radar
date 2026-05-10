#!/usr/bin/env python3
"""Render the Radar daily report from a snapshot JSON."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

import _report_toolkit  # noqa: F401
from report_toolkit import html_to_pdf, markdown_to_html

from radar_freshness_utils import summarize_snapshot_freshness
from radar_sentiment_sidecar import compact_market_sentiment_text


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_INPUT_INVENTORY = ROOT / "output" / "inventory" / "radar_catalyst_inventory_latest.json"
DEFAULT_INPUT_HANDOFF = ROOT / "output" / "handoffs" / "radar_research_handoff_latest.json"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "reports"
DEFAULT_LATEST_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_daily_report_latest.md"
DEFAULT_LATEST_HTML_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_daily_report_latest.html"
DEFAULT_LATEST_PDF_OUTPUT = DEFAULT_OUTPUT_DIR / "radar_daily_report_latest.pdf"
PDF_RENDER_TIMEOUT_SECONDS = max(int(os.environ.get("RADAR_PDF_RENDER_TIMEOUT_SECONDS", "120")), 20)
DEFAULT_SOURCE_READINESS_JSON = DEFAULT_OUTPUT_DIR / "radar_source_readiness_latest.json"
DEFAULT_KIMI_EDITORIAL_JSON = DEFAULT_OUTPUT_DIR / "radar_kimi_editorial_latest.json"
DEFAULT_KIMI_RESEARCH_JSON = DEFAULT_OUTPUT_DIR / "radar_kimi_research_harness_latest.json"
DEFAULT_PRICE_FRESHNESS_JSON = DEFAULT_OUTPUT_DIR / "radar_price_freshness_latest.json"
DEFAULT_NEWS_VERIFICATION_JSON = DEFAULT_OUTPUT_DIR / "radar_news_verification_latest.json"
DEFAULT_IPO_WATCHLIST_JSON = DEFAULT_OUTPUT_DIR / "radar_ipo_watchlist_latest.json"
DEFAULT_HK_IPO_WATCHLIST_JSON = DEFAULT_OUTPUT_DIR / "radar_hk_ipo_watchlist_latest.json"
DEFAULT_STRUCTURAL_SIGNAL_JSON = DEFAULT_OUTPUT_DIR / "radar_structural_signal_latest.json"
DEFAULT_RULES_PATH = ROOT / "config" / "radar_report_rules_v1.json"
BUCKETS = ["strong_alert", "strong_candidate", "research_candidate", "observe"]
TRIAGE_TITLES = [
    ("immediate_research", "Immediate Research Queue"),
    ("thesis_watch", "Thesis Watch"),
    ("risk_review", "Risk Review"),
    ("background_only", "Background Monitor"),
]
TRIAGE_PRIORITY = {
    "immediate_research": 0,
    "thesis_watch": 1,
    "risk_review": 2,
    "background_only": 3,
}
OBJECT_TYPE_LABELS = {
    "company": "公司",
    "industry": "行业",
    "macro": "宏观/代理",
    "commodity": "商品",
    "special_situation": "特殊事件",
    "watchlist_priority_change": "观察池变化",
}
BUCKET_LABELS = {
    "strong_alert": "强提醒",
    "strong_candidate": "强候选",
    "research_candidate": "研究候选",
    "observe": "观察池",
}
RUNTIME_STATE_LABELS = {
    "cold": "冷启动",
    "warming": "升温中",
    "candidate": "候选",
    "strong_alert": "强提醒",
}
TRIAGE_LABELS = {
    "immediate_research": "立即研究",
    "thesis_watch": "继续跟踪",
    "risk_review": "风险复核",
    "background_only": "背景观察",
}
EVIDENCE_PRIORITY = {
    "structured_confirmed": 0,
    "proxy_confirmed": 1,
    "early_thematic": 2,
    "risk_signal": 3,
    "narrative_only": 4,
}
EVIDENCE_LABELS = {
    "structured_confirmed": "结构化确认",
    "proxy_confirmed": "代理变量确认",
    "early_thematic": "早期主题",
    "risk_signal": "风险信号",
    "narrative_only": "叙事主导",
}
HARD_SOFT_LABELS = {
    "hard": "硬催化",
    "soft": "软催化",
}
CATALYST_TYPE_LABELS = {
    "earnings_guidance": "业绩改善",
    "order_project": "订单/项目",
    "approval_registration": "审批/注册",
    "industry_proxy": "代理变量",
    "policy_regulation": "政策/监管",
    "capital_markets": "资本运作",
    "buyback_shareholder_support": "股东支持/增持",
    "financing_dilution": "融资/摊薄",
    "litigation_regulatory": "诉讼/监管",
    "merger_restructuring": "并购/重组",
    "dividend_capital_return": "分红/资本回报",
    "general_corporate": "一般公司事件",
}
CATALYST_STAGE_LABELS = {
    "announced": "已公告",
    "execution_window": "执行窗口",
    "signal_clustered": "信号聚集",
    "monitoring": "持续跟踪",
    "pending": "待确认",
}
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
INDUSTRY_EVENT_TYPES = {
    "approval_registration",
    "commodity_disruption",
    "deal_mna",
    "industry_proxy",
    "order_project",
    "policy_regulation",
    "production_supply",
}
COMMODITY_SUPPLY_KEYWORDS = ("霍尔木兹", "OPEC", "LNG", "液化天然气", "原油", "炼油", "供应", "出口", "航运")
COMMODITY_PRICE_DOWN_KEYWORDS = ("跌破", "下跌", "回落", "走低", "承压", "转跌")
OIL_SUPPLY_INCREASE_KEYWORDS = ("产量配额提高", "提高石油产量", "增产")
PRICED_IN_DAILY_RETURN = 0.04
PARTIAL_PRICED_IN_DAILY_RETURN = 0.02
PRICED_IN_AMOUNT_RATIO = 2.0
PRICED_IN_POSITIVE_DAYS = 0.8
HK_IPO_NAME_CN = {
    "shanghaisunmitechnologycoltd": "上海商米科技集团股份有限公司",
    "starsportsmedicinecoltd": "北京天星医疗股份有限公司",
    "cofoemedicaltechnologycoltd": "可孚医疗科技股份有限公司",
    "shenzhenldrobotcoltd": "深圳乐动机器人股份有限公司",
    "impacttherapeuticsinc": "南京英派药业股份有限公司",
    "metistechbiocoltd": "剂泰科技(北京)股份有限公司",
    "robotphoenixintelligenttechnologycoltd": "浙江翼菲智能科技股份有限公司",
}
GENERIC_EVENT_SUBJECTS = {
    "中泰证券",
    "国金证券",
    "银河证券",
    "中信证券",
    "央视快评",
    "乘联分会崔东树",
    "郑丽文一行",
    "美国副总统",
    "特朗普政府",
    "中国人民银行",
    "中越联合声明",
}
NEGATIVE_EVENT_KEYWORDS = ("下降", "下修", "亏损", "终止上市", "摘牌", "ST", "停牌", "风险警示", "退")
NEGATIVE_COMPANY_EVENT_KEYWORDS = (
    "减持",
    "拟减持",
    "终止收购",
    "终止购买",
    "终止筹划",
    "终止重大资产重组",
    "补缴税款",
    "滞纳金",
    "尚存不确定性",
    "无开展",
    "无相关业务计划",
)
DISPLAY_RELEVANT_COMPANY_TOKENS = (
    "净利润",
    "增长",
    "预增",
    "订单",
    "中标",
    "回购",
    "增持",
    "减持",
    "停牌",
    "重组",
    "收购",
    "合作",
    "获批",
)
LOW_SIGNAL_COMPANY_EVENT_KEYWORDS = (
    "经营情况正常",
    "不存在未披露重大事项",
    "正常履职",
    "续聘会计师事务所",
    "会计师事务所",
    "独立董事提名人声明",
    "年度报告摘要",
)
MATERIAL_RISK_COMPANY_EVENT_KEYWORDS = (
    "监管函",
    "问询函",
    "立案调查",
    "行政处罚",
    "处罚决定",
    "纪律处分",
)
POSITIVE_EVENT_KEYWORDS = ("增长", "预增", "获批", "批准", "订单", "中标", "合作", "扩散", "上车", "充足", "回购", "增持", "发布")
GENERIC_EVENT_KEYWORDS = ("证券：", "市场重心", "风险资产", "关注A股", "市场或缩圈", "快评", "新闻发布会")
NON_INDUSTRY_EVENT_KEYWORDS = (
    "副总统",
    "特朗普",
    "佩斯科夫",
    "郑丽文",
    "伊斯兰堡",
    "结束大陆参访",
    "停火",
    "沙特能源部",
    "二手房",
    "以军空袭",
    "黎巴嫩",
    "地方政府债券",
    "航空燃油",
)
REPORT_CSS = """
:root {
  color-scheme: light;
  --navy: #16324f;
  --ink: #1f2937;
  --muted: #6b7280;
  --line: #d7dee8;
  --soft: #f5f8fc;
}

body {
  font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  color: var(--ink);
  margin: 28px 34px;
  line-height: 1.58;
}

h1, h2, h3 {
  color: var(--navy);
  margin-top: 1.05em;
}

h4, h5 {
  color: var(--navy);
  margin: 0.9em 0 0.35em;
}

h1 {
  margin-top: 0;
  padding-bottom: 10px;
  border-bottom: 2px solid var(--line);
}

code {
  font-family: "SFMono-Regular", "Menlo", "Monaco", monospace;
  font-size: 0.92em;
}

blockquote {
  margin: 1em 0;
  padding: 0.75em 1em;
  border-left: 4px solid #9bb9d5;
  background: var(--soft);
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 1em 0;
}

th, td {
  border: 1px solid var(--line);
  padding: 8px 10px;
  vertical-align: top;
  font-size: 0.92em;
}

th {
  background: var(--soft);
  color: #243b53;
  font-weight: 650;
}

ul {
  padding-left: 1.25em;
}

li {
  margin: 0.25em 0;
}
"""


def label_from(mapping: dict[str, str], value: Any, fallback: str = "未标注") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return mapping.get(text, text)


def object_type_label(value: Any) -> str:
    return label_from(OBJECT_TYPE_LABELS, value, fallback="未分类")


def bucket_label(value: Any) -> str:
    return label_from(BUCKET_LABELS, value, fallback="未分桶")


def runtime_state_label(value: Any) -> str:
    return label_from(RUNTIME_STATE_LABELS, value, fallback="未标注")


def triage_label(value: Any) -> str:
    return label_from(TRIAGE_LABELS, value, fallback="未分流")


def evidence_label(value: Any) -> str:
    return label_from(EVIDENCE_LABELS, value, fallback="未标注")


def hard_soft_label(value: Any) -> str:
    return label_from(HARD_SOFT_LABELS, value, fallback="未标注")


def catalyst_type_label(value: Any) -> str:
    return label_from(CATALYST_TYPE_LABELS, value, fallback="未归类催化")


def catalyst_stage_label(value: Any) -> str:
    return label_from(CATALYST_STAGE_LABELS, value, fallback="待确认阶段")


def catalyst_summary(item: dict[str, Any]) -> str:
    return " / ".join(
        [
            catalyst_type_label(item.get("catalyst_type")),
            hard_soft_label(item.get("hard_or_soft")),
            catalyst_stage_label(item.get("catalyst_stage")),
        ]
    )


def triage_reason_text(item: dict[str, Any]) -> str:
    existing = str(item.get("triage_reason") or "").strip()
    if existing:
        return existing
    action = str(item.get("triage_action") or "")
    evidence = evidence_label(item.get("evidence_quality"))
    bucket = bucket_label(item.get("radar_bucket"))
    if action == "immediate_research":
        return f"当前已进入 {bucket}，且证据达到 {evidence}，适合立刻展开研究。"
    if action == "thesis_watch":
        return f"当前更像早期线索，仍需继续补齐确认缺口，所以先留在跟踪层。"
    if action == "risk_review":
        return "当前更像需要防守或复核的信号，不适合正向推进。"
    return "当前更多承担背景观察作用，不进入主研究队列。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT, help="Radar snapshot JSON path.")
    parser.add_argument("--input-inventory", type=Path, default=DEFAULT_INPUT_INVENTORY, help="Radar catalyst inventory JSON path.")
    parser.add_argument("--input-handoff", type=Path, default=DEFAULT_INPUT_HANDOFF, help="Radar research handoff JSON path.")
    parser.add_argument("--input-kimi-editorial", type=Path, default=DEFAULT_KIMI_EDITORIAL_JSON, help="Optional Kimi editorial JSON path.")
    parser.add_argument("--input-kimi-research", type=Path, default=DEFAULT_KIMI_RESEARCH_JSON, help="Optional Kimi research harness JSON path.")
    parser.add_argument("--input-price-freshness", type=Path, default=DEFAULT_PRICE_FRESHNESS_JSON, help="Optional price freshness JSON path.")
    parser.add_argument("--input-news-verification", type=Path, default=DEFAULT_NEWS_VERIFICATION_JSON, help="Optional news verification JSON path.")
    parser.add_argument("--input-ipo-watchlist", type=Path, default=DEFAULT_IPO_WATCHLIST_JSON, help="Optional IPO watchlist JSON path.")
    parser.add_argument("--input-hk-ipo-watchlist", type=Path, default=DEFAULT_HK_IPO_WATCHLIST_JSON, help="Optional HK IPO watchlist JSON path.")
    parser.add_argument("--input-structural-signal", type=Path, default=DEFAULT_STRUCTURAL_SIGNAL_JSON, help="Optional structural signal JSON path.")
    parser.add_argument("--output", type=Path, default=None, help="Optional dated report output path.")
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT, help="Latest report output path.")
    parser.add_argument("--html-output", type=Path, default=None, help="Optional dated report HTML output path.")
    parser.add_argument("--latest-html-output", type=Path, default=DEFAULT_LATEST_HTML_OUTPUT, help="Latest report HTML output path.")
    parser.add_argument("--pdf-output", type=Path, default=None, help="Optional dated report PDF output path.")
    parser.add_argument("--latest-pdf-output", type=Path, default=DEFAULT_LATEST_PDF_OUTPUT, help="Latest report PDF output path.")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH, help="Report rules config path.")
    return parser.parse_args()


def load_snapshot(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return data


def load_rules(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return data


def load_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_kimi_editorial(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def strip_markdown_front_matter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    marker = "\n---\n"
    end = text.find(marker, 4)
    if end == -1:
        return text
    return text[end + len(marker) :]


def render_html_pdf(markdown: str, title: str, html_path: Path, pdf_path: Path) -> tuple[bool, str | None]:
    tmp_pdf_path = pdf_path.with_suffix(pdf_path.suffix + ".tmp")
    try:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_pdf_path.unlink(missing_ok=True)
        markdown_to_html(strip_markdown_front_matter(markdown), REPORT_CSS, html_path, title)
        html_to_pdf(html_path, tmp_pdf_path, timeout=PDF_RENDER_TIMEOUT_SECONDS)
        pdf_bytes = tmp_pdf_path.read_bytes()
        if not pdf_bytes.startswith(b"%PDF") or b"%%EOF" not in pdf_bytes[-2048:]:
            raise RuntimeError("rendered PDF failed basic PDF integrity check")
        tmp_pdf_path.replace(pdf_path)
        return True, None
    except Exception as exc:  # noqa: BLE001
        tmp_pdf_path.unlink(missing_ok=True)
        pdf_path.unlink(missing_ok=True)
        return False, str(exc)


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


def inventory_summary_line(inventory: dict[str, Any]) -> str | None:
    summary = inventory.get("summary")
    if not isinstance(summary, dict):
        return None
    return (
        "当前 Catalyst Inventory 为 "
        "`confirmed {confirmed} / watching {watching} / open {open_count} / risk_review {risk_count} / expired {expired}`。".format(
            confirmed=int(summary.get("confirmed_count") or 0),
            watching=int(summary.get("watching_count") or 0),
            open_count=int(summary.get("open_count") or 0),
            risk_count=int(summary.get("risk_review_count") or 0),
            expired=int(summary.get("expired_count") or 0),
        )
    )


def kimi_editorial_lines(editorial: dict[str, Any]) -> list[str]:
    if not isinstance(editorial, dict) or str(editorial.get("status") or "") != "pass":
        return []
    lines = ["## 零、PM 编辑摘要", ""]
    headline = str(editorial.get("headline") or "").strip()
    if headline:
        lines.extend([f"> {headline}", ""])
    summary = [str(item).strip() for item in (editorial.get("pm_summary") or []) if str(item).strip()]
    if summary:
        lines.append("### 今日判断")
        lines.append("")
        lines.extend(f"- {item}" for item in summary)
        lines.append("")
    focus_actions = [item for item in (editorial.get("focus_actions") or []) if isinstance(item, dict)]
    if focus_actions:
        lines.extend(["### 只补这几件事", ""])
        for item in focus_actions:
            lines.append(
                "- {name}：{action}。原因：{why}。风险：{risk}".format(
                    name=str(item.get("name") or "").replace("|", "/"),
                    why=str(item.get("why") or "").replace("|", "/"),
                    action=str(item.get("action") or "").replace("|", "/"),
                    risk=str(item.get("risk") or "").replace("|", "/"),
                )
            )
        lines.append("")
    risk_notes = [str(item).strip() for item in (editorial.get("risk_notes") or []) if str(item).strip()]
    if risk_notes:
        lines.extend(["### 风险提醒", ""])
        lines.extend(f"- {item}" for item in risk_notes)
        lines.append("")
    return lines


def bucket_objects(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    items = snapshot.get("objects") or []
    grouped = {bucket: [] for bucket in BUCKETS}
    for item in items:
        if not isinstance(item, dict):
            continue
        bucket = str(item.get("radar_bucket") or "")
        if bucket in grouped:
            grouped[bucket].append(item)
    for bucket in grouped:
        grouped[bucket].sort(key=lambda item: (int(item.get("rank_in_bucket") or 9999), -int(item.get("radar_score") or 0)))
    return grouped


def format_next_step(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    text = str(value or "").strip()
    return text or "继续观察下一轮变化"


def object_type_counts(snapshot: dict[str, Any]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for item in snapshot.get("objects") or []:
        if not isinstance(item, dict):
            continue
        counter[str(item.get("radar_object_type") or "unknown")] += 1
    return counter


def triage_queue_counts(snapshot: dict[str, Any]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for item in snapshot.get("objects") or []:
        if not isinstance(item, dict):
            continue
        counter[str(item.get("triage_action") or "unknown")] += 1
    return counter


def optional_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def pct_text(value: Any) -> str:
    numeric = optional_float(value)
    if numeric is None:
        return "N/A"
    return f"{numeric * 100:+.1f}%"


def ratio_x_text(value: Any) -> str:
    numeric = optional_float(value)
    if numeric is None or numeric <= 0:
        return "N/A"
    return f"{numeric:.2f}x"


def compact_metric_tail(values: list[str], limit: int = 2) -> str:
    cleaned = [clean_display_text(value) for value in values if clean_display_text(value)]
    return " / ".join(cleaned[:limit])


def supporting_events(item: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    events = item.get("supporting_events") or []
    result: list[dict[str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        title = str(event.get("title") or "").strip()
        headline = str(event.get("headline") or "").strip()
        summary = str(event.get("summary") or "").strip()
        source = str(event.get("source") or "").strip()
        if not any((title, headline, summary, event_type)):
            continue
        result.append(
            {
                "event_type": event_type,
                "title": title,
                "headline": headline,
                "summary": summary,
                "source": source or "unknown",
            }
        )
        if len(result) >= limit:
            break
    return result


def clean_display_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"\[[^\]]*\]\s*", "", cleaned)
    cleaned = re.sub(r"【[^】]*】\s*", "", cleaned)
    cleaned = re.sub(r"（[^）]*）", "", cleaned)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    replacements = {
        "supporting_events": "事件库",
        "news_verification_samples": "新闻补核",
        "company_enrichment_samples": "公司补核",
        "earnings_guidance": "业绩指引",
        "company_action": "公司动作",
        "regulation": "监管事件",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def canonical_name_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def ipo_display_name(item: dict[str, Any]) -> str:
    for key in ("short_name_cn", "display_name_cn", "name_cn", "name"):
        value = clean_display_text(str(item.get(key) or ""))
        if value and contains_cjk(value):
            return value
    name = clean_display_text(str(item.get("name") or ""))
    mapped = HK_IPO_NAME_CN.get(canonical_name_key(name), "")
    return mapped or name


def ipo_code_text(item: dict[str, Any]) -> str:
    code = clean_display_text(str(item.get("code") or ""))
    if code:
        return code.zfill(5) if code.isdigit() and len(code) <= 5 else code
    fields: list[str] = [str(item.get("name") or "")]
    for key in ("verified_facts", "initial_research_points", "event_tags"):
        value = item.get(key)
        if isinstance(value, list):
            fields.extend(str(x or "") for x in value)
        elif value:
            fields.append(str(value))
    text = "；".join(fields)
    match = re.search(r"代码\s*[:：]?\s*(\d{4,5})", text)
    if match:
        return match.group(1).zfill(5)
    match = re.search(r"\b0?(\d{4,5})\b", text)
    if match and str(item.get("market") or "").upper().startswith("H"):
        return match.group(1).zfill(5)
    return ""


def primary_symbol_subject(item: dict[str, Any]) -> str:
    primary = item.get("primary_symbols") or []
    if isinstance(primary, list) and primary:
        text = clean_display_text(str(primary[0]))
        return text.split(" ")[0].strip()
    return ""


def primary_symbol_text(item: dict[str, Any]) -> str:
    primary = item.get("primary_symbols") or []
    if isinstance(primary, list) and primary:
        return clean_display_text(str(primary[0]))
    return clean_display_text(str(item.get("radar_object_name") or ""))


def tracking_symbols_text(item: dict[str, Any]) -> str:
    primary = [
        clean_display_text(str(x or ""))
        for x in (item.get("primary_symbols") or [])
        if clean_display_text(str(x or ""))
    ]
    etfs = [
        clean_display_text(str(x or ""))
        for x in (item.get("etf_proxies") or [])
        if clean_display_text(str(x or ""))
    ]
    parts: list[str] = []
    if primary:
        parts.append("代表股：" + "、".join(primary[:3]))
    if etfs:
        parts.append("ETF：" + "、".join(etfs[:2]))
    return "；".join(parts)


def extract_event_subject(text: str) -> str:
    cleaned = clean_display_text(text)
    if "：" in cleaned:
        subject = cleaned.split("：", 1)[0].strip()
    elif ":" in cleaned:
        subject = cleaned.split(":", 1)[0].strip()
    else:
        subject = ""
    if not subject or len(subject) > 18:
        return ""
    return subject


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def event_display_text(event: dict[str, Any]) -> str:
    for key in ("title", "headline", "summary", "event_type"):
        text = clean_display_text(event.get(key) or "")
        if text:
            return text
    return ""


def item_event_text(item: dict[str, Any]) -> str:
    texts: list[str] = []
    for event in item.get("supporting_events") or []:
        if isinstance(event, dict):
            text = event_display_text(event)
            if text:
                texts.append(text)
            event_type = clean_display_text(str(event.get("event_type") or ""))
            if event_type:
                texts.append(event_type)
    texts.extend(clean_display_text(str(x or "")) for x in (item.get("key_evidence") or []))
    texts.append(clean_display_text(str(item.get("catalyst_type") or "")))
    texts.append(clean_display_text(str(item.get("why_now") or "")))
    return "；".join(text for text in texts if text)


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


def latest_event_date(item: dict[str, Any]) -> str:
    dates = [
        parse_date_text(event.get("published_at"))
        for event in (item.get("supporting_events") or [])
        if isinstance(event, dict)
    ]
    dates = [date for date in dates if date]
    return max(dates) if dates else ""


def is_earnings_only_item(item: dict[str, Any]) -> bool:
    if str(item.get("radar_object_type") or "") != "company":
        return False
    text = item_event_text(item)
    if not any(keyword in text for keyword in EARNINGS_EVENT_KEYWORDS):
        return False
    return not any(keyword in text for keyword in STRUCTURAL_EVENT_KEYWORDS)


def opportunity_precheck(item: dict[str, Any]) -> dict[str, Any]:
    if str(item.get("radar_object_type") or "") != "company":
        return {"status": "not_company", "penalty": 0, "text": ""}
    if not is_earnings_only_item(item):
        context = item.get("price_context") or {}
        daily_return = optional_float(context.get("daily_return")) if isinstance(context, dict) else None
        if daily_return is not None and daily_return >= 0.06:
            return {
                "status": "hot_price_reaction",
                "penalty": 25,
                "text": f"价格预检：最新收盘涨跌 {pct_text(daily_return)}，事件已被明显交易，只保留承接/扩散验证。",
            }
        return {"status": "non_earnings", "penalty": 0, "text": ""}
    context = item.get("price_context") or {}
    if not isinstance(context, dict) or not context:
        return {
            "status": "missing_price",
            "penalty": 75,
            "text": "业绩类预检：缺少最新收盘反应，先由价格底座自动补数，再决定是否进入高优先级。",
        }
    price_date = parse_date_text(context.get("as_of_date"))
    event_date = latest_event_date(item)
    daily_return = optional_float(context.get("daily_return"))
    amount_ratio_20 = optional_float(context.get("amount_ratio_20"))
    positive_days_5d = optional_float(context.get("positive_days_5d"))
    if event_date and price_date and event_date > price_date:
        close_text = f"，上一交易日涨跌 {pct_text(daily_return)}" if daily_return is not None else ""
        return {
            "status": "awaiting_first_trade",
            "penalty": 15,
            "text": f"业绩类预检：事件在 {price_date} 收盘后发布{close_text}，首个交易日反应尚未产生；系统将在下一次生产运行自动补齐首日价格与量能，当前不因这个缺口单独降权。",
        }
    priced_in = (
        daily_return is not None
        and daily_return >= PRICED_IN_DAILY_RETURN
        and (
            (amount_ratio_20 is not None and amount_ratio_20 >= PRICED_IN_AMOUNT_RATIO)
            or (positive_days_5d is not None and positive_days_5d >= PRICED_IN_POSITIVE_DAYS)
        )
    )
    if priced_in:
        amount_text = f"，20日量比 {ratio_x_text(amount_ratio_20)}" if amount_ratio_20 is not None else ""
        return {
            "status": "priced_in",
            "penalty": 80,
            "text": f"业绩类预检：最新收盘涨跌 {pct_text(daily_return)}{amount_text}，利好大概率已被价格/量能先反映，降权到承接验证。",
        }
    partially_priced = daily_return is not None and daily_return >= PARTIAL_PRICED_IN_DAILY_RETURN
    if partially_priced:
        return {
            "status": "partially_priced",
            "penalty": 35,
            "text": f"业绩类预检：最新收盘涨跌 {pct_text(daily_return)}，已有部分反应，只保留为承接/扩散观察。",
        }
    return {
        "status": "needs_confirmation",
        "penalty": 15,
        "text": "业绩类预检：上一交易日未明显反映，需看首个交易日是否补涨和放量。",
    }


def opportunity_precheck_text(item: dict[str, Any]) -> str:
    return str(opportunity_precheck(item).get("text") or "").strip()


def is_generic_subject(subject: str) -> bool:
    if not subject:
        return True
    if subject in GENERIC_EVENT_SUBJECTS:
        return True
    return any(keyword in subject for keyword in ("证券", "政府", "快评", "分会", "副总统", "人民银行", "联合声明", "署长"))


def score_event(event: dict[str, str]) -> int:
    text = event_display_text(event)
    source = str(event.get("source") or "")
    score = 0
    subject = extract_event_subject(text)
    if subject and not is_generic_subject(subject):
        score += 12
    if any(keyword in text for keyword in POSITIVE_EVENT_KEYWORDS):
        score += 10
    if any(keyword in text for keyword in NEGATIVE_EVENT_KEYWORDS):
        score -= 18
    if any(keyword in text for keyword in NEGATIVE_COMPANY_EVENT_KEYWORDS):
        score -= 20
    if any(keyword in text for keyword in LOW_SIGNAL_COMPANY_EVENT_KEYWORDS):
        score -= 18
    if any(keyword in text for keyword in MATERIAL_RISK_COMPANY_EVENT_KEYWORDS):
        score += 16
    if any(keyword in text for keyword in GENERIC_EVENT_KEYWORDS):
        score -= 8
    if any(keyword in text for keyword in NON_INDUSTRY_EVENT_KEYWORDS):
        score -= 18
    if "legacy:industry_signal_scan" in source:
        score -= 20
    if source.startswith("akshare:stock_notice_report"):
        score += 10
    if str(event.get("event_type") or "").strip() in INDUSTRY_EVENT_TYPES and source.startswith("news_event_hub:structured_event"):
        score += 10
    if str(event.get("event_type") or "").strip() in {"commodity_disruption", "production_supply"} and any(
        keyword in text for keyword in COMMODITY_SUPPLY_KEYWORDS
    ):
        score += 18
    if source.startswith("proxy:"):
        score += 8
    return score


def preferred_events(item: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    events = supporting_events(item, limit=12)
    ranked = sorted(events, key=score_event, reverse=True)
    filtered = [event for event in ranked if event_display_text(event)]
    if str(item.get("radar_object_type") or "") == "company":
        material = [event for event in filtered if score_event(event) >= 8]
        if material:
            filtered = material
    if str(item.get("radar_object_type") or "") == "industry":
        structured_sources = [
            event
            for event in filtered
            if str(event.get("source") or "").startswith(("news_event_hub:structured_event", "akshare:stock_notice_report", "proxy:"))
        ]
        if structured_sources:
            filtered = structured_sources + [event for event in filtered if event not in structured_sources]
        non_negative = []
        for event in filtered:
            text = event_display_text(event)
            subject = extract_event_subject(text)
            source = str(event.get("source") or "")
            if score_event(event) < 8:
                continue
            if source.startswith("proxy:"):
                non_negative.append(event)
                continue
            if not contains_cjk(text):
                continue
            if subject and is_generic_subject(subject):
                continue
            if subject:
                non_negative.append(event)
                continue
            if str(event.get("event_type") or "").strip() in INDUSTRY_EVENT_TYPES:
                non_negative.append(event)
        if non_negative:
            filtered = non_negative
        else:
            filtered = []
        non_fund = []
        for event in filtered:
            subject = extract_event_subject(event_display_text(event))
            if subject.endswith(("LOF", "ETF")):
                continue
            non_fund.append(event)
        if non_fund:
            filtered = non_fund
    return filtered[:limit]


def top_event_preview(item: dict[str, Any], *, rules: dict[str, Any]) -> str:
    events = preferred_events(item, limit=1)
    if events and str(item.get("radar_object_type") or "") == "industry":
        event = events[0]
        text = event_display_text(event)
        subject = extract_event_subject(text)
        source = str(event.get("source") or "")
        structured_source = source.startswith(("news_event_hub:structured_event", "akshare:stock_notice_report", "proxy:"))
        fallback_score = int(rules.get("industry_event_fallback_score") or 10)
        force_observe_fallback = bool(rules.get("industry_observe_force_fallback_without_structured_source"))
        if (
            (force_observe_fallback and str(item.get("radar_bucket") or "") == "observe" and not structured_source)
            or score_event(event) < fallback_score
            or (subject and is_generic_subject(subject))
        ):
            events = []
    if not events:
        evidence = [clean_display_text(str(x or "")) for x in (item.get("key_evidence") or []) if clean_display_text(str(x or ""))]
        return evidence[0] if evidence else "暂无明确事件标题"
    return event_display_text(events[0]).replace("\n", " ")


def event_inline_text(events: list[dict[str, str]]) -> str:
    if not events:
        return "当前没有可展开的具体事件标题"
    seen: set[str] = set()
    texts: list[str] = []
    for event in events:
        text = event_display_text(event)
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)
    if not texts:
        return "当前没有可展开的具体事件标题"
    return "；".join(texts)


def event_or_evidence_text(item: dict[str, Any], *, rules: dict[str, Any], limit: int = 1) -> str:
    events = preferred_events(item, limit=limit)
    if events:
        return event_inline_text(events)
    evidence = [
        clean_display_text(str(x or ""))
        for x in (item.get("key_evidence") or [])
        if clean_display_text(str(x or ""))
    ]
    if evidence:
        if str(item.get("radar_object_type") or "") == "industry":
            return "；".join(evidence[:3])
        return evidence[0]
    return top_event_preview(item, rules=rules)


def display_followup_text(item: dict[str, Any], *, rules: dict[str, Any]) -> str:
    followups = [str(x).strip() for x in (item.get("followup_path") or []) if str(x).strip()]
    if not followups:
        return "继续观察下一轮变化"
    if str(item.get("radar_object_type") or "") != "industry":
        return normalize_system_action_text(followups[0])
    top_event = top_event_preview(item, rules=rules)
    event_subject = extract_event_subject(top_event)
    rep_subject = primary_symbol_subject(item)
    if event_subject and is_generic_subject(event_subject):
        return normalize_system_action_text(followups[0])
    if event_subject and rep_subject and event_subject != rep_subject and not is_generic_subject(event_subject):
        symbols = tracking_symbols_text(item)
        suffix = f"；{symbols}" if symbols else ""
        return f"先验证“{event_subject}”事件能否扩散成行业机会，再看跟踪标的是否同步放量/跑赢行业{suffix}"
    if str(item.get("radar_object_type") or "") == "industry":
        symbols = tracking_symbols_text(item)
        if symbols:
            return f"看代表股/ETF 是否同步放量或跑赢行业；{symbols}"
    return normalize_system_action_text(followups[0])


def resolve_item_industry(item: dict[str, Any]) -> str:
    for evidence in item.get("key_evidence") or []:
        text = str(evidence or "").strip()
        if text.startswith("所属方向："):
            return text.replace("所属方向：", "", 1).strip()
    scope = str(item.get("radar_object_scope") or "").strip()
    if " / " in scope:
        return scope.rsplit(" / ", 1)[-1].strip()
    return ""


def merge_semicolon_parts(*values: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in str(value or "").split("；"):
            cleaned = clean_display_text(part)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                parts.append(cleaned)
    return "；".join(parts)


def absorbed_promoted_companies(
    industry_item: dict[str, Any],
    all_items: list[dict[str, Any]],
    kimi_research: dict[str, Any],
    *,
    limit: int = 2,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if str(industry_item.get("radar_object_type") or "") != "industry":
        return []
    industry_name = str(industry_item.get("radar_object_name") or "").strip()
    rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    for company in all_items:
        if not isinstance(company, dict) or str(company.get("radar_object_type") or "") != "company":
            continue
        if resolve_item_industry(company) != industry_name:
            continue
        verdict = kimi_research_verdict(company, kimi_research)
        if str(verdict.get("decision") or "") != "promote":
            continue
        weight_delta = int(verdict.get("weight_delta") or 0)
        if weight_delta <= 0:
            continue
        rank = int(company.get("rank_overall") or 9999)
        rows.append((-weight_delta, rank, company, verdict))
    return [(company, verdict) for _weight, _rank, company, verdict in sorted(rows, key=lambda row: (row[0], row[1]))[:limit]]


def promoted_company_label(company: dict[str, Any], verdict: dict[str, Any]) -> str:
    label = primary_symbol_text(company)
    weight_delta = int(verdict.get("weight_delta") or 0)
    details: list[str] = []
    if weight_delta:
        details.append(f"Kimi +{weight_delta}")
    price_context = company.get("price_context") or {}
    if isinstance(price_context, dict):
        amount_ratio = price_context.get("amount_ratio_20")
        daily_return = price_context.get("daily_return")
        positive_days = price_context.get("positive_days_5d")
        try:
            details.append(f"20日量比 {float(amount_ratio):.2f}x")
        except (TypeError, ValueError):
            pass
        try:
            details.append(f"收盘 {float(daily_return) * 100:+.1f}%")
        except (TypeError, ValueError):
            pass
        try:
            details.append(f"5日阳线 {float(positive_days) * 100:.0f}%")
        except (TypeError, ValueError):
            pass
    return f"{label}（{'，'.join(details)}）" if details else label


def absorbed_company_tracking_text(absorbed: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    if not absorbed:
        return ""
    return "触发公司：" + "、".join(promoted_company_label(company, verdict) for company, verdict in absorbed)


def absorbed_company_action_text(
    base_action: str,
    absorbed: list[tuple[dict[str, Any], dict[str, Any]]],
    industry_item: dict[str, Any],
) -> str:
    if not absorbed:
        return base_action
    company_checks: list[str] = []
    for company, _verdict in absorbed:
        label = primary_symbol_text(company).replace(" ", "")
        company_checks.append(f"{label}价格承接/成交额20日比值")
    etfs = [clean_display_text(str(x or "")) for x in (industry_item.get("etf_proxies") or []) if clean_display_text(str(x or ""))]
    if etfs:
        company_checks.append(f"{etfs[0]}净值/份额")
    prefix = "1-3个交易日：" + "；".join(company_checks)
    if not base_action:
        return prefix
    cleaned_base = clean_display_text(base_action)
    if all(primary_symbol_text(company).split()[0] in cleaned_base for company, _verdict in absorbed):
        return cleaned_base
    cleaned_base = re.sub(r"^1-3个交易日[：:]\s*", "", cleaned_base)
    return merge_semicolon_parts(prefix, cleaned_base)


def object_mix_summary(snapshot: dict[str, Any]) -> str:
    counts = object_type_counts(snapshot)
    if not counts:
        return "当前没有对象进入 snapshot。"
    parts = [f"{name} {count}" for name, count in counts.items()]
    summary = " / ".join(parts)
    if len(counts) == 1 and "industry" in counts:
        return f"当前 live 对象覆盖仍然是 `{summary}`，这说明这轮真实主链还没有进入 mixed-object Radar 状态。"
    return f"当前 live 对象覆盖为 `{summary}`。"


def market_sentiment_summary_lines(snapshot: dict[str, Any]) -> list[str]:
    context = snapshot.get("market_sentiment_context") or {}
    if not isinstance(context, dict) or not context:
        return []
    summary = compact_market_sentiment_text(context)
    if not summary:
        return []
    lines = [f"- 当前市场情绪背景：{summary}。"]
    lag_days = int(context.get("lag_days") or 0)
    sidecar_date = str(context.get("as_of_date") or "").strip()
    if lag_days > 0 and sidecar_date:
        lines.append(f"- 情绪 sidecar 最新样本日期为 `{sidecar_date}`，相对当前 Radar 样本滞后 `{lag_days}` 天。")
    return lines


def source_readiness_summary_lines(path: Path = DEFAULT_SOURCE_READINESS_JSON) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    status = str(payload.get("status") or "").strip() or "unknown"
    expected_sample_date = str(payload.get("expected_sample_date") or "").strip()
    if status == "pass":
        return [f"- 当前上游源数据门禁为 `pass`：新闻 / 情绪 / 市场三层都已对齐到期望样本 `{expected_sample_date}`。"]
    blockers = [str(item).strip() for item in (payload.get("blockers") or []) if str(item).strip()]
    if blockers:
        return [f"- 当前上游源数据门禁为 `{status}`：{'；'.join(blockers[:3])}"]
    warnings = [str(item).strip() for item in (payload.get("warnings") or []) if str(item).strip()]
    if warnings:
        return [f"- 当前上游源数据门禁为 `{status}`：{'；'.join(warnings[:3])}"]
    return [f"- 当前上游源数据门禁为 `{status}`。"]


def price_freshness_summary_lines(payload: dict[str, Any]) -> list[str]:
    if not payload:
        return []
    status = str(payload.get("status") or "").strip() or "unknown"
    target_date = str(payload.get("target_date") or "").strip()
    used_lane = str(payload.get("used_lane") or "").strip()
    csv_latest = str((payload.get("csv_status") or {}).get("latest_as_of_date") or "").strip()
    db_latest = str((payload.get("db_status") or {}).get("latest_trade_date") or "").strip()
    if status == "pass":
        return [
            f"- 价格补数门禁为 `pass`：当前会在价格样本缺口出现时主动调用 canonical 价格系统，当前目标交易日 `{target_date}` 已补齐"
            f"（lane `{used_lane or 'unknown'}` / DB `{db_latest or 'unknown'}` / CSV `{csv_latest or 'unknown'}`）。"
        ]
    if status == "warn":
        return [
            f"- 价格底座已对齐目标交易日 `{target_date or 'unknown'}`；报告前排公司通过最新价格门禁，长尾候选缺口保留在后台 freshness 账本"
            f"（lane `{used_lane or 'unknown'}` / DB `{db_latest or 'unknown'}` / CSV `{csv_latest or 'unknown'}`）。"
        ]
    return [
        f"- 价格补数门禁为 `{status}`：目标交易日 `{target_date or 'unknown'}` 尚未完全补齐"
        f"（lane `{used_lane or 'unknown'}` / DB `{db_latest or 'unknown'}` / CSV `{csv_latest or 'unknown'}`）。"
    ]


def news_verification_summary_lines(payload: dict[str, Any]) -> list[str]:
    if not payload:
        return []
    status = str(payload.get("status") or "").strip() or "unknown"
    verified_count = int(payload.get("verified_count") or 0)
    if status == "skip":
        return ["- 新闻补核本轮未触发：当前前排对象暂时没有进入按需 discovery 的公司线索。"]
    pass_count = sum(1 for item in (payload.get("items") or []) if str((item or {}).get("status") or "") == "pass")
    if status == "warn":
        return [
            f"- 新闻补核已运行：`{verified_count}` 个前排公司对象进入补核账本，其中 `{pass_count}` 个拿到可用补充；未补齐对象转入后台研究提醒。"
        ]
    return [
        f"- 新闻补核状态为 `{status}`：已对 `{verified_count}` 个前排公司对象调用 News Event Hub 补搜，当前 `{pass_count}` 个对象拿到了可用的相关文章/相关事件补充。"
    ]


def news_verification_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        object_id = str(item.get("radar_object_id") or "").strip()
        object_name = str(item.get("radar_object_name") or "").strip()
        if object_id:
            index[object_id] = item
        if object_name and object_name not in index:
            index[object_name] = item
    return index


def news_verification_line(item: dict[str, Any], verification_index: dict[str, dict[str, Any]]) -> str:
    lookup = verification_index.get(str(item.get("radar_object_id") or "").strip()) or verification_index.get(
        str(item.get("radar_object_name") or "").strip()
    )
    if not lookup:
        return ""
    coverage_after = lookup.get("coverage_after") or {}
    events = int(coverage_after.get("matching_events") or 0)
    articles = int(coverage_after.get("matching_articles") or 0)
    latest = str(lookup.get("latest_published_at") or "").strip()
    titles = [str(x).strip() for x in (lookup.get("top_titles") or []) if str(x).strip()]
    core = f"新闻补核：相关事件 {events} 条 / 相关文章 {articles} 条"
    if latest:
        core += f" / 最新 {latest[:16].replace('T', ' ')}"
    if titles:
        core += f" / 标题 {best_verification_title(str(item.get('radar_object_name') or ''), titles)}"
    return core


def sanitize_company_title_for_display(name: str, title: str) -> str:
    cleaned = str(title or "").strip()
    if not cleaned:
        return ""
    subject = clean_display_text(name)
    if subject:
        cleaned = re.sub(
            rf"^.*?\${re.escape(subject)}(?:\((?:SH|SZ|BJ|HK)\d{{4,6}}\))?\$\s*",
            "",
            cleaned,
            count=1,
        ).strip()
    cleaned = clean_display_text(cleaned)
    if subject:
        cleaned = re.sub(rf"^(?:专栏)?{re.escape(subject)}[:：]\s*", "", cleaned).strip()
    if subject and cleaned and any(keyword in cleaned for keyword in DISPLAY_RELEVANT_COMPANY_TOKENS):
        return f"{subject}：{cleaned[:100]}"
    if subject and cleaned and len(cleaned) <= 36:
        return f"{subject}：{cleaned[:100]}"
    if subject:
        return f"{subject}：社交舆情发酵，待补核"
    return cleaned[:100]


def best_verification_title(name: str, titles: list[str]) -> str:
    best = ""
    best_score = -1
    for raw in titles:
        candidate = sanitize_company_title_for_display(name, raw)
        if not candidate:
            continue
        score = 0
        if "社交舆情发酵，待补核" not in candidate:
            score += 3
        if any(token in candidate for token in DISPLAY_RELEVANT_COMPANY_TOKENS):
            score += 4
        if any(token in candidate for token in ("净利润", "收购", "增持", "回购", "停牌", "订单", "中标")):
            score += 3
        if len(candidate) <= 40:
            score += 1
        if candidate.count("：") == 1:
            score += 1
        if any(token in candidate for token in ("看了一些人的发言", "挺有意思", "…")):
            score -= 4
        if score > best_score:
            best = candidate
            best_score = score
    return best or sanitize_company_title_for_display(name, titles[0] if titles else "")


def company_sentiment_line(item: dict[str, Any]) -> str:
    context = item.get("sentiment_context") or {}
    if not isinstance(context, dict) or not context:
        return ""
    if not any(
        context.get(key) not in (None, "")
        for key in (
            "company_market_sentiment",
            "company_event_sentiment",
            "company_composite_sentiment",
            "company_market_score",
            "company_event_score",
            "company_composite_score",
        )
    ):
        return ""
    return (
        "事件情绪 {event_score}（{event_label}） | 行情情绪 {market_score}（{market_label}） | 综合情绪 {composite_score}（{composite_label}）"
    ).format(
        event_score=int(context.get("company_event_score") or 0),
        event_label=str(context.get("company_event_label") or "未标注"),
        market_score=int(context.get("company_market_score") or 0),
        market_label=str(context.get("company_market_label") or "未标注"),
        composite_score=int(context.get("company_composite_score") or 0),
        composite_label=str(context.get("company_composite_label") or "未标注"),
    )


def company_quant_line(item: dict[str, Any]) -> str:
    context = item.get("quant_signal_context") or {}
    if not isinstance(context, dict) or str(context.get("status") or "") != "pass":
        return ""
    bundle = dict(context.get("bundle_summary") or {})
    signals = dict(bundle.get("signals") or {})
    alpha = dict(signals.get("alpha158") or {})
    timesfm = dict(signals.get("timesfm") or {})
    parts: list[str] = []
    normalized_score = optional_float(alpha.get("normalized_score"))
    rank = alpha.get("rank")
    if normalized_score is not None:
        alpha_text = f"Alpha158 {normalized_score:.2f}"
        if rank not in (None, ""):
            try:
                alpha_text += f" / rank {int(rank)}"
            except (TypeError, ValueError):
                pass
        driver = str(alpha.get("primary_driver_label") or alpha.get("primary_driver_feature") or "").strip()
        if driver:
            alpha_text += f" / driver {driver}"
        parts.append(alpha_text)
    directional_view = str(timesfm.get("directional_view") or "").strip()
    expected_return_5d = optional_float(timesfm.get("expected_return_5d"))
    if directional_view or expected_return_5d is not None:
        timesfm_text = f"TimesFM {directional_view or 'neutral'}"
        if expected_return_5d is not None:
            timesfm_text += f" / 5d {expected_return_5d * 100:+.1f}%"
        confidence_hint = str(timesfm.get("confidence_hint") or "").strip()
        if confidence_hint:
            timesfm_text += f" / {confidence_hint}"
        parts.append(timesfm_text)
    return "；".join(parts[:2])


def company_price_reaction_line(item: dict[str, Any]) -> str:
    context = item.get("price_context") or item.get("sentiment_context") or {}
    if not isinstance(context, dict) or not context:
        return ""
    lag_days = int(context.get("lag_days") or 0)
    as_of_date = str(context.get("as_of_date") or "").strip()
    trading_status = str(context.get("trading_status") or "").strip()
    close_price = optional_float(context.get("close"))
    daily_return = optional_float(context.get("daily_return"))
    amount_ratio_20 = optional_float(context.get("amount_ratio_20"))
    positive_days_5d = optional_float(context.get("positive_days_5d"))
    parts: list[str] = []
    if trading_status == "halt_on_market_sample_date" and as_of_date:
        parts.append(f"停牌前最后收盘 {as_of_date}")
    elif lag_days > 0 and as_of_date:
        parts.append(f"价格样本 {as_of_date}（滞后 {lag_days} 天）")
    if close_price is not None and close_price > 0:
        parts.append(f"收盘 {close_price:.2f}")
    if daily_return is not None:
        parts.append(f"收盘涨跌 {pct_text(daily_return)}")
    if amount_ratio_20 is not None:
        parts.append(f"20日量比 {ratio_x_text(amount_ratio_20)}")
    if positive_days_5d is not None:
        parts.append(f"近5日阳线占比 {positive_days_5d:.0%}")
    return " | ".join(parts)


def score_breakdown_line(item: dict[str, Any]) -> str:
    return (
        "总分 {score} | 值得看 {worth} / why now {why_now} / 确认度 {confidence} / 跟踪价值 {followup}"
    ).format(
        score=int(item.get("radar_score") or 0),
        worth=int(item.get("worth_watching") or 0),
        why_now=int(item.get("why_now_strength") or 0),
        confidence=int(item.get("confidence") or 0),
        followup=int(item.get("followup_value") or 0),
    )


def evidence_detail_line(item: dict[str, Any]) -> str:
    evidence = [clean_display_text(str(x or "")) for x in (item.get("key_evidence") or []) if clean_display_text(str(x or ""))]
    object_type = str(item.get("radar_object_type") or "")
    if object_type == "company":
        reaction = company_price_reaction_line(item)
        if reaction:
            return reaction
        usable = [
            text
            for text in evidence
            if not text.startswith(("所属方向：", "股票代码：", "母行业分数", "情绪侧车：", "收盘反应："))
        ]
        return compact_metric_tail(usable[1:], limit=1) if len(usable) > 1 else ""
    if object_type == "macro":
        return compact_metric_tail(evidence[1:], limit=2)
    if object_type == "industry":
        return compact_metric_tail(evidence[1:], limit=2)
    return compact_metric_tail(evidence[1:], limit=2)


def workflow_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, str]:
    triage = TRIAGE_PRIORITY.get(str(item.get("triage_action") or ""), 99)
    evidence = EVIDENCE_PRIORITY.get(str(item.get("evidence_quality") or ""), 99)
    bucket_rank = BUCKETS.index(str(item.get("radar_bucket") or "")) if str(item.get("radar_bucket") or "") in BUCKETS else len(BUCKETS)
    return (
        triage,
        evidence,
        bucket_rank,
        -int(item.get("radar_score") or 0),
        str(item.get("radar_object_name") or ""),
    )


def opportunity_event_score(item: dict[str, Any]) -> int:
    events = preferred_events(item, limit=3)
    if not events:
        return 0
    return max(score_event(event) for event in events)


def opportunity_breadth_score(item: dict[str, Any]) -> int:
    score = 0
    for evidence in item.get("key_evidence") or []:
        text = str(evidence or "")
        match = re.search(r"(\d+)\s*条", text)
        if not match:
            continue
        count = int(match.group(1))
        if "结构化事件" in text:
            score += count * 3
        elif "政策/舆情事件" in text:
            score += count
    return score


def kimi_research_verdict_maps(kimi_research: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    if str(kimi_research.get("status") or "") not in {"pass", "deterministic_fallback"}:
        return by_id, by_name
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


def kimi_research_decision(item: dict[str, Any], kimi_research: dict[str, Any]) -> str:
    return str(kimi_research_verdict(item, kimi_research).get("decision") or "").strip()


def kimi_research_weight_delta(item: dict[str, Any], kimi_research: dict[str, Any]) -> int:
    verdict = kimi_research_verdict(item, kimi_research)
    try:
        return int(verdict.get("weight_delta") or 0)
    except (TypeError, ValueError):
        return 0


def kimi_research_sort_penalty(item: dict[str, Any], kimi_research: dict[str, Any]) -> int:
    decision = kimi_research_decision(item, kimi_research)
    decision_penalty = {
        "promote": -60,
        "keep": 0,
        "watch_only": 80,
        "research_gap": 70,
        "risk_review": 90,
        "reject": 120,
    }.get(decision, 0)
    return decision_penalty - kimi_research_weight_delta(item, kimi_research)


def kimi_research_allows_top(item: dict[str, Any], kimi_research: dict[str, Any]) -> bool:
    decision = kimi_research_decision(item, kimi_research)
    if not decision:
        return str(kimi_research.get("status") or "") not in {"pass", "deterministic_fallback"}
    return decision in {"promote", "keep"}


def mixed_direction_top_penalty(item: dict[str, Any]) -> int:
    if str(item.get("radar_object_type") or "") != "industry":
        return 0
    text = item_event_text(item)
    name = str(item.get("radar_object_name") or "")
    penalty = 0
    if str(item.get("event_driven_lane") or "") == "soft_catalyst_watchlist" and "缺行业代理变量" in str(item.get("confirmation_gap") or ""):
        penalty += 80
    if name in {"石油石化", "煤炭"} and any(token in text for token in OIL_SUPPLY_INCREASE_KEYWORDS):
        penalty += 220
    if name in {"有色金属", "黄金"} and "黄金" in text and any(token in text for token in COMMODITY_PRICE_DOWN_KEYWORDS):
        penalty += 220
    return penalty


def bucket_table_limit(bucket: str, rules: dict[str, Any]) -> int:
    limits = rules.get("bucket_table_limits") or {}
    if isinstance(limits, dict):
        value = limits.get(bucket)
        if value is not None:
            return int(value)
    defaults = {
        "strong_alert": 5,
        "strong_candidate": 6,
        "research_candidate": 8,
        "observe": 8,
    }
    return defaults.get(bucket, 8)


def table_for_bucket(title: str, items: list[dict[str, Any]], *, rules: dict[str, Any]) -> list[str]:
    lines = [f"### {bucket_label(title)}", ""]
    if not items:
        lines.append("- 无")
        lines.append("")
        return lines
    limited_items = items[: bucket_table_limit(title, rules)]
    for item in limited_items:
        lines.append(
            "- {name} | {type} | 排名 {rank} | 分数 {score} | {why} | 动作：{next_step}".format(
                name=str(item.get("radar_object_name") or ""),
                type=object_type_label(item.get("radar_object_type")),
                rank=int(item.get("rank_in_bucket") or 0),
                score=int(item.get("radar_score") or 0),
                why=top_event_preview(item, rules=rules).replace("|", "/"),
                next_step=display_followup_text(item, rules=rules).replace("\n", " ").replace("|", "/"),
            )
        )
    if len(items) > len(limited_items):
        lines.append(f"- 其余 {len(items) - len(limited_items)} 个对象已省略，详见 handoff / snapshot。")
    lines.append("")
    return lines


def top_opportunities(snapshot: dict[str, Any], *, rules: dict[str, Any], kimi_research: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    kimi_research = kimi_research or {}
    excluded = {str(x) for x in (rules.get("top_opportunities_exclude_triage") or [])}
    limit = int(rules.get("top_opportunities_limit") or 5)
    max_precheck_penalty = int(rules.get("top_opportunities_max_precheck_penalty") or 30)
    items = [
        item
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict) and str(item.get("triage_action") or "") not in excluded
    ]
    actionable = [
        item
        for item in items
        if not (
            (
                str(item.get("radar_object_type") or "") == "industry"
                and top_event_preview(item, rules=rules) in {"暂无明确事件标题", "当前没有可展开的具体事件标题"}
            )
            or (
                str(item.get("radar_object_type") or "") == "company"
                and not (item.get("price_context") or {})
            )
            or (
                str(item.get("radar_object_type") or "") == "company"
                and int(((item.get("price_context") or {}).get("lag_days") or 0)) > 0
                and str(((item.get("price_context") or {}).get("trading_status") or "")) != "halt_on_market_sample_date"
            )
            or (
                str(item.get("radar_object_type") or "") == "company"
                and str(item.get("radar_bucket") or "") == "observe"
                and (
                    "社交舆情发酵，待补核" in top_event_preview(item, rules=rules)
                    or "价格样本停在" in str(item.get("confirmation_gap") or "")
                )
            )
        )
    ]
    publishable = [
        item
        for item in actionable
        if int(opportunity_precheck(item).get("penalty") or 0) <= max_precheck_penalty
        and mixed_direction_top_penalty(item) < 200
        and kimi_research_allows_top(item, kimi_research)
    ]
    if publishable:
        items = publishable
    elif len(actionable) >= limit:
        items = actionable
    items.sort(
        key=lambda item: (
            1
            if str(item.get("radar_object_type") or "") == "industry"
            and top_event_preview(item, rules=rules) in {"暂无明确事件标题", "当前没有可展开的具体事件标题"}
            else 0,
            int(opportunity_precheck(item).get("penalty") or 0),
            mixed_direction_top_penalty(item),
            kimi_research_sort_penalty(item, kimi_research),
            *workflow_sort_key(item),
            -opportunity_event_score(item),
            -opportunity_breadth_score(item),
        )
    )
    return items[:limit]


def company_event_board(snapshot: dict[str, Any], *, rules: dict[str, Any]) -> list[tuple[str, str, str]]:
    limit = int(rules.get("company_event_board_limit") or 12)
    rows: list[tuple[int, str, str, str]] = []
    for item in snapshot.get("objects") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("radar_object_type") or "") != "company":
            continue
        company_label = primary_symbol_text(item)
        industry_name = resolve_item_industry(item) or "未识别归属"
        for event in preferred_events(item, limit=6):
            text = event_display_text(event)
            subject = extract_event_subject(text)
            if not subject or is_generic_subject(subject):
                continue
            rows.append((score_event(event) + int(item.get("radar_score") or 0), company_label, industry_name, text))
    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for _, company_label, industry_name, text in sorted(rows, key=lambda row: row[0], reverse=True):
        key = (company_label, text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((company_label, industry_name, text))
        if len(deduped) >= limit:
            break
    return deduped


def proxy_signal_board(snapshot: dict[str, Any], *, rules: dict[str, Any]) -> list[tuple[str, str]]:
    limit = int(rules.get("proxy_signal_board_limit") or 8)
    has_macro = any(
        isinstance(item, dict) and str(item.get("radar_object_type") or "") == "macro"
        for item in (snapshot.get("objects") or [])
    )
    rows: list[tuple[int, str, str]] = []
    for item in snapshot.get("objects") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("radar_object_type") or "") == "macro":
            evidence = [clean_display_text(str(x or "")) for x in (item.get("key_evidence") or []) if str(x or "").strip()]
            signal_text = "；".join(evidence[:2]) if evidence else top_event_preview(item)
            rows.append((int(item.get("radar_score") or 0), str(item.get("radar_object_name") or ""), signal_text))
            continue
        if has_macro:
            continue
        for evidence in item.get("key_evidence") or []:
            text = str(evidence or "").strip()
            if not text.startswith("行业代理："):
                continue
            rows.append((int(item.get("radar_score") or 0), str(item.get("radar_object_name") or ""), text.replace("行业代理：", "", 1).strip()))
    rows.sort(key=lambda row: row[0], reverse=True)
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for _, industry_name, signal_text in rows:
        key = (industry_name, signal_text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((industry_name, signal_text))
        if len(deduped) >= limit:
            break
    return deduped


def board_items(
    snapshot: dict[str, Any],
    lane: str,
    *,
    limit: int,
    include_triage: set[str] | None = None,
    exclude_triage: set[str] | None = None,
) -> list[dict[str, Any]]:
    items = [
        item
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict)
        and str(item.get("event_driven_lane") or "") == lane
        and (include_triage is None or str(item.get("triage_action") or "") in include_triage)
        and (exclude_triage is None or str(item.get("triage_action") or "") not in exclude_triage)
    ]
    items.sort(key=workflow_sort_key)
    return items[:limit]


def compact_board_lines(title: str, items: list[dict[str, Any]]) -> list[str]:
    lines = [f"### {title}", ""]
    if not items:
        lines.append("- 无")
        lines.append("")
        return lines
    is_risk_board = title == "Risk Monitor"
    for item in items:
        detail = evidence_detail_line(item)
        if is_risk_board:
            lines.append(
                "- {name} | {type} | 风险点：{detail} | 复核：{milestone}".format(
                    name=str(item.get("radar_object_name") or ""),
                    type=object_type_label(item.get("radar_object_type")),
                    detail=detail or str(item.get("confirmation_gap") or ""),
                    milestone=display_followup_text(item, rules={}),
                )
            )
        else:
            lines.append(
                "- {name} | {type} | {triage} | {catalyst} | {detail} | 动作：{milestone}".format(
                    name=str(item.get("radar_object_name") or ""),
                    type=object_type_label(item.get("radar_object_type")),
                    triage=triage_label(item.get("triage_action")),
                    catalyst=catalyst_summary(item),
                    detail=detail or str(item.get("confirmation_gap") or ""),
                    milestone=display_followup_text(item, rules={}),
                )
            )
    lines.append("")
    return lines


def triage_items(snapshot: dict[str, Any], action: str, *, limit: int) -> list[dict[str, Any]]:
    items = [
        item
        for item in (snapshot.get("objects") or [])
        if isinstance(item, dict) and str(item.get("triage_action") or "") == action
    ]
    items.sort(key=workflow_sort_key)
    return items[:limit]


def triage_lines(title: str, items: list[dict[str, Any]]) -> list[str]:
    lines = [f"### {title}", ""]
    if not items:
        lines.append("- 无")
        lines.append("")
        return lines
    for item in items:
        detail = evidence_detail_line(item)
        bucket_text = triage_label(item.get("triage_action")) if str(item.get("triage_action") or "") == "risk_review" else bucket_label(item.get("radar_bucket"))
        lines.append(
            "- {name} | {type} | {bucket} | 证据：{quality} | 催化：{catalyst} | {detail} | 动作：{next_step}".format(
                name=str(item.get("radar_object_name") or ""),
                type=object_type_label(item.get("radar_object_type")),
                bucket=bucket_text,
                quality=evidence_label(item.get("evidence_quality")),
                catalyst=catalyst_summary(item),
                detail=detail or str(item.get("confirmation_gap") or ""),
                next_step=display_followup_text(item, rules={}),
            )
        )
    lines.append("")
    return lines


def pm_quick_view_lines(items: list[dict[str, Any]], *, rules: dict[str, Any], verification_index: dict[str, dict[str, Any]]) -> list[str]:
    lines = ["### PM Quick View", "", "| 对象 | 动作 | 催化 | 当前抓手 | 盘后反应/关键信号 |", "| --- | --- | --- | --- | --- |"]
    if not items:
        lines.append("| 无 | - | - | - | - |")
        lines.append("")
        return lines
    for item in items[: int(rules.get("top_opportunities_limit") or 5)]:
        detail_parts = [evidence_detail_line(item) or "无"]
        quant_line = company_quant_line(item)
        if quant_line:
            detail_parts.append(quant_line)
        detail = "；".join(part.replace("|", " / ") for part in detail_parts if part)
        lines.append(
            "| {name} | {action} | {catalyst} | {followup} | {detail} |".format(
                name=str(item.get("radar_object_name") or ""),
                action=triage_label(item.get("triage_action")),
                catalyst=catalyst_type_label(item.get("catalyst_type")),
                followup=display_followup_text(item, rules=rules).replace("\n", " "),
                detail=detail,
            )
        )
    lines.append("")
    return lines


def score_method_lines() -> list[str]:
    return [
        "### 评分怎么看",
        "",
        "- `Radar score` 是排序索引，不是收益预测。",
        "- `why_now_strength = 热度 35% + 政策/舆情 25% + 结构化事件 20% + 辅助资金/结构 20%`。",
        "- `confidence = 基本面 40% + 政策 20% + 结构化事件 20% + 来源丰富度 20%`。",
        "- `followup_value` 主要看研究抓手：代表股、ETF、代理变量、结构化事件、政策、overlay 是否齐全。",
        "- 公司对象先按 `母行业分数 × 0.58 + 20 + event_rank_score` 形成基线，再叠加催化类型、映射质量、情绪 sidecar；若事件当日已强反应，会再扣减“已被价格兑现”的分数。",
        "",
    ]


def kimi_preliminary_research_lines(kimi_editorial: dict[str, Any]) -> list[str]:
    rows = [item for item in (kimi_editorial.get("preliminary_research") or []) if isinstance(item, dict)]
    if not rows:
        return []
    lines = ["### Kimi 初步研究", ""]
    for item in rows[:3]:
        lines.append(
            "- {name} | 机会雏形：{setup} | 初判：{early_read} | 下一步：{next_check}".format(
                name=str(item.get("name") or ""),
                setup=str(item.get("setup") or ""),
                early_read=str(item.get("early_read") or ""),
                next_check=str(item.get("next_check") or ""),
            )
        )
    lines.append("")
    return lines


def ipo_research_summary(item: dict[str, Any]) -> str:
    research = item.get("subscription_research") or {}
    if not isinstance(research, dict):
        return ""
    def labeled_value(label: str, value: Any) -> str:
        text = clean_display_text(str(value or ""))
        for prefix in (f"{label}：", f"{label}:", "风险：", "风险:"):
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
        return f"{label}：{text}" if text else ""
    fields = [
        ("窗口", research.get("window")),
        ("拥挤", research.get("crowding")),
        ("发行人估值", research.get("issuer_valuation")),
        ("估值", research.get("valuation")),
        ("保荐", research.get("sponsor")),
        ("招股书", research.get("prospectus")),
        ("风险", research.get("risk") or research.get("skip_reason")),
    ]
    parts = [part for label, value in fields if (part := labeled_value(label, value))]
    return "；".join(parts)


def md_cell(value: Any, *, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\n", " ")).strip()
    text = text.replace("|", "/")
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def ipo_placeholder_text(text: str) -> bool:
    cleaned = md_cell(text, limit=0)
    if not cleaned:
        return True
    exact_placeholders = {
        "IPO 明细源暂未返回发售价/估值",
        "发行人PE/盈利口径待抓取，不能只用可比PE替代",
        "发行人PE/盈利口径待抓取，不能只用可比PE替代发行人估值",
        "当前孖展/公开认购倍数未披露",
        "申购起止日待核",
        "估值样本待抓取",
        "保荐人 保荐人待核；基石偏弱或待核",
        "招股书摘要待抓取",
        "核心风险待抓取",
    }
    if cleaned in exact_placeholders:
        return True
    if any(token in cleaned for token in ("待抓取", "待核", "暂未返回")):
        useful_detail = bool(re.search(r"\d", cleaned) or "；" in cleaned or ";" in cleaned)
        return not useful_detail
    return False


def ipo_material_text(value: Any, *, limit: int = 260) -> str:
    text = md_cell(value, limit=limit)
    return "" if ipo_placeholder_text(text) else text


def ipo_score_text(item: dict[str, Any]) -> str:
    research = item.get("subscription_research") if isinstance(item.get("subscription_research"), dict) else {}
    score = research.get("score") if isinstance(research, dict) else item.get("subscription_score")
    if score in (None, "", 0):
        return ""
    return f"{score}分"


def ipo_comparable_text(item: dict[str, Any]) -> str:
    research = item.get("subscription_research") if isinstance(item.get("subscription_research"), dict) else {}
    rows = research.get("comparables") if isinstance(research, dict) else []
    if not isinstance(rows, list):
        rows = []
    parts: list[str] = []
    for row in rows[:6]:
        if not isinstance(row, dict):
            continue
        name = md_cell(row.get("name"), limit=40)
        details = []
        if row.get("ten_day_change"):
            details.append(f"10日{row.get('ten_day_change')}")
        if row.get("pe") and str(row.get("pe")) != "N/A":
            details.append(f"PE {row.get('pe')}x")
        if row.get("market_cap") and str(row.get("market_cap")) != "N/A":
            details.append(f"市值{row.get('market_cap')}")
        if name:
            parts.append(f"{name}（{'，'.join(details) if details else 'N/A'}）")
    return "；".join(parts)


def ipo_card_lines(item: dict[str, Any]) -> list[str]:
    research = item.get("subscription_research") if isinstance(item.get("subscription_research"), dict) else {}
    if not isinstance(research, dict):
        research = {}
    name = ipo_display_name(item)
    code = ipo_code_text(item) or clean_display_text(str(item.get("code") or ""))
    verdict = md_cell(research.get("verdict") or item.get("subscription_judgement") or item.get("potential"))
    score = ipo_score_text(item)
    status = md_cell("、".join(str(x) for x in (item.get("event_tags") or [])), limit=90)
    window = ipo_material_text(research.get("window") or status, limit=140)
    valuation = ipo_material_text(research.get("valuation") or "IPO 明细源暂未返回发售价/估值", limit=360)
    issuer_valuation = ipo_material_text(research.get("issuer_valuation"), limit=300)
    crowding = ipo_material_text(research.get("crowding"), limit=180)
    sponsor = ipo_material_text(research.get("sponsor"), limit=220)
    prospectus = ipo_material_text(research.get("prospectus"), limit=360)
    risk = ipo_material_text((research.get("risk") or research.get("skip_reason")), limit=260)
    action = md_cell(item.get("execution_action"), limit=240)
    comparable = md_cell(ipo_comparable_text(item), limit=360)
    lines = [
        f"##### {name} {code}".strip(),
        "",
        "| 项 | 内容 |",
        "| --- | --- |",
        f"| 结论 | {md_cell(' / '.join(x for x in (verdict, score) if x), limit=120)} |",
    ]
    if window:
        lines.append(f"| 窗口 | {window} |")
    if valuation:
        lines.append(f"| 发行条款 / 可比 | {valuation} |")
    if issuer_valuation:
        lines.append(f"| 发行人估值 | {issuer_valuation} |")
    if crowding:
        lines.append(f"| 拥挤度 | {crowding} |")
    if comparable:
        lines.append(f"| 可比样本 | {comparable} |")
    if sponsor:
        lines.append(f"| 保荐 / 基石 | {sponsor} |")
    if prospectus:
        lines.append(f"| 招股书判断 | {prospectus} |")
    if risk:
        for prefix in ("风险：", "风险:"):
            if risk.startswith(prefix):
                risk = risk[len(prefix) :].strip()
        lines.append(f"| 风险 / 跳过原因 | {risk} |")
    if action:
        lines.append(f"| 申购动作 | {action} |")
    lines.append("")
    return lines


def ipo_watchlist_lines(ipo_watchlist: dict[str, Any], hk_ipo_watchlist: dict[str, Any], kimi_editorial: dict[str, Any]) -> list[str]:
    kimi_rows = [item for item in (kimi_editorial.get("ipo_watchlist") or []) if isinstance(item, dict)]
    raw_rows = [item for item in (ipo_watchlist.get("items") or []) if isinstance(item, dict)]
    hk_rows = [item for item in (hk_ipo_watchlist.get("items") or []) if isinstance(item, dict)]
    lines = ["### A/H IPO 打新申购初筛", ""]
    seen: set[str] = set()
    seen_name_keys: set[str] = set()
    seen_codes: set[str] = set()
    def remember_ipo_aliases(item: dict[str, Any], *extra_names: str) -> None:
        for alias in (
            *extra_names,
            str(item.get("name") or ""),
            str(item.get("display_name_cn") or ""),
            str(item.get("short_name_cn") or ""),
            str(item.get("name_en") or ""),
        ):
            alias_key = canonical_name_key(alias)
            if alias_key:
                seen.add(alias_key)
                seen_name_keys.add(alias_key)

    def ipo_name_already_seen(name: str) -> bool:
        key = canonical_name_key(name)
        if not key:
            return False
        return any(
            key == seen_key or (min(len(key), len(seen_key)) >= 4 and (key in seen_key or seen_key in key))
            for seen_key in seen_name_keys
        )

    raw_display_rows = [*raw_rows, *hk_rows]
    raw_display_rows.sort(
        key=lambda item: (
            0 if str(item.get("ipo_research_bucket") or "") == "actionable" else 1,
            str(item.get("subscription_end_date") or item.get("listing_date") or ""),
            str(item.get("code") or ""),
        )
    )
    actionable_lines: list[str] = []
    skipped_lines: list[str] = []
    if raw_display_rows:
        for item in raw_display_rows[:8]:
            name = ipo_display_name(item)
            code = clean_display_text(str(item.get("code") or ""))
            normalized_code = ipo_code_text(item) or code
            key = canonical_name_key(name) or normalized_code
            if (normalized_code and normalized_code in seen_codes) or (key and key in seen):
                continue
            if key:
                seen.add(key)
            remember_ipo_aliases(item, name)
            if normalized_code:
                seen_codes.add(normalized_code)
            target_lines = actionable_lines if str(item.get("ipo_research_bucket") or "") == "actionable" else skipped_lines
            target_lines.extend(ipo_card_lines(item))
    if actionable_lines:
        lines.extend(["#### 可申购 / 待申购", "", *actionable_lines, ""])
    if skipped_lines:
        lines.extend(["#### 不达标 / 跳过", "", *skipped_lines, ""])
    if kimi_rows:
        for item in kimi_rows[:4]:
            name = ipo_display_name(item)
            code = ipo_code_text(item)
            key = canonical_name_key(name) or code
            if (code and code in seen_codes) or (name and ipo_name_already_seen(name)) or (key and key in seen):
                continue
            if name:
                seen.add(key)
                remember_ipo_aliases(item, name)
            if code:
                seen_codes.add(code)
            action = clean_display_text(str(item.get("execution_action") or item.get("next_check") or ""))
            if action and any(token in action for token in ("上市首日", "首日承接", "暗盘")) and "申购" not in action:
                continue
            parts = [
                f"{name} {code}".strip(),
                clean_display_text(str(item.get("market") or "")),
                clean_display_text(str(item.get("stance") or "")),
                clean_display_text(str(item.get("why") or "")),
            ]
            if action:
                parts.append(f"申购动作：{action}")
            lines.append("- " + " | ".join(part for part in parts if part))
    if len(lines) > 2:
        lines.append("")
        return lines
    lines.append("- 今日未抓到 A/H 新股申购窗口或可复盘的新股对象。")
    lines.append("")
    return lines


def brief_top_opportunity_lines(
    top_items: list[dict[str, Any]],
    *,
    rules: dict[str, Any],
    verification_index: dict[str, dict[str, Any]],
) -> list[str]:
    lines = ["## 四、Top Opportunities", ""]
    if not top_items:
        lines.extend(["- 当前没有可展开的对象。", ""])
        return lines
    for idx, item in enumerate(top_items[: int(rules.get("top_opportunities_limit") or 3)], start=1):
        detail_line = evidence_detail_line(item)
        quant_line = company_quant_line(item)
        compact_evidence = "；".join(str(x) for x in (item.get("key_evidence") or [])[:2])
        lines.extend(
            [
                f"### {idx}. {item.get('radar_object_name', '')} | {object_type_label(item.get('radar_object_type'))} | {triage_label(item.get('triage_action'))}",
                "",
                f"- 事件/证据：{event_inline_text(preferred_events(item, limit=2)) or compact_evidence or item.get('why_now', '')}",
                *([f"- 市场反应：{detail_line}"] if detail_line else []),
                *([f"- 量化侧车：{quant_line}"] if quant_line else []),
                f"- 今日动作：{display_followup_text(item, rules=rules)}",
                f"- 主要缺口：{item.get('confirmation_gap', '') or item.get('failure_mode', '')}",
                "",
            ]
        )
    return lines


def fresh_or_non_company_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("radar_object_type") or "") != "company":
            filtered.append(item)
            continue
        context = item.get("price_context") or {}
        if context and int(context.get("lag_days") or 0) == 0:
            filtered.append(item)
    return filtered


def hidden_codex_footer(*, title: str, report_date: str, run_id: str) -> list[str]:
    return [
        "",
        "<!--",
        "codex_output: true",
        'codex_output_category: "radar_daily_report"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "{title}"',
        f'codex_output_report_date: "{report_date}"',
        f'codex_output_run_id: "{run_id}"',
        "-->",
    ]


def kimi_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("name") or "").strip(): item for item in rows if isinstance(item, dict) and str(item.get("name") or "").strip()}


def compact_text_list(value: Any, *, limit: int = 2) -> list[str]:
    if isinstance(value, list):
        return [clean_display_text(str(item or "")) for item in value if clean_display_text(str(item or ""))][:limit]
    text = clean_display_text(str(value or ""))
    return [text] if text else []


def verification_only_action(text: str) -> bool:
    cleaned = clean_display_text(text)
    if not cleaned:
        return False
    return any(
        token in cleaned
        for token in (
            "先核实",
            "今日核实",
            "核实事件真实性",
            "核实“",
            "确认是否真实",
            "补具体公告原文",
            "必须补具体公告原文",
            "确认是监管问询",
            "定位具体监管函内容",
            "定位具体",
        )
    )


def remove_verification_prefix(text: str) -> str:
    cleaned = clean_display_text(text)
    if not cleaned:
        return ""
    for token in ("同步扫描", "扫描", "再看", "继续看", "对比", "补看"):
        if token in cleaned:
            return cleaned[cleaned.index(token) :].strip(" ，；,;")
    return cleaned


def normalize_system_action_text(text: str) -> str:
    cleaned = clean_display_text(text)
    if not cleaned:
        return ""
    if "下一个交易日优先补齐" in cleaned:
        return cleaned.replace("下一个交易日优先补齐", "系统自动动作：下一次生产运行自动刷新", 1)
    if cleaned.startswith("先补 ") or cleaned.startswith("先补齐 "):
        return "系统自动动作：下一次生产运行" + cleaned[1:]
    return cleaned


def fallback_verified_facts(item: dict[str, Any], *, rules: dict[str, Any], limit: int = 2) -> list[str]:
    facts: list[str] = []
    for event in preferred_events(item, limit=limit):
        text = event_display_text(event)
        if text:
            facts.append(text)
    if facts:
        return facts[:limit]
    evidence = [clean_display_text(str(x or "")) for x in (item.get("key_evidence") or []) if clean_display_text(str(x or ""))]
    return [
        text
        for text in evidence
        if not text.startswith(("所属方向：", "股票代码：", "收盘反应：", "情绪侧车："))
    ][:limit]


def kimi_research_maps(kimi_editorial: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    focus_by_name = kimi_index([item for item in (kimi_editorial.get("focus_actions") or []) if isinstance(item, dict)])
    research_by_name = kimi_index([item for item in (kimi_editorial.get("preliminary_research") or []) if isinstance(item, dict)])
    return focus_by_name, research_by_name


def resolved_action_text(
    item: dict[str, Any],
    *,
    rules: dict[str, Any],
    focus: dict[str, Any] | None = None,
    research: dict[str, Any] | None = None,
    verdict: dict[str, Any] | None = None,
) -> str:
    focus = focus or {}
    research = research or {}
    verdict = verdict or {}
    verdict_action = ""
    next_action = verdict.get("next_action")
    if isinstance(next_action, dict):
        when = clean_display_text(str(next_action.get("when") or ""))
        checks = [
            clean_display_text(str(x or ""))
            for x in (next_action.get("check") or [])
            if clean_display_text(str(x or ""))
        ]
        if checks:
            verdict_action = f"{when}：{'；'.join(checks[:3])}" if when else "；".join(checks[:3])
    candidates = [
        verdict_action,
        str(research.get("execution_action") or "").strip(),
        str(focus.get("action") or "").strip(),
        str(research.get("next_check") or "").strip(),
        display_followup_text(item, rules=rules),
    ]
    for candidate in candidates:
        actionable = remove_verification_prefix(candidate)
        actionable = normalize_system_action_text(actionable)
        if actionable and not verification_only_action(actionable):
            return actionable
    industry = resolve_item_industry(item)
    if str(item.get("radar_object_type") or "") == "company":
        return f"已完成第一轮事件定位；今天看成交承接、价格是否延续，以及{industry or '同方向'}标的是否扩散"
    return candidates[-1] if candidates else "继续观察下一轮变化"


def key_event_line(
    item: dict[str, Any],
    *,
    rules: dict[str, Any],
    verification_index: dict[str, dict[str, Any]],
    kimi_editorial: dict[str, Any],
    kimi_research: dict[str, Any],
) -> str:
    name = str(item.get("radar_object_name") or "")
    object_type = object_type_label(item.get("radar_object_type"))
    event_text = event_or_evidence_text(item, rules=rules, limit=1)
    event_text = event_text or str(item.get("why_now") or "")
    reaction = evidence_detail_line(item)
    precheck = opportunity_precheck_text(item)
    tracking = tracking_symbols_text(item) if str(item.get("radar_object_type") or "") == "industry" else ""
    focus_by_name, research_by_name = kimi_research_maps(kimi_editorial)
    verdict = kimi_research_verdict(item, kimi_research)
    action = resolved_action_text(item, rules=rules, focus=focus_by_name.get(name, {}), research=research_by_name.get(name, {}), verdict=verdict)
    parts = [f"{object_type}｜{name}：{event_text}"]
    if reaction:
        parts.append(reaction)
    if precheck:
        parts.append(precheck)
    if tracking and tracking not in action:
        parts.append(tracking)
    if action:
        parts.append(f"动作：{action}")
    return "- " + " | ".join(part.replace("\n", " ") for part in parts if part)


def tracking_target_text(item: dict[str, Any]) -> str:
    primary = [clean_display_text(str(x or "")) for x in (item.get("primary_symbols") or []) if clean_display_text(str(x or ""))]
    etfs = [clean_display_text(str(x or "")) for x in (item.get("etf_proxies") or []) if clean_display_text(str(x or ""))]
    targets = [*primary[:2], *etfs[:1]]
    return "、".join(targets)


def key_event_impact_profile(item: dict[str, Any], event_text: str) -> dict[str, str]:
    text = event_text
    full_text = f"{event_text}；{item_event_text(item)}"
    name = clean_display_text(str(item.get("radar_object_name") or ""))
    targets = tracking_target_text(item)
    object_type = str(item.get("radar_object_type") or "")
    if "光力科技" in text and "半导体设备" in text:
        return {
            "direction": "直接偏多光力科技；二阶偏多国产半导体后道/封测设备；三阶才看半导体设备ETF",
            "targets": "光力科技300480、长川科技、华峰测控、华海清科；ETF/权重观察：北方华创、中微公司、华峰测控",
            "logic": "光力科技订单对应切割划片/减薄等后道封测精密加工设备；它对北方华创、中微公司不是同产品线传导，而是作为国产设备订单周期的早期温度计，只有更多设备公司订单/交付共振时才扩散到ETF。",
            "elasticity": "光力科技/后道设备约+3%-8%；半导体设备ETF约+1%-3%；北方华创/中微只看设备周期beta。",
        }
    if "光模块" in text or "1.6T" in text or "Cage" in text:
        return {
            "direction": "偏多光模块/高速连接订单链",
            "targets": f"华工科技、奕东电子、光模块ETF；现有代理：{targets}" if targets else "华工科技、奕东电子、光模块ETF",
            "logic": "光模块订单或 Cage 产能紧张先验证 AI 算力链需求，再看产能瓶颈能否转成收入确认和同链条扩单。",
            "elasticity": "订单确认股约+3%-8%；相关ETF约+1%-3%，需客户和交付节奏确认。",
        }
    if ("黄金" in text or "贵金属" in text) and any(token in text for token in COMMODITY_PRICE_DOWN_KEYWORDS):
        return {
            "direction": "偏空黄金/贵金属链；不把全有色直接当多头机会",
            "targets": f"黄金ETF、山东黄金、中金黄金、赤峰黄金；现有代理：{targets}" if targets else "黄金ETF、山东黄金、中金黄金、赤峰黄金",
            "logic": "金价下行先压缩黄金股和黄金ETF的价格弹性；除非随后出现政策、实际利率或美元方向反转，否则只能做风险/承接观察。",
            "elasticity": "黄金ETF约-0.5%-1.5%；高弹性金股约-2%-4%，以金价企稳为反证。",
        }
    if "黄金" in text or "贵金属" in text:
        return {
            "direction": "偏多黄金/贵金属链；不直接等同全有色",
            "targets": f"黄金ETF、山东黄金、中金黄金、赤峰黄金；现有代理：{targets}" if targets else "黄金ETF、山东黄金、中金黄金、赤峰黄金",
            "logic": "进出口许可摩擦下降，贸易/加工周转改善；若叠加金价上行，资源股和黄金ETF弹性更清晰。",
            "elasticity": "黄金ETF约+0.5%-1.5%；高弹性金股约+2%-5%，需金价确认。",
        }
    if "半导体" in text or ("订单" in text and ("电子" in name or "科技" in text)):
        return {
            "direction": "偏多半导体设备/材料订单链",
            "targets": f"半导体设备ETF、北方华创、中微公司、华峰测控；现有代理：{targets}" if targets else "半导体设备ETF、北方华创、中微公司、华峰测控",
            "logic": "订单改善先验证单家公司收入可见度；若封测、前道、量测等多家公司同时出现订单/交付改善，才说明设备资本开支周期扩散。",
            "elasticity": "强订单确认股约+3%-8%；ETF约+1%-3%，以放量为前提。",
        }
    if "OPEC" in text or "产量配额" in text or "增产" in text:
        return {
            "direction": "偏空上游油价弹性；偏多炼化/航空成本端",
            "targets": f"油气ETF、炼化链、航空运输；现有代理：{targets}" if targets else "油气ETF、炼化链、航空运输",
            "logic": "供应预期增加压低油价风险溢价，上游资源股承压；下游成本敏感行业边际受益。",
            "elasticity": "上游油气约-1%-3%；炼化/航空相对收益约+1%-2%，看油价确认。",
        }
    if "原油油轮" in text or ("伊朗" in text and "油轮" in text):
        return {
            "direction": "方向不单边：油轮离港增加供给线索，同时保留地缘风险溢价",
            "targets": f"Brent/WTI、油运、炼化链；现有代理：{targets}" if targets else "Brent/WTI、油运、炼化链",
            "logic": "若离港意味着供给增加，上游油价弹性反而承压；若市场解读为制裁/通行风险升温，才会推升油运和风险溢价，必须用油价与油运股同向确认。",
            "elasticity": "不设单边涨幅；油价与油运股同向放量后再给+1%-3%跟踪弹性。",
        }
    if "霍尔木兹" in text or "航运" in text:
        return {
            "direction": "事件升温偏多油运/能源运输，缓和则降温",
            "targets": targets or "招商轮船、物流/航运ETF",
            "logic": "海峡通行和风险溢价影响运价、保险成本与能源运输预期；核心看船舶通行和油价同步。",
            "elasticity": "风险升温时油运股约+2%-5%；若通行恢复则回吐。",
        }
    if "ST" in text or "风险警示" in text or "退市" in text:
        return {
            "direction": "看空/风险回避",
            "targets": targets or name,
            "logic": "风险警示会抬高折价率、压缩流动性，事件驱动资金通常先规避。",
            "elasticity": "风险延续约-5%-10%；只做风险复核。",
        }
    if object_type == "company" and any(keyword in full_text for keyword in EARNINGS_EVENT_KEYWORDS):
        context = item.get("price_context") or {}
        daily_return = optional_float(context.get("daily_return")) if isinstance(context, dict) else None
        reaction = "尚未被价格明显兑现" if daily_return is None or daily_return < 0.02 else "已有部分价格反应"
        return {
            "direction": f"偏多 {name}，看同板块扩散",
            "targets": targets or name,
            "logic": f"利润/收入超预期先抬升盈利预测；{reaction}，下一步看放量承接和同行跟涨。",
            "elasticity": "首日确认约+2%-5%；若已定价则只看+1%-2%承接。",
        }
    if object_type == "industry":
        return {
            "direction": f"偏多 {name} 代表股/ETF",
            "targets": targets or name,
            "logic": "行业热度与结构化事件共振，先看代表股是否跑赢，再看 ETF 和同链条扩散。",
            "elasticity": "代表股约+2%-4%；ETF约+0.8%-2%，需量能确认。",
        }
    return {
        "direction": f"偏多 {name}" if object_type == "company" else "观察",
        "targets": targets or name,
        "logic": f"{name} 当前只有单点事件，影响先限定在{targets or name}；只有出现放量承接和同链条对象同步跑赢，才可升级为板块线索。",
        "elasticity": "单点对象约+1%-3%；扩散确认后再按代表股/ETF重新估算。",
    }


def key_event_table_lines(
    rows: list[dict[str, Any]],
    *,
    rules: dict[str, Any],
    verification_index: dict[str, dict[str, Any]],
    kimi_editorial: dict[str, Any],
    kimi_research: dict[str, Any],
    all_items: list[dict[str, Any]] | None = None,
) -> list[str]:
    if not rows:
        return ["- 今日没有足够清晰的关键事件。"]
    focus_by_name, research_by_name = kimi_research_maps(kimi_editorial)
    lines = [
        "> 弹性是假设区间，不是收益预测；只有价格、量能和同链条扩散确认后才上调权重。",
        "",
        "| 对象 | 事件 | 影响方向 / 标的 | 逻辑传导 | 弹性假设 | 今日动作 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in rows:
        name = str(item.get("radar_object_name") or "")
        object_type = object_type_label(item.get("radar_object_type"))
        event_text = event_or_evidence_text(item, rules=rules, limit=1) or str(item.get("why_now") or "")
        if str(item.get("radar_object_type") or "") == "industry" and "行业状态" in event_text and "总分" in event_text:
            continue
        profile = key_event_impact_profile(item, event_text)
        absorbed = absorbed_promoted_companies(item, all_items or [], kimi_research)
        verdict = kimi_research_verdict(item, kimi_research)
        action = resolved_action_text(
            item,
            rules=rules,
            focus=focus_by_name.get(name, {}),
            research=research_by_name.get(name, {}),
            verdict=verdict,
        )
        if "光力科技" in event_text and "半导体设备" in event_text:
            action = "1-3个交易日：光力科技300480量价承接；半导体设备ETF净值/成交额；北方华创、中微公司、华峰测控是否同步跑赢"
        elif absorbed:
            action = absorbed_company_action_text(action, absorbed, item)
        direction = profile["direction"]
        if profile.get("targets"):
            direction = f"{direction}；{profile['targets']}"
        absorbed_tracking = absorbed_company_tracking_text(absorbed)
        if absorbed_tracking:
            direction = merge_semicolon_parts(direction, absorbed_tracking)
        lines.append(
            "| {obj} | {event} | {direction} | {logic} | {elasticity} | {action} |".format(
                obj=md_cell(f"{object_type}｜{name}", limit=50),
                event=md_cell(event_text, limit=100),
                direction=md_cell(direction, limit=120),
                logic=md_cell(profile.get("logic"), limit=150),
                elasticity=md_cell(profile.get("elasticity"), limit=90),
                action=md_cell(action, limit=130),
            )
        )
    return lines


def key_event_items(
    *,
    top_items: list[dict[str, Any]],
    hard_board: list[dict[str, Any]],
    soft_watchlist: list[dict[str, Any]],
    risk_monitor: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in (top_items, hard_board, soft_watchlist, risk_monitor):
        for item in group:
            key = str(item.get("radar_object_id") or item.get("radar_object_name") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(item)
            if len(rows) >= limit:
                return rows
    return rows


def campaign_lines(
    top_items: list[dict[str, Any]],
    *,
    rules: dict[str, Any],
    kimi_editorial: dict[str, Any],
    verification_index: dict[str, dict[str, Any]],
    kimi_research: dict[str, Any],
    all_items: list[dict[str, Any]] | None = None,
) -> list[str]:
    focus_by_name, research_by_name = kimi_research_maps(kimi_editorial)
    lines = ["## 二、三条作战线", ""]
    if not top_items:
        lines.extend(["- 今日没有进入作战线的对象。", ""])
        return lines
    for idx, item in enumerate(top_items[: int(rules.get("top_opportunities_limit") or 3)], start=1):
        name = str(item.get("radar_object_name") or "")
        focus = focus_by_name.get(name, {})
        research = research_by_name.get(name, {})
        verdict = kimi_research_verdict(item, kimi_research)
        event_text = event_or_evidence_text(item, rules=rules, limit=2)
        reaction = evidence_detail_line(item)
        precheck = opportunity_precheck_text(item)
        tracking = tracking_symbols_text(item) if str(item.get("radar_object_type") or "") == "industry" else ""
        absorbed = absorbed_promoted_companies(item, all_items or [], kimi_research)
        absorbed_tracking = absorbed_company_tracking_text(absorbed)
        if absorbed_tracking:
            tracking = merge_semicolon_parts(absorbed_tracking, tracking)
        if "光力科技" in event_text and "半导体设备" in event_text:
            tracking = (
                "直接：光力科技300480；同类/相邻设备：长川科技、华峰测控、华海清科；"
                "ETF/权重观察：北方华创、中微公司、华峰测控；"
                "原系统代理待降权：水晶光电 002273、消费电子ETF华夏 159732"
            )
        kimi_read = str(research.get("early_read") or focus.get("why") or "").strip()
        setup = str(research.get("setup") or "").strip()
        verified_facts = compact_text_list(research.get("verified_facts"), limit=2)
        if not verified_facts:
            verified_facts = compact_text_list(focus.get("verified_facts"), limit=2)
        if not verified_facts:
            verified_facts = fallback_verified_facts(item, rules=rules, limit=2)
        for company, _verdict in absorbed:
            fact = event_or_evidence_text(company, rules=rules, limit=1) or str(company.get("why_now") or "")
            if fact and all(fact not in existing for existing in verified_facts):
                verified_facts.append(fact)
        unresolved_gaps = compact_text_list(research.get("unresolved_gaps"), limit=2)
        action = resolved_action_text(item, rules=rules, focus=focus, research=research, verdict=verdict)
        if "光力科技" in event_text and "半导体设备" in event_text:
            action = "1-3个交易日：光力科技300480量价承接；半导体设备ETF净值/成交额；北方华创、中微公司、华峰测控是否同步跑赢"
        elif absorbed:
            action = absorbed_company_action_text(action, absorbed, item)
        harness_reason = clean_display_text(str(verdict.get("reason") or ""))
        if "光力科技" in event_text and "半导体设备" in event_text:
            harness_reason = "光力科技订单是后道封测设备的直接信号，ETF层面只作为国产设备资本开支扩散验证"
        elif absorbed:
            absorbed_names = "、".join(primary_symbol_text(company) for company, _verdict in absorbed)
            suffix = f"Kimi 同时 promote {absorbed_names}，已折叠为{name}主线的公司触发点"
            harness_reason = merge_semicolon_parts(harness_reason, suffix)
        risk = str(focus.get("risk") or item.get("failure_mode") or item.get("confirmation_gap") or "").strip()
        next_check = str(research.get("execution_action") or research.get("next_check") or "").strip()
        remaining = "；".join(unresolved_gaps) or risk
        if next_check and next_check != action and not verification_only_action(next_check):
            remaining = f"{remaining}；{next_check}" if remaining else next_check
        lines.extend(
            [
                f"### {idx}. {name}",
                "",
                f"- 关键事件：{event_text or item.get('why_now', '')}",
                *([f"- 市场反应：{reaction}"] if reaction else []),
                *([f"- 机会预检：{precheck}"] if precheck else []),
                *([f"- 跟踪标的：{tracking}"] if tracking and tracking not in action else []),
                *([f"- 已核事实：{'；'.join(verified_facts)}"] if verified_facts else []),
                f"- 初步判断：{harness_reason or (('机会雏形：' + setup + '；') if setup else '') + (kimi_read or item.get('why_now', ''))}",
                f"- 建议动作：{action}",
                f"- 最大风险 / 待验证：{remaining}",
                "",
            ]
        )
    return lines


def structural_signal_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    categories = set(str(x) for x in (item.get("categories") or []))
    concrete = 0
    if "订单/需求验证" in categories:
        concrete -= 4
    if "产能/资本开支" in categories:
        concrete -= 3
    if "资本动作/治理" in categories:
        concrete -= 2
    if str(item.get("object_type") or "") == "company":
        concrete -= 1
    evidence_count = len([x for x in (item.get("evidence") or []) if str(x).strip()])
    return (concrete, -int(item.get("structural_score") or 0), -evidence_count, str(item.get("name") or ""))


def structural_direction_text(item: dict[str, Any]) -> str:
    categories = set(str(x) for x in (item.get("categories") or []))
    name = clean_display_text(str(item.get("name") or ""))
    evidence_text = "；".join(clean_display_text(str(x or "")) for x in (item.get("evidence") or []))
    if {"订单/需求验证", "产能/资本开支"} <= categories:
        return f"{name} 所在链条可能从单点订单走向产能/收入确认，优先看 1-2 个季度订单金额、客户质量和交付节奏"
    if {"订单/需求验证", "基本面质量"} <= categories:
        return f"{name} 可能形成景气兑现型线索，优先看订单交付、毛利率和同链条扩散"
    if "资本动作/治理" in categories:
        return f"{name} 的并购/重组线索可能升级为资本动作主线，优先看交易结构、审批节奏和产业协同"
    if "全球化/国产替代" in categories:
        return f"{name} 可能受益于全球供给链重排，优先看外需、运价/订单和代表股扩散"
    if "政策/标准/牌照" in categories and "基本面质量" in categories:
        return f"{name} 同时出现政策与业绩改善征兆，优先筛选能把政策转成利润的细分方向"
    if "AI" in evidence_text or "光模块" in evidence_text or "CPO" in evidence_text:
        return f"{name} 可能沿 AI 算力硬件链扩散，优先看订单、产能和价格是否共同验证"
    return clean_display_text(str(item.get("thesis") or ""))


def kimi_structural_verdict_maps(kimi_research: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    if str(kimi_research.get("status") or "") != "pass":
        return by_id, by_name
    for item in kimi_research.get("structural_verdicts") or []:
        if not isinstance(item, dict):
            continue
        object_id = str(item.get("object_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if object_id:
            by_id[object_id] = item
        if name:
            by_name[name] = item
    return by_id, by_name


def structural_signal_verdict(item: dict[str, Any], kimi_research: dict[str, Any]) -> dict[str, Any]:
    by_id, by_name = kimi_structural_verdict_maps(kimi_research)
    object_id = str(item.get("radar_object_id") or item.get("candidate_id") or "").strip()
    name = str(item.get("name") or item.get("radar_object_name") or "").strip()
    return by_id.get(object_id) or by_name.get(name) or {}


def structural_transmission_text(verdict: dict[str, Any]) -> str:
    chain = verdict.get("transmission_chain")
    if isinstance(chain, list):
        return "；".join(clean_display_text(str(x or "")) for x in chain[:2] if clean_display_text(str(x or "")))
    return clean_display_text(str(chain or ""))


def structural_action_text(item: dict[str, Any], verdict: dict[str, Any]) -> str:
    action = verdict.get("next_action") if isinstance(verdict.get("next_action"), dict) else {}
    checks = [clean_display_text(str(x or "")) for x in (action.get("check") or []) if clean_display_text(str(x or ""))]
    if checks:
        return "；".join(checks[:3])
    return clean_display_text(str(item.get("next_research_action") or ""))


def structural_signal_lines(structural_signal: dict[str, Any], *, kimi_research: dict[str, Any] | None = None, limit: int = 3) -> list[str]:
    lines = ["## 三、中长期隐性线索", ""]
    kimi_research = kimi_research or {}
    rows = [item for item in (structural_signal.get("items") or []) if isinstance(item, dict)]
    if not rows:
        lines.extend(["- 当前没有达到阈值的 30-120 天结构性线索。", ""])
        return lines
    rows = sorted(rows, key=structural_signal_sort_key)
    for item in rows[:limit]:
        verdict = structural_signal_verdict(item, kimi_research)
        evidence = [clean_display_text(str(x or "")) for x in (item.get("evidence") or []) if clean_display_text(str(x or ""))]
        direction = (
            clean_display_text(str(verdict.get("structural_thesis") or ""))
            or clean_display_text(str(verdict.get("reason") or ""))
            or structural_direction_text(item)
        )
        transmission = structural_transmission_text(verdict)
        if transmission:
            direction = f"{direction}；传导={transmission}" if direction else f"传导={transmission}"
        tracking = tracking_symbols_text(item)
        risk = clean_display_text(str(item.get("risk_or_disconfirming_evidence") or ""))
        action = structural_action_text(item, verdict)
        line = (
            "- {name}：征兆={signs}；中期方向={direction}；窗口={window}；验证={action}{tracking}{risk}"
        ).format(
            name=clean_display_text(str(item.get("name") or "")),
            signs="；".join(evidence[:2]) or "待补第二来源",
            direction=direction or clean_display_text(str(item.get("thesis") or "")) or "结构性线索",
            window=clean_display_text(str(item.get("watch_window") or "30-120d")),
            action=action,
            tracking=f"；跟踪={tracking}" if tracking else "",
            risk=f"；反证={risk}" if risk else "",
        )
        lines.append(line)
    lines.append("")
    return lines


def ipo_section_lines(ipo_watchlist: dict[str, Any], hk_ipo_watchlist: dict[str, Any], kimi_editorial: dict[str, Any]) -> list[str]:
    lines = ["## 四、IPO / 打新申购窗口", ""]
    body = ipo_watchlist_lines(ipo_watchlist, hk_ipo_watchlist, kimi_editorial)
    return lines + body[2:] if body[:2] == ["### A/H IPO 打新申购初筛", ""] else lines + body


def backend_status_lines(
    *,
    triage_counts: Counter[str],
    source_readiness: list[str],
    price_freshness: list[str],
    news_verification: list[str],
    inventory: dict[str, Any],
    freshness_lag: Any,
    expected_date: str,
) -> list[str]:
    immediate_count = int(triage_counts.get("immediate_research") or 0)
    thesis_count = int(triage_counts.get("thesis_watch") or 0)
    risk_count = int(triage_counts.get("risk_review") or 0)
    lines = ["## 五、后台状态", ""]
    lines.append(f"- 当前研究分流为 `Immediate Research {immediate_count} / Thesis Watch {thesis_count} / Risk Review {risk_count}`。")
    if freshness_lag == 0:
        lines.append(f"- 市场样本已对齐期望样本 `{expected_date}`。")
    lines.extend(source_readiness)
    lines.extend(price_freshness)
    lines.extend(news_verification)
    inv_line = inventory_summary_line(inventory)
    if inv_line:
        lines.append(f"- {inv_line}")
    lines.append("")
    return lines


def render_morning_brief_v2(
    *,
    snapshot: dict[str, Any],
    rules: dict[str, Any],
    inventory: dict[str, Any],
    kimi_editorial: dict[str, Any],
    kimi_research: dict[str, Any],
    price_freshness: dict[str, Any],
    news_verification: dict[str, Any],
    ipo_watchlist: dict[str, Any],
    hk_ipo_watchlist: dict[str, Any],
    structural_signal: dict[str, Any],
    top_items: list[dict[str, Any]],
    hard_board: list[dict[str, Any]],
    soft_watchlist: list[dict[str, Any]],
    risk_monitor: list[dict[str, Any]],
    triage_counts: Counter[str],
    report_date: str,
    market_sample_date: str,
    run_id: str,
    freshness_lag: Any,
    expected_date: str,
    verification_index: dict[str, dict[str, Any]],
) -> str:
    focus = " / ".join(str(item.get("radar_object_name") or "") for item in top_items[:3])
    title = f"Radar 事件作战单 {report_date or market_sample_date}"
    ipo_target_date = str(hk_ipo_watchlist.get("target_date") or ipo_watchlist.get("target_date") or "").strip()
    date_parts = [
        f"事件窗口 {report_date}" if report_date else "",
        f"市场样本 {market_sample_date}" if market_sample_date else "",
        f"IPO申购日 {ipo_target_date}" if ipo_target_date and ipo_target_date != report_date else "",
    ]
    date_line = " | ".join(part for part in date_parts if part)
    lines: list[str] = [
        "# Radar 事件作战单",
        "",
        f"{date_line or (report_date or market_sample_date)} | 主线：{focus or '无'}",
        "",
        "## 一、今日关键事件",
        "",
    ]
    key_rows = key_event_items(
        top_items=top_items,
        hard_board=fresh_or_non_company_items(hard_board),
        soft_watchlist=fresh_or_non_company_items(soft_watchlist),
        risk_monitor=fresh_or_non_company_items(risk_monitor),
        limit=int(rules.get("key_events_limit") or 8),
    )
    lines.extend(
        key_event_table_lines(
            key_rows,
            rules=rules,
            verification_index=verification_index,
            kimi_editorial=kimi_editorial,
            kimi_research=kimi_research,
            all_items=[item for item in (snapshot.get("objects") or []) if isinstance(item, dict)],
        )
    )
    lines.append("")
    lines.extend(
        campaign_lines(
            top_items,
            rules=rules,
            kimi_editorial=kimi_editorial,
            verification_index=verification_index,
            kimi_research=kimi_research,
            all_items=[item for item in (snapshot.get("objects") or []) if isinstance(item, dict)],
        )
    )
    lines.extend(
        structural_signal_lines(
            structural_signal,
            kimi_research=kimi_research,
            limit=int(rules.get("structural_signal_limit") or 3),
        )
    )
    lines.extend(ipo_section_lines(ipo_watchlist, hk_ipo_watchlist, kimi_editorial))
    lines.extend(
        backend_status_lines(
            triage_counts=triage_counts,
            source_readiness=source_readiness_summary_lines(),
            price_freshness=price_freshness_summary_lines(price_freshness),
            news_verification=news_verification_summary_lines(news_verification),
            inventory=inventory,
            freshness_lag=freshness_lag,
            expected_date=expected_date,
        )
    )
    lines.extend(hidden_codex_footer(title=title, report_date=report_date or market_sample_date, run_id=run_id))
    return "\n".join(lines) + "\n"


def render_report(
    snapshot: dict[str, Any],
    *,
    rules: dict[str, Any],
    inventory: dict[str, Any],
    handoff: dict[str, Any],
    kimi_editorial: dict[str, Any],
    kimi_research: dict[str, Any],
    price_freshness: dict[str, Any],
    news_verification: dict[str, Any],
    ipo_watchlist: dict[str, Any],
    hk_ipo_watchlist: dict[str, Any],
    structural_signal: dict[str, Any],
) -> str:
    summary = snapshot.get("summary") or {}
    grouped = bucket_objects(snapshot)
    top_items = top_opportunities(snapshot, rules=rules, kimi_research=kimi_research)
    hard_board = board_items(
        snapshot,
        "hard_catalyst_board",
        limit=int(rules.get("hard_catalyst_board_limit") or 8),
        include_triage={"immediate_research", "thesis_watch"},
    )
    soft_watchlist = board_items(
        snapshot,
        "soft_catalyst_watchlist",
        limit=int(rules.get("soft_catalyst_watchlist_limit") or 8),
        include_triage={"thesis_watch"},
        exclude_triage={"background_only"},
    )
    risk_monitor = board_items(
        snapshot,
        "risk_monitor",
        limit=int(rules.get("risk_monitor_limit") or 5),
        include_triage={"risk_review"},
    )
    triage_limit = int(rules.get("triage_board_limit") or 8)
    triage_board = {action: triage_items(snapshot, action, limit=triage_limit) for action, _ in TRIAGE_TITLES}
    handoff_queues = handoff.get("queues") or {}
    triage_counts = (
        Counter({name: len(items) for name, items in handoff_queues.items() if isinstance(items, list)})
        if isinstance(handoff_queues, dict) and handoff_queues
        else triage_queue_counts(snapshot)
    )
    bark_items = [item for item in snapshot.get("objects") or [] if isinstance(item, dict) and item.get("trigger_state") != "report_only"]
    report_date = str(snapshot.get("event_window_end_date") or snapshot.get("report_date") or snapshot.get("as_of_date") or "")
    market_sample_date = str(snapshot.get("market_sample_date") or snapshot.get("as_of_date") or "")
    run_id = str(snapshot.get("radar_run_id") or "")
    generated_at = format_timestamp_label(snapshot.get("generated_at"))
    generated_date = generated_at[:10] if generated_at else ""
    freshness = summarize_snapshot_freshness(snapshot)
    age_days = freshness.get("sample_age_days")
    expected_date = str(freshness.get("expected_sample_date") or "")
    freshness_lag = freshness.get("freshness_lag_days")
    verification_index = news_verification_index(news_verification)
    if str(rules.get("report_style") or "").strip() == "morning_brief_v2":
        return render_morning_brief_v2(
            snapshot=snapshot,
            rules=rules,
            inventory=inventory,
            kimi_editorial=kimi_editorial,
            kimi_research=kimi_research,
            price_freshness=price_freshness,
            news_verification=news_verification,
            ipo_watchlist=ipo_watchlist,
            hk_ipo_watchlist=hk_ipo_watchlist,
            structural_signal=structural_signal,
            top_items=top_items,
            hard_board=hard_board,
            soft_watchlist=soft_watchlist,
            risk_monitor=risk_monitor,
            triage_counts=triage_counts,
            report_date=report_date,
            market_sample_date=market_sample_date,
            run_id=run_id,
            freshness_lag=freshness_lag,
            expected_date=expected_date,
            verification_index=verification_index,
        )
    lines: list[str] = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_daily_report"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar 投资机会日报 {report_date or market_sample_date} 事件窗口"',
        "---",
        "",
        "# Radar 投资机会日报",
        "",
        (
            f"事件窗口截至 {report_date} | 市场样本日期 {market_sample_date} | 本地生成 {generated_at or 'unknown'} | "
            f"运行批次 {run_id} | 市场时区 {snapshot.get('market_tz', 'Asia/Shanghai')}"
            if report_date and market_sample_date and report_date != market_sample_date
            else f"市场样本日期 {market_sample_date or report_date} | 本地生成 {generated_at or 'unknown'} | 运行批次 {run_id} | 市场时区 {snapshot.get('market_tz', 'Asia/Shanghai')}"
        ),
        "",
        "## 一、顶部摘要",
        "",
        "- 本轮共生成 `{candidate}` 个 Radar 候选，其中 `strong_alert {strong_alert}` 个，`strong_candidate {strong_candidate}` 个，`research_candidate {research_candidate}` 个，`observe {observe}` 个。".format(
            candidate=int(summary.get("candidate_count") or 0),
            strong_alert=int(summary.get("strong_alert_count") or 0),
            strong_candidate=int(summary.get("strong_candidate_count") or 0),
            research_candidate=int(summary.get("research_candidate_count") or 0),
            observe=int(summary.get("observe_count") or 0),
        ),
        f"- 当日实际触发 Bark `{int(summary.get('bark_trigger_count') or 0)}` 条。",
        f"- {object_mix_summary(snapshot)}",
    ]
    immediate_count = int(triage_counts.get("immediate_research") or 0)
    thesis_count = int(triage_counts.get("thesis_watch") or 0)
    risk_count = int(triage_counts.get("risk_review") or 0)
    lines.append(
        f"- 当前研究分流为 `Immediate Research {immediate_count} / Thesis Watch {thesis_count} / Risk Review {risk_count}`。"
    )
    lines.extend(market_sentiment_summary_lines(snapshot))
    quant_signal_status = str(snapshot.get("quant_signal_status") or "").strip()
    if quant_signal_status:
        quant_objects = sum(
            1
            for item in (snapshot.get("objects") or [])
            if isinstance(item, dict) and str(((item.get("quant_signal_context") or {}).get("status") or "")).strip() == "pass"
        )
        lines.append(f"- 当前量化信号侧车状态为 `{quant_signal_status}`：已有 `{quant_objects}` 个对象挂上 Alpha158 / TimesFM / 情绪研究 bundle。")
    lines.extend(source_readiness_summary_lines())
    lines.extend(price_freshness_summary_lines(price_freshness))
    lines.extend(news_verification_summary_lines(news_verification))
    inv_summary_line = inventory_summary_line(inventory)
    if inv_summary_line:
        lines.append(f"- {inv_summary_line}")
    if top_items:
        focus_count = int(rules.get("summary_focus_count") or 3)
        focus = " / ".join(str(item.get("radar_object_name") or "") for item in top_items[:focus_count])
        lines.append(f"- 当前最值得优先研究的方向集中在 `{focus}`。")
    if report_date and market_sample_date and report_date != market_sample_date:
        lines.append(f"- 当前事件窗口已经推进到 `{report_date}`，但可验证的市场收盘与资金样本仍停在 `{market_sample_date}`。")
    if generated_date and market_sample_date and generated_date != market_sample_date:
        lines.append(f"- 这份日报基于 `{market_sample_date}` 的有效市场样本重建，本地最近一次生成时间为 `{generated_at}`。")
    if freshness_lag is not None:
        if freshness_lag == 0:
            if age_days == 0:
                lines.append("- 当前市场样本与期望样本日期一致。")
            else:
                lines.append(
                    f"- 当前市场样本虽然已跨自然日，但与期望样本日期 `{expected_date}` 仍然一致；按市场日语义 freshness 正常。"
                )
        else:
            lines.append(
                f"- 当前市场样本落后于期望样本日期 `{expected_date}` 已有 `{freshness_lag}` 个市场日"
                f"（sample_age_days={age_days}），后续应优先刷新 live sample 再做 PM 级判断。"
            )
    kimi_lines = kimi_editorial_lines(kimi_editorial)
    if kimi_lines:
        lines.extend(["", *kimi_lines])
    lines.extend(["", *pm_quick_view_lines(top_items, rules=rules, verification_index=verification_index)])
    if str(rules.get("report_style") or "").strip() == "morning_brief":
        lines.extend(["## 二、事件驱动作战板", ""])
        lines.extend(compact_board_lines("Hard Catalysts", fresh_or_non_company_items(hard_board)[: int(rules.get("hard_catalyst_board_limit") or 3)]))
        lines.extend(compact_board_lines("Soft Catalyst Watchlist", fresh_or_non_company_items(soft_watchlist)[: int(rules.get("soft_catalyst_watchlist_limit") or 3)]))
        if risk_monitor:
            lines.extend(compact_board_lines("Risk Monitor", fresh_or_non_company_items(risk_monitor)[: int(rules.get("risk_monitor_limit") or 2)]))
        lines.extend(["## 三、Kimi 初研 / IPO", ""])
        kimi_research = kimi_preliminary_research_lines(kimi_editorial)
        if kimi_research:
            lines.extend(kimi_research)
        elif str(kimi_editorial.get("status") or "") != "pass":
            lines.extend([f"### Kimi 初步研究", "", f"- 本轮未生成 Kimi 初研层：{kimi_editorial.get('status') or 'missing'}。", ""])
        lines.extend(ipo_watchlist_lines(ipo_watchlist, hk_ipo_watchlist, kimi_editorial))
        lines.extend(brief_top_opportunity_lines(top_items, rules=rules, verification_index=verification_index))
        lines.extend(["## 五、明日跟踪", ""])
        watch_names = [str(item.get("radar_object_name") or "") for item in top_items[:3] if str(item.get("radar_object_name") or "")]
        lines.append("- " + ("、".join(watch_names) if watch_names else "无"))
        return "\n".join(lines) + "\n"
    lines.extend(score_method_lines())
    lines.extend(["", "## 二、事件驱动面板", ""])
    lines.extend(compact_board_lines("Hard Catalysts", hard_board))
    lines.extend(compact_board_lines("Soft Catalyst Watchlist", soft_watchlist))
    if risk_monitor:
        lines.extend(compact_board_lines("Risk Monitor", risk_monitor))
    lines.extend(["## 三、研究分流板", ""])
    for action, title in TRIAGE_TITLES:
        if action == "background_only":
            continue
        lines.extend(triage_lines(title, triage_board[action]))
    lines.extend(["## 四、分桶面板", ""])
    for bucket in BUCKETS:
        lines.extend(table_for_bucket(bucket, grouped[bucket], rules=rules))
    lines.extend(["## 五、今日 Top Opportunities", ""])
    if not top_items:
        lines.extend(["- 当前没有可展开的对象。", ""])
    else:
        for idx, item in enumerate(top_items, start=1):
            events = preferred_events(item, limit=3)
            detail_line = evidence_detail_line(item)
            lines.extend(
                [
                    f"### {idx}. {item.get('radar_object_name', '')} | {object_type_label(item.get('radar_object_type'))} | {bucket_label(item.get('radar_bucket'))}",
                    "",
                    f"- 核心事件：{event_inline_text(events)} | 研究动作：{triage_label(item.get('triage_action'))} | 催化：{catalyst_summary(item)}",
                    *([f"- 当日反应：{detail_line}"] if detail_line else []),
                    *([f"- 情绪侧车：{company_sentiment_line(item)}"] if company_sentiment_line(item) and detail_line else []),
                    *([f"- 量化侧车：{company_quant_line(item)}"] if company_quant_line(item) else []),
                    f"- 评分拆解：{score_breakdown_line(item)}",
                    f"- 当前判断：{item.get('why_now', '')}",
                    f"- 关键动作：{display_followup_text(item, rules=rules)}",
                    f"- 关键确认 / 失败路径：{item.get('confirmation_gap', '')} / {item.get('failure_mode', '')}",
                    f"- 关联标的：{'；'.join(str(x) for x in (item.get('primary_symbols') or []) + (item.get('etf_proxies') or []) + (item.get('theme_overlays') or [])) or '无'}",
                    "",
                ]
            )
    lines.extend(["## 六、个股事件线索", ""])
    company_events = company_event_board(snapshot, rules=rules)
    if company_events:
        for subject, industry_name, text in company_events:
            lines.append(f"- {subject} | 所属方向：{industry_name} | 事件：{text}")
    else:
        lines.append("- 当前没有足够清晰的个股事件线索。")
    lines.extend(["", "## 七、商品 / 代理线索", ""])
    proxy_signals = proxy_signal_board(snapshot, rules=rules)
    if proxy_signals:
        for industry_name, signal_text in proxy_signals:
            lines.append(f"- {industry_name}：{signal_text}")
    else:
        lines.append("- 当前没有可展开的商品或代理变量线索。")
    lines.extend(["", "## 八、状态变化", ""])
    added = [item for item in snapshot.get("objects") or [] if isinstance(item, dict) and item.get("is_new")]
    upgraded = [item for item in snapshot.get("objects") or [] if isinstance(item, dict) and item.get("is_upgraded")]
    observe = grouped["observe"][:3]
    if added:
        lines.append("- 新增重点：" + "；".join(f"{item.get('radar_object_name')} 进入 `{item.get('radar_bucket')}`" for item in added[:3]))
    else:
        lines.append("- 今日无新增重点。")
    if upgraded:
        lines.append("- 升级重点：" + "；".join(f"{item.get('radar_object_name')} 从 `{item.get('previous_bucket')}` 升到 `{item.get('radar_bucket')}`" for item in upgraded[:3]))
    else:
        lines.append("- 今日无升级重点。")
    if observe:
        lines.append("- 继续观察：" + "；".join(f"{item.get('radar_object_name')}（{item.get('why_now')}）" for item in observe))
    else:
        lines.append("- 当前无需要补充说明的观察对象。")
    lines.extend(["", "## 九、Bark 事件回顾", ""])
    if bark_items:
        for item in bark_items[:3]:
            lines.append(
                "- {name} | {type} | {alert} | {state} | {why}".format(
                    name=str(item.get("radar_object_name") or ""),
                    type=str(item.get("radar_object_type") or ""),
                    why=top_event_preview(item, rules=rules),
                    alert=str(item.get("alert_level") or ""),
                    state=str(item.get("trigger_state") or ""),
                )
            )
    else:
        lines.append("- 今日无实际 Bark 触发。")
    lines.extend(["", "## 十、附录", ""])
    lines.append(f"- 运行批次：`{run_id}`")
    lines.append(f"- snapshot 版本：`{snapshot.get('ranking_version', 'v1')}`")
    lines.append("- 运行状态机与排序 bucket 分离：`cold / warming / candidate / strong_alert` 不等于 `observe / research_candidate / strong_candidate / strong_alert`")
    lines.append("- 明天需要继续盯的对象：" + ("、".join(str(item.get("radar_object_name") or "") for item in top_items[:3]) if top_items else "无"))
    return "\n".join(lines) + "\n"


def default_dated_output(snapshot: dict[str, Any]) -> Path:
    date_text = str(snapshot.get("as_of_date") or datetime.now(timezone.utc).date().isoformat()).replace("-", "")
    return DEFAULT_OUTPUT_DIR / f"radar_daily_report_{date_text}.md"


def main() -> int:
    args = parse_args()
    snapshot = load_snapshot(args.input_snapshot)
    inventory = load_inventory(args.input_inventory)
    handoff = load_optional_json(args.input_handoff)
    kimi_editorial = load_kimi_editorial(args.input_kimi_editorial)
    kimi_research = load_optional_json(args.input_kimi_research)
    price_freshness = load_optional_json(args.input_price_freshness)
    news_verification = load_optional_json(args.input_news_verification)
    ipo_watchlist = load_optional_json(args.input_ipo_watchlist)
    hk_ipo_watchlist = load_optional_json(args.input_hk_ipo_watchlist)
    structural_signal = load_optional_json(args.input_structural_signal)
    rules = load_rules(args.rules)
    report_text = render_report(
        snapshot,
        rules=rules,
        inventory=inventory,
        handoff=handoff,
        kimi_editorial=kimi_editorial,
        kimi_research=kimi_research,
        price_freshness=price_freshness,
        news_verification=news_verification,
        ipo_watchlist=ipo_watchlist,
        hk_ipo_watchlist=hk_ipo_watchlist,
        structural_signal=structural_signal,
    )
    latest_output = args.latest_output
    dated_output = args.output or default_dated_output(snapshot)
    latest_html_output = args.latest_html_output
    latest_pdf_output = args.latest_pdf_output
    dated_html_output = args.html_output or dated_output.with_suffix(".html")
    dated_pdf_output = args.pdf_output or dated_output.with_suffix(".pdf")
    write_text(latest_output, report_text)
    write_text(dated_output, report_text)
    pdf_generated, render_error = render_html_pdf(
        report_text,
        f"Radar 投资机会日报 {snapshot.get('as_of_date', '')}",
        latest_html_output,
        latest_pdf_output,
    )
    if pdf_generated:
        latest_html_output.parent.mkdir(parents=True, exist_ok=True)
        latest_pdf_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(latest_html_output, dated_html_output)
        shutil.copy2(latest_pdf_output, dated_pdf_output)
    print(
        json.dumps(
            {
                "latest_output": str(latest_output),
                "dated_output": str(dated_output),
                "latest_html_output": str(latest_html_output) if pdf_generated else "",
                "dated_html_output": str(dated_html_output) if pdf_generated else "",
                "latest_pdf_output": str(latest_pdf_output) if pdf_generated else "",
                "dated_pdf_output": str(dated_pdf_output) if pdf_generated else "",
                "pdf_generated": pdf_generated,
                "render_error": render_error or "",
                "candidate_count": int((snapshot.get("summary") or {}).get("candidate_count") or 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if pdf_generated else 1


if __name__ == "__main__":
    raise SystemExit(main())
