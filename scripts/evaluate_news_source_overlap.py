from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from radar_news_policy import fetch_cctv_news, fetch_cls_news, fetch_em_news


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "workpapers" / "news_source_overlap"


@dataclass
class SourceSnapshot:
    source_id: str
    frame: pd.DataFrame
    meta: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate overlap across current news sources.")
    parser.add_argument("--run-at", default="", help="ISO timestamp override.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory to write json/md snapshots.")
    return parser.parse_args()


def parse_run_dt(raw: str) -> datetime:
    if raw:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def normalize_title(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[【】\[\]（）()<>《》“”\"'':：，,。！？!?.;；\-—_·/\\|]+", "", text)
    return text


def bigram_set(value: str) -> set[str]:
    normalized = normalize_title(value)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[idx : idx + 2] for idx in range(len(normalized) - 1)}


def jaccard_similarity(left: str, right: str) -> float:
    left_set = bigram_set(left)
    right_set = bigram_set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def normalize_frame(source_id: str, frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["source_id"] = source_id
    normalized["title"] = normalized.get("title", "").astype(str)
    normalized["content"] = normalized.get("content", "").astype(str)
    normalized["date"] = normalized.get("date", "").astype(str)
    normalized["time"] = normalized.get("time", "").astype(str)
    normalized["title_norm"] = normalized["title"].map(normalize_title)
    normalized["has_date"] = normalized["date"].str.strip().astype(bool)
    normalized["has_time"] = normalized["time"].str.strip().astype(bool)
    normalized = normalized[normalized["title_norm"].astype(bool)].reset_index(drop=True)
    return normalized


def collect_sources(run_dt: datetime) -> list[SourceSnapshot]:
    frames_and_meta = [
        fetch_cctv_news(run_dt),
        fetch_cls_news(),
        fetch_em_news(),
    ]
    snapshots: list[SourceSnapshot] = []
    for frame, meta in frames_and_meta:
        source_id = str(meta.get("source_id", ""))
        snapshots.append(SourceSnapshot(source_id=source_id, frame=normalize_frame(source_id, frame), meta=meta))
    return snapshots


def source_summary(snapshot: SourceSnapshot) -> dict[str, Any]:
    frame = snapshot.frame
    unique_titles = frame["title_norm"].nunique()
    return {
        "source_id": snapshot.source_id,
        "fetched_count": int(snapshot.meta.get("fetched_count", len(frame.index))),
        "usable_row_count": int(len(frame.index)),
        "unique_title_count": int(unique_titles),
        "duplicate_title_count": int(len(frame.index) - unique_titles),
        "date_coverage_ratio": round(float(frame["has_date"].mean()) if not frame.empty else 0.0, 4),
        "time_coverage_ratio": round(float(frame["has_time"].mean()) if not frame.empty else 0.0, 4),
        "used_date": str(snapshot.meta.get("used_date", "")),
        "note": str(snapshot.meta.get("note", "")),
        "sample_titles": frame["title"].head(5).tolist(),
    }


def exact_overlap(left: pd.DataFrame, right: pd.DataFrame) -> tuple[int, list[str]]:
    left_titles = set(left["title_norm"].tolist())
    right_titles = set(right["title_norm"].tolist())
    overlap = sorted(left_titles & right_titles)
    return len(overlap), overlap[:10]


def approximate_overlap(left: pd.DataFrame, right: pd.DataFrame, threshold: float = 0.82) -> tuple[int, list[dict[str, Any]]]:
    left_rows = left[["title", "title_norm"]].drop_duplicates("title_norm").to_dict("records")
    right_rows = right[["title", "title_norm"]].drop_duplicates("title_norm").to_dict("records")
    if len(left_rows) > len(right_rows):
        left_rows, right_rows = right_rows, left_rows
    matches: list[dict[str, Any]] = []
    seen_right_norms: set[str] = set()
    for left_row in left_rows:
        best_score = 0.0
        best_row: dict[str, Any] | None = None
        for right_row in right_rows:
            if right_row["title_norm"] in seen_right_norms:
                continue
            score = max(
                jaccard_similarity(left_row["title"], right_row["title"]),
                difflib.SequenceMatcher(None, left_row["title_norm"], right_row["title_norm"]).ratio(),
            )
            if score > best_score:
                best_score = score
                best_row = right_row
        if best_row is not None and best_score >= threshold:
            seen_right_norms.add(best_row["title_norm"])
            matches.append(
                {
                    "left_title": left_row["title"],
                    "right_title": best_row["title"],
                    "similarity": round(best_score, 4),
                }
            )
    return len(matches), matches[:10]


def pair_summary(left: SourceSnapshot, right: SourceSnapshot) -> dict[str, Any]:
    exact_count, exact_samples = exact_overlap(left.frame, right.frame)
    approx_count, approx_samples = approximate_overlap(left.frame, right.frame)
    left_unique = max(left.frame["title_norm"].nunique(), 1)
    right_unique = max(right.frame["title_norm"].nunique(), 1)
    return {
        "left_source_id": left.source_id,
        "right_source_id": right.source_id,
        "left_unique_title_count": int(left.frame["title_norm"].nunique()),
        "right_unique_title_count": int(right.frame["title_norm"].nunique()),
        "exact_overlap_count": exact_count,
        "exact_overlap_ratio_vs_left": round(exact_count / left_unique, 4),
        "exact_overlap_ratio_vs_right": round(exact_count / right_unique, 4),
        "approx_overlap_count": approx_count,
        "approx_overlap_ratio_vs_left": round(approx_count / left_unique, 4),
        "approx_overlap_ratio_vs_right": round(approx_count / right_unique, 4),
        "exact_overlap_samples": exact_samples,
        "approx_overlap_samples": approx_samples,
    }


def unique_title_examples(target: SourceSnapshot, others: list[SourceSnapshot], limit: int = 8) -> list[str]:
    other_titles: set[str] = set()
    for item in others:
        other_titles.update(item.frame["title_norm"].tolist())
    unique_rows = target.frame[~target.frame["title_norm"].isin(other_titles)]
    return unique_rows["title"].head(limit).tolist()


def build_payload(run_dt: datetime, snapshots: list[SourceSnapshot]) -> dict[str, Any]:
    source_summaries = [source_summary(item) for item in snapshots]
    pair_summaries: list[dict[str, Any]] = []
    for idx, left in enumerate(snapshots):
        for right in snapshots[idx + 1 :]:
            pair_summaries.append(pair_summary(left, right))
    unique_examples = {
        item.source_id: unique_title_examples(item, [other for other in snapshots if other.source_id != item.source_id])
        for item in snapshots
    }
    return {
        "generated_at": utc_now_iso(),
        "run_at": run_dt.isoformat(timespec="seconds"),
        "sources": source_summaries,
        "pairs": pair_summaries,
        "unique_title_examples": unique_examples,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 新闻源重叠评估快照",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 评估时间：`{payload['run_at']}`",
        "",
        "## 源概览",
        "",
        "| source_id | usable_rows | unique_titles | date_cov | time_cov | note |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in payload["sources"]:
        lines.append(
            f"| `{item['source_id']}` | {item['usable_row_count']} | {item['unique_title_count']} | "
            f"{item['date_coverage_ratio']:.2f} | {item['time_coverage_ratio']:.2f} | {item['note']} |"
        )

    lines.extend(["", "## 两两重叠", "", "| left | right | exact_overlap | approx_overlap |", "| --- | --- | ---: | ---: |"])
    for item in payload["pairs"]:
        lines.append(
            f"| `{item['left_source_id']}` | `{item['right_source_id']}` | "
            f"{item['exact_overlap_count']} | {item['approx_overlap_count']} |"
        )

    lines.extend(["", "## 各源独有标题样本", ""])
    for source_id, titles in payload["unique_title_examples"].items():
        lines.append(f"### `{source_id}`")
        if not titles:
            lines.append("- 无明显独有标题样本")
        else:
            for title in titles:
                lines.append(f"- {title}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def main() -> None:
    args = parse_args()
    run_dt = parse_run_dt(args.run_at)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshots = collect_sources(run_dt)
    payload = build_payload(run_dt, snapshots)
    stamp = run_dt.strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"news_source_overlap_{stamp}.json"
    md_path = output_dir / f"news_source_overlap_{stamp}.md"
    latest_json_path = output_dir / "news_source_overlap_latest.json"
    latest_md_path = output_dir / "news_source_overlap_latest.md"

    json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    md_text = render_markdown(payload)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    latest_json_path.write_text(json_text, encoding="utf-8")
    latest_md_path.write_text(md_text, encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
