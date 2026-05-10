from __future__ import annotations

import argparse
from pathlib import Path

import akshare as ak
import pandas as pd

from radar_industry_registry import load_industry_registry


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE_OUTPUT_PATH = ROOT / "data" / "industry_representative_stock_candidates.csv"
DEFAULT_PRIMARY_OUTPUT_PATH = ROOT / "data" / "industry_representative_stocks_primary.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build first-pass representative stock candidates for industry objects.")
    parser.add_argument("--candidate-output", type=Path, default=DEFAULT_CANDIDATE_OUTPUT_PATH, help="Output CSV path for representative stock candidates.")
    parser.add_argument("--primary-output", type=Path, default=DEFAULT_PRIMARY_OUTPUT_PATH, help="Output CSV path for primary representative stocks.")
    parser.add_argument("--top-k", type=int, default=5, help="Top representative stock candidates per industry.")
    return parser.parse_args()


def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def percentile_desc(series: pd.Series) -> pd.Series:
    cleaned = safe_numeric(series).fillna(0)
    return cleaned.rank(pct=True, ascending=True, method="average")


def is_st_name(name: str) -> bool:
    normalized = str(name).upper().replace(" ", "")
    return "ST" in normalized


def clean_constituent_frame(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    cleaned["名称"] = cleaned["名称"].astype(str)
    cleaned = cleaned[~cleaned["名称"].map(is_st_name)].copy()
    cleaned["成交额"] = safe_numeric(cleaned["成交额"])
    cleaned["换手率"] = safe_numeric(cleaned["换手率"])
    cleaned["涨跌幅"] = safe_numeric(cleaned["涨跌幅"])
    cleaned["最新价"] = safe_numeric(cleaned["最新价"])
    cleaned["absolute_pct_change"] = cleaned["涨跌幅"].abs()
    cleaned = cleaned[cleaned["成交额"].fillna(0) > 0].copy()
    return cleaned


def build_candidates(registry: list[dict[str, str]], board_frame: pd.DataFrame, top_k: int) -> pd.DataFrame:
    board_map = {
        str(row["板块名称"]): row
        for row in board_frame[["板块名称", "板块代码", "领涨股票", "涨跌幅", "排名"]].to_dict("records")
    }
    rows: list[dict[str, object]] = []

    for item in registry:
        board_name = str(item["display_name_cn"])
        board = board_map.get(board_name)
        if board is None:
            continue
        frame = clean_constituent_frame(ak.stock_board_industry_cons_em(symbol=board_name))
        if frame.empty:
            continue
        frame["turnover_amount_pct"] = percentile_desc(frame["成交额"])
        frame["turnover_rate_pct"] = percentile_desc(frame["换手率"])
        frame["abs_change_pct"] = percentile_desc(frame["absolute_pct_change"])
        leader_name = str(board.get("领涨股票", ""))
        frame["is_board_leader"] = frame["名称"].astype(str).eq(leader_name)
        frame["representative_score"] = (
            frame["turnover_amount_pct"] * 0.7
            + frame["turnover_rate_pct"] * 0.1
            + frame["abs_change_pct"] * 0.1
            + frame["is_board_leader"].astype(float) * 0.1
        ).round(6)
        frame = frame.sort_values(
            by=["representative_score", "成交额", "换手率"],
            ascending=[False, False, False],
        ).head(top_k)
        frame = frame.reset_index(drop=True)
        frame["candidate_rank"] = frame.index + 1

        for _, row in frame.iterrows():
            rows.append(
                {
                    "industry_id": item["industry_id"],
                    "display_name_cn": item["display_name_cn"],
                    "sw_code": item["sw_code"],
                    "board_name": board_name,
                    "board_code": str(board.get("板块代码", "")),
                    "board_rank": board.get("排名", ""),
                    "board_pct_change": board.get("涨跌幅", ""),
                    "board_leader": leader_name,
                    "stock_code": str(row["代码"]),
                    "stock_name": str(row["名称"]),
                    "latest_price": row["最新价"],
                    "pct_change": row["涨跌幅"],
                    "turnover_amount": row["成交额"],
                    "turnover_rate": row["换手率"],
                    "representative_score": row["representative_score"],
                    "is_board_leader": bool(row["is_board_leader"]),
                    "candidate_rank": int(row["candidate_rank"]),
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "industry_id",
                "display_name_cn",
                "sw_code",
                "board_name",
                "board_code",
                "board_rank",
                "board_pct_change",
                "board_leader",
                "stock_code",
                "stock_name",
                "latest_price",
                "pct_change",
                "turnover_amount",
                "turnover_rate",
                "representative_score",
                "is_board_leader",
                "candidate_rank",
            ]
        )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    registry = load_industry_registry()
    board_frame = ak.stock_board_industry_name_em().copy()
    output = build_candidates(registry, board_frame, args.top_k)
    primary = output[output["candidate_rank"] == 1].copy()
    args.candidate_output.parent.mkdir(parents=True, exist_ok=True)
    args.primary_output.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.candidate_output, index=False, encoding="utf-8")
    primary.to_csv(args.primary_output, index=False, encoding="utf-8")
    print(f"wrote {len(output)} representative stock candidate rows to {args.candidate_output}")
    print(f"wrote {len(primary)} primary representative stock rows to {args.primary_output}")


if __name__ == "__main__":
    main()
