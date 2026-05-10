from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from radar_company_mapping import company_names_by_industry, ensure_company_mapping_cache
from radar_industry_registry import (
    build_industry_overlay_context,
    load_industry_registry,
    load_theme_chain_overlay_members,
    load_theme_chain_overlays,
)
from radar_news_overlay_routing import classify_non_industry_overlay
from radar_news_policy import (
    article_matches,
    article_matches_company_names,
    fetch_cctv_news,
    fetch_cls_news,
    fetch_em_news,
    headline_supports_company_match,
    industry_keywords,
    overlay_keywords_for_industry,
)


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "workpapers" / "news_source_mapping"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 新闻源行业映射命中率快照",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 评估时间：`{payload['run_at']}`",
        "",
        "## 源概览",
        "",
        "| source_id | usable_rows | raw_unmatched | overlay_hit | true_unmatched | single_hit | company_hit | multi_hit | single_hit_ratio | multi_hit_ratio |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in payload["sources"]:
        lines.append(
            "| `{source_id}` | {usable_rows} | {raw_unmatched} | {overlay_hit} | {true_unmatched} | {single_hit} | {company_hit} | {multi_hit} | {single_hit_ratio:.2f} | {multi_hit_ratio:.2f} |".format(
                **item
            )
        )
    for item in payload["sources"]:
        lines.extend(
            [
                "",
                f"## `{item['source_id']}` 样本",
                "",
                "### 公司名命中样本",
            ]
        )
        for sample in item.get("company_hit_examples", []):
            lines.append(f"- `{sample['industry_label']}`: {sample['title']}")
        lines.extend(
            [
                "",
                "### 单行业命中样本",
            ]
        )
        for sample in item.get("single_hit_examples", []):
            lines.append(f"- `{sample['industry_label']}`: {sample['title']}")
        lines.extend(["", "### 多行业歧义样本"])
        for sample in item.get("multi_hit_examples", []):
            labels = "、".join(sample.get("industry_labels", []))
            lines.append(f"- `{labels}`: {sample['title']}")
        lines.extend(["", "### 非行业 overlay 分流样本"])
        for sample in item.get("overlay_hit_examples", []):
            lines.append(f"- `{sample['overlay_label']}`: {sample['title']}")
        lines.extend(["", "### 仍未命中样本"])
        for sample in item.get("true_unmatched_examples", []):
            lines.append(f"- {sample['title']}")
    lines.append("")
    return "\n".join(lines)


