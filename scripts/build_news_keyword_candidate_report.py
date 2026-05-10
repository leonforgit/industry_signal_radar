from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

from radar_industry_registry import load_industry_registry, parse_heat_keywords


ROOT = Path(__file__).resolve().parent.parent
MAPPING_DIR = ROOT / "workpapers" / "news_source_mapping"
OUTPUT_DIR = ROOT / "workpapers" / "news_keyword_candidates"
DEFAULT_MAPPING_PATH = MAPPING_DIR / "news_source_mapping_latest.json"

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{1,}|[\u4e00-\u9fff]{2,8}")
STOP_TOKENS = {
    "美国",
    "中国",
    "我国",
    "国内",
    "国际",
    "全球",
    "国家",
    "本周",
    "社会",
    "今日",
    "明天",
    "今年",
    "明年",
    "市场",
    "公司",
    "产业",
    "项目",
    "同比",
    "环比",
    "公开征求意见",
    "公开征求意见稿",
    "新闻精选",
    "国内联播快讯",
    "国际联播快讯",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_existing_keywords() -> set[str]:
    keywords: set[str] = set()
    for item in load_industry_registry():
        keywords.update(parse_heat_keywords(item.get("heat_keywords", "")))
    return {item.strip() for item in keywords if item.strip()}


def extract_candidate_tokens(titles: list[str], existing_keywords: set[str]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for title in titles:
        for token in TOKEN_PATTERN.findall(title):
            candidate = token.strip()
            if len(candidate) < 2:
                continue
            if candidate in STOP_TOKENS or candidate in existing_keywords:
                continue
            counter[candidate] += 1
            bucket = examples.setdefault(candidate, [])
            if len(bucket) < 3 and title not in bucket:
                bucket.append(title)
    rows: list[dict[str, Any]] = []
    for token, freq in counter.most_common(40):
        rows.append(
            {
                "token": token,
                "frequency": freq,
                "examples": examples.get(token, []),
            }
        )
    return rows


def build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 新闻行业词候选池",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 基于映射快照：`{payload['mapping_generated_at']}`",
        "",
        "## 使用规则",
        "",
        "- 这里只是候选池，不是可直接并入行业词表的最终答案。",
        "- 优先接受 `行业含义明确、跨样本重复出现、不会明显污染其他行业` 的词。",
        "- 公司名、地缘政治通用词、宏观统计词默认谨慎处理。",
        "",
    ]
    for item in payload["sources"]:
        lines.extend(
            [
                f"## `{item['source_id']}`",
                "",
                f"- 仍未命中标题数：`{item['unmatched_count']}`",
                "",
                "### 高频候选词",
                "",
                "| token | frequency | examples |",
                "| --- | ---: | --- |",
            ]
        )
        for token in item.get("candidate_tokens", []):
            examples = "；".join(token.get("examples", []))
            lines.append(
                f"| `{token['token']}` | {token['frequency']} | {examples} |"
            )
        lines.extend(["", "### 未命中标题样本"])
        for title in item.get("unmatched_titles", [])[:20]:
            lines.append(f"- {title}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    mapping_payload = load_json(DEFAULT_MAPPING_PATH)
    existing_keywords = load_existing_keywords()
    run_at = utc_now()

    sources: list[dict[str, Any]] = []
    for item in mapping_payload.get("sources", []):
        unmatched_titles = [
            str(title).strip()
            for title in item.get("true_unmatched_titles", item.get("unmatched_titles", []))
            if str(title).strip()
        ]
        sources.append(
            {
                "source_id": str(item.get("source_id", "")),
                "unmatched_count": len(unmatched_titles),
                "unmatched_titles": unmatched_titles,
                "candidate_tokens": extract_candidate_tokens(unmatched_titles, existing_keywords),
            }
        )

    payload = {
        "generated_at": run_at.isoformat(timespec="seconds"),
        "mapping_generated_at": str(mapping_payload.get("generated_at", "")),
        "sources": sources,
    }
    file_stamp = stamp(run_at)
    json_path = OUTPUT_DIR / f"news_keyword_candidates_{file_stamp}.json"
    md_path = OUTPUT_DIR / f"news_keyword_candidates_{file_stamp}.md"
    latest_json_path = OUTPUT_DIR / "news_keyword_candidates_latest.json"
    latest_md_path = OUTPUT_DIR / "news_keyword_candidates_latest.md"

    write_json(json_path, payload)
    write_json(latest_json_path, payload)
    markdown = build_markdown(payload)
    md_path.write_text(markdown + "\n", encoding="utf-8")
    latest_md_path.write_text(markdown + "\n", encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
