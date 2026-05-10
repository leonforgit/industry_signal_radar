#!/usr/bin/env python3
"""Build an optional Kimi editorial layer for the Radar daily report."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import re
import signal
import shlex
import subprocess
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from radar_config import DEFAULT_CONFIG_PATH, load_config_section
    from radar_upstream_bridge import build_scp_command, build_ssh_command, load_deployment_config, run_remote_python
except ModuleNotFoundError:
    from scripts.radar_config import DEFAULT_CONFIG_PATH, load_config_section
    from scripts.radar_upstream_bridge import build_scp_command, build_ssh_command, load_deployment_config, run_remote_python


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_SNAPSHOT = ROOT / "output" / "snapshots" / "radar_opportunity_snapshot_latest.json"
DEFAULT_INPUT_HANDOFF = ROOT / "output" / "handoffs" / "radar_research_handoff_latest.json"
DEFAULT_INPUT_QUALITY = ROOT / "output" / "reports" / "radar_report_quality_latest.json"
DEFAULT_INPUT_SOURCE_READINESS = ROOT / "output" / "reports" / "radar_source_readiness_latest.json"
DEFAULT_INPUT_COMPANY_ENRICHMENT = ROOT / "output" / "reports" / "radar_company_enrichment_latest.json"
DEFAULT_INPUT_NEWS_VERIFICATION = ROOT / "output" / "reports" / "radar_news_verification_latest.json"
DEFAULT_INPUT_IPO_WATCHLIST = ROOT / "output" / "reports" / "radar_ipo_watchlist_latest.json"
DEFAULT_INPUT_HK_IPO_WATCHLIST = ROOT / "output" / "reports" / "radar_hk_ipo_watchlist_latest.json"
DEFAULT_INPUT_STRUCTURAL_SIGNAL = ROOT / "output" / "reports" / "radar_structural_signal_latest.json"
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_kimi_editorial_latest.json"
DEFAULT_MD_OUTPUT = ROOT / "output" / "reports" / "radar_kimi_editorial_latest.md"
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
PRICED_IN_DAILY_RETURN = 0.04
PARTIAL_PRICED_IN_DAILY_RETURN = 0.02
PRICED_IN_AMOUNT_RATIO = 2.0
PRICED_IN_POSITIVE_DAYS = 0.8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input-snapshot", type=Path, default=DEFAULT_INPUT_SNAPSHOT)
    parser.add_argument("--input-handoff", type=Path, default=DEFAULT_INPUT_HANDOFF)
    parser.add_argument("--input-quality", type=Path, default=DEFAULT_INPUT_QUALITY)
    parser.add_argument("--input-source-readiness", type=Path, default=DEFAULT_INPUT_SOURCE_READINESS)
    parser.add_argument("--input-company-enrichment", type=Path, default=DEFAULT_INPUT_COMPANY_ENRICHMENT)
    parser.add_argument("--input-news-verification", type=Path, default=DEFAULT_INPUT_NEWS_VERIFICATION)
    parser.add_argument("--input-ipo-watchlist", type=Path, default=DEFAULT_INPUT_IPO_WATCHLIST)
    parser.add_argument("--input-hk-ipo-watchlist", type=Path, default=DEFAULT_INPUT_HK_IPO_WATCHLIST)
    parser.add_argument("--input-structural-signal", type=Path, default=DEFAULT_INPUT_STRUCTURAL_SIGNAL)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} is not a JSON object.")
    return payload


def read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        try:
            tokens = shlex.split(stripped, comments=True, posix=True)
        except ValueError:
            continue
        if not tokens:
            continue
        token = tokens[0]
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        env[key.strip()] = value
    return env


def resolve_output_path(raw: Any, fallback: Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        return fallback
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return ROOT / path


def remote_repo_path(remote_root: str, local_path: Path) -> str:
    try:
        relative = local_path.resolve().relative_to(ROOT)
    except ValueError:
        relative = Path(local_path.name)
    return str(Path(remote_root) / relative)


def copy_remote_file(*, host: str, ssh_options: str, remote_path: str, local_path: Path, timeout_seconds: int) -> tuple[bool, str]:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_scp_command(ssh_options, f"{host}:{remote_path}", str(local_path))
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"scp exited with {result.returncode}"
        return False, message
    return True, ""


def push_local_file_to_remote(*, host: str, ssh_options: str, local_path: Path, remote_path: str, timeout_seconds: int) -> tuple[bool, str]:
    if not local_path.exists():
        return True, f"skip_missing={local_path}"
    mkdir_command = build_ssh_command(host, ssh_options, ["mkdir", "-p", str(Path(remote_path).parent)])
    mkdir_result = subprocess.run(
        mkdir_command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if mkdir_result.returncode != 0:
        message = mkdir_result.stderr.strip() or mkdir_result.stdout.strip() or f"mkdir exited with {mkdir_result.returncode}"
        return False, message
    command = build_scp_command(ssh_options, str(local_path), f"{host}:{remote_path}")
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 30),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"scp exited with {result.returncode}"
        return False, message
    return True, ""


def try_remote_kimi_editorial(
    *,
    args: argparse.Namespace,
    config_path: Path,
    output_json: Path,
    output_md: Path,
    timeout_seconds: int,
) -> tuple[dict[str, Any] | None, str]:
    deployment = load_deployment_config(config_path)
    host = str(deployment.get("server_host") or "").strip()
    ssh_options = str(deployment.get("ssh_options") or "").strip()
    remote_root = str(deployment.get("remote_root") or "").strip().rstrip("/")
    remote_venv = str(deployment.get("remote_venv") or "").strip().rstrip("/")
    if not (host and remote_root and remote_venv):
        return None, "remote deployment unavailable"

    local_to_remote_inputs = [
        (args.input_snapshot, remote_repo_path(remote_root, args.input_snapshot)),
        (args.input_handoff, remote_repo_path(remote_root, args.input_handoff)),
        (args.input_quality, remote_repo_path(remote_root, args.input_quality)),
        (args.input_source_readiness, remote_repo_path(remote_root, args.input_source_readiness)),
        (args.input_company_enrichment, remote_repo_path(remote_root, args.input_company_enrichment)),
        (args.input_news_verification, remote_repo_path(remote_root, args.input_news_verification)),
        (args.input_ipo_watchlist, remote_repo_path(remote_root, args.input_ipo_watchlist)),
        (args.input_hk_ipo_watchlist, remote_repo_path(remote_root, args.input_hk_ipo_watchlist)),
        (args.input_structural_signal, remote_repo_path(remote_root, args.input_structural_signal)),
    ]
    for local_path, remote_path in local_to_remote_inputs:
        pushed, message = push_local_file_to_remote(
            host=host,
            ssh_options=ssh_options,
            local_path=local_path,
            remote_path=remote_path,
            timeout_seconds=timeout_seconds,
        )
        if not pushed:
            return None, f"sync input failed: {message}"

    remote_json = remote_repo_path(remote_root, output_json)
    remote_md = remote_repo_path(remote_root, output_md)
    remote_args = [
        "--config",
        f"{remote_root}/config/runtime_defaults.json",
        "--input-snapshot",
        remote_repo_path(remote_root, args.input_snapshot),
        "--input-handoff",
        remote_repo_path(remote_root, args.input_handoff),
        "--input-quality",
        remote_repo_path(remote_root, args.input_quality),
        "--input-source-readiness",
        remote_repo_path(remote_root, args.input_source_readiness),
        "--input-company-enrichment",
        remote_repo_path(remote_root, args.input_company_enrichment),
        "--input-news-verification",
        remote_repo_path(remote_root, args.input_news_verification),
        "--input-ipo-watchlist",
        remote_repo_path(remote_root, args.input_ipo_watchlist),
        "--input-hk-ipo-watchlist",
        remote_repo_path(remote_root, args.input_hk_ipo_watchlist),
        "--input-structural-signal",
        remote_repo_path(remote_root, args.input_structural_signal),
        "--output-json",
        remote_json,
        "--output-md",
        remote_md,
    ]
    remote_result, remote_message = run_remote_python(
        host=host,
        ssh_options=ssh_options,
        python_bin=f"{remote_venv}/bin/python",
        script_path=f"{remote_root}/scripts/build_radar_kimi_editorial.py",
        script_args=remote_args,
        timeout_seconds=timeout_seconds,
    )
    if remote_message:
        return None, remote_message
    copied_json, json_message = copy_remote_file(
        host=host,
        ssh_options=ssh_options,
        remote_path=remote_json,
        local_path=output_json,
        timeout_seconds=timeout_seconds,
    )
    if not copied_json:
        return None, f"sync json failed: {json_message}"
    copied_md, md_message = copy_remote_file(
        host=host,
        ssh_options=ssh_options,
        remote_path=remote_md,
        local_path=output_md,
        timeout_seconds=timeout_seconds,
    )
    if not copied_md:
        return None, f"sync md failed: {md_message}"
    payload = read_json(output_json)
    source_snapshot = read_json(args.input_snapshot)
    if not str(payload.get("run_id") or "").strip():
        payload["run_id"] = str(source_snapshot.get("run_id") or source_snapshot.get("radar_run_id") or "")
    payload["remote_fallback"] = {
        "status": "pass",
        "host": host,
        "remote_root": remote_root,
        "remote_status": (remote_result or {}).get("status"),
    }
    write_json(output_json, payload)
    write_text(output_md, render_markdown(payload))
    return payload, ""


def append_credential_candidate(
    candidates: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    *,
    token: str,
    base_url: str,
    model: str,
    source: str,
) -> None:
    value = str(token or "").strip()
    if not value:
        return
    resolved_base = str(base_url or "").strip().rstrip("/")
    resolved_model = str(model or "").strip()
    key = (value, resolved_base, resolved_model)
    if key in seen:
        return
    seen.add(key)
    candidates.append(
        {
            "token": value,
            "base_url": resolved_base,
            "model": resolved_model,
            "source": source,
        }
    )


def credential_endpoint_for_key(
    *,
    key_name: str,
    env_payload: dict[str, str],
    cfg_base_url: str,
    cfg_model: str,
    allow_env_model_override: bool,
    allow_env_base_url_override: bool,
) -> tuple[str, str]:
    if key_name == "MOONSHOT_API_KEY":
        base_url = str(env_payload.get("MOONSHOT_BASE_URL") or "").strip().rstrip("/")
        model = str(env_payload.get("KIMI_MODEL") or "").strip()
        if not base_url:
            base_url = cfg_base_url if "api.moonshot." in cfg_base_url else "https://api.moonshot.ai/v1"
        if not model:
            model = cfg_model if not cfg_model.startswith("anthropic/kimi-for-coding") else "kimi-k2.6"
        return base_url, model
    if key_name == "KIMI_API_KEY":
        base_url = (
            str(env_payload.get("KIMI_BASE_URL") or cfg_base_url).strip().rstrip("/")
            if allow_env_base_url_override
            else cfg_base_url
        )
        model = str(env_payload.get("KIMI_MODEL") or cfg_model).strip() if allow_env_model_override else cfg_model
        return base_url, model
    if key_name in {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"}:
        base_url = (
            str(env_payload.get("ANTHROPIC_BASE_URL") or env_payload.get("KIMI_BASE_URL") or cfg_base_url).strip().rstrip("/")
            if allow_env_base_url_override
            else cfg_base_url
        )
        model = (
            str(env_payload.get("LITELLM_CHAT_MODEL") or env_payload.get("ANTHROPIC_MODEL") or env_payload.get("KIMI_MODEL") or cfg_model).strip()
            if allow_env_model_override
            else cfg_model
        )
        return base_url, model
    return cfg_base_url, cfg_model


def discover_credential_candidates(cfg: dict[str, Any]) -> list[dict[str, str]]:
    env_var_names = [str(name).strip() for name in (cfg.get("env_var_names") or []) if str(name).strip()]
    env_file_paths = [str(path).strip() for path in (cfg.get("env_file_paths") or []) if str(path).strip()]
    base_url = str(cfg.get("base_url") or "https://api.kimi.com/coding").strip().rstrip("/")
    model = str(cfg.get("model") or "anthropic/kimi-for-coding").strip()
    allow_env_model_override = bool(cfg.get("allow_env_model_override", False))
    allow_env_base_url_override = bool(cfg.get("allow_env_base_url_override", False))
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for key in env_var_names:
        value = str(os.environ.get(key) or "").strip()
        if value:
            env_payload = dict(os.environ)
            candidate_base, candidate_model = credential_endpoint_for_key(
                key_name=key,
                env_payload=env_payload,
                cfg_base_url=base_url,
                cfg_model=model,
                allow_env_model_override=allow_env_model_override,
                allow_env_base_url_override=allow_env_base_url_override,
            )
            append_credential_candidate(
                candidates,
                seen,
                token=value,
                base_url=candidate_base,
                model=candidate_model,
                source=f"env:{key}",
            )

    for raw_path in env_file_paths:
        env_payload = load_env_file(Path(raw_path).expanduser())
        for key in env_var_names:
            value = str(env_payload.get(key) or "").strip()
            if value:
                candidate_base, candidate_model = credential_endpoint_for_key(
                    key_name=key,
                    env_payload=env_payload,
                    cfg_base_url=base_url,
                    cfg_model=model,
                    allow_env_model_override=allow_env_model_override,
                    allow_env_base_url_override=allow_env_base_url_override,
                )
                append_credential_candidate(
                    candidates,
                    seen,
                    token=value,
                    base_url=candidate_base or base_url,
                    model=candidate_model or model,
                    source=f"{raw_path}:{key}",
                )

    return candidates


def discover_credentials(cfg: dict[str, Any]) -> tuple[str, str, str]:
    candidates = discover_credential_candidates(cfg)
    if not candidates:
        base_url = str(cfg.get("base_url") or "https://api.kimi.com/coding").strip().rstrip("/")
        model = str(cfg.get("model") or "anthropic/kimi-for-coding").strip()
        return "", base_url, model
    first = candidates[0]
    return first["token"], first["base_url"], first["model"]


def normalize_model_name(model: str) -> str:
    text = str(model or "").strip()
    if text.startswith("anthropic/"):
        return text.split("/", 1)[1]
    return text or "kimi-for-coding"


def is_openai_compatible_model(*, base_url: str, model: str) -> bool:
    text = str(model or "").strip()
    base = str(base_url or "").strip().rstrip("/")
    if "api.kimi.com/coding" in base and not base.endswith("/v1"):
        return False
    if "api.kimi.com/coding" in base and base.endswith("/v1"):
        return True
    if "api.moonshot.ai" in base or "api.moonshot.cn" in base:
        return True
    if text.startswith("kimi-k2."):
        return True
    return False


def openai_chat_endpoint(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def compact_market_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    context = snapshot.get("market_sentiment_context") or {}
    if not isinstance(context, dict):
        return {}
    keys = [
        "market_flow_score",
        "market_flow_label",
        "market_event_score",
        "market_event_label",
        "market_composite_score",
        "market_composite_label",
    ]
    return {key: context.get(key) for key in keys if key in context}


def top_focus_items(snapshot: dict[str, Any], handoff: dict[str, Any], max_items: int) -> list[dict[str, Any]]:
    priority_items = handoff.get("priority_items") or []
    focus: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in priority_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("radar_object_id") or "")
        if not item_id or item_id in seen:
            continue
        focus.append(item)
        seen.add(item_id)
        if len(focus) >= max_items:
            return focus
    for item in snapshot.get("objects") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("triage_action") or "")
        if action not in {"immediate_research", "thesis_watch", "risk_review"}:
            continue
        item_id = str(item.get("radar_object_id") or "")
        if not item_id or item_id in seen:
            continue
        focus.append(item)
        seen.add(item_id)
        if len(focus) >= max_items:
            break
    return focus


def summarize_event(event: dict[str, Any]) -> str:
    source = str(event.get("source") or "").strip()
    text = str(event.get("event_type") or event.get("title") or "").strip()
    if source.startswith("news_event_hub"):
        source = "news"
    elif source.startswith("akshare:stock_notice_report"):
        source = "notice"
    elif source.startswith("proxy:"):
        source = "proxy"
    return f"{text} [{source or 'unknown'}]"


def clean_display_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_text(value: Any) -> str:
    number = optional_float(value)
    if number is None:
        return "N/A"
    return f"{number * 100:+.1f}%"


def ratio_x_text(value: Any) -> str:
    number = optional_float(value)
    if number is None or number <= 0:
        return "N/A"
    return f"{number:.2f}x"


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


def item_event_text(item: dict[str, Any]) -> str:
    texts: list[str] = []
    for event in item.get("supporting_events") or []:
        if not isinstance(event, dict):
            continue
        texts.extend(
            clean_display_text(str(event.get(key) or ""))
            for key in ("title", "headline", "summary", "event_type")
            if clean_display_text(str(event.get(key) or ""))
        )
    texts.extend(clean_display_text(str(x or "")) for x in (item.get("key_evidence") or []))
    texts.append(clean_display_text(str(item.get("catalyst_type") or "")))
    texts.append(clean_display_text(str(item.get("why_now") or "")))
    return "；".join(text for text in texts if text)


def is_earnings_only_item(item: dict[str, Any]) -> bool:
    if str(item.get("radar_object_type") or "") != "company":
        return False
    text = item_event_text(item)
    if not any(keyword in text for keyword in EARNINGS_EVENT_KEYWORDS):
        return False
    return not any(keyword in text for keyword in STRUCTURAL_EVENT_KEYWORDS)


def opportunity_precheck(item: dict[str, Any]) -> dict[str, Any]:
    if str(item.get("radar_object_type") or "") != "company":
        return {"status": "not_applicable", "weight": "normal", "reason": ""}
    if not is_earnings_only_item(item):
        context = item.get("price_context") or {}
        daily_return = optional_float(context.get("daily_return")) if isinstance(context, dict) else None
        if daily_return is not None and daily_return >= 0.06:
            return {
                "status": "hot_price_reaction",
                "weight": "watch_only",
                "reason": f"最新收盘涨跌 {pct_text(daily_return)}，事件已被明显交易，只适合做承接/扩散验证",
            }
        return {"status": "not_applicable", "weight": "normal", "reason": ""}
    context = item.get("price_context") or {}
    if not isinstance(context, dict) or not context:
        return {"status": "missing_price", "weight": "downgrade", "reason": "业绩类事件缺少最新收盘反应"}
    price_date = parse_date_text(context.get("as_of_date"))
    event_date = latest_event_date(item)
    daily_return = optional_float(context.get("daily_return"))
    amount_ratio_20 = optional_float(context.get("amount_ratio_20"))
    positive_days_5d = optional_float(context.get("positive_days_5d"))
    if event_date and price_date and event_date > price_date:
        return {
            "status": "awaiting_first_trade",
            "weight": "normal",
            "reason": f"事件在 {price_date} 收盘后发布，上一交易日涨跌 {pct_text(daily_return)}，首个交易日反应尚未产生，等待系统自动补齐首日价格与量能",
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
        return {
            "status": "priced_in",
            "weight": "downgrade",
            "reason": f"最新收盘涨跌 {pct_text(daily_return)}，20日量比 {ratio_x_text(amount_ratio_20)}，业绩利好可能已先被价格/量能反映",
        }
    if daily_return is not None and daily_return >= PARTIAL_PRICED_IN_DAILY_RETURN:
        return {
            "status": "partially_priced",
            "weight": "watch_only",
            "reason": f"最新收盘涨跌 {pct_text(daily_return)}，已有部分反应",
        }
    return {"status": "needs_confirmation", "weight": "normal", "reason": "上一交易日未明显反映，仍需验证承接"}


def summarize_item(item: dict[str, Any]) -> dict[str, Any]:
    price_context = item.get("price_context") or {}
    sentiment_context = item.get("sentiment_context") or {}
    supporting_events = item.get("supporting_events") or []
    return {
        "name": item.get("radar_object_name"),
        "object_type": item.get("radar_object_type"),
        "bucket": item.get("radar_bucket"),
        "triage_action": item.get("triage_action"),
        "catalyst_type": item.get("catalyst_type"),
        "hard_or_soft": item.get("hard_or_soft"),
        "catalyst_stage": item.get("catalyst_stage"),
        "radar_score": item.get("radar_score"),
        "rank_overall": item.get("rank_overall"),
        "why_now": item.get("why_now"),
        "confirmation_gap": item.get("confirmation_gap"),
        "failure_mode": item.get("failure_mode"),
        "followup_path": item.get("followup_path") or [],
        "primary_symbols": item.get("primary_symbols") or [],
        "etf_proxies": item.get("etf_proxies") or [],
        "opportunity_precheck": opportunity_precheck(item),
        "key_evidence": item.get("key_evidence") or [],
        "price_context": {
            "as_of_date": price_context.get("as_of_date"),
            "lag_days": price_context.get("lag_days"),
            "close": price_context.get("close"),
            "daily_return": price_context.get("daily_return"),
            "amount_ratio_20": price_context.get("amount_ratio_20"),
            "positive_days_5d": price_context.get("positive_days_5d"),
        }
        if isinstance(price_context, dict) and price_context
        else {},
        "sentiment_context": sentiment_context if isinstance(sentiment_context, dict) else {},
        "supporting_events": [summarize_event(event) for event in supporting_events[:4] if isinstance(event, dict)],
    }


def compact_sidecar_items(payload: dict[str, Any], *, key: str, limit: int) -> list[dict[str, Any]]:
    rows = payload.get(key) or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows[:limit] if isinstance(row, dict)]


def build_prompt(
    *,
    snapshot: dict[str, Any],
    handoff: dict[str, Any],
    quality: dict[str, Any],
    source_readiness: dict[str, Any],
    company_enrichment: dict[str, Any],
    news_verification: dict[str, Any],
    ipo_watchlist: dict[str, Any],
    hk_ipo_watchlist: dict[str, Any],
    structural_signal: dict[str, Any],
    max_focus_items: int,
    max_watch_items: int,
    max_summary_bullets: int,
) -> str:
    triage_counts = quality.get("queue_counts") or {}
    focus_items = [summarize_item(item) for item in top_focus_items(snapshot, handoff, max_focus_items)]
    context = {
        "as_of_date": snapshot.get("as_of_date"),
        "run_id": snapshot.get("run_id") or snapshot.get("radar_run_id"),
        "summary": snapshot.get("summary") or {},
        "queue_counts": triage_counts,
        "top_opportunities": quality.get("top_opportunities") or [],
        "market_sentiment_context": compact_market_context(snapshot),
        "source_readiness": {
            "status": source_readiness.get("status"),
            "expected_sample_date": source_readiness.get("expected_sample_date"),
            "market_latest_trade_date": ((source_readiness.get("market") or {}) if isinstance(source_readiness.get("market"), dict) else {}).get("latest_trade_date"),
        },
        "focus_items": focus_items,
        "company_enrichment_samples": compact_sidecar_items(company_enrichment, key="items", limit=max_focus_items),
        "news_verification_samples": compact_sidecar_items(news_verification, key="items", limit=max_focus_items),
        "ipo_watchlist": {
            "status": ipo_watchlist.get("status"),
            "target_date": ipo_watchlist.get("target_date"),
            "items": compact_sidecar_items(ipo_watchlist, key="items", limit=6),
        },
        "hk_ipo_watchlist": {
            "status": hk_ipo_watchlist.get("status"),
            "target_date": hk_ipo_watchlist.get("target_date"),
            "source": hk_ipo_watchlist.get("source"),
            "items": compact_sidecar_items(hk_ipo_watchlist, key="items", limit=6),
        },
        "structural_signal": {
            "status": structural_signal.get("status"),
            "method": structural_signal.get("method"),
            "category_counts": structural_signal.get("category_counts") or {},
            "items": compact_sidecar_items(structural_signal, key="items", limit=4),
        },
    }
    return (
        "你是事件驱动基金的 PM 编辑台。"
        "你只可以基于我给你的结构化 JSON 写一个更像基金经理晨会前会看的压缩层。"
        "不要重算分数，不要编造不存在的事实，不要给泛泛而谈的空话。"
        "你的输出是日报最上方的一层 PM note，不是第二份正文。"
        "你现在处在日报生成前的初研环节：凡是 context 已经给出 supporting_events、news_verification_samples、company_enrichment_samples 或 IPO watchlist 的对象，"
        "你必须先基于这些材料完成第一轮核实，不要把“核实真实性/看原文/确认是什么事件”原样丢给 PM 当下一步动作。"
        "请严格返回 JSON，不要带 Markdown 代码块。\n\n"
        "返回 schema:\n"
        "{\n"
        '  "headline": "一句话判断，18-32字",\n'
        '  "pm_summary": ["最多3条，每条一句话，强调今天最重要的确认/未确认"],\n'
        '  "focus_actions": [\n'
        '    {"name":"对象名","stance":"立即研究/继续跟踪/风险复核","why":"为什么今天值得看","verified_facts":["你已基于context核到的事实，最多2条"],"action":"核实后今天最该做的执行动作","risk":"最大风险或确认缺口"}\n'
        "  ],\n"
        '  "watch_items": [\n'
        '    {"name":"对象名","signal":"已经发生了什么变化","watch_for":"下一步要盯什么"}\n'
        "  ],\n"
        '  "preliminary_research": [\n'
        '    {"name":"对象名","setup":"机会雏形","verified_facts":["已核事实，最多2条"],"early_read":"你基于证据的初步判断","unresolved_gaps":["第一轮后仍缺的事实，最多2条"],"execution_action":"核实后今天最该推进的动作"}\n'
        "  ],\n"
        '  "ipo_watchlist": [\n'
        '    {"name":"新股名","market":"A/H","stance":"申购/小额申购/暂缓申购/放弃/已过申购窗口","why":"是否值得申购 IPO 股份的初判","verified_facts":["申购窗口/发行价/发行PE/行业PE/基石/保荐人等已知事实"],"execution_action":"申购前还要补什么决策字段"}\n'
        "  ],\n"
        '  "risk_notes": ["最多2条，指出今天最该防的误判"]\n'
        "}\n\n"
        "额外规则:\n"
        f"1. pm_summary 最多 {max_summary_bullets} 条，focus_actions 最多 {max_focus_items} 条，watch_items 最多 {max_watch_items} 条，risk_notes 最多 2 条。\n"
        "2. 如果价格样本滞后，就直接说滞后，不要假装已经看到盘后反应。\n"
        "3. focus_items 里每个对象都有 opportunity_precheck；凡 status=priced_in / missing_price 的纯业绩事件，原则上不要放入 focus_actions，只能放入 watch_items 或 preliminary_research，并写明原因。\n"
        "4. 如果某个业绩类事件涨幅已经较大，就明确指出“价格可能已先反映”；如果事件发生在价格样本之后，要写成系统自动补齐首个交易日承接验证，不能因为首日尚未产生而机械降权。\n"
        "5. 行业机会必须使用 context 的 primary_symbols / etf_proxies 给出代表股与 ETF 的跟踪口径，不能只写“看代表股/ETF 是否确认”。\n"
        "6. 动作建议必须是 1-3 个交易日内可执行的研究/验证动作，不能写“等下个季度再看”。\n"
        "7. 不要给出买入、试仓、加仓、减仓、止损之类交易执行建议；这里只是前置研究系统，不是交易系统。\n"
        "8. 文风要短、硬、像 PM 口径，不要写成研究报告。\n"
        "9. headline 和 focus_actions 只能引用 JSON context 里的 top_opportunities 或 focus_items，不要引入新的宏大叙事主体。\n"
        "10. pm_summary 只保留最关键的矛盾点、确认缺口或已被价格反映的风险，不要重复 PM Quick View 已经明显写出的事实。\n"
        "11. watch_items 只作为补充跟踪，不要和 focus_actions 重复。\n\n"
        "12. preliminary_research 是你对 focus_items 的初步研究，不是复述标题；必须指出已核事实、证据强弱、核实后还剩什么缺口、今天该推进什么。\n"
        "13. 如果 supporting_events 已经给出“监管函/业绩/公告标题”，就把它写进 verified_facts；不要再写“核实事件真实性”。\n"
        "14. action / execution_action 禁止使用“先核实真实性”“确认是否真实”“补具体公告原文”这类前置动作；这些应在 verified_facts / unresolved_gaps 里交代。\n"
        "15. ipo_watchlist 可同时参考 ipo_watchlist.items 和 hk_ipo_watchlist.items；IPO 的核心动作是是否申购 IPO 股份，不是上市后二级买入，也不是跟踪首日承接。\n"
        "16. IPO 必须先判断申购窗口：已过申购窗口只能写“复盘打新模型/跳过”，不能写成当前机会；仍可申购或即将申购时，动作要围绕发行价、估值、募资用途、基石/保荐人、公开认购热度和申购上限。\n"
        "17. 如果 structural_signal.items 给出 30-120d 线索，可把最重要的一条放入 watch_items；写清楚征兆、方向、验证和反证，不要只写“继续关注”。\n\n"
        "JSON context:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )


def extract_first_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty model response.")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped)
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model response.")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Model response JSON is not an object.")
    return payload


@contextmanager
def wall_clock_timeout(seconds: float):
    timeout_seconds = max(float(seconds or 0), 0.0)
    if timeout_seconds <= 0 or threading.current_thread() is not threading.main_thread():
        yield
        return

    def on_timeout(_signum, _frame):
        raise TimeoutError(f"Kimi call exceeded wall-clock timeout {timeout_seconds:.0f}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    signal.signal(signal.SIGALRM, on_timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])
        signal.signal(signal.SIGALRM, previous_handler)


def _call_kimi_http(*, token: str, base_url: str, model: str, prompt: str, max_tokens: int, timeout_seconds: float) -> str:
    if is_openai_compatible_model(base_url=base_url, model=model):
        request_body: dict[str, Any] = {
            "model": str(model).strip(),
            "messages": [
                {"role": "system", "content": "你是一个负责日报编辑层的中文金融编辑。只输出严格 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": int(max_tokens),
        }
        if str(model).strip().startswith(("kimi-k2.6", "kimi-k2.5")):
            request_body["thinking"] = {"type": "disabled"}
        request = Request(
            openai_chat_endpoint(base_url),
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with wall_clock_timeout(timeout_seconds):
                with urlopen(request, timeout=float(timeout_seconds)) as response:
                    raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code}: {body[:400]}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error: {exc}") from exc
        payload = json.loads(raw)
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI-compatible response did not include choices.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        return str((message or {}).get("content") or "").strip()

    request_body = {
        "model": normalize_model_name(model),
        "max_tokens": int(max_tokens),
        "temperature": 0.0,
        "system": "你是一个负责日报编辑层的中文金融编辑。只输出严格 JSON。",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
    }
    endpoint = base_url.rstrip("/") + "/v1/messages"
    request = Request(
        endpoint,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with wall_clock_timeout(timeout_seconds):
            with urlopen(request, timeout=float(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {body[:400]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    payload = json.loads(raw)
    content = payload.get("content") or []
    texts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "") == "text":
            texts.append(str(block.get("text") or ""))
    return "\n".join(texts).strip()


def _call_kimi_worker(
    result_queue: "mp.Queue[dict[str, Any]]",
    *,
    token: str,
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
) -> None:
    try:
        text = _call_kimi_http(
            token=token,
            base_url=base_url,
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
    except BaseException as exc:  # noqa: BLE001
        result_queue.put({"ok": False, "error_type": type(exc).__name__, "error": str(exc)})
    else:
        result_queue.put({"ok": True, "text": text})


def call_kimi(*, token: str, base_url: str, model: str, prompt: str, max_tokens: int, timeout_seconds: float) -> str:
    timeout_budget = max(float(timeout_seconds or 0), 1.0)
    try:
        context = mp.get_context("fork")
    except ValueError:
        context = mp.get_context()
    result_queue: "mp.Queue[dict[str, Any]]" = context.Queue(maxsize=1)
    process = context.Process(
        target=_call_kimi_worker,
        kwargs={
            "result_queue": result_queue,
            "token": token,
            "base_url": base_url,
            "model": model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_budget,
        },
        daemon=True,
    )
    process.start()
    process.join(timeout_budget)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        raise TimeoutError(f"Kimi call exceeded hard process timeout {timeout_budget:.0f}s")
    try:
        result = result_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError(f"Kimi call worker exited without result; exitcode={process.exitcode}") from exc
    if result.get("ok") is True:
        return str(result.get("text") or "").strip()
    raise RuntimeError(f"Kimi call worker failed: {result.get('error_type')}: {result.get('error')}")


def call_kimi_json(
    *,
    token: str,
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    raw_response = call_kimi(
        token=token,
        base_url=base_url,
        model=model,
        prompt=prompt,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    try:
        return extract_first_json_object(raw_response)
    except Exception as first_exc:  # noqa: BLE001
        repair_prompt = (
            prompt
            + "\n\n你上一轮输出不是合法 JSON，解析错误为："
            + str(first_exc)
            + "\n请重新输出。要求：只返回一个 JSON object；字符串内部不要使用英文双引号，必要时改用中文引号；不要省略逗号；不要输出解释文字。"
        )
        raw_retry = call_kimi(
            token=token,
            base_url=base_url,
            model=model,
            prompt=repair_prompt,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        return extract_first_json_object(raw_retry)


def compact_kimi_error(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc or "")).strip()
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-<redacted>", text)
    return text[:360]


def is_transient_kimi_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "remote end closed",
            "http 502",
            "http 503",
            "http 504",
        )
    )


def call_kimi_json_with_candidates(
    *,
    candidates: list[dict[str, str]],
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
    retry_attempts: int = 0,
    retry_timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    errors: list[str] = []
    for candidate in candidates:
        source = str(candidate.get("source") or "credential")
        base_url = str(candidate.get("base_url") or "").strip().rstrip("/")
        model = str(candidate.get("model") or "").strip()
        token = str(candidate.get("token") or "").strip()
        if not token:
            continue
        attempt_timeouts = [float(timeout_seconds)]
        retry_budget = max(int(retry_attempts), 0)
        if retry_budget:
            expanded_timeout = float(retry_timeout_seconds or max(float(timeout_seconds) * 2, 120.0))
            attempt_timeouts.extend(expanded_timeout for _ in range(retry_budget))
        for attempt_index, attempt_timeout in enumerate(attempt_timeouts):
            try:
                payload = call_kimi_json(
                    token=token,
                    base_url=base_url,
                    model=model,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    timeout_seconds=attempt_timeout,
                )
                return payload, candidate, errors
            except Exception as exc:  # noqa: BLE001
                retrying = attempt_index < len(attempt_timeouts) - 1 and is_transient_kimi_error(exc)
                suffix = f" retry_timeout={attempt_timeouts[attempt_index + 1]:.0f}s" if retrying else ""
                errors.append(f"{source} attempt={attempt_index + 1} timeout={attempt_timeout:.0f}s -> {type(exc).__name__}: {compact_kimi_error(exc)}{suffix}")
                if not retrying:
                    break
    raise RuntimeError("All Kimi credential candidates failed: " + " | ".join(errors[:4]))


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "---",
        'codex_output: true',
        'codex_output_category: "radar_kimi_editorial"',
        'codex_output_entity: "radar_workspace"',
        f'codex_output_title: "Radar Kimi Editorial {payload.get("as_of_date", "")}"',
        "---",
        "",
        "# Radar Kimi Editorial Layer",
        "",
        f"- 样本日期：`{payload.get('as_of_date', '')}`",
        f"- 运行批次：`{payload.get('run_id', '')}`",
        f"- 生成时间：`{payload.get('generated_at', '')}`",
        f"- 状态：`{payload.get('status', '')}`",
    ]
    if payload.get("provider"):
        lines.append(f"- provider：`{payload.get('provider')}`")
    if payload.get("model"):
        lines.append(f"- model：`{payload.get('model')}`")
    if payload.get("model_family"):
        lines.append(f"- model_family：`{payload.get('model_family')}`")
    lines.append("")
    status = str(payload.get("status") or "")
    if status != "pass":
        lines.append(f"- 这轮 Kimi editorial 未启用：{payload.get('note', 'unknown')}")
        return "\n".join(lines) + "\n"
    headline = str(payload.get("headline") or "").strip()
    if headline:
        lines.extend(["## PM Headline", "", f"> {headline}", ""])
    summary = payload.get("pm_summary") or []
    if summary:
        lines.extend(["## PM Summary", ""])
        lines.extend(f"- {str(item).strip()}" for item in summary if str(item).strip())
        lines.append("")
    focus_actions = payload.get("focus_actions") or []
    if focus_actions:
        lines.extend(["## Focus Actions", "", "| 对象 | 立场 | 为什么今天要看 | 已核事实 | 今天动作 | 最大风险 |", "| --- | --- | --- | --- | --- | --- |"])
        for item in focus_actions:
            if not isinstance(item, dict):
                continue
            facts = item.get("verified_facts") or []
            fact_text = "；".join(str(x).strip() for x in facts if str(x).strip()) if isinstance(facts, list) else str(facts or "")
            lines.append(
                "| {name} | {stance} | {why} | {facts} | {action} | {risk} |".format(
                    name=str(item.get("name") or "").replace("|", "/"),
                    stance=str(item.get("stance") or "").replace("|", "/"),
                    why=str(item.get("why") or "").replace("|", "/"),
                    facts=fact_text.replace("|", "/"),
                    action=str(item.get("action") or "").replace("|", "/"),
                    risk=str(item.get("risk") or "").replace("|", "/"),
                )
            )
        lines.append("")
    watch_items = payload.get("watch_items") or []
    if watch_items:
        lines.extend(["## Watch Items", ""])
        for item in watch_items:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- {name} | 已见信号：{signal} | 下一步：{watch_for}".format(
                    name=str(item.get("name") or ""),
                    signal=str(item.get("signal") or ""),
                    watch_for=str(item.get("watch_for") or ""),
                )
            )
        lines.append("")
    preliminary_research = payload.get("preliminary_research") or []
    if preliminary_research:
        lines.extend(["## Preliminary Research", ""])
        for item in preliminary_research:
            if not isinstance(item, dict):
                continue
            facts = item.get("verified_facts") or []
            gaps = item.get("unresolved_gaps") or []
            fact_text = "；".join(str(x).strip() for x in facts if str(x).strip()) if isinstance(facts, list) else str(facts or "")
            gap_text = "；".join(str(x).strip() for x in gaps if str(x).strip()) if isinstance(gaps, list) else str(gaps or "")
            lines.append(
                "- {name} | 机会雏形：{setup} | 已核：{facts} | 初判：{early_read} | 剩余缺口：{gaps} | 下一步：{action}".format(
                    name=str(item.get("name") or ""),
                    setup=str(item.get("setup") or ""),
                    facts=fact_text,
                    early_read=str(item.get("early_read") or ""),
                    gaps=gap_text,
                    action=str(item.get("execution_action") or item.get("next_check") or ""),
                )
            )
        lines.append("")
    ipo_watchlist = payload.get("ipo_watchlist") or []
    if ipo_watchlist:
        lines.extend(["## IPO Watchlist", ""])
        for item in ipo_watchlist:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- {name} | {market} | {stance} | {why} | 下一步：{next_check}".format(
                    name=str(item.get("name") or ""),
                    market=str(item.get("market") or ""),
                    stance=str(item.get("stance") or ""),
                    why=str(item.get("why") or ""),
                    next_check=str(item.get("execution_action") or item.get("next_check") or ""),
                )
            )
        lines.append("")
    risk_notes = payload.get("risk_notes") or []
    if risk_notes:
        lines.extend(["## Risk Notes", ""])
        lines.extend(f"- {str(item).strip()}" for item in risk_notes if str(item).strip())
        lines.append("")
    return "\n".join(lines) + "\n"


def build_skip_payload(
    *,
    as_of_date: str,
    run_id: str,
    status: str,
    note: str,
    provider: str,
    model: str,
    model_family: str,
    base_url: str,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "as_of_date": as_of_date,
        "status": status,
        "note": note,
        "provider": provider,
        "model": model,
        "model_family": model_family,
        "base_url": base_url,
        "headline": "",
        "pm_summary": [],
        "focus_actions": [],
        "watch_items": [],
        "preliminary_research": [],
        "ipo_watchlist": [],
        "risk_notes": [],
    }


def clamp_editorial_payload(
    payload: dict[str, Any],
    *,
    max_focus_items: int,
    max_watch_items: int,
    max_summary_bullets: int,
) -> dict[str, Any]:
    focus_actions = [item for item in (payload.get("focus_actions") or []) if isinstance(item, dict)]
    watch_items = [item for item in (payload.get("watch_items") or []) if isinstance(item, dict)]
    preliminary_research = [item for item in (payload.get("preliminary_research") or []) if isinstance(item, dict)]
    ipo_watchlist = [item for item in (payload.get("ipo_watchlist") or []) if isinstance(item, dict)]
    summary = [str(item).strip() for item in (payload.get("pm_summary") or []) if str(item).strip()]
    risk_notes = [str(item).strip() for item in (payload.get("risk_notes") or []) if str(item).strip()]
    payload["pm_summary"] = summary[:max_summary_bullets]
    payload["focus_actions"] = focus_actions[:max_focus_items]
    payload["watch_items"] = watch_items[:max_watch_items]
    payload["preliminary_research"] = preliminary_research[:max_focus_items]
    payload["ipo_watchlist"] = ipo_watchlist[:3]
    payload["risk_notes"] = risk_notes[:2]
    return payload


def main() -> int:
    args = parse_args()
    cfg = load_config_section(args.config, "kimi_editorial")
    enabled = bool(cfg.get("enabled", True))
    required = bool(cfg.get("required", False))
    provider = str(cfg.get("provider") or "kimi_coding")
    base_url = str(cfg.get("base_url") or "https://api.kimi.com/coding").strip().rstrip("/")
    model = str(cfg.get("model") or "anthropic/kimi-for-coding").strip()
    model_family = str(cfg.get("model_family") or "").strip()
    timeout_seconds = float(cfg.get("timeout_seconds") or 60)
    retry_attempts = int(cfg.get("retry_attempts") or 0)
    retry_timeout_seconds = float(cfg.get("retry_timeout_seconds") or max(timeout_seconds * 2, 120.0))
    max_tokens = int(cfg.get("max_tokens") or 1800)
    max_focus_items = int(cfg.get("max_focus_items") or 4)
    max_watch_items = int(cfg.get("max_watch_items") or 2)
    max_summary_bullets = int(cfg.get("max_summary_bullets") or 3)

    output_json = args.output_json or resolve_output_path(cfg.get("output_json_path"), DEFAULT_JSON_OUTPUT)
    output_md = args.output_md or resolve_output_path(cfg.get("output_md_path"), DEFAULT_MD_OUTPUT)

    snapshot = read_json(args.input_snapshot)
    handoff = read_json(args.input_handoff)
    quality = read_json(args.input_quality)
    source_readiness = read_json(args.input_source_readiness)
    company_enrichment = read_optional_json(args.input_company_enrichment)
    news_verification = read_optional_json(args.input_news_verification)
    ipo_watchlist = read_optional_json(args.input_ipo_watchlist)
    hk_ipo_watchlist = read_optional_json(args.input_hk_ipo_watchlist)
    structural_signal = read_optional_json(args.input_structural_signal)
    as_of_date = str(snapshot.get("as_of_date") or "")
    run_id = str(snapshot.get("run_id") or snapshot.get("radar_run_id") or "")

    if not enabled:
        payload = build_skip_payload(
            as_of_date=as_of_date,
            run_id=run_id,
            status="disabled",
            note="kimi_editorial.enabled=false",
            provider=provider,
            model=model,
            model_family=model_family,
            base_url=base_url,
        )
        write_json(output_json, payload)
        write_text(output_md, render_markdown(payload))
        print(json.dumps({"status": payload["status"], "output_json": str(output_json), "output_md": str(output_md)}, ensure_ascii=False))
        return 0

    credential_candidates = discover_credential_candidates(cfg)
    if credential_candidates:
        base_url = credential_candidates[0].get("base_url") or base_url
        model = credential_candidates[0].get("model") or model

    if not credential_candidates:
        if bool(cfg.get("remote_fallback_enabled", True)):
            remote_payload, remote_message = try_remote_kimi_editorial(
                args=args,
                config_path=args.config,
                output_json=output_json,
                output_md=output_md,
                timeout_seconds=int(timeout_seconds),
            )
            if remote_payload is not None:
                print(
                    json.dumps(
                        {
                            "status": remote_payload.get("status"),
                            "output_json": str(output_json),
                            "output_md": str(output_md),
                            "headline": remote_payload.get("headline") or "",
                            "remote_fallback": "pass",
                        },
                        ensure_ascii=False,
                    )
                )
                return 0
        else:
            remote_message = "remote fallback disabled"
        payload = build_skip_payload(
            as_of_date=as_of_date,
            run_id=run_id,
            status="skip_no_credentials",
            note=f"No Kimi credentials discovered from env or configured env files. Remote fallback: {remote_message}",
            provider=provider,
            model=model,
            model_family=model_family,
            base_url=base_url,
        )
        write_json(output_json, payload)
        write_text(output_md, render_markdown(payload))
        print(json.dumps({"status": payload["status"], "output_json": str(output_json), "output_md": str(output_md)}, ensure_ascii=False))
        if required:
            raise SystemExit(payload["note"])
        return 0

    prompt = build_prompt(
        snapshot=snapshot,
        handoff=handoff,
        quality=quality,
        source_readiness=source_readiness,
        company_enrichment=company_enrichment,
        news_verification=news_verification,
        ipo_watchlist=ipo_watchlist,
        hk_ipo_watchlist=hk_ipo_watchlist,
        structural_signal=structural_signal,
        max_focus_items=max_focus_items,
        max_watch_items=max_watch_items,
        max_summary_bullets=max_summary_bullets,
    )
    try:
        parsed, used_credential, credential_errors = call_kimi_json_with_candidates(
            candidates=credential_candidates,
            prompt=prompt,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
            retry_timeout_seconds=retry_timeout_seconds,
        )
        base_url = used_credential.get("base_url") or base_url
        model = used_credential.get("model") or model
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": run_id,
            "as_of_date": as_of_date,
            "status": "pass",
            "provider": provider,
            "model": model,
            "model_family": model_family,
            "base_url": base_url,
            "credential_source": used_credential.get("source") or "",
            "credential_fallback_errors": credential_errors[:3],
            "headline": str(parsed.get("headline") or "").strip(),
            "pm_summary": [str(item).strip() for item in (parsed.get("pm_summary") or []) if str(item).strip()],
            "focus_actions": [item for item in (parsed.get("focus_actions") or []) if isinstance(item, dict)],
            "watch_items": [item for item in (parsed.get("watch_items") or []) if isinstance(item, dict)],
            "preliminary_research": [item for item in (parsed.get("preliminary_research") or []) if isinstance(item, dict)],
            "ipo_watchlist": [item for item in (parsed.get("ipo_watchlist") or []) if isinstance(item, dict)],
            "risk_notes": [str(item).strip() for item in (parsed.get("risk_notes") or []) if str(item).strip()],
        }
        payload = clamp_editorial_payload(
            payload,
            max_focus_items=max_focus_items,
            max_watch_items=max_watch_items,
            max_summary_bullets=max_summary_bullets,
        )
    except Exception as exc:  # noqa: BLE001
        payload = build_skip_payload(
            as_of_date=as_of_date,
            run_id=run_id,
            status="error",
            note=str(exc),
            provider=provider,
            model=model,
            model_family=model_family,
            base_url=base_url,
        )
        if required:
            write_json(output_json, payload)
            write_text(output_md, render_markdown(payload))
            raise

    write_json(output_json, payload)
    write_text(output_md, render_markdown(payload))
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "output_json": str(output_json),
                "output_md": str(output_md),
                "headline": payload.get("headline") or "",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
