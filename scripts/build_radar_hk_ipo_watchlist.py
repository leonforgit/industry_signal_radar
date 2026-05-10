#!/usr/bin/env python3
"""Build a Hong Kong IPO watchlist from the HKEX calendar feed."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
import re
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from radar_freshness_utils import DEFAULT_MARKET_TZ, market_now

try:
    import pandas as pd
except Exception:  # noqa: BLE001
    pd = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
except Exception:  # noqa: BLE001
    BeautifulSoup = None  # type: ignore[assignment]

try:
    from pypdf import PdfReader
except Exception:  # noqa: BLE001
    PdfReader = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_hk_ipo_watchlist_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_hk_ipo_watchlist_latest.md"
DEFAULT_HKEX_ICAL_URL = "https://www.hkex.com.hk/News/HKEX-Calendar/Subscribe-Calendar?sc_lang=en"
AASTOCKS_BASE_URL = "https://www.aastocks.com"
AASTOCKS_IPO_SUMMARY_URL = f"{AASTOCKS_BASE_URL}/en/stocks/market/ipo/upcomingipo/company-summary"
HKEX_NEW_LISTING_MAIN_BOARD_URL = "https://www2.hkexnews.hk/new-listings/new-listing-information/main-board?sc_lang=en"
SL886_IPO_DETAIL_URL = "https://www.sl886.com/ipo/{code}"
HK_IPO_NAME_CN = {
    "shanghaisunmitechnologycoltd": ("上海商米科技集团股份有限公司", "商米科技"),
    "starsportsmedicinecoltd": ("北京天星医疗股份有限公司", "天星医疗"),
    "cofoemedicaltechnologycoltd": ("可孚医疗科技股份有限公司", "可孚医疗"),
    "shenzhenldrobotcoltd": ("深圳乐动机器人股份有限公司", "乐动机器人"),
    "impacttherapeuticsinc": ("南京英派药业股份有限公司", "英派药业-B"),
    "metistechbiocoltd": ("剂泰科技(北京)股份有限公司", "剂泰科技-P"),
    "robotphoenixintelligenttechnologycoltd": ("浙江翼菲智能科技股份有限公司", "翼菲智能"),
}
HK_COMPARABLE_NAME_CN = {
    "MINIMAX-W": "稀宇科技-W",
    "KNOWLEDGE ATLAS": "智谱",
    "LENS": "蓝思科技",
    "INNOVENT BIO": "信达生物",
    "AKESO": "康方生物",
    "SBP GROUP": "中国生物制药",
    "SKB BIO": "科伦博泰生物-B",
    "WUXI XDC": "药明合联",
    "3SBIO": "三生制药",
    "XTALPI": "晶泰控股-P",
    "EXTREME VISION": "极视角科技",
    "ESTUN": "埃斯顿",
    "YUNJI": "云迹",
    "MICROPORT": "微创医疗",
    "WEIGAO GROUP": "威高股份",
    "LIFETECH SCI": "先健科技",
    "GUANZE MEDICAL": "冠泽医疗",
    "AK MEDICAL": "爱康医疗",
    "MICROPORT NEURO": "微创脑科学",
    "XIZHI TECH-P": "希智科技-P",
    "BIREN TECH": "壁仞科技",
    "MANYCORE TECH": "群核科技",
    "MININGLAMP-W": "明略科技-W",
    "51WORLD": "五一视界",
    "HAIZHI TECH GP": "海致科技集团",
    "SUNMI TECH-W": "商米科技-W",
    "MABWELL-B": "迈博药业-B",
    "HUAQIN": "华勤",
}
HK_SPONSOR_NAME_CN = {
    "Goldman Sachs (Asia) L.L.C.": "高盛(亚洲)",
    "Jefferies Hong Kong Limited": "杰富瑞香港",
    "Deutsche Securities Asia Limited": "德意志证券亚洲",
    "Haitong International Capital Limited": "海通国际资本",
    "Guotai Junan Capital Limited": "国泰君安融资",
    "CCB International Capital Limited": "建银国际金融",
    "CITIC Securities (Hong Kong) Limited": "中信证券(香港)",
    "Huatai Financial Holdings (Hong Kong) Limited": "华泰金融控股(香港)",
    "BNP Paribas Securities (Asia) Limited": "法巴证券(亚洲)",
    "China International Capital Corporation Hong Kong Securities Limited": "中金香港证券",
    "BOCOM International (Asia) Limited": "交银国际(亚洲)",
    "CMB International Capital Limited": "招银国际融资",
    "CMBC International Capital Limited": "民银资本",
    "SPDB International Capital Limited": "浦银国际融资",
    "Orient Capital (Hong Kong) Limited": "东方融资(香港)",
    "ABCI Capital Limited": "农银国际融资",
}
NON_EQUITY_LISTING_KEYWORDS = ("ETF", "EXCHANGE TRADED FUND", "COVERED CALL")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default="", help="Target date in YYYY-MM-DD. Defaults to current Asia/Shanghai date.")
    parser.add_argument("--include-upcoming-days", type=int, default=14)
    parser.add_argument("--include-recent-days", type=int, default=0)
    parser.add_argument("--include-closed", dest="include_closed", action="store_true", default=True, help="Include IPOs whose subscription window has already closed.")
    parser.add_argument("--only-actionable", dest="include_closed", action="store_false", help="Only include IPOs whose subscription window is open/upcoming.")
    parser.add_argument("--hkex-ical-url", default=DEFAULT_HKEX_ICAL_URL)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD_OUTPUT)
    return parser.parse_args()


def target_date(raw: str) -> date:
    text = str(raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    return market_now(DEFAULT_MARKET_TZ).date()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def unfold_ical(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw.rstrip())
    return lines


def parse_ical_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    match = re.match(r"(\d{8})T", text)
    if match:
        compact = match.group(1)
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return ""


def parse_detail_date(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    text = text.replace("/", "-")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return ""


def parse_offer_period(value: Any) -> tuple[str, str]:
    text = compact_text(value)
    if not text:
        return "", ""
    dates = re.findall(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", text)
    if len(dates) >= 2:
        return parse_detail_date(dates[0]), parse_detail_date(dates[1])
    if len(dates) == 1:
        single = parse_detail_date(dates[0])
        return single, single
    return "", ""


def canonical_name_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def chinese_name_pair(name: str) -> tuple[str, str]:
    return HK_IPO_NAME_CN.get(canonical_name_key(name), ("", ""))


def is_non_equity_listing(name: str) -> bool:
    upper = compact_text(name).upper()
    return any(keyword in upper for keyword in NON_EQUITY_LISTING_KEYWORDS)


def parse_ical_events(text: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in unfold_ical(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key.split(";", 1)[0]] = value.replace("\\,", ",").replace("\\n", " ").strip()
    return events


def fetch_ical(url: str) -> tuple[str, str]:
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 RadarIPO/1.0"})
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="ignore"), ""
    except URLError as exc:
        return "", f"hkex_calendar_fetch_error={exc}"
    except Exception as exc:  # noqa: BLE001
        return "", f"hkex_calendar_fetch_error={type(exc).__name__}: {exc}"


def compact_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()
    return "" if text in {"nan", "None"} else text


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def safe_float(value: Any) -> float | None:
    text = compact_text(value).replace(",", "").replace("%", "")
    if not text or text in {"-", "N/A"}:
        return None
    try:
        output = float(text)
    except ValueError:
        return None
    if output != output or output in {float("inf"), float("-inf")}:
        return None
    return output


def flatten_html_frame(frame: Any) -> Any:
    flat = frame.copy()
    columns: list[str] = []
    for column in flat.columns:
        if isinstance(column, tuple):
            parts = [
                compact_text(part)
                for part in column
                if compact_text(part) and not compact_text(part).startswith("Unnamed:")
            ]
            columns.append(parts[-1] if parts else "")
        else:
            columns.append(compact_text(column))
    flat.columns = columns
    return flat


def normalize_field_name(value: Any) -> str:
    text = compact_text(value)
    text = re.sub(r"(?<=\D)\d+$", "", text)
    return text.strip()


def frame_to_pairs(frame: Any) -> dict[str, str]:
    flat = flatten_html_frame(frame)
    if flat.empty or flat.shape[1] < 2:
        return {}
    pairs: dict[str, str] = {}
    for row in flat.itertuples(index=False):
        key = normalize_field_name(row[0])
        value = compact_text(row[1])
        if key and value:
            pairs[key] = value
    return pairs


def parse_percent_text(value: Any) -> float | None:
    return safe_float(value)


def median_float(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def parse_usd_millions(value: Any) -> float | None:
    text = compact_text(value).upper().replace("US$", "USD")
    match = re.search(r"USD\s*([0-9]+(?:\.[0-9]+)?)\s*([MBK]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    if unit == "B":
        return number * 1000.0
    if unit == "K":
        return number / 1000.0
    return number


def parse_multiple_text(value: Any) -> float | None:
    text = compact_text(value)
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:倍|x|X)", text)
    if match:
        return safe_float(match.group(1))
    return safe_float(text)


def fetch_url_text(url: str, *, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) RadarIPO/1.1",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def fetch_url_bytes(url: str, *, timeout: int = 45) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) RadarIPO/1.1",
            "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def clean_pdf_text(text: str) -> str:
    output = re.sub(r"/H\d+", " ", str(text or ""))
    output = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", " ", output)
    output = re.sub(r"\s+", " ", output)
    return output.strip()


def extract_between(text: str, starts: tuple[str, ...], stops: tuple[str, ...], *, max_chars: int) -> str:
    lower = text.lower()
    start_idx = -1
    for marker in starts:
        idx = lower.find(marker.lower())
        if idx >= 0 and (start_idx < 0 or idx < start_idx):
            start_idx = idx
    if start_idx < 0:
        return ""
    end_idx = len(text)
    for marker in stops:
        idx = lower.find(marker.lower(), start_idx + 1)
        if idx >= 0:
            end_idx = min(end_idx, idx)
    return clean_pdf_text(text[start_idx:end_idx])[:max_chars]


def rmb_million_text(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return ""
    if number >= 100:
        return f"{number / 100:.2f}亿元"
    return f"{number:.1f}百万元"


def prospectus_business_cn(summary: str) -> str:
    text = compact_text(summary)
    lower = text.lower()
    if "nano" in lower and ("nanoforge" in lower or "functional payloads" in lower or "ai-empowered" in lower):
        return "业务定位：AI 纳米材料与递送技术平台，核心看 NanoForge 平台能否转化为订单、管线里程碑和商业化收入。"
    if "pipeline assets" in lower or "oncology" in lower or "pharmaceutical market" in lower:
        return "业务定位：肿瘤创新药/生物医药研发平台，核心看管线推进、商业化节奏和资金消耗。"
    if "integrated robotics solutions" in lower or "robotics solutions" in lower:
        return "业务定位：工业智能装备/机器人解决方案，核心看大客户订单、项目交付和毛利率稳定性。"
    if "visual perception products" in lower or "robot lawn mowers" in lower:
        return "业务定位：机器人视觉感知产品与机器人割草机，核心看机器人 LiDAR/视觉感知矩阵能否转化为放量产品。"
    if "home care medical devices" in lower:
        return "业务定位：家用医疗器械供应商；招股书披露按 2024 年国内收入排名第二，市占率约 2.1%，行业竞争较充分。"
    if "sports medical implants" in lower:
        return "业务定位：运动医学植入物、手术设备及配套耗材；招股书披露在中国运动医学植入物市场本土品牌排名第一、整体第四。"
    if text and contains_cjk(text):
        return "业务定位：" + text[:180]
    if text:
        return "业务定位：招股书业务摘要已抓取，需继续做中文结构化；当前先按行业空间、订单/管线兑现和现金消耗验证。"
    return ""


def prospectus_financial_cn(text: str) -> str:
    source = clean_pdf_text(text)
    lower = source.lower()
    parts: list[str] = []
    revenue_growth = re.search(
        r"revenue(?:\s+increased)?\s+from\s+RMB([0-9,.]+)\s+million\s+in\s+(\d{4})\s+to\s+RMB([0-9,.]+)\s+million\s+in\s+(\d{4}),\s+and\s+further(?:\s+increased)?\s+to\s+RMB([0-9,.]+)\s+million\s+in\s+(\d{4})(?:,\s+representing\s+a\s+CAGR\s+of\s+approximately\s+([0-9.]+)%)?",
        source,
        flags=re.I,
    )
    if revenue_growth:
        first, year1, second, year2, third, year3, cagr = revenue_growth.groups()
        cagr_text = f"，CAGR 约 {cagr}%" if cagr else ""
        parts.append(f"收入：{year1}-{year3} 从 {rmb_million_text(first)} 增至 {rmb_million_text(second)}、{rmb_million_text(third)}{cagr_text}")
    profit_growth = re.search(
        r"profit\s+for\s+the\s+year\s+increased\s+from\s+RMB([0-9,.]+)\s+million\s+in\s+(\d{4})\s+to\s+RMB([0-9,.]+)\s+million\s+in\s+(\d{4}),\s+and\s+further\s+increased\s+to\s+RMB([0-9,.]+)\s+million\s+in\s+(\d{4})",
        source,
        flags=re.I,
    )
    if profit_growth:
        first, year1, second, _, third, year3 = profit_growth.groups()
        parts.append(f"盈利：{year1}-{year3} 净利润从 {rmb_million_text(first)} 增至 {rmb_million_text(second)}、{rmb_million_text(third)}")
    overseas = re.search(
        r"overseas\s+sales\s+accounted\s+for\s+([0-9.]+)%,\s+([0-9.]+)%\s+and\s+([0-9.]+)%\s+of\s+our\s+total\s+revenue\s+in\s+2023,\s+2024\s+and\s+2025",
        source,
        flags=re.I,
    )
    if overseas:
        parts.append(f"结构：海外收入占比 2023-2025 从 {overseas.group(1)}% 提升至 {overseas.group(3)}%")
    if "net losses" in lower or "net loss" in lower or "net operating cash outflows" in lower:
        parts.append("质量：仍录得净亏损或经营现金流出，盈利拐点未确认")
    if "continuous profitability improvements" in lower or "net cash flows from operating activities" in lower:
        parts.append("质量：招股书披露盈利能力改善，经营现金流为正")
    return "；".join(parts)


def prospectus_risk_cn(risk_text: str, full_text: str) -> str:
    text = compact_text(risk_text) or compact_text(full_text)
    lower = text.lower()
    parts: list[str] = []
    if "net losses" in lower or "net loss" in lower or "net operating cash outflows" in lower:
        parts.append("仍亏损或经营现金流为负，盈利兑现需要继续验证")
    if "highly competitive" in lower or "competitive" in lower:
        parts.append("行业竞争强，若产品/渠道跟不上会压制估值")
    if "laws and regulations" in lower or "regulations" in lower:
        parts.append("医疗器械/行业监管复杂，合规和注册节奏是风险点")
    if "new products" in lower or "market acceptance" in lower or "r&d" in lower:
        parts.append("新品研发、注册和市场接受存在不确定性")
    if "consumer preferences" in lower or "public health outbreak" in lower:
        parts.append("需求受消费者偏好和外部公共卫生扰动影响")
    if "nanoforge" in lower or "proprietary" in lower or "ai nanotechnologies" in lower:
        parts.append("核心技术平台依赖度高，若迭代或验证不及预期会影响商业化")
    if "pipeline" in lower or "clinical" in lower or "approval" in lower:
        parts.append("研发管线、临床进度和审批节奏存在不确定性")
    if "large-scale" in lower or "customers" in lower or "customer" in lower:
        parts.append("大项目交付和客户集中度可能放大收入波动")
    if parts:
        return "风险：" + "；".join(dict.fromkeys(parts))
    return ""


def hkd_million_text(value: float) -> str:
    if abs(value) >= 100:
        return f"{value / 100:.1f}亿港元"
    return f"{value:.1f}百万港元"


def format_hkd_million_band(values: list[float]) -> str:
    cleaned = [value for value in values if value > 0]
    if not cleaned:
        return ""
    if len(cleaned) == 1 or abs(cleaned[0] - cleaned[-1]) < 1e-6:
        return hkd_million_text(cleaned[0])
    return f"{hkd_million_text(cleaned[0]).replace('港元', '')}-{hkd_million_text(cleaned[-1])}"


def format_multiple_band(values: list[float]) -> str:
    cleaned = [value for value in values if value == value and value not in {float("inf"), float("-inf")}]
    if not cleaned:
        return ""
    if len(cleaned) == 1 or abs(cleaned[0] - cleaned[-1]) < 0.05:
        return f"{cleaned[0]:.1f}x"
    return f"{cleaned[0]:.1f}x至{cleaned[-1]:.1f}x"


def extract_rmb_per_hkd(text: str) -> float | None:
    source = clean_pdf_text(text)
    patterns = (
        r"HK\$1\.00\s+to\s+RMB([0-9]+(?:\.[0-9]+)?)",
        r"RMB([0-9]+(?:\.[0-9]+)?)\s+to\s+HK\$1\.00",
        r"exchange rate of\s+HK\$1\.00\s+to\s+RMB([0-9]+(?:\.[0-9]+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.I)
        if match:
            return safe_float(match.group(1))
    return None


def extract_market_cap_hkd_m(text: str) -> list[float]:
    source = clean_pdf_text(text)
    match = re.search(
        r"Market capitalization of our Shares.*?(HK\$[0-9,.]+\s+million(?:\s+HK\$[0-9,.]+\s+million){0,3})",
        source,
        flags=re.I,
    )
    if not match:
        return []
    values = [safe_float(value) for value in re.findall(r"HK\$([0-9,.]+)\s+million", match.group(1), flags=re.I)]
    return [value for value in values if value is not None]


def latest_revenue_rmb_m(text: str) -> float | None:
    source = clean_pdf_text(text)
    patterns = (
        r"revenue(?:\s+increased)?\s+from\s+RMB[0-9,.]+\s+million\s+in\s+\d{4}\s+to\s+RMB[0-9,.]+\s+million\s+in\s+\d{4},\s+and\s+further(?:\s+increased)?\s+to\s+RMB([0-9,.]+)\s+million\s+in\s+\d{4}",
        r"revenue increased from RMB[0-9,.]+\s+million in \d{4} to RMB[0-9,.]+\s+million in \d{4}, and further to RMB([0-9,.]+)\s+million in \d{4}",
    )
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.I)
        if match:
            return safe_float(match.group(1))
    return None


def latest_net_profit_rmb_m(text: str) -> float | None:
    source = clean_pdf_text(text)
    growth = re.search(
        r"profit\s+for\s+the\s+year\s+increased\s+from\s+RMB[0-9,.]+\s+million\s+in\s+\d{4}\s+to\s+RMB[0-9,.]+\s+million\s+in\s+\d{4},\s+and\s+further\s+increased\s+to\s+RMB([0-9,.]+)\s+million\s+in\s+\d{4}",
        source,
        flags=re.I,
    )
    if growth:
        return safe_float(growth.group(1))
    values = [
        value
        for value in (safe_float(raw) for raw in re.findall(r"profit for the year of RMB([0-9,.]+)\s+million", source, flags=re.I))
        if value is not None
    ]
    return values[-1] if values else None


def latest_net_loss_rmb_m(text: str) -> float | None:
    source = clean_pdf_text(text)
    match = re.search(
        r"net losses of RMB[0-9,.]+\s+million,\s+RMB[0-9,.]+\s+million\s+and\s+RMB([0-9,.]+)\s+million",
        source,
        flags=re.I,
    )
    if match:
        return safe_float(match.group(1))
    values = [
        value
        for value in (safe_float(raw) for raw in re.findall(r"net loss(?:es)?(?: for the year)?(?: of)? RMB([0-9,.]+)\s+million", source, flags=re.I))
        if value is not None
    ]
    return values[-1] if values else None


def prospectus_issuer_metrics(text: str) -> dict[str, Any]:
    source = clean_pdf_text(text)
    profit = latest_net_profit_rmb_m(source)
    loss = latest_net_loss_rmb_m(source)
    revenue = latest_revenue_rmb_m(source)
    status = "unknown"
    if loss is not None and ("net losses" in source.lower() or "net loss" in source.lower()):
        status = "loss"
    elif profit is not None:
        status = "profitable"
    metrics: dict[str, Any] = {
        "latest_year": 2025 if "2025" in source else "",
        "profit_status": status,
        "revenue_rmb_m": round(revenue, 1) if revenue is not None else None,
        "net_profit_rmb_m": round(profit, 1) if profit is not None else None,
        "net_loss_rmb_m": round(loss, 1) if loss is not None else None,
        "rmb_per_hkd": extract_rmb_per_hkd(source),
        "market_cap_hkd_m": extract_market_cap_hkd_m(source),
    }
    return {key: value for key, value in metrics.items() if value not in (None, "", [])}


def augment_financial_cn_with_metrics(base: str, metrics: dict[str, Any]) -> str:
    parts = [compact_text(base)]
    year = compact_text(metrics.get("latest_year")) or "最近一年"
    revenue = safe_float(metrics.get("revenue_rmb_m"))
    profit = safe_float(metrics.get("net_profit_rmb_m"))
    loss = safe_float(metrics.get("net_loss_rmb_m"))
    if revenue is not None and "收入" not in parts[0]:
        parts.append(f"{year}收入 {rmb_million_text(revenue)}")
    if profit is not None and "净利润" not in parts[0]:
        parts.append(f"{year}净利润 {rmb_million_text(profit)}")
    if loss is not None and rmb_million_text(loss) not in parts[0]:
        parts.append(f"{year}净亏损 {rmb_million_text(loss)}")
    return "；".join(part for part in parts if part)


def fetch_prospectus_research(url: str) -> tuple[dict[str, Any], str]:
    if PdfReader is None:
        return {}, "pypdf_unavailable"
    if not compact_text(url):
        return {}, "missing_prospectus_url"
    try:
        data = fetch_url_bytes(url)
        reader = PdfReader(BytesIO(data))
        page_texts: list[str] = []
        for page in reader.pages[:80]:
            try:
                page_texts.append(page.extract_text() or "")
            except Exception:
                continue
        text = clean_pdf_text("\n".join(page_texts))
    except Exception as exc:  # noqa: BLE001
        return {}, f"prospectus_extract_error={type(exc).__name__}: {exc}"
    summary = extract_between(
        text,
        ("Who We Are", "Our Business"),
        ("OUR COMPETITIVE STRENGTHS", "OUR STRATEGIES", "RISK FACTORS"),
        max_chars=320,
    )
    financial = extract_between(
        text,
        (
            "Growth Achievement",
            "During the Track Record Period, our revenue",
            "revenue increased from RMB",
            "We had a net loss",
            "selected consolidated statement of profit or loss",
        ),
        ("GLOBAL OFFERING STATISTICS", "KEY FINANCIAL RATIOS", "FUTURE PLANS"),
        max_chars=900,
    )
    use_of_proceeds = extract_between(
        text,
        ("USE OF PROCEEDS", "Future Plans and Use of Proceeds", "Approximately 45.0%"),
        ("DIVIDEND", "LISTING EXPENSES", "UNDERWRITING"),
        max_chars=300,
    )
    risk = extract_between(
        text,
        ("Some of the major risks we face include",),
        ("SUMMARY OF KEY FINANCIAL INFORMATION", "GLOBAL OFFERING STATISTICS"),
        max_chars=300,
    )
    metrics = prospectus_issuer_metrics(text)
    financial_cn = augment_financial_cn_with_metrics(prospectus_financial_cn(text), metrics)
    return (
        {
            "company_summary": summary,
            "financial_highlights": financial,
            "use_of_proceeds": use_of_proceeds,
            "risk_highlights": risk,
            "company_summary_cn": prospectus_business_cn(summary),
            "financial_highlights_cn": financial_cn,
            "risk_highlights_cn": prospectus_risk_cn(risk, text),
            "issuer_metrics": metrics,
        },
        "",
    )


def table_records(frame: Any, *, limit: int = 10) -> list[dict[str, str]]:
    flat = flatten_html_frame(frame)
    if flat.empty:
        return []
    rows: list[dict[str, str]] = []
    for _, raw_row in flat.head(limit).iterrows():
        record: dict[str, str] = {}
        for column in flat.columns:
            key = compact_text(column)
            if not key or key.startswith("Unnamed:"):
                continue
            value = compact_text(raw_row.get(column))
            if value:
                record[key] = value
        if record:
            rows.append(record)
    return rows


def fetch_sl886_detail(code: str) -> tuple[dict[str, Any], str]:
    if BeautifulSoup is None:
        return {}, "bs4_unavailable"
    digits = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(5)
    if not digits.strip("0"):
        return {}, "missing_hk_code"
    url = SL886_IPO_DETAIL_URL.format(code=digits)
    try:
        html = fetch_url_text(url)
    except Exception as exc:  # noqa: BLE001
        return {"source_url": url}, f"sl886_fetch_error={type(exc).__name__}: {exc}"
    soup = BeautifulSoup(html, "html.parser")
    lines = [compact_text(line) for line in soup.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if line]

    def next_value(label: str) -> str:
        for idx, line in enumerate(lines):
            if line == label and idx + 1 < len(lines):
                return lines[idx + 1]
        return ""

    def section_between(start: str, stops: tuple[str, ...], *, max_chars: int = 420) -> str:
        try:
            start_idx = lines.index(start) + 1
        except ValueError:
            return ""
        end_idx = len(lines)
        for idx in range(start_idx, len(lines)):
            if lines[idx] in stops:
                end_idx = idx
                break
        text = " ".join(lines[start_idx:end_idx])
        return text[:max_chars]

    recommendation = next_value("用戶建議")
    score_text = next_value("建議分數")
    margin_text = next_value("預計孖展倍數")
    summary = section_between("公司摘要", ("盈利/業績重點", "所得款項用途", "保薦人 / IPO 條款"), max_chars=260)
    financial = section_between("盈利/業績重點", ("所得款項用途", "保薦人 / IPO 條款", "官方風險因素摘錄"), max_chars=360)
    proceeds = section_between("所得款項用途", ("保薦人 / IPO 條款", "官方風險因素摘錄", "預計孖展倍數"), max_chars=260)
    risk = section_between("官方風險因素摘錄", ("預計孖展倍數", "近期新股表現對比", "用戶建議"), max_chars=300)
    score = safe_float(score_text)
    return (
        {
            "source_url": url,
            "status_text": lines[lines.index(f"IPO {digits} 樂動機器人") + 1] if f"IPO {digits} 樂動機器人" in lines else "",
            "recommendation": recommendation,
            "score": score,
            "margin_multiple_text": margin_text,
            "margin_multiple": parse_multiple_text(margin_text),
            "updated_at": next_value("更新時間"),
            "company_summary": summary,
            "financial_highlights": financial,
            "use_of_proceeds": proceeds,
            "risk_highlights": risk,
        },
        "",
    )


def fetch_aastocks_detail(code: str) -> tuple[dict[str, Any], str]:
    if pd is None:
        return {}, "pandas_unavailable"
    digits = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(5)
    if not digits.strip("0"):
        return {}, "missing_hk_code"
    summary_url = f"{AASTOCKS_IPO_SUMMARY_URL}?symbol={digits}#info"
    try:
        html = fetch_url_text(summary_url)
        tables = pd.read_html(StringIO(html))
    except Exception as exc:  # noqa: BLE001
        return {"summary_url": summary_url}, f"aastocks_detail_fetch_error={type(exc).__name__}: {exc}"

    timetable: dict[str, str] = {}
    basic_info: dict[str, str] = {}
    ipo_info: dict[str, str] = {}
    cornerstone_df = pd.DataFrame()
    industry_comp_df = pd.DataFrame()
    sponsor_perf_df = pd.DataFrame()
    subscription_heat_df = pd.DataFrame()

    for frame in tables:
        flat = flatten_html_frame(frame)
        if flat.empty:
            continue
        first_col = [normalize_field_name(value) for value in flat.iloc[:, 0].tolist()]
        column_names = {compact_text(column) for column in flat.columns}
        if {"Offer Period", "Listing Date"}.issubset(set(first_col)):
            timetable = frame_to_pairs(flat)
            continue
        if {"Listing Market", "Industry", "Background"}.issubset(set(first_col)):
            basic_info = frame_to_pairs(flat)
            continue
        if "Lot Size" in first_col and "Offer Price" in first_col:
            for row in flat.itertuples(index=False):
                key = normalize_field_name(row[0])
                value = compact_text(row[1] if len(row) > 1 else "")
                if not key or not value:
                    continue
                if key in {"White Application Form", "Payee Bank"}:
                    break
                ipo_info[key] = value
            continue
        if {"Name", "Type", "Total"}.issubset(column_names):
            cornerstone_df = flat
            continue
        if {"Name", "Last2", "10-day % Chg.", "P/E Ratio", "Market Cap(B)"}.issubset(column_names):
            industry_comp_df = flat
            continue
        if {"Sponsor", "Company", "% Chg. on Debut1"}.issubset(column_names):
            sponsor_perf_df = flat
            continue
        if {"Name", "Over-sub. Rate", "One Lot Success Rate", "Applied lots for 1 lot"}.issubset(column_names):
            subscription_heat_df = flat

    comparable_changes = [
        value
        for value in (parse_percent_text(raw) for raw in industry_comp_df.get("10-day % Chg.", []))
        if value is not None
    ]
    comparable_pe = [
        value
        for value in (safe_float(raw) for raw in industry_comp_df.get("P/E Ratio", []))
        if value is not None
    ]
    sponsor_debut_changes = [
        value
        for value in (parse_percent_text(raw) for raw in sponsor_perf_df.get("% Chg. on Debut1", []))
        if value is not None
    ]
    recent_over_sub = [
        value
        for value in (safe_float(raw) for raw in subscription_heat_df.get("Over-sub. Rate", []))
        if value is not None
    ]
    cornerstone_total_usd = sum(parse_usd_millions(value) or 0.0 for value in cornerstone_df.get("Total", []))
    return (
        {
            "summary_url": summary_url,
            "offer_period": timetable.get("Offer Period", ""),
            "price_set_date": timetable.get("Price-set Date", ""),
            "allotment_date": timetable.get("Allotment Date", ""),
            "refund_date": timetable.get("Refund Date", ""),
            "listing_date": timetable.get("Listing Date", ""),
            "listing_market": basic_info.get("Listing Market", ""),
            "industry_en": basic_info.get("Industry", ""),
            "background_en": basic_info.get("Background", ""),
            "major_business_area_en": basic_info.get("Major Business Area", ""),
            "lot_size": int(safe_float(ipo_info.get("Lot Size")) or 0) or None,
            "offer_price_text": ipo_info.get("Offer Price", ""),
            "market_cap_text": ipo_info.get("Market Cap", ""),
            "hk_offer_shares_text": ipo_info.get("No. of HK Offer Shares", ""),
            "sponsors_en": ipo_info.get("Sponsor(s)", ""),
            "cornerstone_count": int(len(cornerstone_df.index)),
            "cornerstone_investors": table_records(cornerstone_df, limit=6),
            "cornerstone_total_usd_m": round(cornerstone_total_usd, 1) if cornerstone_total_usd else None,
            "comparable_companies": table_records(industry_comp_df, limit=6),
            "comparable_avg_10d_pct": round(sum(comparable_changes) / len(comparable_changes), 2) if comparable_changes else None,
            "comparable_median_pe": round(median_float(comparable_pe), 2) if comparable_pe else None,
            "sponsor_recent_deals": table_records(sponsor_perf_df, limit=5),
            "sponsor_perf_avg_debut_pct": round(sum(sponsor_debut_changes) / len(sponsor_debut_changes), 2) if sponsor_debut_changes else None,
            "recent_subscription_heat": table_records(subscription_heat_df, limit=5),
            "recent_subscription_median_over_sub": round(median_float(recent_over_sub), 2) if recent_over_sub else None,
        },
        "",
    )


def fetch_hkex_listing_documents() -> tuple[dict[str, dict[str, str]], str]:
    if BeautifulSoup is None:
        return {}, "bs4_unavailable"
    try:
        html = fetch_url_text(HKEX_NEW_LISTING_MAIN_BOARD_URL)
    except Exception as exc:  # noqa: BLE001
        return {}, f"hkex_new_listing_documents_error={type(exc).__name__}: {exc}"
    soup = BeautifulSoup(html, "html.parser")
    index: dict[str, dict[str, str]] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 5:
            continue
        code = "".join(ch for ch in cells[0].get_text(" ", strip=True) if ch.isdigit()).zfill(5)
        if not code.strip("0"):
            continue
        links_by_cell: list[str] = []
        for cell in cells[2:5]:
            link = ""
            anchor = cell.find("a", href=True)
            if anchor:
                link = str(anchor.get("href") or "").strip()
            links_by_cell.append(link)
        index[code] = {
            "new_listing_announcement_url": links_by_cell[0] if len(links_by_cell) > 0 else "",
            "prospectus_url": links_by_cell[1] if len(links_by_cell) > 1 else "",
            "allotment_results_url": links_by_cell[2] if len(links_by_cell) > 2 else "",
        }
    return index, ""


def short_sponsor_text(text: Any) -> str:
    value = compact_text(text)
    if not value:
        return ""
    parts = [part.strip() for part in re.split(r"[,;/]+|\band\b", value) if part.strip()]
    return "、".join(parts[:2]) if parts else value[:60]


def display_comparable_name(value: Any) -> str:
    text = compact_text(value)
    return HK_COMPARABLE_NAME_CN.get(text.upper(), text)


def display_sponsor_name(value: Any) -> str:
    text = compact_text(value)
    return HK_SPONSOR_NAME_CN.get(text, text)


def sponsor_display_text(value: Any, *, limit: int = 3) -> str:
    text = compact_text(value)
    if not text:
        return ""
    parts = [part.strip() for part in re.split(r"[,;/]+|\band\b", text) if part.strip()]
    displayed = [display_sponsor_name(part) for part in parts if part]
    return "、".join(displayed[:limit])


def hk_money_text(number: float, *, currency: str = "港元") -> str:
    if abs(number) >= 100_000_000:
        value = number / 100_000_000
        return f"{value:.1f}亿{currency}"
    if abs(number) >= 10_000:
        value = number / 10_000
        return f"{value:.0f}万{currency}"
    return f"{number:.0f}{currency}"


def format_hk_market_cap(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    numbers = [safe_float(part) for part in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    numbers = [number for number in numbers if number is not None]
    if len(numbers) >= 2:
        if abs(numbers[0]) >= 100_000_000 and abs(numbers[1]) >= 100_000_000:
            return f"{numbers[0] / 100_000_000:.1f}亿-{numbers[1] / 100_000_000:.1f}亿港元"
        return f"{hk_money_text(numbers[0])}-{hk_money_text(numbers[1])}"
    if len(numbers) == 1:
        return hk_money_text(numbers[0])
    return text


def format_aastocks_market_cap_b(value: Any) -> str:
    text = compact_text(value).upper()
    if not text or text == "N/A":
        return "N/A"
    number = safe_float(text.replace("B", ""))
    if number is None:
        return text
    return f"{number * 10:.1f}亿港元"


def subscription_window_status(item: dict[str, Any], target: date) -> tuple[str, str, str]:
    start_text, end_text = parse_offer_period(item.get("offer_period"))
    if not start_text and not end_text:
        return "unknown", "", ""
    start = date.fromisoformat(start_text) if start_text else None
    end = date.fromisoformat(end_text) if end_text else start
    if start and target < start:
        return "upcoming", start_text, end_text
    if end and target > end:
        return "closed", start_text, end_text
    return "open", start_text, end_text


def sponsor_quality_bonus(text: Any) -> tuple[int, str]:
    value = compact_text(text)
    if not value:
        return 0, "保荐人待核"
    preferred = (
        "CITIC",
        "CICC",
        "China International Capital",
        "Goldman",
        "Morgan Stanley",
        "J.P. Morgan",
        "UBS",
        "Huatai",
        "Haitong",
        "CCB International",
    )
    if any(token.lower() in value.lower() for token in preferred):
        return 6, "保荐人质量可接受"
    return 1, "保荐人需看过往项目表现"


def hk_subscription_judgement(item: dict[str, Any]) -> tuple[str, list[str]]:
    status = str(item.get("subscription_status") or "")
    if status == "closed":
        item["subscription_score"] = 0
        return "已过申购窗口", ["当前已不能申购，不作为今日打新机会"]

    def top_reasons(values: list[str]) -> list[str]:
        priority_tokens = ("亏损", "PE", "PS", "发售价未定")
        priority = [value for value in values if any(token in value for token in priority_tokens)]
        regular = [value for value in values if value not in priority]
        return [*priority, *regular][:5]

    score = 50
    reasons: list[str] = []
    if status == "open":
        score += 8
        reasons.append("申购窗口仍开放")
    elif status == "upcoming":
        score += 3
        reasons.append("申购窗口未开始，可提前预筛")
    else:
        score -= 8
        reasons.append("申购起止日待核")
    if item.get("prospectus_url"):
        score += 4
        reasons.append("招股书已抓取")
    else:
        score -= 6
        reasons.append("招股书待补")
    margin_multiple = safe_float(((item.get("subscription_crowding") or {}) if isinstance(item.get("subscription_crowding"), dict) else {}).get("estimated_margin_multiple"))
    if margin_multiple is not None:
        if margin_multiple >= 300:
            score += 4
            reasons.append(f"孖展约 {margin_multiple:.1f} 倍，极拥挤，胜率看中签率")
        elif margin_multiple >= 100:
            score += 8
            reasons.append(f"孖展约 {margin_multiple:.1f} 倍，市场热度高")
        elif margin_multiple >= 20:
            score += 5
            reasons.append(f"孖展约 {margin_multiple:.1f} 倍，有热度")
        else:
            score -= 2
            reasons.append(f"孖展约 {margin_multiple:.1f} 倍，热度一般")
    else:
        median_over_sub = safe_float(item.get("recent_subscription_median_over_sub"))
        if median_over_sub is not None and median_over_sub >= 500:
            score += 1
            reasons.append(f"近期新股超购中位 {median_over_sub:.1f} 倍，市场拥挤")
    if compact_text(item.get("offer_price_text")):
        score += 6
        reasons.append(f"发售价 {compact_text(item.get('offer_price_text'))}")
    else:
        score -= 10
        reasons.append("发售价未定")
    sponsor_bonus, sponsor_reason = sponsor_quality_bonus(item.get("sponsors_en"))
    score += sponsor_bonus
    reasons.append(sponsor_reason)
    cornerstone_total = safe_float(item.get("cornerstone_total_usd_m"))
    cornerstone_count = int(item.get("cornerstone_count") or 0)
    if cornerstone_total is not None and cornerstone_total >= 30:
        score += 8
        reasons.append(f"基石 {cornerstone_count} 家 / 约 {cornerstone_total:.1f} 百万美元")
    elif cornerstone_count:
        score += 3
        reasons.append(f"基石 {cornerstone_count} 家")
    else:
        score -= 4
        reasons.append("基石支持偏弱或待核")
    comparable_avg = safe_float(item.get("comparable_avg_10d_pct"))
    if comparable_avg is not None and comparable_avg >= 5:
        score += 7
        reasons.append(f"可比公司近10日 {comparable_avg:+.1f}%")
    elif comparable_avg is not None and comparable_avg <= -5:
        score -= 8
        reasons.append(f"可比公司近10日 {comparable_avg:+.1f}%")
    elif comparable_avg is not None:
        reasons.append(f"可比公司近10日 {comparable_avg:+.1f}%")
    prospectus = (item.get("prospectus_research") or {}) if isinstance(item.get("prospectus_research"), dict) else {}
    financial_text = " ".join(
        compact_text(prospectus.get(key))
        for key in ("financial_highlights", "financial_highlights_cn")
        if compact_text(prospectus.get(key))
    )
    metrics = prospectus.get("issuer_metrics") if isinstance(prospectus.get("issuer_metrics"), dict) else {}
    if (
        compact_text(metrics.get("profit_status")) == "loss"
        or "net losses" in financial_text.lower()
        or "net loss" in financial_text.lower()
        or "净亏损" in financial_text
        or "淨虧損" in financial_text
        or "虧損" in financial_text
    ):
        score -= 8
        reasons.append("发行人仍亏损，PE不适用")
    else:
        profit = safe_float(metrics.get("net_profit_rmb_m"))
        fx = safe_float(metrics.get("rmb_per_hkd"))
        market_caps = metrics.get("market_cap_hkd_m") if isinstance(metrics.get("market_cap_hkd_m"), list) else []
        comparable_pe = safe_float(item.get("comparable_median_pe"))
        if profit and fx and market_caps and comparable_pe:
            issuer_pe = safe_float(market_caps[-1]) * fx / profit if safe_float(market_caps[-1]) else None
            if issuer_pe is not None and issuer_pe > comparable_pe * 1.5:
                score -= 5
                reasons.append(f"发行PE约 {issuer_pe:.1f}x，高于可比中位 {comparable_pe:.1f}x")
    if "收入" in financial_text and ("增" in financial_text or "CAGR" in financial_text or "複合" in financial_text):
        score += 4
        reasons.append("招股书显示收入增长")
    sl886_score = safe_float(item.get("external_rule_score"))
    if sl886_score is not None and sl886_score >= 75:
        score += 3
        reasons.append(f"第三方规则分 {sl886_score:.0f}/100")
    item["subscription_score"] = round(score, 1)
    if score >= 72:
        return "倾向申购", top_reasons(reasons)
    if score >= 60:
        return "小额申购观察", top_reasons(reasons)
    if score >= 48:
        return "暂缓申购，待补关键项", top_reasons(reasons)
    return "不建议申购", top_reasons(reasons)


def apply_subscription_decision(item: dict[str, Any], target: date) -> dict[str, Any]:
    status, start_text, end_text = subscription_window_status(item, target)
    item["subscription_status"] = status
    item["subscription_start_date"] = start_text
    item["subscription_end_date"] = end_text
    window_text = f"{start_text} 至 {end_text}" if start_text or end_text else "待核"
    if status == "open":
        item["potential"] = "申购窗口开放"
        item["event_tags"] = [f"申购截至 {end_text or '待核'}"]
    elif status == "upcoming":
        item["potential"] = "申购前预筛"
        item["event_tags"] = [f"申购开始 {start_text or '待核'}"]
    elif status == "closed":
        item["potential"] = "已过申购窗口"
        item["event_tags"] = [f"申购已于 {end_text or '此前'} 截止"]
    else:
        item["potential"] = "申购窗口待核"
        item["event_tags"] = ["申购起止日待核"]
    judgement, reasons = hk_subscription_judgement(item)
    item["subscription_judgement"] = judgement
    item["subscription_rationale"] = reasons
    points = [f"申购窗口：{window_text}（{item['potential']}）", *reasons]
    existing = [str(x) for x in (item.get("initial_research_points") or []) if str(x).strip()]
    item["initial_research_points"] = [*points, *existing][:8]
    if status == "closed":
        item["execution_action"] = "不作为今日申购机会；仅用于复盘打新筛选模型，不写成上市后二级买入动作。"
    elif status in {"open", "upcoming"}:
        item["execution_action"] = (
            f"给出是否申购结论：{judgement}；申购前只补发行价/市值、基石、保荐人、公开认购热度和可比公司估值，不跟踪上市后买入。"
        )
    else:
        item["execution_action"] = "系统自动动作：先补申购起止日、发行价和招股书；补齐前不进入申购建议。"
    return item


def short_join(values: list[str], *, limit: int = 2) -> str:
    cleaned = [compact_text(value) for value in values if compact_text(value)]
    return "、".join(cleaned[:limit])


def subscription_crowding_line(item: dict[str, Any]) -> str:
    crowding = item.get("subscription_crowding") or {}
    if not isinstance(crowding, dict):
        crowding = {}
    margin = safe_float(crowding.get("estimated_margin_multiple"))
    median_over_sub = safe_float(crowding.get("recent_subscription_median_over_sub"))
    if median_over_sub is None:
        median_over_sub = safe_float(item.get("recent_subscription_median_over_sub"))
    parts: list[str] = []
    if margin is not None:
        label = "极拥挤" if margin >= 300 else "拥挤偏高" if margin >= 100 else "有热度" if margin >= 20 else "热度一般"
        parts.append(f"预计孖展 {margin:.1f} 倍（{label}）")
    if median_over_sub is not None:
        prefix = "个股实时孖展/公开认购未披露；" if margin is None else ""
        parts.append(f"{prefix}近期新股超购中位 {median_over_sub:.1f} 倍")
    if not parts:
        return "当前孖展/公开认购倍数未披露"
    return "；".join(parts)


def comparable_research_rows(item: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in item.get("comparable_companies") or []:
        if not isinstance(row, dict):
            continue
        raw_name = compact_text(row.get("Name"))
        if not raw_name:
            continue
        rows.append(
            {
                "name": display_comparable_name(raw_name),
                "name_en": raw_name,
                "ten_day_change": compact_text(row.get("10-day % Chg.")) or "N/A",
                "pe": compact_text(row.get("P/E Ratio")) or "N/A",
                "market_cap": format_aastocks_market_cap_b(row.get("Market Cap(B)")),
            }
        )
    return rows[:6]


def comparable_summary_text(item: dict[str, Any]) -> str:
    rows = comparable_research_rows(item)
    if not rows:
        return ""
    parts = []
    for row in rows:
        detail = [row["ten_day_change"]]
        if row["pe"] != "N/A":
            detail.append(f"PE {row['pe']}x")
        if row["market_cap"] != "N/A":
            detail.append(f"市值 {row['market_cap']}")
        parts.append(f"{row['name']}（{', '.join(detail)}）")
    return "样本 " + "；".join(parts)


def parse_market_cap_hkd_m_values(value: Any) -> list[float]:
    text = compact_text(value)
    if not text:
        return []
    numbers = [safe_float(part) for part in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    cleaned = [number for number in numbers if number is not None and number > 0]
    if not cleaned:
        return []
    if "亿" in text:
        return [number * 100 for number in cleaned]
    if any(number >= 100_000_000 for number in cleaned):
        return [number / 1_000_000 for number in cleaned]
    if "million" in text.lower() or "百万" in text:
        return cleaned
    return []


def issuer_metrics(item: dict[str, Any]) -> dict[str, Any]:
    research = item.get("prospectus_research") or {}
    metrics = research.get("issuer_metrics") if isinstance(research, dict) else {}
    return metrics if isinstance(metrics, dict) else {}


def issuer_market_cap_hkd_m_values(item: dict[str, Any]) -> list[float]:
    metrics = issuer_metrics(item)
    values = metrics.get("market_cap_hkd_m")
    if isinstance(values, list):
        cleaned = [safe_float(value) for value in values]
        output = [value for value in cleaned if value is not None and value > 0]
        if output:
            return output
    return parse_market_cap_hkd_m_values(item.get("market_cap_text"))


def issuer_valuation_line(item: dict[str, Any]) -> str:
    metrics = issuer_metrics(item)
    status = compact_text(metrics.get("profit_status"))
    year = compact_text(metrics.get("latest_year")) or "最近一年"
    market_caps = issuer_market_cap_hkd_m_values(item)
    market_cap_text = format_hkd_million_band(market_caps) or format_hk_market_cap(item.get("market_cap_text"))
    fx = safe_float(metrics.get("rmb_per_hkd"))
    profit = safe_float(metrics.get("net_profit_rmb_m"))
    loss = safe_float(metrics.get("net_loss_rmb_m"))
    revenue = safe_float(metrics.get("revenue_rmb_m"))
    fx_note = f"，按招股书汇率 HK$1=RMB{fx:.4f} 折算" if fx is not None else ""
    if status == "loss" or loss is not None:
        parts = [f"发行人亏损，PE 不适用"]
        if loss is not None:
            parts.append(f"{year}净亏损 {rmb_million_text(loss)}")
        if market_caps and fx is not None and loss:
            mechanical_pe = [-(cap * fx / loss) for cap in market_caps]
            parts.append(f"机械负PE {format_multiple_band(mechanical_pe)}（不具备估值含义{fx_note}）")
        if market_caps and fx is not None and revenue:
            ps = [cap * fx / revenue for cap in market_caps]
            parts.append(f"PS 约 {format_multiple_band(ps)}（{year}收入 {rmb_million_text(revenue)}）")
        if market_cap_text:
            parts.append(f"发行市值 {market_cap_text}")
        return "；".join(parts)
    if profit is not None:
        parts = [f"发行人已盈利，{year}净利润 {rmb_million_text(profit)}"]
        if market_caps and fx is not None:
            pe = [cap * fx / profit for cap in market_caps]
            parts.append(f"发行PE 约 {format_multiple_band(pe)}{fx_note}")
        elif market_cap_text:
            parts.append(f"发行市值 {market_cap_text}；PE 待汇率/总股本字段补齐")
        else:
            parts.append("发行PE 待发行市值字段补齐")
        return "；".join(parts)
    if market_cap_text:
        return f"发行市值 {market_cap_text}；发行人盈利口径待招股书表格补抽，不能只看可比PE"
    return "发行人PE/盈利口径待抓取，不能只用可比PE替代发行人估值"


def valuation_line(item: dict[str, Any]) -> str:
    parts: list[str] = []
    if compact_text(item.get("offer_price_text")):
        parts.append(f"发行价 {compact_text(item.get('offer_price_text'))} 港元")
    market_cap = format_hk_market_cap(item.get("market_cap_text"))
    if market_cap:
        parts.append(f"市值 {market_cap}")
    comparable_avg = safe_float(item.get("comparable_avg_10d_pct"))
    if comparable_avg is not None:
        parts.append(f"可比近10日 {comparable_avg:+.1f}%")
    comparable_pe = safe_float(item.get("comparable_median_pe"))
    if comparable_pe is not None:
        parts.append(f"可比PE中位 {comparable_pe:.1f}x")
    return "；".join(parts) if parts else "估值样本待抓取"


def sponsor_line(item: dict[str, Any]) -> str:
    sponsors = sponsor_display_text(item.get("sponsors_en")) or short_sponsor_text(item.get("sponsors_en")) or "保荐人待核"
    parts = [f"保荐人 {sponsors}"]
    sponsor_perf = safe_float(item.get("sponsor_perf_avg_debut_pct"))
    if sponsor_perf is not None:
        parts.append(f"保荐人近案首日均值 {sponsor_perf:+.1f}%")
    cornerstone_total = safe_float(item.get("cornerstone_total_usd_m"))
    cornerstone_count = int(item.get("cornerstone_count") or 0)
    if cornerstone_total is not None:
        parts.append(f"基石 {cornerstone_count} 家 / 约 {cornerstone_total:.1f} 百万美元")
    elif cornerstone_count:
        parts.append(f"基石 {cornerstone_count} 家")
    else:
        parts.append("基石偏弱或待核")
    return "；".join(parts)


def meaningful_prospectus_segment(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    if re.search(r"\.{5,}\s*\d", text):
        return ""
    return text


def prospectus_line(item: dict[str, Any]) -> str:
    research = item.get("prospectus_research") or {}
    if not isinstance(research, dict):
        return "招股书摘要待抓取"
    parts = [
        compact_text(research.get("company_summary_cn")),
        compact_text(research.get("financial_highlights_cn")),
        meaningful_prospectus_segment(research.get("use_of_proceeds")),
    ]
    text = "；".join(part for part in parts if part)
    return text[:260] if text else "招股书摘要待抓取"


def risk_line(item: dict[str, Any]) -> str:
    research = item.get("prospectus_research") or {}
    if isinstance(research, dict):
        risk_cn = compact_text(research.get("risk_highlights_cn"))
        if risk_cn:
            return risk_cn[:180]
        raw_risk = compact_text(research.get("risk_highlights"))
        if raw_risk and contains_cjk(raw_risk):
            return raw_risk[:180]
        if raw_risk:
            return "核心风险：招股书英文风险因素已抓取，需继续结构化中文化；先按盈利兑现、技术平台、监管审批和客户集中度复核。"
    if str(item.get("subscription_status") or "") == "closed":
        return "申购窗口已关闭，当前不再具备打新动作价值。"
    return "核心风险待抓取"


def apply_subscription_research(item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("subscription_status") or "")
    judgement = str(item.get("subscription_judgement") or "")
    if status == "closed":
        bucket = "not_qualified"
        skip_reason = "申购窗口已关闭"
    elif judgement in {"不建议申购", "暂缓申购，待补关键项"}:
        bucket = "not_qualified"
        skip_reason = judgement
    elif status in {"open", "upcoming"}:
        bucket = "actionable"
        skip_reason = ""
    else:
        bucket = "not_qualified"
        skip_reason = "申购窗口未核清"
    item["ipo_research_bucket"] = bucket
    item["ipo_skip_reason"] = skip_reason
    item["subscription_research"] = {
        "window": f"{item.get('subscription_start_date') or ''} 至 {item.get('subscription_end_date') or ''}".strip(" 至"),
        "score": item.get("subscription_score"),
        "verdict": judgement,
        "crowding": subscription_crowding_line(item),
        "issuer_valuation": issuer_valuation_line(item),
        "issuer_metrics": issuer_metrics(item),
        "valuation": valuation_line(item),
        "offer_price": f"{compact_text(item.get('offer_price_text'))} 港元" if compact_text(item.get("offer_price_text")) else "",
        "market_cap": format_hk_market_cap(item.get("market_cap_text")),
        "comparables": comparable_research_rows(item),
        "sponsor": sponsor_line(item),
        "prospectus": prospectus_line(item),
        "risk": risk_line(item),
        "decision_reasons": item.get("subscription_rationale") or [],
        "skip_reason": skip_reason,
    }
    if bucket == "actionable":
        item["execution_action"] = (
            f"申购研究结论：{judgement}；今天已完成窗口、拥挤度、可比估值、保荐/基石和招股书摘要预检，"
            "只需在截止前复核最终孖展/公开认购热度和定价。"
        )
    else:
        item["execution_action"] = f"不纳入今日申购：{skip_reason}；保留公司名与研究字段用于打新模型复盘。"
    return item


def hk_detail_points(item: dict[str, Any], detail: dict[str, Any]) -> list[str]:
    points: list[str] = [f"HKEX 日历确认 {item.get('listing_date')} 新上市"]
    offer_price = compact_text(detail.get("offer_price_text"))
    lot_size = detail.get("lot_size")
    if offer_price:
        points.append(f"发售价 {offer_price} 港元")
    if lot_size:
        points.append(f"每手 {lot_size} 股")
    if detail.get("market_cap_text"):
        points.append(f"上市市值 {format_hk_market_cap(detail.get('market_cap_text')) or compact_text(detail.get('market_cap_text'))}")
    if detail.get("hk_offer_shares_text"):
        points.append(f"香港公开发售 {compact_text(detail.get('hk_offer_shares_text'))}")
    if item.get("prospectus_url"):
        points.append("HKEX 招股书链接已抓取")
    if item.get("allotment_results_url"):
        points.append("HKEX 配发结果已披露")
    sponsor_text = short_sponsor_text(detail.get("sponsors_en"))
    if sponsor_text:
        points.append(f"保荐人 {sponsor_text}")
    cornerstone_count = int(detail.get("cornerstone_count") or 0)
    cornerstone_total = safe_float(detail.get("cornerstone_total_usd_m"))
    if cornerstone_count and cornerstone_total:
        points.append(f"基石 {cornerstone_count} 家 / 约 {cornerstone_total:.1f} 百万美元")
    comparable_avg = safe_float(detail.get("comparable_avg_10d_pct"))
    if comparable_avg is not None:
        points.append(f"可比公司近10日均值 {comparable_avg:+.1f}%")
    missing = []
    if not offer_price:
        missing.append("发售价")
    if not item.get("prospectus_url"):
        missing.append("招股书")
    if not sponsor_text:
        missing.append("保荐人")
    if not cornerstone_count:
        missing.append("基石")
    if missing:
        points.append("仍待自动补齐：" + "、".join(missing))
    return points[:7]


def enrich_hk_ipo_item(item: dict[str, Any], document_index: dict[str, dict[str, str]]) -> tuple[dict[str, Any], str]:
    documents = document_index.get(str(item.get("code") or "").zfill(5)) or {}
    item["new_listing_announcement_url"] = documents.get("new_listing_announcement_url", "")
    item["prospectus_url"] = documents.get("prospectus_url", "")
    item["allotment_results_url"] = documents.get("allotment_results_url", "")
    detail, note = fetch_aastocks_detail(str(item.get("code") or ""))
    if note:
        item["detail_status"] = "warn"
        item["detail_note"] = note
        return item, note
    item["detail_status"] = "pass"
    item["detail_source"] = "AASTOCKS IPO detail"
    item["detail_source_url"] = detail.get("summary_url")
    item["offer_price_text"] = detail.get("offer_price_text")
    item["lot_size"] = detail.get("lot_size")
    item["market_cap_text"] = detail.get("market_cap_text")
    item["hk_offer_shares_text"] = detail.get("hk_offer_shares_text")
    item["sponsors_en"] = detail.get("sponsors_en")
    item["cornerstone_count"] = detail.get("cornerstone_count")
    item["cornerstone_investors"] = detail.get("cornerstone_investors") or []
    item["cornerstone_total_usd_m"] = detail.get("cornerstone_total_usd_m")
    item["comparable_companies"] = detail.get("comparable_companies") or []
    item["comparable_avg_10d_pct"] = detail.get("comparable_avg_10d_pct")
    item["comparable_median_pe"] = detail.get("comparable_median_pe")
    item["sponsor_recent_deals"] = detail.get("sponsor_recent_deals") or []
    item["sponsor_perf_avg_debut_pct"] = detail.get("sponsor_perf_avg_debut_pct")
    item["recent_subscription_heat"] = detail.get("recent_subscription_heat") or []
    item["recent_subscription_median_over_sub"] = detail.get("recent_subscription_median_over_sub")
    item["subscription_crowding"] = {
        "estimated_margin_multiple": None,
        "estimated_margin_multiple_text": "",
        "updated_at": "",
        "recent_subscription_median_over_sub": detail.get("recent_subscription_median_over_sub"),
        "recent_subscription_heat": detail.get("recent_subscription_heat") or [],
    }
    item["industry_en"] = detail.get("industry_en")
    item["offer_period"] = detail.get("offer_period")
    item["price_set_date"] = detail.get("price_set_date")
    item["allotment_date"] = detail.get("allotment_date")
    sl886, sl886_note = fetch_sl886_detail(str(item.get("code") or ""))
    if sl886_note:
        item["ipo_research_source_status"] = "partial"
        item["ipo_research_note"] = sl886_note
    else:
        item["ipo_research_source_status"] = "pass"
        item["sl886_source_url"] = sl886.get("source_url")
        item["external_rule_recommendation"] = sl886.get("recommendation")
        item["external_rule_score"] = sl886.get("score")
        item["subscription_crowding"] = {
            "estimated_margin_multiple": sl886.get("margin_multiple"),
            "estimated_margin_multiple_text": sl886.get("margin_multiple_text"),
            "updated_at": sl886.get("updated_at"),
            "recent_subscription_median_over_sub": detail.get("recent_subscription_median_over_sub"),
            "recent_subscription_heat": detail.get("recent_subscription_heat") or [],
        }
        item["prospectus_research"] = {
            "company_summary": sl886.get("company_summary"),
            "financial_highlights": sl886.get("financial_highlights"),
            "use_of_proceeds": sl886.get("use_of_proceeds"),
            "risk_highlights": sl886.get("risk_highlights"),
        }
    prospectus_payload, prospectus_note = fetch_prospectus_research(str(item.get("prospectus_url") or ""))
    if prospectus_payload:
        existing_research = item.get("prospectus_research") or {}
        if not isinstance(existing_research, dict):
            existing_research = {}
        item["prospectus_research"] = {
            key: existing_research.get(key) or prospectus_payload.get(key) or ""
            for key in (
                "company_summary",
                "financial_highlights",
                "use_of_proceeds",
                "risk_highlights",
                "company_summary_cn",
                "financial_highlights_cn",
                "risk_highlights_cn",
                "issuer_metrics",
            )
        }
        item["prospectus_research_source"] = "HKEX prospectus"
    elif prospectus_note:
        item["prospectus_research_note"] = prospectus_note
    item["initial_research_points"] = hk_detail_points(item, detail)
    return item, ""


def normalize_new_listing(event: dict[str, str], *, target: date, recent_days: int, upcoming_days: int) -> dict[str, Any] | None:
    summary = event.get("SUMMARY", "")
    if "New Listing" not in summary:
        return None
    listing_date_text = parse_ical_date(event.get("DTSTART", ""))
    if not listing_date_text:
        return None
    listing_date = date.fromisoformat(listing_date_text)
    if listing_date < target - timedelta(days=max(recent_days, 0)):
        return None
    if listing_date > target + timedelta(days=max(upcoming_days, 0)):
        return None

    tail = summary.split("New Listing", 1)[-1].strip(" -")
    board = ""
    name_part = tail
    if " - " in tail:
        parts = [part.strip() for part in tail.split(" - ") if part.strip()]
        board = parts[0] if parts else ""
        name_part = parts[-1] if parts else tail
    match = re.search(r"(.+?)\s*\((\d{4,5})\)\s*$", name_part)
    name = name_part
    code = ""
    if match:
        name = match.group(1).strip()
        code = match.group(2).zfill(5)
    if is_non_equity_listing(name):
        return None
    name_cn, short_name_cn = chinese_name_pair(name)

    delta = (listing_date - target).days
    if delta == 0:
        tag = "今日上市"
        stance = "已过申购窗口"
    elif delta > 0:
        tag = f"{delta}日后上市"
        stance = "申购前预筛"
    else:
        tag = f"{abs(delta)}日前上市"
        stance = "已过申购窗口"
    return {
        "name": name_cn or name,
        "name_en": name if name_cn else "",
        "display_name_cn": name_cn,
        "short_name_cn": short_name_cn,
        "code": code,
        "market": "HK",
        "board": board or "HKEX",
        "source": "hkex_calendar",
        "event_tags": [tag],
        "listing_date": listing_date_text,
        "potential": stance,
        "initial_research_points": [
            f"HKEX 日历确认 {listing_date_text} 新上市",
            "等待 IPO 明细源补充申购窗口、发售价、每手、保荐人、基石和认购热度",
        ],
        "execution_action": "系统自动动作：补 IPO 明细源；PM 只看是否值得申购 IPO 股份",
    }


def build_payload(*, target: date, recent_days: int, upcoming_days: int, url: str, include_closed: bool = False) -> dict[str, Any]:
    ical_text, note = fetch_ical(url)
    items: list[dict[str, Any]] = []
    if ical_text:
        document_index, document_note = fetch_hkex_listing_documents()
        if document_note:
            note = f"{note}; {document_note}" if note else document_note
        for event in parse_ical_events(ical_text):
            item = normalize_new_listing(event, target=target, recent_days=recent_days, upcoming_days=upcoming_days)
            if item:
                item, detail_note = enrich_hk_ipo_item(item, document_index)
                item = apply_subscription_decision(item, target)
                item = apply_subscription_research(item)
                if detail_note:
                    note = f"{note}; {detail_note}" if note else detail_note
                if str(item.get("subscription_status") or "") == "closed" and not include_closed:
                    continue
                items.append(item)
    items.sort(key=lambda item: (str(item.get("listing_date") or ""), str(item.get("code") or ""), str(item.get("name") or "")))
    status = "pass" if not note else "warn"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_date": target.isoformat(),
        "status": status,
        "source": "HKEX Calendar iCal",
        "source_url": url,
        "include_recent_days": recent_days,
        "include_upcoming_days": upcoming_days,
        "include_closed": include_closed,
        "source_notes": [note] if note else [],
        "items": items,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_hk_ipo_watchlist"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar HK IPO Watchlist {payload.get("target_date") or "unknown"}"',
        "---",
        "",
        "# Radar HK IPO Watchlist",
        "",
        f"- 目标日期：`{payload.get('target_date')}`",
        f"- 状态：`{payload.get('status')}`",
        f"- 来源：`{payload.get('source')}`",
        f"- 覆盖窗口：近 `{payload.get('include_recent_days')}` 天 / 后 `{payload.get('include_upcoming_days')}` 天",
        f"- 已过申购窗口：`{'保留' if payload.get('include_closed') else '过滤'}`",
        f"- 事件数：`{len(payload.get('items') or [])}`",
        "",
    ]
    notes = payload.get("source_notes") or []
    if notes:
        lines.append("- source notes：" + " / ".join(str(item) for item in notes[:3]))
        lines.append("")
    items = payload.get("items") or []
    if not items:
        lines.append("- 当前窗口没有抓到仍可申购或即将开放申购的港股 IPO。")
        return "\n".join(lines) + "\n"
    for title, bucket in (("可申购 / 待申购", "actionable"), ("不达标 / 跳过", "not_qualified")):
        bucket_items = [item for item in items if str(item.get("ipo_research_bucket") or "") == bucket]
        if not bucket_items:
            continue
        lines.extend([f"## {title}", ""])
        for item in bucket_items:
            research = item.get("subscription_research") or {}
            if not isinstance(research, dict):
                research = {}
            lines.extend(
                [
                    f"### {item.get('name') or ''} {item.get('code') or ''}".strip(),
                    "",
                    f"- 结论：{research.get('verdict') or item.get('subscription_judgement') or ''}；评分：{research.get('score') or 'N/A'}；窗口：{research.get('window') or '?'}",
                    f"- 拥挤度：{research.get('crowding') or 'N/A'}",
                    f"- 发行人估值：{research.get('issuer_valuation') or 'N/A'}",
                    f"- 发行条款/可比：{research.get('valuation') or 'N/A'}",
                    f"- 保荐/基石：{research.get('sponsor') or 'N/A'}",
                    f"- 招股书要点：{research.get('prospectus') or 'N/A'}",
                    f"- 风险/跳过原因：{research.get('risk') or research.get('skip_reason') or 'N/A'}",
                    f"- 动作：{item.get('execution_action') or ''}",
                    "",
                ]
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    target = target_date(args.target_date)
    payload = build_payload(
        target=target,
        recent_days=max(args.include_recent_days, 0),
        upcoming_days=max(args.include_upcoming_days, 0),
        url=str(args.hkex_ical_url),
        include_closed=bool(args.include_closed),
    )
    write_json(args.output_json, payload)
    write_text(args.output_md, render_markdown(payload))
    print(json.dumps({"status": payload["status"], "target_date": payload["target_date"], "item_count": len(payload["items"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
