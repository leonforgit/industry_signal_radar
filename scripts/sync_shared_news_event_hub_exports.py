#!/usr/bin/env python3
"""Mirror shared News Event Hub consumer exports from a configured remote runtime."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "runtime_defaults.json"
DEFAULT_LOCAL_MIRROR_DIR = ROOT / "output" / "sidecars" / "news_event_hub" / "consumer_exports"
CORE_EXPORTS = {
    "industry_radar_feed_latest.json",
    "opportunity_report_feed_latest.json",
    "research_feed_latest.json",
    "source_health_latest.json",
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="runtime_defaults.json path")
    parser.add_argument("--mirror-dir", type=Path, default=DEFAULT_LOCAL_MIRROR_DIR, help="Local mirror output directory.")
    return parser.parse_args(argv)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def mirror_remote_json(host: str, ssh_options: str, remote_path: str, local_path: Path) -> str:
    local_candidate = Path(str(remote_path or "")).expanduser()
    if local_candidate.exists():
        tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_candidate, tmp_path)
        tmp_path.replace(local_path)
        return ""
    command = ["ssh"]
    if ssh_options.strip():
        command.extend(shlex.split(ssh_options))
    command.extend([host, "cat", remote_path])
    tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with tmp_path.open("wb") as handle:
        result = subprocess.run(command, check=False, stdout=handle, stderr=subprocess.PIPE, text=False)
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        return stderr or f"ssh cat exited with {result.returncode}"
    if tmp_path.stat().st_size <= 0:
        tmp_path.unlink(missing_ok=True)
        return "empty_json_export"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.replace(local_path)
    return ""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = load_config(args.config)
    deployment = config.get("deployment", {}) if isinstance(config.get("deployment"), dict) else {}
    shared_news = config.get("shared_news_event_hub", {}) if isinstance(config.get("shared_news_event_hub"), dict) else {}
    host = str(deployment.get("server_host") or "").strip()
    ssh_options = str(deployment.get("ssh_options") or "").strip()
    remote_targets = {
        "industry_radar_feed_latest.json": str(shared_news.get("industry_radar_feed_path") or "").strip(),
        "opportunity_report_feed_latest.json": str(shared_news.get("opportunity_report_feed_path") or "").strip(),
        "research_feed_latest.json": str(shared_news.get("research_feed_path") or "").strip(),
        "source_health_latest.json": str(shared_news.get("source_health_path") or "").strip(),
    }
    # Extra exports help with debugging and future candidate routing.
    extra_remote_root = "/opt/news-event-hub/state/consumer_exports"
    remote_targets.update(
        {
            "legacy_news_digest_latest.json": f"{extra_remote_root}/legacy_news_digest_latest.json",
            "entity_day_panel_latest.json": f"{extra_remote_root}/entity_day_panel_latest.json",
            "industry_day_panel_latest.json": f"{extra_remote_root}/industry_day_panel_latest.json",
            "manifest_latest.json": f"{extra_remote_root}/manifest_latest.json",
        }
    )

    mirrored: list[str] = []
    failed: list[dict[str, str]] = []
    for filename, remote_path in remote_targets.items():
        if not remote_path:
            failed.append({"file": filename, "reason": "missing_remote_path"})
            continue
        if not host and not Path(remote_path).expanduser().exists():
            failed.append({"file": filename, "reason": "missing_server_host"})
            continue
        local_path = args.mirror_dir / filename
        message = mirror_remote_json(host, ssh_options, remote_path, local_path)
        if message:
            failed.append({"file": filename, "reason": message})
            continue
        mirrored.append(filename)

    failed_files = {str(item.get("file") or "") for item in failed}
    core_failed = sorted(failed_files & CORE_EXPORTS)
    status = "pass"
    warnings: list[str] = []
    blockers: list[str] = []
    if core_failed or not mirrored:
        status = "fail"
        blockers.append("核心 News Event Hub 导出镜像失败：" + " / ".join(core_failed or ["no_files_mirrored"]))
    elif failed:
        status = "warn"
        warnings.append("非核心 News Event Hub 导出镜像失败：" + " / ".join(sorted(failed_files - CORE_EXPORTS)[:6]))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "mirror_dir": str(args.mirror_dir),
        "mirrored_count": len(mirrored),
        "mirrored_files": mirrored,
        "failed": failed,
        "failure_count": len(failed) if status == "fail" else 0,
        "warning_count": len(warnings),
        "warnings": warnings,
        "blockers": blockers,
    }
    manifest_path = args.mirror_dir / "sync_manifest_latest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
