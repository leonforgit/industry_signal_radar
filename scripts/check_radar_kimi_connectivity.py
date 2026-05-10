#!/usr/bin/env python3
"""Check Radar Kimi credential discovery and minimal API connectivity."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from build_radar_kimi_editorial import call_kimi_json, compact_kimi_error, discover_credential_candidates
from radar_config import DEFAULT_CONFIG_PATH, load_config_section


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON_OUTPUT = ROOT / "output" / "reports" / "radar_kimi_connectivity_latest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--section", default="kimi_research_harness")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON_OUTPUT)
    return parser.parse_args()


def fingerprint(token: str) -> dict[str, Any]:
    value = str(token or "")
    if not value:
        return {"present": False}
    return {
        "present": True,
        "len": len(value),
        "prefix": value[:6] + "..." if len(value) >= 6 else "short",
        "sha256_12": hashlib.sha256(value.encode()).hexdigest()[:12],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    cfg = load_config_section(args.config, args.section)
    if not cfg and args.section != "kimi_editorial":
        cfg = load_config_section(args.config, "kimi_editorial")
    candidates = discover_credential_candidates(cfg)
    results: list[dict[str, Any]] = []
    prompt = (
        "只返回 JSON，不要 Markdown。"
        '{"headline":"OK","pm_summary":["OK"],"focus_actions":[],"watch_items":[],"preliminary_research":[],"ipo_watchlist":[],"risk_notes":[]}'
    )
    for candidate in candidates:
        token = str(candidate.get("token") or "")
        item = {
            "source": candidate.get("source") or "",
            "base_url": candidate.get("base_url") or "",
            "model": candidate.get("model") or "",
            "token": fingerprint(token),
        }
        try:
            payload = call_kimi_json(
                token=token,
                base_url=str(candidate.get("base_url") or ""),
                model=str(candidate.get("model") or ""),
                prompt=prompt,
                max_tokens=128,
                timeout_seconds=30,
            )
            item["status"] = "pass"
            item["response_keys"] = sorted(str(key) for key in payload.keys())
        except Exception as exc:  # noqa: BLE001
            item["status"] = "fail"
            item["error"] = compact_kimi_error(exc)
        results.append(item)
    status = "pass" if any(item.get("status") == "pass" for item in results) else "fail"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "section": args.section,
        "candidate_count": len(candidates),
        "results": results,
    }
    write_json(args.output_json, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
