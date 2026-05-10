from __future__ import annotations

import argparse
from pathlib import Path

import akshare as ak
import pandas as pd

from radar_industry_registry import load_industry_registry, parse_heat_keywords


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = ROOT / "data" / "industry_etf_proxy_candidates.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build first-pass ETF proxy candidates for industry objects.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output CSV path.")
    parser.add_argument("--top-k", type=int, default=5, help="Top ETF candidates per industry.")
    return parser.parse_args()


def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def etf_keywords(item: dict[str, str]) -> list[str]:
    blacklist = {"行业", "概念", "板块", "交易", "A_share", "coverage_backbone"}
    raw = [item.get("display_name_cn", "")] + parse_heat_keywords(item.get("heat_keywords", ""))
    deduped: list[str] = []
    for text in raw:
        keyword = str(text).strip()
        if not keyword or keyword in blacklist:
            continue
        if keyword not in deduped:
            deduped.append(keyword)
    return deduped


def build_candidates(registry: list[dict[str, str]], etf_frame: pd.DataFrame, top_k: int) -> pd.DataFrame:
    frame = etf_frame.copy()
    frame["名称"] = frame["名称"].astype(str)
    frame["流通市值"] = safe_numeric(frame["流通市值"])
    frame["成交额"] = safe_numeric(frame["成交额"])
    frame["最新价"] = safe_numeric(frame["最新价"])
    rows: list[dict[str, object]] = []

    for item in registry:
        keywords = etf_keywords(item)
        if not keywords:
            continue
        candidates = []
        for _, etf in frame.iterrows():
            name = str(etf["名称"])
            matched = [keyword for keyword in keywords if keyword in name]
            if not matched:
                continue
            candidates.append(
                {
                    "industry_id": item["industry_id"],
                    "display_name_cn": item["display_name_cn"],
                    "sw_code": item["sw_code"],
                    "etf_code": str(etf["代码"]),
                    "etf_name": name,
                    "match_keyword": matched[0],
                    "match_keyword_count": len(matched),
                    "latest_price": etf["最新价"],
                    "turnover_amount": etf["成交额"],
                    "float_market_cap": etf["流通市值"],
                }
            )
        if not candidates:
            continue
        candidate_frame = pd.DataFrame(candidates).sort_values(
            by=["match_keyword_count", "float_market_cap", "turnover_amount"],
            ascending=[False, False, False],
        )
        candidate_frame = candidate_frame.head(top_k).reset_index(drop=True)
        candidate_frame["candidate_rank"] = candidate_frame.index + 1
        rows.extend(candidate_frame.to_dict("records"))

    if not rows:
        return pd.DataFrame(
            columns=[
                "industry_id",
                "display_name_cn",
                "sw_code",
                "etf_code",
                "etf_name",
                "match_keyword",
                "match_keyword_count",
                "latest_price",
                "turnover_amount",
                "float_market_cap",
                "candidate_rank",
            ]
        )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    registry = load_industry_registry()
    etf_frame = ak.fund_etf_spot_em()
    output = build_candidates(registry, etf_frame, args.top_k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False, encoding="utf-8")
    print(f"wrote {len(output)} ETF proxy candidate rows to {args.output}")


if __name__ == "__main__":
    main()