def evaluate_source(
    source_id: str,
    rows: list[dict[str, str]],
    registry: list[dict[str, str]],
    company_name_map: dict[str, list[str]],
    item_keywords: dict[str, list[str]],
) -> dict[str, Any]:
    usable_rows = 0
    raw_unmatched = 0
    overlay_hit = 0
    true_unmatched = 0
    single_hit = 0
    multi_hit = 0
    single_hit_examples: list[dict[str, Any]] = []
    multi_hit_examples: list[dict[str, Any]] = []
    overlay_hit_examples: list[dict[str, Any]] = []
    overlay_hit_titles: list[str] = []
    true_unmatched_examples: list[dict[str, Any]] = []
    true_unmatched_titles: list[str] = []
    company_hit = 0
    company_hit_examples: list[dict[str, Any]] = []

    for row in rows:
        title = str(row.get("title", "")).strip()
        content = str(row.get("content", "")).strip()
        combined = f"{title}\n{content[:240]}".strip()
        if not combined:
            continue
        usable_rows += 1
        matched: list[dict[str, str]] = []
        company_reason_hit = False
        for item in registry:
            keyword_hit = article_matches(combined, item, item_keywords.get(item["industry_id"], []))
            company_hit_here = False
            if not keyword_hit and headline_supports_company_match(title):
                company_hit_here = article_matches_company_names(
                    title,
                    company_name_map.get(item["industry_id"], []),
                )
            if keyword_hit or company_hit_here:
                matched.append(item)
                company_reason_hit = company_reason_hit or (company_hit_here and not keyword_hit)
        if not matched:
            raw_unmatched += 1
            overlay_match = classify_non_industry_overlay(combined)
            if overlay_match is not None:
                overlay_hit += 1
                if title:
                    overlay_hit_titles.append(title)
                if len(overlay_hit_examples) < 5 and title:
                    overlay_hit_examples.append(
                        {
                            "title": title,
                            "overlay_id": overlay_match["overlay_id"],
                            "overlay_label": overlay_match["display_name_cn"],
                            "matched_keywords": overlay_match.get("matched_keywords", []),
                        }
                    )
                continue
            true_unmatched += 1
            if title:
                true_unmatched_titles.append(title)
            if len(true_unmatched_examples) < 5 and title:
                true_unmatched_examples.append({"title": title})
            continue
        if len(matched) == 1:
            single_hit += 1
            if company_reason_hit:
                company_hit += 1
                if len(company_hit_examples) < 5 and title:
                    company_hit_examples.append(
                        {
                            "title": title,
                            "industry_id": matched[0]["industry_id"],
                            "industry_label": matched[0]["display_name_cn"],
                        }
                    )
            if len(single_hit_examples) < 5 and title:
                single_hit_examples.append(
                    {
                        "title": title,
                        "industry_id": matched[0]["industry_id"],
                        "industry_label": matched[0]["display_name_cn"],
                    }
                )
            continue
        multi_hit += 1
        if len(multi_hit_examples) < 5 and title:
            multi_hit_examples.append(
                {
                    "title": title,
                    "industry_ids": [item["industry_id"] for item in matched[:5]],
                    "industry_labels": [item["display_name_cn"] for item in matched[:5]],
                }
            )

    denom = usable_rows or 1
    return {
        "source_id": source_id,
        "usable_rows": usable_rows,
        "raw_unmatched": raw_unmatched,
        "overlay_hit": overlay_hit,
        "true_unmatched": true_unmatched,
        "single_hit": single_hit,
        "company_hit": company_hit,
        "multi_hit": multi_hit,
        "single_hit_ratio": single_hit / denom,
        "multi_hit_ratio": multi_hit / denom,
        "company_hit_examples": company_hit_examples,
        "single_hit_examples": single_hit_examples,
        "multi_hit_examples": multi_hit_examples,
        "overlay_hit_examples": overlay_hit_examples,
        "overlay_hit_titles": overlay_hit_titles,
        "true_unmatched_examples": true_unmatched_examples,
        "true_unmatched_titles": true_unmatched_titles,
    }


def main() -> None:
    run_at = utc_now()
    registry = load_industry_registry()
    company_name_map = company_names_by_industry(ensure_company_mapping_cache(registry))
    overlay_context = build_industry_overlay_context(
        overlays=load_theme_chain_overlays(),
        members=load_theme_chain_overlay_members(),
    )
    item_keywords: dict[str, list[str]] = {}
    for item in registry:
        combined = industry_keywords(item)
        combined.extend(overlay_keywords_for_industry(item["industry_id"], overlay_context))
        deduped: list[str] = []
        seen: set[str] = set()
        for keyword in combined:
            if keyword in seen:
                continue
            seen.add(keyword)
            deduped.append(keyword)
        item_keywords[item["industry_id"]] = deduped
    source_frames = [
        fetch_cctv_news(run_at),
        fetch_cls_news(),
        fetch_em_news(),
    ]

    sources: list[dict[str, Any]] = []
    for frame, meta in source_frames:
        rows = frame.to_dict("records")
        sources.append(
            evaluate_source(
                str(meta.get("source_id", "")),
                rows,
                registry,
                company_name_map,
                item_keywords,
            )
        )

    payload = {
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "run_at": run_at.isoformat(timespec="seconds"),
        "sources": sources,
    }
    file_stamp = stamp(run_at)
    json_path = OUTPUT_DIR / f"news_source_mapping_{file_stamp}.json"
    md_path = OUTPUT_DIR / f"news_source_mapping_{file_stamp}.md"
    latest_json_path = OUTPUT_DIR / "news_source_mapping_latest.json"
    latest_md_path = OUTPUT_DIR / "news_source_mapping_latest.md"

    write_json(json_path, payload)
    write_json(latest_json_path, payload)
    markdown = build_markdown(payload)
    md_path.write_text(markdown + "\n", encoding="utf-8")
    latest_md_path.write_text(markdown + "\n", encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
